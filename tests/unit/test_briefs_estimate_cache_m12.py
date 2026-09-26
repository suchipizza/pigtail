"""M12 cost estimate (R18.5, ADR-053.1) and re-run planning / cache keys (R18.4, R18.6).

Synthetic briefs only (the repo's example).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pigtail.briefs.budget import Allowance
from pigtail.briefs.cache import (
    PER_ITEM,
    STAGES,
    item_key,
    plan_rerun,
    stage_keys,
)
from pigtail.briefs.estimate import estimate, render_text
from pigtail.briefs.model import Brief, load_brief_text

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"
ALLOW = Allowance(10_000_000, "configured")


def brief(**updates: Any) -> Brief:
    b = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    for path, value in updates.items():
        section, _, key = path.partition("__")
        sub = getattr(b, section)
        b = b.model_copy(update={section: sub.model_copy(update={key: value})})
    return b


def edited(b: Brief, version: int, **updates: Any) -> Brief:
    out = brief(**updates) if updates else b
    return out.model_copy(update={"version": version})


# --- estimate -----------------------------------------------------------------------------------
def test_r18_5_estimate_reports_buckets_calls_tokens_share_and_zero_money():
    e = estimate(brief(), allowance=ALLOW)
    d = e.to_dict()
    assert d["label"] == "estimate"
    assert set(d["github"]["requests"]) == {"core", "graphql", "search"}
    assert all(v > 0 for v in d["github"]["requests"].values())
    assert d["llm"]["calls"] > 0 and d["llm"]["tokens"] > 0
    sub = d["llm"]["subscription"]
    assert sub["share_of_weekly_allowance"] == round(e.tokens / 10_000_000, 4)
    assert sub["allowance"] == {
        "weekly_tokens": 10_000_000,
        "basis": "configured",
        "label": "estimate",
    }
    assert d["money"] == {"usd": 0.0, "paid_steps": [], "requires_approval": False}
    text = render_text(e, brief())
    assert "ESTIMATE" in text and "$0" in text


def test_adr_053_share_over_cap_spreads_over_weeks():
    tight = Allowance(1_000_000, "configured")
    e = estimate(brief(budget__subscription_share=0.1), allowance=tight)
    assert e.subscription_share > 0.1
    assert e.weeks == 1 + -(-(e.tokens - 100_000) // 100_000)
    small = estimate(brief(), allowance=Allowance(10**12, "configured"))
    assert small.weeks == 1


def test_adr_053_paid_sources_require_approval_and_price_is_unknown():
    b = brief(optional_sources__x=True, optional_sources__trendshift=True)
    e = estimate(b, allowance=ALLOW)
    assert e.requires_approval
    assert {p.source for p in e.paid_steps} == {"x", "trendshift"}
    assert e.money_usd is None  # never invented: unknown until the terms audit sets a price
    assert "--approve-paid" in render_text(e, b)


def test_adr_053_api_backend_is_a_paid_step():
    e = estimate(brief(budget__llm_backend="api"), allowance=ALLOW, model="claude-opus-5")
    assert e.llm_api_usd > 0
    assert e.requires_approval and e.paid_steps[0].source == "anthropic_api"
    assert e.subscription_share == 0


def test_r18_4_unchanged_rerun_estimates_no_new_llm_calls():
    b = brief()
    plan = plan_rerun(b, edited(b, 2))
    e = estimate(edited(b, 2), allowance=ALLOW, plan=plan)
    assert e.llm_calls == 0 and e.tokens == 0
    assert e.github_requests == {"core": 0, "graphql": 0, "search": 0}
    assert e.to_dict()["reuse"]["changed_fields"] == []


def test_r18_4_success_edit_reestimates_only_downstream_stages():
    b = brief()
    b2 = edited(b, 2, success__primary_threshold="top_decile")
    plan = plan_rerun(b, b2)
    e = estimate(b2, allowance=ALLOW, plan=plan)
    reused = {s.stage for s in e.stages if s.reused}
    assert {"relevance", "expansion"} <= reused
    assert "patterns" not in reused
    assert e.github_requests["search"] == 0  # discovery reused


# --- re-run plan and cache keys -----------------------------------------------------------------
def actions(plan: Any) -> dict[str, str]:
    return {s.stage: s.action for s in plan.stages}


def test_r18_4_first_run_computes_everything():
    p = plan_rerun(None, brief())
    assert set(actions(p).values()) == {"recompute"}


def test_r18_4_unchanged_brief_reuses_every_stage():
    b = brief()
    p = plan_rerun(b, edited(b, 2))
    assert p.recomputed() == []
    assert set(p.fully_reused()) == set(STAGES)


def test_r18_4_success_edit_recomputes_only_what_it_affects():
    b = brief()
    p = plan_rerun(b, edited(b, 2, success__primary_threshold="top_decile"))
    a = actions(p)
    assert p.changed_fields == ["success.primary_threshold"]
    assert a["expansion"] == a["discovery"] == "reuse"
    assert a["relevance"] == a["evidence"] == "reuse_unchanged_items"
    assert a["outcome_sort"] == a["matching"] == a["patterns"] == "recompute"
    # extraction items are keyed by their own evidence: reused, but the case set may change
    assert a["extraction"] == "reuse_unchanged_items"
    assert "extraction" not in p.fully_reused()


def test_r18_4_field_edit_recomputes_discovery_and_relevance():
    b = brief()
    b2 = edited(b, 2, field__exclude=["general-purpose code linters"])
    a = actions(plan_rerun(b, b2))
    assert a["expansion"] == "recompute"
    assert a["discovery"] == "recompute"
    assert a["relevance"] == "recompute_items"
    assert a["evidence"] == "reuse_unchanged_items"


def test_r18_6_stage_keys_are_deterministic_and_data_version_sensitive():
    b = brief()
    assert stage_keys(b, "dv1-a") == stage_keys(edited(b, 9), "dv1-a")  # version isn't content
    k2 = stage_keys(b, "dv1-b")
    assert all(k2[s] != stage_keys(b, "dv1-a")[s] for s in STAGES)
    ks = stage_keys(edited(b, 2, panel__winners=25), "dv1-a")
    base = stage_keys(b, "dv1-a")
    assert ks["discovery"] == base["discovery"]
    assert ks["matching"] != base["matching"]


def test_r18_4_item_keys_depend_on_item_inputs_and_brief_subset_only():
    b = brief()
    k = item_key(b, "extraction", "case_1", "evhash")
    # extraction reads no brief field: the same case evidence is reused across briefs
    other = b.model_copy(update={"brief_id": "another-brief"})
    assert item_key(other, "extraction", "case_1", "evhash") == k
    assert item_key(b, "extraction", "case_1", "evhash2") != k
    r = item_key(b, "relevance", "cand_1", "h")
    stricter = edited(b, 2, success__primary_threshold="top_decile")
    assert item_key(stricter, "relevance", "cand_1", "h") == r
    assert item_key(edited(b, 2, field__exclude=["x"]), "relevance", "cand_1", "h") != r
    assert {"relevance", "evidence", "extraction"} == PER_ITEM
