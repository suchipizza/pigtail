"""Deletion log (tombstones) and shared deletion helpers (DPIA CB-01, CB-08, CB-13, CB-17).

Every purge writes one row per action to `deletion_log` (migration 0003). The table is
append-only (a trigger rejects UPDATE, DELETE and TRUNCATE) and holds hashes and ids, never
content, so deletions can be re-applied after a backup restore (retention-policy.md §4, §5).

`PERSON_TABLES` is the registry of Postgres tables holding pseudonymous person-level rows. None
exists yet: capture stores only aggregates (`repo_hourly_activity`) and raw snapshots. M5 tables
(actors, edges, posts) must register here so the retention purge (by `time_column`) and erasure
(by `pseudonym_column`) reach them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from psycopg import sql

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.snapshots import SnapshotStore

Reason = Literal["retention", "erasure", "objection", "deleted_upstream"]
Action = Literal[
    "raw_dropped", "rows_deleted", "cache_purged", "evidence_deleted", "error_text_cleared"
]


@dataclass(frozen=True)
class PersonTable:
    """A table of pseudonymous person-level rows, purged at 24 months and on erasure."""

    table: str
    pseudonym_column: str
    time_column: str


PERSON_TABLES: tuple[PersonTable, ...] = ()


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
