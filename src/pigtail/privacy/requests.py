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
- `purge_repo()`: a project owner's opt-out (CB-13, CB-13c). Deletes or clears every row keyed
  to the repo in the tables registered in `deletion.REPO_TABLES` (GH Archive hourly rows, GitHub
  counts, star history, events, detection agreement, watch list, settle-lag rows, the ETag
  cache, HN mentions and links, cases, linked evidence with raw bytes first, and the `repos`
  row), matched by id, GitHub id and every `owner/name` pigtail associates with the id.
- `purge_repo_name()` (M1-T23): the same opt-out matched by normalized `owner/name`, which also
  reaches data about repos not in `repos`: HN mentions (rows and their snapshots), story titles
  and urls from the rank poller (the rank history keeps only the item id), Show HN screen links,
  watch-list entries (deleted; the refusal list is the tombstone) and per-repo API pages. The
  refusal list holds only a keyed hash of the name (`suppression.repo_name_key`, HMAC with
  `PSEUDONYM_KEY`, CB-13b).
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
    REPO_TABLES,
    DeletionLog,
    PersonTable,
    RepoTable,
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


def _repo_names_for_id(db: CaptureDB, repo_key: str, host_id: int | None) -> set[str]:
    """Every `owner/name` pigtail associates with a repo id (lowercase): `repos.full_name` and,
    for GitHub, watch-list names and GH Archive names (renames included)."""
    rows = db.conn.execute("SELECT full_name FROM repos WHERE id = %s", (repo_key,)).fetchall()
    if host_id is not None:
        rows += db.conn.execute(
            "SELECT full_name FROM watchlist WHERE repo_host_id = %(h)s"
            " UNION SELECT DISTINCT repo_name FROM repo_hourly_activity WHERE repo_host_id = %(h)s",
            {"h": host_id},
        ).fetchall()
    return {str(r[0]).lower() for r in rows if r[0]}


def _url_prefixes(names: Iterable[str]) -> list[str]:
    """Lowercase GitHub API URL prefixes of `names` (`/repos/o/n`, then `/` or `?`)."""
    from pigtail.connectors.github import API

    out = []
    for n in sorted(names):
        base = f"{API}/repos/{n}".lower()
        out += [base + "/", base + "?"]
    return out


def _url_cond(column: str, names: Iterable[str]) -> tuple[sql.Composable, dict[str, Any]]:
    names = sorted(names)
    col = sql.Identifier(column)
    exact = [f"{p[:-1]}" for p in _url_prefixes(names)[::2]]
    cond = sql.SQL(
        "(lower({c}) = ANY(%(url_exact)s) OR EXISTS (SELECT 1 FROM unnest(%(url_prefix)s::text[])"
        " AS p(x) WHERE starts_with(lower({c}), p.x)))"
    ).format(c=col)
    return cond, {"url_exact": exact, "url_prefix": _url_prefixes(names)}


def _repo_cond(
    t: RepoTable, key: str | None, host_id: int | None, names: Sequence[str]
) -> tuple[sql.Composable, dict[str, Any]] | None:
    """The WHERE condition selecting `t`'s rows of this repo, or None if nothing to match."""
    col = sql.Identifier(t.column)
    if t.match == "id":
        if key is None:
            return None
        return sql.SQL("{} = %(key)s").format(col), {"key": key}
    if t.match == "host_id":
        if host_id is None:
            return None
        return sql.SQL("{} = %(host_id)s").format(col), {"host_id": host_id}
    if not names:
        return None
    if t.match == "name":
        return sql.SQL("lower({}) = ANY(%(names)s)").format(col), {"names": list(names)}
    return _url_cond(t.column, names)


def _repo_evidence(
    db: CaptureDB, key: str | None, host_id: int | None, names: Sequence[str]
) -> list[tuple[str, str]]:
    """Evidence `(id, content_hash)` of one repo (CB-13c): linked by `repo_id` or through its
    cases; per-repo GitHub API pages (by URL, and pages behind its star-history rows); HN
    mention snapshots of the repo that no other repo's mention uses. Shared snapshots (GraphQL
    count batches, GH Archive dumps, HN front pages) stay: they hold every other repo too."""
    parts: list[sql.Composable] = []
    params: dict[str, Any] = {"key": key, "host_id": host_id, "names": list(names)}
    if key is not None:
        parts.append(
            sql.SQL(
                "SELECT id FROM evidence WHERE repo_id = %(key)s"
                " OR case_id IN (SELECT id FROM cases WHERE repo_id = %(key)s)"
            )
        )
    if host_id is not None:
        for table in ("repo_star_daily", "star_history_settle_obs"):
            parts.append(
                sql.SQL(
                    "SELECT t.evidence_id FROM {t} t WHERE t.repo_host_id = %(host_id)s"
                    " AND t.evidence_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {t} o"
                    " WHERE o.evidence_id = t.evidence_id AND o.repo_host_id <> %(host_id)s)"
                ).format(t=sql.Identifier(table))
            )
    if names:
        cond, p = _url_cond("url", names)
        params |= p
        parts.append(sql.SQL("SELECT id FROM evidence WHERE ") + cond)
    mention = sql.SQL(
        "(m.repo_full_name = ANY(%(names)s)"
        + (" OR m.repo_id = %(key)s" if key is not None else "")
        + ")"
    )
    other = sql.SQL(
        "(o.repo_full_name <> ALL(%(names)s)"
        + (" AND o.repo_id IS DISTINCT FROM %(key)s" if key is not None else "")
        + ")"
    )
    parts.append(
        sql.SQL(
            "SELECT e.id FROM hn_mention m JOIN evidence e"
            " ON e.id IN (m.evidence_id, m.item_evidence_id) WHERE {m} AND NOT EXISTS ("
            " SELECT 1 FROM hn_mention o WHERE {o} AND e.id IN (o.evidence_id, o.item_evidence_id))"
        ).format(m=mention, o=other)
    )
    q = sql.SQL("SELECT id, content_hash FROM evidence WHERE id IN ({}) ORDER BY id").format(
        sql.SQL(" UNION ").join(parts)
    )
    return [(str(i), str(h)) for i, h in db.conn.execute(q, params).fetchall()]


