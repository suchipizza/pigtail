"""Plain-language names and one-sentence definitions of the pattern step's features (M25, ADR-091
addendum 1), for plans and reports read by a person. The `pattern.MC-xx` names and definitions
mirror the pattern registry `schemas/mechanisms/candidates-v0.json` (`name`, `description`;
codebook 0.4.0 §7, `docs/methodology/mechanisms/candidates.md`) so the app does not need
`schemas/`; `tests/unit/test_m25_plan.py` fails when they drift. Assets and README sections
follow the report-facts rules (`pigtail.forensics.facts`, codebook §5), modules codebook §8.
"""

from __future__ import annotations

# registry mirror (candidates-v0.json `cards[].name` / `.description`)
PATTERNS: dict[str, tuple[str, str]] = {
    "MC-01": ("First-party launch post on Hacker News (Show HN / Launch HN)",
              "Maker announces the project on HN in a first-party post; an attention burst "
              "follows."),
    "MC-02": ("HN front-page exposure",
              "An HN story about the repo (any poster) reaches the front page; the exposure "
              "produces a short-run star jump."),
    "MC-03": ("HN posting in the 12-17 UTC window",
              "The first HN story about the repo is posted 12:00-17:00 UTC and gets more early "
              "stars than comparable posts at other times."),
    "MC-04": ("Social-media posts linking the repo",
              "Posts on social platforms linking the repo raise stars, with a smaller effect on "
              "contributors."),
    "MC-05": ("Launch week (multi-day announcement campaign)",
              "One major feature announced per day over consecutive days, with a planned channel "
              "schedule and community amplification."),
    "MC-06": ("Disclosed paid social promotion alongside a launch",
              "Disclosed paid promotion on a social platform runs alongside a community launch; "
              "together they push the repo onto GitHub's trending page."),
    "MC-07": ("Active multi-channel first-party promotion",
              "The maker actively promotes on several own and community channels around launch "
              "rather than relying on one post."),
    "MC-08": ("Early cross-community breadth",
              "Within days of the first burst, several distinct communities or publications "
              "pick up the repo independently (breadth rather than one broadcast). Currently a "
              "spread pattern; no actionable precondition known."),
    "MC-09": ("Novelty positioning",
              "The project positions itself as novel; this attracts attention but comes with "
              "lower long-run participation."),
    "MC-10": ("Release-driven attention", "New releases produce attention bursts."),
    "MC-11": ("'Try it now' asset at launch",
              "At launch the project offers a zero-friction trial (hosted demo or one-command "
              "install), which turns attention into adoption. Basis is a platform rule only."),
    "MC-12": ("Star inflation suspected from aggregate anomalies (fake-star proxy; "
              "anti-pattern; detection and contrast only)",
              "Purchased or coordinated fake stars inflate star counts. Ruled out by PRD §4; "
              "never recommended (F10, R12.4). Since 0.4.0 detected by pigtail's aggregate "
              "anomaly checks (anomaly-v0, OM v2.1 §4), a proxy for the account-level campaigns "
              "the literature describes; unvalidated, no precision or recall measured (OM §4.5)."),
    "MC-13": ("Vote solicitation on HN (anti-pattern; detection and contrast only)",
              "The maker asks people to upvote or comment on an HN launch, which HN guidelines "
              "prohibit. Never recommended."),
}  # fmt: skip
NOT_RECOMMENDABLE = frozenset({"pattern.MC-12", "pattern.MC-13"})

OTHER: dict[str, tuple[str, str]] = {
    "asset.demo_media": ("Demo GIF or video in the README",
                         "The README at launch embeds or links an animated demo or a video."),
    "asset.screenshots": ("Screenshots in the README",
                          "The README at launch shows static images of the product's UI or "
                          "output (not banners, icons or logos)."),
    "asset.install_one_liner": ("One-command install",
                                "The README at launch gives a single install command (pip, npm, "
                                "brew, cargo, curl | sh, ...)."),
    "asset.benchmarks": ("Benchmarks",
                         "The README at launch has a benchmark or performance section or claim."),
    "asset.docs_site": ("Documentation site",
                        "The README at launch links a documentation site on the project's own "
                        "host."),
    "asset.comparison_table": ("Comparison table",
                               "The README at launch has a table comparing the project with "
                               "alternatives."),
    "asset.featured_in_claim": ("'Featured in' claim",
                                "The README at launch says the project was featured in a "
                                "publication or newsletter."),
    "readme.quick_start_section": ("Quick-start section",
                                   "The README at launch has a quick-start / getting-started / "
                                   "usage heading."),
    "readme.features_section": ("Features section",
                                "The README at launch has a features / highlights heading."),
    "launch.show_hn": ("Show HN post", "The project was launched with a Show HN post."),
    "launch.launch_hn": ("Launch HN post", "The project was launched with a Launch HN post."),
    "launch.product_hunt": ("Product Hunt launch",
                            "The project was launched on Product Hunt (a confirmed launch)."),
    "launch.bluesky_maintainer_post": ("Maintainer's Bluesky launch post",
                                       "A declared maintainer account announced the launch on "
                                       "Bluesky."),
    "launch.release_launch": ("Release announced as a launch",
                              "A GitHub release worded as a launch marks the launch."),
    "amplifier.organization": ("Owned by an organisation account",
                               "The repository belongs to a GitHub organisation, not a personal "
                               "account (a condition of the project)."),
    "novelty_claim": ("Claims to be novel",
                      "In its own words at launch, the project claims to be new in kind, a new "
                      "approach or a new combination."),
}  # fmt: skip

MODULES: dict[str, str] = {
    "ai_hype": "AI / LLM project",
    "b2b_oss_saas": "open source with a paid or hosted offering",
    "chinese_ecosystem": "Chinese-language ecosystem",
    "cli_devtools": "command-line / developer tool",
    "corporate_backed": "backed by a company or foundation",
    "relaunch_pivot": "relaunch or pivot",
}


def describe(feature: str) -> tuple[str, str]:
    """(plain-language name, one-sentence definition) of a feature id; unknown ids echo."""
    if feature.startswith("pattern."):
        pid = feature.split(".", 1)[1]
        if pid in PATTERNS:
            return PATTERNS[pid]
    if feature.startswith("module_active."):
        m = feature.split(".", 1)[1]
        if m in MODULES:
            return (f"Module: {MODULES[m]}",
                    f"The case is in the `{m}` module (codebook §8; a condition of the project, "
                    "not a practice).")  # fmt: skip
    return OTHER.get(feature, (feature, "no definition recorded"))


def title(feature: str) -> str:
    """`Plain name [feature id]`."""
    return f"{describe(feature)[0]} [{feature}]"
