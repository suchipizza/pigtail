"""Deterministic research gaps. Absence of evidence is recorded as a gap, never as a negative claim."""

from __future__ import annotations

from pigtail.research.builder import BundleBuilder


def record_standard_gaps(
    b: BundleBuilder,
    *,
    is_repo: bool,
    surfaces_seen: set[str],
    link_only: dict[str, int],
    product_hunt_api: bool = False,
) -> None:
    kinds = {c["claim_kind"] for c in b.c["claims"]}
    if not b.narrative.get("first_users") and "first_users" not in kinds:
        b.gap(
            "unknown_early_user_acquisition",
            "Public sources did not say who the first users were or how they were reached.",
            severity="material",
        )
    if not any(
        m["metric_key"] not in ("github_stars", "github_forks", "github_stars_window") for m in b.c["metric_snapshots"]
    ):
        b.gap(
            "unknown_metric",
            "No historical user, revenue or adoption metrics were found in public sources.",
            severity="material" if not is_repo else "minor",
        )
    if not any(e["event_type"] == "product_hunt_launch" for e in b.c["events"]):
        b.gap(
            "surface_not_covered",
            "No Product Hunt launch was found. Product Hunt's API was checked with your token, but it cannot "
            "search by name, so a launch under a different name may exist without being shown."
            if product_hunt_api
            else "No Product Hunt launch was found in permitted sources. Product Hunt's API was not used (it needs "
            "your own token), so a launch may exist without being shown.",
            surface_key="product_hunt",
        )
    if "reddit" in link_only:
        b.gap(
            "link_only_surface",
            f"{link_only['reddit']} Reddit link(s) were found but not read: Reddit content is link-only under "
            "Pigtail's source policy.",
            surface_key="reddit",
        )
    if "x" in link_only:
        b.gap(
            "link_only_surface",
            f"{link_only['x']} X/Twitter link(s) were found but not read (link-only policy).",
            surface_key="x",
        )
    if "product_hunt" in link_only:
        b.gap(
            "link_only_surface",
            f"{link_only['product_hunt']} Product Hunt page(s) were found but not read (link-only policy)"
            + ("; their addresses were looked up through Product Hunt's API." if product_hunt_api else "."),
            surface_key="product_hunt",
        )
    for o in b.c["outcomes"]:
        if o["causal_attribution"] in ("weakly_associated", "unknown"):
            b.gap(
                "unsupported_causality",
                "Several outcomes are shown only as observed after an event; no source measured their cause.",
                severity="minor",
            )
            break
    failed = [f for f in b.c["source_fetches"] if f["status"] not in ("success", "skipped_policy")]
    if failed:
        b.gap(
            "source_inaccessible",
            f"{len(failed)} source(s) could not be fetched or parsed (blocked, missing "
            "or unreadable); their content is not used.",
            severity="minor",
        )


def insufficient(b: BundleBuilder, *, is_repo: bool) -> bool:
    content_claims = [
        c
        for c in b.c["claims"]
        if c["claim_kind"]
        not in (
            "star_history",
            "star_growth_episode",
            "release_count",
            "metric",
            "release",
            "repository_created",
        )
    ]
    if is_repo:
        return not b.c["metric_snapshots"] and len(content_claims) < 3
    return len(content_claims) < 6 or len(b.c["events"]) < 2