def _purge_repo_rows(
    db: CaptureDB,
    store: SnapshotStore,
    log: DeletionLog,
    counts: dict[str, int],
    *,
    key: str | None,
    host_id: int | None,
    names: Iterable[str],
    llm_store: LLMStore | None,
    tables: Sequence[RepoTable],
) -> None:
    """Remove or clear every registered repo-keyed row of one repo (CB-13c, `REPO_TABLES`)."""
    names = sorted({n.lower() for n in names})
    evs = _repo_evidence(db, key, host_id, names)
    final: list[RepoTable] = []
    for t in tables:
        if t.action == "evidence":
            continue
        if t.action == "final":
            final.append(t)
            continue
        got = _repo_cond(t, key, host_id, names)
        if got is None:
            continue
        cond, params = got
        if t.table == "github_http_cache" and evs:
            cond = sql.SQL("({} OR evidence_id = ANY(%(ev_ids)s))").format(cond)
            params = {**params, "ev_ids": [e[0] for e in evs]}
        if t.action == "delete":
            q = sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(t.table)) + cond
            action: Any = "rows_deleted"
        else:
            q = (
                sql.SQL("UPDATE {} SET {} WHERE ").format(
                    sql.Identifier(t.table), sql.SQL(t.clear_sql)
                )
                + cond
            )
            action = "fields_cleared"
        n = db.conn.execute(q, params).rowcount
        counts[t.key] = counts.get(t.key, 0) + n
        if n:
            log.write(action, t.table, rows=n)
    _delete_evidence(db, store, evs, log, counts, llm_store)
    for t in final:
        got = _repo_cond(t, key, host_id, names)
        if got is None:
            continue
        cond, params = got
        if t.table == "repos" and t.match == "host_id":
            host = (key or "").partition(":")[0]
            cond = sql.SQL("({} AND host = %(host)s)").format(cond)
            params = {**params, "host": host}
        elif t.table == "repos" and t.match == "name":
            continue  # a repo with a known id is purged by id (purge_repo_name resolves ids)
        q = sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(t.table)) + cond
        n = db.conn.execute(q, params).rowcount
        counts[t.key] = counts.get(t.key, 0) + n
        if n:
            log.write("rows_deleted", t.table, rows=n)


def _zero_counts(tables: Sequence[RepoTable], *extra: str) -> dict[str, int]:
    return dict.fromkeys((*extra, *(t.key for t in tables), "evidence_deleted"), 0)


def purge_repo(
    db: CaptureDB,
    store: SnapshotStore,
    repo_key: str,
    log: DeletionLog,
    *,
    llm_store: LLMStore | None = None,
    tables: Sequence[RepoTable] = REPO_TABLES,
) -> dict[str, int]:
    """Remove an opted-out project (CB-13, CB-13c): every row keyed to it in any table
    registered in `REPO_TABLES`, by id (`<host>:<id>`), GitHub id, and every `owner/name`
    pigtail associates with that id (so HN data matched by name goes too). Linked evidence loses
    its raw bytes first; `cases` and the `repos` row go last. Idempotent."""
    host, _, hid = repo_key.partition(":")
    host_id = int(hid) if host == "github" and hid.isdigit() else None
    counts = _zero_counts(tables)
    names = _repo_names_for_id(db, repo_key, host_id)
    _purge_repo_rows(
        db, store, log, counts, key=repo_key, host_id=host_id, names=names,
        llm_store=llm_store, tables=tables,
    )  # fmt: skip
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
    tables: Sequence[RepoTable] = REPO_TABLES,
) -> dict[str, int]:
    """M1-T23 / CB-13c: remove what pigtail holds about an opted-out repo matched by name
    (`name_key`: keyed `rk_`, or a legacy unkeyed `rn_` entry, CB-13b).

    Every repo id known for that name (`repos`, and the GitHub watch list) is added to the
    refusal list by id and purged with `purge_repo`. Then the rows matched by name alone are
    removed: HN mention rows and the snapshots they came from (unless another repo's mention
    still uses the same snapshot), rank-poller story titles, urls and repo links (the rank history
    keeps the item id only, as at ingest), Show HN screen links, watch-list entries, GH Archive
    hourly rows, per-repo GitHub API pages and their ETag cache rows.
    """
    counts = _zero_counts(tables, "names_matched")
    for name in names_for_key(db, name_key, _keyer(name_key, pz, host), host):
        counts["names_matched"] += 1
        ids = {
            str(r[0])
            for r in db.conn.execute(
                "SELECT id FROM repos WHERE host = %s AND lower(full_name) = %s", (host, name)
            ).fetchall()
        }
        if host == "github":
            ids |= {
                f"github:{r[0]}"
                for r in db.conn.execute(
                    "SELECT repo_host_id FROM watchlist WHERE lower(full_name) = %s"
                    " AND repo_host_id IS NOT NULL",
                    (name,),
                ).fetchall()
            }
        for rid in sorted(ids):
            suppression.add(db, "repo", rid, platform=host, reason="objection")
            for k, v in purge_repo(db, store, rid, log, llm_store=llm_store, tables=tables).items():
                counts["repo_" + k] = counts.get("repo_" + k, 0) + v
        _purge_repo_rows(
            db, store, log, counts, key=None, host_id=None, names=[name],
            llm_store=llm_store, tables=tables,
        )  # fmt: skip
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
