"""M12 brief-run provenance, cache reuse and the budget hard stop on Postgres
(PRD R18.4, R18.5, R18.6; D7 acceptance "re-running an unchanged brief makes no new LLM calls
for cached items ... the run report lists both" and "a run given a budget below its estimate
stops at the budget with a resumable checkpoint").

The stages here are stand-ins (M13 builds the real ones); what is tested is the machinery they
will call: `StageCache`, `BriefRuns`, `plan_rerun`, `BudgetGuard`. Synthetic briefs only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.briefs.budget import BudgetGuard, BudgetStop
from pigtail.briefs.cache import (
    BriefRuns,
    StageCache,
    data_version,
    item_key,
    plan_rerun,
)
from pigtail.briefs.model import Brief, load_brief_text
from pigtail.llm.store import LLMStore, UsageRow

pytestmark = pytest.mark.db

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"
CANDIDATES = [f"cand_{i}" for i in range(6)]
USD_PER_CALL = 0.1


def brief(version: int = 1, **success: Any) -> Brief:
    b = load_brief_text(EXAMPLE.read_text())
    if success:
        b = b.model_copy(update={"success": b.success.model_copy(update=success)})
    return b.model_copy(update={"version": version})


@pytest.fixture
def conn(capture_db: Any) -> psycopg.Connection[Any]:
    capture_db.conn.autocommit = True
    return capture_db.conn  # type: ignore[no-any-return]


class FakeLLM:
    """Counts model calls a stand-in stage makes (and records them in the usage ledger)."""

    def __init__(self, ledger: LLMStore, tokens_per_call: int = 100) -> None:
        self.calls = 0
        self.ledger = ledger
        self.tokens = tokens_per_call

    def __call__(self, what: str) -> dict[str, Any]:
        self.calls += 1
        self.ledger.record(UsageRow("subscription", "relevance", "m", "p", "1", "ok", self.tokens))
        return {"verdict": "relevant", "about": what}


def run_brief(
    conn: psycopg.Connection[Any],
    b: Brief,
    llm: FakeLLM,
    guard: BudgetGuard | None = None,
    previous: Brief | None = None,
    resume_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A stand-in pipeline: per-candidate relevance (cached per item) + a whole-stage sort."""
    dv = data_version(conn)
    runs = BriefRuns(conn)
    run = runs.create(
        b,
        data_version=dv,
        plan=plan_rerun(previous, b),
        code_commit="0" * 40,
        codebook_version="0.3.1",
        prompt_versions={"relevance": "1"},
        model_versions={"relevance": "m"},
        resumed_from=resume_from["id"] if resume_from else None,
    )
    run.start()
    cache = StageCache(conn)
    start = (resume_from or {}).get("checkpoint") or {}
    done = int(start.get("relevance_done", 0))
    for i, cand in enumerate(CANDIDATES):
        if i < done:
            continue
        key = item_key(b, "relevance", cand, input_hash=f"snap-{cand}")
        try:
            miss = guard is not None and cache.get(key) is None
            if guard is not None and miss:
                guard.check_llm(f"relevance {cand}", est_usd=USD_PER_CALL, est_tokens=llm.tokens)
            cache.get_or_compute(
                key,
                lambda c=cand: llm(c),  # type: ignore[misc]
                stage="relevance",
                item_ref=cand,
                input_hash=f"snap-{cand}",
                brief=b,
                brief_run_id=run.id,
                counter=run.counter,
            )
            if guard is not None and miss:
                guard.charge_api(f"relevance {cand}", USD_PER_CALL)
        except BudgetStop as stop:
            run.pause_for_budget(stop, {"stage": "relevance", "relevance_done": i}, {})
            out = runs.get(run.id)
            assert out is not None
            return out
    # whole-stage outcome sort: reused when its stage key is unchanged
    sort_key = runs.get(run.id)["stage_keys"]["outcome_sort"]  # type: ignore[index]
    got = cache.get(sort_key)
    run.counter.add("outcome_sort", reused=got is not None)
    if got is None:
        cache.put(
            sort_key,
            stage="outcome_sort",
            item_ref="all",
            input_hash=sort_key,
            result={"winners": []},
            brief=b,
            brief_run_id=run.id,
        )
    run.finish("succeeded", {"money_usd": 0})
    out = runs.get(run.id)
    assert out is not None
    return out


