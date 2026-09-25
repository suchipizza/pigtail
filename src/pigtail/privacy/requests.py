"""Data-subject requests (DPIA CB-08) and the refusal-list purge paths (CB-13).

The requester gives a platform and a handle. The handle is pseudonymized at once with
`PSEUDONYM_KEY` in that platform's namespace (the same pseudonym connectors store) and then
discarded: it is never written to the database, the request log, the deletion log or run records.

- `access()`: exports everything keyed by the pseudonym to a local JSON file (mode 0600): the
  parsed records in retained raw snapshots of sources on that platform, rows of registered
  person-level tables, and LLM cache rows that mention the pseudonym.
- `erasure()` (and `objection()`, used by `privacy optout add`): adds the pseudonym to the
  refusal list, so the person is dropped at ingest from then on, then purges: raw snapshots that
  contain their records lose their bytes (whole snapshots: content-addressed blobs cannot be
  edited; evidence moves to `raw_dropped`, replay re-downloads and drops them at ingest),
  person-level rows are deleted, and LLM cache rows derived from those snapshots or mentioning
  the pseudonym are deleted. Project-level aggregates with no pseudonym are kept
  (retention-policy.md §5).
- `purge_repo()`: a project owner's opt-out. Deletes the repo's aggregates, cases and linked
  evidence (raw bytes first), and its `repos` row.
- `purge_repo_name()` (M1-T23): the same opt-out matched by normalized `owner/name`, which also
  reaches data about repos not in `repos`: HN mentions (rows and their snapshots), story titles
  and urls from the rank poller (the rank history keeps only the item id), Show HN screen links
  and watch-list entries. The refusal list holds only a keyed hash of the name
  (`suppression.repo_name_key`, HMAC with `PSEUDONYM_KEY`, CB-13b).
- `rekey_unkeyed_names()` (CB-13b): converts legacy unkeyed name entries (before migration 0009)
  to keyed ones for every name found in local data; the rest stay matched until re-added.

Every request writes a `privacy_requests` row (id, type, platform, dates, outcome, counts; no
handle and no pseudonym) and runs inside a `RunRecorder`. Deletions write tombstones to
`deletion_log`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from psycopg import sql
from psycopg.types.json import Jsonb

from pigtail.capture.snapshots import SnapshotMeta, SnapshotNotFound
from pigtail.connectors.base import Connector, Record
from pigtail.connectors.registry import CONNECTORS
from pigtail.privacy import suppression
from pigtail.privacy.deletion import (
    PERSON_TABLES,
    DeletionLog,
    PersonTable,
    delete_person_rows,
    drop_raw,
)
from pigtail.pseudonymize import Pseudonymizer

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import SnapshotStore
    from pigtail.llm.store import LLMStore

RequestType = Literal["access", "erasure", "objection"]


def new_request_id() -> str:
    return "dsr_" + uuid.uuid4().hex


@dataclass
class SubjectHit:
    """Records of a subject found in one raw snapshot."""

    source: str
    content_hash: str
    evidence: list[dict[str, Any]]
    records: list[Record]


@dataclass
class ScanResult:
    hits: list[SubjectHit] = field(default_factory=list)
    snapshots_scanned: int = 0
    snapshots_missing: int = 0  # evidence says present but the bytes are gone
    raw_dropped_not_searchable: int = 0  # hash only; no person-level content left to search


@dataclass
class RequestResult:
    request_id: str
    type: RequestType
    outcome: str
    counts: dict[str, int]
    export_path: Path | None = None


def scan_snapshots(
    db: CaptureDB,
    store: SnapshotStore,
    pz: Pseudonymizer,
    platform: str,
    pseudonyms: Iterable[str],
    *,
    connectors: Mapping[str, type[Connector]] = CONNECTORS,
) -> ScanResult:
    """Find records of `pseudonyms` in retained raw snapshots of `platform`'s sources.

    Parses through the connector's own path (the same one used at ingest and replay) with an
    empty refusal list, so already-suppressed subjects are still found.
    """
    ns = suppression.platform_namespace(platform)
    wanted = set(pseudonyms)
    res = ScanResult()
    for name, cls in sorted(connectors.items()):
        if cls.handle_namespace != ns:
            continue
        row = db.conn.execute(
            "SELECT count(*) FROM evidence WHERE source = %s AND deletion_state <> 'present'",
            (name,),
        ).fetchone()
        res.raw_dropped_not_searchable += int(row[0]) if row else 0
        rows = db.conn.execute(
            "SELECT content_hash, id, url, fetched_at, retention_class, collector_version,"
            " terms_basis, content_type FROM evidence"
            " WHERE source = %s AND deletion_state = 'present' ORDER BY fetched_at, id",
            (name,),
        ).fetchall()
        by_hash: dict[str, list[tuple[Any, ...]]] = {}
        for r in rows:
            by_hash.setdefault(r[0], []).append(r)
        with httpx.Client() as http:
            conn = cls(store=store, pseudonymizer=pz, http=http, enabled=False, env={})
            for h, evs in by_hash.items():
                try:
                    data = store.get(h)
                except SnapshotNotFound:
                    res.snapshots_missing += 1
                    continue
                res.snapshots_scanned += 1
                first = evs[0]
                meta = SnapshotMeta(
                    source=name,
                    url=first[2],
                    fetched_at=first[3],
                    collector_version=first[5],
                    terms_basis=first[6],
                    content_type=first[7],
                )
                found = [
                    r
                    for r in conn.records(data, meta)
                    if any(v in wanted for v in conn.handle_values(r))
                ]
                if found:
                    res.hits.append(
                        SubjectHit(
                            source=name,
                            content_hash=h,
                            evidence=[
                                {
                                    "id": e[1],
                                    "url": e[2],
                                    "fetched_at": e[3].isoformat(),
                                    "retention_class": e[4],
                                }
                                for e in evs
                            ],
                            records=found,
                        )
                    )
    return res


def _person_rows(
    db: CaptureDB, tables: Sequence[PersonTable], pseudonyms: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    ps = sorted(set(pseudonyms))
    out: dict[str, list[dict[str, Any]]] = {}
    for t in tables:
        q = sql.SQL("SELECT * FROM {} WHERE {} = ANY(%s)").format(
            sql.Identifier(t.table), sql.Identifier(t.pseudonym_column)
        )
        cur = db.conn.execute(q, (ps,))
        cols = [d.name for d in cur.description or []]
        out[t.table] = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    return out


# --- request log -------------------------------------------------------------------------------
def _log_request(
    db: CaptureDB, rid: str, rtype: RequestType, platform: str, run: RunRecorder | None
) -> None:
    db.conn.execute(
        "INSERT INTO privacy_requests (id, type, platform, received_at, outcome, run_id)"
        " VALUES (%s, %s, %s, %s, 'pending', %s)",
        (rid, rtype, platform, datetime.now(UTC), run.id if run else None),
    )


def _finish_request(db: CaptureDB, rid: str, outcome: str, counts: Mapping[str, int]) -> None:
    db.conn.execute(
        "UPDATE privacy_requests SET outcome = %s, completed_at = %s, counts = %s WHERE id = %s",
        (outcome, datetime.now(UTC), Jsonb(dict(counts)), rid),
    )


def requests_log(db: CaptureDB) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT id, type, platform, received_at, completed_at, outcome, counts, run_id"
        " FROM privacy_requests ORDER BY received_at, id"
    ).fetchall()
    keys = ("id", "type", "platform", "received_at", "completed_at", "outcome", "counts", "run_id")
    return [dict(zip(keys, r, strict=True)) for r in rows]


# --- access (Art. 15 / FADP Art. 25) -----------------------------------------------------------
def access(
    db: CaptureDB,
    store: SnapshotStore,
    pz: Pseudonymizer,
    *,
    platform: str,
    handle: str,
    out_dir: Path,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    person_tables: Sequence[PersonTable] = PERSON_TABLES,
    connectors: Mapping[str, type[Connector]] = CONNECTORS,
) -> RequestResult:
    p = suppression.subject_pseudonym(pz, platform, handle)
    generic = pz.pseudonym(handle, "generic")
    rid = new_request_id()
    _log_request(db, rid, "access", platform, run)
    try:
        scan = scan_snapshots(db, store, pz, platform, {p}, connectors=connectors)
        person = _person_rows(db, person_tables, {p})
        cache = llm_store.find_containing([p]) if llm_store else []
        cache_generic = llm_store.find_containing([generic]) if llm_store else []
        on_list = p in suppression.load(db).pseudonyms
        counts = {
            "snapshots_scanned": scan.snapshots_scanned,
            "snapshots_with_records": len(scan.hits),
            "records": sum(len(h.records) for h in scan.hits),
            "person_rows": sum(len(v) for v in person.values()),
            "llm_cache_rows": len(cache),
            "llm_cache_rows_generic_namespace": len(cache_generic),
            "raw_dropped_not_searchable": scan.raw_dropped_not_searchable,
        }
        export = {
            "request_id": rid,
            "type": "access",
            "platform": platform,
            "generated_at": datetime.now(UTC).isoformat(),
            "pseudonym": p,
            "on_refusal_list": on_list,
            "counts": counts,
            "snapshots": [
                {
                    "source": h.source,
                    "content_hash": h.content_hash,
                    "evidence": h.evidence,
                    "records": h.records,
                }
                for h in scan.hits
            ],
            "person_tables": person,
            "llm_cache": cache,
            "llm_cache_generic_namespace": {
                "note": (
                    "Outputs from LLM calls made without a source namespace; the same handle "
                    "on another platform would match too. Review before sending."
                ),
                "rows": cache_generic,
            },
            "notes": [
                "Records are shown with pseudonymized handles, as pigtail stores them.",
                "Evidence whose raw bytes were dropped (retention or erasure) keeps only a "
                "hash, URL and fetch time and cannot be searched for a person.",
            ],
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{rid}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(export, f, indent=2, sort_keys=True, default=str)
        found = counts["records"] + counts["person_rows"] + counts["llm_cache_rows"]
        outcome = "completed" if found else "no_data"
        _finish_request(db, rid, outcome, counts)
    except BaseException:
        _finish_request(db, rid, "failed", {})
        raise
    if run is not None:
        run.incr("records_exported", counts["records"])
    return RequestResult(rid, "access", outcome, counts, path)


# --- erasure / objection (Art. 17, Art. 21 / FADP Art. 30(2)(b), 32(2)) ------------------------
def purge_subject(
    db: CaptureDB,
    store: SnapshotStore,
    pz: Pseudonymizer,
    *,
    platform: str,
    pseudonyms: Iterable[str],
    log: DeletionLog,
    llm_store: LLMStore | None = None,
    extra_cache_tokens: Iterable[str] = (),
    person_tables: Sequence[PersonTable] = PERSON_TABLES,
    connectors: Mapping[str, type[Connector]] = CONNECTORS,
) -> dict[str, int]:
    """Purge existing data of `pseudonyms` on `platform` from every store (CB-08, CB-13)."""
    ps = set(pseudonyms)
    scan = scan_snapshots(db, store, pz, platform, ps, connectors=connectors)
    evidence_ids: list[str] = []
    for h in scan.hits:
        evidence_ids += drop_raw(db, store, h.content_hash, log)
    rows = delete_person_rows(db, person_tables, log, pseudonyms=ps)
    cache = 0
    if llm_store is not None:
        cache = llm_store.purge_for_evidence(evidence_ids, dry_run=log.dry_run)
        cache += llm_store.purge_containing([*ps, *extra_cache_tokens], dry_run=log.dry_run)
        if cache:
            log.write("cache_purged", "llm_cache", rows=cache)
    return {
        "snapshots_scanned": scan.snapshots_scanned,
        "snapshots_raw_dropped": len(scan.hits),
        "evidence_raw_dropped": len(evidence_ids),
        "records_found": sum(len(h.records) for h in scan.hits),
        "person_rows_deleted": sum(rows.values()),
        "llm_cache_rows_deleted": cache,
    }


def erasure(
    db: CaptureDB,
    store: SnapshotStore,
    pz: Pseudonymizer,
    *,
    platform: str,
    handle: str,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    reason: Literal["erasure", "objection"] = "erasure",
    purge: bool = True,
    person_tables: Sequence[PersonTable] = PERSON_TABLES,
    connectors: Mapping[str, type[Connector]] = CONNECTORS,
) -> RequestResult:
    """Refusal-list entry plus purge. `reason="objection"` is `privacy optout add`."""
    p = suppression.subject_pseudonym(pz, platform, handle)
    generic = pz.pseudonym(handle, "generic")
    rid = new_request_id()
    _log_request(db, rid, reason, platform, run)
    try:
        added = suppression.add(
            db, "pseudonym", p, platform=platform, reason=reason, request_id=rid
        )
        counts: dict[str, int] = {"suppression_added": int(added)}
        if purge:
            log = DeletionLog(db, reason, run_id=run.id if run else None, request_id=rid)
            counts |= purge_subject(
                db,
                store,
                pz,
                platform=platform,
                pseudonyms={p},
                log=log,
                llm_store=llm_store,
                extra_cache_tokens=[generic],
                person_tables=person_tables,
                connectors=connectors,
            )
        found = sum(
            counts.get(k, 0)
            for k in ("records_found", "person_rows_deleted", "llm_cache_rows_deleted")
        )
        outcome = "completed" if found or not purge else "no_data"
        _finish_request(db, rid, outcome, counts)
    except BaseException:
        _finish_request(db, rid, "failed", {})
        raise
    if run is not None:
        for k, v in counts.items():
            run.incr(k, v)
    return RequestResult(rid, reason, outcome, counts)


def _delete_evidence(
    db: CaptureDB,
    store: SnapshotStore,
    evs: Sequence[tuple[str, str]],
    log: DeletionLog,
    counts: dict[str, int],
    llm_store: LLMStore | None,
) -> None:
    """Delete evidence rows `(id, content_hash)`: raw bytes first (unless another present
    record still needs the blob), then the rows, then derived LLM cache rows."""
    ids = [e[0] for e in evs]
    for h in sorted({e[1] for e in evs}):
        row = db.conn.execute(
            "SELECT count(*) FROM evidence WHERE content_hash = %s AND deletion_state = 'present'"
            " AND NOT (id = ANY(%s))",
            (h, ids),
        ).fetchone()
        if row and int(row[0]) == 0:
            store.delete(h)
            log.write("raw_dropped", "snapshot", rows=1, content_hash=h)
    if ids:
        db.conn.execute(
            "UPDATE gharchive_hours SET evidence_id = NULL WHERE evidence_id = ANY(%s)", (ids,)
        )
        db.conn.execute("DELETE FROM evidence WHERE id = ANY(%s)", (ids,))
        for eid, h in evs:
            log.write("evidence_deleted", "evidence", rows=1, content_hash=h, evidence_id=eid)
    counts["evidence_deleted"] = counts.get("evidence_deleted", 0) + len(ids)
    if llm_store is not None:
        n = llm_store.purge_for_evidence(ids)
        counts["llm_cache_rows_deleted"] = counts.get("llm_cache_rows_deleted", 0) + n
        if n:
            log.write("cache_purged", "llm_cache", rows=n)


def purge_repo(
    db: CaptureDB,
    store: SnapshotStore,
    repo_key: str,
    log: DeletionLog,
    *,
    llm_store: LLMStore | None = None,
) -> dict[str, int]:
    """Remove an opted-out project (CB-13): aggregates, cases, linked evidence, repo row."""
    host, _, host_id = repo_key.partition(":")
    counts = {"hourly_rows_deleted": 0, "evidence_deleted": 0, "cases_deleted": 0}
    if host == "github":
        n = db.conn.execute(
            "DELETE FROM repo_hourly_activity WHERE repo_host_id = %s", (int(host_id),)
        ).rowcount
        counts["hourly_rows_deleted"] = n
        if n:
            log.write("rows_deleted", "repo_hourly_activity", rows=n)
        for table in ("star_history_settle_obs", "settle_lag_schedule"):  # K2 (M4-T4)
            n = db.conn.execute(
                sql.SQL("DELETE FROM {} WHERE repo_host_id = %s").format(sql.Identifier(table)),
                (int(host_id),),
            ).rowcount
            if n:
                log.write("rows_deleted", table, rows=n)
    evs = db.conn.execute(
        "SELECT id, content_hash FROM evidence WHERE repo_id = %(r)s"
        " OR case_id IN (SELECT id FROM cases WHERE repo_id = %(r)s) ORDER BY id",
        {"r": repo_key},
    ).fetchall()
    _delete_evidence(db, store, evs, log, counts, llm_store)
    n = db.conn.execute("DELETE FROM cases WHERE repo_id = %s", (repo_key,)).rowcount
    counts["cases_deleted"] = n
    if n:
        log.write("rows_deleted", "cases", rows=n)
    n = db.conn.execute("DELETE FROM repos WHERE id = %s", (repo_key,)).rowcount
    if n:
        log.write("rows_deleted", "repos", rows=n)
    return counts


def names_for_key(
    db: CaptureDB, name_key: str, keyer: Callable[[str], str], host: str = "github"
) -> list[str]:
    """Normalized repo names stored anywhere pigtail keeps them whose key (`keyer(name)`) is
    `name_key` (the refusal list holds only the hash, M1-T23)."""
    rows = db.conn.execute(
        """
        SELECT repo_full_name FROM hn_mention
        UNION SELECT repo_full_name FROM hn_story WHERE repo_full_name IS NOT NULL
        UNION SELECT repo_full_name FROM hn_show_screen WHERE repo_full_name IS NOT NULL
        UNION SELECT lower(full_name) FROM watchlist
        UNION SELECT lower(full_name) FROM repos WHERE host = %s
        """,
        (host,),
    ).fetchall()
    out = set()
    for (n,) in rows:
        try:
            if keyer(n) == name_key:
                out.add(suppression.normalize_repo_name(n))
        except ValueError:
            continue
    return sorted(out)


def _keyer(name_key: str, pz: Pseudonymizer, host: str) -> Callable[[str], str]:
    """The key function matching `name_key`'s kind: keyed (`rk_`) or legacy unkeyed (`rn_`)."""
    if suppression.LEGACY_REPO_NAME_KEY_RE.match(name_key):
        return lambda n: suppression.legacy_repo_name_key(n, host)
    return lambda n: suppression.repo_name_key(n, pz, host)


