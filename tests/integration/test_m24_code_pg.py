"""M24 `pigtail brief code` end to end on Postgres, a local snapshot store and fakes (no
network, synthetic data only; ADR-089).

Covers: the full case rule (every winner, matched loser, exemplar and exemplar loser of every
view, as `cost.full_brief_cases` counts); the estimate with the x1.25 contingency and the
approval; `--max-usd` as a hard stop before anything starts; cases a finished pilot coded are
copied, not fetched or paid again; report facts per case (launch events with HN titles <= 25
words, assets at launch, amplifiers by role and bucket, the star trajectory and bursts with the
event that explains them), every fact citing an evidence id that resolves to a snapshot; the
report-only items never reach the coders; no handle anywhere; resume pays for nothing.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from pigtail.connectors.base import RetryPolicy, TokenBucket
from pigtail.connectors.hn import HNStoryMetaConnector
from pigtail.forensics import store as fstore
from pigtail.forensics.facts import TITLE_MAX_WORDS
from pigtail.forensics.pilot import (
    EXIT_BUDGET,
    PilotDeps,
    PilotOptions,
    plan,
    run_pilot,
)
from tests.forensics_fake import ANCHOR, HANDLE, NOW, POINTS, REPOS
from tests.integration.test_m23_pilot_pg import Env, env, q  # noqa: F401 (fixture)

pytestmark = pytest.mark.db
PILOT = PilotOptions(cases=5, approve_paid=True, bootstrap_resamples=200)
FULL = PilotOptions(approve_paid=True, bootstrap_resamples=200, rule="full")
LONG_TITLE = " ".join(f"word{i}" for i in range(40))


class FakeAlgolia:
    """HN Algolia `search` by story ids: title, url, points, time (as the connector asks)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        tags = req.url.params.get("tags", "")
        ids = [int(x) for x in re.findall(r"story_(\d+)", tags)]
        hits = []
        for i in ids:
            full = next((f for f, r in REPOS.items() if 900 + r["id"] % 100 == i), None)
            if full is None:
                continue
            if tags.startswith("front_page") and full != "org-p/alpha-cli":
                continue
            name = full.split("/")[1]
            title = (
                LONG_TITLE
                if full == "org-p/gamma-lib"
                else f"Show HN: {name} – checks configs, by @{HANDLE}"
            )
            hits.append(
                {
                    "objectID": str(i),
                    "title": title,
                    "url": (
                        "https://other-project.example.net/"
                        if full == "org-p/delta-app"
                        else f"https://github.com/{full}"
                    ),
                    "points": POINTS,
                    "created_at_i": int(ANCHOR.timestamp()),
                    "author": HANDLE,  # never asked for; must never be read or stored
                }
            )
        return httpx.Response(200, json={"hits": hits, "nbHits": len(hits)})


def hn(e: Env, fake: FakeAlgolia) -> HNStoryMetaConnector:
    return HNStoryMetaConnector(
        store=e.snaps, pseudonymizer=None, http=fake.client(), env={},
        evidence_sink=e.db.upsert_evidence, sleep=lambda _s: None,
        limiter=TokenBucket(1000, burst=1000), retry=RetryPolicy(max_retries=1),
        clock=lambda: NOW,
    )  # fmt: skip


def seed_stars(e: Env) -> None:
    """A daily series from 2025-11-01 with a burst on the anchor's endpoint days."""
    start, end = date(2025, 11, 1), NOW.date() - timedelta(days=2)
    burst = {date(2026, 3, 10): 400, date(2026, 3, 11): 300}
    for r in REPOS.values():
        d = start
        while d <= end:
            e.conn.execute(
                "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label,"
                " day_boundary_tz, fetched_at) VALUES (%s, %s, %s, 'w', 'America/Los_Angeles',"
                " %s)",
                (r["id"], d, burst.get(d, 1), NOW),
            )
            d += timedelta(days=1)


def deps(e: Env, fake: FakeAlgolia | None = None, **kw: Any) -> PilotDeps:
    d = e.deps(**kw)
    d.hn = hn(e, fake or FakeAlgolia())
    return d


def _full_cases(e: Env) -> int:
    from pigtail.forensics.cost import full_brief_cases

    sid = q(e, "SELECT id FROM brief_selection")[0][0]
    return int(full_brief_cases(e.conn, sid)["total"])


