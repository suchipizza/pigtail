"""Bundle contract tests (spec §48.1)."""

from __future__ import annotations

import copy
import json

import pytest

from pigtail.bundle.models import ResearchBundle
from pigtail.bundle.schema import dumps, generate_schema, load_checked_in_schema
from pigtail.bundle.validator import validate_data
from pigtail.domain.ids import new_id


def errors_of(d: dict, **kw) -> list[str]:
    return validate_data(d, **kw).errors


def test_checked_in_schema_matches_models():
    assert dumps(generate_schema()) == dumps(load_checked_in_schema()), (
        "schemas/research-bundle/0.1.0.schema.json is out of date: run python -m pigtail.bundle.schema --write"
    )


def test_schema_identity():
    s = load_checked_in_schema()
    assert s["$id"] == "https://pigtail.dev/schemas/research-bundle/0.1.0"


def test_fixture_is_valid(bundle_dict):
    rep = validate_data(bundle_dict)
    assert rep.ok, rep.errors


def test_all_top_level_arrays_exist(bundle_dict):
    for key in (
        "people",
        "repositories",
        "sources",
        "source_fetches",
        "claims",
        "evidence_links",
        "events",
        "launch_episodes",
        "growth_episodes",
        "metric_snapshots",
        "company_stages",
        "strategy_phases",
        "surfaces",
        "surface_presences",
        "tactics",
        "tactic_occurrences",
        "growth_engines",
        "growth_engine_occurrences",
        "outcomes",
        "prerequisites",
        "constraints",
        "conflicts",
        "gaps",
    ):
        assert isinstance(bundle_dict[key], list)
        bad = copy.deepcopy(bundle_dict)
        del bad[key]
        assert errors_of(bad)


def test_round_trip(bundle):
    data = json.loads(bundle.model_dump_json())
    again = ResearchBundle.model_validate(data)
    assert again.model_dump(mode="json") == bundle.model_dump(mode="json")


def test_rejects_unsupported_version(bundle_dict):
    bundle_dict["schema_version"] = "0.2.0"
    assert any("unsupported schema_version" in e for e in errors_of(bundle_dict))


def test_rejects_non_uuid7(bundle_dict):
    bundle_dict["claims"][0]["id"] = "6f1c2c8e-0000-4000-8000-000000000000"  # v4
    assert errors_of(bundle_dict)


def test_rejects_duplicate_ids(bundle_dict):
    bundle_dict["events"][0]["id"] = bundle_dict["claims"][0]["id"]
    assert any("duplicate" in e or "dangling" in e for e in errors_of(bundle_dict))


def test_rejects_dangling_reference(bundle_dict):
    bundle_dict["events"][0]["claim_ids"].append(new_id())
    assert any("dangling" in e for e in errors_of(bundle_dict))


def test_rejects_claim_without_evidence(bundle_dict):
    cid = bundle_dict["claims"][0]["id"]
    bundle_dict["evidence_links"] = [e for e in bundle_dict["evidence_links"] if e["claim_id"] != cid]
    assert any("no EvidenceLink" in e for e in errors_of(bundle_dict))


def test_rejects_evidence_with_missing_source_or_fetch(bundle_dict):
    d1 = copy.deepcopy(bundle_dict)
    d1["evidence_links"][0]["source_id"] = new_id()
    assert any("missing source" in e for e in errors_of(d1))
    d2 = copy.deepcopy(bundle_dict)
    d2["evidence_links"][0]["source_fetch_id"] = new_id()
    assert any("missing source_fetch" in e for e in errors_of(d2))


def test_rejects_event_without_claim(bundle_dict):
    bundle_dict["events"][0]["claim_ids"] = []
    assert any("no supporting claim" in e for e in errors_of(bundle_dict))


def test_rejects_reversed_time_range(bundle_dict):
    tr = bundle_dict["events"][0]["time"]
    tr["start"], tr["end"] = tr["end"], tr["start"]
    assert any("ends before it starts" in e for e in errors_of(bundle_dict))


@pytest.mark.parametrize("numeric,text", [(None, None), (5.0, "five")])
def test_rejects_metric_value_xor(bundle_dict, numeric, text):
    m = bundle_dict["metric_snapshots"][0]
    m["value_numeric"], m["value_text"] = numeric, text
    assert any("exactly one of value_numeric/value_text" in e for e in errors_of(bundle_dict))


def test_rejects_unknown_causal_attribution(bundle_dict):
    bundle_dict["outcomes"][0]["causal_attribution"] = "caused"
    assert errors_of(bundle_dict)


def test_rejects_growth_episode_without_repository(bundle_dict):
    bundle_dict["growth_episodes"][0]["repository_id"] = bundle_dict["target"]["id"]
    assert any("does not reference a repository" in e for e in errors_of(bundle_dict))


def test_rejects_launch_episode_with_missing_event(bundle_dict):
    bundle_dict["launch_episodes"][0]["event_ids"] = [new_id()]
    assert any("dangling" in e for e in errors_of(bundle_dict))


def test_rejects_retained_artifact_without_permission(bundle_dict):
    bundle_dict["source_fetches"][0]["retained_artifact_path"] = "assets/copy.html"
    assert any("retained artifact" in e for e in errors_of(bundle_dict))


def test_rejects_excerpt_on_link_only_source(bundle_dict):
    reddit = next(s for s in bundle_dict["sources"] if s["surface_key"] == "reddit")
    link = copy.deepcopy(bundle_dict["evidence_links"][0])
    link["source_id"] = reddit["id"]
    link["source_fetch_id"] = next(f["id"] for f in bundle_dict["source_fetches"] if f["source_id"] == reddit["id"])
    link["id"] = new_id()
    link["excerpt"] = "copied text"
    bundle_dict["evidence_links"].append(link)
    assert any("forbids excerpts" in e for e in errors_of(bundle_dict))


def test_rejects_narrative_with_unknown_claim(bundle_dict):
    bundle_dict["narrative"]["thirty_second"]["claim_ids"] = [new_id()]
    assert any("narrative cites claim" in e for e in errors_of(bundle_dict))


def test_published_requires_human_verified_negative(bundle_dict):
    bundle_dict["claims"][0]["is_negative_sensitive"] = True
    assert validate_data(bundle_dict).ok
    assert any("negative-sensitive" in e for e in errors_of(bundle_dict, published=True))


def test_inferred_objects_need_claims(bundle_dict):
    bundle_dict["tactics"][0]["claim_ids"] = []
    assert any("inferred object without supporting claims" in e for e in errors_of(bundle_dict))


def test_warns_but_passes_on_gaps(bundle_dict):
    rep = validate_data(bundle_dict)
    assert rep.ok
    assert any("material evidence gap" in w for w in rep.warnings)


def test_bundle_has_no_full_content_fields(bundle_dict):
    """Source objects carry metadata + locator only; there is no field for page bodies."""
    for s in bundle_dict["sources"]:
        assert set(s) == {
            "id",
            "url",
            "canonical_url",
            "surface_key",
            "source_type",
            "title",
            "author",
            "published_at",
            "discovered_at",
            "policy",
        }
    for f in bundle_dict["source_fetches"]:
        assert f["retained_artifact_path"] is None
