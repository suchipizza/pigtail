"""DPIA CB-06: identifier redaction before LLM calls (profile URLs, DIDs, per-source namespaces).

All handles here are synthetic.
"""

from __future__ import annotations

import pytest

from pigtail.pseudonymize import Pseudonymizer, scrub_identifiers
from tests.conftest import Echo, FakeBackend, make_client


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
def test_cb06_github_profile_urls_redacted(pz, text, login):
    out = pz.strip_identifiers(text)
    assert login not in out
    assert f"[profile:github:{pz.pseudonym(login, 'github')}]" in out


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
def test_cb06_repo_paths_and_site_pages_kept(pz, text):
    assert pz.strip_identifiers(text) == text


def test_cb06_trailing_punctuation_kept_outside_the_token(pz):
    out = pz.strip_identifiers("Made by https://github.com/user0001.")
    assert out == f"Made by [profile:github:{pz.pseudonym('user0001', 'github')}]."


def test_cb06_bluesky_profile_handle_and_did(pz):
    out = pz.strip_identifiers("https://bsky.app/profile/tester.example.social/post/3kxyz")
    assert "tester" not in out
    p = pz.pseudonym("tester.example.social", "bluesky")
    assert out == f"[profile:bluesky:{p}]/post/3kxyz"
    did = "did:plc:" + "a" * 24
    out = pz.strip_identifiers(f"bsky.app/profile/{did}, hi")
    assert did not in out and out.endswith(", hi")


def test_cb06_hn_profile_urls(pz):
    for page in ("user", "submitted", "threads", "favorites"):
        out = pz.strip_identifiers(f"https://news.ycombinator.com/{page}?id=hn_tester9 ok")
        assert "hn_tester9" not in out
        assert out == f"[profile:hn:{pz.pseudonym('hn_tester9', 'hn')}] ok"
    item = "https://news.ycombinator.com/item?id=41234567"
    assert pz.strip_identifiers(item) == item  # story ids are not people


def test_cb06_dids_redacted(pz):
    plc = "did:plc:" + "b2" * 12
    out = pz.strip_identifiers(f"author {plc} and did:web:tester.example.org.")
    assert "did:plc" not in out and "tester.example.org" not in out
    assert f"[did:{pz.pseudonym(plc, 'bluesky')}]" in out
    assert out.endswith(".")


def test_cb06_per_source_namespace_for_mentions(pz):
    gh = pz.strip_identifiers("thanks @user0001", namespace="github")
    hn = pz.strip_identifiers("thanks @user0001", namespace="hn")
    assert gh == f"thanks @{pz.pseudonym('user0001', 'github')}"
    assert gh != hn
    # the default stays "generic" (backwards compatible)
    assert pz.strip_identifiers("@user0001") == "@" + pz.pseudonym("user0001")


def test_cb06_profile_url_uses_platform_namespace_whatever_the_caller_says(pz):
    a = pz.strip_identifiers("https://github.com/user0001", namespace="hn")
    b = pz.strip_identifiers("https://github.com/user0001", namespace="generic")
    assert a == b


@pytest.mark.parametrize(
    "text",
    [
        "released 2026-09-25 with 25000 (2024) stars",
        "doi 10.1145/3597503.3639148, v1.2.3",
        "https://doi.org/10.1145/3597503.3639148",
        "npm i @scope/pkg",
    ],
)
def test_cb06_dates_counts_dois_untouched(pz, text):
    assert pz.strip_identifiers(text, namespace="github") == text


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
    pz = Pseudonymizer("test-key-not-secret-0123456789")
    assert "user0001" not in sent and "tester" not in sent
    assert "@" + pz.pseudonym("user0001", "github") in sent
