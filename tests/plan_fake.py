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
VIEWS = {"A": "follow_through", "B": "launch"}


def example_brief(**channels: Any) -> Brief:
    b = load_brief_text(EXAMPLE.read_text(encoding="utf-8"))
    upd: dict[str, Any] = {"version": 1}
    if channels:
        upd["channels"] = b.channels.model_copy(update=channels)
    return b.model_copy(update=upd)


def _ev(i: int, tag: str) -> str:
    return f"ev_{(i * 7919 + sum(map(ord, tag))) % 16**24:024x}"


def _facts(i: int, has: dict[str, bool]) -> dict[str, Any]:
    t0 = L0 + timedelta(days=7 * i)
    events: list[dict[str, Any]] = []
    if has["show_hn"]:
        events.append(
            {"kind": "show_hn", "where": "Hacker News (Show HN)", "ref": f"hn{i}", "launch": True,
             "at": t0.isoformat(), "title": "Show HN: a synthetic tool",
             "evidence_ids": [_ev(i, "hn")]}
        )  # fmt: skip
    if has["ph"]:
        events.append(
            {"kind": "product_hunt", "where": "Product Hunt", "ref": f"ph{i}", "launch": True,
             "at": (t0 + timedelta(days=2, hours=-7)).isoformat(), "title": None,
             "title_missing": "not stored (ADR-085)", "evidence_ids": [_ev(i, "ph")]}
        )  # fmt: skip

    def asset(key: str, excerpt: str) -> dict[str, Any]:
        on = has[key]
        return {"value": "present" if on else "absent", "evidence_id": _ev(i, "readme"),
                **({"excerpt": excerpt} if on else {})}  # fmt: skip

    return {
        "assets": {
            "assets": {
                "demo_media": asset("demo", "![demo](docs/demo.gif)"),
                "screenshots": asset("shots", "![screenshot](docs/ui.png)"),
                "install_one_liner": asset("install", "pip install synthetic-tool"),
            },
            "readme_structure": {"evidence_id": _ev(i, "readme"), "headings": [],
                                 "quick_start_section": has["quick"], "features_section": False},
        },
        "events": events,
        "amplifiers": [{"role": "organization", "value": "present",
                        "evidence_ids": [_ev(i, "meta")]}],
        "source_status": {"ph_launch": "complete", "gh_releases": "complete",
                          "bsky_maintainer_posts": "complete"},
        "trajectory": {"evidence_id": _ev(i, "stars"), "per_event": [], "bursts": []},
    }  # fmt: skip


