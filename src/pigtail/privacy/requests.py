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

Every request writes a `privacy_requests` row (id, type, platform, dates, outcome, counts; no
handle and no pseudonym) and runs inside a `RunRecorder`. Deletions write tombstones to
`deletion_log`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable, Mapping, Sequence
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
    evs = db.conn.execute(
        "SELECT id, content_hash FROM evidence WHERE repo_id = %(r)s"
        " OR case_id IN (SELECT id FROM cases WHERE repo_id = %(r)s) ORDER BY id",
        {"r": repo_key},
    ).fetchall()
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
    counts["evidence_deleted"] = len(ids)
    if llm_store is not None:
        n = llm_store.purge_for_evidence(ids)
        counts["llm_cache_rows_deleted"] = n
        if n:
            log.write("cache_purged", "llm_cache", rows=n)
    n = db.conn.execute("DELETE FROM cases WHERE repo_id = %s", (repo_key,)).rowcount
    counts["cases_deleted"] = n
    if n:
        log.write("rows_deleted", "cases", rows=n)
    n = db.conn.execute("DELETE FROM repos WHERE id = %s", (repo_key,)).rowcount
    if n:
        log.write("rows_deleted", "repos", rows=n)
    return counts


def optout_repo(
    db: CaptureDB,
    store: SnapshotStore,
    *,
    platform: str,
    repo_key: str,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    purge: bool = True,
) -> RequestResult:
    """A project owner's opt-out: refusal-list entry for the repo id, then purge (CB-13)."""
    rid = new_request_id()
    _log_request(db, rid, "objection", platform, run)
    try:
        added = suppression.add(
            db, "repo", repo_key, platform=platform, reason="objection", request_id=rid
        )
        counts: dict[str, int] = {"suppression_added": int(added)}
        if purge:
            log = DeletionLog(db, "objection", run_id=run.id if run else None, request_id=rid)
            counts |= purge_repo(db, store, repo_key, log, llm_store=llm_store)
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
    for e in suppression.entries(db):
        if e["kind"] == "pseudonym":
            by_platform.setdefault(e["platform"], set()).add(e["value"])
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
