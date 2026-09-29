"""A labelled synthetic set for the asset rules (`assets-labelled-v1`; ADR-089 addendum 4).

Each example is one README (or release-notes) snippet with the true label of one asset, written
by hand for this purpose (invented projects, no real text). The negatives reproduce the error
types verifier M24 round 1 found with `assets-v1` on a hand-checked sample (about 56 %
precision): logos, banners and icons taken for screenshots, third-party docs links taken for a
docs site, "mentioned in chat" and "covered by this licence" taken for a press claim, a "VS Code"
heading and a plain data table taken for a comparison, development installs, multi-line
commands and other tools' installers taken for an install one-liner. The report measures each
rule's precision and recall on this set at run time (`measure`) and labels the asset findings
with it (below 0.70: "low reliability"). A synthetic set measures the rules against the known
error types; it is not a sample of real READMEs, and the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pigtail.forensics.facts import ASSETS, Project, detect_assets

SET_VERSION = "assets-labelled-v1"
_P = Project("tinyqueue", "[owner]", "tinyqueue.dev")


@dataclass(frozen=True)
class Example:
    asset: str
    text: str
    label: bool
    project: Project = _P


EXAMPLES: tuple[Example, ...] = (
    # held-out hard cases, written before looking at the rules' output: what a real README
    # also does (a diagram is not a screenshot; a docs host on another domain of the project; a
    # binary named differently from the repo; a benchmark under a "Speed" heading)
    Example("screenshots", "![architecture diagram](docs/architecture.png)", False),
    Example("screenshots", "![](docs/hero.png)", True),
    Example("docs_site", "Docs: https://docs.tinyqueue.io/", True),
    Example("install_one_liner", "```sh\ncargo install tq-cli\n```", True),
    Example("benchmarks", "## Speed\n\ntinyqueue processes 1.2M jobs/s on one core.", True),
    Example("featured_in_claim", "Featured on Product Hunt as #2 product of the day.", True),
    Example(
        "comparison_table",
        "## Why not Redis?\n\n| | tinyqueue | redis |\n|---|---|---|\n| deps | 0 | 1 |",
        True,
    ),
    Example("demo_media", "![](docs/preview.webp)", True),
    # demo media
    Example("demo_media", "![demo](docs/demo.gif)", True),
    Example("demo_media", "Watch it: https://www.youtube.com/watch?v=abc123", True),
    Example("demo_media", '<video src="assets/run.mp4" controls></video>', True),
    Example(
        "demo_media",
        "[![asciicast](https://asciinema.org/a/42.svg)](https://asciinema.org/a/42)",
        True,
    ),
    Example("demo_media", "![logo](assets/logo.png)", False),
    Example("demo_media", "See the docs for a walkthrough.", False),
    # screenshots
    Example("screenshots", "![Screenshot of the dashboard](docs/screenshot.png)", True),
    Example("screenshots", '<img src="docs/ui.png" alt="the queue view" width="600">', True),
    Example("screenshots", "![output](media/terminal-output.jpg)", True),
    Example("screenshots", "![logo](assets/logo.png)", False),
    Example("screenshots", '<img src="assets/banner.png" alt="tinyqueue banner">', False),
    Example("screenshots", '<img src="icon.png" width="64">', False),
    Example("screenshots", "![tinyqueue](assets/wordmark.svg)", False),
    Example("screenshots", "![build](https://img.shields.io/badge/build-passing-green.png)", False),
    Example(
        "screenshots", '<img src="https://example.org/sponsors/acme.png" alt="sponsor">', False
    ),
    # install one-liner
    Example("install_one_liner", "```sh\npip install tinyqueue\n```", True),
    Example("install_one_liner", "Install with `brew install tinyqueue`.", True),
    Example("install_one_liner", "curl -fsSL https://tinyqueue.dev/install.sh | sh", True),
    Example("install_one_liner", "```\nnpx tinyqueue init\n```", True),
    Example("install_one_liner", "```sh\nnpm install\nnpm run dev\n```", False),
    Example(
        "install_one_liner", "```sh\ndocker run -d \\\n  -p 8080:8080 tinyqueue/server\n```", False
    ),
    Example(
        "install_one_liner",
        "First install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`",
        False,
    ),
    Example("install_one_liner", "Then run tinyqueue with your config file.", False),
    Example("install_one_liner", "```sh\npip install -r requirements.txt\n```", False),
    # benchmarks
    Example(
        "benchmarks", "## Benchmarks\n\n| queue | ops/s |\n|---|---|\n| tinyqueue | 1.2M |", True
    ),
    Example("benchmarks", "In our benchmark, tinyqueue handles 3x more jobs per second.", True),
    Example("benchmarks", "## Performance\n\nIt is fast.", True),
    Example("benchmarks", "We have no benchmarks yet.", False),
    Example("benchmarks", "## Usage\n\nRun the worker.", False),
    # docs site
    Example("docs_site", "Documentation: https://docs.tinyqueue.dev/", True),
    Example("docs_site", "Read the docs at https://tinyqueue.readthedocs.io/en/latest/", True),
    Example("docs_site", "Full docs: https://tinyqueue.dev/docs/getting-started", True),
    Example("docs_site", "See https://docs.python.org/3/library/queue.html for queues.", False),
    Example("docs_site", "Get an API key at https://docs.example-llm.com/keys", False),
    Example("docs_site", "Feedback: https://docs.google.com/forms/d/e/abc/viewform", False),
    Example("docs_site", "See docs/USAGE.md in this repo.", False),
    Example("docs_site", "Uses https://docs.rs/serde for parsing.", False),
    # comparison table
    Example(
        "comparison_table", "| | tinyqueue | other |\n|---|---|---|\n| retries | ✅ | ❌ |", True
    ),
    Example(
        "comparison_table",
        "## Comparison\n\n| feature | tinyqueue | celery |\n|---|---|---|\n| size | 1 | 9 |",
        True,
    ),
    Example(
        "comparison_table",
        "## tinyqueue vs celery\n\n| | a | b |\n|---|---|---|\n| x | 1 | 2 |",
        True,
    ),
    Example(
        "comparison_table",
        "### VS Code (GitHub Copilot)\n\n| key | value |\n|---|---|\n| a | b |",
        False,
    ),
    Example(
        "comparison_table",
        "## Memory types\n\n| type | lifetime |\n|---|---|\n| short | 1 h |",
        False,
    ),
    Example(
        "comparison_table", "## Comparison\n\nWe compare it with others in the blog post.", False
    ),
    # featured-in claim
    Example("featured_in_claim", "As seen in the Changelog newsletter and on Hacker News.", True),
    Example("featured_in_claim", "Featured in Console.dev's weekly digest.", True),
    Example("featured_in_claim", "People mentioned in chat that it works.", False),
    Example("featured_in_claim", "Files not covered by this license are listed below.", False),
    Example("featured_in_claim", "Featured in the examples folder.", False),
)


def measure() -> dict[str, Any]:
    """Precision and recall of each asset rule on the set (deterministic)."""
    out: dict[str, Any] = {}
    for a in ASSETS:
        tp = fp = fn = tn = 0
        for ex in EXAMPLES:
            if ex.asset != a:
                continue
            hit = bool(detect_assets(ex.text, ex.project).get(a))
            if hit and ex.label:
                tp += 1
            elif hit:
                fp += 1
            elif ex.label:
                fn += 1
            else:
                tn += 1
        n = tp + fp + fn + tn
        out[a] = {
            "examples": n,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": None if tp + fp == 0 else round(tp / (tp + fp), 3),
            "recall": None if tp + fn == 0 else round(tp / (tp + fn), 3),
        }
    return {
        "set": SET_VERSION,
        "per_asset": out,
        "note": "synthetic labelled set of the known error types; not a sample of real READMEs",
    }