def _walk(obj: Any) -> str:
    return json.dumps(obj, default=str)


def test_m24_code_dry_run_counts_every_case(env: Env) -> None:  # noqa: F811
    """[M24-T1] the full case rule covers exactly the set the projection counted."""
    sel, cases, est, err = plan(env.conn, env.brief, FULL, deps(env))
    assert err is None and sel is not None and est is not None
    assert len(cases) == _full_cases(env) == 11
    assert est["cases_total"] == 11 and est["cases"] == 11  # no pilot yet: all to code
    assert est["rule"] == "full-cases-v1"
    assert est["total_with_contingency_usd"] == pytest.approx(est["total_usd"] * 1.25, rel=1e-3)
    assert est["requires_approval"] is True
    # deterministic: views A then B, winners before their losers
    assert [c.view for c in cases][:7] == ["follow_through"] * 7
    assert cases[0].role == "winner" and cases[1].role == "matched_loser"
    assert len({c.case_key for c in cases}) == 11


def test_m24_code_max_usd_is_a_hard_stop(env: Env) -> None:  # noqa: F811
    """[M24-T1] --max-usd below the estimate with contingency: refused before anything."""
    out = run_pilot(
        env.brief, deps(env), PilotOptions(approve_paid=True, rule="full", max_usd=0.01)
    )
    assert out.exit_code == EXIT_BUDGET and out.status == "refused_budget"
    assert "--max-usd" in out.message
    assert q(env, "SELECT count(*) FROM brief_runs")[0][0] == 0
    assert env.backend.submitted == []


def test_m24_code_needs_approval(env: Env) -> None:  # noqa: F811
    out = run_pilot(env.brief, deps(env), PilotOptions(rule="full"))
    assert out.exit_code == 3 and out.status == "needs_approval"
    assert env.backend.submitted == []


