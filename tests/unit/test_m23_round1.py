"""M23 verifier round 1 (ADR-086 addendum 3), without a database: the ledger status vocabulary,
the cost model's row rule and per-call weighting, the contingency, the case-resampled alpha
interval and the STATUS wording. Synthetic values only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pigtail.forensics import alpha as ka
from pigtail.forensics.cost import (
    ADR087_AT,
    CONTINGENCY_FACTOR,
    LEGACY_SUPERSEDED_OUTPUT_TOKENS,
    PLANNING,
    CaseCostModel,
    StageTotals,
    current_settings,
    measured,
    projection,
    row_exclusion,
)
from pigtail.forensics.report import ops_lines, pooled_wording
from pigtail.llm.store import (
    COST_MODEL_STATUSES,
    LEDGER_STATUSES,
    NO_CALL_STATUSES,
    SPEND_STATUSES,
)


# --- the status vocabulary ------------------------------------------------------------------
def test_ledger_status_vocabulary_is_defined_and_diagnostic_never_models() -> None:
    assert set(LEDGER_STATUSES) == {
        "ok", "invalid_output", "error", "error_billed", "diagnostic", "cached", "limit",
    }  # fmt: skip
    assert set(SPEND_STATUSES) | set(NO_CALL_STATUSES) == set(LEDGER_STATUSES)
    assert set(COST_MODEL_STATUSES) == {"ok", "invalid_output", "error_billed"}
    assert "diagnostic" in SPEND_STATUSES and "diagnostic" not in COST_MODEL_STATUSES
    assert "error" not in COST_MODEL_STATUSES


def test_migration_check_matches_the_vocabulary() -> None:
    from pathlib import Path

    sql = Path(__file__).resolve().parents[2] / "migrations" / "0031_pilot_round1_fixes.sql"
    text = sql.read_text()
    for s in SPEND_STATUSES:
        assert f"'{s}'" in text
    for s in NO_CALL_STATUSES:
        assert f"'{s}'" not in text


# --- which rows enter the model -------------------------------------------------------------
def _row(**kw: Any) -> dict[str, Any]:
    from pigtail.forensics.prompts import CODER_A

    return {
        "status": "ok",
        "prompt_id": CODER_A.id,
        "prompt_version": CODER_A.version,
        "job": "double_coding",
        "model": "claude-sonnet-5",
        "thinking": "disabled",
        "output_tokens": 5000,
        "created_at": ADR087_AT + timedelta(hours=1),
        **kw,
    }


def test_row_exclusion_rules() -> None:
    s = current_settings()
    early = ADR087_AT - timedelta(minutes=1)
    assert row_exclusion(_row(), s) is None
    assert row_exclusion(_row(status="error_billed"), s) is None  # billed failure, current
    assert row_exclusion(_row(status="diagnostic"), s) == "status:diagnostic"
    assert row_exclusion(_row(status="error"), s) == "status:error"
    assert row_exclusion(_row(prompt_version="0"), s) == "prompt_version"
    assert row_exclusion(_row(thinking="adaptive"), s) == "thinking"
    assert row_exclusion(_row(prompt_id="relevance-filter"), s) == "not_a_coding_prompt"
    # legacy rows (no thinking label): superseded only when all three hold
    legacy = {"thinking": None, "output_tokens": LEGACY_SUPERSEDED_OUTPUT_TOKENS}
    assert row_exclusion(_row(**legacy, created_at=early), s) == "legacy_superseded_thinking"
    assert row_exclusion(_row(**legacy, status="error_billed", created_at=early), s) == (
        "legacy_superseded_thinking")  # fmt: skip
    assert row_exclusion(_row(**legacy), s) is None  # after the ADR-087 commit
    assert row_exclusion(_row(thinking=None, output_tokens=15_999, created_at=early), s) is None
    assert row_exclusion(_row(**legacy, created_at=early, job="adjudication"), s) is None
    # the diagnostic rule holds for legacy rows too
    assert row_exclusion(_row(thinking=None, status="diagnostic"), s) == "status:diagnostic"
    # a model that can't disable thinking sends `adaptive+effort:low` for `disabled`
    assert row_exclusion(_row(model="claude-opus-5-5", thinking="adaptive+effort:low"), s) is None
    assert row_exclusion(_row(model="claude-opus-5-5", thinking="disabled"), s) == "thinking"
    # the subscription backend records `cli-default`
    sub = current_settings(backend="subscription")
    assert row_exclusion(_row(thinking="cli-default"), sub) is None


def _t(calls: int, tin: int, tout: int, usd: float) -> StageTotals:
    t = StageTotals()
    t.add({"calls": calls, "batched": calls, "input_tokens": tin, "output_tokens": tout,
           "cost_usd": usd})  # fmt: skip
    return t


def test_measured_model_is_per_call_and_counts_retries() -> None:
    by_case = {
        # one row covering 2 requests (a retry): 2 calls, not 1
        "cod_a": {"coder_a": _t(2, 18_000, 10_000, 0.08), "coder_b": _t(1, 9000, 5000, 0.04),
                  "adjudication": _t(1, 10_000, 1500, 0.02)},
        "cod_b": {"coder_a": _t(1, 9000, 5000, 0.04), "coder_b": _t(1, 9000, 5000, 0.04)},
    }  # fmt: skip
    m = measured(by_case, {}, 2)
    assert m.coder.output == 5000 and m.coder.input == 9000  # per call
    assert m.coder_calls_per_case == 2.5 and m.adjudication_share == 0.5
    per = m.usd_per_case("claude-sonnet-5")
    from pigtail.llm.pricing import cost_usd

    call = cost_usd("claude-sonnet-5", m.coder.times(1), batch=True)
    assert per["coder_a"] == pytest.approx((call or 0) * 2.5 / 2)
    assert per["total"] == pytest.approx(per["coder_a"] + per["coder_b"] + per["adjudication"])
    rt = CaseCostModel.from_dict(m.to_dict())
    assert rt.coder_calls_per_case == 2.5 and rt.model_version == "case-cost-v3"


def test_projection_shows_base_and_contingency_and_gates_on_contingency() -> None:
    kw: dict[str, Any] = {
        "extraction_model": "claude-sonnet-5",
        "synthesis_model": "claude-opus-5-5",
        "full_cases": 147,
        "coded_cases": 5,
        "brief_spent_usd": 2.76,
        "month_spent_usd": 3.0,
        "month_cap_usd": 200.0,
    }
    p = projection(PLANNING, cap_usd=90.0, **kw)
    future = p["projected_total_usd"] - p["brief_spent_usd"]
    assert CONTINGENCY_FACTOR == 1.25 and p["contingency_factor"] == 1.25
    assert p["projected_total_with_contingency_usd"] == pytest.approx(2.76 + 1.25 * future,
                                                                      abs=1e-3)  # fmt: skip
    assert not p["h6"] and p["h6_basis"] == "projected_total_with_contingency_usd"
    # a cap between the base and the contingency figure: H6 (the conservative figure gates)
    mid = (p["projected_total_usd"] + p["projected_total_with_contingency_usd"]) / 2
    p2 = projection(PLANNING, cap_usd=mid, **kw)
    assert p2["h6"] and not p2["within_cap"]


# --- alpha: the interval resamples cases ----------------------------------------------------
def test_bootstrap_resamples_cases_when_units_share_a_case() -> None:
    # 6 cases x 4 units; within-case agreement makes unit resampling look tighter
    units: list[tuple[Any, Any]] = []
    cases: list[str] = []
    for c in range(6):
        for u in range(4):
            disagree = c in (1, 4) and u < 3
            units.append(("present", "absent" if disagree else "present") if u % 2 else
                         ("absent", "present" if disagree else "absent"))  # fmt: skip
            cases.append(f"case{c}")
    by_unit = ka.bootstrap_ci(units, resamples=400, seed=3)
    by_case = ka.bootstrap_ci(units, resamples=400, seed=3, clusters=cases)
    assert by_unit.resampled == "units" and by_case.resampled == "cases"
    assert by_case.clusters == 6 and by_case.to_dict()["cases"] == 6
    assert "cluster bootstrap over cases" in by_case.to_dict()["method"]
    assert by_unit.low is not None and by_case.low is not None
    assert by_unit.high is not None and by_case.high is not None
    assert (by_case.high - by_case.low) > (by_unit.high - by_unit.low)
    # one unit per case: the unit bootstrap, identical numbers
    singletons = [str(i) for i in range(len(units))]
    single = ka.bootstrap_ci(units, resamples=200, seed=5, clusters=singletons)
    plain = ka.bootstrap_ci(units, resamples=200, seed=5)
    assert (single.low, single.high, single.resampled) == (plain.low, plain.high, "units")
    with pytest.raises(ValueError):
        ka.bootstrap_ci(units, clusters=["x"])
    assert ka.ALPHA_VERSION == "kalpha-v2"


def test_reliability_rows_pool_patterns_with_a_case_resampled_ci() -> None:
    from pigtail.forensics.coding import reliability_rows
    from pigtail.forensics.store import CodingRow, PilotCase

    rows = []
    for c in range(3):
        case = PilotCase(f"v:c{c}", "follow_through", f"gh:o/c{c}", f"o/c{c}", None, None, c + 1,
                         "winner", 1, {"at": "2026-01-01T00:00:00+00:00"},
                         coding_id=f"cod_{c:016d}")  # fmt: skip
        for i in range(1, 12):
            unit = f"pattern.MC-{i:02d}"
            for p in ("A", "B"):
                v = "present" if (i + c + (p == "B" and i % 5 == 0)) % 2 else "absent"
                rows.append(CodingRow(case, p, unit, unit, v, "ok"))
    rel = reliability_rows(rows, 3, resamples=100)
    pooled = next(r for r in rel if r["field"] == "pattern.*")
    assert pooled["ci"]["resampled"] == "cases" and pooled["ci"]["cases"] == 3
    single = next(r for r in rel if r["field"] == "pattern.MC-01")
    assert single["ci"] == {} or single["ci"]["resampled"] == "units"


# --- the STATUS wording ---------------------------------------------------------------------
def test_status_wording_says_assessed_pooled_over_the_cases() -> None:
    p = {"alpha": 0.8512, "n_pairable": 60, "n_cases": 5, "assessed": True, "reason": None,
         "ci_low": 0.7, "ci_high": 0.93, "ci_resampled": "cases"}  # fmt: skip
    w = pooled_wording(p)
    assert "assessed (pooled over 5 cases)" in w and "α 0.85" in w and "cases resampled" in w
    no = pooled_wording({**p, "assessed": False, "reason": "n_pairable 20 < 30"})
    assert "reliability not assessed (n_pairable 20 < 30)" in no
    assert pooled_wording(None) == ""
    summary = {"cases": 5, "reliability": {"fields": 3, "statistics": 4, "assessed": 1,
               "pooled_patterns": p}, "cost": {"total_usd": 0.49, "per_case_usd": 0.098},
               "projection": {"full_brief_cases": 147, "projected_total_usd": 18.01,
                              "projected_total_with_contingency_usd": 21.83,
                              "contingency_factor": 1.25, "cap_usd": 90.0}}  # fmt: skip
    lines = ops_lines(summary, label="brief 1", month="2026-09")
    assert "assessed (pooled over 5 cases)" in lines["STATUS.md"]
    assert "USD 18.0100 (with ×1.25 contingency USD 21.8300)" in lines["COSTS.md"]


def test_adr087_at_is_the_fix_commit_time() -> None:
    assert datetime(2026, 9, 28, 18, 56, 14, tzinfo=UTC) == ADR087_AT
