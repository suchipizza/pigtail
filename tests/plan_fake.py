"""A synthetic D2 report for the M25 plan tests, built with the report's own functions
(`narrative_cases`, `fact_sheet`, `comparison_row`, `run_patterns`) over made-up cases, so its
structure is the one `pigtail report brief` writes. Every repo and text is invented (`org-s/...`).
The brief is the synthetic example brief (`docs/examples/brief-example.yaml`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pigtail.briefs.model import Brief, load_brief_text
from pigtail.forensics.patterns import run_patterns
from pigtail.forensics.plan import PlanContext, feature_evidence
from pigtail.forensics.report_brief import comparison_row, fact_sheet, narrative_cases
from pigtail.forensics.store import PilotCase

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "brief-example.yaml"
L0 = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # a Tuesday


def example_brief(**channels: Any) -> Brief:
    b = load_brief_text(EXAMPLE.read_text(encoding="utf-8"))
    upd: dict[str, Any] = {"version": 1}
    if channels:
        upd["channels"] = b.channels.model_copy(update=channels)
    return b.model_copy(update=upd)


def _ev(i: int, tag: str) -> str:
    return f"ev_{(i * 7919 + sum(map(ord, tag))) % 16**24:024x}"


def _facts(i: int, winner: bool, demo: bool, ph: bool, launch: bool = True) -> dict[str, Any]:
    t0 = L0 + timedelta(days=7 * i)
    events: list[dict[str, Any]] = []
    if launch:
        events.append(
            {"kind": "show_hn", "where": "Hacker News (Show HN)", "ref": f"hn{i}", "launch": True,
             "at": t0.isoformat(), "title": "Show HN: a synthetic tool",
             "evidence_ids": [_ev(i, "hn")]}
        )  # fmt: skip
    if ph:
        events.append(
            {"kind": "product_hunt", "where": "Product Hunt", "ref": f"ph{i}", "launch": True,
             "at": (t0 + timedelta(days=2, hours=-7)).isoformat(), "title": None,
             "title_missing": "not stored (ADR-085)", "evidence_ids": [_ev(i, "ph")]}
        )  # fmt: skip
    return {
        "assets": {
            "assets": {
                "demo_media": {"value": "present" if demo else "absent",
                               "evidence_id": _ev(i, "readme"),
                               **({"excerpt": "![demo](docs/demo.gif)"} if demo else {})},
                "install_one_liner": {"value": "present" if winner else "absent",
                                      "evidence_id": _ev(i, "readme"),
                                      **({"excerpt": "pip install synthetic-tool"}
                                         if winner else {})},
            },
            "readme_structure": {"evidence_id": _ev(i, "readme"), "headings": [],
                                 "quick_start_section": winner, "features_section": False},
        },
        "events": events,
        "amplifiers": [{"role": "organization", "value": "present",
                        "evidence_ids": [_ev(i, "meta")]}],
        "source_status": {"ph_launch": "complete", "gh_releases": "complete",
                          "bsky_maintainer_posts": "complete"},
        "trajectory": {"evidence_id": _ev(i, "stars"), "per_event": [], "bursts": []},
    }  # fmt: skip


def world(n: int = 12, *, ph_winners: int = 8) -> tuple[list[PilotCase], dict[Any, Any]]:
    """`n` view-A headline pairs: winners show a demo (all but pair 1), an install one-liner
    and a quick-start section, and (the first `ph_winners`) a Product Hunt launch 2 days after
    Show HN; losers show a demo in pairs 2 and 3 only and never launch on Product Hunt."""
    cases: list[PilotCase] = []
    rows: dict[Any, Any] = {}
    for i in range(1, n + 1):
        for role, ref in (("winner", f"w{i}"), ("matched_loser", f"l{i}")):
            win = role == "winner"
            demo = i != 1 if win else i in (2, 3)
            c = PilotCase(
                case_key=f"follow_through:gh:org-s/{ref}", view="follow_through",
                candidate_ref=f"gh:org-s/{ref}", repo_full_name=f"org-s/{ref}", repo_id=None,
                repo_host_id=None, position=2 * i - (1 if win else 0), role=role, pair_id=i,
                anchor={}, coding_id=f"cod_{i:08x}{int(win):08x}",
                facts=_facts(i, win, demo, win and i <= ph_winners),
            )  # fmt: skip
            cases.append(c)
            rows[("follow_through", c.candidate_ref)] = {
                "pair_panel": "field", "rank": i if win else None, "headline": not win,
                "distance": 1,
                "detail": {"values": {"att.stars_follow@3-30": {"value": (900 if win else 40) + i},
                                      "att.stars_launch@0-2": {"value": (300 if win else 20) + i}},
                           "pair": {"distance": 0.1 * i, "same_language_group": True},
                           "covariates": {"language": "Python", "launch_half_year": "2026H1"}},
            }  # fmt: skip
    return cases, rows


def report(brief: Brief, n: int = 12, **kw: Any) -> dict[str, Any]:
    cases, rows = world(n, **kw)
    pats = run_patterns(cases, {}, rows, [])
    chosen = narrative_cases(cases, rows)
    return {
        "provenance": {
            "report_version": "brief-report-v1", "brief_id": brief.brief_id,
            "brief_version": brief.version, "brief_hash": brief.content_hash(),
            "selection_id": "sel_00000000000000000000", "data_version": "dv-syn",
            "code_commit": "c0ffee1", "coding_run": "brun_syn", "report_run": "brun_rep",
            "date": "2026-09-29", "label": pats["outcome_label"],
            "coding_label": "LLM-coded, not human-validated",
        },
        "narratives": [{"coding_id": c.coding_id, "case": c.repo_full_name, "label": lab,
                        "narrative": {"sentences": []}, "facts": fact_sheet(c, lab)}
                       for c, lab in chosen],
        "comparison": [comparison_row(c, lab) for c, lab in chosen],
        "patterns": {k: v for k, v in pats.items() if k != "case_features"},
        "reliability": [],
        "evidence_index": {},
        "limitations": [],
    }  # fmt: skip


def context(n: int = 12, **kw: Any) -> PlanContext:
    from pigtail.forensics.patterns import feature_names
    from pigtail.forensics.plan import similarity_of

    cases, rows = world(n, **kw)
    ctx = PlanContext()
    for c in cases:
        for f in feature_names():
            ids = feature_evidence(f, c.facts, {})
            if ids:
                ctx.evidence[("A", c.repo_full_name, f)] = ids
        ctx.similarity[("A", c.repo_full_name)] = similarity_of(
            rows[("follow_through", c.candidate_ref)]
        )
    return ctx
