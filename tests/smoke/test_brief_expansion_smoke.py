"""R18.7 smoke test: one real expansion proposal for the synthetic example brief on the
subscription backend. Runs only with PIGTAIL_RUN_SMOKE=1 and a logged-in `claude` CLI (it uses
the operator's plan, like `test_backends_smoke.py`). Nothing is saved.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from pigtail.briefs.budget import Allowance, BudgetGuard
from pigtail.briefs.expansion import propose_expansion
from pigtail.briefs.model import load_brief_text
from pigtail.llm import LLMClient
from pigtail.llm.store import LLMStore
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY

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
        model=os.environ.get("LLM_MODEL", "claude-opus-5"),
        redactor=Pseudonymizer(TEST_KEY).strip_identifiers,
    )
    brief = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    guard = BudgetGuard(budget=brief.budget, usage=store, allowance=Allowance(10**9, "configured"))
    p = propose_expansion(brief, client, guard)
    assert p.expansion.problem_statement
    assert p.expansion.keywords and p.expansion.provenance is not None
    assert p.to_dict()["saved"] is False
