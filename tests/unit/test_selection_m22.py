"""M22 selection core on synthetic inputs (no database): outcome sort and qualification (R4.8,
R18.8; outcome-model §3, §5), deterministic winners and matched losers (R4.3, ADR-054.1),
widening and the too-few-winners fallback logged step by step (R4.10, ADR-053.2), the exemplar
panel (ADR-057.1), reference cases (R4.11), balance diagnostics (§9.2) and the sensitivity check
(R4.9, outcome-model §8)."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import replace

import pytest

from pigtail.briefs.selection import (
    LSM_CALIPER_SD,
    MIN_POPULATION,
    Context,
    Definition,
    SelectionError,
    Value,
    jaccard,
    percentiles,
    select,
    smd_numeric,
)
from tests.selection_fake import brief, case, obs, population


def run(cases, b=None, **ctx_over):
    b = b or brief()
    ctx = replace(Context.from_brief(b), **ctx_over)
    return select(cases, ctx, Definition.from_brief(b))


def roles(sel) -> dict[str, str]:
    return {c["candidate_ref"]: c["role"] for c in sel.cases}


# --- determinism (R4.8) -----------------------------------------------------------------------
def test_selection_is_deterministic_and_order_independent():
    cases = population()
    a, b = run(cases), run(list(cases))
    assert a.to_dict() == b.to_dict() and a.result_hash == b.result_hash
    for seed in (1, 2, 3):
        shuffled = list(cases)
        random.Random(seed).shuffle(shuffled)
        c = run(shuffled)
        assert c.result_hash == a.result_hash and c.inputs_hash == a.inputs_hash
        assert c.balance == a.balance and c.sensitivity == a.sensitivity
        assert [x["candidate_ref"] for x in c.cases] == [x["candidate_ref"] for x in a.cases]
    # a different input (one value changed) gives a different inputs hash
    changed = [*cases[:-1], replace(cases[-1], values={**cases[-1].values, "att.stars@30": obs(1)})]
    assert run(changed).inputs_hash != a.inputs_hash
    with pytest.raises(SelectionError, match="twice"):
        run([*cases, cases[0]])


def test_ties_are_broken_by_the_seeded_hash_not_input_order():
    """Equal percentiles: rank order follows sha256(brief_id:version:ref) (outcome-model §5.4)."""
    cases = [case(i, stars=100 if i < 6 else 1 + i, community=9) for i in range(30)]
    b = brief(minimums={}, primary_threshold="none")
    sel = run(cases, b)
    ctx = Context.from_brief(b)
    ranked = sorted((c for c in sel.cases if c["rank"] is not None), key=lambda c: c["rank"])
    top = [c["candidate_ref"] for c in ranked if c["score"] == ranked[0]["score"]]
    assert len(top) == 6 and top == sorted(top, key=ctx.tie)
    assert top != sorted(top)  # not alphabetical: the hash decides
    # reversed input order: the same ranking
    again = run(list(reversed(cases)), b)
    assert [c["rank"] for c in again.cases] == [c["rank"] for c in sel.cases]


# --- outcome sort and qualification (§3, §5.3, R18.8) ------------------------------------------
def test_mid_rank_percentiles_and_the_minimum_population():
    cases = [case(i, stars=float(i + 1)) for i in range(MIN_POPULATION)]
    pct, info = percentiles(cases, "att.stars@30")
    assert pct["gh:org-q/repo-00"] == pytest.approx(100 * 0.5 / 20)
    assert pct["gh:org-q/repo-19"] == pytest.approx(100 * 19.5 / 20)
    tied = [case(i, stars=5.0) for i in range(MIN_POPULATION)]
    assert set(percentiles(tied, "att.stars@30")[0].values()) == {50.0}  # ties share one
    small, info = percentiles(cases[:-1], "att.stars@30")
    assert set(small.values()) == {None} and info["small_population"] == ["all"]
    # unknown values are left out of n and counted
    mixed = [*cases, case(99, stars=None)]
    _, info = percentiles(mixed, "att.stars@30")
    assert info["n_by_group"] == {"all": 20} and info["statuses"]["unknown"] == 1


def test_unknown_and_pending_never_meet_a_threshold_and_are_not_losers():
    cases = population()
    top = max(range(len(cases)), key=lambda i: cases[i].values["att.stars@30"].value or 0)
    cases[top] = replace(
        cases[top],
        values={
            **cases[top].values,
            "comm.returning_external_contributors@90": Value("pending", reason="horizon"),
        },
    )
    sel = run(cases)
    r = next(c for c in sel.cases if c["candidate_ref"] == cases[top].ref)
    assert r["role"] == "undetermined"
    chk = {x["dimension"]: x for x in r["qualification"]["checks"]}
    assert chk["community"]["result"] == "undetermined"
    assert sel.summary["undetermined_by_dimension"]["community"] == 1


def test_zero_floor_fails_a_percentile_threshold():
    cases = [case(i, stars=0.0 if i < 25 else float(i)) for i in range(40)]
    sel = run(cases, brief(minimums={}, primary_threshold="at_least_p25"))
    zero = next(c for c in sel.cases if c["candidate_ref"] == "gh:org-q/repo-00")
    (chk,) = zero["qualification"]["checks"]
    assert chk["result"] == "fail" and chk["reason"] == "zero_floor"


def test_no_measurable_adoption_uses_community_and_is_flagged():
    """ADR-053.2: a comparable that isn't a package ranks on community, flagged."""
    cases = [case(i, stars=10 + i, downloads=100 + i, community=i) for i in range(30)]
    na = Value("not_applicable", reason="no_package")
    cases[29] = replace(cases[29], values={**cases[29].values, "adopt.downloads@90": na})
    b = brief(primary="adoption", primary_threshold="top_quartile", minimums={},
              fallbacks={"too_few_winners": {"min_winners": 1, "steps": []}})  # fmt: skip
    sel = run(cases, b)
    r = next(c for c in sel.cases if c["candidate_ref"] == cases[29].ref)
    assert r["role"] == "winner" and r["rank_basis"].startswith("comm.")
    assert "adoption_not_measurable_community_as_primary" in r["qualification"]["flags"]
    b2 = brief(primary="adoption", minimums={},
               fallbacks={"no_measurable_adoption": "fail_threshold",
                          "too_few_winners": {"min_winners": 1, "steps": []}})  # fmt: skip
    r2 = next(c for c in run(cases, b2).cases if c["candidate_ref"] == cases[29].ref)
    assert r2["role"] in ("matched_loser", "loser_pool_unmatched")


