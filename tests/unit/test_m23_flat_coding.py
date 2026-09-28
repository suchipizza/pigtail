"""ADR-086 addendum 1 (the live pilot's coder calls all failed with "compiled grammar too large"):
the flat coder output and its per-unit validation, the schema-size guard over every schema the
product sends, the API error kept per request (scrubbed), and no cost model from zero measured
cost. Synthetic data, no network, no database."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pigtail.forensics.citations import CitationStats, check
from pigtail.forensics.cost import StageTotals, measurable, measured
from pigtail.forensics.frame import (
    CODED_PATTERNS,
    FIELDS,
    MODULES,
    NOVELTY_KINDS,
    SCHEMA_INVALID,
    Adjudication,
    CaseCoding,
    allowed_values,
    expected_units,
    flatten,
    value_ok,
)
from pigtail.llm.errors import safe_error_message
from pigtail.llm.schema_guard import LIMITS, complexity, over_limits
from pigtail.llm.types import schema_of
from tests.llm_contract import product_schemas

ROOT = Path(__file__).resolve().parents[2]
TEXTS = {"ev_1": "Alpha is the first CLI to check configs.\nMaintained by @user1.", "ev_2": "x"}


def u(unit: str, value: str, ev: str | None = "ev_1", quote: str = "the first CLI",
      reason: str | None = None) -> dict[str, Any]:  # fmt: skip
    if ev is None:
        return {"unit": unit, "value": value, "unknown_reason": reason, "evidence_ids": [],
                "excerpts": [], "confidence": "low"}  # fmt: skip
    return {"unit": unit, "value": value, "unknown_reason": reason, "evidence_ids": [ev],
            "excerpts": [{"evidence_id": ev, "quote": quote}], "confidence": "high"}  # fmt: skip


def coding(*units: dict[str, Any]) -> CaseCoding:
    return CaseCoding.model_validate({"units": list(units)})


def by_unit(c: CaseCoding, offered: tuple[str, ...] = ("ev_1",)) -> dict[str, Any]:
    return {x.unit: check(x, TEXTS) for x in flatten(c, offered).units}


# --- the flat schema and per-unit validation -------------------------------------------------
def test_flat_schema_shape_and_size() -> None:
    s = schema_of(CaseCoding)
    unit = s["$defs"]["UnitCoding"]
    assert set(unit["properties"]) == {"unit", "value", "unknown_reason", "evidence_ids",
                                       "excerpts", "confidence"}  # fmt: skip
    assert unit["properties"]["unit"]["type"] == unit["properties"]["value"]["type"] == "string"
    assert len(json.dumps(s)) < 2_000  # 1.0.0 was 7.8 KB
    # no larger, grammar-wise, than the adjudicator's schema the API accepts
    c, a = complexity(CaseCoding), complexity(Adjudication)
    assert (c.enum_values, c.branches, c.objects) <= (a.enum_values, a.branches, a.objects)
    assert c.properties <= a.properties


def test_expected_units_cover_the_frame() -> None:
    units = expected_units(("ev_1", "ev_2"))
    case = [f.field for f in FIELDS if f.unit == "case"]
    assert units[: len(case)] == case and len(case) == 1 + len(MODULES) + 1 + 4 + 12
    assert units[len(case):] == [
        "reliability@ev_1", "first_party@ev_1", "event_type_supported@ev_1",
        "reliability@ev_2", "first_party@ev_2", "event_type_supported@ev_2",
    ]  # fmt: skip


def test_value_types_mirror_allowed_values() -> None:
    for f in FIELDS:
        for v in allowed_values(f.field):
            assert value_ok(f.field, v), (f.field, v)
        assert not value_ok(f.field, "maybe")
    assert value_ok("category_primary", "devtools") and not value_ok("category_primary", "cli")
    assert value_ok("reliability", "unknown") and not value_ok("reliability", "very high")
    assert not value_ok("pattern.MC-01", "yes")  # presence, not yes/no
    assert not value_ok("module_active.ai_hype", "present")


def test_valid_value_is_kept_with_its_citation() -> None:
    r = by_unit(coding(u("category_primary", "devtools")))["category_primary"]
    assert r.status == "ok" and r.unit.value == "devtools"
    assert r.unit.excerpts == (("ev_1", "the first CLI"),)


def test_invalid_enum_value_becomes_schema_invalid_and_is_counted() -> None:
    c = coding(
        u("category_primary", "command-line-tools"),  # not a category
        u("pattern.MC-01", "yes"),  # yes/no on a presence field
        u("reliability@ev_1", "high"),
    )
    got = by_unit(c)
    for key in ("category_primary", "pattern.MC-01"):
        r = got[key]
        assert r.status == SCHEMA_INVALID and r.unit.value == "unknown", key
        assert r.unit.unknown_reason == SCHEMA_INVALID and r.unit.excerpts == ()
        assert "value_not_allowed" in r.problems
    assert got["reliability@ev_1"].status == "ok"
    assert flatten(c, ("ev_1",)).schema_invalid == 2
    stats = CitationStats()
    for r in got.values():
        stats.add(r)
    d = stats.to_dict()
    assert d["schema_invalid"] == 2 and d["reasons"]["value_not_allowed"] == 2
    assert d["citation_failed"] == 0


def test_unknown_is_allowed_and_missing_units_are_unknown() -> None:
    got = by_unit(coding(u("pattern.MC-04", "unknown", None, reason="unobservable_channel")))
    r = got["pattern.MC-04"]
    assert r.status == "unknown" and r.unit.unknown_reason == "unobservable_channel"
    miss = got["first_party@ev_1"]  # the coder left it out
    assert miss.status == "unknown" and miss.unit.unknown_reason == "insufficient_evidence"
    assert len(got) == len(expected_units(("ev_1",)))


def test_citations_still_enforced_on_flat_units() -> None:
    got = by_unit(coding(
        u("module_active.cli_devtools", "yes", quote="not in the text"),
        u("module_active.ai_hype", "no", ev="ev_9", quote="x"),
        u("module_active.corporate_backed", "yes", quote="Maintained by @user1"),
        u("first_party@ev_1", "yes", ev=None),
    ))  # fmt: skip
    assert "excerpt_not_found" in got["module_active.cli_devtools"].problems
    assert "evidence_not_offered" in got["module_active.ai_hype"].problems
    assert "excerpt_names_person" in got["module_active.corporate_backed"].problems
    assert "no_evidence_id" in got["first_party@ev_1"].problems
    for k in ("module_active.cli_devtools", "module_active.ai_hype",
              "module_active.corporate_backed", "first_party@ev_1"):  # fmt: skip
        assert got[k].status == "citation_failed" and got[k].unit.value == "unknown"


def test_unexpected_and_repeated_units_are_ignored_and_listed() -> None:
    c = coding(
        u("category_primary", "devtools"),
        u("category_primary", "security"),  # repeated: the first counts
        u("reliability@ev_7", "high"),  # an item not offered
        u("pattern.MC-12", "present"),  # derived, never coded
        u("handle", "someone"),
    )
    f = flatten(c, ("ev_1",))
    assert set(f.ignored) == {"category_primary", "reliability@ev_7", "pattern.MC-12", "handle"}
    assert {x.unit: x.value for x in f.units}["category_primary"] == "devtools"
    assert all(x.unit != "pattern.MC-12" for x in f.units)


def test_novelty_kinds_follow_the_coders_claim() -> None:
    kinds = [u(f"novelty_kind.{k}", "yes" if k == "new_approach" else "no") for k in NOVELTY_KINDS]
    present = by_unit(coding(u("novelty_claim", "present"), *kinds))
    assert present["novelty_kind.new_approach"].unit.value == "yes"
    assert present["novelty_kind.other"].unit.value == "no"
    for claim in ("absent", "unknown", "invalid-claim"):
        got = flatten(coding(u("novelty_claim", claim), *kinds), ("ev_1",)).units
        nk = [x for x in got if x.field.startswith("novelty_kind.")]
        assert len(nk) == 4 and all(x.excluded == "not_applicable" for x in nk), claim
    assert len(CODED_PATTERNS) == 12


# --- the schema-size guard -------------------------------------------------------------------
def test_guard_calibration_refused_schema_fails_accepted_passes() -> None:
    refused = json.loads((ROOT / "schemas/coding/v1.0.0.json").read_text())  # the live 400
    over = over_limits(refused)
    assert set(over) == {"json_bytes", "enum_values", "branches", "objects", "properties"}
    assert over_limits(Adjudication) == {}  # accepted by the API
    a = complexity(Adjudication)
    for k, v in a.to_dict().items():  # with room to spare, so the limit is not a coin toss
        assert v * 2 <= LIMITS.to_dict()[k], k


@pytest.mark.parametrize("name,job,schema", product_schemas(), ids=lambda x: str(x)[:20])
def test_every_product_schema_fits_the_guard(name: str, job: str, schema: Any) -> None:
    from pigtail.llm.stages import stage_for

    assert stage_for(job)  # a known job
    assert over_limits(schema) == {}, (name, complexity(schema))


def test_guard_inlines_refs_and_counts_branches() -> None:
    leaf = {"type": "object", "properties": {"v": {"enum": ["a", "b", "c"]},
            "r": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "additionalProperties": False}  # fmt: skip
    s = {"type": "object", "properties": {f"p{i}": {"$ref": "#/$defs/L"} for i in range(4)},
         "$defs": {"L": leaf}, "additionalProperties": False}  # fmt: skip
    c = complexity(s)
    assert (c.enum_values, c.branches, c.objects, c.properties) == (12, 8, 5, 12)


# --- API errors kept per request, scrubbed ---------------------------------------------------
def test_safe_error_message_masks_secrets_and_identifiers_and_truncates() -> None:
    msg = (
        "The compiled grammar is too large. key sk-ant-api03-SECRETSECRET token=abc123 "
        "Authorization: Bearer xyz contact someone@example.org " + "x" * 600
    )
    out = safe_error_message(msg)
    assert out is not None and "compiled grammar" in out
    for leak in ("SECRETSECRET", "abc123", "xyz", "someone@example.org"):
        assert leak not in out
    assert len(out) <= 400
    assert safe_error_message(None) is None


def test_api_batch_results_keep_the_error_type_and_message() -> None:
    from pigtail.llm.api import ApiBackend

    err = SimpleNamespace(error=SimpleNamespace(
        type="invalid_request_error",
        message="The compiled grammar is too large, which would cause performance issues.",
    ))  # fmt: skip
    results = [SimpleNamespace(custom_id="k1", result=SimpleNamespace(type="errored", error=err))]
    sdk = SimpleNamespace(messages=SimpleNamespace(batches=SimpleNamespace(
        results=lambda _bid: iter(results))))  # fmt: skip
    (r,) = list(ApiBackend(client=sdk).batch_results("msgbatch_x"))  # type: ignore[arg-type]
    assert r.kind == "errored" and r.error == "invalid_request_error" and not r.retryable
    assert r.message is not None and "compiled grammar is too large" in r.message


def test_run_batch_reports_the_error_per_item_and_never_retries_a_400() -> None:
    from tests.forensics_fake import GRAMMAR_ERROR, CodingBatchBackend
    from tests.unit.test_llm_m21b import PROMPT, Echo, client, items

    fb = CodingBatchBackend(fail_when=lambda _p: True)
    c = client({"api": fb})
    run = c.run_batch(PROMPT, items("a", "b"), Echo, job="extraction",
                      before_submit=lambda *_a: None, est_usd_per_item=0.01,
                      sleep=lambda _s: None)  # fmt: skip
    assert run.results == {} and set(run.failed) == {"r0", "r1"}
    assert run.errors["r0"] == {"type": "invalid_request_error", "message": GRAMMAR_ERROR}
    assert len(fb.submitted) == 1  # no standard-call fallback for a bad request
    (bid,) = run.batch_ids
    reqs = c.batch_store.requests(bid)
    assert {r.error_type for r in reqs} == {"invalid_request_error"}
    assert {r.error_message for r in reqs} == {GRAMMAR_ERROR}


# --- no cost model from zero measured cost ---------------------------------------------------
def _t(calls: int, usd: float) -> StageTotals:
    t = StageTotals()
    t.add({"calls": calls, "batched": calls, "input_tokens": 0 if not usd else 5000,
           "output_tokens": 0 if not usd else 2000, "cost_usd": usd})  # fmt: skip
    return t


def test_no_cost_model_from_zero_measured_cost() -> None:
    assert not measurable({})
    zero = {"cod_a": {"coder_a": _t(0, 0.0), "coder_b": _t(0, 0.0)}}
    assert not measurable(zero)
    with pytest.raises(ValueError):
        measured(zero, {}, 5)
    only_adj = {"cod_a": {"adjudication": _t(1, 0.01)}}
    assert not measurable(only_adj)
    ok = {"cod_a": {"coder_a": _t(1, 0.03), "coder_b": _t(1, 0.03)}}
    assert measurable(ok) and measured(ok, {}, 1).measured_usd == {
        "coder_a": 0.03, "coder_b": 0.03, "adjudication": 0.0, "total": 0.06}  # fmt: skip


def test_reports_say_failed_and_skip_the_projection() -> None:
    from pigtail.forensics.report import ops_lines, pilot_markdown

    report = {
        "provenance": {"brief_id": "b1", "brief_version": 1, "date": "2026-09-28"},
        "outcome": {"status": "failed", "stop": {"errors": [
            {"error": "invalid_request_error: The compiled grammar is too large", "requests": 10}
        ]}},
        "cases": [{"position": 1, "coding_id": "cod_x", "view": "A", "role": "winner",
                   "pair_id": 1, "repo": "org-p/x", "anchor": {}, "evidence": [], "gaps": [],
                   "coding_errors": {"A": {"type": "invalid_request_error",
                                           "message": "The compiled grammar is too large"}},
                   "units": []}],
        "reliability": [],
        "cost": {"per_case": {}, "total_usd": 0.0, "per_case_usd": 0.0},
        "projection": {"label": "projection", "skipped": "every coder call failed",
                       "h6": False},
        "decay": {"scheduled": 0},
        "limitations": [],
    }  # fmt: skip
    md = pilot_markdown(report)
    assert "FAILED" in md and "compiled grammar is too large" in md
    assert "No projection: every coder call failed." in md and "Within the brief's cap" not in md
    lines = ops_lines({"cases": 5, "cost": {"total_usd": 0.0}, "projection": report["projection"]},
                      label="brief 1", month="2026-09")  # fmt: skip
    assert "no projection" in lines["COSTS.md"]
