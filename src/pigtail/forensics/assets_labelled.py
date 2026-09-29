"""The asset rules' precision: the real-data measurement and a labelled synthetic regression set
(ADR-089 addenda 4 and 5).

**Real data** (`REAL_PRECISION`): the precision verifier M24 round 2 measured by hand on one
brief's full run under `assets-v2` (65 `present` asset facts, 60 decided). It is a declared
constant, printed in the report as the rules' measured precision; the rules were tightened after
it (`assets-v3`) and have not been re-measured on real data.

**Synthetic set** (`EXAMPLES`, `assets-labelled-v2`): README snippets of invented projects with
the true label of one asset, reproducing the error types both verifier rounds found (logos,
icons, diagrams and covers taken for screenshots; third-party docs; "mentioned in chat"; tables
of a project's own types, plans, channels or platforms taken for comparisons; the word
"benchmark" without a result; dev installs, multi-line commands, other tools' installers;
placeholder video URLs) plus a few held-out hard cases. It guards the rules against regressions;
it is not a sample of real READMEs, and the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pigtail.forensics.facts import ASSETS, Project, detect_assets

SET_VERSION = "assets-labelled-v2"
REAL_PRECISION: dict[str, Any] = {
    "source": "verifier hand check round 2, n=65 (60 decided)",
    "rule_measured": "assets-v2",
    "overall": 0.78,
    "per_asset": {
        "install_one_liner": {"precision": 0.95, "decided": 19},
        "demo_media": {"precision": 0.80, "decided": 5},
        "screenshots": {"precision": 0.71, "decided": 14},
        "benchmarks": {"precision": 0.70, "decided": 10},
        "comparison_table": {"precision": 0.56, "decided": 9},
        "docs_site": {"precision": None, "decided": 3, "note": "3/3 correct, too few to state"},
        "featured_in_claim": {"precision": None, "decided": 0, "note": "none present"},
    },
}
_P = Project("tinyqueue", "[owner]", "tinyqueue.dev")


@dataclass(frozen=True)
class Example:
    asset: str
    text: str
    label: bool
    project: Project = _P


_CMP_OK_1 = "| | tinyqueue | other | celery |\n|---|---|---|---|\n| retries | ✅ | ❌ | ✅ |"
_CMP_OK_2 = "## Comparison\n\n| feature | tinyqueue | celery |\n|---|---|---|\n| size | 1 | 9 |"
_CMP_OK_3 = "## vs celery\n\n| queue | ops/s |\n|---|---|\n| tinyqueue | 9 |\n| celery | 4 |"
_CMP_OK_4 = (
    "## Why not Redis?\n\n| | tinyqueue | redis | rq |\n|---|---|---|---|\n| deps | 0 | 1 | 2 |"
)
_CMP_NO_1 = "### VS Code (GitHub Copilot)\n\n| key | value |\n|---|---|\n| a | b |"
_CMP_NO_2 = "## Memory types\n\n| type | lifetime |\n|---|---|\n| short | 1 h |"
_CMP_NO_3 = "## Comparison\n\nWe compare it with others in the blog post."
_CMP_NO_4 = "| | Free | Pro | Team |\n|---|---|---|---|\n| jobs | 10 | ✅ | ✅ |"
_CMP_NO_5 = "| channel | status |\n|---|---|\n| stable | ✅ |\n| beta | ❌ |"
_CMP_NO_6 = "| platform | supported |\n|---|---|\n| Linux | ✅ |\n| Windows | ❌ |"
_BENCH_OK_1 = "## Benchmarks\n\n| queue | ops/s |\n|---|---|\n| tinyqueue | 1.2M ops/s |"

EXAMPLES: tuple[Example, ...] = (
    # held-out hard cases, written before looking at the rules' output
    Example("screenshots", "![architecture diagram](docs/architecture.png)", False),
    Example("screenshots", "![](docs/hero.png)", True),
    Example("docs_site", "Docs: https://docs.tinyqueue.io/", True),
    Example("install_one_liner", "```sh\ncargo install tq-cli\n```", True),
    Example("benchmarks", "## Speed\n\ntinyqueue processes 1.2M jobs/s on one core.", True),
    Example("featured_in_claim", "Featured on Product Hunt as #2 product of the day.", True),
    Example("comparison_table", _CMP_OK_4, True),
    Example("demo_media", "![](docs/preview.webp)", True),
    # demo media
    Example("demo_media", "![demo](docs/demo.gif)", True),
    Example("demo_media", "Watch it: https://www.youtube.com/watch?v=abc123", True),
    Example("demo_media", '<video src="assets/run.mp4" controls></video>', True),
    Example(
        "demo_media", "[![cast](https://asciinema.org/a/42.svg)](https://asciinema.org/a/42)", True
    ),
    Example("demo_media", "![logo](assets/logo.png)", False),
    Example("demo_media", "See the docs for a walkthrough.", False),
    Example("demo_media", "e.g. `tinyqueue add https://youtube.com/watch?v=xxx`", False),
    # screenshots
    Example("screenshots", "![Screenshot of the dashboard](docs/screenshot.png)", True),
    Example("screenshots", '<img src="docs/ui.png" alt="the queue view" width="600">', True),
    Example("screenshots", "![output](media/terminal-output.jpg)", True),
    Example("screenshots", "![logo](assets/logo.png)", False),
    Example("screenshots", '<img src="assets/banner.png" alt="tinyqueue banner">', False),
    Example("screenshots", '<img src="icon.png" width="64">', False),
    Example("screenshots", '<img src="docs/agent.png" width="48">', False),
    Example("screenshots", "![cover](docs/cover.jpg)", False),
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
    Example("install_one_liner", "```sh\ndocker run -d \\\n  -p 8080:8080 tq/server\n```", False),
    Example(
        "install_one_liner", "Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`", False
    ),
    Example("install_one_liner", "Then run tinyqueue with your config file.", False),
    Example("install_one_liner", "```sh\npip install -r requirements.txt\n```", False),
    # benchmarks
    Example("benchmarks", _BENCH_OK_1, True),
    Example("benchmarks", "In our benchmark, tinyqueue handles 3x more jobs per second.", True),
    Example("benchmarks", "## Performance\n\nIt is fast.", False),
    Example("benchmarks", "We have no benchmarks yet.", False),
    Example("benchmarks", "Strong on benchmarks, weak on docs.", False),
    Example(
        "benchmarks", "```sh\n# For the benchmark suite\npip install tinyqueue[bench]\n```", False
    ),
    Example("benchmarks", "## Status\n\nThis is not a benchmark.", False),
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
    # comparison table: the project against named other tools only
    Example("comparison_table", _CMP_OK_1, True),
    Example("comparison_table", _CMP_OK_2, True),
    Example("comparison_table", _CMP_OK_3, True),
    Example("comparison_table", _CMP_NO_1, False),
    Example("comparison_table", _CMP_NO_2, False),
    Example("comparison_table", _CMP_NO_3, False),
    Example("comparison_table", _CMP_NO_4, False),
    Example("comparison_table", _CMP_NO_5, False),
    Example("comparison_table", _CMP_NO_6, False),
    # featured-in claim
    Example("featured_in_claim", "As seen in the Changelog newsletter and on Hacker News.", True),
    Example("featured_in_claim", "Featured in Console.dev's weekly digest.", True),
    Example("featured_in_claim", "People mentioned in chat that it works.", False),
    Example("featured_in_claim", "Files not covered by this license are listed below.", False),
    Example("featured_in_claim", "Featured in the examples folder.", False),
)


def measure() -> dict[str, Any]:
    """Precision and recall of each asset rule on the synthetic set (deterministic), with the
    real-data measurement beside it."""
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
        out[a] = {
            "examples": tp + fp + fn + tn,
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
        "real": REAL_PRECISION,
    }
