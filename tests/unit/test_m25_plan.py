"""M25 D3 plan generator on the synthetic example brief and synthetic reports (DELIVERABLES D3;
PRD F10, F11 R11.1-R11.2; ADR-091). Deterministic, no LLM, no network, no database."""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from pigtail.forensics.patterns import INSUFFICIENT
from pigtail.forensics.plan import (
    EXPERIMENTAL,
    MIN_CASES,
    SECTIONS,
    PlanInputs,
    build_plan,
    load_inputs,
    lock_predictions,
    plan_markdown,
    run_plan,
)
from tests import plan_fake as pf

H = "0" * 64


def _plan(n: int = 12, inputs: PlanInputs | None = None, **kw):  # type: ignore[no-untyped-def]
    b = pf.example_brief(**kw)
    return build_plan(b, pf.report(b, n), report_hash=H, inputs=inputs, ctx=pf.context(n))


def test_m25_d3_2_every_recommendation_cites_pattern_cases_n_contrast_reliability() -> None:
    """D3 acceptance: every recommendation cites its pattern and >= 3 cases, with n, the loser
    contrast and the reliability labels; only report features; evidence ids attached."""
    plan = _plan()
    rp = plan["recommended_patterns"]
    assert rp["status"] == "ok" and rp["patterns"]
    feats = {
        f["feature"] for f in pf.report(pf.example_brief())["patterns"]["views"]["A"]["features"]
    }
    for r in rp["patterns"]:
        assert r["feature"] in feats and r["pattern_ref"].startswith("report patterns, view")
        assert len(r["cited_cases"]) >= MIN_CASES and r["supporting_cases"] >= MIN_CASES
        assert r["winners"]["n_known"] >= 10 and r["matched_losers"]["n_known"] >= 10
        assert "winners vs" in r["loser_contrast"] and r["d"] > 0
        assert r["reliability_labels"] and r["counterexamples"] is not None
        assert all(e.startswith("ev_") for e in r["evidence_ids"])
        assert r["evidence_ids"] or r["evidence_note"]  # fix 4: ids, or why none
        if r["feature"].startswith(("asset.", "launch.", "readme.")):
            assert r["evidence_ids"]
    md = plan_markdown(plan)
    assert "deterministic rule (no alpha)" in md and "Counterexamples:" in md
    # conditions of the project are never recommended (organisation owner is in every case)
    assert "amplifier.organization" not in {r["feature"] for r in rp["patterns"]}


def test_m25_d3_2_ranking_and_channels_avoided() -> None:
    plan = _plan()
    order = [r["feature"] for r in plan["recommended_patterns"]["patterns"]]
    assert order.index("asset.install_one_liner") < order.index("launch.product_hunt")
    ph = next(
        r for r in plan["recommended_patterns"]["patterns"] if r["feature"] == "launch.product_hunt"
    )
    assert {"condition": "channel `product_hunt` in the brief's planned channels",
            "status": "unmet"} in ph["preconditions"]  # fmt: skip
    avoid = _plan(avoid=["product_hunt"])
    assert "launch.product_hunt" not in [
        r["feature"] for r in avoid["recommended_patterns"]["patterns"]
    ]
    why = {x["feature"]: x["why"] for x in avoid["recommended_patterns"]["not_recommended"]}
    assert "channels to avoid" in why["launch.product_hunt"]
    assert "Product Hunt" not in avoid["calendar"]["channels_scheduled"]


def test_m25_d3_insufficient_evidence_everywhere_on_a_thin_report() -> None:
    """D3: too little evidence -> "insufficient evidence in this neighbourhood", no
    extrapolation (2 pairs: below 10 known per side)."""
    plan = _plan(n=2)
    for s in SECTIONS:
        if s == "similar_projects":
            continue
        assert plan[s]["status"] == INSUFFICIENT, s
    assert plan["recommended_patterns"]["patterns"] == []
    assert plan["predictions"]["items"] == []
    assert plan["calendar"]["channels_scheduled"] == []
    md = plan_markdown(plan)
    assert md.count(INSUFFICIENT) >= 7