# --- matching (R4.3, ADR-054.1) ----------------------------------------------------------------
def test_losers_are_exactly_matched_and_inside_the_calipers():
    sel = run(population())
    by = {c["candidate_ref"]: c for c in sel.cases}
    sets: dict[int, dict[str, list[str]]] = {}
    for c in sel.cases:
        if c["pair"] and c["pair"]["panel"] == "field":
            g = sets.setdefault(c["pair"]["pair_id"], {"winner": [], "loser": []})
            g[c["pair"]["side"]].append(c["candidate_ref"])
    # one winner per matched set, one or more losers (later rounds give a winner a second one)
    assert sets and all(len(g["winner"]) == 1 and g["loser"] for g in sets.values())
    sd = sel.balance["sd"]["lsm"]
    links = 0
    for g in sets.values():
        (w_ref,) = g["winner"]
        assert by[w_ref]["pair"]["losers"] == len(g["loser"])
        for lo_ref in g["loser"]:
            links += 1
            w, lo = by[w_ref]["covariates"], by[lo_ref]["covariates"]
            assert w["launch_half_year"] == lo["launch_half_year"]
            assert w["audience_band"] == lo["audience_band"]
            assert abs(w["lsm"] - lo["lsm"]) <= LSM_CALIPER_SD * sd + 1e-9
            assert by[w_ref]["role"] == "winner" and by[lo_ref]["role"] == "matched_loser"
    assert sel.balance["exact_match"]["ok"] is True
    assert links == sel.balance["pairs"] == 20  # panel.losers
    # losers are used once
    losers = [r for g in sets.values() for r in g["loser"]]
    assert len(losers) == len(set(losers))


