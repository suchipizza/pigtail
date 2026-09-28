"""M23 pilot, pure parts (synthetic data, no network, no database): Krippendorff's alpha against
textbook values, the coding frame and its schema, the two coder prompts, citation validation,
the case rule, the cost model and projection, evidence decay, project pages, private report
paths, the ops lines and the estimate's case model."""

from __future__ import annotations

import json
import random
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pigtail.forensics import alpha as ka
from pigtail.forensics.citations import check
from pigtail.forensics.cost import (
    PLANNING,
    CallTokens,
    CaseCostModel,
    StageTotals,
    measured,
    projection,
)
from pigtail.forensics.frame import (
    CODED_PATTERNS,
    MODULES,
    NOVELTY_KINDS,
    CaseCoding,
    CodedUnit,
    flatten,
    output_schema,
)
from pigtail.forensics.pilot import select_cases
from pigtail.forensics.prompts import (
    ADJUDICATOR,
    CODER_A,
    CODER_B,
    RenderedItem,
    case_input,
    fingerprints,
    option_order,
    order_a,
    order_b,
)

ROOT = Path(__file__).resolve().parents[2]
N = None


# --- Krippendorff's alpha ------------------------------------------------------------------
# Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability", the example with four
# observers, 12 units and missing values: nominal .743, ordinal .815, interval .849.
KRIPP_2011 = list(
    zip(
        [1, 2, 3, 3, 2, 1, 4, 1, 2, N, N, N],
        [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, N, 3],
        [N, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, N],
        [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, N],
        strict=True,
    )
)


def test_alpha_reproduces_krippendorff_2011_worked_example() -> None:
    assert round(ka.alpha(KRIPP_2011, "nominal") or 0, 3) == 0.743
    assert round(ka.alpha(KRIPP_2011, "ordinal", order=[1, 2, 3, 4, 5]) or 0, 3) == 0.815
    assert round(ka.alpha(KRIPP_2011, "interval") or 0, 3) == 0.849
    # unit 12 has one value only: not pairable
    assert len(ka.pairable(KRIPP_2011)) == 11


def test_alpha_textbook_two_coder_nominal_and_binary_examples() -> None:
    # binary, two observers, no missing data: alpha = 0.095
    a = ["0", "1", "0", "0", "0", "0", "0", "0", "1", "0"]
    b = ["1", "1", "1", "0", "0", "1", "0", "0", "0", "0"]
    assert round(ka.alpha(list(zip(a, b, strict=True))) or 0, 3) == 0.095
    # nominal, two observers: alpha = 0.692
    a = ["a", "a", "b", "b", "d", "c", "c", "c", "e", "d", "d", "a"]
    b = ["b", "a", "b", "b", "b", "c", "c", "c", "e", "d", "d", "d"]
    assert round(ka.alpha(list(zip(a, b, strict=True))) or 0, 3) == 0.692


def test_alpha_edge_cases() -> None:
    assert ka.alpha([("x", "x"), ("x", "x")]) is None  # one value: undefined, not 1.0
    assert ka.alpha([]) is None and ka.alpha([(1, None)]) is None
    assert ka.alpha([("a", "a"), ("b", "b")]) == 1.0
    assert ka.raw_agreement([("a", "a"), ("a", "b")]) == 0.5
    # ordinal order is respected: labels ranked by the given order, not alphabetically
    units = [("low", "medium"), ("high", "high"), ("medium", "medium"), ("low", "low")]
    order = ["low", "medium", "high"]
    a1 = ka.alpha(units, "ordinal", order)
    a2 = ka.alpha([(1, 2), (3, 3), (2, 2), (1, 1)], "ordinal", [1, 2, 3])
    assert a1 == pytest.approx(a2)
    with pytest.raises(ValueError):
        ka.alpha([("a", "b")], "ordinal", ["a"])


def test_alpha_bootstrap_is_deterministic_and_brackets_alpha() -> None:
    ci1 = ka.bootstrap_ci(KRIPP_2011, "interval", resamples=500, seed=7)
    ci2 = ka.bootstrap_ci(KRIPP_2011, "interval", resamples=500, seed=7)
    assert ci1 == ci2 and ci1.low is not None and ci1.high is not None
    assert ci1.low <= 0.849 <= ci1.high
    assert ka.bootstrap_ci([("x", "x")] * 5, resamples=50).dropped == 50


