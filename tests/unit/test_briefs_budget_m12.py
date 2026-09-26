"""Budget hard stop (PRD R15.11, R18.5; ADR-064.4, ADR-072.4), tested with tiny budgets.

M21b: `budget.money_usd` is the brief's total cap (API included) and `BUDGET_USD_MONTH` the
instance's monthly API cap, both hard stops that name H6; `subscription_share` applies to the
agents only. The monthly spend is metered from the real `LLMStore` usage ledger (in memory).
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
    month_start,
    resolve_allowance,
)
from pigtail.briefs.model import Budget
from pigtail.llm.store import LLMStore, UsageRow


def ledger(tokens: int = 0, status: str = "ok", backend: str = "subscription") -> LLMStore:
    s = LLMStore(":memory:")
    if tokens:
        s.record(UsageRow(backend, "job", "m", "p", "1", status, tokens, 0))
    return s


def spend(store: LLMStore, usd: float, status: str = "ok") -> None:
    store.record(UsageRow("api", "extraction", "m", "p", "1", status, 10, 1, cost_usd=usd))


def guard(budget: Budget, store: LLMStore, **kw: object) -> BudgetGuard:
    return BudgetGuard(budget, store, **kw)  # type: ignore[arg-type]


def api(money: float = 0.0) -> Budget:
    return Budget(llm_backend="api", money_usd=money)


def test_r18_5_money_default_zero_stops_any_paid_step_even_when_approved():
    g = guard(Budget(), ledger(), approved_paid=True)
    g.check_paid("free step", 0.0)  # free steps always pass
    with pytest.raises(BudgetStop) as ei:
        g.charge_paid("x collection", 0.01)
    assert ei.value.kind == "money"
    assert "H6" in ei.value.detail
    assert g.spent_usd == 0.0  # nothing charged


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
    assert g.spent_usd == pytest.approx(0.8)
    assert [e["step"] for e in g.log] == ["page 1", "page 2"]


def test_r18_5_resumed_run_carries_the_briefs_spend():
    g = guard(Budget(money_usd=1.0), ledger(), approved_paid=True, spent_usd=0.9)
    with pytest.raises(BudgetStop):
        g.check_paid("next", 0.2)


def test_adr_072_4_money_usd_is_the_total_cap_including_api():
    """API calls and other paid steps draw on the same brief cap."""
    g = guard(api(1.0), ledger(), approved_paid=True)
    g.check_llm("extract", est_usd=0.5)
    g.charge_api("extract", 0.5)
    g.charge_paid("x collection", 0.3)
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("extract 2", est_usd=0.3)
    assert ei.value.kind == "money"
    assert "total cap, API included" in ei.value.detail


def test_r15_11_api_backend_needs_approval():
    g = guard(api(10), ledger())
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("extract", est_usd=0.01)
    assert ei.value.kind == "approval"


def test_r15_11_unknown_price_still_needs_approval():
    g = guard(api(10), ledger())
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("extract", est_usd=None)
    assert ei.value.kind == "approval" and "unknown amount" in ei.value.detail
    guard(api(10), ledger(), approved_paid=True).check_llm("extract", est_usd=None)


def test_r15_11_monthly_cap_from_the_usage_ledger_hard_stops_with_h6():
    store = ledger()
    spend(store, 199.0)
    g = guard(api(1000), store, approved_paid=True, month_cap_usd=200.0)
    g.check_llm("batch 1", est_usd=1.0)  # exactly at the cap: allowed
    with pytest.raises(BudgetStop) as ei:
        g.check_llm("batch 2", est_usd=1.01)
    assert ei.value.kind == "month"
    assert "BUDGET_USD_MONTH" in ei.value.detail and "H6" in ei.value.detail
    # cache hits cost nothing and don't count
    spend(store, 50.0, status="cached")
    assert g.month_spent() == pytest.approx(199.0)


def test_r15_11_monthly_cap_counts_only_this_calendar_month():
    store = ledger()
    spend(store, 150.0)
    later = datetime.now(UTC).replace(day=1) + timedelta(days=40)  # next month
    g = guard(api(1000), store, approved_paid=True, month_cap_usd=200.0, clock=lambda: later)
    assert g.month_spent() == 0.0
    assert month_start(later).day == 1 and month_start(later).hour == 0


def test_adr_064_4_subscription_share_does_not_limit_product_calls():
    store = ledger(10**9)  # far beyond any weekly allowance
    g = guard(Budget(subscription_share=0.05), store)
    g.check_llm("chunk", est_usd=0.0, backend="subscription", est_tokens=10**6)  # no stop
    assert g.status()["subscription_share"]["applies_to"].startswith("agents")


def test_adr_053_never_switches_backend_on_its_own():
    g = guard(Budget(), ledger())
    g.check_backend("subscription", job="extraction")
    with pytest.raises(BudgetStop) as ei:
        g.check_backend("api", job="extraction")
    assert ei.value.kind == "backend"
    # an explicit per-job override (R15.5) is the only way
    g = guard(Budget(), ledger(), overrides={"extraction": "api"})
    g.check_backend("api", job="extraction")


def test_r15_9_before_submit_hook_checks_a_whole_batch():
    g = guard(api(1.0), ledger(), approved_paid=True)
    g.before_submit("extraction", 100, 0.9)
    with pytest.raises(BudgetStop) as ei:
        g.before_submit("extraction", 100, 1.5)
    assert ei.value.step == "extraction batch of 100"


def test_allowance_configured_calibrated_or_assumed():
    """Kept for the agents' allowance reporting (ADR-055.2, ADR-064.2)."""
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
    store = ledger()
    spend(store, 2.5)
    st = guard(api(150), store, spent_usd=1.0).status()
    assert st["label"] == "estimate"
    assert st["brief_usd"] == {"spent": 1.0, "cap": 150.0}
    assert st["month_usd"] == {"spent": 2.5, "cap": 200.0}
