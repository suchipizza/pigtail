"""Repo and case lookups shared by the mention and rank capture jobs (R1.2)."""

from __future__ import annotations

from dataclasses import dataclass

from pigtail.capture.db import CaptureDB

OPEN_CASE_STATUSES = ("live", "pre_launch")


@dataclass(frozen=True)
class RepoLink:
    """Where evidence about `full_name` attaches: its `repos` row and newest open case, if any."""

    full_name: str
    repo_id: str | None
    case_id: str | None


def link_repo(db: CaptureDB, full_name: str) -> RepoLink:
    name = full_name.strip().lower()
    row = db.conn.execute(
        "SELECT id FROM repos WHERE lower(full_name) = %s ORDER BY first_seen_at LIMIT 1", (name,)
    ).fetchone()
    if row is None:
        return RepoLink(name, None, None)
    repo_id = str(row[0])
    case = db.conn.execute(
        "SELECT id FROM cases WHERE repo_id = %s AND status = ANY(%s)"
        " ORDER BY opened_at DESC, id LIMIT 1",
        (repo_id, list(OPEN_CASE_STATUSES)),
    ).fetchone()
    return RepoLink(name, repo_id, str(case[0]) if case else None)
