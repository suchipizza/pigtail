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
            {
                "text": f"It was posted on {ev['where']} on {ev['when']}." if ev else "Unknown.",
                "evidence_ids": ids,
            },
            {"text": "It gained 123456 stars overnight.", "evidence_ids": ids},  # fabricated
            {"text": "It was popular.", "evidence_ids": []},  # no citation
            {"text": "It was posted.", "evidence_ids": ["ev_not_in_this_case"]},
            {"text": f"Shared by @{HANDLE}.", "evidence_ids": ids},  # a handle
        ]
    }


class ReportBackend(CodingBatchBackend):
    def _resp(self, params: dict[str, Any], batch_id: str | None) -> BackendResponse:
        if "case narratives" not in params["system"][0]["text"]:
            return super()._resp(params, batch_id)
        usage = TokenUsage(input=3000, output=800, cache_read=1500)
        model = params["model"]
        return BackendResponse(
            data=narrative_answer(params["messages"][0]["content"]),
            model=model,
            input_tokens=usage.input,
            output_tokens=usage.output,
            cache_read_tokens=usage.cache_read,
            cost_usd=cost_usd(model, usage, batch=batch_id is not None) or 0.0,
            batch_id=batch_id,
        )


def rdeps(e: Env) -> ReportDeps:
    return ReportDeps(
        conn=e.conn,
        client=e.client(),
        snapshots=e.snaps,
        data_dir=e.data_dir,
        clock=lambda: NOW,
        sleep=lambda _s: None,
        code_commit="c0ffee1",
    )


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
    pos = [
        text.index(h)
        for h in (
            "## (a) Case narratives",
            "## (b) Comparison table",
            "## (c) Winner-vs-loser patterns",
            "## (d) D3 plan and fast-path verdict",
        )
    ]
    assert pos == sorted(pos)
    # header provenance
    p = rep["provenance"]
    assert p["brief_version"] == env.brief.version and p["data_version"] == "dv1-x"
    assert p["code_commit"] == "c0ffee1"
    assert p["frame_version"] == "pilot-frame-v1+report-facts-v3"
    assert p["narrative_thinking"] == "adaptive+effort:low"  # claude-opus-5-5, disabled
    assert p["label"] == "attention-based"
    assert p["cost_usd"]["report_run"] > 0 and p["cost_usd"]["coding_run"] > 0
    # narrative cases: the exemplar, then view A pairs by rank, nearest headline loser
    labels = [n["case"] for n in rep["narratives"]]
    assert labels == [
        "org-p/zeta-ex",
        "org-p/alpha-cli",
        "org-p/beta-tool",
        "org-p/gamma-lib",
        "org-p/delta-app",
    ]
    # the check: only the first sentence survives; every kept claim resolves to a snapshot
    for n in rep["narratives"]:
        nar = n["narrative"]
        assert len(nar["sentences"]) == 1, nar
        assert nar["dropped"] == {
            "number_not_in_facts": 1,
            "no_evidence_id": 1,
            "evidence_id_not_resolvable": 1,
            "handle": 1,
        }
        for s in nar["sentences"]:
            assert s["evidence_ids"]
            for eid in s["evidence_ids"]:
                assert eid in rep["evidence_index"]
                row = q(env, "SELECT content_hash, deletion_state FROM evidence WHERE id = %s", eid)
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
    meta = SnapshotMeta(
        source="github",
        url="https://api.github.com/repos/org-z/old",
        fetched_at=NOW,
        collector_version="x/0",
        terms_basis="t",
        content_type="application/json",
    )
    h = env.snaps.put(b'{"old": true}', meta)
    url = "https://api.github.com/repos/org-z/old"
    env.db.upsert_evidence(
        Evidence(
            id=evidence_id("github", url, h),
            source="github",
            url=url,
            fetched_at=NOW,
            content_hash=h,
            snapshot_ref=env.snaps.ref(h),
            content_type="application/json",
            http_status=200,
            reliability="high",
            terms_basis="t",
            retention_class="project_level",
            deletion_state="present",
            collector_version="x/0",
            case_id=None,
            repo_id=None,
            run_id=None,
        )
    )
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
    assert (
        q(
            env,
            "SELECT count(*) FROM deletion_log WHERE reason = 'purpose_limitation'"
            " AND action = 'cache_purged'",
        )[0][0]
        >= 1
    )
    # every item a brief references is still there
    gone = q(
        env,
        "SELECT count(*) FROM brief_case_evidence ce JOIN evidence e ON e.id ="
        " ce.evidence_id WHERE e.deletion_state <> 'present'",
    )[0][0]
    assert referenced > 0 and gone == 0


