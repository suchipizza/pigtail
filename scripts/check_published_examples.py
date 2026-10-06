#!/usr/bin/env python3
"""Fail if anything under examples/reviewed/ is not a current, passed publication (PRD §14).

Checks every example directory: required files present, raw/internal files absent, manifest status
PASS with no unresolved findings, current policy version, hashes matching the files, projection
invariants, and the third-party content notice (examples/reviewed/NOTICE.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

from pigtail.publication.verify import verify_examples_root

ROOT = Path(__file__).resolve().parents[1] / "examples" / "reviewed"

if __name__ == "__main__":
    problems = verify_examples_root(ROOT)
    for p in problems:
        print(f"  - {p}")
    print("published examples: OK" if not problems else f"published examples: {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
