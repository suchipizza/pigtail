"""Opt-out / refusal list (DPIA CB-13; FADP Art. 30(2)(b), GDPR Art. 21).

The list (`privacy_suppression`, migration 0003) holds only:

- **pseudonyms** (`p_<16 hex>`) of people who objected or asked for erasure. The handle given
  by the requester is pseudonymized immediately with `PSEUDONYM_KEY` in the platform's namespace
  and never stored; a CHECK constraint rejects anything that is not a pseudonym;
- **repo ids** (`<host>:<host_id>`, as in `repos.id`) of projects whose owner opted out;
- **repo name keys** (`rn_<32 hex>`, M1-T23): SHA-256 of `<host>:<owner/name>` (normalized,
  lowercase). They let an opt-out reach data about a repo that is not (yet) in `repos`, such as
  HN stories and mentions, matched by name. The name itself is not stored.

Connectors load the list once (`load()`) and drop matching records at ingest
(`pigtail.connectors.base.Connector.records`). Existing data is purged by
`pigtail.privacy.requests.purge_subject` / `purge_repo` (`pigtail privacy optout add|purge`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pigtail.pseudonymize import PLATFORM_NAMESPACES, PSEUDONYM_RE, Pseudonymizer

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB

Kind = Literal["pseudonym", "repo", "repo_name"]
Reason = Literal["objection", "erasure"]
REPO_KEY_RE = re.compile(r"^[a-z]+:[0-9]+$")
REPO_NAME_KEY_RE = re.compile(r"^rn_[0-9a-f]{32}$")
_FULL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38})/[a-z0-9._-]{1,100}$")


def normalize_repo_name(full_name: str) -> str:
    """`owner/name`, lowercase, without scheme, `github.com/`, trailing slash or `.git`."""
    n = full_name.strip().lower()
    for prefix in ("https://", "http://", "www.", "github.com/"):
        n = n.removeprefix(prefix)
    n = n.strip("/").removesuffix(".git")
    if not _FULL_NAME_RE.match(n):
        raise ValueError(f"expected owner/name, got {full_name!r}")
    return n


def repo_name_key(full_name: str, host: str = "github") -> str:
    """Refusal-list key of a repo name (M1-T23): `rn_` + 32 hex of SHA-256(`host:owner/name`)."""
    digest = hashlib.sha256(f"{host}:{normalize_repo_name(full_name)}".encode()).hexdigest()
    return "rn_" + digest[:32]


@dataclass(frozen=True)
class Suppressions:
    """In-memory snapshot of the refusal list, checked for every parsed record."""

    pseudonyms: frozenset[str] = frozenset()
    repos: frozenset[str] = frozenset()
    repo_names: frozenset[str] = frozenset()  # repo_name_key() values

    def __bool__(self) -> bool:
        return bool(self.pseudonyms or self.repos or self.repo_names)

    def name_suppressed(self, full_name: str | None, host: str = "github") -> bool:
        """True if `owner/name` is on the list by name (M1-T23). Unparseable names: False."""
        if not full_name or not self.repo_names:
            return False
        try:
            return repo_name_key(full_name, host) in self.repo_names
        except ValueError:
            return False


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
        repo_names=frozenset(v for k, v in rows if k == "repo_name"),
    )


def _check(kind: Kind, value: str) -> None:
    if kind == "pseudonym" and not PSEUDONYM_RE.match(value):
        raise ValueError("only pseudonyms (p_<16 hex>) may be stored, never raw handles")
    if kind == "repo" and not REPO_KEY_RE.match(value):
        raise ValueError("repo opt-outs are stored as '<host>:<numeric id>'")
    if kind == "repo_name" and not REPO_NAME_KEY_RE.match(value):
        raise ValueError("repo name opt-outs are stored as repo_name_key() hashes, never names")


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
