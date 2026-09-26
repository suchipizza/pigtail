"""R18.7 smoke test: one real expansion proposal for the synthetic example brief on the
subscription backend. Runs only with PIGTAIL_RUN_SMOKE=1 and a logged-in `claude` CLI (it uses
the operator's plan, like `test_backends_smoke.py`). Nothing is saved.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from pigtail.briefs.budget import BudgetGuard
from pigtail.briefs.expansion import propose_expansion
from pigtail.briefs.model import load_brief_text
from pigtail.llm import LLMClient
from pigtail.llm.redact import alias_redact
from pigtail.llm.store import LLMStore

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"


@pytest.mark.smoke
@pytest.mark.skipif(os.environ.get("PIGTAIL_RUN_SMOKE") != "1", reason="set PIGTAIL_RUN_SMOKE=1")
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_r18_7_expansion_proposal_on_subscription_smoke():
    from pigtail.llm.subscription import SubscriptionBackend

    store = LLMStore(":memory:")
    client = LLMClient(
        backends={"subscription": SubscriptionBackend()},
        default_backend="subscription",
        store=store,
        models={"synthesis": os.environ.get("LLM_MODEL_SYNTHESIS", "claude-opus-5-5")},
        redactor=alias_redact,
    )
    brief = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    budget = brief.budget.model_copy(update={"llm_backend": "subscription"})
    brief = brief.model_copy(update={"budget": budget})
    guard = BudgetGuard(budget=brief.budget, usage=store)
    p = propose_expansion(brief, client, guard)
    assert p.expansion.problem_statement
    assert p.expansion.keywords and p.expansion.provenance is not None
    assert p.to_dict()["saved"] is False