# --- the frame and its schema --------------------------------------------------------------
def test_frame_enums_mirror_codebook_json() -> None:
    cb = json.loads((ROOT / "schemas/codebook/v0.4.0.json").read_text())
    from typing import get_args

    from pigtail.forensics import frame

    assert set(get_args(frame.Category)) == set(cb["category_taxonomy"]["categories"])
    assert set(get_args(frame.UnknownReason)) == set(cb["coding_rules"]["unknown_reasons"])
    assert set(get_args(frame.Confidence)) == set(cb["coding_rules"]["coder_confidence"])
    assert set(MODULES) == set(cb["modules"])
    assert set(NOVELTY_KINDS) == set(cb["case_fields"]["novelty_kind"]["values"])
    ev = set(get_args(frame.EventType)) - {"unknown"}
    assert ev == set(cb["event_type_supported"])
    assert set(frame.RELIABILITY_ORDER) == {x["value"] for x in cb["reliability_scale"]["levels"]}
    assert set(get_args(frame.YesNo)) == set(cb["first_party"])
    seeds = json.loads((ROOT / "schemas/mechanisms/candidates-v0.json").read_text())
    ids = {c["id"] for c in seeds["cards"]}
    assert set(CODED_PATTERNS) | {"MC-12"} == ids


def test_output_schema_file_in_sync() -> None:
    on_disk = json.loads((ROOT / "schemas/coding/v2.0.0.json").read_text())
    assert on_disk == json.loads(json.dumps(output_schema(), sort_keys=True))
    assert output_schema()["$id"] == "pigtail/coding/v2.0.0"


def _coded(unit: str, value: Any, ev: str | None = None, quote: str = "") -> dict[str, Any]:
    if ev is None:
        return {"unit": unit, "value": value, "evidence_ids": [], "excerpts": [],
                "unknown_reason": "no_evidence", "confidence": "low"}  # fmt: skip
    return {"unit": unit, "value": value, "evidence_ids": [ev],
            "excerpts": [{"evidence_id": ev, "quote": quote}], "unknown_reason": None,
            "confidence": "high"}  # fmt: skip


def sample_coding(ev: str = "ev_1", quote: str = "the first tool") -> dict[str, Any]:
    return {"units": [
        _coded("category_primary", "devtools", ev, quote),
        *(_coded(f"module_active.{m}", "no", ev, quote) for m in MODULES),
        _coded("novelty_claim", "present", ev, quote),
        _coded("novelty_kind.new_in_kind", "yes", ev, quote),
        *(_coded(f"novelty_kind.{k}", "no", ev, quote) for k in NOVELTY_KINDS[1:]),
        *(_coded(f"pattern.{p}", "unknown") for p in CODED_PATTERNS),
        _coded(f"reliability@{ev}", "high", ev, quote),
        _coded(f"first_party@{ev}", "yes", ev, quote),
        _coded(f"event_type_supported@{ev}", "none", ev, quote),
    ]}  # fmt: skip


def test_structured_output_validates_against_the_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "schemas/coding/v2.0.0.json").read_text())
    good = sample_coding()
    jsonschema.validate(good, schema)
    CaseCoding.model_validate(good)
    bad = sample_coding()
    bad["units"][0]["confidence"] = "certain"  # the enums left in the schema still hold
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    with pytest.raises(ValueError):
        CaseCoding.model_validate(bad)
    extra = sample_coding()
    extra["units"][0]["handle"] = "someone"  # closed objects (no extra fields, CB-11 P1)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra, schema)


