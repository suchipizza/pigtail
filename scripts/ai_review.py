#!/usr/bin/env python3
"""Let an AI fill the pending decisions of a publication review (owner-authorized; costs API money).

    uv run python scripts/publication_gate.py <run-dir>           # creates publication-review.yaml
    uv run python scripts/ai_review.py <run-dir> --budget-usd 2    # AI decides pending items
    uv run python scripts/publication_gate.py <run-dir>           # gate re-checks everything

Decisions are recorded as made by an AI on the owner's behalf (never under a person's name), with
"[AI]" rationales. Items the model cannot decide within the allowed options stay pending for a person.
Source pages are re-read (respecting source policies and robots.txt) unless --no-fetch is given.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from pigtail.config import load_dotenv
from pigtail.policies.loader import load_policies
from pigtail.providers.base import Meter
from pigtail.providers.fetchers.web import WebFetcher
from pigtail.providers.models.anthropic import AnthropicModelProvider
from pigtail.publication.ai_review import ai_review


async def run(a: argparse.Namespace) -> int:
    meter = Meter(budget_usd=a.budget_usd)
    model = AnthropicModelProvider(a.model, os.environ.get("ANTHROPIC_API_KEY"), meter)
    fetcher = None if a.no_fetch else WebFetcher(meter)
    label = f"AI ({a.model}) on behalf of {a.on_behalf_of}; human review pending"
    try:
        s = await ai_review(
            a.run_dir, model, label, fetcher=fetcher, policies=load_policies(), budget_usd=a.budget_usd, meter=meter
        )
    finally:
        if fetcher:
            await fetcher.close()
    print(f"AI decided {s.decided} item(s); {s.skipped} left for a person. Cost ${s.cost_usd:.2f}.")
    print("Now re-run: uv run python scripts/publication_gate.py", a.run_dir)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--budget-usd", type=float, required=True, help="Stop deciding once this much was spent.")
    ap.add_argument("--on-behalf-of", default="the owner")
    ap.add_argument("--no-fetch", action="store_true", help="Do not re-read source pages.")
    load_dotenv()
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
