"""M24 pattern step on synthetic cases: n on both sides, contrast, counterexamples, minimum
evidence, language check (M24-L), transferability, absolute numbers, downloads as a secondary
exploratory outcome (ADR-089, ADR-084.3, ADR-050.3, ADR-057, ADR-090). Deterministic."""

from __future__ import annotations

from typing import Any

from pigtail.forensics.patterns import (
    INSUFFICIENT,
    case_features,
    language_rule,
    run_patterns,
    transfer_label,
    view_cases,
)
from pigtail.forensics.store import PilotCase


def _facts(demo: str, org: str = "present") -> dict[str, Any]:
    return {
        "assets": {"assets": {"demo_media": {"value": demo}}, "readme_structure": None},
        "events": [{"kind": "show_hn", "launch": True}],
        "amplifiers": [{"role": "organization", "value": org}],
        "source_status": {"ph_launch": "complete"},
    }


def _case(view: str, ref: str, role: str, pid: int, demo: str) -> PilotCase:
    return PilotCase(
        case_key=f"{view}:{ref}", view=view, candidate_ref=ref, repo_full_name=f"org/{ref}",
        repo_id=None, repo_host_id=None, position=pid, role=role, pair_id=pid, anchor={},
        facts=_facts(demo),
    )  # fmt: skip


def _world(n: int = 12, same_every: int = 2) -> tuple[list[PilotCase], dict[Any, Any]]:
    cases, rows = [], {}
    for i in range(1, n + 1):
        # winners: demo present except pair 1; losers: demo absent except pairs 2 and 3
        w = _case("follow_through", f"w{i}", "winner", i, "absent" if i == 1 else "present")
        lo = _case("follow_through", f"l{i}", "matched_loser", i,
                   "present" if i in (2, 3) else "absent")  # fmt: skip
        cases += [w, lo]
        rows[("follow_through", w.candidate_ref)] = {
            "pair_panel": "field", "rank": i,
            "detail": {"values": {"att.stars@30": {"value": 1000 + i}}},
        }  # fmt: skip
        rows[("follow_through", lo.candidate_ref)] = {
            "pair_panel": "field", "headline": True,
            "detail": {"pair": {"same_language_group": i % same_every == 0},
                       "values": {"att.stars@30": {"value": 10 + i}}},
        }  # fmt: skip
    return cases, rows


def test_m24_t4_counts_contrast_counterexamples() -> None:
    cases, rows = _world()
    out = run_patterns(cases, {}, rows, [])
    a = out["views"]["A"]
    assert a["winners"] == 12 and a["matched_losers"] == 12
    f = next(x for x in a["features"] if x["feature"] == "asset.demo_media")
    assert f["winners"]["n_present"] == 11 and f["winners"]["n_known"] == 12
    assert f["matched_losers"]["n_present"] == 2 and f["matched_losers"]["n_known"] == 12
    assert f["d"] == round(11 / 12 - 2 / 12, 4)
    assert f["counterexamples"]["winners_without"] == ["org/w1"]
    assert f["counterexamples"]["losers_with"] == ["org/l2", "org/l3"]
    assert f["pairs"] == {
        "winner_only": 9,
        "loser_only": 0,
        "both": 2,
        "neither": 1,
        "not_known": 0,
    }
    assert f["sufficient"] and INSUFFICIENT not in f["labels"]
    assert f["reliability"]["labels"] == ["deterministic rule (no alpha)"]
    # coded features without codings: all unknown -> insufficient evidence
    m = next(x for x in a["features"] if x["feature"] == "pattern.MC-01")
    assert INSUFFICIENT in m["labels"] and m["winners"]["n_known"] == 0
    # absolute numbers per class; attention-based without download data
    assert a["absolute_numbers"]["winners"]["att.stars@30"]["n"] == 12
    assert out["outcome_label"] == "attention-based"