def test_flatten_units_novelty_and_missing_items() -> None:
    c = CaseCoding.model_validate(sample_coding())
    units = {u.unit: u for u in flatten(c, ("ev_1", "ev_2")).units}
    assert units["novelty_kind.new_in_kind"].value == "yes"
    assert units["novelty_kind.other"].value == "no"
    assert units["first_party@ev_2"].value == "unknown"  # the coder left ev_2 out
    absent = sample_coding()
    absent["units"][len(MODULES) + 1]["value"] = "absent"  # novelty_claim
    units = {u.unit: u for u in flatten(CaseCoding.model_validate(absent), ("ev_1",)).units}
    assert units["novelty_kind.new_in_kind"].excluded == "not_applicable"
    assert "pattern.MC-12" not in units  # derived, never coded


# --- prompts --------------------------------------------------------------------------------
PINNED = {  # blind-v1 input spec included (ADR-086); flat unit output (addendum 1)
    "A": "case-coder-a@3#1c7ae4d34bac",
    "B": "case-coder-b@3#7d06803578f2",
    "adjudicator": "case-adjudicator@3#5c1bf110bb6b",
}


def test_coder_prompts_differ_and_are_fingerprinted() -> None:
    assert CODER_A.system != CODER_B.system and CODER_A.template != CODER_B.template
    assert CODER_A.context == CODER_B.context == ADJUDICATOR.context  # the same frame
    fps = fingerprints()
    assert len({v.split("#")[1] for v in fps.values()}) == 3
    # fixed: changing a prompt's text needs a version bump and a new pinned fingerprint
    assert fps == PINNED
    for p in (CODER_A, CODER_B, ADJUDICATOR):
        assert "winner" not in (p.system + p.template + p.context).lower()
        assert "loser" not in (p.system + p.template + p.context).lower()


def _item(ev: str, kind: str, dated: str) -> RenderedItem:
    return RenderedItem(ev, kind, dated, f"kind: {kind} | dated: {dated}", f"text of {ev}")


def test_coder_evidence_orders_differ_and_input_is_blind() -> None:
    items = [
        _item("ev_c", "homepage", "2026-09-28"),
        _item("ev_a", "repo_metadata", "2025-01-01"),
        _item("ev_b", "readme_at_anchor", "2026-03-01"),
    ]
    assert [i.evidence_id for i in order_a(items)] == ["ev_a", "ev_b", "ev_c"]
    assert [i.evidence_id for i in order_b(items)] == ["ev_c", "ev_b", "ev_a"]
    text = case_input("cod_0123456789abcdef", "2026-03-10 15:00 UTC", order_a(items))
    for word in ("winner", "loser", "view", "rank", "pair", "outcome", "success"):
        assert word not in text.lower()
    flips = {option_order(f"u{i}", "cod_x") for i in range(40)}
    assert flips == {True, False}  # both orders occur, fixed per unit
    assert option_order("u1", "cod_x") == option_order("u1", "cod_x")


# --- citations -------------------------------------------------------------------------------
TEXTS = {"ev_1": "Alpha is the first CLI to check configs.\nMaintained by @user1.", "ev_2": "x"}


def unit(value: str = "present", ids: tuple[str, ...] = ("ev_1",), ex: Any = None) -> CodedUnit:
    excerpts = ex if ex is not None else (("ev_1", "the first  CLI to check"),)
    return CodedUnit("novelty_claim", "novelty_claim", value, ids, excerpts, None, "high")


def test_citations_required_and_unknown_allowed() -> None:
    ok = check(unit(), TEXTS)
    assert ok.status == "ok" and ok.unit.value == "present"  # whitespace collapsed
    unk = check(unit("unknown", (), ()), TEXTS)
    assert unk.status == "unknown" and unk.unit.unknown_reason == "insufficient_evidence"
    for bad, why in (
        (unit(ids=()), "no_evidence_id"),
        (unit(ids=("ev_9",), ex=(("ev_9", "x"),)), "evidence_not_offered"),
        (unit(ex=()), "missing_excerpt"),
        (unit(ex=(("ev_1", "not in the text"),)), "excerpt_not_found"),
        (unit(ex=(("ev_1", "a" * 301),)), "excerpt_length"),
        (unit(ex=(("ev_1", "Maintained by @user1"),)), "excerpt_names_person"),
    ):
        c = check(bad, TEXTS)
        assert c.status == "citation_failed" and why in c.problems, (why, c.problems)
        assert c.unit.value == "unknown" and c.unit.unknown_reason == "citation_failed"
        assert c.unit.excerpts == ()


