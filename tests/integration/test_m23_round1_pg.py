"""M23 verifier round 1 (ADR-086 addendum 3) on Postgres, fakes and synthetic data only.

1. The measured cost model: the ledger's thinking label and request count, per-call weighting,
   `diagnostic` and superseded-setting rows left out (and the back-compat rule for rows written
   before migration 0031), billed failures of the current setting kept, the contingency, the
   rebuild of an existing pilot's model, and a cost block whose stages add up.
2. Code-commit provenance: one invocation per run of the command with its commit and steps,
   coding rows stamped with the commit that coded them, annotations that change nothing else.
3. Decay lateness: the actual age at check and the on-time flag, late checks in the aggregate.
5. OPS-2: the monthly figure takes the Postgres ledger's total where it is larger.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.budget import BudgetGuard, BudgetStop
from pigtail.forensics.cost import (
    ADR087_AT,
    CONTINGENCY_FACTOR,
    COST_MODEL_VERSION,
    current_settings,
    model_rows,
)
from pigtail.forensics.decay import aggregate, run_due
from pigtail.forensics.pilot import rebuild_cost_model, run_pilot
from pigtail.forensics.store import add_annotation, latest_cost_model, pilot_row
from pigtail.llm.batch import MemoryBatchStore, PgCostLedger
from pigtail.llm.store import LLMStore
from tests import selection_fake
from tests.forensics_fake import NOW, CodingBatchBackend, FakeGitHubCases, FakeSite, seed_selection
from tests.integration.test_m23_pilot_pg import OPTS, Env, q

pytestmark = pytest.mark.db


@pytest.fixture
def env(capture_db: Any, tmp_path: Path) -> Env:
    """The pilot tests' environment (`test_m23_pilot_pg.env`)."""
    from pigtail.capture.snapshots import LocalSnapshotStore

    brief = selection_fake.brief()
    seed_selection(capture_db.conn, brief)
    return Env(
        conn=capture_db.conn, db=capture_db, brief=brief,
        snaps=LocalSnapshotStore(tmp_path / "snaps"), gh_fake=FakeGitHubCases(),
        site=FakeSite(), backend=CodingBatchBackend(), batches=MemoryBatchStore(),
        llm=LLMStore(tmp_path / "llm.sqlite3"), data_dir=tmp_path / "private-data",
    )  # fmt: skip


def _ledger_row(env: Env, rid: str, **kw: Any) -> None:
    """A hand-written ledger row (as an operator's back-fill would be)."""
    row = {
        "case_ref": None,
        "job": "double_coding",
        "stage": "extraction",
        "backend": "api",
        "model": "claude-sonnet-5",
        "prompt_id": "case-coder-a",
        "prompt_version": "3",
        "batch_id": None,
        "status": "ok",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "thinking": None,
        "requests": 1,
        "created_at": NOW,
        **kw,
    }
    env.conn.execute(
        "INSERT INTO llm_cost_ledger (brief_id, brief_run_id, case_ref, job, stage, backend,"
        " model, prompt_id, prompt_version, batch_id, status, input_tokens, output_tokens,"
        " cost_usd, thinking, requests, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            env.brief.brief_id, rid, row["case_ref"], row["job"], row["stage"], row["backend"],
            row["model"], row["prompt_id"], row["prompt_version"], row["batch_id"],
            row["status"], row["input_tokens"], row["output_tokens"], row["cost_usd"],
            row["thinking"], row["requests"], row["created_at"],
        ),
    )  # fmt: skip


def _prompt_version() -> str:
    from pigtail.forensics.prompts import CODER_A

    return CODER_A.version


