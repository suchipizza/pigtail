"""Deletion log (tombstones) and shared deletion helpers (DPIA CB-01, CB-08, CB-13, CB-17).

Every purge writes one row per action to `deletion_log` (migration 0003). The table is
append-only (a trigger rejects UPDATE, DELETE and TRUNCATE) and holds hashes and ids, never
content, so deletions can be re-applied after a backup restore (retention-policy.md §4, §5).

`PERSON_TABLES` is the registry of Postgres tables holding pseudonymous person-level rows, so the
retention purge (by `time_column`) and erasure (by `pseudonym_column`) reach them:
`hn_mention` (M1-T4), `upstream_items` (deletion sync, CB-02; migration 0005) and
`repo_event_actor` (GitHub per-repo events, M1-T24; TM-33; capped at 30 days by `retention_days`,
CB-22). M5 tables (actors, edges, posts) must register here too.

`REPO_TABLES` (CB-13c) is the matching registry for a project owner's opt-out: every column that
keys a row to a repository (by `<host>:<id>`, by GitHub id, by `owner/name`, or by an API URL of
the repo) and what `pigtail.privacy.requests.purge_repo` does with those rows. A test introspects
the schema and fails when a repo-keyed column is not registered here.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from psycopg import sql

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import SnapshotStore

logger = logging.getLogger("pigtail.privacy.deletion")

# What a failed parse of raw JSON / gzip bytes raises (json, int(), dict access, gzip, zlib).
PARSE_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    EOFError,
    OSError,
    zlib.error,
)

Reason = Literal[
    "retention", "erasure", "objection", "deleted_upstream", "key_rotation", "purpose_limitation"
]
Action = Literal[
    "raw_dropped",
    "rows_deleted",
    "cache_purged",
    "evidence_deleted",
    "error_text_cleared",
    "fields_cleared",  # e.g. hn_story title/url of a story deleted upstream (M1-T23)
]


@dataclass(frozen=True)
class PersonTable:
    """A table of pseudonymous person-level rows, purged at 24 months (or `retention_days` if
    shorter) and on erasure."""

    table: str
    pseudonym_column: str
    time_column: str
    retention_days: int | None = None  # a source-specific cap below the 24-month default
    retention_class: str = "person_level_24m"  # person_level_30d rows follow its cap (CB-22)
    # CB-26 (`pigtail privacy rekey`): what happens to a row whose pseudonym cannot be mapped to
    # the new key (or to every row with `--purge-person-level`). `delete` the row, or `null` the
    # pseudonym column when the row is still needed without it (e.g. deletion-sync tracking).
    rekey_unmapped: Literal["delete", "null"] = "delete"


PERSON_TABLES: tuple[PersonTable, ...] = (
    PersonTable("hn_mention", "author", "last_seen_at"),
    # rows without an author are valid (rank-poller stories): deletion sync keeps tracking them
    PersonTable("upstream_items", "author_pseudonym", "last_seen_at", rekey_unmapped="null"),
    PersonTable(
        "repo_event_actor",
        "actor_pseudonym",
        "created_at",
        retention_days=30,
        retention_class="person_level_30d",
    ),
)


RepoMatch = Literal["id", "host_id", "name", "url"]
RepoAction = Literal["delete", "clear", "evidence", "final"]


@dataclass(frozen=True)
class RepoTable:
    """A column keying rows to a repository, and what a repo opt-out does with them (CB-13c).

    `match`: `id` = `<host>:<id>` (`repos.id`); `host_id` = GitHub numeric id (GitHub only);
    `name` = normalized `owner/name` (compared lowercase); `url` = GitHub API URL of the repo
    (`/repos/<owner>/<name>` and below).
    `action`: `delete` rows; `clear` = keep the row but null the repo link and content
    (`clear_sql`, e.g. the HN rank history keeps the item id); `evidence` = deleted through
    `_delete_evidence` (raw bytes first, then rows and derived LLM cache); `final` = deleted
    after the evidence (rows other tables reference: `cases`, `repos`).
    """

    table: str
    column: str
    match: RepoMatch
    action: RepoAction
    count_key: str = ""
    clear_sql: str = ""

    @property
    def key(self) -> str:
        if self.count_key:
            return self.count_key
        return f"{self.table}_rows_{'cleared' if self.action == 'clear' else 'deleted'}"


_HN_STORY_CLEAR = (
    "title = NULL, url = NULL, repo_full_name = NULL, repo_id = NULL,"
    " content_cleared_at = COALESCE(content_cleared_at, now())"
)

# Order matters: rows are removed in this order (evidence ids are collected before any delete).
REPO_TABLES: tuple[RepoTable, ...] = (
    # per-repo GitHub collectors (M1-T24): star history and events
    RepoTable("repo_star_daily", "repo_host_id", "host_id", "delete"),
    RepoTable("star_history_fetch", "repo_host_id", "host_id", "delete"),
    RepoTable("repo_event_actor", "repo_host_id", "host_id", "delete"),
    RepoTable("repo_event_poll", "repo_host_id", "host_id", "delete"),
    RepoTable("repo_event_daily_agg", "repo_host_id", "host_id", "delete"),
    # launch-mode windows of a tracked project (M11 stub, filled from M14; ADR-049.1)
    RepoTable("launch_mode_window", "repo_id", "id", "delete"),
    # brief stage cache items and shortlist decisions about the repo (M12, R18.4, R4.7)
    RepoTable("brief_stage_cache", "repo_id", "id", "delete"),
    RepoTable("shortlist_decision", "candidate_repo_id", "id", "delete"),
    # ETag cache of per-repo GitHub API pages (url and etag only)
    RepoTable("github_http_cache", "url", "url", "delete"),
    # HN (M1-T4, M1-T14, M1-T23): mentions are deleted; the rank history keeps the item id but
    # loses the repo link (and a story its title and url)
    RepoTable("hn_mention", "repo_id", "id", "delete", "mention_rows_deleted"),
    RepoTable("hn_mention", "repo_full_name", "name", "delete", "mention_rows_deleted"),
    RepoTable("hn_story", "repo_id", "id", "clear", "story_rows_cleared", _HN_STORY_CLEAR),
    RepoTable("hn_story", "repo_full_name", "name", "clear", "story_rows_cleared", _HN_STORY_CLEAR),
    # evidence linked to the repo, its cases or its per-repo API pages; then cases and the repo
    RepoTable("evidence", "repo_id", "id", "evidence", "evidence_deleted"),
    RepoTable("evidence", "url", "url", "evidence", "evidence_deleted"),
    RepoTable("cases", "repo_id", "id", "final", "cases_deleted"),
    RepoTable("repos", "host_id", "host_id", "final", "repos_deleted"),
    RepoTable("repos", "full_name", "name", "final", "repos_deleted"),
)


@dataclass
class DeletionLog:
    """Writes tombstones for one purge; in dry-run mode it only collects them."""

    db: CaptureDB
    reason: Reason
    run_id: str | None = None
    request_id: str | None = None
    dry_run: bool = False
    entries: list[dict[str, Any]] = field(default_factory=list)

    def write(
        self,
        action: Action,
        target: str,
        *,
        rows: int = 0,
        content_hash: str | None = None,
        evidence_id: str | None = None,
    ) -> None:
        entry = {
            "action": action,
            "target": target,
            "rows_affected": rows,
            "content_hash": content_hash,
            "evidence_id": evidence_id,
        }
        self.entries.append(entry)
        if self.dry_run:
            return
        self.db.conn.execute(
            "INSERT INTO deletion_log (reason, action, target, content_hash, evidence_id,"
            " rows_affected, run_id, request_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                self.reason,
                action,
                target,
                content_hash,
                evidence_id,
                rows,
                self.run_id,
                self.request_id,
            ),
        )


def drop_raw(db: CaptureDB, store: SnapshotStore, content_hash: str, log: DeletionLog) -> list[str]:
    """Delete the raw bytes of a hash and mark its present evidence `raw_dropped`.

    The evidence rows (hash, url, fetch time, terms basis) stay: coded facts keep their
    provenance. Returns the ids of the evidence rows changed.
    """
    ids = [
        r[0]
        for r in db.conn.execute(
            "SELECT id FROM evidence WHERE content_hash = %s AND deletion_state = 'present' "
            "ORDER BY id",
            (content_hash,),
        )
    ]
    if not log.dry_run:
        store.delete(content_hash)
        db.conn.execute(
            "UPDATE evidence SET deletion_state = 'raw_dropped' "
            "WHERE content_hash = %s AND deletion_state = 'present'",
            (content_hash,),
        )
    log.write("raw_dropped", "snapshot", rows=len(ids), content_hash=content_hash)
    return ids


def delete_person_rows(
    db: CaptureDB,
    tables: Sequence[PersonTable],
    log: DeletionLog,
    *,
    pseudonyms: Iterable[str] | None = None,
    older_than: Any = None,
) -> dict[str, int]:
    """Delete rows of registered person-level tables by pseudonym or by age."""
    if (pseudonyms is None) == (older_than is None):
        raise ValueError("give exactly one of pseudonyms / older_than")
    out: dict[str, int] = {}
    for t in tables:
        if pseudonyms is not None:
            cond = sql.SQL("{} = ANY(%s)").format(sql.Identifier(t.pseudonym_column))
            param: Any = sorted(set(pseudonyms))
        else:
            cond = sql.SQL("{} <= %s").format(sql.Identifier(t.time_column))
            param = older_than
        if log.dry_run:
            q = sql.SQL("SELECT count(*) FROM {} WHERE ").format(sql.Identifier(t.table)) + cond
            row = db.conn.execute(q, (param,)).fetchone()
            n = int(row[0]) if row else 0
        else:
            q = sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(t.table)) + cond
            n = db.conn.execute(q, (param,)).rowcount
        out[t.table] = n
        if n:
            log.write("rows_deleted", t.table, rows=n)
    return out


def drop_after_parse(
    db: CaptureDB, store: SnapshotStore, evidence_id: str, content_hash: str, log: DeletionLog
) -> bool:
    """Minimisation: drop a snapshot's raw bytes right after parsing (e.g. HN item JSON read by
    the project-level rank poller, whose `by` field must not be kept).

    Only this evidence record moves to `raw_dropped`. If another *present* record still needs
    the same blob, the bytes stay for it. Returns True if bytes were deleted.
    """
    row = db.conn.execute(
        "SELECT count(*) FROM evidence WHERE content_hash = %s AND deletion_state = 'present'"
        " AND id <> %s",
        (content_hash, evidence_id),
    ).fetchone()
    shared = bool(row and int(row[0]))
    if log.dry_run:
        return False
    db.conn.execute(
        "UPDATE evidence SET deletion_state = 'raw_dropped' WHERE id = %s AND deletion_state ="
        " 'present'",
        (evidence_id,),
    )
    if shared:
        return False
    store.delete(content_hash)
    log.write("raw_dropped", "snapshot", rows=1, content_hash=content_hash, evidence_id=evidence_id)
    return True


def drop_unparseable(
    db: CaptureDB,
    store: SnapshotStore,
    evidence_id: str,
    content_hash: str,
    log: DeletionLog,
    *,
    source: str,
    error: BaseException,
    run: RunRecorder | None = None,
) -> bool:
    """CB-23b: a person-level page that fails to parse (malformed JSON, truncated gzip, ...) is
    useless as evidence, so its raw bytes are dropped at once instead of waiting for the
    retention purge (hash and URL kept, evidence `raw_dropped`, tombstone in `deletion_log`).

    The failure goes to the run record as counts only (`<source>.parse_failed` and
    `<source>.parse_failed.<ExceptionType>`); the exception message is never recorded or logged,
    since it can quote the content. Returns True if bytes were deleted.
    """
    kind = type(error).__name__
    if run is not None:
        run.incr(f"{source}.parse_failed")
        run.incr(f"{source}.parse_failed.{kind}")
    logger.warning(
        "%s: unparseable snapshot for evidence %s dropped (%s)", source, evidence_id, kind
    )
    return drop_after_parse(db, store, evidence_id, content_hash, log)


def unparseable_sink(
    db: CaptureDB,
    store: SnapshotStore,
    log: DeletionLog,
    *,
    run: RunRecorder | None = None,
) -> Any:
    """A `Connector.parse_failure_sink` for capture jobs (CB-23b): person-level snapshots that
    fail to parse are dropped at once (`drop_unparseable`); project-level ones are only counted
    (they hold no personal data and help debugging)."""

    def sink(f: Any, error: BaseException) -> None:
        ev = f.evidence
        if str(ev.retention_class).startswith("person_level"):
            drop_unparseable(
                db, store, ev.id, f.content_hash, log, source=ev.source, error=error, run=run
            )
        elif run is not None:
            run.incr(f"{ev.source}.parse_failed")

    return sink


def mark_deleted_upstream(
    db: CaptureDB, store: SnapshotStore, content_hash: str, log: DeletionLog
) -> list[str]:
    """Deletion sync (R1.5, CB-02): the blob holds content removed upstream.

    Drops the raw bytes and moves **every** evidence record with this hash (present or already
    `raw_dropped`) to `deleted_upstream`; hash, URL, fetch time and terms basis stay, so coded
    facts keep their provenance. Returns the ids of the evidence rows changed.
    """
    ids = [
        r[0]
        for r in db.conn.execute(
            "SELECT id FROM evidence WHERE content_hash = %s AND deletion_state <> "
            "'deleted_upstream' ORDER BY id",
            (content_hash,),
        )
    ]
    if not ids:
        return []
    if not log.dry_run:
        store.delete(content_hash)
        db.conn.execute(
            "UPDATE evidence SET deletion_state = 'deleted_upstream' WHERE id = ANY(%s)", (ids,)
        )
    log.write("raw_dropped", "snapshot", rows=len(ids), content_hash=content_hash)
    return ids