def test_at_most_one_excerpt_per_source() -> None:
    c = check(unit(ex=(("ev_1", "the first CLI"), ("ev_1", "check configs"))), TEXTS)
    assert c.status == "ok" and c.unit.excerpts == (("ev_1", "the first CLI"),)
    assert c.excerpts_dropped == 1


# --- the case rule ---------------------------------------------------------------------------
def row(view: str, name: str, role: str, rank: Any, pid: Any, panel: Any, head: Any,
        dist: Any = None) -> dict[str, Any]:  # fmt: skip
    pair = None if pid is None else {"pair_id": pid, "distance": dist}
    return {
        "view": view, "candidate_ref": f"gh:org-q/{name}", "repo_full_name": f"org-q/{name}",
        "role": role, "rank": rank, "pair_id": pid, "pair_panel": panel, "headline": head,
        "repo_host_id": 1, "detail": {"anchor": {"at": "2026-03-10T15:00:00+00:00"},
                                      "pair": pair, "star_anomaly": {"flag": "false"}},
    }  # fmt: skip


ROWS = [
    row("follow_through", "a-w2", "winner", 2, 2, "field", None),
    row("follow_through", "a-l2", "matched_loser", None, 2, "field", True, 0.3),
    row("follow_through", "a-w1", "winner", 1, 1, "field", None),
    row("follow_through", "a-l1b", "matched_loser", None, 1, "field", True, 0.8),
    row("follow_through", "a-l1a", "matched_loser", None, 1, "field", True, 0.2),
    row("follow_through", "a-w3", "winner", 3, 3, "field", None),
    row("follow_through", "a-l3", "matched_loser", None, 3, "field", False, 0.1),  # not headline
    row("follow_through", "ex-1", "exemplar", None, 9, "exemplar", None),
    row("follow_through", "ex-1l", "exemplar_matched_loser", None, 9, "exemplar", None, 0.4),
    row("launch", "b-w1", "winner", 1, 1, "field", None),
    row("launch", "b-l1", "matched_loser", None, 1, "field", True, 0.5),
    row("launch", "b-w2", "winner", 2, 2, "field", None),
    row("launch", "b-l2", "matched_loser", None, 2, "field", True, 0.5),
    row("launch_undeclared", "u-w1", "winner", 1, 1, "field", None),
    row("launch_undeclared", "u-l1", "matched_loser", None, 1, "field", True, 0.1),
]


def names(cases: list[Any]) -> list[str]:
    return [f"{c.view[:1]}:{c.candidate_ref.split('/')[1]}" for c in cases]


def test_pilot_case_rule_is_deterministic_and_stratified() -> None:
    five = select_cases(ROWS, 5)
    assert names(five) == ["f:a-w1", "f:a-l1a", "l:b-w1", "l:b-l1", "f:ex-1"]
    for seed in range(5):
        shuffled = list(ROWS)
        random.Random(seed).shuffle(shuffled)
        assert names(select_cases(shuffled, 5)) == names(five)
    assert [c.position for c in five] == [1, 2, 3, 4, 5]
    nine = select_cases(ROWS, 9)
    assert names(nine)[5:] == ["f:ex-1l", "f:a-w2", "f:a-l2", "l:b-w2"]
    assert all(c.view != "launch_undeclared" for c in select_cases(ROWS, 40))
    assert "f:a-w3" not in names(select_cases(ROWS, 40))  # its only pair is not headline
    two = select_cases(ROWS, 2)  # at least one view-A and one view-B case
    assert {c.view for c in two} == {"follow_through", "launch"}
    assert names(select_cases(ROWS, 1)) == ["f:a-w1"]
    with pytest.raises(ValueError):
        select_cases(ROWS, 0)


