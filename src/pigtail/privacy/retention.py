"""Retention purge for person-level data (DPIA CB-01; retention-policy.md §1, §2).

`purge()` applies, in order:

1. **CB-04**: GH Archive raw dumps older than `GHARCHIVE_RAW_RETENTION_DAYS` (default 30) lose
   their raw bytes (`pigtail.capture.retention.purge_raw`).
2. **CB-01**: every `person_level_24m` evidence record older than `PERSON_LEVEL_RETENTION_DAYS`
   (default 730 = 24 months, from `fetched_at`) loses its raw bytes and moves to
   `deletion_state = 'raw_dropped'`. The record keeps `content_hash`, `url`, `source`,
   `fetched_at` and `terms_basis`, so coded facts keep their provenance. Content addressing means
   one blob can back several records: a hash is dropped only when no *present* record still
   needs it (a `project_level` or `derived_aggregate` record, or a younger person-level one).
   Such hashes are reported as `blocked_shared`.
3. Rows of registered person-level tables (`PERSON_TABLES`) older than the cutoff are deleted.
4. **CB-05**: LLM cache rows linked to the evidence dropped in step 2 and rows past
   `LLM_CACHE_RETENTION_DAYS` are deleted; the usage ledger and pause log follow the same period.
5. **CB-18**: `runs.error` text older than `LOG_RETENTION_DAYS` (default 365) is cleared.

`project_level` and `derived_aggregate` data are never touched. Each action writes a tombstone
to the append-only `deletion_log`; the caller wraps the purge in a `RunRecorder` (job
`retention.purge`). `dry_run=True` computes the same report and changes nothing. Running the
purge twice is a no-op the second time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pigtail.capture.retention import purge_raw
from pigtail.privacy.deletion import (
    PERSON_TABLES,
    DeletionLog,
    PersonTable,
    delete_person_rows,
    drop_raw,
)

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import SnapshotStore
    from pigtail.llm.store import LLMStore


@dataclass(frozen=True)
class RetentionConfig:
    person_level_days: int = 730
    gharchive_raw_days: int = 30
    log_days: int = 365


@dataclass
class PurgeReport:
    dry_run: bool
    now: str
    person_level_cutoff: str
    log_cutoff: str
    gharchive_raw_hashes_dropped: int = 0
    person_level_hashes_dropped: int = 0
    person_level_evidence_raw_dropped: int = 0
    blocked_shared: int = 0
    person_rows_deleted: dict[str, int] = field(default_factory=dict)
    llm_cache_rows_for_evidence: int = 0
    llm_cache_rows_expired: int = 0
    llm_ledger_rows_deleted: int = 0
    run_errors_cleared: int = 0
    dropped_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def purge(
    db: CaptureDB,
    store: SnapshotStore,
    *,
    cfg: RetentionConfig | None = None,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    person_tables: Sequence[PersonTable] = PERSON_TABLES,
) -> PurgeReport:
    cfg = cfg or RetentionConfig()
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=cfg.person_level_days)
    log_cutoff = now - timedelta(days=cfg.log_days)
    log = DeletionLog(db, "retention", run_id=run.id if run else None, dry_run=dry_run)
    rep = PurgeReport(
        dry_run=dry_run,
        now=now.isoformat(),
        person_level_cutoff=cutoff.isoformat(),
        log_cutoff=log_cutoff.isoformat(),
    )

    # 1. CB-04: raw GH Archive dumps past their short retention.
    rep.gharchive_raw_hashes_dropped = purge_raw(
        db, store, source="gharchive", retention_days=cfg.gharchive_raw_days, now=now, log=log
    )

    # 2. CB-01: person-level evidence past 24 months.
    rows = db.conn.execute(
        """
        SELECT e.content_hash, array_agg(e.id ORDER BY e.id),
               EXISTS (
                   SELECT 1 FROM evidence o
                   WHERE o.content_hash = e.content_hash AND o.deletion_state = 'present'
                     AND (o.retention_class <> 'person_level_24m' OR o.fetched_at > %(cutoff)s)
               )
        FROM evidence e
        WHERE e.retention_class = 'person_level_24m' AND e.deletion_state = 'present'
          AND e.fetched_at <= %(cutoff)s
        GROUP BY e.content_hash
        ORDER BY e.content_hash
        """,
        {"cutoff": cutoff},
    ).fetchall()
    done = {e["content_hash"] for e in log.entries if e["action"] == "raw_dropped"}
    dropped_evidence: list[str] = []
    for h, ids, blocked in rows:
        if h in done:  # dry run: already counted in step 1
            continue
        if blocked:
            rep.blocked_shared += 1
            continue
        dropped_evidence += list(ids)
        drop_raw(db, store, h, log)
        rep.person_level_hashes_dropped += 1
        rep.dropped_hashes.append(h)
    rep.person_level_evidence_raw_dropped = len(dropped_evidence)

    # 3. Pseudonymous person-level rows (none registered before M5).
    rep.person_rows_deleted = delete_person_rows(db, person_tables, log, older_than=cutoff)

    # 4. CB-05: LLM cache derived from dropped evidence, then everything past the cache TTL.
    if llm_store is not None:
        n = llm_store.purge_for_evidence(dropped_evidence, dry_run=dry_run)
        rep.llm_cache_rows_for_evidence = n
        if n:
            log.write("cache_purged", "llm_cache", rows=n)
        n = llm_store.purge_expired(now, dry_run=dry_run)
        rep.llm_cache_rows_expired = n
        if n:
            log.write("cache_purged", "llm_cache", rows=n)
        n = llm_store.purge_ledger(now, dry_run=dry_run)
        rep.llm_ledger_rows_deleted = n
        if n:
            log.write("rows_deleted", "llm_usage", rows=n)

    # 5. CB-18: error text in run records past the log retention.
    if dry_run:
        row = db.conn.execute(
            "SELECT count(*) FROM runs WHERE error IS NOT NULL AND started_at <= %s",
            (log_cutoff,),
        ).fetchone()
        n = int(row[0]) if row else 0
    else:
        n = db.conn.execute(
            "UPDATE runs SET error = NULL WHERE error IS NOT NULL AND started_at <= %s",
            (log_cutoff,),
        ).rowcount
    rep.run_errors_cleared = n
    if n:
        log.write("error_text_cleared", "runs.error", rows=n)

    if run is not None:
        for key in (
            "gharchive_raw_hashes_dropped",
            "person_level_hashes_dropped",
            "person_level_evidence_raw_dropped",
            "blocked_shared",
            "llm_cache_rows_for_evidence",
            "llm_cache_rows_expired",
            "llm_ledger_rows_deleted",
            "run_errors_cleared",
        ):
            run.incr(key, getattr(rep, key))
        run.incr("person_rows_deleted", sum(rep.person_rows_deleted.values()))
    return rep
