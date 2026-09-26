"""Snapshot retention anchored on final reports (Owner Directive §8.2, ADR-066.2; PRD R19.9).

Raw snapshots of person-level evidence (retention class `person_level_24m`, the name kept from
before R19.9) are stored locally, encrypted at rest, and kept **until the report of the brief
that used them is final plus 12 months**. The scheduled retention job (`pigtail retention purge`,
daily in `infra/schedule.toml`) then deletes the raw bytes and keeps the coded facts and the
content hash: the evidence row stays with `deletion_state = 'raw_dropped'`, a tombstone goes to
`deletion_log` (reason `retention`) and the job writes a `runs` record (`retention.purge`).

**Anchor.** Per content hash (one blob can back several evidence rows):

- `referenced`: some brief run used an evidence row with this hash (`brief_evidence`, written by
  the brief pipeline through `link()`);
- the hash is due at `max(report_final_at) + SNAPSHOT_AFTER_REPORT_DAYS` (default and ceiling
  365) once **every** referencing brief version has a final report (`brief_report_final`,
  `mark_report_final()`, `pigtail retention report-final`);
- while any referencing report is not final, or when no brief references the hash, the ceiling
  `PERSON_LEVEL_RETENTION_DAYS` (default and ceiling 730 days from the newest fetch) applies, so
  an abandoned brief cannot hold snapshots forever; if a final report is also present, the later
  of the two dates wins;
- a hash still needed by a present record of another class (project-level) is never dropped
  (`blocked_shared`), as before.

Shorter source rules still apply on top: raw GH Archive dumps (`GHARCHIVE_RAW_RETENTION_DAYS`,
≤ 30) and per-repo events (`person_level_30d`, ≤ 30; normally dropped right after parsing).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import psycopg

    from pigtail.capture.db import CaptureDB

PERSON_CLASS = "person_level_24m"
Basis = Literal["report_final", "ceiling_pending_report", "ceiling_unreferenced"]


@dataclass(frozen=True)
class SnapshotDue:
    content_hash: str
    evidence_ids: tuple[str, ...]
    due_at: datetime
    basis: Basis
    blocked: bool  # a present record of another class still needs the blob


def due_at(
    *,
    newest_fetch: datetime,
    referenced: bool,
    pending: bool,
    last_final: datetime | None,
    after_report_days: int,
    ceiling_days: int,
) -> tuple[datetime, Basis]:
    """When a person-level snapshot's raw bytes are due to be dropped, and on what basis."""
    ceiling = newest_fetch + timedelta(days=ceiling_days)
    final = last_final + timedelta(days=after_report_days) if last_final is not None else None
    if referenced and not pending and final is not None:
        return final, "report_final"
    if referenced:
        return (max(ceiling, final) if final is not None else ceiling), "ceiling_pending_report"
    return ceiling, "ceiling_unreferenced"


_ANCHOR_SQL = """
WITH person AS (
    SELECT content_hash, array_agg(id ORDER BY id) AS ids, max(fetched_at) AS newest
    FROM evidence
    WHERE retention_class = %(cls)s AND deletion_state = 'present' {hash_filter}
    GROUP BY content_hash
),
refs AS (
    SELECT e.content_hash, br.brief_id, br.brief_version, f.report_final_at
    FROM brief_evidence be
    JOIN evidence e ON e.id = be.evidence_id
    JOIN brief_runs br ON br.id = be.brief_run_id
    LEFT JOIN brief_report_final f
        ON f.brief_id = br.brief_id AND f.brief_version = br.brief_version
    WHERE e.content_hash IN (SELECT content_hash FROM person)
)
SELECT p.content_hash, p.ids, p.newest,
       EXISTS (SELECT 1 FROM refs r WHERE r.content_hash = p.content_hash),
       EXISTS (SELECT 1 FROM refs r WHERE r.content_hash = p.content_hash
               AND r.report_final_at IS NULL),
       (SELECT max(r.report_final_at) FROM refs r WHERE r.content_hash = p.content_hash),
       EXISTS (SELECT 1 FROM evidence o WHERE o.content_hash = p.content_hash
               AND o.deletion_state = 'present' AND o.retention_class <> %(cls)s)
FROM person p
ORDER BY p.content_hash
"""