def test_m24_l_language_check() -> None:
    cases, rows = _world(n=24, same_every=1)  # all pairs same group: the subset is the whole
    f = next(
        x
        for x in run_patterns(cases, {}, rows, [])["views"]["A"]["features"]
        if x["feature"] == "asset.demo_media"
    )
    assert f["language_check"]["result"] == "holds"
    cases, rows = _world(n=12, same_every=2)  # 6 same-group pairs: below the minimum
    f = next(
        x
        for x in run_patterns(cases, {}, rows, [])["views"]["A"]["features"]
        if x["feature"] == "asset.demo_media"
    )
    assert f["language_check"]["result"] == "not assessable"
    assert language_rule(0.5, 0.3, True) == "holds"
    assert language_rule(0.5, 0.2, True) == "weakens"
    assert language_rule(0.5, 0.0, True) == "weakens"
    assert language_rule(0.5, -0.1, True) == "reverses"
    assert language_rule(0.0, 0.3, True) == "no pattern"
    assert language_rule(None, 0.3, True) == "no pattern"


def test_m24_t4_features_and_transferability() -> None:
    feats = case_features(
        {"module_active.ai_hype": "yes", "pattern.MC-01": "unknown"}, _facts("present")
    )
    assert feats["module_active.ai_hype"] == "present" and feats["pattern.MC-01"] == "unknown"
    assert feats["launch.show_hn"] == "present" and feats["launch.launch_hn"] == "absent"
    assert feats["launch.product_hunt"] == "absent"  # searched completely
    assert feats["launch.bluesky_maintainer_post"] == "unknown"  # not searched completely
    ex = [_case("follow_through", f"x{i}", "exemplar", i, "present") for i in range(3)]
    rows = {("follow_through", c.candidate_ref): {"pair_panel": "exemplar"} for c in ex}
    vc = view_cases(ex, rows, "follow_through", panel="exemplar")
    fe = {c.case_key: {"asset.demo_media": "present", "module_active.ai_hype": "present"}
          for c in ex}  # fmt: skip
    assert vc.winners == []  # no loser: no exemplar pair
    from pigtail.forensics.patterns import ViewCases

    vc = ViewCases("follow_through", [c.case_key for c in ex], [], [], [])
    assert transfer_label("asset.demo_media", vc, fe)["label"] == "conditional"
    assert transfer_label("module_active.ai_hype", vc, fe)["label"] == "not transferable"
    fe2 = {k: {"asset.demo_media": "present"} for k in fe}
    assert transfer_label("asset.demo_media", vc, fe2)["label"] == "transferable"


def test_m24_downloads_secondary_exploratory() -> None:
    cases, rows = _world()
    sec = {"w1": {"adopt.npm_downloads_follow@3-30": {"value": 500, "exploratory": True}}}
    out = run_patterns(cases, {}, rows, [], sec)
    a = out["views"]["A"]
    assert a["secondary_exploratory"]["winners"]["adopt.npm_downloads_follow@3-30"]["n"] == 1
    assert out["downloads_present"] and "exploratory" in out["outcome_label"]
    # never a pattern feature
    assert not any("download" in f["feature"] for f in a["features"])


def test_m24_r1_downloads_cite_evidence() -> None:
    """[M24-T4] verifier round 1 fix 7: every download figure cites its evidence ids."""
    cases, rows = _world()
    rec = {
        "value": 500,
        "exploratory": True,
        "evidence": [{"evidence_id": "ev_npm1", "content_hash": "0" * 64}],
    }
    out = run_patterns(cases, {}, rows, [], {"w1": {"adopt.npm_downloads_follow@3-30": rec}})
    sec = out["views"]["A"]["secondary_exploratory"]
    assert sec["evidence_ids"]["winners"] == {"adopt.npm_downloads_follow@3-30": ["ev_npm1"]}


def test_m24_r1_unconfirmed_hn_match_is_not_a_launch() -> None:
    """[M24-T4] verifier round 1 fix 2: an unconfirmed HN title match is `unknown`, never
    `present`, in the launch patterns."""
    facts = _facts("present")
    facts["events"] = [{"kind": "show_hn", "launch": True, "counts": False}]
    feats = case_features({}, facts)
    assert feats["launch.show_hn"] == "unknown"
    facts["events"].append({"kind": "show_hn", "launch": True, "counts": True})
    assert case_features({}, facts)["launch.show_hn"] == "present"
