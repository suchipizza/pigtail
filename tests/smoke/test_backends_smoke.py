"""M0 acceptance: structured-output smoke test on each real backend (PRD F15).

Subscription: runs only with PIGTAIL_RUN_SMOKE=1 and a logged-in `claude` CLI (it uses the
operator's plan). API (M21b): runs only with PIGTAIL_RUN_SMOKE=1 and ANTHROPIC_API_KEY, and makes
one tiny call on the relevance-stage model (Haiku, R15.8): a few hundred tokens, about USD 0.001.
"""

from __future__ import annotations

import os
import shutil

import pytest

from pigtail.cli import SMOKE_SYSTEM, SMOKE_TEMPLATE, SmokeOutput
from pigtail.llm import LLMClient, PromptSpec
from pigtail.llm.redact import alias_redact
from pigtail.llm.store import LLMStore

PROMPT = PromptSpec(id="smoke", version="1", system=SMOKE_SYSTEM, template=SMOKE_TEMPLATE)


def _client(backend_name: str, backend: object) -> LLMClient:
    from pigtail.config import stage_models

    return LLMClient(
        backends={backend_name: backend},  # type: ignore[dict-item]
        default_backend=backend_name,  # type: ignore[arg-type]
        store=LLMStore(":memory:"),
        models={str(k): v for k, v in stage_models(dict(os.environ)).items()},
        redactor=alias_redact,
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
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_r15_8_api_single_tiny_haiku_call_smoke():
    """One standard call on the relevance model (job `smoke`), structured output, cache marker
    on the system prompt; the provenance names the model and stage."""
    from pigtail.llm.api import ApiBackend

    client = _client("api", ApiBackend(max_tokens=256))
    r = client.complete(PROMPT, "pigtail", SmokeOutput, job="smoke", use_cache=False)
    assert r.output.answer == 42
    assert r.stage == "relevance" and r.model.startswith("claude-haiku-4-5")
    (row,) = client.store.summary().values()
    assert row["sessions"] == 1 and 0 < row["cost_usd"] < 0.01
