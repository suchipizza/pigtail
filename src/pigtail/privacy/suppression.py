"""Opt-out / refusal list (DPIA CB-13; FADP Art. 30(2)(b), GDPR Art. 21; ADR-071.1).

The list (`privacy_suppression`, migrations 0003, 0009, 0017) holds only:

- **opt-out fingerprints** (kind `person`, `p_<16 hex>`; PRD §7 `optout_fingerprint`) of people
  who objected or asked for erasure: HMAC-SHA256 with the opt-out key (`OPTOUT_KEY`, alias
  `PSEUDONYM_KEY`; kept apart from the data) of the handle in the platform's namespace. The
  handle given by the requester is hashed at once and never stored; a CHECK constraint rejects
  anything else. This is the only person-derived value pigtail keeps: coded data holds roles and
  buckets (`pigtail.privacy.roles`). Kind `person` was called `pseudonym` before migration 0017
  (same values);
- **repo ids** (`<host>:<host_id>`, as in `repos.id`) of projects whose owner opted out;
- **repo name keys** (`rk_<32 hex>`, kind `repo_name`; M1-T23, CB-13b): keyed HMAC-SHA256 with
  the opt-out key of the normalized, lowercase `owner/name` in namespace `repo_name`
  (`repo_name.<host>` for hosts other than GitHub). They let an opt-out reach data about a repo
  that is not (yet) in `repos`, such as HN stories and mentions, matched by name. The name itself
  is not stored, and without the key the hash cannot be reversed by a dictionary of public repo
  names (ADR-040.4).
- **legacy unkeyed name keys** (`rn_<32 hex>`, kind `repo_name_unkeyed`): entries written before
  migration 0009 as an unkeyed SHA-256 of `<host>:<owner/name>`. Their names cannot be recovered
  in SQL, so 0009 keeps them (still matched, so the opt-out keeps working) instead of dropping
  them; the database refuses new ones. `pigtail privacy optout rekey` converts every entry whose
  name is found in local data; re-adding a name (`optout add --repo`) converts it too; `pigtail
  doctor` warns while any are left.

Name matching needs the key: `load()` takes the `OptoutKey` (or reads `OPTOUT_KEY` /
`PSEUDONYM_KEY`) and fails closed (`MissingNameKey`) when keyed entries exist but no key is
available.

Key check (CB-25, ADR-043): when a key is available, `load()` first verifies it against the
fingerprint stored in the database (recording it on first use) and raises
`KeyFingerprintMismatch` if the key changed, since every keyed entry would silently stop matching.
The loaded list carries that fingerprint, and the connector base refuses an opt-out key with a
different key (`Suppressions.check_key`).

Connectors load the list once (`load()`) and drop matching records at ingest
(`pigtail.connectors.base.Connector.records`): the handles of each parsed record are hashed in
memory and compared with the list, then discarded. Existing data is purged by
`pigtail.privacy.requests.purge_subject` / `purge_repo` (`pigtail privacy optout add|purge`).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pigtail.pseudonymize import (
    PERSON_FINGERPRINT_RE,
    PLATFORM_NAMESPACES,
    OptoutKey,
    optout_key_from_env,
)

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB

Kind = Literal["person", "repo", "repo_name", "repo_name_unkeyed"]
Reason = Literal["objection", "erasure"]
REPO_KEY_RE = re.compile(r"^[a-z]+:[0-9]+$")
REPO_NAME_KEY_RE = re.compile(r"^rk_[0-9a-f]{32}$")
LEGACY_REPO_NAME_KEY_RE = re.compile(r"^rn_[0-9a-f]{32}$")
NAME_KEY_NAMESPACE = "repo_name"
KEY_ENV = "OPTOUT_KEY"  # alias: PSEUDONYM_KEY (pigtail.pseudonymize.optout_key_from_env)
Pseudonymizer = OptoutKey  # earlier name


class MissingNameKey(RuntimeError):
    """Keyed repo-name opt-outs exist but no opt-out key is available to match them."""


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


def name_key_namespace(host: str = "github") -> str:
    return NAME_KEY_NAMESPACE if host == "github" else f"{NAME_KEY_NAMESPACE}.{host}"


def repo_name_key(full_name: str, pz: OptoutKey, host: str = "github") -> str:
    """Refusal-list key of a repo name (M1-T23, CB-13b): `rk_` + 32 hex of
    HMAC-SHA256(opt-out key, `repo_name:<owner/name>`), name normalized and lowercase."""
    return "rk_" + pz.keyed_hex(normalize_repo_name(full_name), name_key_namespace(host))[:32]


def legacy_repo_name_key(full_name: str, host: str = "github") -> str:
    """The pre-0009 **unkeyed** key (`rn_` + SHA-256(`host:owner/name`)). Only for matching and
    converting legacy entries; never written (the database refuses new ones, CB-13b)."""
    digest = hashlib.sha256(f"{host}:{normalize_repo_name(full_name)}".encode()).hexdigest()
    return "rn_" + digest[:32]


NameKeyFn = Callable[[str, str], str]  # (full_name, host) -> rk_ key


@dataclass(frozen=True)
class Suppressions:
    """In-memory snapshot of the refusal list, checked for every parsed record."""

    persons: frozenset[str] = frozenset()  # opt-out fingerprints (kind `person`)
    repos: frozenset[str] = frozenset()
    repo_names: frozenset[str] = frozenset()  # repo_name_key() values (keyed, rk_)
    legacy_repo_names: frozenset[str] = frozenset()  # legacy_repo_name_key() values (rn_)
    name_key: NameKeyFn | None = field(default=None, compare=False, repr=False)
    key_fingerprint: str | None = None  # CB-25: fingerprint of the key the list was loaded under

    def check_key(self, pz: OptoutKey | None) -> None:
        """CB-25: raise `KeyFingerprintMismatch` if `pz` is not the key this list was verified
        against (a connector must hash handles with the same key the refusal list matches)."""
        fp = self.key_fingerprint
        if pz is not None and fp is not None and pz.fingerprint() != fp:
            from pigtail.privacy.key_fingerprint import KeyFingerprintMismatch

            raise KeyFingerprintMismatch()

    def __bool__(self) -> bool:
        return bool(self.persons or self.repos or self.repo_names or self.legacy_repo_names)

    def name_suppressed(self, full_name: str | None, host: str = "github") -> bool:
        """True if `owner/name` is on the list by name (M1-T23, CB-13b). Unparseable: False.

        Raises `MissingNameKey` if keyed entries exist but no key was given (fail closed)."""
        if not full_name or not (self.repo_names or self.legacy_repo_names):
            return False
        try:
            if self.legacy_repo_names and (
                legacy_repo_name_key(full_name, host) in self.legacy_repo_names
            ):
                return True
            if not self.repo_names:
                return False
            if self.name_key is None:
                raise MissingNameKey("repo-name opt-outs need OPTOUT_KEY to be matched (CB-13b)")
            return self.name_key(full_name, host) in self.repo_names
        except ValueError:
            return False


def platform_namespace(platform: str) -> str:
    try:
        return PLATFORM_NAMESPACES[platform]
    except KeyError as e:
        known = ", ".join(sorted(PLATFORM_NAMESPACES))
        raise ValueError(f"unknown platform {platform!r}; expected one of {known}") from e


def subject_fingerprint(pz: OptoutKey, platform: str, handle: str) -> str:
    """The opt-out fingerprint of `handle` on `platform`: what a connector for that platform
    computes in memory at ingest (CB-08, CB-13; ADR-071.1)."""
    if not handle.strip().lstrip("@"):
        raise ValueError("empty handle")
    return pz.person_fingerprint(handle, platform_namespace(platform))


subject_pseudonym = subject_fingerprint  # earlier name


def name_keyer(pz: OptoutKey) -> NameKeyFn:
    """`(full_name, host) -> repo_name_key(...)` bound to `pz`."""
    return lambda full_name, host: repo_name_key(full_name, pz, host)


def _key_from_env() -> OptoutKey | None:
    key = optout_key_from_env(os.environ)
    try:
        return OptoutKey(key) if key else None
    except ValueError:
        return None


def load(db: CaptureDB, pz: OptoutKey | None = None) -> Suppressions:
    """The refusal list. Repo-name entries are matched with `pz` (default: `OPTOUT_KEY` /
    `PSEUDONYM_KEY` from the environment); keyed entries without any key raise `MissingNameKey`
    (fail closed).

    With a key, it is first verified against the database's key fingerprint (CB-25; recorded on
    first use); a different key raises `KeyFingerprintMismatch`."""
    from pigtail.privacy import key_fingerprint

    pz = pz or _key_from_env()
    fp: str | None = None
    if pz is not None and key_fingerprint.verify(db.conn, pz) == "ok":
        fp = pz.fingerprint()
    rows = db.conn.execute("SELECT kind, value FROM privacy_suppression").fetchall()
    names = frozenset(v for k, v in rows if k == "repo_name")
    if names and pz is None:
        raise MissingNameKey(
            f"{len(names)} repo-name opt-out(s) need OPTOUT_KEY to be matched (CB-13b)"
        )
    return Suppressions(
        persons=frozenset(v for k, v in rows if k == "person"),
        repos=frozenset(v for k, v in rows if k == "repo"),
        repo_names=names,
        legacy_repo_names=frozenset(v for k, v in rows if k == "repo_name_unkeyed"),
        name_key=name_keyer(pz) if pz is not None else None,
        key_fingerprint=fp,
    )


def _check(kind: Kind, value: str) -> None:
    if kind == "person" and not PERSON_FINGERPRINT_RE.match(value):
        raise ValueError("only opt-out fingerprints (p_<16 hex>) may be stored, never handles")
    if kind == "repo" and not REPO_KEY_RE.match(value):
        raise ValueError("repo opt-outs are stored as '<host>:<numeric id>'")
    if kind == "repo_name" and not REPO_NAME_KEY_RE.match(value):
        raise ValueError("repo name opt-outs are stored as repo_name_key() hashes, never names")
    if kind == "repo_name_unkeyed":
        raise ValueError("unkeyed repo-name hashes are legacy (CB-13b); use repo_name_key()")


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


def remove_legacy_name(db: CaptureDB, full_name: str, host: str = "github") -> bool:
    """Drop the legacy unkeyed entry for `owner/name`, if any (after the keyed one is added)."""
    cur = db.conn.execute(
        "DELETE FROM privacy_suppression WHERE kind = 'repo_name_unkeyed' AND value = %s",
        (legacy_repo_name_key(full_name, host),),
    )
    return cur.rowcount == 1


def legacy_count(db: CaptureDB) -> int:
    row = db.conn.execute(
        "SELECT count(*) FROM privacy_suppression WHERE kind = 'repo_name_unkeyed'"
    ).fetchone()
    return int(row[0]) if row else 0


def entries(db: CaptureDB) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT kind, value, platform, reason, request_id, added_at "
        "FROM privacy_suppression ORDER BY added_at, kind, value"
    ).fetchall()
    keys = ("kind", "value", "platform", "reason", "request_id", "added_at")
    return [dict(zip(keys, r, strict=True)) for r in rows]
