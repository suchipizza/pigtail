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
        assert r["evidence_ids"] and all(e.startswith("ev_") for e in r["evidence_ids"])
    md = plan_markdown(plan)
    assert "deterministic rule (no alpha)" in md and "Counterexamples:" in md
    # conditions of the project are never recommended (organisation owner is in every case)
    assert "amplifier.organization" not in {r["feature"] for r in rp["patterns"]}


def test_m25_d3_2_ranking_and_channels_avoided() -> None:
    plan = _plan()
    order = [r["feature"] for r in plan["recommended_patterns"]["patterns"]]
    assert order.index("asset.demo_media") < order.index("launch.product_hunt")  # d 0.75 > 0.67
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
        {"available_assets": ["demo_media"], "time_budget_hours_per_week": 5,
         "launch_window": {"start": "2026-11-03"}}
    )  # fmt: skip
    plan = _plan(inputs=inputs)
    cal = plan["calendar"]
    assert cal["status"] == "ok" and cal["launch_day"] == "2026-11-03"
    assert cal["channels_scheduled"] == ["Product Hunt"]  # Show HN: d = 0, reference only
    assert [x["where"] for x in cal["channels_reference_only"]] == ["Hacker News (Show HN)"]
    hn = next(c for c in plan["launch_timing"]["channels"] if c["kind"] == "show_hn")
    assert hn["winners"] == 10 and hn["median_offset_days"] == 0 and hn["hours_utc"]["min"] == 15
    assert hn["losers"] == 10  # the loser contrast is shown, not hidden
    whens = [e["when"] for e in cal["entries"]]
    assert "L (2026-11-03)" in whens and any(w.startswith("L-28 (2026-10-06)") for w in whens)
    assert cal["pre_launch_hours"] == 20.0
    gaps = {i["asset"]: i["status"] for i in plan["readiness_gaps"]["items"]}
    assert gaps["demo_media"] == "ready" and gaps["install_one_liner"].startswith("gap")
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
    ac = _plan()["asset_checklist"]
    demo = next(i for i in ac["items"] if i["asset"] == "demo_media")
    assert demo["examples"] and all(e["evidence_ids"] for e in demo["examples"])
    assert demo["examples"][0]["excerpt"] == "![demo](docs/demo.gif)"
    assert "org-s/w1" not in {e["case"] for e in demo["examples"]}  # it has no demo


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