def _has(view: str, i: int, win: bool, ph_winners: int, mode: str) -> dict[str, bool]:
    if mode == "indep":  # four modest contrasts on disjoint pairs (1-3, 4-6, 7-9, 10-12)
        own = ("demo", "shots", "install", "quick")[min((i - 1) // 3, 3)]
        return {"demo": win or own != "demo", "shots": win or own != "shots",
                "install": win or own != "install", "quick": win or own != "quick",
                "show_hn": True, "ph": False, "mc07": True}  # fmt: skip
    if (
        mode == "weak"
    ):  # one modest contrast (demo: winners 1-7, losers 1-4); everything else shared
        return {"demo": i <= (7 if win else 4), "shots": True, "install": True, "quick": True,
                "show_hn": True, "ph": False, "mc07": True}  # fmt: skip
    if view == "A":
        return {
            "demo": i != 1 if win else i in (2, 3),
            "shots": i <= 9 if win else 2 <= i <= 9,  # common practice (9/12 vs 8/12)
            "install": win,
            "quick": win,
            "show_hn": True,  # everyone: no contrast on view A
            "ph": win and i <= ph_winners,
            "mc07": win or i in (1, 2),
        }
    # view B (launch size): the demo reverses; Show HN separates (launch size only)
    return {
        "demo": i <= 2 if win else i >= 3,
        "shots": i <= 9 if win else 2 <= i <= 9,
        "install": win,
        "quick": win,
        "show_hn": win or i <= 3,
        "ph": win and i <= ph_winners,
        "mc07": win or i in (1, 2),
    }


def world(
    n: int = 12, *, ph_winners: int = 8, mode: str = "strong", views: str = "AB"
) -> tuple[list[PilotCase], dict[Any, Any], dict[str, dict[str, str]]]:
    """`n` headline pairs per view (module docstring of the tests). Returns (cases, selection
    rows, final codings)."""
    cases: list[PilotCase] = []
    rows: dict[Any, Any] = {}
    finals: dict[str, dict[str, str]] = {}
    for vl in views:
        view = VIEWS[vl]
        for i in range(1, n + 1):
            for role, ref in (("winner", f"w{i}"), ("matched_loser", f"l{i}")):
                win = role == "winner"
                has = _has(vl, i, win, ph_winners, mode)
                c = PilotCase(
                    case_key=f"{view}:gh:org-s/{ref}", view=view,
                    candidate_ref=f"gh:org-s/{ref}", repo_full_name=f"org-s/{ref}",
                    repo_id=None, repo_host_id=None, position=2 * i - (1 if win else 0),
                    role=role, pair_id=i, anchor={},
                    coding_id=f"cod_{i:06x}{int(win):02x}{ord(vl):08x}", facts=_facts(i, has),
                )  # fmt: skip
                cases.append(c)
                finals[c.case_key] = {"pattern.MC-07": "yes" if has["mc07"] else "no"}
                rows[(view, c.candidate_ref)] = {
                    "pair_panel": "field", "rank": i if win else None, "headline": not win,
                    "distance": 1, "role": role, "pair_id": i, "view": view,
                    "repo_full_name": c.repo_full_name,
                    "detail": {"values": {
                        "att.stars_follow@3-30": {"value": (900 if win else 40) + i},
                        # launch size: winners' median below the losers' (no contrast)
                        "att.stars_launch@0-2": {"value": (20 if win else 30) + i}},
                        "pair": {"distance": 0.1 * i, "same_language_group": True},
                        "covariates": {"language": "Python", "launch_half_year": "2026H1"}},
                }  # fmt: skip
    return cases, rows, finals


def report(brief: Brief, n: int = 12, **kw: Any) -> dict[str, Any]:
    cases, rows, finals = world(n, **kw)
    # MC-07's stored alpha row still says "pilot" while the report's reliability block says
    # "full run" (M25 fix 3)
    rel = [{"field": "pattern.MC-07", "statistic": "nominal", "alpha": 0.81, "assessed": True,
            "labels": ["LLM-coded, not human-validated", f"pilot, n = {2 * n}"]}]  # fmt: skip
    flags = {"follow_through:gh:org-s/w10": ["definition_sensitive"]}
    pats = run_patterns(cases, finals, rows, rel, flags=flags)
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
        "reliability": [{**rel[0], "labels": ["LLM-coded, not human-validated",
                                              f"full run, n = {2 * n}"]}],
        "evidence_index": {},
        "limitations": [],
    }  # fmt: skip


def context(n: int = 12, **kw: Any) -> PlanContext:
    """What `load_context` reads from the database, built from the same synthetic world."""
    from pigtail.forensics.patterns import feature_names
    from pigtail.forensics.plan import VIEW_LABEL, similarity_of

    cases, rows, _finals = world(n, **kw)
    ctx = PlanContext()
    for c in cases:
        vl = VIEW_LABEL[c.view]
        for f in feature_names():
            ids = feature_evidence(f, c.facts, {})
            if ids:
                ctx.evidence[(vl, c.repo_full_name, f)] = ids
        row = rows[(c.view, c.candidate_ref)]
        ctx.similarity[(vl, c.repo_full_name)] = similarity_of(row)
        ctx.values[(vl, c.repo_full_name)] = {
            m: float(r["value"]) for m, r in row["detail"]["values"].items()
        }
    for vl in ("A", "B"):
        ctx.pairs[vl] = [(f"org-s/w{i}", [f"org-s/l{i}"]) for i in range(1, n + 1)]
    return ctx
