"""M25 `pigtail plan` over a real M24 report on Postgres and fakes (synthetic, no network;
ADR-091). The M24 fixture has two view-A pairs, so every evidence-based section must say
"insufficient evidence in this neighbourhood" (DELIVERABLES D3), while the similar projects and
the context read from the coding run and the selection are present."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pigtail.forensics.patterns import INSUFFICIENT
from pigtail.forensics.plan import SECTIONS, load_context, run_plan
from pigtail.forensics.report_brief import ReportOptions, run_report
from tests.forensics_fake import HANDLE, NOW
from tests.integration.test_m23_pilot_pg import Env, env, q  # noqa: F401 (fixture)
from tests.integration.test_m24_report_pg import _coded, rdeps

pytestmark = pytest.mark.db


def test_m25_plan_from_the_m24_report_insufficient_and_reproducible(env: Env) -> None:  # noqa: F811
    _coded(env)
    rep = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True))
    assert rep.exit_code == 0, rep.message
    report = json.loads(Path(rep.report_paths["json"]).read_text())
    ctx = load_context(env.conn, report)
    assert ctx.similarity and ctx.evidence and ctx.pairs.get("A")  # the fixture stores no values
    out = run_plan(env.brief, env.data_dir, conn=env.conn, day=NOW.date())
    assert out.exit_code == 0, out.message
    plan = json.loads(Path(out.paths["json"]).read_text())
    assert plan["provenance"]["context_loaded"] is True
    assert plan["provenance"]["report_run"] == report["provenance"]["report_run"]
    for s in SECTIONS:
        if s != "similar_projects":
            assert plan[s]["status"] == INSUFFICIENT, s
    assert plan["similar_projects"]["status"] == "ok"
    assert all(c["why_similar"] != "unknown (no selection context)"
               for c in plan["similar_projects"]["cases"])  # fmt: skip
    md = Path(out.paths["md"]).read_text()
    assert INSUFFICIENT in md and HANDLE not in md
    # the plan sits beside the report, privately
    assert Path(out.paths["md"]).parent == Path(rep.report_paths["md"]).parent
    # reproducible: same brief version, report and inputs -> byte-identical files
    again = run_plan(env.brief, env.data_dir, conn=env.conn, day=NOW.date())
    assert again.paths == out.paths
    assert Path(again.paths["md"]).read_text() == md
    # nothing was paid or written to the database by the plan
    n = q(env, "SELECT count(*) FROM llm_cost_ledger")[0][0]
    run_plan(env.brief, env.data_dir, conn=env.conn, day=NOW.date())
    assert q(env, "SELECT count(*) FROM llm_cost_ledger")[0][0] == n