def test_m25_d3_6_calendar_from_winners_launch_events() -> None:
    inputs = PlanInputs.model_validate(
        {"available_assets": ["install_one_liner"], "time_budget_hours_per_week": 5,
         "launch_window": {"start": "2026-11-03"}}
    )  # fmt: skip
    plan = _plan(inputs=inputs)
    cal = plan["calendar"]
    assert cal["status"] == "ok" and cal["launch_day"].startswith("2026-11-03")
    assert cal["channels_scheduled"] == ["Product Hunt"]  # Show HN: d = 0, reference only
    assert cal["channels_reference_only"] == []
    assert [x["where"] for x in cal["table_stakes"]] == ["Hacker News (Show HN)"]
    hn = next(c for c in plan["launch_timing"]["channels"] if c["kind"] == "show_hn")
    assert hn["winners"] == 10 and hn["median_offset_days"] == 0 and hn["hours_utc"]["min"] == 15
    assert hn["losers"] == 10  # the loser contrast is shown, not hidden
    whens = [e["when"] for e in cal["entries"]]
    assert "L (2026-11-03)" in whens and any(w.startswith("L-28 (2026-10-06)") for w in whens)
    assert cal["pre_launch_hours"] == 20.0
    gaps = {i["asset"]: i["status"] for i in plan["readiness_gaps"]["items"]}
    assert gaps["install_one_liner"] == "ready" and gaps["quick_start_section"].startswith("gap")
    assert plan["provenance"]["inputs_missing"] == []


def test_m25_d3_missing_inputs_are_named_and_calendar_is_relative() -> None:
    plan = _plan()
    assert plan["provenance"]["inputs_missing"] == [
        "available_assets", "time_budget_hours_per_week", "launch_window"
    ]  # fmt: skip
    assert plan["calendar"]["launch_day"].startswith("L (symbolic")
    assert all("unknown" in i["status"] for i in plan["readiness_gaps"]["items"])
    assert "Missing plan inputs" in plan_markdown(plan)


def test_m25_d3_5_asset_checklist_examples_from_report_cases() -> None:
    b = pf.example_brief()
    rep = pf.report(b, mode="indep")
    ac = build_plan(b, rep, report_hash=H, ctx=pf.context(mode="indep"))["asset_checklist"]
    demo = next(i for i in ac["items"] if i["asset"] == "demo_media")
    assert demo["examples"] and all(e["evidence_ids"] for e in demo["examples"])
    assert demo["examples"][0]["excerpt"] == "![demo](docs/demo.gif)"


def test_m25_d3_7_predictions_from_matched_pair_distribution() -> None:
    pr = _plan()["predictions"]
    p = next(i for i in pr["items"] if i["metric"] == "att.stars_follow@3-30")
    assert p["expected_band"] == {"low": 46.5, "high": 906.5}
    assert p["observed_range"] == {"min": 41.0, "max": 912.0}
    assert p["window"] == "days 3–30 after T" and p["pattern"] and p["probability"] == 1.0
    assert "not a calibrated probability" in p["probability_basis"]


def test_m25_d3_3_trending_separate_and_experimental() -> None:
    # 9 pairs: below 10 known per side, so positive contrasts are only experimental signals
    plan = _plan(n=9)
    tr = plan["trending_opportunities"]
    assert tr["status"] == "ok" and tr["label"] == EXPERIMENTAL
    assert all(EXPERIMENTAL in s["labels"] for s in tr["signals"])
    assert plan["recommended_patterns"]["patterns"] == []  # never recommended
    assert "## 3. Trending opportunities (experimental" in plan_markdown(plan)


def test_m25_d3_8_adaptation_notes_per_recommendation() -> None:
    plan = _plan()
    notes = plan["adaptation_notes"]["notes"]
    assert [n["pattern"] for n in notes] == [
        r["feature"] for r in plan["recommended_patterns"]["patterns"]
    ]
    assert all(n["how_to_test"] and "falsified" not in n["how_to_test"] for n in notes)
    assert all("matched losers' median" in n["falsified_if"] for n in notes)


