from __future__ import annotations

import pytest

from pigtail.config import Settings, parse_overrides
from pigtail.pseudonymize import Pseudonymizer


def test_r15_1_default_backend_is_subscription():
    assert Settings.from_env({}).llm_backend == "subscription"


def test_r15_1_backend_switch_via_env():
    assert Settings.from_env({"LLM_BACKEND": "api"}).llm_backend == "api"
    with pytest.raises(ValueError):
        Settings.from_env({"LLM_BACKEND": "openai"})


def test_r15_5_overrides_parse():
    assert parse_overrides("tier2_extraction:api, pilot:subscription") == {
        "tier2_extraction": "api",
        "pilot": "subscription",
    }
    with pytest.raises(ValueError):
        parse_overrides("nocolon")


def test_prd10_pseudonym_stable_keyed_and_namespaced(pz):
    assert pz.pseudonym("Alice") == pz.pseudonym("@alice")
    assert pz.pseudonym("alice", "github") != pz.pseudonym("alice", "hn")
    assert Pseudonymizer("another-key-0123456789").pseudonym("alice") != pz.pseudonym("alice")


def test_prd10_strip_identifiers(pz):
    out = pz.strip_identifiers("by @bob, contact bob@example.org or +41 79 123 45 67")
    assert "bob" not in out.replace(pz.pseudonym("bob"), "")
    assert "[email]" in out and "[phone]" in out


def test_package_scopes_and_urls_untouched(pz):
    text = "npm i @scope/pkg and see https://github.com/org/repo"
    assert pz.strip_identifiers(text) == text


def test_key_required():
    with pytest.raises(ValueError):
        Pseudonymizer("short")
