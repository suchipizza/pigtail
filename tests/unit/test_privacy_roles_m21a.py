"""M21a unit tests: roles and buckets (Directive §8.1, ADR-066.1, PRD R5.3 / §7 `actor`), the
opt-out key and its `PSEUDONYM_KEY` alias (ADR-071.1), the maintainer rule, and in-memory
de-duplication of per-repo events (ADR-071.2). Synthetic values only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pigtail.capture.mentions import author_code
from pigtail.capture.repo_events import _Counts
from pigtail.config import Settings
from pigtail.privacy.roles import (
    BUCKETS,
    ROLES,
    ActorCode,
    code_actor,
    follower_bucket,
    owned_repos,
)
from pigtail.pseudonymize import (
    ConflictingKeys,
    OptoutKey,
    Pseudonymizer,
    optout_key_from_env,
)

KEY = "test-key-not-secret-0123456789"  # gitleaks:allow
OTHER = "another-test-key-not-secret-000"  # gitleaks:allow


def test_r5_3_role_and_bucket_vocabulary():
    assert ROLES == (
        "maintainer",
        "account",
        "newsletter",
        "community",
        "organization",
        "automated_account",
    )
    assert BUCKETS == ("r0", "r1", "r2", "r3", "r4")  # codebook §4.5 reach bands


@pytest.mark.parametrize(
    ("count", "bucket"),
    [
        (None, "r0"),
        ("12", "r0"),
        (True, "r0"),
        (-1, "r0"),
        (0, "r1"),
        (999, "r1"),
        (1_000, "r2"),
        (9_999, "r2"),
        (10_000, "r3"),
        (99_999, "r3"),
        (100_000, "r4"),
    ],
)
def test_r5_3_follower_bucket_edges(count, bucket):
    assert follower_bucket(count) == bucket


def test_adr_071_2_code_actor_precedence_and_invariants():
    assert code_actor(automated=True, maintainer=True).role == "automated_account"
    assert code_actor(automated=False, maintainer=True).role == "maintainer"
    assert code_actor(automated=False, organization=True).role == "organization"
    a = code_actor(automated=False, followers=25_000)
    assert (a.role, a.bucket, a.automated_account) == ("account", "r3", False)
    assert a.bot_rule_version == "bot-filter-v0" and a.role_rule_version == "roles-v1"
    with pytest.raises(ValueError):
        ActorCode("account", "r0", True)  # the flag and the role must agree
    with pytest.raises(ValueError):
        ActorCode("person", "r0", False)  # type: ignore[arg-type]


def test_m21a_maintainer_rule_is_owner_match_in_memory():
    assert owned_repos("Org-A", ["org-a/repo-1", "org-b/x", 3]) == ["org-a/repo-1"]
    assert owned_repos(None, ["org-a/repo-1"]) == []
    rec = {"automated_account": False, "actor_owns": ["org-a/repo-1"], "actor_bucket": "r0"}
    assert author_code(rec, "Org-A/Repo-1").role == "maintainer"
    assert author_code(rec, "org-b/other").role == "account"
    assert author_code({"automated_account": True}, "org-a/repo-1").role == "automated_account"


# --- opt-out key ---------------------------------------------------------------------------------
def test_adr_071_1_optout_key_env_and_legacy_alias():
    assert optout_key_from_env({"OPTOUT_KEY": KEY}) == KEY
    assert optout_key_from_env({"PSEUDONYM_KEY": KEY}) == KEY  # the earlier name still works
    assert optout_key_from_env({"OPTOUT_KEY": KEY, "PSEUDONYM_KEY": KEY}) == KEY
    assert optout_key_from_env({"OPTOUT_KEY": " ", "PSEUDONYM_KEY": ""}) is None
    with pytest.raises(ConflictingKeys):
        optout_key_from_env({"OPTOUT_KEY": KEY, "PSEUDONYM_KEY": OTHER})
    s = Settings.from_env({"OPTOUT_KEY": KEY})
    assert s.optout_key == s.pseudonym_key == KEY
    assert KEY not in repr(s)  # CB-29: still masked


def test_adr_071_1_fingerprint_is_the_earlier_pseudonym_value():
    """Existing opt-outs keep matching: the fingerprint equals the pre-0017 pseudonym."""
    assert Pseudonymizer is OptoutKey
    k = OptoutKey(KEY)
    assert k.person_fingerprint("@User0001", "github") == k.pseudonym("user0001", "github")
    assert k.person_fingerprint("user0001", "github") != k.person_fingerprint("user0001", "hn")
    assert k.fingerprint().startswith("kfp1_")  # CB-25 key fingerprint unchanged
    with pytest.raises(ValueError, match="OPTOUT_KEY"):
        OptoutKey("short")


# --- per-repo events: counts in memory -----------------------------------------------------------
def test_adr_071_2_events_deduplicated_within_a_poll_then_only_counts_remain():
    c = _Counts()
    t = datetime(2026, 9, 25, 12, 5, tzinfo=UTC)
    star = {"type": "WatchEvent", "automated_account": False}
    c.add({**star, "_actor_token": "a" * 16}, t)
    c.add({**star, "_actor_token": "a" * 16}, t)  # the same account again in this poll/hour
    c.add({**star, "_actor_token": "b" * 16}, t)
    c.add({"type": "WatchEvent", "automated_account": True, "_actor_token": None}, t)
    c.add({"type": "ForkEvent", "automated_account": False, "_actor_token": "a" * 16}, t)
    (hour,) = c.hours
    assert hour == datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert c.hours[hour].as_tuple() == (2, 1, 1, 0)