def person_snapshots(
    conn: psycopg.Connection[Any],
    *,
    after_report_days: int,
    ceiling_days: int,
    content_hash: str | None = None,
) -> list[SnapshotDue]:
    """Every present person-level snapshot (or one hash) with its due date and basis."""
    q = _ANCHOR_SQL.format(hash_filter="AND content_hash = %(h)s" if content_hash else "")
    rows = conn.execute(q, {"cls": PERSON_CLASS, "h": content_hash}).fetchall()
    out = []
    for h, ids, newest, referenced, pending, last_final, blocked in rows:
        at, basis = due_at(
            newest_fetch=newest,
            referenced=bool(referenced),
            pending=bool(pending),
            last_final=last_final,
            after_report_days=after_report_days,
            ceiling_days=ceiling_days,
        )
        out.append(SnapshotDue(str(h), tuple(ids), at, basis, bool(blocked)))
    return out


def link(db: CaptureDB, brief_run_id: str, evidence_ids: Iterable[str]) -> int:
    """Record that a brief run used these evidence rows (the retention anchor). Idempotent;
    returns the number of new links. Called by the brief pipeline."""
    ids = sorted(set(evidence_ids))
    if not ids:
        return 0
    n = 0
    with db.conn.cursor() as cur:
        for eid in ids:
            cur.execute(
                "INSERT INTO brief_evidence (brief_run_id, evidence_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (brief_run_id, eid),
            )
            n += cur.rowcount
    return n


def mark_report_final(
    db: CaptureDB,
    brief_id: str,
    brief_version: int,
    *,
    at: datetime,
    brief_run_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """Record (or move, for a revised report) the time a brief version's report became final."""
    if not brief_id.strip() or brief_version < 1:
        raise ValueError("brief id and version (>= 1) are required")
    if at.tzinfo is None:
        raise ValueError("report_final_at must be timezone-aware")
    db.conn.execute(
        """
        INSERT INTO brief_report_final (brief_id, brief_version, report_final_at, brief_run_id,
            run_id)
        VALUES (%s, %s, %s, %s, (SELECT id FROM runs WHERE id = %s))
        ON CONFLICT (brief_id, brief_version) DO UPDATE SET
            report_final_at = EXCLUDED.report_final_at,
            brief_run_id = COALESCE(EXCLUDED.brief_run_id, brief_report_final.brief_run_id),
            run_id = EXCLUDED.run_id, marked_at = now()
        """,
        (brief_id, brief_version, at, brief_run_id, run_id),
    )


def evidence_due(
    conn: psycopg.Connection[Any],
    *,
    content_hash: str,
    retention_class: str,
    after_report_days: int,
    ceiling_days: int,
) -> tuple[datetime | None, str]:
    """The drop date and rule for one evidence row's snapshot (UI, `api.app.retention_info`)."""
    if retention_class != PERSON_CLASS:
        return None, "kept (no raw-retention limit for this class)"
    got = person_snapshots(
        conn,
        after_report_days=after_report_days,
        ceiling_days=ceiling_days,
        content_hash=content_hash,
    )
    if not got:
        return None, "raw bytes already dropped"
    d = got[0]
    rule = {
        "report_final": "report final + SNAPSHOT_AFTER_REPORT_DAYS (R19.9)",
        "ceiling_pending_report": "report not final: PERSON_LEVEL_RETENTION_DAYS ceiling",
        "ceiling_unreferenced": "no brief uses it: PERSON_LEVEL_RETENTION_DAYS ceiling",
    }[d.basis]
    if d.blocked:
        return None, "kept: a project-level record shares the blob"
    return d.due_at, rule