def test_m25_d3_reproducible_byte_identical_and_fast(tmp_path: Path) -> None:
    """D3: same brief version, report and inputs -> byte-identical plan; <= 2 min."""
    b = pf.example_brief()
    rep = tmp_path / "reports" / b.brief_id / "v1" / "report-2026-09-29.json"
    rep.parent.mkdir(parents=True)
    rep.write_text(json.dumps(pf.report(b)), encoding="utf-8")
    verdict = tmp_path / "verdict.md"
    verdict.write_text("Synthetic verdict text.\n", encoding="utf-8")
    t = time.monotonic()
    a = run_plan(b, tmp_path, append_path=verdict, day=date(2026, 9, 30))
    first = {k: Path(v).read_bytes() for k, v in a.paths.items()}
    b2 = run_plan(b, tmp_path, append_path=verdict, day=date(2026, 9, 30))
    assert time.monotonic() - t < 120
    assert a.exit_code == 0 and a.paths == b2.paths
    assert first == {k: Path(v).read_bytes() for k, v in b2.paths.items()}
    md = first["md"].decode()
    assert "## Fast-path verdict (ADR-088.6)\n\nSynthetic verdict text." in md
    assert Path(a.paths["md"]).name.startswith("plan-2026-09-30-")
    assert oct(Path(a.paths["md"]).stat().st_mode & 0o777) == "0o600"
    # other inputs -> another plan version, both kept
    c = run_plan(b, tmp_path, day=date(2026, 9, 30))
    assert c.summary["plan_version"] != a.summary["plan_version"] and c.paths != a.paths


def test_m25_d3_refuses_a_report_of_another_brief_version(tmp_path: Path) -> None:
    b = pf.example_brief()
    rep = pf.report(b)
    rep["provenance"]["brief_version"] = 2
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rep))
    out = run_plan(b, tmp_path, report_path=p, day=date(2026, 9, 30))
    assert out.exit_code == 1 and "another brief" in out.message
    assert run_plan(b, tmp_path, day=date(2026, 9, 30)).exit_code == 1  # no report at all
    rep["provenance"]["brief_version"] = 1
    rep["provenance"]["brief_hash"] = "f" * 64
    p.write_text(json.dumps(rep))
    assert run_plan(b, tmp_path, report_path=p, day=date(2026, 9, 30)).exit_code == 1


def test_m25_f11_lock_predictions_hash_and_timestamp(tmp_path: Path) -> None:
    """PRD F11 R11.2: predictions locked (hashed and timestamped), write-once, private."""
    b = pf.example_brief()
    rep = tmp_path / "reports" / b.brief_id / "v1" / "report-2026-09-29.json"
    rep.parent.mkdir(parents=True)
    rep.write_text(json.dumps(pf.report(b)))
    out = run_plan(b, tmp_path, day=date(2026, 9, 30))
    now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    lk = lock_predictions(Path(out.paths["json"]), now=now)
    assert lk.status == "locked" and lk.locked_at == now.isoformat()
    assert lk.predictions_sha256 and len(lk.predictions_sha256) == 64
    rec = json.loads(Path(str(lk.path)).read_text())
    assert rec["predictions_sha256"] == lk.predictions_sha256 and rec["predictions"]
    again = lock_predictions(Path(out.paths["json"]), now=datetime(2026, 10, 2, tzinfo=UTC))
    assert again.status == "already_locked" and again.locked_at == lk.locked_at
    # a thin plan has nothing to lock
    thin_dir = tmp_path / "thin"
    rep2 = thin_dir / "reports" / b.brief_id / "v1" / "report-2026-09-29.json"
    rep2.parent.mkdir(parents=True)
    rep2.write_text(json.dumps(pf.report(b, 2)))
    thin = run_plan(b, thin_dir, day=date(2026, 9, 30))
    assert lock_predictions(Path(thin.paths["json"]), now=now).status == "refused"


