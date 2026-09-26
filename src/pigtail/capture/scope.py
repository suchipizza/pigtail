"""Mention scope: shortlisted projects only (Owner Directive §8.3, ADR-066.3; PRD R1.2).

Person-level mention capture (HN search and items; later Bluesky and others) runs only for repos
on a brief version's shortlist whose status is `in_review` or `final` (`brief_shortlist_entry`,
migration 0019). There are no sweeps of users or accounts and no follower lists: a capture job
searches for a project, never for a person.

The brief pipeline (shortlist review, R4.7) writes the entries with `set_entries()`; an operator
can also set them with `pigtail capture shortlist set` (the names go to the private database
only, never to git). `capture_hn_mentions()` raises `NotShortlisted` before any request for a
repo outside the scope, and the scheduler's `hn_mentions` job plans only cases whose repo is in
scope.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Literal

from pigtail.privacy.suppression import normalize_repo_name

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB

Status = Literal["in_review", "final", "removed"]
IN_SCOPE: tuple[Status, ...] = ("in_review", "final")


class NotShortlisted(RuntimeError):
    """The repo is on no in-review or final shortlist: its mentions are not collected (§8.3)."""


def set_entries(
    db: CaptureDB,
    brief_id: str,
    brief_version: int,
    full_names: Iterable[str],
    status: Status,
) -> int:
    """Upsert shortlist entries of one brief version with `status`. Returns rows written.

    Names are normalized (`owner/name`, lowercase); `repo_id` is filled from `repos` when known."""
    if status not in ("in_review", "final", "removed"):
        raise ValueError(f"unknown shortlist status {status!r}")
    if not brief_id.strip() or brief_version < 1:
        raise ValueError("brief id and version (>= 1) are required")
    names = sorted({normalize_repo_name(n) for n in full_names})
    n = 0
    with db.conn.cursor() as cur:
        for name in names:
            cur.execute(
                """
                INSERT INTO brief_shortlist_entry (brief_id, brief_version, repo_full_name,
                    repo_id, status, updated_at)
                VALUES (%s, %s, %s, (SELECT id FROM repos WHERE lower(full_name) = %s
                                     ORDER BY first_seen_at DESC LIMIT 1), %s, now())
                ON CONFLICT (brief_id, brief_version, repo_full_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    repo_id = COALESCE(EXCLUDED.repo_id, brief_shortlist_entry.repo_id),
                    updated_at = now()
                """,
                (brief_id, brief_version, name, name, status),
            )
            n += cur.rowcount
    return n


def in_scope(db: CaptureDB, full_name: str, repo_id: str | None = None) -> bool:
    """True if `owner/name` (or `repo_id`) is on an in-review or final shortlist."""
    try:
        name = normalize_repo_name(full_name)
    except ValueError:
        return False
    row = db.conn.execute(
        "SELECT EXISTS (SELECT 1 FROM brief_shortlist_entry WHERE status = ANY(%s)"
        " AND (repo_full_name = %s OR (%s::text IS NOT NULL AND repo_id = %s)))",
        (list(IN_SCOPE), name, repo_id, repo_id),
    ).fetchone()
    return bool(row and row[0])


def require_in_scope(db: CaptureDB, full_name: str, repo_id: str | None = None) -> None:
    if not in_scope(db, full_name, repo_id):
        raise NotShortlisted(
            "mentions are collected for shortlisted projects only (Directive §8.3): this repo is "
            "on no in-review or final shortlist (`pigtail capture shortlist set`)"
        )


def entries(db: CaptureDB) -> list[dict[str, Any]]:
    """Every entry (for the operator's own terminal; never logged)."""
    cur = db.conn.execute(
        "SELECT brief_id, brief_version, repo_full_name, repo_id, status, updated_at"
        " FROM brief_shortlist_entry ORDER BY brief_id, brief_version, repo_full_name"
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