def test_m24_code_end_to_end_reuses_the_pilot(env: Env) -> None:  # noqa: F811
    """[M24-T1, M24-T2] full coding after the pilot: pilot cases copied, the rest coded, report
    facts per case with resolvable evidence, no handle anywhere."""
    seed_stars(env)
    pilot = run_pilot(env.brief, env.deps(), PILOT)
    assert pilot.exit_code == 0, pilot.message
    coder_prompts_before = len([p for b in env.backend.submitted[:2] for p in b])
    assert coder_prompts_before == 10  # 5 cases x 2 passes
    fake = FakeAlgolia()
    _s, _c, est, _e = plan(env.conn, env.brief, FULL, deps(env, fake))
    assert est is not None and est["reuse"]["cases"] == 5 and est["cases"] == 6
    n_batches = len(env.backend.submitted)
    out = run_pilot(env.brief, deps(env, fake), FULL)
    assert out.exit_code == 0, out.message
    rid = out.brief_run_id
    assert q(env, "SELECT kind, status FROM brief_runs WHERE id = %s", rid) == [
        ("coding", "succeeded")
    ]
    # only the 6 new cases were sent to the coders (one batch per pass), never the pilot's 5
    new = env.backend.submitted[n_batches:]
    coder_batches = [b for b in new if "adjudicate" not in b[0][1]["system"][0]["text"]]
    assert [len(b) for b in coder_batches] == [6, 6]
    cases = fstore.load_cases(env.conn, rid)
    assert len(cases) == 11
    assert sum(1 for c in cases if c.reused_from == pilot.brief_run_id) == 5
    # every case coded by both passes with a final value
    for p in ("A", "B", "final"):
        assert fstore.coded_cases(env.conn, rid, p) == {c.case_key for c in cases}
    # the ledger of this run holds only the new cases' calls
    led = q(env, "SELECT count(DISTINCT case_ref) FROM llm_cost_ledger WHERE brief_run_id = %s"
                 " AND prompt_id = 'case-coder-a'", rid)[0][0]  # fmt: skip
    assert led == 6
    # alpha over all 11 cases
    assert {r[0] for r in q(env, "SELECT DISTINCT n_cases FROM brief_reliability"
                                 " WHERE brief_run_id = %s", rid)} == {11}  # fmt: skip
    # no decay checks scheduled by the coding run (the study is the pilot's)
    assert q(env, "SELECT count(*) FROM brief_evidence_decay WHERE brief_run_id = %s", rid) == [
        (0,)
    ]
    # --- report facts ---
    for c in cases:
        f = c.facts
        assert f is not None and f["version"] == "report-facts-v2"
        ev_ids = set(f["evidence"].values())
        for eid in ev_ids:  # every cited item resolves to a present snapshot
            row = q(env, "SELECT content_hash, deletion_state FROM evidence WHERE id = %s", eid)
            assert row and row[0][1] == "present" and env.snaps.exists(row[0][0])
        cited = set()
        for e in f["events"]:
            cited |= set(e["evidence_ids"])
            if e["title"]:
                assert len(e["title"].split()) <= TITLE_MAX_WORDS
                assert e["title_evidence_id"] in ev_ids
        for a in f["assets"]["assets"].values():
            if a["value"] in ("present", "absent"):
                cited.add(a["evidence_id"])
        for amp in f["amplifiers"]:
            cited |= set(amp["evidence_ids"])
        if f["trajectory"].get("evidence_id"):
            cited.add(f["trajectory"]["evidence_id"])
        assert cited <= ev_ids, cited - ev_ids
        blob = _walk(f)
        assert HANDLE not in blob and "SECRET POST TEXT" not in blob
    by = {c.case_key: c.facts for c in cases}
    alpha = by["follow_through:gh:org-p/alpha-cli"]
    assert alpha is not None
    show = [e for e in alpha["events"] if e["kind"] == "show_hn"]
    assert show and show[0]["title"].startswith("Show HN: alpha-cli")
    ph = [e for e in alpha["events"] if e["kind"] == "product_hunt"]
    assert ph and ph[0]["title"] is None and "ADR-085" in ph[0]["title_missing"]
    gamma = by["follow_through:gh:org-p/gamma-lib"]
    assert gamma is not None
    gshow = next(e for e in gamma["events"] if e["kind"] == "show_hn")
    assert gshow["title_truncated"] is True and len(gshow["title"].split()) == TITLE_MAX_WORDS
    # [M24-T2] a title-only HN match (the story links another project) is unconfirmed: it is
    # not a counted launch event, not maintainer amplification and never explains a burst
    delta = by["follow_through:gh:org-p/delta-app"]
    assert delta is not None
    dshow = next(e for e in delta["events"] if e["kind"] == "show_hn")
    assert dshow["confirmed"] is False and dshow["counts"] is False
    assert "unconfirmed (title match)" in dshow["where"]
    damp = {a["role"]: a for a in delta["amplifiers"]}
    assert "show_hn" not in " ".join(damp["maintainer"]["events"])
    assert damp["maintainer"]["unconfirmed_title_matches"]
    assert all(not b["explained_by"].startswith("show_hn") for b in delta["trajectory"]["bursts"])
    # assets at launch from the README at T: the fake README has `brew install <name>`
    inst = alpha["assets"]["assets"]["install_one_liner"]
    assert inst["value"] == "present" and "brew install" in inst["excerpt"]
    assert alpha["assets"]["assets"]["comparison_table"]["value"] == "absent"
    # amplifiers by role and bucket only
    roles = {a["role"]: a for a in alpha["amplifiers"]}
    assert roles["hn_front_page"]["value"] == "present"  # the fake tags alpha-cli
    assert roles["organization"]["value"] == "present"
    assert roles["account_by_follower_bucket"]["value"] == "unknown"
    assert by["follow_through:gh:org-p/beta-tool"]["amplifiers"][2]["value"] == "unknown"
    # trajectory: the burst on the anchor's days is explained by a launch event
    tr = alpha["trajectory"]
    assert tr["bursts"], {k: v for k, v in tr.items() if k != "per_event"}
    assert tr["bursts"][0]["explained_by"] != "unexplained"
    assert tr["bursts"][0]["label"] == "day-level"
    ev = next(p for p in tr["per_event"] if p["event"].startswith("show_hn:"))
    assert ev["gained"]["+1d"]["value"] == 400 and ev["gained"]["+3d"]["value"] == 701
    assert ev["stars_before"]["value"] is not None
    # the report-only items never reached the coders
    for b in new:
        for _cid, p in b:
            text = p["messages"][0]["content"]
            assert "hn_stories" not in text and "star_trajectory" not in text
            assert str(POINTS) not in text
    # private report of the coding run, with the facts; no handle
    js = Path(out.report_paths["json"])
    assert js.name == f"coding-{NOW.date().isoformat()}.json"
    report = json.loads(js.read_text())
    assert report["provenance"]["frame_version"] == "pilot-frame-v1+report-facts-v2"
    assert report["cost"]["reused_cases"] == 5
    assert len(report["facts"]) == 11
    assert HANDLE not in js.read_text()
    # HN raw pages dropped after parsing
    assert q(env, "SELECT count(*) FROM evidence WHERE url LIKE %s AND deletion_state ="
                  " 'present'", "%pigtail_story_meta%")[0][0] == 0  # fmt: skip
    # running it again pays for nothing
    n = len(env.backend.submitted)
    again = run_pilot(env.brief, deps(env, fake), FULL)
    assert again.status == "complete" and len(env.backend.submitted) == n
    # the pilot stays the latest pilot
    assert fstore.latest_pilot(env.conn, env.brief.brief_id)["brief_run_id"] == (  # type: ignore[index]
        pilot.brief_run_id
    )
    assert datetime.fromisoformat(tr["bursts"][0]["onset_day"]).date() == date(2026, 3, 10)