def rekey_unkeyed_names(db: CaptureDB, pz: Pseudonymizer) -> dict[str, int]:
    """CB-13b: replace legacy unkeyed name entries with keyed ones where the name is known.

    A legacy entry's name is recovered only by matching names pigtail already holds (HN, watch
    list, repos); the keyed entry keeps the platform, reason and request id. Entries whose name
    is not found stay (still matched) until the operator re-adds the name."""
    counts = {"converted": 0, "remaining": 0}
    rows = db.conn.execute(
        "SELECT value, platform, reason, request_id FROM privacy_suppression"
        " WHERE kind = 'repo_name_unkeyed' ORDER BY added_at, value"
    ).fetchall()
    for value, platform, reason, request_id in rows:
        names = names_for_key(db, value, _keyer(value, pz, platform), platform)
        if not names:
            counts["remaining"] += 1
            continue
        for name in names:
            suppression.add(
                db, "repo_name", suppression.repo_name_key(name, pz, platform),
                platform=platform, reason=reason, request_id=request_id,
            )  # fmt: skip
        db.conn.execute(
            "DELETE FROM privacy_suppression WHERE kind = 'repo_name_unkeyed' AND value = %s",
            (value,),
        )
        counts["converted"] += 1
    return counts


def purge_repo_name(
    db: CaptureDB,
    store: SnapshotStore,
    name_key: str,
    log: DeletionLog,
    *,
    pz: Pseudonymizer,
    host: str = "github",
    llm_store: LLMStore | None = None,
) -> dict[str, int]:
    """M1-T23: remove what pigtail holds about an opted-out repo matched by name (`name_key`:
    keyed `rk_`, or a legacy unkeyed `rn_` entry, CB-13b).

    HN mention rows and the snapshots they came from (unless another repo's mention still uses
    the same snapshot), rank-poller story titles, urls and repo links (the rank history keeps
    the item id only, as at ingest), Show HN screen links, and watch-list entries (deactivated
    as `opted_out`). A `repos` row with that name is purged by id too, and its id is added to
    the refusal list so ingest by id drops it as well.
    """
    counts: dict[str, int] = {
        "names_matched": 0,
        "mention_rows_deleted": 0,
        "story_rows_cleared": 0,
        "show_rows_cleared": 0,
        "watchlist_deactivated": 0,
        "evidence_deleted": 0,
    }
    for name in names_for_key(db, name_key, _keyer(name_key, pz, host), host):
        counts["names_matched"] += 1
        evs = db.conn.execute(
            """
            SELECT DISTINCT e.id, e.content_hash FROM hn_mention m
            JOIN evidence e ON e.id IN (m.evidence_id, m.item_evidence_id)
            WHERE m.repo_full_name = %(n)s AND NOT EXISTS (
                SELECT 1 FROM hn_mention o WHERE o.repo_full_name <> %(n)s
                  AND e.id IN (o.evidence_id, o.item_evidence_id))
            ORDER BY 1
            """,
            {"n": name},
        ).fetchall()
        n = db.conn.execute("DELETE FROM hn_mention WHERE repo_full_name = %s", (name,)).rowcount
        counts["mention_rows_deleted"] += n
        if n:
            log.write("rows_deleted", "hn_mention", rows=n)
        _delete_evidence(db, store, evs, log, counts, llm_store)
        n = db.conn.execute(
            "UPDATE hn_story SET title = NULL, url = NULL, repo_full_name = NULL, repo_id = NULL,"
            " content_cleared_at = COALESCE(content_cleared_at, now()) WHERE repo_full_name = %s",
            (name,),
        ).rowcount
        counts["story_rows_cleared"] += n
        if n:
            log.write("fields_cleared", "hn_story", rows=n)
        n = db.conn.execute(
            "UPDATE hn_show_screen SET repo_full_name = NULL WHERE repo_full_name = %s", (name,)
        ).rowcount
        counts["show_rows_cleared"] += n
        if n:
            log.write("fields_cleared", "hn_show_screen", rows=n)
        counts["watchlist_deactivated"] += db.conn.execute(
            "UPDATE watchlist SET active = false, deactivated_at = now(),"
            " deactivated_reason = 'opted_out' WHERE lower(full_name) = %s"
            " AND (active OR deactivated_reason IS DISTINCT FROM 'opted_out')",
            (name,),
        ).rowcount
        for (rid,) in db.conn.execute(
            "SELECT id FROM repos WHERE host = %s AND lower(full_name) = %s", (host, name)
        ).fetchall():
            suppression.add(db, "repo", str(rid), platform=host, reason="objection")
            for k, v in purge_repo(db, store, str(rid), log, llm_store=llm_store).items():
                counts["repo_" + k] = counts.get("repo_" + k, 0) + v
    return counts