# --- cost model and projection ----------------------------------------------------------------
def test_planning_cost_per_case() -> None:
    per = PLANNING.usd_per_case("claude-sonnet-5", batch=True)
    assert per["coder_a"] == per["coder_b"] and per["total"] is not None
    assert per["total"] == pytest.approx(2 * per["coder_a"] + per["adjudication"])
    std = PLANNING.usd_per_case("claude-sonnet-5", batch=False)
    assert std["total"] == pytest.approx(2 * per["total"])  # batch is 50 %
    assert PLANNING.usd_per_case("unknown-model")["total"] is None
    rt = CaseCostModel.from_dict(PLANNING.to_dict())
    assert rt.usd_per_case("claude-sonnet-5")["total"] == pytest.approx(per["total"], rel=1e-3)


def _totals(calls: int, tin: int, tout: int, usd: float) -> StageTotals:
    t = StageTotals()
    t.add({"calls": calls, "batched": calls, "input_tokens": tin, "output_tokens": tout,
           "cache_write_tokens": 100, "cache_read_tokens": 100, "cost_usd": usd})  # fmt: skip
    return t


def test_measured_model_and_projection_with_h6() -> None:
    by_case = {
        "cod_a": {"coder_a": _totals(1, 5000, 2000, 0.02), "coder_b": _totals(1, 5000, 2000,
                  0.02), "adjudication": _totals(1, 7000, 1000, 0.01)},
        "cod_b": {"coder_a": _totals(1, 7000, 2000, 0.03), "coder_b": _totals(1, 7000, 2000,
                  0.03)},
    }  # fmt: skip
    m = measured(by_case, {"cod_a": {"core": 6, "graphql": 1}, "cod_b": {"core": 4}}, 2)
    assert m.source == "measured" and m.n_cases == 2 and m.adjudication_share == 0.5
    assert m.coder.input == 6000 and m.github == {"core": 5.0, "graphql": 0.5}
    assert m.measured_usd == {"coder_a": 0.025, "coder_b": 0.025, "adjudication": 0.005,
                              "total": 0.055}  # fmt: skip
    kw: dict[str, Any] = dict(
        extraction_model="claude-sonnet-5",
        synthesis_model="claude-opus-5-5",
        full_cases=120,
        coded_cases=5,
        brief_spent_usd=2.0,
        month_spent_usd=10.0,
        month_cap_usd=200.0,
    )
    ok = projection(m, cap_usd=150.0, **kw)
    assert ok["within_cap"] and not ok["h6"] and ok["cases_remaining"] == 115
    total = ok["projected_total_usd"]
    assert total == pytest.approx(
        2.0 + ok["per_case_usd"]["total"] * 115 + ok["synthesis_usd_planning"], abs=1e-3
    )
    over = projection(m, cap_usd=total - 0.01, **kw)
    assert over["h6"] and not over["within_cap"]
    unknown = projection(m, cap_usd=1e9, **{**kw, "extraction_model": "no-such-model"})
    assert unknown["h6"]  # unknown money is never "within the cap"


