"""Golden tests over reviewed examples: structure and known facts, never exact prose (spec §48.3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pigtail.bundle.models import ResearchBundle
from pigtail.bundle.validator import validate_data
from pigtail.renderer.render import render_html

from .known_facts import KNOWN_FACTS

ROOT = Path(__file__).resolve().parents[2] / "examples" / "reviewed"
EXAMPLES = sorted(p for p in ROOT.iterdir() if (p / "research-bundle.json").exists()) if ROOT.exists() else []


def load(p: Path) -> dict:
    return json.loads((p / "research-bundle.json").read_text())


def test_golden_set_shape():
    """PRD §16: ~4-5 examples, ≥2 exercising the OSS visualization, ≥1 non-OSS."""
    if not EXAMPLES:
        pytest.skip("no reviewed examples yet")
    kinds = [json.loads((p / "metadata.json").read_text())["kind"] for p in EXAMPLES]
    assert len(EXAMPLES) >= 4
    assert sum(k == "repository" for k in kinds) >= 2
    assert any(k != "repository" for k in kinds)


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_example_is_valid_for_publication(ex: Path):
    rep = validate_data(load(ex), published=True)
    assert rep.ok, rep.errors


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_metadata_contract(ex: Path):
    meta = json.loads((ex / "metadata.json").read_text())
    assert set(meta) == {"slug", "title", "description", "kind", "featured", "published_at", "bundle_schema_version"}
    assert meta["slug"] == ex.name
    assert meta["bundle_schema_version"] == load(ex)["schema_version"]


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_structural_invariants(ex: Path):
    b = load(ex)
    # Every material claim is traceable to a source fetch.
    fetch_ids = {f["id"] for f in b["source_fetches"]}
    linked = {el["claim_id"] for el in b["evidence_links"] if el["source_fetch_id"] in fetch_ids}
    assert all(c["id"] in linked for c in b["claims"] if c["status"] == "active")
    # Temporal association is never promoted to causation on growth episodes.
    assert all(g["causal_attribution"] in ("weakly_associated", "unknown") for g in b["growth_episodes"])
    # No third-party content archived.
    assert all(f["retained_artifact_path"] is None for f in b["source_fetches"])
    # Narrative exists and is cited.
    assert b["narrative"]["thirty_second"] and b["narrative"]["thirty_second"]["claim_ids"]
    # Unexplained growth episodes are reported as gaps, not hidden.
    for g in b["growth_episodes"]:
        if not g["related_event_ids"]:
            assert "No high-confidence public event found" in g["summary"]
    if b["target"]["kind"] == "repository":
        assert b["repositories"] and b["repositories"][0]["star_history_quality"] in ("exact", "sampled")
        assert any(m["metric_key"] == "github_stars" for m in b["metric_snapshots"])
        assert any(e["event_type"] == "release" for e in b["events"]) or any(
            g["gap_type"] == "no_releases" for g in b["gaps"]
        )


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_report_renders_from_bundle(ex: Path):
    html = render_html(ResearchBundle.model_validate(load(ex)))
    assert not re.search(r"<script[^>]+src=", html)
    if load(ex)["repositories"]:
        assert 'id="starChart"' in html


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_known_facts(ex: Path):
    facts = KNOWN_FACTS.get(ex.name)
    if not facts:
        pytest.skip("no known facts recorded")
    b = load(ex)
    statements = " ".join(c["statement"] for c in b["claims"]).lower()
    for needle in facts.get("claims_mention", []):
        assert needle.lower() in statements, f"expected a claim mentioning {needle!r}"
    urls = " ".join(s["canonical_url"] for s in b["sources"])
    for u in facts.get("sources_include", []):
        assert u in urls, f"expected source {u}"
    types = {e["event_type"] for e in b["events"]}
    for t in facts.get("event_types", []):
        assert t in types, f"expected an event of type {t}"
    for key, value, period in facts.get("metrics", []):
        assert any(
            key in m["metric_key"] and m["value_numeric"] == value and (m["time"]["start"] or "").startswith(period)
            for m in b["metric_snapshots"]
        ), f"expected metric {key}={value} in {period}"
    if "repo_created" in facts:
        assert b["repositories"][0]["created_at"].startswith(facts["repo_created"])