def test_a_winner_without_an_exact_match_stays_unmatched():
    cases = [case(i, stars=10 + i, half=0) for i in range(30)]
    cases.append(case(90, stars=5000, half=1))  # the top winner, alone in its half-year
    sel = run(cases, brief(minimums={}, primary_threshold="top_decile"))
    top = next(c for c in sel.cases if c["candidate_ref"] == "gh:org-q/repo-90")
    assert top["role"] == "winner" and top["pair"] is None
    assert sel.balance["unmatched_winners"] >= 1


def test_smd_and_the_headline_exclusion_rule():
    s = smd_numeric([1.0, 2.0, 3.0], [2.0, 3.0, 4.0])
    assert s["smd"] == pytest.approx(-1.0) and s["variance_ratio"] == pytest.approx(1.0)
    assert smd_numeric([1.0], [])["smd"] is None
    # same half-year and LSM; languages differ in every pair -> excluded from headline patterns
    cases = [case(i, stars=10 + i, lang="go" if i >= 20 else "rust") for i in range(30)]
    sel = run(cases, brief(minimums={}, primary_threshold="top_quartile"))
    field = [c["pair"] for c in sel.cases if c["pair"] and c["pair"]["side"] == "loser"]
    assert field
    for p in field:
        assert p["diffs"]["language"] in (0.0, 1.0)
        assert p["headline"] is (p["diffs"]["language"] == 0.0 and not p["excluded_on"])
        assert ("language" in p["excluded_on"]) is (p["diffs"]["language"] == 1.0)
    bal = sel.balance
    assert bal["headline_pairs"] + bal["headline_excluded"]["pairs"] == bal["pairs"]
    assert bal["after_matching"]["language"]["meets_target"] is False
    assert "language" in bal["covariates_missing_target"]
    # the balance numbers come from the matched pairs (SMD recomputed by hand for LSM)
    by = {c["candidate_ref"]: c for c in sel.cases}
    wl = [by[c["candidate_ref"]]["covariates"]["lsm"] for c in sel.cases if c["role"] == "winner"
          and c["pair"]]  # fmt: skip
    ll = [c["covariates"]["lsm"] for c in sel.cases if c["role"] == "matched_loser"]
    exp = smd_numeric(wl, ll)["smd"]
    assert bal["after_matching"]["lsm"]["smd"] == exp


# --- fallbacks and widening (R4.10, ADR-053.2) --------------------------------------------------
def test_too_few_winners_fallback_is_logged_step_by_step():
    sel = run(population(), min_winners=14)
    steps = sel.summary["steps"]
    names = [s["step"] for s in steps]
    assert names == [
        "baseline",
        "widen_to_distance_1",
        "widen_to_distance_2",
        "relax_primary_to_top_third",
        "drop_community_minimum",
    ]
    assert [s.get("applied", True) for s in steps[1:3]] == [False, False]  # nothing to add
    relax, drop = steps[3], steps[4]
    assert relax["rule"] == "ADR-053.2" and "fewer than 14" in relax["reason"]
    assert relax["floors"]["attention"] == pytest.approx(200 / 3, abs=1e-5)
    assert "community" not in drop["floors"]
    assert steps[0]["rankable_qualifiers"] < relax["rankable_qualifiers"]
    assert relax["rankable_qualifiers"] <= drop["rankable_qualifiers"]
    assert sel.summary["final_definition"]["floors"] == drop["floors"]
    assert any("relaxed" in w for w in sel.summary["warnings"])
    # the fallback stops as soon as enough qualify
    assert [s["step"] for s in run(population(), min_winners=5).summary["steps"]][-1] != (
        "relax_primary_to_top_third"
    )


def test_widening_adds_the_next_distance_and_labels_it():
    core = [case(i, stars=float(i), distance=0) for i in range(12)]
    wide = [case(50 + i, stars=1000.0 + i, distance=1) for i in range(12)]
    far = [case(80 + i, stars=5000.0 + i, distance=2) for i in range(4)]
    sel = run([*core, *wide, *far], brief(minimums={}, primary_threshold="at_least_p25"))
    steps = sel.summary["steps"]
    assert steps[1]["step"] == "widen_to_distance_1" and steps[1]["added"] == 12
    assert sel.summary["core_field_only"] is False
    winners = [c for c in sel.cases if c["role"] == "winner"]
    assert {c["distance"] for c in winners} >= {1}
    # the example brief declares two widening steps; without them nothing widens
    nb = brief(minimums={}, primary_threshold="at_least_p25")
    nb = nb.model_copy(update={"field": nb.field.model_copy(update={"widening_steps": []})})
    sel2 = run([*core, *wide, *far], nb)
    assert sel2.summary["steps"][1] == {
        "step": "widen",
        "applied": False,
        "rule": "R4.10",
        "reason": "the brief declares no widening steps",
    }
    assert {c["role"] for c in sel2.cases if c["distance"] > 0} == {"outside_widening"}


