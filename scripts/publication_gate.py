#!/usr/bin/env python3
"""Pigtail publication gate (internal). Run it on a Pigtail output directory before publishing.

    uv run python scripts/publication_gate.py <run-dir> [--review <publication-review.yaml>]

Writes <run-dir>/publication/: public-report-bundle.json, report.html, publication-audit.json,
publication-manifest.json and, when human decisions are needed, publication-review.yaml.
The original research-bundle.json, source-index.json, run.json and report.html are not touched.
Exit code: 0 PASS, 1 NEEDS_REVIEW, 2 BLOCKED, 3 input problem. There is no force option.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pigtail.publication.runner import GateInputError, run_gate

EXIT = {"PASS": 0, "NEEDS_REVIEW": 1, "BLOCKED": 2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument(
        "--review", type=Path, default=None, help="Review file (default: <run-dir>/publication/publication-review.yaml)"
    )
    a = ap.parse_args()
    try:
        run = run_gate(a.run_dir, a.review)
    except GateInputError as exc:
        print(f"Error: {exc}")
        return 3
    res = run.result
    for step in res.audit.steps:
        print(f"{step.name:<40} {step.result}")
    print()
    s = res.audit.summary
    print(
        f"Findings: {s.dropped} dropped, {s.redacted} redacted, {s.relabeled} relabeled, {s.truncated} truncated, "
        f"{s.requires_review} review, {s.blocked} block; {res.unresolved} unresolved"
    )
    print(f"Publication status: {res.status}")
    print(f"Audit: {run.out_dir / 'publication-audit.json'}")
    review = a.review or run.out_dir / "publication-review.yaml"
    if res.status != "PASS" and review.exists():
        print(f"Review: {review}")
    if res.status == "BLOCKED":
        for f in res.audit.findings:
            if f.action == "BLOCK" and f.resolution is None:
                print(f"  {f.finding_id} {f.rule_id}: {f.reason}")
    return EXIT[res.status]


if __name__ == "__main__":
    raise SystemExit(main())