def _blob(e: Env, url: str, body: bytes) -> tuple[str, str]:
    """A present synthetic snapshot and its evidence row; returns (evidence id, hash)."""
    from pigtail.capture.models import Evidence, evidence_id
    from pigtail.capture.snapshots import SnapshotMeta

    meta = SnapshotMeta(
        source="npm_downloads",
        url=url,
        fetched_at=NOW,
        collector_version="x/0",
        terms_basis="t",
        content_type="application/json",
    )
    h = e.snaps.put(body, meta)
    eid = evidence_id("npm_downloads", url, h)
    e.db.upsert_evidence(
        Evidence(
            id=eid,
            source="npm_downloads",
            url=url,
            fetched_at=NOW,
            content_hash=h,
            snapshot_ref=e.snaps.ref(h),
            content_type="application/json",
            http_status=200,
            reliability="high",
            terms_basis="t",
            retention_class="project_level",
            deletion_state="present",
            collector_version="x/0",
            case_id=None,
            repo_id=None,
            run_id=None,
        )
    )
    return eid, h


def test_m24_t7_purge_keeps_evidence_referenced_only_by_outcomes_facts_or_report(
    env: Env,  # noqa: F811
) -> None:
    """M24-T7: evidence cited only by a secondary outcome (ADR-090), only by a case's report
    facts, or only by a stored report JSON is never purged; unreferenced evidence still is."""
    from psycopg.types.json import Jsonb

    from pigtail.capture.db import CaptureDB
    from pigtail.privacy.cache_purge import purge_unreferenced, referenced_ids, report_refs

    _coded(env)
    base = "https://api.npmjs.org/downloads/range/x/"
    by_outcome, h1 = _blob(env, base + "syn-a", b'{"a":1}')
    by_fact, h2 = _blob(env, base + "syn-b", b'{"b":2}')
    by_report, h3 = _blob(env, base + "syn-c", b'{"c":3}')
    stale, h4 = _blob(env, base + "syn-d", b'{"d":4}')
    rid, key, sel, ref = q(
        env,
        "SELECT p.brief_run_id, p.case_key, b.selection_id,"
        " p.candidate_ref FROM brief_pilot_case p JOIN brief_pilot b"
        " USING (brief_run_id) ORDER BY 1 DESC, 2 LIMIT 1",
    )[0]
    env.conn.execute(
        "INSERT INTO brief_secondary_outcome (selection_id, candidate_ref, repo_full_name,"
        " metric, status, value, record, rule_version, as_of) VALUES (%s, %s, %s,"
        " 'adopt.npm_downloads_launch@0-2', 'observed', 5, %s, 'downloads-v1', %s)",
        (sel, ref, ref[3:], Jsonb({"evidence": [{"evidence_id": by_outcome}]}), NOW.date()),
    )
    facts = q(
        env,
        "SELECT facts FROM brief_pilot_case WHERE brief_run_id = %s AND case_key = %s",
        rid,
        key,
    )[0][0]
    facts = dict(facts or {})
    facts["synthetic_extra"] = {"nested": [{"evidence_ids": [by_fact]}]}
    env.conn.execute(
        "UPDATE brief_pilot_case SET facts = %s WHERE brief_run_id = %s AND case_key = %s",
        (Jsonb(facts), rid, key),
    )
    rep = Path(env.data_dir) / "reports" / "syn-brief" / "v1" / "report-2026-01-01.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps({"evidence_index": {by_report: {}}}))
    db = CaptureDB(env.conn)
    refs = referenced_ids(db, report_refs(env.data_dir))
    assert {by_outcome, by_fact, by_report} <= refs and stale not in refs
    # the dry-run preview no longer counts them, and matches what the purge then does
    dry = run_report(env.brief, rdeps(env), ReportOptions(dry_run=True))
    prev = dry.summary["cache_purge_preview"]
    assert prev["version"] == "cache-purge-v2" and prev["blobs"] >= 1
    res = purge_unreferenced(db, env.snaps, apply=True, extra_refs=report_refs(env.data_dir))
    assert res.blobs == prev["blobs"]
    for h in (h1, h2, h3):
        assert env.snaps.exists(h)
        assert q(env, "SELECT deletion_state FROM evidence WHERE content_hash = %s", h) == [
            ("present",)
        ]
    assert not env.snaps.exists(h4)
    # every coded citation still resolves
    gone = q(
        env,
        "SELECT count(*) FROM brief_coding c, unnest(c.evidence_ids) i JOIN evidence e"
        " ON e.id = i WHERE e.deletion_state <> 'present'",
    )[0][0]
    assert gone == 0