# --- exemplars (ADR-057.1) and reference cases (R4.11) -----------------------------------------
def test_exemplar_panel_gets_losers_matched_on_launch_type_period_and_audience():
    cases = population()
    ex0 = case(0, stars=None, panel="exemplar", ref="gh:org-e/exemplar-0", named_index=0,
               half=1, lang="haskell", launch_type="show_hn", distance=2)  # fmt: skip
    ex1 = case(1, stars=None, panel="exemplar", ref="gh:org-e/exemplar-1", named_index=1,
               anchor=False)  # fmt: skip
    sel = run([*cases, ex0, ex1], losers_per_exemplar=2)
    by = {c["candidate_ref"]: c for c in sel.cases}
    assert by[ex0.ref]["role"] == by[ex1.ref]["role"] == "exemplar"
    ex_losers = [c for c in sel.cases if c["role"] == "exemplar_matched_loser"]
    assert len(ex_losers) == 2
    ex_pair = by[ex0.ref]["pair"]["pair_id"]
    for c in ex_losers:
        assert c["pair"]["panel"] == "exemplar" and c["pair"]["side"] == "loser"
        cov = c["covariates"]
        assert cov["launch_type"] == "show_hn" and cov["launch_half_year"] == "2026H1"
        assert cov["audience_band"] == "unknown"
        assert cov["language"] != "haskell"  # never matched on field or language
        assert c["qualification"]["outcome"] == "fails"
    assert ex_pair in {c["pair"]["pair_id"] for c in ex_losers}
    log = sel.summary["exemplars"]["log"]
    assert log == [{"named_index": 0, "matched": 2}, {"named_index": 1, "matched": 0,
                                                       "reason": "no_anchor"}]  # fmt: skip
    # exemplar losers are neither field winners nor field matched losers
    assert not {c["candidate_ref"] for c in ex_losers} & {
        c["candidate_ref"] for c in sel.cases if c["role"] in ("winner", "matched_loser")
    }
    assert run([*cases, ex0], losers_per_exemplar=1).summary["exemplars"]["pairs"] == 1


def test_reference_cases_are_always_studied_whatever_their_outcome():
    cases = population()
    ref_low = case(70, stars=0, panel="reference", ref="gh:org-r/ref-low", named_index=0)
    ref_none = case(71, stars=None, panel="reference", ref="gh:org-r/ref-none", named_index=1,
                    anchor=False)  # fmt: skip
    sel = run([*cases, ref_low, ref_none])
    by = {c["candidate_ref"]: c for c in sel.cases}
    assert by[ref_low.ref]["is_reference"] and by[ref_none.ref]["is_reference"]
    assert by[ref_none.ref]["role"] == "no_anchor"
    assert by[ref_low.ref]["qualification"]["outcome"] == "fails"
    assert sel.summary["reference_cases"]["n"] == 2