def test_m24_code_anchorless_exemplar_and_resume_after_crash(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[M24-T1] regression (ADR-089 addendum 2): a selected case without an anchor (an exemplar
    the selection stored with `anchor: null`) gets `readme_at_anchor` as a `no_anchor` gap,
    its releases without a window, facts from its first launch event, and is coded; a crash in
    the evidence stage leaves the run resumable, with nothing paid twice."""
    from pigtail.forensics.evidence import EvidenceStage

    env.conn.execute(
        "UPDATE brief_selection_case SET detail = jsonb_set(detail, '{anchor}', 'null')"
        " WHERE candidate_ref = 'gh:org-p/zeta-ex'"
    )
    seed_stars(env)
    real = EvidenceStage.collect
    calls = {"n": 0}

    def flaky(self: Any, pilot: Any, case: Any, cand: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("simulated crash in the evidence stage")
        return real(self, pilot, case, cand)

    monkeypatch.setattr(EvidenceStage, "collect", flaky)
    with pytest.raises(RuntimeError):
        run_pilot(env.brief, deps(env), FULL)
    assert env.backend.submitted == []  # nothing paid before the crash
    rid = q(env, "SELECT id FROM brief_runs WHERE kind = 'coding'")[0][0]
    done = q(
        env,
        "SELECT count(*) FROM brief_pilot_case WHERE brief_run_id = %s"
        " AND evidence_status = 'done'",
        rid,
    )[0][0]
    assert done == 3
    fetched = len(env.gh_fake.requests)
    out = run_pilot(env.brief, deps(env), FULL)  # the same command resumes the same run
    assert out.exit_code == 0, out.message
    assert out.brief_run_id == rid
    assert calls["n"] == 4 + 8  # only the 8 cases not done were collected again
    assert len(env.gh_fake.requests) > fetched
    cases = {c.candidate_ref: c for c in fstore.load_cases(env.conn, rid)}
    z = cases["gh:org-p/zeta-ex"]
    assert z.anchor_at is None
    gaps = dict(
        q(
            env,
            "SELECT source, reason FROM brief_case_gap WHERE brief_run_id = %s AND case_key = %s",
            rid,
            z.case_key,
        )
    )
    assert gaps["readme_at_anchor"] == "no_anchor"
    kinds = {
        r[0]
        for r in q(
            env,
            "SELECT kind FROM brief_case_evidence WHERE brief_run_id = %s AND case_key = %s",
            rid,
            z.case_key,
        )
    }
    assert {"releases", "readme_current", "launch_events"} <= kinds
    assert z.facts is not None and z.facts["anchor_source"] == "first_launch_event"
    assert z.facts["assets"]["assets"]["demo_media"]["value"] == "unknown"
    assert z.facts["trajectory"]["anchor"] is not None
    assert z.case_key in fstore.coded_cases(env.conn, rid, "final")
    prompts = [p["messages"][0]["content"] for b in env.backend.submitted for _c, p in b]
    assert any("Reference date T: unknown (no anchor)" in t for t in prompts)