def test_m24_r1_report_fixes(env: Env) -> None:  # noqa: F811
    """Verifier M24 round 1 fixes 1, 3, 4, 6 and 8 and the refresh (ADR-089 addendum 4): the
    selection record and diagnostics, definition-sensitive flags wherever a case appears, the
    distribution-example labels, asset precision, unknown assets, the per-field alpha panel with
    the full-run label, stale facts refused until `--refresh-facts`."""
    from pigtail.forensics.pilot import refresh_facts

    _coded(env)
    env.conn.execute(
        "UPDATE brief_selection_case SET sensitivity_flags = ARRAY['definition_sensitive']"
        " WHERE candidate_ref = 'gh:org-p/alpha-cli' AND view = 'follow_through'"
    )
    # stale facts (an older fact version): refused until refreshed, no LLM call
    env.conn.execute(
        "UPDATE brief_pilot_case SET facts = jsonb_set(facts, '{version}', '\"report-facts-v1\"')"
        " WHERE candidate_ref = 'gh:org-p/beta-tool'"
    )
    n = len(env.backend.submitted)
    out = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True))
    assert out.status == "refused" and "--refresh-facts" in out.message
    res = refresh_facts(env.brief, deps(env, FakeAlgolia()))
    assert res["cases_with_facts"] >= 1 and len(env.backend.submitted) == n
    # a case whose README at T is missing: its assets print as unknown, never "none detected"
    env.conn.execute(
        "UPDATE brief_pilot_case SET facts = jsonb_set(facts, '{assets,all_unknown}', 'true')"
        " WHERE candidate_ref = 'gh:org-p/gamma-lib' AND view = 'follow_through'"
    )
    out = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True))
    assert out.exit_code == 0, out.message
    text = Path(out.report_paths["md"]).read_text()
    rep = json.loads(Path(out.report_paths["json"]).read_text())
    # fix 1: selection record and diagnostics; flags in narratives, table and counterexamples
    assert "## Selection record and diagnostics" in text
    assert "relevance-filter precision" in text and "Sensitivity check" in text
    assert "Balance after matching" in text
    diag = rep["selection_diagnostics"]
    assert {"success_definition", "shortlist", "balance", "sensitivity"} <= set(diag)
    nar = {x["case"]: x for x in rep["narratives"]}
    assert "[definition-sensitive]" in nar["org-p/alpha-cli"]["label"]
    row = next(c for c in rep["comparison"] if c["case"] == "org-p/alpha-cli")
    assert row["definition_sensitive"] is True
    assert "org-p/alpha-cli [definition-sensitive]" in json.dumps(rep["patterns"])
    assert rep["definition_sensitive_cases"] == ["org-p/alpha-cli (view A)"]
    # fix 6: unknown assets, "none found" on an empty counterexample side
    assert "assets at launch: unknown (no README at T)" in text
    assert "none detected []" not in text
    g = next(c for c in rep["comparison"] if c["case"] == "org-p/gamma-lib")
    assert g["assets"] == ["unknown (no README at T)"]
    # fix 3: distribution-example tables carry the labels, alpha and "not assessable"
    ex = rep["patterns"]["distribution_examples"]["A"]["features"]
    assert all(f["transferability"]["label"] == "not assessable" for f in ex if not f["sufficient"])
    assert "insufficient evidence in this neighbourhood" in text.split("### Distribution")[1]
    # fix 4: asset findings carry the measured precision, not "no alpha"
    feats = rep["patterns"]["views"]["A"]["features"]
    a = next(f for f in feats if f["feature"] == "asset.screenshots")
    # round 2 fix 2: the real-data precision (verifier hand check) is the measured precision;
    # 0.71 is "borderline", the comparison table (0.56) "low reliability"
    assert a["reliability"]["precision"] == 0.71
    assert "verifier hand check round 2, n=65" in a["reliability"]["labels"][0]
    assert "borderline" in a["reliability"]["labels"]
    ct = next(f for f in feats if f["feature"] == "asset.comparison_table")
    assert (
        ct["reliability"]["precision"] == 0.56 and "low reliability" in ct["reliability"]["labels"]
    )
    assert "| screenshots |" in text
    # round 2 fix 3: no stale "pilot" label anywhere in the report
    assert "pilot, n = " not in text
    # fix 8: the alpha panel, labelled for the full run
    assert "## Methods and data quality: per-field agreement" in text
    assert "full run, n = 11" in text and "pilot, n = 11" not in text


