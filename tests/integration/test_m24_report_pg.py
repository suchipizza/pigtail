"""M24 `pigtail report brief` end to end on Postgres and fakes (synthetic, no network; ADR-089).

Covers: the narrative cases (exemplars, then view A's headline pairs by winner rank with the
nearest headline loser); the estimate and approval; the narrative check (every kept sentence
cites evidence ids of its case that resolve to present snapshots; fabricated numbers, missing or
foreign ids and handles are dropped); the report sections in the owner's order; the header's
provenance and cost; the thinking setting recorded; `--final` marks the report final and purges
the unreferenced cache, logged; no handle in the report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from pigtail.forensics.pilot import run_pilot
from pigtail.forensics.report_brief import ReportDeps, ReportOptions, run_report
from pigtail.llm.pricing import TokenUsage, cost_usd
from pigtail.llm.types import BackendResponse
from tests.forensics_fake import HANDLE, NOW, CodingBatchBackend
from tests.integration.test_m23_pilot_pg import Env, env, q  # noqa: F401 (fixture)
from tests.integration.test_m24_code_pg import FULL, PILOT, FakeAlgolia, deps, seed_stars

pytestmark = pytest.mark.db


def narrative_answer(prompt: str) -> dict[str, Any]:
    sheet = json.loads(prompt.rsplit("\n\nReturn `sentences`", 1)[0])
    ev = sheet["launch_events"][0] if sheet["launch_events"] else None
    ids = ev["evidence_ids"] if ev else []
    return {
        "sentences": [
            {"text": f"It was posted on {ev['where']} on {ev['when']}." if ev else "Unknown.",
             "evidence_ids": ids},
            {"text": "It gained 123456 stars overnight.", "evidence_ids": ids},  # fabricated
            {"text": "It was popular.", "evidence_ids": []},  # no citation
            {"text": "It was posted.", "evidence_ids": ["ev_not_in_this_case"]},
            {"text": f"Shared by @{HANDLE}.", "evidence_ids": ids},  # a handle
        ]
    }  # fmt: skip


class ReportBackend(CodingBatchBackend):
    def _resp(self, params: dict[str, Any], batch_id: str | None) -> BackendResponse:
        if "case narratives" not in params["system"][0]["text"]:
            return super()._resp(params, batch_id)
        usage = TokenUsage(input=3000, output=800, cache_read=1500)
        model = params["model"]
        return BackendResponse(
            data=narrative_answer(params["messages"][0]["content"]), model=model,
            input_tokens=usage.input, output_tokens=usage.output,
            cache_read_tokens=usage.cache_read,
            cost_usd=cost_usd(model, usage, batch=batch_id is not None) or 0.0,
            batch_id=batch_id,
        )  # fmt: skip


def rdeps(e: Env) -> ReportDeps:
    return ReportDeps(
        conn=e.conn, client=e.client(), snapshots=e.snaps, data_dir=e.data_dir,
        clock=lambda: NOW, sleep=lambda _s: None, code_commit="c0ffee1",
    )  # fmt: skip


def _coded(e: Env) -> None:
    e.backend = ReportBackend()
    seed_stars(e)
    assert run_pilot(e.brief, e.deps(), PILOT).exit_code == 0
    fake = FakeAlgolia()
    assert run_pilot(e.brief, deps(e, fake), FULL).exit_code == 0


def test_m24_t5_report_needs_a_coding_run_and_approval(env: Env) -> None:  # noqa: F811
    out = run_report(env.brief, rdeps(env), ReportOptions())
    assert out.status == "refused" and "brief code" in out.message
    _coded(env)
    dry = run_report(env.brief, rdeps(env), ReportOptions(dry_run=True))
    assert dry.exit_code == 0 and dry.estimate and dry.estimate["narratives"] == 5
    assert dry.summary["narratives"][0].startswith("exemplar")
    assert q(env, "SELECT count(*) FROM brief_runs WHERE kind = 'report'") == [(0,)]
    out = run_report(env.brief, rdeps(env), ReportOptions())
    assert out.exit_code == 3 and out.status == "needs_approval"
    capped = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True, max_usd=0.001))
    assert capped.exit_code == 4


def test_m24_t5_report_end_to_end(env: Env) -> None:  # noqa: F811
    _coded(env)
    out = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True))
    assert out.exit_code == 0, out.message
    md = Path(out.report_paths["md"])
    js = Path(out.report_paths["json"])
    assert md.name == f"report-{NOW.date().isoformat()}.md"
    text = md.read_text()
    rep = json.loads(js.read_text())
    # sections in the owner's order
    pos = [text.index(h) for h in ("## (a) Case narratives", "## (b) Comparison table",
                                   "## (c) Winner-vs-loser patterns",
                                   "## (d) D3 plan and fast-path verdict")]  # fmt: skip
    assert pos == sorted(pos)
    # header provenance
    p = rep["provenance"]
    assert p["brief_version"] == env.brief.version and p["data_version"] == "dv1-x"
    assert p["code_commit"] == "c0ffee1"
    assert p["frame_version"] == "pilot-frame-v1+report-facts-v1"
    assert p["narrative_thinking"] == "adaptive+effort:low"  # claude-opus-5-5, disabled
    assert p["label"] == "attention-based"
    assert p["cost_usd"]["report_run"] > 0 and p["cost_usd"]["coding_run"] > 0
    # narrative cases: the exemplar, then view A pairs by rank, nearest headline loser
    labels = [n["case"] for n in rep["narratives"]]
    assert labels == ["org-p/zeta-ex", "org-p/alpha-cli", "org-p/beta-tool", "org-p/gamma-lib",
                      "org-p/delta-app"]  # fmt: skip
    # the check: only the first sentence survives; every kept claim resolves to a snapshot
    for n in rep["narratives"]:
        nar = n["narrative"]
        assert len(nar["sentences"]) == 1, nar
        assert nar["dropped"] == {"number_not_in_facts": 1, "no_evidence_id": 1,
                                  "evidence_id_not_resolvable": 1, "handle": 1}  # fmt: skip
        for s in nar["sentences"]:
            assert s["evidence_ids"]
            for eid in s["evidence_ids"]:
                assert eid in rep["evidence_index"]
                row = q(env, "SELECT content_hash, deletion_state FROM evidence WHERE id = %s",
                        eid)  # fmt: skip
                assert row[0][1] == "present" and env.snaps.exists(row[0][0])
    assert out.summary["sentences_kept"] == 5 and out.summary["sentences_dropped"] == 20
    # comparison table and patterns
    assert len(rep["comparison"]) == 5
    assert set(rep["patterns"]["views"]) == {"A", "B"}
    fa = rep["patterns"]["views"]["A"]["features"]
    assert all("n_known" in f["winners"] and "counterexamples" in f for f in fa)
    assert "insufficient evidence in this neighbourhood" in text  # 2 winners in the fixture
    assert "Placeholder" not in text and "M25" in text and "ADR-088.6" in text
    assert HANDLE not in text and "123456" not in text
    assert not re.search(r"@[A-Za-z]", text.split("## Evidence index")[0])
    # running it again is served from the result cache: nothing new is submitted
    n = len(env.backend.submitted)
    again = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True))
    assert again.exit_code == 0 and len(env.backend.submitted) == n


def test_m24_t6_final_marks_report_and_purges_unreferenced_cache(env: Env) -> None:  # noqa: F811
    from pigtail.capture.models import Evidence, evidence_id
    from pigtail.capture.snapshots import SnapshotMeta

    _coded(env)
    # an unreferenced cache blob (collected before, used by no brief)
    meta = SnapshotMeta(source="github", url="https://api.github.com/repos/org-z/old",
                        fetched_at=NOW, collector_version="x/0", terms_basis="t",
                        content_type="application/json")  # fmt: skip
    h = env.snaps.put(b'{"old": true}', meta)
    url = "https://api.github.com/repos/org-z/old"
    env.db.upsert_evidence(Evidence(
        id=evidence_id("github", url, h), source="github", url=url, fetched_at=NOW,
        content_hash=h, snapshot_ref=env.snaps.ref(h), content_type="application/json",
        http_status=200, reliability="high", terms_basis="t", retention_class="project_level",
        deletion_state="present", collector_version="x/0", case_id=None, repo_id=None,
        run_id=None,
    ))  # fmt: skip
    referenced = q(env, "SELECT count(*) FROM brief_case_evidence")[0][0]
    out = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True, final=True))
    assert out.exit_code == 0, out.message
    fin = out.summary["final"]
    assert fin["report_final"] and fin["cache_purge"]["blobs"] >= 1
    assert q(env, "SELECT count(*) FROM brief_report_final")[0][0] == 1
    assert not env.snaps.exists(h)
    assert q(env, "SELECT deletion_state FROM evidence WHERE content_hash = %s", h) == [
        ("raw_dropped",)
    ]
    assert q(env, "SELECT count(*) FROM deletion_log WHERE reason = 'purpose_limitation'"
                  " AND action = 'cache_purged'")[0][0] >= 1  # fmt: skip
    # every item a brief references is still there
    gone = q(env, "SELECT count(*) FROM brief_case_evidence ce JOIN evidence e ON e.id ="
                  " ce.evidence_id WHERE e.deletion_state <> 'present'")[0][0]  # fmt: skip
    assert referenced > 0 and gone == 0
