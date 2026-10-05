"""Serialize Research Bundles and derived convenience files (spec §22)."""

from __future__ import annotations

import json
from pathlib import Path

from pigtail.bundle.models import ResearchBundle
from pigtail.bundle.validator import ValidationReport, validate_data
from pigtail.errors import BundleValidationError


def bundle_to_dict(bundle: ResearchBundle) -> dict:
    return bundle.model_dump(mode="json")


def dump_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def validate_bundle(bundle: ResearchBundle) -> ValidationReport:
    return validate_data(bundle_to_dict(bundle))


def write_bundle(bundle: ResearchBundle, path: Path) -> ValidationReport:
    """Validate, then write. Never writes an invalid bundle under the primary filename (PRD E8)."""
    data = bundle_to_dict(bundle)
    report = validate_data(data)
    if not report.ok:
        debug = path.with_name("research-bundle.invalid.json")
        debug.write_text(dump_json(data), encoding="utf-8")
        raise BundleValidationError(
            f"Generated bundle failed validation; diagnostic copy written to {debug}", errors=report.errors
        )
    path.write_text(dump_json(data), encoding="utf-8")
    return report


def source_index(bundle: ResearchBundle) -> dict:
    """A convenience projection of sources + fetches + evidence (spec §22). The Bundle stays authoritative."""
    fetches: dict[str, list[dict]] = {}
    for f in bundle.source_fetches:
        fetches.setdefault(f.source_id, []).append(
            {"retrieved_at": f.retrieved_at, "status": f.status, "content_hash": f.content_hash}
        )
    uses: dict[str, int] = {}
    for el in bundle.evidence_links:
        uses[el.source_id] = uses.get(el.source_id, 0) + 1
    return {
        "bundle_id": bundle.bundle_id,
        "schema_version": bundle.schema_version,
        "sources": [
            {
                "id": s.id,
                "url": s.url,
                "title": s.title,
                "surface": s.surface_key,
                "source_type": s.source_type,
                "author": s.author,
                "published_at": s.published_at,
                "coverage_tier": s.policy.coverage_tier,
                "public_display_mode": s.policy.public_display_mode,
                "fetches": fetches.get(s.id, []),
                "evidence_link_count": uses.get(s.id, 0),
            }
            for s in bundle.sources
        ],
    }
