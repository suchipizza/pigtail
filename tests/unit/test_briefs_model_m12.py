"""M12 brief schema v1 (PRD R18.1, R18.2, R18.7, R18.8; ADR-053, ADR-054; D7 acceptance).

Synthetic briefs only: every test starts from `docs/examples/brief-example.yaml`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from pigtail.briefs.model import (
    SCHEMA_VERSION,
    Brief,
    BriefInvalid,
    band_for_count,
    dump_yaml,
    json_schema,
    load_brief_text,
    upgrade_v0,
    validate_brief,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "docs" / "examples" / "brief-example.yaml"
SCHEMA = ROOT / "schemas" / "brief" / "v1.2.json"
SCHEMA_V1 = ROOT / "schemas" / "brief" / "v1.json"


def example() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(EXAMPLE.read_text()))


def problems(data: dict[str, Any]) -> dict[str, str]:
    with pytest.raises(BriefInvalid) as ei:
        validate_brief(data)
    return {p.path: p.message for p in ei.value.problems}


# --- schema file ---------------------------------------------------------------------------------
def test_r18_1_schema_file_is_valid_2020_12_and_matches_the_model():
    committed = json.loads(SCHEMA.read_text())
    Draft202012Validator.check_schema(committed)
    assert committed == json_schema(), "schemas/brief/v1.2.json drifted: `pigtail brief schema`"
    assert committed["$id"].endswith("/schemas/brief/v1.2.json")
    assert committed["additionalProperties"] is False


def test_r18_9_example_brief_validates_against_model_and_json_schema():
    data = example()
    assert not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(data))
    b = validate_brief(data)
    assert b.schema_version == SCHEMA_VERSION
    assert b.warnings() == []


def test_r18_1_every_prd_and_adr_field_is_in_the_schema():
    props = json.loads(SCHEMA.read_text())
    top = set(props["properties"])
    assert {
        "schema_version", "brief_id", "version", "created_at", "edited_at", "project", "field",
        "window", "success", "own_audience", "channels", "geography", "panel", "budget",
        "optional_sources", "expansion",
    } <= top  # fmt: skip
    defs = props["$defs"]
    assert {"name", "description", "target_users", "business_model"} <= set(
        defs["Project"]["properties"]
    )
    assert {
        "core_field", "include", "exclude", "seed_projects", "reference_cases", "widening_steps"
    } <= set(defs["FieldBoundaries"]["properties"])  # fmt: skip
    assert {"primary", "primary_threshold", "minimums", "weights", "fallbacks"} <= set(
        defs["Success"]["properties"]
    )
    assert {"no_measurable_adoption", "too_few_winners"} <= set(defs["Fallbacks"]["properties"])
    assert {"min_winners", "steps"} <= set(defs["TooFewWinners"]["properties"])
    assert {"winners", "losers", "exact_match", "smd_target", "headline_exclusion_smd"} <= set(
        defs["Panel"]["properties"]
    )
    assert {"money_usd", "subscription_share", "llm_backend"} <= set(defs["Budget"]["properties"])
    assert {"trendshift", "x"} <= set(defs["OptionalSources"]["properties"])
    assert {"problem_statement", "users", "keywords", "topics", "competitors"} <= set(
        defs["Expansion"]["properties"]
    )


def test_adr_053_budget_and_source_defaults():
    """money_usd 0, subscription_share 0.5, subscription backend, no auto switch, sources off."""
    d = example()
    for k in ("budget", "optional_sources", "panel", "window"):
        d.pop(k)
    b = validate_brief(d)
    assert b.budget.money_usd == 0
    assert b.budget.subscription_share == 0.5
    assert b.budget.llm_backend == "subscription"
    assert b.budget.auto_switch_backend is False
    assert b.optional_sources.enabled_paid() == []
    assert (b.panel.winners, b.panel.losers) == (20, 20)
    assert b.panel.exact_match == ["founder_audience_bucket", "launch_half_year"]
    assert (b.panel.smd_target, b.panel.headline_exclusion_smd) == (0.25, 0.5)
    assert b.window.months == 18
    assert b.success.fallbacks.too_few_winners.min_winners == 10
    d["budget"] = {"auto_switch_backend": True}
    assert "budget.auto_switch_backend" in problems(d)


def test_yaml_off_means_disabled():
    text = EXAMPLE.read_text().replace("trendshift: false", "trendshift: off")
    assert load_brief_text(text).optional_sources.trendshift is False


# --- D7 acceptance: invalid briefs are rejected with a message naming the field ----------------
def test_d7_thirty_winners_rejected_naming_the_field():
    d = example()
    d["panel"]["winners"] = 30
    msgs = problems(d)
    assert "panel.winners" in msgs
    assert "25" in msgs["panel.winners"] and "30" in msgs["panel.winners"]


def test_d7_no_primary_dimension_rejected_naming_the_field():
    d = example()
    del d["success"]["primary"]
    assert problems(d) == {"success.primary": "is required"}


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda d: d["panel"].update(losers=14), "panel.losers"),
        (lambda d: d["window"].update(months=24), "window.months"),
        (lambda d: d["window"].update(months=6), "window.months"),
        (lambda d: d["budget"].update(subscription_share=1.5), "budget.subscription_share"),
        (lambda d: d["budget"].update(money_usd=-1), "budget.money_usd"),
        (lambda d: d["budget"].update(llm_backend="auto"), "budget.llm_backend"),
        (lambda d: d["success"].update(primary="stars"), "success.primary"),
        (lambda d: d["success"]["minimums"].update(attention="p80"), "success.minimums.attention"),
        (lambda d: d["success"]["minimums"].update(adoption="at_least_median"),
         "success.minimums.adoption"),
        (lambda d: d["field"].update(include=[]), "field.include"),
        (lambda d: d["project"].pop("target_users"), "project.target_users"),
        (lambda d: d.update(brief_id="Bad Id!"), "brief_id"),
        (lambda d: d.update(colour="blue"), "colour"),
        (lambda d: d["panel"].update(headline_exclusion_smd=0.2), "panel"),
        (lambda d: d["own_audience"]["channels"].update(github="huge"),
         "own_audience.channels.github"),
    ],
)  # fmt: skip
def test_r18_1_invalid_values_name_their_field(mutate, path):
    d = example()
    mutate(d)
    assert path in problems(d)


def test_r18_8_success_rules():
    d = example()
    d["success"]["weights"] = {"adoption": 0.7, "attention": 0.2}
    assert problems(d) == {"success.weights": "must sum to 1 (got 0.9)"}
    d["success"]["weights"] = {"adoption": 0.7, "attention": 0.3}
    assert validate_brief(d).warnings()  # the advanced option is flagged
    d = example()
    d["success"]["metrics"] = {"adoption": "att.stars@30"}
    assert "success.metrics.adoption" in problems(d)
    d["success"]["metrics"] = {"adoption": "adopt.dependents@365"}
    assert validate_brief(d).success.metric("adoption") == "adopt.dependents@365"
    assert validate_brief(d).success.metric("community").startswith("comm.returning")
    d = example()
    d["success"].update(primary="business")
    assert "success.primary_threshold" in problems(d)


def test_adr_053_2_fallback_steps_must_fit_the_definition():
    d = example()
    d["success"]["fallbacks"]["too_few_winners"]["steps"] = ["drop_community_minimum"]
    assert "success.fallbacks.too_few_winners.steps" in problems(d)  # no community minimum
    d = example()
    d["success"]["primary_threshold"] = "at_least_median"
    assert "success.fallbacks.too_few_winners.steps" in problems(d)  # can't relax to top third
    d = example()
    d["success"]["fallbacks"]["too_few_winners"]["min_winners"] = 22
    d["panel"]["winners"] = 20
    assert "success.fallbacks.too_few_winners.min_winners" in problems(d)


def test_cb10_audience_counts_become_bands():
    assert [band_for_count(n) for n in (0, 999, 1000, 99_999, 100_000)] == [
        "none", "r1", "r2", "r3", "r4",
    ]  # fmt: skip
    d = example()
    d["own_audience"]["channels"] = {"github": 2500, "reddit": 0}
    b = validate_brief(d)
    assert b.own_audience.channels == {"github": "r2", "reddit": "none"}
    assert "2500" not in dump_yaml(b)


def test_adr_054_panel_and_field_widening():
    d = example()
    d["panel"]["exact_match"] = ["launch_half_year", "launch_half_year"]
    assert "panel.exact_match" in problems(d)
    d = example()
    d["field"]["widening_steps"] = []
    assert any("widening" in w for w in validate_brief(d).warnings())


def test_adr_053_paid_source_with_zero_money_warns():
    d = example()
    d["optional_sources"]["x"] = True
    d["budget"]["money_usd"] = 0
    assert any("money_usd is 0" in w for w in validate_brief(d).warnings())


def test_r18_2_yaml_and_form_json_give_the_same_brief_and_hash():
    from_yaml = load_brief_text(EXAMPLE.read_text())
    from_form = validate_brief(json.loads(json.dumps(from_yaml.model_dump(mode="json"))))
    assert from_form == from_yaml
    assert from_form.content_hash() == from_yaml.content_hash()
    assert load_brief_text(dump_yaml(from_yaml)) == from_yaml  # canonical YAML round-trips


def test_r18_4_content_hash_ignores_store_metadata():
    b = load_brief_text(EXAMPLE.read_text())
    stamped = b.model_copy(update={"version": 7, "supersedes": 6})
    assert stamped.content_hash() == b.content_hash()
    other = b.model_copy(update={"notes": "changed"})
    assert other.content_hash() != b.content_hash()


def test_yaml_syntax_error_is_reported_with_position():
    with pytest.raises(BriefInvalid) as ei:
        load_brief_text("brief_id: x\nproject: [unclosed\n")
    assert ei.value.problems[0].path == "(yaml)"
    assert "line" in ei.value.problems[0].message


def test_not_a_mapping_rejected():
    with pytest.raises(BriefInvalid):
        validate_brief(["a", "b"])


def test_upgrade_v0_maps_the_provisional_layout():
    """A synthetic document in the provisional v0 layout (ADR-047) converts to a valid v1."""
    v0 = {
        "brief_id": "synthetic-v0",
        "version": 2,
        "created": "2026-01-01",
        "project": {
            "name": "Synthetic",
            "description": "A synthetic project used only to test the v0 upgrade.",
            "target_users": {"primary": "developers"},
            "business_model": "open source",
        },
        "field": {"include": ["tools"], "exclude": ["games"], "reference_cases": None},
        "core_field": "synthetic tools",
        "widening_steps": ["adjacent tools"],
        "time_window_months": 12,
        "success": {
            "primary": "adoption",
            "primary_threshold": "top_quartile",
            "minimums": {"attention": "at_least_median_in_neighbourhood"},
            "revenue": "not_used",
            "fallbacks": {"too_few_winners": {"steps": ["relax_primary_to_top_third"]}},
        },
        "own_audience": {"github": "none", "note": "none yet"},
        "channels": {"planned": ["github"], "extra_list": ["a", "b"], "avoid": ["paid_ads"]},
        "geography": {"focus": "global", "later": "one more region"},
        "optional_sources": {"trendshift": False},
        "legacy_key": {"kept": True},
    }
    b = validate_brief(upgrade_v0(v0))
    assert b.field.core_field == "synthetic tools"
    assert b.window.months == 12
    assert b.success.minimums == {"attention": "at_least_median"}
    assert b.own_audience.channels == {"github": "none"}
    assert b.channels.details == {"extra_list": ["a", "b"]}
    assert b.geography.later == ["one more region"]
    assert b.notes and "legacy_key" in b.notes and "revenue" in b.notes
    assert b.version is None  # the store assigns versions


def test_brief_model_is_the_single_definition():
    assert Brief.model_config.get("extra") == "forbid"
