"""Opt-out / refusal list (DPIA CB-13; FADP Art. 30(2)(b), GDPR Art. 21).

The list (`privacy_suppression`, migration 0003) holds only:

- **pseudonyms** (`p_<16 hex>`) of people who objected or asked for erasure. The handle given
  by the requester is pseudonymized immediately with `PSEUDONYM_KEY` in the platform's namespace
  and never stored; a CHECK constraint rejects anything that is not a pseudonym;
- **repo ids** (`<host>:<host_id>`, as in `repos.id`) of projects whose owner opted out.

Connectors load the list once (`load()`) and drop matching records at ingest
(`pigtail.connectors.base.Connector.records`). Existing data is purged by
`pigtail.privacy.requests.purge_subject` / `purge_repo` (`pigtail privacy optout add|purge`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pigtail.pseudonymize import PLATFORM_NAMESPACES, PSEUDONYM_RE, Pseudonymizer

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB

Kind = Literal["pseudonym", "repo"]
Reason = Literal["objection", "erasure"]
REPO_KEY_RE = re.compile(r"^[a-z]+:[0-9]+$")


@dataclass(frozen=True)
class Suppressions:
    """In-memory snapshot of the refusal list, checked for every parsed record."""

    pseudonyms: frozenset[str] = frozenset()
    repos: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.pseudonyms or self.repos)


def platform_namespace(platform: str) -> str:
    try:
        return PLATFORM_NAMESPACES[platform]
    except KeyError as e:
        known = ", ".join(sorted(PLATFORM_NAMESPACES))
        raise ValueError(f"unknown platform {platform!r}; expected one of {known}") from e


def subject_pseudonym(pz: Pseudonymizer, platform: str, handle: str) -> str:
    """The pseudonym a connector for `platform` stores for `handle` (CB-08, CB-13)."""
    if not handle.strip().lstrip("@"):
        raise ValueError("empty handle")
    return pz.pseudonym(handle, platform_namespace(platform))


def load(db: CaptureDB) -> Suppressions:
    rows = db.conn.execute("SELECT kind, value FROM privacy_suppression").fetchall()
    return Suppressions(
        pseudonyms=frozenset(v for k, v in rows if k == "pseudonym"),
        repos=frozenset(v for k, v in rows if k == "repo"),
    )


def _check(kind: Kind, value: str) -> None:
    if kind == "pseudonym" and not PSEUDONYM_RE.match(value):
        raise ValueError("only pseudonyms (p_<16 hex>) may be stored, never raw handles")
    if kind == "repo" and not REPO_KEY_RE.match(value):
        raise ValueError("repo opt-outs are stored as '<host>:<numeric id>'")


def add(
    db: CaptureDB,
    kind: Kind,
    value: str,
    *,
    platform: str,
    reason: Reason,
    request_id: str | None = None,
) -> bool:
    """Add an entry; False if it was already on the list (idempotent)."""
    _check(kind, value)
    platform_namespace(platform)
    cur = db.conn.execute(
        "INSERT INTO privacy_suppression (kind, value, platform, reason, request_id) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (kind, value) DO NOTHING",
        (kind, value, platform, reason, request_id),
    )
    return cur.rowcount == 1


def remove(db: CaptureDB, kind: Kind, value: str) -> bool:
    cur = db.conn.execute(
        "DELETE FROM privacy_suppression WHERE kind = %s AND value = %s", (kind, value)
    )
    return cur.rowcount == 1


def entries(db: CaptureDB) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT kind, value, platform, reason, request_id, added_at "
        "FROM privacy_suppression ORDER BY added_at, kind, value"
    ).fetchall()
    keys = ("kind", "value", "platform", "reason", "request_id", "added_at")
    return [dict(zip(keys, r, strict=True)) for r in rows]
