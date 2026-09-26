"""M12 budget hard stop (PRD R18.5; ADR-053.1), tested with tiny budgets.

The subscription share is metered from the real `LLMStore` usage ledger (in memory).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pigtail.briefs.budget import (
    ALLOWANCE_ENV,
    ASSUMED_WEEKLY_TOKENS,
    Allowance,
    BudgetGuard,
    BudgetStop,
    resolve_allowance,
)
from pigtail.briefs.model import Budget
from pigtail.llm.store import LLMStore, UsageRow


def ledger(tokens: int = 0, status: str = "ok", backend: str = "subscription") -> LLMStore:
    s = LLMStore(":memory:")
    if tokens:
        s.record(UsageRow(backend, "job", "m", "p", "1", status, tokens, 0))
    return s


def guard(budget: Budget, store: LLMStore, allowance: int = 1_000, **kw: object) -> BudgetGuard:
    return BudgetGuard(budget, store, Allowance(allowance, "configured"), **kw)  # type: ignore[arg-type]


def test_r18_5_money_default_zero_stops_any_paid_step_even_when_approved():
    g = guard(Budget(), ledger(), approved_paid=True)
    g.check_paid("free step", 0.0)  # free steps always pass
    with pytest.raises(BudgetStop) as ei:
        g.charge_paid("x collection", 0.01)
    assert ei.value.kind == "money"
    assert g.spent_money_usd == 0.0  # nothing charged


def test_adr_053_paid_step_needs_explicit_approval():
    g = guard(Budget(money_usd=100), ledger())
    with pytest.raises(BudgetStop) as ei:
        g.check_paid("trendshift collection", 5.0)
    assert ei.value.kind == "approval"
    assert "--approve-paid" in ei.value.detail


def test_r18_5_tiny_money_budget_hard_stops_at_the_cap():
    g = guard(Budget(money_usd=1.0), ledger(), approved_paid=True)
    g.charge_paid("page 1", 0.4)
    g.charge_paid("page 2", 0.4)
    with pytest.raises(BudgetStop) as ei:
        g.charge_paid("page 3", 0.4)
    assert ei.value.kind == "money" and ei.value.step == "page 3"
    assert g.spent_money_usd == pytest.approx(0.8)
    assert [e["step"] for e in g.log] == ["page 1", "page 2"]


def test_r18_5_resumed_run_carries_its_spend():
    g = guard(Budget(money_usd=1.0), ledger(), approved_paid=True, spent_money_usd=0.9)
    with pytest.raises(BudgetStop):
        g.check_paid("next", 0.2)


def test_adr_053_subscription_share_stop_uses_the_usage_ledger():
    store = ledger(400)  # 400 of 1,000 tokens used this week
    g = guard(Budget(subscription_share=0.5), store)  # cap 500
    g.check_llm("chunk 1", est_tokens=100)  # 500: at the cap, allowed
    store.record(UsageRow("subscription", "job", "m", "p", "1", "ok", 100, 0))
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("chunk 2", est_tokens=1)
    assert ei.value.kind == "subscription_share"
    assert "estimate" in ei.value.detail
    # cache hits and limit events carry no spend
    store.record(UsageRow("subscription", "job", "m", "p", "1", "cached", 10_000, 0))
    assert g.subscription_used() == 500


def test_adr_053_api_backend_capped_by_llm_api_usd_and_approval():
    g = guard(Budget(llm_backend="api", llm_api_usd=0.05), ledger())
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("extract", est_tokens=10, est_usd=0.01)
    assert ei.value.kind == "approval"
    g = guard(Budget(llm_backend="api", llm_api_usd=0.05), ledger(), approved_paid=True)
    g.check_llm("extract", est_tokens=10, est_usd=0.04)
    g.charge_api("extract", 0.04)
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("extract 2", est_tokens=10, est_usd=0.02)
    assert ei.value.kind == "api_usd"


def test_adr_053_never_switches_backend_on_its_own():
    g = guard(Budget(), ledger())
    g.check_backend("subscription", job="extraction")
    with pytest.raises(BudgetStop) as ei:
        g.check_backend("api", job="extraction")
    assert ei.value.kind == "backend"
    # an explicit per-job override (R15.5) is the only way
    g = guard(Budget(), ledger(), overrides={"extraction": "api"})
    g.check_backend("api", job="extraction")


def test_allowance_configured_calibrated_or_assumed():
    assert resolve_allowance(None, {ALLOWANCE_ENV: "1234"}) == Allowance(1234, "configured")
    with pytest.raises(ValueError):
        resolve_allowance(None, {ALLOWANCE_ENV: "0"})
    assert resolve_allowance(ledger(), {}) == Allowance(ASSUMED_WEEKLY_TOKENS, "assumed_default")
    store = ledger(700)
    store.record(UsageRow("subscription", "job", "m", "p", "1", "limit"))
    a = resolve_allowance(store, {})
    assert a == Allowance(700, "calibrated_from_limit_event")
    later = datetime.now(UTC) + timedelta(weeks=6)  # an old limit hit no longer calibrates
    assert resolve_allowance(store, {}, now=later).basis == "assumed_default"


def test_status_is_labelled_estimate():
    st = guard(Budget(), ledger(10)).status()
    assert st["label"] == "estimate"
    assert st["subscription"]["used_tokens_7d"] == 10
    assert st["subscription"]["cap_tokens"] == 500
