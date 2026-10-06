"""Golden tests over published examples: structure and known facts, never exact prose (spec §48.3).

Published examples are publication-gate projections (public-report-bundle.json), not raw Bundles.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pigtail.publication.verify import verify_example_dir, verify_examples_root
from pigtail.renderer.render import render_public_html

from .known_facts import KNOWN_FACTS

ROOT = Path(__file__).resolve().parents[2] / "examples" / "reviewed"
EXAMPLES = sorted(p for p in ROOT.iterdir() if p.is_dir()) if ROOT.exists() else []


def load(p: Path) -> dict:
    return json.loads((p / "public-report-bundle.json").read_text())


def test_golden_set_shape():
    """PRD §16: ~4-5 examples, ≥2 exercising the OSS visualization, ≥1 non-OSS."""
    if not EXAMPLES:
        pytest.skip("no reviewed examples yet")
    kinds = [json.loads((p / "metadata.json").read_text())["kind"] for p in EXAMPLES]
    if len(EXAMPLES) < 4:
        # Launch requirement, not yet met: Plausible, PocketBase, Superhuman and Tally are waiting for the
        # owner's publication review (IMPLEMENTATION_STATUS.md, "Owner decisions needed").
        pytest.skip(f"launch needs >=4 published examples; {len(EXAMPLES)} published, 4 awaiting publication review")
    assert len(EXAMPLES) >= 4
    assert sum(k == "repository" for k in kinds) >= 2
    assert any(k != "repository" for k in kinds)


def test_examples_root_passes_publication_checks():
    if not EXAMPLES:
        pytest.skip("no published examples yet")
    assert verify_examples_root(ROOT) == []


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_example_passed_the_publication_gate(ex: Path):
    assert verify_example_dir(ex) == []


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_metadata_contract(ex: Path):
    meta = json.loads((ex / "metadata.json").read_text())
    assert set(meta) == {
        "slug",
        "title",
        "description",
        "kind",
        "featured",
        "published_at",
        "bundle_schema_version",
        "publication_policy_version",
    }
    assert meta["slug"] == ex.name
    assert meta["bundle_schema_version"] == load(ex)["schema_version"]


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda p: p.name)
def test_structural_invariants(ex: Path):
    b = load(ex)
    # Every public claim is traceable to a successfully read source (or a manual verification).
    ok_fetch = {f["id"] for f in b["source_fetches"] if f["status"] == "success"}
    linked = {el["claim_id"] for el in b["evidence_links"] if el["source_fetch_id"] in ok_fetch}
    assert all(c["id"] in linked or c["manually_verified"] for c in b["claims"] if c["status"] == "active")
    # People and account names stay out of published examples.
    assert "people" not in b and all(s["author"] is None for s in b["sources"])
    # Temporal association is never promoted to causation on growth episodes.
    assert all(g["causal_attribution"] in ("weakly_associated", "unknown") for g in b["growth_episodes"])
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
def test_report_renders_from_public_bundle(ex: Path):
    html = render_public_html(load(ex))
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
