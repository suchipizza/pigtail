"""M1-T1 / PRD §7 / R1.4: JSON Schemas v0, examples, and pydantic models in lockstep."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ValidationError

from pigtail.capture.models import RECORD_MODELS, case_id, evidence_id

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "v0"
EXAMPLES = sorted((SCHEMAS / "examples").glob("*.json"))


def schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def validator(name: str) -> Draft202012Validator:
    s = schema(name)
    Draft202012Validator.check_schema(s)
    return Draft202012Validator(s, format_checker=FormatChecker())


def kind(path: Path) -> str:
    return path.name.split(".")[0]


def test_r1_4_all_four_schemas_exist_and_are_valid_2020_12():
    for name in ("repo", "case", "evidence", "run"):
        s = schema(name)
        assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(s)
        assert "schema_version" in s["required"], name


def test_prd7_evidence_has_required_prd_fields():
    req = set(schema("evidence")["required"])
    prd = {
        "id", "source", "url", "fetched_at", "content_hash", "snapshot_ref", "reliability",
        "terms_basis", "retention_class", "deletion_state",
    }  # fmt: skip
    assert prd | {"collector_version", "case_id", "repo_id"} <= req


def test_every_schema_has_an_example():
    assert {kind(p) for p in EXAMPLES} == {"repo", "case", "evidence", "run"}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_prd7_examples_validate_against_schema(path: Path):
    doc = json.loads(path.read_text())
    errors = list(validator(kind(path)).iter_errors(doc))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_prd7_pydantic_model_json_matches_schema_for_examples(path: Path):
    doc = json.loads(path.read_text())
    model = RECORD_MODELS[kind(path)].model_validate(doc)
    dumped = model.to_json_dict()
    assert dumped == doc  # round-trip is lossless
    assert not list(validator(kind(path)).iter_errors(dumped))


@pytest.mark.parametrize("name", ["repo", "case", "evidence", "run"])
def test_prd7_model_fields_equal_schema_properties(name: str):
    s = schema(name)
    assert set(RECORD_MODELS[name].model_fields) == set(s["properties"])


def test_prd7_nested_detection_and_coverage_fields_match():
    from pigtail.capture.models import Coverage, VelocityDetection

    defs = schema("case")["$defs"]
    pairs: list[tuple[type[BaseModel], str]] = [
        (VelocityDetection, "velocity_detection"),
        (Coverage, "coverage"),
    ]
    for model, key in pairs:
        assert set(model.model_fields) == set(defs[key]["properties"]), key
        assert set(defs[key]["required"]) == set(model.model_fields), key


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("evidence", lambda d: d.update(schema_version="v9")),
        ("evidence", lambda d: d.update(content_hash="ABC")),
        ("evidence", lambda d: d.update(retention_class="forever")),
        ("case", lambda d: d.update(trigger="vibes")),
        ("run", lambda d: d.update(status="done")),
        ("repo", lambda d: d.update(extra_field=1)),
    ],
)
def test_prd7_invalid_documents_rejected_by_both(name: str, mutate: Any):
    doc = json.loads((SCHEMAS / "examples" / f"{name}.example.json").read_text())
    mutate(doc)
    assert list(validator(name).iter_errors(doc))
    with pytest.raises(ValidationError):
        RECORD_MODELS[name].model_validate(doc)


def test_prd7_schema_requires_schema_version():
    doc = json.loads((SCHEMAS / "examples" / "evidence.example.json").read_text())
    doc.pop("schema_version")
    assert list(validator("evidence").iter_errors(doc))
    # the model fills the current version by default, so its output always carries it
    assert RECORD_MODELS["evidence"].model_validate(doc).to_json_dict()["schema_version"] == "v0"


def test_ids_are_deterministic():
    from datetime import UTC, datetime

    t = datetime(2026, 9, 20, tzinfo=UTC)
    assert case_id("github:1", "velocity", t) == case_id("github:1", "velocity", t)
    assert case_id("github:1", "velocity", t) != case_id("github:2", "velocity", t)
    assert evidence_id("s", "u", "0" * 64) == evidence_id("s", "u", "0" * 64)