def test_m25_inputs_yaml_validated(tmp_path: Path) -> None:
    p = tmp_path / "in.yaml"
    p.write_text("available_assets: [demo_media, quick_start_section]\n"
                 "time_budget_hours_per_week: 6\nlaunch_window: {start: 2026-11-03}\n")  # fmt: skip
    i = load_inputs(p)
    assert i.available_assets == ["demo_media", "quick_start_section"] and not i.missing()
    p.write_text("available_assets: [a_rocket]\n")
    with pytest.raises(ValueError):
        load_inputs(p)
    p.write_text("unknown_key: 1\n")
    with pytest.raises(ValueError):
        load_inputs(p)


def test_m25_cli_plan_and_lock_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pigtail plan <id>` then `pigtail plan lock <id>` on the stored example brief, without a
    database (context not loaded; nothing paid, no network)."""
    from pigtail.briefs.cli import _store
    from pigtail.cli import main

    d = tmp_path / "data"
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(d))
    monkeypatch.setenv("PIGTAIL_BRIEFS_DIR", str(d / "briefs"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["brief", "new", "--from", str(pf.EXAMPLE)]) == 0
    b = _store().get("example-config-linter").brief
    rep = d / "reports" / b.brief_id / "v1" / "report-2026-09-29.json"
    rep.parent.mkdir(parents=True)
    rep.write_text(json.dumps(pf.report(b)))
    inputs = tmp_path / "in.yaml"
    inputs.write_text("time_budget_hours_per_week: 4\n")
    capsys.readouterr()
    assert main(["plan", b.brief_id, "--inputs", str(inputs), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "written" and out["summary"]["recommended"] >= 3
    assert out["summary"]["context_loaded"] is False
    assert out["summary"]["inputs_missing"] == ["available_assets", "launch_window"]
    assert main(["plan", "lock", b.brief_id, "--json"]) == 0
    lk = json.loads(capsys.readouterr().out)
    assert lk["status"] == "locked" and Path(lk["path"]).is_file()
    assert main(["plan", "nope-brief"]) == 1


def test_m25_cli_parses_plan_and_lock() -> None:
    from pigtail.cli import build_parser

    a = build_parser().parse_args(["plan", "b1", "--report", "latest", "--json"])
    assert a.brief_id == "b1" and a.target is None and a.report == "latest"
    a = build_parser().parse_args(["plan", "lock", "b1"])
    assert a.brief_id == "lock" and a.target == "b1"


# --- M25 fixes after the first run on a real brief (ADR-091 addendum 1) ------------------------
def test_m25_fix1_plain_language_names_with_ids() -> None:
    """Headings show the registry's plain-language name, a one-sentence definition and the id."""
    plan = _plan()
    md = plan_markdown(plan)
    assert "Active multi-channel first-party promotion [pattern.MC-07]" in md
    assert "Quick-start section [readme.quick_start_section]" in md
    mc = next(r for r in plan["recommended_patterns"]["patterns"]
              if r["feature"] == "pattern.MC-07")  # fmt: skip
    assert mc["definition"].startswith("The maker actively promotes")
    assert f"_{mc['definition']}_" in md


def test_m25_fix1_registry_mirror_matches_schema() -> None:
    from pigtail.forensics.feature_names import PATTERNS

    reg = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "mechanisms"
                      / "candidates-v0.json").read_text())  # fmt: skip
    cards = {c["id"]: (c["name"], c["description"]) for c in reg["cards"]}
    assert cards == PATTERNS


def test_m25_fix2_threshold_and_common_practice() -> None:
    """rank-v2: d >= 0.15, winner-only > loser-only discordant pairs, >= 3 supporting cases;
    a practice both sides mostly do is common practice with its n, never recommended."""
    from pigtail.forensics.plan import MIN_D

    plan = _plan()
    rp = plan["recommended_patterns"]
    assert MIN_D == 0.15
    for r in rp["patterns"]:
        assert r["d"] >= MIN_D and r["discordant_margin"] > 0
    common = {x["feature"]: x for x in rp["common_practice"]}
    # screenshots 9/12 vs 8/12 (d = 0.08); Show HN 12/12 vs 12/12 on view A but it separates
    # on view B, so it is "launch size only", not common practice
    assert set(common) == {"asset.screenshots"}
    assert [x["feature"] for x in rp["launch_size_only"]] == ["launch.show_hn"]
    assert common["asset.screenshots"]["winners"]["n_present"] == 9
    rec = {r["feature"] for r in rp["patterns"]}
    assert not rec & set(common)
    md = plan_markdown(plan)
    assert "### Common practice (winners and losers both do it; table stakes, not a " in md
    assert "Screenshots in the README [asset.screenshots] (view A): 9/12 winners vs 8/12" in md


def test_m25_fix3_reliability_label_from_the_report() -> None:
    plan = _plan()
    mc = next(r for r in plan["recommended_patterns"]["patterns"]
              if r["feature"] == "pattern.MC-07")  # fmt: skip
    assert "full run, n = 24" in mc["reliability_labels"]
    assert not any(x.startswith("pilot, n =") for x in mc["reliability_labels"])
    assert "pilot, n =" not in plan_markdown(plan)


def test_m25_fix4_evidence_found_for_flagged_case_names_or_explained() -> None:
    plan = _plan()
    inst = next(r for r in plan["recommended_patterns"]["patterns"]
                if r["feature"] == "asset.install_one_liner")  # fmt: skip
    assert "org-s/w10 [definition-sensitive]" in inst["cited_cases"]
    assert pf._ev(10, "readme") in inst["evidence_ids"]
    mc = next(r for r in plan["recommended_patterns"]["patterns"]
              if r["feature"] == "pattern.MC-07")  # fmt: skip
    assert mc["evidence_ids"] == [] and "carry no evidence id" in mc["evidence_note"]
    assert "not loaded (no database context)" not in plan_markdown(plan)
    b = pf.example_brief()
    no_ctx = build_plan(b, pf.report(b), report_hash=H)
    assert all(r["evidence_note"] == "not loaded (no database context)"
               for r in no_ctx["recommended_patterns"]["patterns"])  # fmt: skip


def test_m25_fix5_calendar_l_is_first_scheduled_channel_and_table_stakes_undated() -> None:
    plan = _plan()
    cal = plan["calendar"]
    ph = next(e for e in cal["entries"] if e["action"].startswith("Product Hunt"))
    assert ph["when"] == "L" and ph["phase"] == "launch day"  # shifted from +1.71 d to L
    assert not any("first public launch post" in e["action"] for e in cal["entries"])
    assert not any(e["action"].startswith("Hacker News") for e in cal["entries"])
    assert cal["table_stakes"][0]["note"] == "table stakes: do it; the evidence doesn't say when"
    assert "table stakes: do it; the evidence doesn't say when" in plan_markdown(plan)


def test_m25_fix6_order_by_margin_then_d_language_weakens_below_holds() -> None:
    b = pf.example_brief()
    rep = pf.report(b)
    for f in rep["patterns"]["views"]["A"]["features"]:
        if f["feature"] == "asset.install_one_liner":
            f["language_check"]["result"] = "weakens"
    plan = build_plan(b, rep, report_hash=H, ctx=pf.context())
    rows = plan["recommended_patterns"]["patterns"]
    order = [r["feature"] for r in rows]
    assert order == ["readme.quick_start_section", "pattern.MC-07", "launch.product_hunt",
                     "asset.install_one_liner"]  # fmt: skip
    margins = [r["discordant_margin"] for r in rows[:-1]]
    assert margins == sorted(margins, reverse=True)
    assert any("weakens" in x for x in rows[-1]["reliability_labels"])


# --- rank-v3 and the verifier round 2 fixes (ADR-091 addendum 2) -------------------------------
def test_m25_v3_a_basis_direction_all_views_and_launch_size_only() -> None:
    """Recommend only on view A; the same direction in every other view that can assess it;
    every view's n and d on each recommendation; B-only passes are "launch size only"."""
    rp = _plan()["recommended_patterns"]
    assert {r["view"] for r in rp["patterns"]} == {"A"}
    assert rp["rule"] == "rank-v3"
    why = {x["feature"]: x["why"] for x in rp["not_recommended"]}
    assert why["asset.demo_media"].startswith("direction not the same in view B")  # reverses
    for r in rp["patterns"]:
        assert [v["view"] for v in r["views"]] == ["A", "B"]
        assert all("d" in v and "winners" in v and "matched_losers" in v for v in r["views"])
    lo = rp["launch_size_only"][0]
    assert lo["label"] == "launch size only (view B), not a recommendation"
    md = plan_markdown(_plan())
    assert "### Launch size only (view B), not a recommendation" in md
    assert "All views: view A (headline 1: basis)" in md


def test_m25_v3_a_b_undeclared_is_never_a_basis() -> None:
    b = pf.example_brief()
    rep = pf.report(b)
    views = rep["patterns"]["views"]
    views["B-undeclared"] = json.loads(json.dumps(views["B"]))
    views["B-undeclared"]["view"] = "launch_undeclared"
    del views["A"]  # nothing on the headline view: nothing is recommended
    plan = build_plan(b, rep, report_hash=H, ctx=pf.context())
    assert plan["recommended_patterns"]["patterns"] == []


def test_m25_v3_a_multiplicity_permutation_and_banner() -> None:
    """Within-pair label permutation (2,000 draws, fixed seed) of the number of view-A
    recommendations; the banner when p >= 0.05 or not computable."""
    from pigtail.forensics.plan import CHANCE_BANNER, PERM_DRAWS, PERM_SEED

    b = pf.example_brief()

    def rp(mode: str, ctx: bool = True):  # type: ignore[no-untyped-def]
        c = pf.context(mode=mode) if ctx else None
        return build_plan(b, pf.report(b, mode=mode), report_hash=H, ctx=c)["recommended_patterns"]

    indep = rp("indep")
    m = indep["multiplicity"]
    assert m["draws"] == PERM_DRAWS == 2000 and m["seed"] == PERM_SEED
    assert m["observed"] == 4 and m["p"] < 0.05 and indep["banner"] is None
    assert m == rp("indep")["multiplicity"]  # deterministic
    weak = rp("weak")
    assert weak["multiplicity"]["p"] >= 0.05 and weak["banner"] == CHANCE_BANNER
    assert weak["multiplicity"]["expected_false"] > 0
    blind = rp("indep", ctx=False)
    assert blind["multiplicity"]["p"] is None and blind["banner"] == CHANCE_BANNER
    md = plan_markdown(build_plan(b, pf.report(b, mode="weak"), report_hash=H,
                                  ctx=pf.context(mode="weak")))  # fmt: skip
    sec2 = md[md.index("## 2. Recommended patterns") :]
    assert sec2.index("not distinguishable from chance") < sec2.index("permutation p = ")
    assert sec2.index("permutation p = ") < sec2.index("### 1.")
    assert "expected false recommendations under no effect" in sec2


def test_m25_v3_b_version_hashes_rules_and_context_and_never_overwrites(tmp_path: Path) -> None:
    from pigtail.forensics.plan import plan_version, rules

    r = rules()
    assert r["rank_rule"] == "rank-v3" and r["min_d"] == 0.15 and r["feature_registry"]
    b = pf.example_brief()
    v0 = plan_version("a" * 64, "b" * 64, PlanInputs(), None, pf.context().digest())
    v1 = plan_version("a" * 64, "b" * 64, PlanInputs(), None, pf.context(n=11).digest())
    assert v0 != v1  # the database context enters the version
    rep = tmp_path / "copy" / "report-2026-09-29.json"
    rep.parent.mkdir()
    rep.write_text(json.dumps(pf.report(b)))
    out = run_plan(b, tmp_path / "data", report_path=rep, day=date(2026, 9, 30))
    assert Path(out.paths["md"]).parent == rep.parent  # --report decides the directory
    Path(out.paths["md"]).write_text("edited by hand\n")
    again = run_plan(b, tmp_path / "data", report_path=rep, day=date(2026, 9, 30))
    assert again.paths["md"] != out.paths["md"] and again.paths["md"].endswith("-r2.md")
    assert Path(out.paths["md"]).read_text() == "edited by hand\n"  # never overwritten
    third = run_plan(b, tmp_path / "data", report_path=rep, day=date(2026, 9, 30))
    assert third.paths == again.paths  # identical bytes: same file, nothing rewritten
    lk = lock_predictions(Path(again.paths["json"]), now=datetime(2026, 10, 1, tzinfo=UTC))
    rec = json.loads(Path(str(lk.path)).read_text())
    assert rec["rules"]["rank_rule"] == "rank-v3" and len(rec["plan_version"]) == 64


def test_m25_v3_c_basis_reliability_alpha_or_real_precision() -> None:
    from pigtail.forensics.plan import NOT_MEASURED

    b = pf.example_brief()
    rep = pf.report(b)
    recs = {r["feature"]: r for r in build_plan(b, rep, report_hash=H, ctx=pf.context())
            ["recommended_patterns"]["patterns"]}  # fmt: skip
    assert recs["pattern.MC-07"]["basis_reliability"]["text"].startswith("alpha 0.81")
    assert recs["asset.install_one_liner"]["basis_reliability"]["text"] == NOT_MEASURED
    rep["rule_precision"] = {"asset.install_one_liner": {"precision": 0.72},
                             "readme.quick_start_section": 0.95}  # fmt: skip
    recs = {r["feature"]: r for r in build_plan(b, rep, report_hash=H, ctx=pf.context())
            ["recommended_patterns"]["patterns"]}  # fmt: skip
    assert recs["asset.install_one_liner"]["basis_reliability"]["text"].startswith("borderline")
    assert (
        "0.95 real-data precision"
        in recs["readme.quick_start_section"]["basis_reliability"]["text"]
    )
    assert "Basis reliability: borderline (0.72 real-data precision)" in plan_markdown(
        build_plan(b, rep, report_hash=H, ctx=pf.context())
    )


def test_m25_v3_d_calendar_confirmed_events_and_timing_evidence() -> None:
    from pigtail.forensics.plan import NO_TIMING, hour_window

    assert hour_window([23, 1, 0]) == {"start": 23, "end": 1, "span_h": 2}
    assert hour_window([1, 20])["span_h"] == 5  # 20 -> 1 across midnight
    b = pf.example_brief()
    # 4 winners with Product Hunt: dated (>= 3) but no timing window (< 5 winners)
    plan = build_plan(b, pf.report(b, ph_winners=4), report_hash=H,
                      ctx=pf.context(ph_winners=4))  # fmt: skip
    ph = next(e for e in plan["calendar"]["entries"] if e["action"].startswith("Product Hunt"))
    assert ph["timing_window"].startswith(NO_TIMING)
    # unconfirmed events never schedule a channel
    rep = pf.report(b)
    for n in rep["narratives"]:
        for e in n["facts"]["launch_events"]:
            if e["where"] == "Product Hunt":
                e["confirmed"] = False
    plan = build_plan(b, rep, report_hash=H, ctx=pf.context())
    assert plan["calendar"]["channels_scheduled"] == []
    stakes = {x["where"]: x["note"] for x in plan["calendar"]["table_stakes"]}
    assert NO_TIMING in stakes["Product Hunt"]  # recommended, but undated


def test_m25_v3_predictions_band_p_per_metric_and_outcome_notes() -> None:
    plan = _plan()
    pr = plan["predictions"]
    assert [i["metric"] for i in pr["items"]] == ["att.stars_follow@3-30"]  # view A only
    assert [x["metric"] for x in pr["no_contrast"]] == ["att.stars_launch@0-2"]  # 26.5 < 36.5
    for i in pr["items"]:
        assert i["expected_band"]["low"] < i["expected_band"]["high"]
        assert i["view"] == "A" and "view A cases with" in i["probability_basis"]
    notes = plan["adaptation_notes"]["notes"]
    assert all(n["outcome_metric"] == "att.stars_follow@3-30" for n in notes)
    assert all("att.stars_follow@3-30" in n["falsified_if"] for n in notes)
    b = pf.example_brief()
    blind = build_plan(b, pf.report(b), report_hash=H)["predictions"]["items"][0]
    assert blind["probability"] is None and "no database context" in blind["probability_basis"]
