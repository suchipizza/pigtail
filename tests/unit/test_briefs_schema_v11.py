"""Brief schema v1.1 (ADR-057; PRD R4.11, R18.1): structured reference cases, the distribution
examples panel, report options; v1 briefs still load and are migrated on save. Synthetic data.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from pigtail.briefs.budget import Allowance
from pigtail.briefs.cache import plan_rerun
from pigtail.briefs.estimate import estimate
from pigtail.briefs.model import (
    SCHEMA_VERSION,
    BriefInvalid,
    dump_yaml,
    load_brief_text,
    validate_brief,
)
from pigtail.briefs.store import BriefStore

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "docs" / "examples" / "brief-example.yaml"
V1 = ROOT / "schemas" / "brief" / "v1.json"
V11 = ROOT / "schemas" / "brief" / "v1.1.json"
ALLOW = Allowance(10_000_000, "configured")


def v1_brief() -> dict[str, Any]:
    """A brief in the v1 layout: string reference cases, no v1.1 sections."""
    d = copy.deepcopy(yaml.safe_load(EXAMPLE.read_text()))
    d["schema_version"] = "brief/v1"
    for k in ("distribution_exemplars", "report"):
        d.pop(k)
    d["field"]["reference_cases"] = [
        "reference | Synthetic Ref One | https://example.com/one | engine repo",
        "Synthetic Ref Two",
        "distribution_exemplar | Synthetic Showcase | https://example.org/a https://example.org/b "
        "| synthetic-org/showcase | launch week",
    ]
    exp = d["expansion"]
    d["expansion"] = {k: exp[k] for k in ("problem_statement", "users", "keywords", "competitors")}
    d["expansion"]["topics"] = ["yaml", "linter"]
    d["expansion"]["generated_by"] = "user"
    return d


def test_adr_057_v1_schema_file_is_kept_and_still_describes_v1_briefs():
    v1 = json.loads(V1.read_text())
    assert v1["$id"].endswith("/schemas/brief/v1.json")
    assert not list(Draft202012Validator(v1).iter_errors(v1_brief()))
    v11 = json.loads(V11.read_text())
    assert {"distribution_exemplars", "report"} <= set(v11["properties"])
    assert "distribution_exemplars" not in v1["properties"]


def test_adr_057_v1_brief_loads_and_migrates_reference_cases_and_exemplars():
    b = validate_brief(v1_brief())
    assert b.schema_version == SCHEMA_VERSION == "brief/v1.1"
    refs = b.field.reference_cases
    assert [(r.name, r.urls, r.note, r.role) for r in refs] == [
        ("Synthetic Ref One", ["https://example.com/one"], "engine repo", "reference"),
        ("Synthetic Ref Two", [], None, "reference"),
    ]
    (ex,) = b.distribution_exemplars.projects
    assert ex.name == "Synthetic Showcase"
    assert ex.urls == ["https://example.org/a", "https://example.org/b"]
    assert ex.repo == "synthetic-org/showcase" and ex.note == "launch week"
    assert b.distribution_exemplars.losers_per_exemplar == 2
    assert b.distribution_exemplars.match_on == ["launch_type", "launch_period", "audience_bucket"]
    assert b.report.show_absolute_numbers is True and b.report.transferability_labels is True
    # the migrated brief validates against the v1.1 JSON Schema
    v11 = json.loads(V11.read_text())
    dumped = yaml.safe_load(dump_yaml(b))
    assert not list(Draft202012Validator(v11).iter_errors(dumped))


def test_adr_057_v1_file_in_the_store_is_read_and_migrated_on_save(tmp_path: Path):
    store = BriefStore(tmp_path / "briefs")
    d = tmp_path / "briefs" / "example-config-linter"
    d.mkdir(parents=True)
    raw = {**v1_brief(), "version": 1, "created_at": "2026-09-26T00:00:00Z"}
    (d / "v0001.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    got = store.get("example-config-linter")
    assert got.version == 1 and "schema_version: brief/v1\n" in got.yaml_text  # untouched on disk
    assert len(got.brief.field.reference_cases) == 2
    _, created = store.save_version(got.brief, base_version=1)
    assert created is False  # unchanged content: no version, the file stays v1
    edited = got.brief.model_copy(update={"notes": "an edit"})
    new, created = store.save_version(edited, base_version=1)
    assert created and new.version == 2
    assert "schema_version: brief/v1.1" in new.yaml_text
    assert "distribution_exemplars:" in new.yaml_text


def test_adr_057_structured_rules_name_the_field():
    d = yaml.safe_load(EXAMPLE.read_text())
    d["distribution_exemplars"]["losers_per_exemplar"] = 3
    d["distribution_exemplars"]["match_on"] = ["field"]
    d["field"]["reference_cases"] = [{"name": "X", "urls": ["ftp://nope"], "repo": "not a repo"}]
    with pytest.raises(BriefInvalid) as ei:
        validate_brief(d)
    paths = {p.path for p in ei.value.problems}
    assert "distribution_exemplars.losers_per_exemplar" in paths
    assert any(p.startswith("distribution_exemplars.match_on") for p in paths)
    assert any(p.startswith("field.reference_cases.0.urls") for p in paths)
    assert any(p.startswith("field.reference_cases.0.repo") for p in paths)


def test_adr_057_exemplars_accept_a_bare_list_and_reject_duplicates():
    d = yaml.safe_load(EXAMPLE.read_text())
    d["distribution_exemplars"] = [{"name": "Synthetic A"}, "Synthetic B | https://example.com/b"]
    b = validate_brief(d)
    assert [p.name for p in b.distribution_exemplars.projects] == ["Synthetic A", "Synthetic B"]
    d["distribution_exemplars"] = {"match_on": ["launch_type", "launch_type"]}
    with pytest.raises(BriefInvalid):
        validate_brief(d)


def test_adr_057_estimate_counts_each_exemplar_and_its_losers_as_cases():
    b = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    no_ex = b.distribution_exemplars.model_copy(update={"projects": []})
    base = b.model_copy(update={"distribution_exemplars": no_ex})
    e0 = estimate(base, allowance=ALLOW)
    e1 = estimate(b, allowance=ALLOW)  # the example has 1 exemplar, 2 losers each
    assert e1.cases - e0.cases == 3 and e1.to_dict()["counts"]["exemplar_cases"] == 3
    one = b.model_copy(
        update={
            "distribution_exemplars": b.distribution_exemplars.model_copy(
                update={"losers_per_exemplar": 1}
            )
        }
    )
    assert estimate(one, allowance=ALLOW).cases - e0.cases == 2
    assert e1.stages[2].llm_calls > e0.stages[2].llm_calls  # extraction grows with cases


def test_adr_057_exemplar_and_report_edits_recompute_only_what_reads_them():
    b = load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})
    report = b.report.model_copy(update={"show_absolute_numbers": False})
    b2 = b.model_copy(update={"version": 2, "report": report})
    a = {s.stage: s.action for s in plan_rerun(b, b2).stages}
    assert a["patterns"] == "recompute" and a["discovery"] == "reuse"
