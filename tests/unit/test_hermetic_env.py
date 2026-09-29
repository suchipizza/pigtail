"""The suite is hermetic (verifier M24 round 2): an instance `.env` loaded in the shell never
reaches a test. Run it as `env LLM_BACKEND=api GITHUB_TOKEN=x ... pytest` to check."""

from __future__ import annotations

import os

from tests.conftest import _ENV_NAMES, _ENV_PREFIXES, _hermetic_env


def test_hermetic_env_clears_instance_settings() -> None:
    leaked = [
        k
        for k in os.environ
        if (k in _ENV_NAMES or k.startswith(_ENV_PREFIXES))
        and not k.startswith("PIGTAIL_REQUIRE_")
        and k not in ("PIGTAIL_RUN_SMOKE", "ANTHROPIC_API_KEY")
        # set by the suite's own autouse fixtures
        and k
        not in ("PIGTAIL_BRIEFS_DIR", "PIGTAIL_SELECTION_PRODUCT_HUNT", "PIGTAIL_SELECTION_BLUESKY")
    ]
    assert leaked == []


def test_hermetic_env_drops_a_leaked_key_but_keeps_test_controls() -> None:
    os.environ["LLM_BACKEND"] = "api"
    os.environ["GITHUB_TOKEN"] = "not-a-real-token"
    os.environ["PIGTAIL_RUN_SMOKE"] = os.environ.get("PIGTAIL_RUN_SMOKE", "0")
    had_smoke = os.environ["PIGTAIL_RUN_SMOKE"] != "0"
    try:
        dropped = _hermetic_env()
        assert {"LLM_BACKEND", "GITHUB_TOKEN"} <= set(dropped)
        assert "LLM_BACKEND" not in os.environ and "PIGTAIL_RUN_SMOKE" in os.environ
    finally:
        os.environ.pop("LLM_BACKEND", None)
        os.environ.pop("GITHUB_TOKEN", None)
        if not had_smoke:
            os.environ.pop("PIGTAIL_RUN_SMOKE", None)
