#!/usr/bin/env python3
"""Fill pending "generic source title" review items with each page's own heading (no API cost).

    uv run python scripts/publication_gate.py <run-dir>        # flags titles like "Hatchet"
    uv run python scripts/fill_source_titles.py <run-dir>      # reads the real headings
    uv run python scripts/publication_gate.py <run-dir>        # gate re-checks everything

Pages are re-read respecting source policies and robots.txt.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pigtail.policies.loader import load_policies
from pigtail.providers.base import Meter
from pigtail.providers.fetchers.web import WebFetcher
from pigtail.publication.ai_review import fill_source_titles


async def run(run_dir: Path) -> int:
    fetcher = WebFetcher(Meter())
    try:
        n = await fill_source_titles(run_dir, fetcher, load_policies())
    finally:
        await fetcher.close()
    print(f"Filled {n} source title(s). Now re-run: uv run python scripts/publication_gate.py {run_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    return asyncio.run(run(ap.parse_args().run_dir))


if __name__ == "__main__":
    raise SystemExit(main())
