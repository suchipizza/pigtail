"""DPIA CB-06: identifier redaction before LLM calls (profile URLs, DIDs, per-source namespaces).

Handles become per-call, keyless aliases (`user1`, `user2`, ...; ADR-066.1, ADR-074). The one
implementation is `pigtail.llm.redact.alias_redact`; `OptoutKey.strip_identifiers` delegates to
it. All handles here are synthetic.
"""

from __future__ import annotations

import re

import pytest

from pigtail.llm.redact import alias_redact as redact
from pigtail.pseudonymize import scrub_identifiers
from tests.conftest import Echo, FakeBackend, make_client

# A keyed token: an opt-out fingerprint `p_<16 hex>` or any run of 16+ hex chars (HMAC output).
_KEYED = re.compile(r"\bp_[0-9a-f]+|[0-9a-f]{16,}", re.IGNORECASE)


@pytest.mark.parametrize(
    ("text", "login"),
    [
        ("see https://github.com/user0001 for details", "user0001"),
        ("http://www.github.com/user0001", "user0001"),
        ("github.com/user0001?tab=repositories", "user0001"),
        ("https://github.com/user0001/", "user0001"),
        ("(https://github.com/user0001)", "user0001"),
        ("https://github.com/sponsors/user0002", "user0002"),
        ("https://api.github.com/users/user0003", "user0003"),
    ],
)
def test_cb06_github_profile_urls_redacted(text, login):
    out = redact(text)
    assert login not in out
    assert "[profile:github:user1]" in out


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/org-a/repo-1",
        "see github.com/org-a/repo-1/issues/12 and https://github.com/org-a/repo-1.",
        "https://github.com/features and https://github.com/topics/cli",
        "https://github.com/",
        "https://example-github.com/user0001",
        "https://gitlab.com/user0001",
    ],
)
def test_cb06_repo_paths_and_site_pages_kept(text):
    assert redact(text) == text


def test_cb06_trailing_punctuation_kept_outside_the_token():
    out = redact("Made by https://github.com/user0001.")
    assert out == "Made by [profile:github:user1]."


def test_cb06_bluesky_profile_handle_and_did():
    out = redact("https://bsky.app/profile/tester.example.social/post/3kxyz")
    assert "tester" not in out
    assert out == "[profile:bluesky:user1]/post/3kxyz"
    did = "did:plc:" + "a" * 24
    out = redact(f"bsky.app/profile/{did}, hi")
    assert did not in out and out == "[profile:bluesky:user1], hi"


def test_cb06_hn_profile_urls():
    for page in ("user", "submitted", "threads", "favorites"):
        out = redact(f"https://news.ycombinator.com/{page}?id=hn_tester9 ok")
        assert "hn_tester9" not in out
        assert out == "[profile:hn:user1] ok"
    item = "https://news.ycombinator.com/item?id=41234567"
    assert redact(item) == item  # story ids are not people


def test_cb06_dids_redacted():
    plc = "did:plc:" + "b2" * 12
    out = redact(f"author {plc} and did:web:tester.example.org, again {plc}.")
    assert "did:plc" not in out and "tester.example.org" not in out
    assert out == "author [did:user1] and [did:user2], again [did:user1]."


def test_cb06_per_source_namespace_for_mentions():
    assert redact("thanks @user0001", namespace="github") == "thanks @user1"
    assert redact("@user0001") == "@user1"  # the default namespace is "generic"
    # namespaces stay separate: the same handle on a profile URL (platform "github") and as a
    # mention in the "hn" namespace is two people, so two aliases
    out = redact("https://github.com/user0001 and @user0001", namespace="hn")
    assert out == "[profile:github:user1] and @user2"
    # ... while a mention in the "github" namespace is the same person as the GitHub profile
    out = redact("https://github.com/user0001 and @User0001", namespace="github")
    assert out == "[profile:github:user1] and @user1"


def test_cb06_profile_url_uses_platform_namespace_whatever_the_caller_says():
    a = redact("https://github.com/user0001", namespace="hn")
    b = redact("https://github.com/user0001", namespace="generic")
    assert a == b == "[profile:github:user1]"


def test_adr074_aliases_are_per_call_not_shared_across_inputs():
    """Same handle twice in one input -> same alias; two inputs never share an alias mapping."""
    one = redact("@user0001 and @user0002, then @user0001 again")
    assert one == "@user1 and @user2, then @user1 again"
    two = redact("@user0002 only")
    assert two == "@user1 only"  # user0002 was user2 above: numbering restarts per call


def test_adr074_strip_identifiers_delegates_to_the_one_alias_implementation(pz):
    text = "@user0001 https://github.com/user0002 did:plc:" + "d" * 24 + " @user0001"
    assert pz.strip_identifiers(text, namespace="github") == redact(text, namespace="github")


@pytest.mark.parametrize("namespace", ["generic", "github", "hn", "bluesky"])
def test_adr074_output_has_no_keyed_token(pz, namespace):
    """Nothing keyed reaches the model: no `p_` fingerprint and no HMAC-looking hex."""
    text = (
        "@user0001 https://github.com/user0002 https://bsky.app/profile/tester.example.social "
        "https://news.ycombinator.com/user?id=hn_tester9 did:plc:" + "e" * 24 + " @user0001"
    )
    for out in (redact(text, namespace), pz.strip_identifiers(text, namespace)):
        assert "p_" not in out
        assert not _KEYED.search(out), out
        for h in ("user0001", "user0002", "tester", "hn_tester9"):
            assert h not in out
        assert pz.person_fingerprint("user0001", namespace) not in out


@pytest.mark.parametrize(
    "text",
    [
        "released 2026-09-25 with 25000 (2024) stars",
        "doi 10.1145/3597503.3639148, v1.2.3",
        "https://doi.org/10.1145/3597503.3639148",
        "npm i @scope/pkg",
    ],
)
def test_cb06_dates_counts_dois_untouched(text):
    assert redact(text, namespace="github") == text


def test_cb06_keyless_scrub_has_no_pseudonyms():
    out = scrub_identifiers(
        "@user0001 https://github.com/user0001 did:plc:" + "c" * 24 + " a@example.org"
    )
    assert out == "@[handle] [profile:github] [did] [email]"
    assert "p_" not in out


def test_cb06_llm_client_redacts_with_namespace(prompt):
    c = make_client([{"value": 1, "label": "a"}])
    c.complete(
        prompt,
        "@user0001 at https://bsky.app/profile/tester.example.social",
        Echo,
        job="j",
        namespace="github",
    )
    fake = c.backends["subscription"]
    assert isinstance(fake, FakeBackend)
    sent = fake.calls[0]["prompt"]
    assert "user0001" not in sent and "tester" not in sent
    # ADR-066 follow-up: per-call, non-keyed aliases on the LLM path
    assert "[profile:bluesky:user1]" in sent and "@user2" in sent and "p_" not in sent
