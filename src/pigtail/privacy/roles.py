"""Roles and buckets: how people appear in coded data (Directive §8.1, ADR-066.1, ADR-071.2;
PRD R5.3 and the §7 `actor` entity; codebook §4.2, §4.5).

Coded and stored records **never** hold a handle, a personal name or a pseudonym of an individual.
An actor is coded at ingest, in memory, into:

- a **role** (`ROLES`): `maintainer` (the account that owns the repo the record is about),
  `account` (any other individual account, coded with its follower bucket), `newsletter` and
  `community` (venues with their own name), `organization` (an organisation account; may be
  named), `automated_account` (a bot, by the versioned bot rule);
- a **follower bucket** (`BUCKETS`, codebook §4.5 reach bands, log10): `r0` unknown, `r1` under
  1,000, `r2` 1,000-9,999, `r3` 10,000-99,999, `r4` 100,000 or more. The exact count is used
  only to compute the band and then discarded. Follower *lists* are never collected (§8.3);
- an **automated-account flag** plus the **bot-rule version** that set it (ADR-071.2), so results
  stay reproducible without the handle.

The handle itself is used transiently (bot rules, maintainer match, opt-out matching and
de-duplication within one run) and dropped before anything is stored. The only person-derived
value that is kept anywhere is the opt-out fingerprint of someone who opted out (ADR-071.1;
`pigtail.privacy.suppression`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pigtail.capture.botfilter import BOT_FILTER_VERSION

Role = Literal[
    "maintainer", "account", "newsletter", "community", "organization", "automated_account"
]
ROLES: tuple[Role, ...] = (
    "maintainer",
    "account",
    "newsletter",
    "community",
    "organization",
    "automated_account",
)
FollowerBucket = Literal["r0", "r1", "r2", "r3", "r4"]
BUCKETS: tuple[FollowerBucket, ...] = ("r0", "r1", "r2", "r3", "r4")
# Lower bounds of r2, r3, r4 (codebook §4.5, v0 choice); r1 is anything known below 1,000.
BUCKET_EDGES: tuple[int, int, int] = (1_000, 10_000, 100_000)
# Version of the role rules below (maintainer = handle equals the repo owner, case-insensitive).
ROLE_RULE_VERSION = "roles-v1"


def follower_bucket(count: Any) -> FollowerBucket:
    """The reach band of a follower count (`r0` when unknown or not a non-negative int)."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return "r0"
    if count < BUCKET_EDGES[0]:
        return "r1"
    if count < BUCKET_EDGES[1]:
        return "r2"
    if count < BUCKET_EDGES[2]:
        return "r3"
    return "r4"


def owned_repos(handle: str | None, repo_full_names: Any) -> list[str]:
    """Repos (`owner/name`, lowercase) among `repo_full_names` whose owner is `handle`.

    In memory only: it lets a capture job code the author as `maintainer` of the repo it is
    about without keeping the handle."""
    if not handle or not isinstance(repo_full_names, list):
        return []
    h = handle.strip().lstrip("@").lower()
    out = []
    for n in repo_full_names:
        if isinstance(n, str) and "/" in n and n.split("/", 1)[0].lower() == h:
            out.append(n.lower())
    return out


@dataclass(frozen=True)
class ActorCode:
    """The coded actor of one record: what is stored instead of a handle."""

    role: Role
    bucket: FollowerBucket
    automated_account: bool
    bot_rule_version: str = BOT_FILTER_VERSION
    role_rule_version: str = ROLE_RULE_VERSION

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")
        if self.bucket not in BUCKETS:
            raise ValueError(f"unknown follower bucket {self.bucket!r}")
        if self.automated_account != (self.role == "automated_account"):
            raise ValueError("automated_account and role 'automated_account' go together")


def code_actor(
    *,
    automated: bool,
    maintainer: bool = False,
    organization: bool = False,
    followers: Any = None,
) -> ActorCode:
    """Role and bucket of one actor. Precedence: automated > maintainer > organization >
    account. The follower count, if any, only sets the bucket and is not kept."""
    role: Role
    if automated:
        role = "automated_account"
    elif maintainer:
        role = "maintainer"
    elif organization:
        role = "organization"
    else:
        role = "account"
    return ActorCode(role=role, bucket=follower_bucket(followers), automated_account=automated)