# --- 1. the cost model --------------------------------------------------------------------------
def test_ledger_rows_carry_the_thinking_label_and_one_request(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0, out.message
    rows = q(env, "SELECT DISTINCT job, thinking, requests FROM llm_cost_ledger"
                  " WHERE brief_run_id = %s ORDER BY 1", out.brief_run_id)  # fmt: skip
    assert rows == [("adjudication", "disabled", 1), ("double_coding", "disabled", 1)]
    # the status vocabulary is checked for new rows
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        _ledger_row(env, out.brief_run_id, status="made_up")


def test_cost_model_leaves_out_diagnostic_superseded_and_aggregate_rows(env: Env) -> None:
    """The verifier's case: aggregate back-filled rows (5 requests, 80,000 output tokens, counted
    as one call), `diagnostic` rows and failures under the superseded setting inflated the
    measured coder output per call (16,814 against 5,527) and the projection. The rebuilt model
    equals the model of the clean rows; the stored pre-fix model is not read any more."""
    out = run_pilot(env.brief, env.deps(), OPTS)
    rid = out.brief_run_id
    assert rid is not None
    settings = current_settings()
    clean, clean_counts = model_rows(env.conn, rid, settings)
    clean_model = latest_cost_model(env.conn, env.brief.brief_id)
    assert clean_model is not None and clean_model["model_version"] == COST_MODEL_VERSION
    clean_out = clean_model["per_case"]["coder_call"]["output"]
    early = ADR087_AT - timedelta(hours=3)
    pv = _prompt_version()
    # two aggregate back-fills of 5 batch requests each (thinking on, 16,000 output tokens each)
    for p in ("case-coder-a", "case-coder-b"):
        _ledger_row(env, rid, prompt_id=p, prompt_version=pv, status="error_billed",
                    batch_id="msgbatch_synthetic", input_tokens=5 * 9000, output_tokens=80_000,
                    cost_usd=0.48, created_at=early)  # fmt: skip
    # the standard fallback of the superseded setting
    _ledger_row(env, rid, prompt_version=pv, status="error_billed", input_tokens=9000,
                output_tokens=16_000, cost_usd=0.18, created_at=early)  # fmt: skip
    # two diagnostic calls made by hand
    for _ in range(2):
        _ledger_row(env, rid, prompt_version=pv, status="diagnostic", input_tokens=9000,
                    output_tokens=4941, cost_usd=0.125, created_at=early)  # fmt: skip
    # a new-style row under another thinking setting, and one of another prompt version
    _ledger_row(env, rid, prompt_version=pv, status="error_billed", thinking="adaptive",
                output_tokens=16_000, cost_usd=0.1)  # fmt: skip
    _ledger_row(env, rid, prompt_version="0", status="ok", thinking="disabled",
                output_tokens=3000, cost_usd=0.05)  # fmt: skip
    by_case, counts = model_rows(env.conn, rid, settings)
    assert counts["excluded"]["legacy_superseded_thinking"] == {
        "rows": 3, "requests": 3, "usd": pytest.approx(1.14)}  # fmt: skip
    assert counts["excluded"]["status:diagnostic"]["rows"] == 2
    assert counts["excluded"]["thinking"]["rows"] == 1
    assert counts["excluded"]["prompt_version"]["rows"] == 1
    assert counts["used"] == clean_counts["used"]
    assert by_case.keys() == clean.keys()
    # the rebuild stores a v3 model equal to the clean one, with the rows it used and left out
    res = rebuild_cost_model(
        env.conn, env.brief, rid, synthesis_model="claude-opus-5-5", batch=True, backend="api",
        month_spent_usd=0.0, month_cap_usd=200.0, commit="c0ffee1", at=NOW,
    )  # fmt: skip
    assert res["stored"] and res["cost_model"]["coder_call"]["output"] == clean_out
    assert res["cost_model"]["ledger_rows"]["excluded"]["status:diagnostic"]["rows"] == 2
    latest = latest_cost_model(env.conn, env.brief.brief_id)
    assert latest is not None and latest["projection"] == res["projection"]
    pr = res["projection"]
    base, with_c = pr["projected_total_usd"], pr["projected_total_with_contingency_usd"]
    spent = pr["brief_spent_usd"]
    assert pr["contingency_factor"] == CONTINGENCY_FACTOR == 1.25
    assert with_c == pytest.approx(spent + 1.25 * (base - spent), abs=1e-3)
    assert pr["h6_basis"] == "projected_total_with_contingency_usd"
    # the pilot's summary carries the new projection; the replaced one is kept in the history
    summary = (pilot_row(env.conn, rid) or {})["summary"]
    assert summary["projection"] == res["projection"]
    assert summary["cost_model_history"][-1]["by_commit"] == "c0ffee1"
    # the back-compat cut-off: rows written after it are not superseded by the rule
    later, _c = model_rows(env.conn, rid, current_settings(superseded_before=early))
    assert later.keys() >= clean.keys()


def test_aggregate_rows_are_weighted_per_request(env: Env) -> None:
    """A row covering 5 requests counts as 5 calls, so its tokens are spread over them."""
    out = run_pilot(env.brief, env.deps(), OPTS)
    rid = out.brief_run_id
    assert rid is not None
    settings = current_settings()
    _before, c0 = model_rows(env.conn, rid, settings)
    _ledger_row(env, rid, case_ref="cod_synthetic0000", prompt_version=_prompt_version(),
                thinking="disabled", requests=5, input_tokens=50_000, output_tokens=25_000,
                cost_usd=0.5)  # fmt: skip
    after, c1 = model_rows(env.conn, rid, settings)
    t = after["cod_synthetic0000"]["coder_a"]
    assert (t.calls, t.output) == (5, 25_000) and t.per_call().output == 5000
    assert c1["used"]["requests"] == c0["used"]["requests"] + 5
    assert c1["used"]["rows"] == c0["used"]["rows"] + 1


def test_billed_failures_of_the_current_setting_stay_in_the_model(env: Env) -> None:
    from tests.integration.test_m23_pilot_pg import _fail_theta_a

    env.backend.max_tokens_when = _fail_theta_a
    out = run_pilot(env.brief, env.deps(), OPTS)
    rid = out.brief_run_id
    assert rid is not None
    _by_case, counts = model_rows(env.conn, rid, current_settings())
    billed = q(env, "SELECT count(*), sum(cost_usd) FROM llm_cost_ledger WHERE brief_run_id = %s"
                    " AND status = 'error_billed'", rid)[0]  # fmt: skip
    assert billed[0] == 2 and "status:error_billed" not in counts["excluded"]
    model = (latest_cost_model(env.conn, env.brief.brief_id) or {})["per_case"]
    # the failed attempts (a batch item and its fallback) add coder calls per case
    assert model["coder_calls_per_case"] > 2.0
    assert out.summary["adjudication_share"] == model["adjudication_share"]


def test_pilot_cost_block_stages_add_up_and_report_shows_contingency(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    report = json.loads(Path(out.report_paths["json"]).read_text())
    cost = report["cost"]
    stages = cost["per_case_usd_by_stage"]
    assert set(stages) >= {"coder_a", "coder_b", "adjudication"}
    assert sum(stages.values()) == pytest.approx(cost["per_case_usd"], abs=1e-6)
    assert cost["per_case_usd"] == pytest.approx(cost["total_usd"] / cost["cases"], abs=1e-5)
    model_pc = cost["cost_model_per_case_usd"]
    assert model_pc["total"] == pytest.approx(
        model_pc["coder_a"] + model_pc["coder_b"] + model_pc["adjudication"])  # fmt: skip
    md = Path(out.report_paths["md"]).read_text()
    assert "projected_total_with_contingency_usd" in md and "Cost model per case" in md
    # the counts-only lines carry the contingency figure and the pooled wording
    from pigtail.forensics.report import ops_lines

    row = pilot_row(env.conn, out.brief_run_id or "") or {}
    lines = ops_lines(row["summary"], label="brief", month="2026-09")
    assert "contingency" in lines["COSTS.md"] and "pooled pattern-seed α" in lines["STATUS.md"]


# --- 2. provenance ------------------------------------------------------------------------------
def test_resumed_pilot_records_each_invocations_commit_and_the_coding_commit(env: Env) -> None:
    deps = env.deps(github=env.github({"core": 7}))
    deps.code_commit = "aaaa111"
    out = run_pilot(env.brief, deps, OPTS)
    assert out.exit_code == 4  # paused on the GitHub budget during case evidence
    rid = out.brief_run_id
    assert rid is not None
    deps2 = env.deps()
    deps2.code_commit = "bbbb222"
    out2 = run_pilot(env.brief, deps2, OPTS)
    assert out2.exit_code == 0 and out2.brief_run_id == rid
    row = pilot_row(env.conn, rid) or {}
    assert row["code_commit"] == "aaaa111"  # the commit that created the run, unchanged
    inv = row["invocations"]
    assert [(i["n"], i["kind"], i["commit"]) for i in inv] == [
        (1, "create", "aaaa111"), (2, "resume", "bbbb222")]  # fmt: skip
    assert inv[0]["steps"] == ["case_evidence"]
    assert inv[1]["steps"][:3] == ["case_evidence", "double_coding", "adjudication"]
    assert {"alpha", "cost_and_projection", "report"} <= set(inv[1]["steps"])
    commits = q(env, "SELECT DISTINCT pass, code_commit FROM brief_coding WHERE brief_run_id = %s",
                rid)  # fmt: skip
    assert {c for _p, c in commits} == {"bbbb222"}
    assert {c for (c,) in q(env, "SELECT DISTINCT code_commit FROM brief_reliability"
                                 " WHERE brief_run_id = %s", rid)} == {"bbbb222"}  # fmt: skip
    report = json.loads(Path(out2.report_paths["json"]).read_text())
    prov = report["provenance"]
    assert prov["code_commit"] == "aaaa111" and len(prov["invocations"]) == 2
    assert prov["coding_commits"]["A"] == {"bbbb222": prov["coding_commits"]["A"]["bbbb222"]}


def test_annotation_is_appended_without_rewriting_anything(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    rid = out.brief_run_id
    assert rid is not None
    before = pilot_row(env.conn, rid) or {}
    codings = q(env, "SELECT id, code_commit FROM brief_coding WHERE brief_run_id = %s"
                     " ORDER BY id", rid)  # fmt: skip
    e = add_annotation(env.conn, rid, note="codings  made at commit 8f441b3's code,\n not fa94e75",
                       at=NOW, commit="8f441b3", step="double_coding",
                       annotated_by_commit="dddd444")  # fmt: skip
    assert e == {"n": 1, "at": NOW.isoformat(),
                 "note": "codings made at commit 8f441b3's code, not fa94e75",
                 "commit": "8f441b3", "step": "double_coding",
                 "annotated_by_commit": "dddd444"}  # fmt: skip
    add_annotation(env.conn, rid, note="second", at=NOW)
    after = pilot_row(env.conn, rid) or {}
    assert [a["n"] for a in after["annotations"]] == [1, 2]
    for k in ("code_commit", "invocations", "summary", "prompt_fingerprints", "batch_ids"):
        assert after[k] == before[k]
    assert q(env, "SELECT id, code_commit FROM brief_coding WHERE brief_run_id = %s"
                  " ORDER BY id", rid) == codings  # fmt: skip
    with pytest.raises(ValueError):
        add_annotation(env.conn, rid, note="   ", at=NOW)
    with pytest.raises(ValueError):
        add_annotation(env.conn, "no-such-run", note="x", at=NOW)


# --- 3. decay lateness ----------------------------------------------------------------------------
def test_decay_records_actual_age_and_flags_late_checks(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0
    gh, pages = env.github(), env.pages()
    captured = q(env, "SELECT min(captured_at), max(captured_at) FROM brief_case_evidence")[0]
    assert captured[0] == captured[1] == NOW
    # +1 d checks on time (30 h after capture: within 24 + 12 h)
    run_due(env.conn, now=NOW + timedelta(hours=30), github=gh, pages=pages)
    rows = q(env, "SELECT DISTINCT age_hours, on_time FROM brief_evidence_decay"
                  " WHERE offset_days = 1 AND checked_at IS NOT NULL")  # fmt: skip
    assert rows == [(30.0, True)]
    # +7 d checks two days late (216 h: beyond 168 + 24 h)
    run_due(env.conn, now=NOW + timedelta(days=9), github=gh, pages=pages)
    late = q(env, "SELECT DISTINCT age_hours, on_time FROM brief_evidence_decay"
                  " WHERE offset_days = 7 AND checked_at IS NOT NULL")  # fmt: skip
    assert late == [(216.0, False)]
    agg = aggregate(env.conn, env.brief.brief_id)
    a1, a7 = agg["by_age"]["+1d"], agg["by_age"]["+7d"]
    assert a1["late_checks"] == 0 and a1["age_hours_at_check"] == {
        "min": 30.0, "median": 30.0, "max": 30.0}  # fmt: skip
    assert a7["late_checks"] == a7["checked"] > 0 and a7["age_hours_at_check"]["max"] == 216.0
    assert a7["on_time_within_hours"] == 192.0 and a1["on_time_within_hours"] == 36.0
    assert agg["late_checks"] == a7["late_checks"]
    assert agg["r19_8"]["late_checks_at_7d"] == a7["late_checks"]
    per = {(r["kind"], r["offset_days"]): r for r in agg["by_kind_and_age"]}
    assert all(r["late_checks"] == r["checked"] for (k, d), r in per.items() if d == 7)
    # the CLI's table shows the actual ages and the late flag
    from pigtail.briefs.pilot_cli import _decay_md

    md = _decay_md(agg)
    assert "actual age at check" in md and "LATE" in md


def test_decay_aggregate_reports_ages_of_checks_made_before_the_columns(env: Env) -> None:
    run_pilot(env.brief, env.deps(), OPTS)
    run_due(env.conn, now=NOW + timedelta(hours=26), github=env.github(), pages=env.pages())
    env.conn.execute("UPDATE brief_evidence_decay SET age_hours = NULL, on_time = NULL")
    agg = aggregate(env.conn, env.brief.brief_id)
    assert agg["by_age"]["+1d"]["age_hours_at_check"]["median"] == 26.0


# --- 5. OPS-2 --------------------------------------------------------------------------------
def test_ops2_month_spent_reads_the_pg_ledger_where_it_is_larger(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    rid = out.brief_run_id
    assert rid is not None
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # a hand back-fill (billed failures, diagnostics) is only in the Postgres ledger
    _ledger_row(env, rid, status="diagnostic", output_tokens=4941, cost_usd=1.38, created_at=now)
    ledger = PgCostLedger(env.conn)
    guard = BudgetGuard(env.brief.budget, env.llm, approved_paid=True, clock=lambda: now,
                        month_ledger=ledger.month_total)  # fmt: skip
    local_now = env.llm.usage_since("api", start)["cost_usd"]
    assert guard.month_spent() == pytest.approx(ledger.month_total(start))
    assert guard.month_spent() >= local_now + 1.38 - 1e-6
    assert guard.status()["month_usd"]["api_sources"]["source"] == "postgres_cost_ledger"
    guard.month_cap_usd = guard.month_spent() + 0.5
    with pytest.raises(BudgetStop):
        guard.check_llm("next batch", est_usd=1.0)