def optout_repo(
    db: CaptureDB,
    store: SnapshotStore,
    *,
    platform: str,
    repo_key: str,
    pz: Pseudonymizer,
    full_name: str | None = None,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    purge: bool = True,
) -> RequestResult:
    """A project owner's opt-out: refusal-list entry for the repo id, then purge (CB-13).

    The repo's name (given, or its `repos.full_name`) is added by hash as well, so HN data about
    it that is matched by name is suppressed and purged too (M1-T23).
    """
    rid = new_request_id()
    _log_request(db, rid, "objection", platform, run)
    try:
        added = suppression.add(
            db, "repo", repo_key, platform=platform, reason="objection", request_id=rid
        )
        counts: dict[str, int] = {"suppression_added": int(added)}
        if full_name is None:
            row = db.conn.execute("SELECT full_name FROM repos WHERE id = %s", (repo_key,))
            got = row.fetchone()
            full_name = str(got[0]) if got else None
        name_key = suppression.repo_name_key(full_name, pz, platform) if full_name else None
        if name_key is not None and full_name is not None:
            counts["name_suppression_added"] = int(
                suppression.add(
                    db, "repo_name", name_key, platform=platform, reason="objection",
                    request_id=rid,
                )
            )  # fmt: skip
            counts["legacy_name_replaced"] = int(
                suppression.remove_legacy_name(db, full_name, platform)
            )
        if purge:
            log = DeletionLog(db, "objection", run_id=run.id if run else None, request_id=rid)
            counts |= purge_repo(db, store, repo_key, log, llm_store=llm_store)
            if name_key is not None:  # HN data matched by name (M1-T23)
                by_name = purge_repo_name(
                    db, store, name_key, log, pz=pz, host=platform, llm_store=llm_store
                )
                counts |= {f"name_{k}": v for k, v in by_name.items()}
        _finish_request(db, rid, "completed", counts)
    except BaseException:
        _finish_request(db, rid, "failed", {})
        raise
    return RequestResult(rid, "objection", "completed", counts)


