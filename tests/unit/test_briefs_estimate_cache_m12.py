"""Cost estimate (R18.5; M21b: R15.8-R15.11, ADR-072.4) and re-run planning / cache keys
(R18.4, R18.6).

Synthetic briefs only (the repo's example).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.cache import (
    PER_ITEM,
    STAGES,
    item_key,
    plan_rerun,
    stage_keys,
)
from pigtail.briefs.estimate import (
    CACHED_PREFIX,
    ESTIMATE_MODEL,
    TOKENS,
    estimate,
    price_stage,
    render_text,
)
from pigtail.briefs.model import Brief, load_brief_text
from pigtail.llm.pricing import TokenUsage, cost_usd

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"


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
def test_r18_5_estimate_on_subscription_reports_calls_tokens_and_zero_money():
    b = brief(budget__llm_backend="subscription", budget__money_usd=0)
    e = estimate(b)
    d = e.to_dict()
    assert d["label"] == "estimate" and d["model"] == ESTIMATE_MODEL == "estimate-v2"
    assert set(d["github"]["requests"]) == {"core", "graphql", "search"}
    assert all(v > 0 for v in d["github"]["requests"].values())
    assert d["llm"]["calls"] > 0 and d["llm"]["tokens"] > 0
    assert "agents building pigtail" in d["llm"]["subscription_note"]  # ADR-064.4
    assert d["money"] == {"usd": 0.0, "paid_steps": [], "requires_approval": False}
    assert d["caps"]["within_caps"] is True
    text = render_text(e, b)
    assert "ESTIMATE" in text and "$0" in text


def test_r15_8_each_stage_uses_its_models_price_and_batch_mode():
    e = estimate(brief())  # example brief: llm_backend api
    by = {s.stage: s for s in e.stages}
    assert by["relevance"].model == "claude-haiku-4-5-20251001"
    assert by["extraction"].model == by["adjudication"].model == "claude-sonnet-5"
    assert by["patterns"].model == by["report"].model == by["plan"].model == "claude-opus-5-5"
    assert {s.mode for s in e.stages if s.llm_calls} == {"batch"}  # R15.9: none time-sensitive
    per = e.to_dict()["llm"]["per_llm_stage"]
    assert set(per) == {"relevance", "extraction", "synthesis"}
    assert all(v["usd"] > 0 for v in per.values())
    assert e.llm_api_usd == pytest.approx(sum(s.usd or 0 for s in e.stages))


def test_r15_9_batch_halves_and_caching_lowers_the_price():
    calls, per = 100, TOKENS["extraction"]
    batch, reads, writes = price_stage("extraction", calls, per, model="claude-sonnet-5",
                                       batch=True, api=True)  # fmt: skip
    std, _, _ = price_stage("extraction", calls, per, model="claude-sonnet-5", batch=False,
                            api=True)  # fmt: skip
    uncached = cost_usd("claude-sonnet-5", TokenUsage(calls * per[0], calls * per[1]))
    assert batch is not None and std is not None and uncached is not None
    assert reads + writes == calls * CACHED_PREFIX["extraction"]
    assert batch < std < uncached
    # Haiku's minimum cacheable prefix (4,096) is above the relevance prefix: priced uncached
    _, r, w = price_stage("relevance", 10, TOKENS["relevance"], model="claude-haiku-4-5",
                          batch=True, api=True)  # fmt: skip
    assert r == w == 0


def test_r15_11_estimate_against_the_brief_cap_and_the_monthly_cap():
    e = estimate(brief(), month_cap_usd=200.0, month_spent_usd=10.0, brief_spent_usd=5.0)
    c = e.to_dict()["caps"]
    assert c["brief"]["cap_usd"] == 150.0 and c["brief"]["spent_usd"] == 5.0
    assert c["brief"]["remaining_usd"] == 145.0
    assert c["month"]["cap_usd"] == 200.0 and c["month"]["remaining_usd"] == 190.0
    assert c["within_caps"] is (e.money_usd is not None and e.money_usd <= 145.0)
    over = estimate(brief(budget__money_usd=0.01))
    assert over.to_dict()["caps"]["within_caps"] is False
    assert "EXCEEDS" in render_text(over, brief(budget__money_usd=0.01))
    month_full = estimate(brief(), month_cap_usd=200.0, month_spent_usd=200.0)
    assert month_full.to_dict()["caps"]["month"]["within"] is False


def test_r15_8_unknown_model_price_is_unknown_not_zero():
    e = estimate(brief(), models={"synthesis": "some-future-model"})
    assert e.llm_api_usd is None and e.money_usd is None
    assert e.to_dict()["caps"]["within_caps"] is None
    assert e.requires_approval


def test_r15_9_llm_batch_off_prices_standard_calls():
    on, off = estimate(brief()), estimate(brief(), batch=False)
    assert {s.mode for s in off.stages if s.llm_calls} == {"standard"}
    assert off.llm_api_usd is not None and on.llm_api_usd is not None
    assert off.llm_api_usd > on.llm_api_usd


def test_adr_053_paid_sources_require_approval_and_price_is_unknown():
    b = brief(optional_sources__x=True, optional_sources__trendshift=True)
    e = estimate(b)
    assert e.requires_approval
    assert {"x", "trendshift"} <= {p.source for p in e.paid_steps}
    assert e.money_usd is None  # never invented: unknown until the terms audit sets a price
    assert "--approve-paid" in render_text(e, b)


def test_adr_053_api_backend_is_a_paid_step():
    e = estimate(brief(budget__llm_backend="api"))
    assert e.llm_api_usd is not None and e.llm_api_usd > 0
    assert e.requires_approval and e.paid_steps[0].source == "anthropic_api"
    assert e.to_dict()["llm"]["pricing"]["as_of"] == "2026-06-24"


def test_r18_4_unchanged_rerun_estimates_no_new_llm_calls():
    b = brief()
    plan = plan_rerun(b, edited(b, 2))
    e = estimate(edited(b, 2), plan=plan)
    assert e.llm_calls == 0 and e.tokens == 0 and e.llm_api_usd == 0
    assert e.github_requests == {"core": 0, "graphql": 0, "search": 0}
    assert e.to_dict()["reuse"]["changed_fields"] == []
    assert not e.requires_approval


def test_r18_4_success_edit_reestimates_only_downstream_stages():
    b = brief()
    b2 = edited(b, 2, success__primary_threshold="top_decile")
    plan = plan_rerun(b, b2)
    e = estimate(b2, plan=plan)
    reused = {s.stage for s in e.stages if s.reused}
    assert {"relevance", "expansion"} <= reused
    assert not {"patterns", "report", "plan"} & reused
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