def test_m24_r2_open_bursts_and_download_evidence(env: Env) -> None:  # noqa: F811
    """Verifier M24 round 2 fixes 4 and 5: an open burst is rendered as open with its first
    7 days, never its total so far as its size; download evidence ids join the evidence index."""
    from psycopg.types.json import Jsonb

    from pigtail.capture.models import Evidence, evidence_id
    from pigtail.capture.snapshots import SnapshotMeta

    _coded(env)
    # a burst still above its baseline at the series' end (open) on a narrative case
    env.conn.execute(
        "UPDATE brief_pilot_case SET facts = jsonb_set(facts, '{trajectory,bursts}', %s)"
        " WHERE candidate_ref = 'gh:org-p/alpha-cli' AND view = 'follow_through'",
        (
            Jsonb(
                [
                    {
                        "onset_day": "2026-03-10",
                        "open": True,
                        "days": 200,
                        "stars_total": 99999,
                        "stars_total_complete": True,
                        "stars_first_7d": 1234,
                        "first_7d_complete": True,
                        "peak_day": "2026-05-01",
                        "peak_stars": 800,
                        "stars_48h": 700,
                        "explained_by": "unexplained",
                        "label": "day-level",
                    }
                ]
            ),
        ),
    )
    # a download record citing a stored evidence item
    meta = SnapshotMeta(
        source="npm_downloads",
        url="https://api.npmjs.org/downloads/x",
        fetched_at=NOW,
        collector_version="x/0",
        terms_basis="t",
        content_type="application/json",
    )
    h = env.snaps.put(b'{"downloads": 1}', meta)
    eid = evidence_id("npm_downloads", meta.url, h)
    env.db.upsert_evidence(
        Evidence(
            id=eid,
            source="npm_downloads",
            url=meta.url,
            fetched_at=NOW,
            content_hash=h,
            snapshot_ref=env.snaps.ref(h),
            content_type="application/json",
            http_status=200,
            reliability="high",
            terms_basis="t",
            retention_class="project_level",
            deletion_state="present",
            collector_version="x/0",
            case_id=None,
            repo_id=None,
            run_id=None,
        )
    )
    sid = q(env, "SELECT id FROM brief_selection")[0][0]
    rec = {
        "value": 500,
        "status": "observed",
        "exploratory": True,
        "evidence": [{"evidence_id": eid, "content_hash": h}],
    }
    env.conn.execute(
        "INSERT INTO brief_secondary_outcome (selection_id, candidate_ref, repo_full_name,"
        " metric, status, value, record, rule_version, as_of) VALUES (%s, 'gh:org-p/alpha-cli',"
        " 'org-p/alpha-cli', 'adopt.npm_downloads_follow@3-30', 'observed', 500, %s,"
        " 'downloads-v1', '2026-09-28')",
        (sid, Jsonb(rec)),
    )
    out = run_report(env.brief, rdeps(env), ReportOptions(approve_paid=True))
    assert out.exit_code == 0, out.message
    text = Path(out.report_paths["md"]).read_text()
    rep = json.loads(Path(out.report_paths["json"]).read_text())
    nar = next(n for n in rep["narratives"] if n["case"] == "org-p/alpha-cli")
    b = nar["facts"]["largest_bursts"][0]
    assert b["open"] is True and b["stars_first_7_days"] == 1234
    assert "stars_total" not in b and b["stars_so_far"] == 99999
    assert "1234 stars in the first 7 days" in text
    assert "open: still above the pre-burst baseline" in text
    assert "99999 stars over its" not in text
    row = next(c for c in rep["comparison"] if c["case"] == "org-p/alpha-cli")
    assert row["largest_burst_stars"] == 1234 and row["open_bursts"] == 1
    assert eid in rep["evidence_index"]
    assert eid in text.split("## Evidence index")[1]