def optout_repo_name(
    db: CaptureDB,
    store: SnapshotStore,
    *,
    platform: str,
    full_name: str,
    pz: Pseudonymizer,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    purge: bool = True,
) -> RequestResult:
    """M1-T23: opt out a repo that is not in `repos`, by normalized `owner/name` (stored only
    as the keyed `repo_name_key`, CB-13b), then purge what is held about it by name. A legacy
    unkeyed entry for the same name is replaced."""
    name_key = suppression.repo_name_key(full_name, pz, platform)  # ValueError on a bad name
    rid = new_request_id()
    _log_request(db, rid, "objection", platform, run)
    try:
        added = suppression.add(
            db, "repo_name", name_key, platform=platform, reason="objection", request_id=rid
        )
        counts: dict[str, int] = {
            "suppression_added": int(added),
            "legacy_name_replaced": int(suppression.remove_legacy_name(db, full_name, platform)),
        }
        if purge:
            log = DeletionLog(db, "objection", run_id=run.id if run else None, request_id=rid)
            counts |= purge_repo_name(
                db, store, name_key, log, pz=pz, host=platform, llm_store=llm_store
            )
        _finish_request(db, rid, "completed", counts)
    except BaseException:
        _finish_request(db, rid, "failed", {})
        raise
    return RequestResult(rid, "objection", "completed", counts)


