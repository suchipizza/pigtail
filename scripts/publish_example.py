#!/usr/bin/env python3
"""Copy a publication-gated report into examples/reviewed/<slug>/ (PRD publication gate §14).

    uv run python scripts/publication_gate.py <run-dir>          # first: must end in PASS
    uv run python scripts/publish_example.py <run-dir> <slug> --title T --description D [--featured]

Only the gate's outputs are copied: report.html, public-report-bundle.json, publication-manifest.json,
plus metadata.json. The raw research-bundle.json, source-index.json, run.json, the audit and the
review file are never copied. The copy is refused unless the manifest is a current PASS for the
current inputs. There is no override.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from pigtail.publication import POLICY_VERSION
from pigtail.publication.runner import promote

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("slug")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", required=True)
    ap.add_argument("--featured", action="store_true")
    ap.add_argument(
        "--reviewed-by",
        choices=["ai", "human"],
        required=True,
        help="Who made the publication-review decisions (shown on the website).",
    )
    a = ap.parse_args()
    public = a.run_dir / "publication" / "public-report-bundle.json"
    kind = json.loads(public.read_text())["target"]["kind"] if public.exists() else None
    meta = {
        "slug": a.slug,
        "title": a.title,
        "description": a.description,
        "kind": kind,
        "featured": a.featured,
        "published_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bundle_schema_version": "0.1.0",
        "publication_policy_version": POLICY_VERSION,
        "reviewed_by": a.reviewed_by,
    }
    problems = promote(a.run_dir, ROOT / "examples" / "reviewed" / a.slug, meta)
    if problems:
        print("Not publishable:\n" + "\n".join(f"  - {p}" for p in problems))
        return 1
    print(f"published {a.slug} -> examples/reviewed/{a.slug}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