def test_r18_6_brief_run_records_provenance(conn):
    ledger = LLMStore(":memory:")
    rec = run_brief(conn, brief(), FakeLLM(ledger))
    assert rec["status"] == "succeeded"
    assert rec["brief_id"] == "example-config-linter" and rec["brief_version"] == 1
    assert rec["brief_hash"] == brief().content_hash()
    assert rec["data_version"].startswith("dv1-")
    assert rec["code_commit"] == "0" * 40 and rec["codebook_version"] == "0.3.1"
    assert rec["prompt_versions"] == {"relevance": "1"}
    assert set(rec["stage_keys"]) >= {"discovery", "relevance", "outcome_sort", "patterns"}
    assert rec["rerun_plan"]["from_version"] is None


def test_d7_rerun_unchanged_brief_makes_no_new_llm_calls_and_reports_it(conn):
    ledger = LLMStore(":memory:")
    llm = FakeLLM(ledger)
    first = run_brief(conn, brief(), llm)
    assert llm.calls == len(CANDIDATES)
    assert first["reuse"] == {
        "outcome_sort": {"reused": 0, "recomputed": 1},
        "relevance": {"reused": 0, "recomputed": len(CANDIDATES)},
    }
    second = run_brief(conn, brief(), llm, previous=brief())
    assert llm.calls == len(CANDIDATES)  # no new calls
    assert second["reuse"] == {
        "outcome_sort": {"reused": 1, "recomputed": 0},
        "relevance": {"reused": len(CANDIDATES), "recomputed": 0},
    }
    assert second["rerun_plan"]["changed_fields"] == []


def test_d7_rerun_edited_brief_recomputes_only_what_the_edit_affects(conn):
    ledger = LLMStore(":memory:")
    llm = FakeLLM(ledger)
    v1 = brief(1)
    run_brief(conn, v1, llm)
    v2 = brief(2, primary_threshold="top_decile")
    rec = run_brief(conn, v2, llm, previous=v1)
    assert llm.calls == len(CANDIDATES)  # relevance doesn't read `success`: all reused
    assert rec["reuse"]["relevance"] == {"reused": len(CANDIDATES), "recomputed": 0}
    assert rec["reuse"]["outcome_sort"] == {"reused": 0, "recomputed": 1}
    plan = {s["stage"]: s["action"] for s in rec["rerun_plan"]["stages"]}
    assert plan["outcome_sort"] == "recompute" and plan["discovery"] == "reuse"


def test_d7_r18_5_tiny_budget_hard_stops_with_resumable_checkpoint(conn):
    ledger = LLMStore(":memory:")
    llm = FakeLLM(ledger, tokens_per_call=100)
    b = brief()
    # ADR-072.4: a brief total cap of USD 0.50 at USD 0.10 per call; the estimate (0.60) is above
    tight = b.budget.model_copy(update={"money_usd": 0.5})
    guard = BudgetGuard(tight, ledger, approved_paid=True)
    paused = run_brief(conn, b, llm, guard=guard)
    assert paused["status"] == "paused_budget"
    assert paused["stop"]["kind"] == "money" and "H6" in paused["stop"]["detail"]
    assert paused["checkpoint"] == {"stage": "relevance", "relevance_done": 5}
    assert llm.calls == 5  # stopped before the call that would cross the cap
    # H6 approved a higher cap: resume from the checkpoint with the spend carried over
    raised = b.budget.model_copy(update={"money_usd": 1.0})
    guard2 = BudgetGuard(raised, ledger, approved_paid=True, spent_usd=guard.spent_usd)
    done = run_brief(conn, b, llm, guard=guard2, resume_from=paused)
    assert done["status"] == "succeeded" and done["resumed_from"] == paused["id"]
    assert llm.calls == len(CANDIDATES)
    assert done["reuse"]["relevance"] == {"reused": 0, "recomputed": 1}


def test_r18_6_data_version_is_stable_until_data_changes(conn):
    a = data_version(conn)
    assert data_version(conn) == a
    conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:7000001', 'github', 7000001, 'org-x/repo-1', now())"
    )
    conn.execute(
        "INSERT INTO star_history_fetch (repo_host_id, fetched_at, per_page, pages, weeks,"
        " complete) VALUES (7000001, now(), 30, 1, 1, true)"
    )
    assert data_version(conn) != a


def test_r4_7_shortlist_decision_stub_rules(conn):
    """M13 writes it; M12 creates it: reason required, roles and decisions checked, no edits."""
    conn.execute(
        "INSERT INTO shortlist_decision (brief_id, brief_version, candidate_ref, decision,"
        " reason, reviewer_role) VALUES ('example-config-linter', 1, 'cand_1', 'accept',"
        " 'in the field', 'user')"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO shortlist_decision (brief_id, brief_version, candidate_ref, decision,"
            " reason, reviewer_role) VALUES ('b', 1, 'c', 'accept', '  ', 'user')"
        )
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE shortlist_decision SET reason = 'changed'")
    assert BriefRuns(conn).latest("nothing-here") is None