# --- estimate integration ----------------------------------------------------------------------
def test_estimate_prices_coding_per_case_and_uses_the_measured_model() -> None:
    from pigtail.briefs.estimate import ESTIMATE_MODEL, estimate
    from tests import selection_fake

    b = selection_fake.brief()
    kw: dict[str, Any] = dict(launch_sources=(False, False), ph_topics=0)
    plan = estimate(b, **kw)
    assert ESTIMATE_MODEL == "estimate-v11"
    by = {s.stage: s for s in plan.stages}
    assert by["extraction"].llm_calls == 2 * plan.cases
    assert by["adjudication"].llm_calls == -(-plan.cases * 9 // 10)
    assert plan.to_dict()["coding_cost_model"]["source"] == "planning"
    cheap = CaseCostModel("measured", CallTokens(1000, 500), CallTokens(800, 200), 0.2,
                          {"core": 5.0, "graphql": 1.0}, n_cases=5)  # fmt: skip
    cal = estimate(b, case_model=cheap, **kw)
    bc = {s.stage: s for s in cal.stages}
    assert bc["extraction"].usd is not None and by["extraction"].usd is not None
    assert bc["extraction"].usd < by["extraction"].usd
    assert bc["adjudication"].llm_calls == -(-cal.cases * 2 // 10)
    assert cal.to_dict()["coding_cost_model"]["source"] == "measured"


# --- decay, project pages, private paths, ops lines, schedule ---------------------------------
def test_decay_classification() -> None:
    from pigtail.connectors.github import Probe
    from pigtail.forensics.decay import classify

    h = "a" * 64
    assert classify(Probe(304, None, None, None), h) == ("not_modified", False, False)
    assert classify(Probe(200, h, None, None), h) == ("unchanged", False, False)
    assert classify(Probe(200, "b" * 64, None, None), h) == ("changed", True, False)
    assert classify(Probe(200, "b" * 64, None, None), None) == ("retrievable", None, False)
    assert classify(Probe(404, None, None, None), h) == ("gone", None, True)
    assert classify(Probe(410, None, None, None), h) == ("gone", None, True)
    for st in (None, 500, 503, 429, 403):
        assert classify(Probe(st, None, None, None), h)[0] == "error"


def test_project_page_rules_and_robots() -> None:
    from pigtail.connectors.project_page import project_page_url, robots_allows, visible_text

    assert project_page_url("https://tool.example.org/", "org/tool") == (
        "https://tool.example.org/", None)  # fmt: skip
    assert project_page_url("tool.example.org", "org/tool")[0] == "https://tool.example.org"
    for url in ("https://twitter.com/someone", "https://x.com/a", "https://github.com/someone",
                "https://www.linkedin.com/in/a", "https://someone.github.io/",
                "https://medium.com/@someone"):  # fmt: skip
        assert project_page_url(url, "someone/tool") == (None, "not_project_page"), url
    assert project_page_url("https://org.github.io/tool/", "org/tool")[0] is not None
    assert project_page_url("https://org.github.io/", "org/org.github.io")[0] is not None
    assert project_page_url(None, "org/tool") == (None, "no_homepage")
    assert project_page_url("ftp://x.org", "org/tool") == (None, "not_http")
    u = "https://s.example.org/page"
    assert robots_allows(404, b"", u) and not robots_allows(503, b"", u)
    assert not robots_allows(None, b"", u)
    assert not robots_allows(200, b"User-agent: *\nDisallow: /\n", u)
    assert robots_allows(200, b"User-agent: *\nDisallow: /private\n", u)
    assert not robots_allows(200, b"User-agent: pigtail\nDisallow: /\n", u)
    html = b"<html><head><title>T</title><style>x{}</style></head><body><p>Hi</p>" \
           b"<script>evil()</script></body></html>"  # fmt: skip
    text = visible_text(html)
    assert "Hi" in text and "evil" not in text and "Title: T" in text


def test_private_report_dir_refuses_tracked_paths(tmp_path: Path) -> None:
    from pigtail.forensics.report import NotPrivate, ensure_private, write_report

    assert ensure_private(tmp_path / "data") == (tmp_path / "data").resolve()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(NotPrivate):
        ensure_private(repo / "reports")
    (repo / ".gitignore").write_text("data/\n")
    assert ensure_private(repo / "data" / "reports")
    with pytest.raises(NotPrivate):  # this repository: tracked paths are refused
        ensure_private(ROOT / "docs" / "reports")
    paths = write_report(tmp_path / "d", "b1", 2, "pilot", {"x": 1}, "# md\n",
                         day=datetime(2026, 9, 28, tzinfo=UTC).date())  # fmt: skip
    assert paths["json"].endswith("reports/b1/v2/pilot-2026-09-28.json")
    assert Path(paths["md"]).read_text() == "# md\n"


def test_ops_lines_carry_counts_only() -> None:
    from pigtail.forensics.report import ops_lines

    summary = {
        "cases": 5,
        "cost": {"total_usd": 0.4, "per_case_usd": 0.08, "mode": "batch",
                 "per_case_usd_by_stage": {"coder_a": 0.03, "coder_b": 0.03,
                                           "adjudication": 0.02},
                 "github_per_case": {"core": 6}},
        "projection": {"full_brief_cases": 126, "projected_total_usd": 12.5, "cap_usd": 150.0,
                       "h6": False},
        "reliability": {"fields": 30, "statistics": 32, "below_070": 3, "undefined": 20,
                        "assessed": 1},
        "decay_scheduled": 75,
    }  # fmt: skip
    lines = ops_lines(summary, label="brief 1", month="2026-09")
    text = " ".join(lines.values())
    assert "0.0800" in text and "126 cases" in text and "150.0000" in text
    assert "H6" not in lines["COSTS.md"]
    over = ops_lines({**summary, "projection": {**summary["projection"], "h6": True}},
                     label="brief 1", month="2026-09")  # fmt: skip
    assert "H6" in over["COSTS.md"] and "H6" in over["STATUS.md"]
    for bad in ("org-", "gh:", "cod_", "sel_", "brun_", "http"):
        assert bad not in text


def test_evidence_decay_job_is_scheduled() -> None:
    from pigtail.scheduler.config import load
    from pigtail.scheduler.jobs import Planner

    cfg = load(ROOT / "infra" / "schedule.toml")
    job = cfg.job("evidence_decay")
    assert job.command == ("brief", "decay", "--all", "--due") and job.enabled
    plan = Planner({}).plan(job, datetime(2026, 9, 28, tzinfo=UTC))
    assert plan.skip is None and plan.commands == (job.command,)


def test_launch_events_doc_keeps_whitelisted_fields_only() -> None:
    from pigtail.briefs.candidates import Candidate
    from pigtail.forensics.evidence import launch_events_doc, render_text
    from pigtail.forensics.store import PilotCase

    cand = Candidate("gh:person-x/tool", "person-x/tool", sources=[
        {"source": "show_hn", "hn_item_id": 1, "title": "Show HN: tool by person-x", "points": 5,
         "time": "2026-01-01T00:00:00+00:00", "by": "someone"},
        {"source": "bsky_maintainer_posts", "status": "complete", "posts": [
            {"kind": "k", "time": "t", "role": "maintainer", "match": "repo_url", "text": "T",
             "handle": "h.bsky.social"}]},
        {"source": "hn_mention", "text": "a comment"},
    ])  # fmt: skip
    case = PilotCase("follow_through:gh:person-x/tool", "follow_through", "gh:person-x/tool",
                     "person-x/tool", None, None, 1, "winner", 1,
                     {"at": "2026-01-02T00:00:00+00:00", "type": "launch"})  # fmt: skip
    doc = json.dumps(launch_events_doc(case, cand))
    for leaked in ('"by"', "someone", '"text"', "h.bsky.social", "a comment", "person-x"):
        assert leaked not in doc, leaked
    assert "Show HN: tool by [owner]" in doc and '"points": 5' in doc
    meta = {"data": {"r0": {"databaseId": 1, "nameWithOwner": "o/t", "stargazerCount": 987654,
                            "forkCount": 4321, "description": "d", "owner": {"__typename":
                            "User"}}}}  # fmt: skip
    text = render_text("repo_metadata", json.dumps(meta).encode())
    assert "987654" not in text and "4321" not in text and "Description: d" in text


def test_blinding_spec_is_in_the_fingerprinted_context() -> None:
    from pigtail.forensics.prompts import BLIND_KEYS, BLIND_VERSION, blind_obj, blind_text

    for p in (CODER_A, CODER_B, ADJUDICATOR):
        assert BLIND_VERSION in p.context
        assert all(k in p.context for k in ("points", "votesCount", "commentsCount", "stars"))
    assert {"points", "votesCount", "commentsCount", "stargazerCount", "forkCount", "percentile",
            "values", "rank", "role", "pair", "view"} <= set(BLIND_KEYS)  # fmt: skip
    doc = {
        "signals": [
            {
                "source": "show_hn",
                "points": 9,
                "time": "t",
                "posts": [{"votesCount": 1, "commentsCount": 2, "createdAt": "c"}],
            }
        ],
        "rank": 1,
        "role": "winner",
    }
    assert blind_obj(doc) == {"signals": [{"source": "show_hn", "time": "t",
                                           "posts": [{"createdAt": "c"}]}]}  # fmt: skip
    t = blind_text("12.5k stars, 1,024 forks, 300 upvotes, 88 points, v2.0 with 3 commands")
    assert t.count("[count withheld]") == 4 and "v2.0 with 3 commands" in t