# --- sensitivity (R4.9, outcome-model §8) -----------------------------------------------------
def test_sensitivity_overlaps_statuses_and_flags():
    sel = run(population())
    sens = sel.sensitivity
    alts = {a["key"]: a for a in sens["alternatives"]}
    assert alts["weights"]["ran"] is False  # the brief uses no weights
    assert alts["primary_swap:adoption"]["ran"] is False  # no observed downloads at all
    assert {"band_shift:attention:looser", "band_shift:attention:tighter",
            "band_shift:community:looser", "band_shift:all:tighter",
            "primary_swap:community", "exclude_anomaly_flagged"} <= set(alts)  # fmt: skip
    base = {c["candidate_ref"] for c in sel.cases if c["role"] == "winner"}
    base_losers = {c["candidate_ref"] for c in sel.cases if c["role"] == "matched_loser"}
    ran = [a for a in sens["alternatives"] if a.get("ran")]
    for a in ran:
        wins = {
            c["candidate_ref"]
            for c in sel.cases
            if c["sensitivity"] and c["sensitivity"]["status"].get(a["key"]) == "winner"
        }
        # statuses are recorded for baseline winners and matched losers only
        assert wins <= base | base_losers
        assert len(wins & base) == len(base) - a["left_baseline"]
        # a matched loser that becomes a winner is definition-sensitive too
        for r in wins & base_losers:
            (c,) = [x for x in sel.cases if x["candidate_ref"] == r]
            assert a["key"] in c["sensitivity"]["changed_by"]
    js = [a["jaccard"] for a in ran]
    assert sens["min_jaccard"] == min(js)
    assert sens["mean_jaccard"] == pytest.approx(statistics.fmean(js), abs=1e-6)
    # a tighter band loses winners: they are definition-sensitive, with the alternative named
    tight = alts["band_shift:attention:tighter"]
    assert tight["left_baseline"] > 0
    flagged = [c for c in sel.cases if c["sensitivity"] and
               "band_shift:attention:tighter" in c["sensitivity"]["changed_by"]]  # fmt: skip
    assert len(flagged) >= tight["left_baseline"]
    assert all("definition_sensitive" in c["sensitivity"]["flags"] for c in flagged)
    # D: anomaly-flagged candidates excluded and the sort redone
    d = alts["exclude_anomaly_flagged"]
    assert d["excluded_anomaly_flagged"] == 2
    for c in sel.cases:
        if c["sensitivity"] and c["star_anomaly"]["flag"] == "true":
            assert c["sensitivity"]["status"]["exclude_anomaly_flagged"] == (
                "excluded_anomaly_flagged"
            )
            assert c["sensitivity"]["flags"] == ["excluded_anomaly_flagged"] or (
                "excluded_anomaly_flagged" in c["sensitivity"]["flags"]
            )
    assert jaccard(set(), set()) is None and jaccard({"a"}, {"a", "b"}) == 0.5


def test_sensitivity_weights_and_d_applicability():
    b = brief(primary="attention", primary_threshold="none", minimums={},
              weights={"attention": 0.7, "community": 0.3},
              fallbacks={"too_few_winners": {"min_winners": 1, "steps": []}})  # fmt: skip
    alts = {a["key"]: a for a in run(population(), b).sensitivity["alternatives"]}
    assert alts["weights:primary_only"]["ran"] and alts["weights:equal"]["ran"]
    assert alts["band_shift"] == {
        "key": "band_shift",
        "ran": False,
        "reason": "not applicable: no percentile threshold in the final definition",
    }
    # no star metric in the definition: D is not applicable
    cases = [case(i, stars=10 + i, community=i, downloads=100 + i) for i in range(40)]
    nb = brief(primary="adoption", minimums={"community": "at_least_p25"},
               fallbacks={"too_few_winners": {"min_winners": 1, "steps": []}})  # fmt: skip
    d = {a["key"]: a for a in run(cases, nb).sensitivity["alternatives"]}
    assert d["exclude_anomaly_flagged"]["ran"] is False
    assert "no star metric" in d["exclude_anomaly_flagged"]["reason"]
    # star metric used but nothing flagged
    clean = [replace(c, star_anomaly_flag="false") for c in population()]
    d2 = {a["key"]: a for a in run(clean).sensitivity["alternatives"]}
    assert d2["exclude_anomaly_flagged"] == {
        "key": "exclude_anomaly_flagged",
        "ran": False,
        "reason": "not applicable: no flagged candidates",
    }


def test_no_brief_text_and_no_other_repo_names_in_summary_balance_or_sensitivity():
    """Selection-level records hold counts only (repo names live in the case rows)."""
    sel = run(population())
    blob = repr((sel.summary, sel.balance, sel.sensitivity, sel.params))
    assert "org-q/" not in blob and "configuration" not in blob
    assert "Example" not in blob
    for c in sel.cases:  # a case record names no other repo
        other = repr({k: v for k, v in c.items() if k != "candidate_ref"})
        assert "org-q/" not in other
    assert not math.isnan(sel.balance["after_matching"]["lsm"]["smd"])
