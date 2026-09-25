"""M0 acceptance: structured-output smoke test on each real backend (PRD F15).

Subscription: runs only with PIGTAIL_RUN_SMOKE=1 and a logged-in `claude` CLI (it uses the
operator's plan). API: skipped when ANTHROPIC_API_KEY is absent.
"""

from __future__ import annotations

import os
import shutil

import pytest

from pigtail.cli import SMOKE_SYSTEM, SMOKE_TEMPLATE, SmokeOutput
from pigtail.llm import LLMClient, PromptSpec
from pigtail.llm.store import LLMStore
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY

PROMPT = PromptSpec(id="smoke", version="1", system=SMOKE_SYSTEM, template=SMOKE_TEMPLATE)


def _client(backend_name: str, backend: object) -> LLMClient:
    return LLMClient(
        backends={backend_name: backend},  # type: ignore[dict-item]
        default_backend=backend_name,  # type: ignore[arg-type]
        store=LLMStore(":memory:"),
        model=os.environ.get("LLM_MODEL", "claude-opus-5"),
        redactor=Pseudonymizer(TEST_KEY).strip_identifiers,
    )


@pytest.mark.smoke
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_subscription_structured_output_smoke():
    from pigtail.llm.subscription import SubscriptionBackend

    r = _client("subscription", SubscriptionBackend()).complete(
        PROMPT, "pigtail", SmokeOutput, job="smoke", use_cache=False
    )
    assert r.output.answer == 42


@pytest.mark.smoke
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_api_structured_output_smoke():
    from pigtail.llm.api import ApiBackend

    r = _client("api", ApiBackend()).complete(
        PROMPT, "pigtail", SmokeOutput, job="smoke", use_cache=False
    )
    assert r.output.answer == 42