def reapply_refusals(
    db: CaptureDB,
    store: SnapshotStore,
    pz: Pseudonymizer,
    *,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    person_tables: Sequence[PersonTable] = PERSON_TABLES,
    connectors: Mapping[str, type[Connector]] = CONNECTORS,
) -> dict[str, int]:
    """Purge existing data of everyone and every repo on the list (after a restore, CB-13/17)."""
    totals: dict[str, int] = {}
    by_platform: dict[str, set[str]] = {}
    repos: list[str] = []
    names: list[tuple[str, str]] = []
    for e in suppression.entries(db):
        if e["kind"] == "pseudonym":
            by_platform.setdefault(e["platform"], set()).add(e["value"])
        elif e["kind"] in ("repo_name", "repo_name_unkeyed"):
            names.append((e["value"], e["platform"]))
        else:
            repos.append(e["value"])
    run_id = run.id if run else None
    for platform, ps in sorted(by_platform.items()):
        log = DeletionLog(db, "objection", run_id=run_id)
        c = purge_subject(
            db,
            store,
            pz,
            platform=platform,
            pseudonyms=ps,
            log=log,
            llm_store=llm_store,
            person_tables=person_tables,
            connectors=connectors,
        )
        for k, v in c.items():
            totals[k] = totals.get(k, 0) + v
    for name_key, host in names:  # M1-T23; may add repo ids, so before the id loop
        c = purge_repo_name(
            db,
            store,
            name_key,
            DeletionLog(db, "objection", run_id=run_id),
            pz=pz,
            host=host,
            llm_store=llm_store,
        )
        for k, v in c.items():
            totals["repo_name_" + k] = totals.get("repo_name_" + k, 0) + v
    for key in repos:
        c = purge_repo(
            db, store, key, DeletionLog(db, "objection", run_id=run_id), llm_store=llm_store
        )
        for k, v in c.items():
            totals["repo_" + k] = totals.get("repo_" + k, 0) + v
    if run is not None:
        for k, v in totals.items():
            run.incr(k, v)
    return totals
