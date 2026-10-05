"""Shared fixtures: a small, fully valid Research Bundle built through the real BundleBuilder."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime

import pytest

from pigtail.bundle.models import ResearchBundle
from pigtail.domain.ids import new_id
from pigtail.domain.time import day_range, month_range
from pigtail.policies.loader import default_registry
from pigtail.research.builder import BundleBuilder, EvidenceSpec


def build_fixture_bundle() -> ResearchBundle:
    pol = default_registry()
    target = {
        "id": new_id(),
        "kind": "repository",
        "name": "Example",
        "canonical_url": "https://github.com/acme/example",
        "domain": "example.dev",
        "description": "An example project.",
        "primary_repository_id": None,
        "external_ids": [{"namespace": "domain", "value": "example.dev", "url": "https://example.dev"}],
        "aliases": ["acme/example"],
    }
    b = BundleBuilder(target=target, model_provider="test", model_id="fixture")
    gpol = pol.policy_for("https://github.com/acme/example", "github")
    wpol = pol.policy_for("https://example.dev/blog/launch", "web")
    rpol = pol.policy_for("https://www.reddit.com/r/x/comments/1", "reddit")

    repo_src = b.add_source(
        "https://api.github.com/repos/acme/example",
        surface_key="github",
        source_type="repository_api",
        policy=gpol,
        title="GitHub API: acme/example",
    )
    repo_f = b.add_fetch(repo_src, status="success", http_status=200, content_hash="sha256:" + "0" * 64)
    repo = b.add(
        "repositories",
        {
            "provider": "github",
            "owner": "acme",
            "name": "example",
            "url": "https://github.com/acme/example",
            "external_id": "123",
            "created_at": "2024-01-02T10:00:00Z",
            "default_branch": "main",
            "language": "Python",
            "license": "MIT",
            "is_archived": False,
            "observed_at": "2026-10-01T00:00:00Z",
            "current": {"stars": 1500, "forks": 40, "watchers": 10, "open_issues": 3, "contributors": 12},
            "star_history_quality": "exact",
            "claim_ids": [],
        },
    )
    b.target["primary_repository_id"] = repo["id"]
    repo_ref = {"type": "repository", "id": repo["id"]}
    c_created = b.add_claim(
        "The GitHub repository acme/example was created on January 02, 2024.",
        kind="repository_created",
        time=day_range(date(2024, 1, 2)),
        subject_ref=repo_ref,
        evidence=[
            EvidenceSpec(
                repo_src["id"], repo_f["id"], "third_party_measured", "independent_direct", "json_path", "$.created_at"
            )
        ],
    )
    repo["claim_ids"].append(c_created)

    hist_src = b.add_source(
        "https://api.github.com/repos/acme/example/stargazers/history",
        surface_key="github",
        source_type="star_history_api",
        policy=gpol,
        title="GitHub star history",
    )
    hist_f = b.add_fetch(hist_src, status="success", http_status=200, content_hash="sha256:" + "1" * 64)
    c_series = b.add_claim(
        "GitHub star history gives acme/example a daily star count.",
        kind="star_history",
        time={"start": "2024-01-02T00:00:00Z", "end": "2024-03-31T23:59:59Z", "precision": "range", "label": None},
        subject_ref=repo_ref,
        evidence=[
            EvidenceSpec(
                hist_src["id"], hist_f["id"], "third_party_measured", "independent_direct", "json_path", "$[*].days"
            )
        ],
    )
    snaps = []
    for d, v in [(date(2024, 1, 2), 1), (date(2024, 2, 1), 40), (date(2024, 2, 5), 900), (date(2024, 3, 31), 1500)]:
        snaps.append(
            b.add(
                "metric_snapshots",
                {
                    "metric_key": "github_stars",
                    "label": "GitHub stars",
                    "value_numeric": v,
                    "value_text": None,
                    "unit": "count",
                    "currency": None,
                    "time": day_range(d),
                    "repository_id": repo["id"],
                    "claim_ids": [c_series],
                    "review_state": "machine_extracted",
                },
            )
        )

    blog = b.add_source(
        "https://example.dev/blog/launch",
        surface_key="web",
        source_type="first_party",
        policy=wpol,
        title="We launched",
        author="Ada",
        published_at="2024-02-06T00:00:00Z",
    )
    blog_f = b.add_fetch(blog, status="success", http_status=200, content_hash="sha256:" + "2" * 64)
    c_launch = b.add_claim(
        "Example launched on Show HN on February 3, 2024.",
        kind="launch",
        time=day_range(date(2024, 2, 3)),
        evidence=[
            EvidenceSpec(
                blog["id"],
                blog_f["id"],
                "documented",
                "primary_direct",
                "text_fragment",
                "we launched on Show HN",
                excerpt="We launched on Show HN on February 3.",
            )
        ],
    )
    c_mrr = b.add_claim(
        "Example reported $5,000 MRR in March 2024.",
        kind="metric",
        time=month_range(2024, 3),
        evidence=[
            EvidenceSpec(
                blog["id"],
                blog_f["id"],
                "company_measured",
                "primary_direct",
                "text_fragment",
                "$5,000 MRR",
                excerpt="we hit $5,000 MRR",
            )
        ],
    )
    b.add(
        "metric_snapshots",
        {
            "metric_key": "mrr",
            "label": "MRR",
            "value_numeric": 5000,
            "value_text": None,
            "unit": "currency",
            "currency": "USD",
            "time": month_range(2024, 3),
            "repository_id": None,
            "claim_ids": [c_mrr],
            "review_state": "machine_extracted",
        },
    )
    red = b.add_source(
        "https://www.reddit.com/r/x/comments/1",
        surface_key="reddit",
        source_type="community",
        policy=rpol,
        title="Example on Reddit",
    )
    b.add_fetch(red, status="skipped_policy", error_code="link_only_policy")

    ev = b.add(
        "events",
        {
            "event_type": "show_hn",
            "title": "Show HN: Example",
            "summary": "Launched on Show HN.",
            "time": day_range(date(2024, 2, 3)),
            "surface_ids": [b.surface("hacker_news")["id"]],
            "claim_ids": [c_launch],
            "metric_snapshot_ids": [],
            "outcome_ids": [],
            "tactic_occurrence_ids": [],
            "launch_episode_id": None,
            "review_state": "machine_inferred",
            "is_negative_sensitive": False,
        },
    )
    b.attach("event", ev, [c_launch])
    le = b.add(
        "launch_episodes",
        {
            "title": "Hacker News launch, Feb 2024",
            "time": day_range(date(2024, 2, 3)),
            "event_ids": [ev["id"]],
            "claim_ids": [c_launch],
            "summary": "One launch event.",
            "review_state": "machine_inferred",
        },
    )
    ev["launch_episode_id"] = le["id"]
    b.add(
        "growth_episodes",
        {
            "repository_id": repo["id"],
            "title": "Sharp star growth, Feb 2024",
            "time": {
                "start": "2024-02-03T00:00:00Z",
                "end": "2024-02-05T23:59:59Z",
                "precision": "range",
                "label": None,
            },
            "start_metric_id": snaps[1]["id"],
            "end_metric_id": snaps[2]["id"],
            "delta_numeric": 860,
            "related_event_ids": [ev["id"]],
            "summary": "+860 stars.",
            "causal_attribution": "weakly_associated",
            "claim_ids": [c_series, c_launch],
        },
    )
    o = b.add(
        "outcomes",
        {
            "summary": "Stars rose after the launch (timing only).",
            "event_id": ev["id"],
            "tactic_occurrence_id": None,
            "metric_snapshot_ids": [snaps[2]["id"]],
            "causal_attribution": "weakly_associated",
            "claim_ids": [c_series, c_launch],
            "review_state": "machine_inferred",
        },
    )
    ev["outcome_ids"].append(o["id"])
    tac = b.add(
        "tactics",
        {
            "name": "Launch on Show HN",
            "description": "Post to Show HN.",
            "mechanism": "Concentrated developer attention.",
            "status": "candidate",
            "claim_ids": [c_launch],
        },
    )
    occ = b.add(
        "tactic_occurrences",
        {
            "tactic_id": tac["id"],
            "event_id": ev["id"],
            "strategy_phase_id": None,
            "time": day_range(date(2024, 2, 3)),
            "implementation": "Posted a Show HN.",
            "outcome_ids": [],
            "claim_ids": [c_launch],
            "review_state": "machine_inferred",
        },
    )
    ev["tactic_occurrence_ids"].append(occ["id"])
    eng = b.add(
        "growth_engines",
        {
            "name": "Developer word of mouth",
            "description": "d",
            "mechanism": "m",
            "status": "candidate",
            "claim_ids": [c_launch],
        },
    )
    b.add(
        "growth_engine_occurrences",
        {
            "growth_engine_id": eng["id"],
            "strategy_phase_ids": [],
            "time": month_range(2024, 2),
            "state": "active",
            "strength": "unknown",
            "tactic_occurrence_ids": [occ["id"]],
            "outcome_ids": [],
            "claim_ids": [c_launch],
            "review_state": "machine_inferred",
        },
    )
    b.add(
        "strategy_phases",
        {
            "label": "Launch phase",
            "summary": "s",
            "time": month_range(2024, 2),
            "claim_ids": [c_launch],
            "review_state": "machine_inferred",
        },
    )
    b.add(
        "company_stages",
        {
            "label": "Side project",
            "summary": "s",
            "time": month_range(2024, 2),
            "claim_ids": [c_mrr],
            "review_state": "machine_inferred",
        },
    )
    b.gap("unknown_early_user_acquisition", "Unknown first users.", severity="material")
    b.narrative["thirty_second"] = {
        "text": "Example launched on Show HN.",
        "claim_ids": [c_launch],
        "inference_strength": "explicit",
    }
    b.narrative["key_takeaways"] = [
        {"text": "Launch timing mattered.", "claim_ids": [c_launch], "inference_strength": "weak_inference"}
    ]
    b.started_at = "2026-10-01T00:00:00Z"
    return b.build(
        status="completed_with_gaps",
        cost={"currency": "USD", "model_cost": 0.0, "search_cost": 0.0, "other_cost": 0.0, "total_cost": 0.0},
        usage={
            "model_input_tokens": 0,
            "model_output_tokens": 0,
            "search_requests": 0,
            "http_requests": 0,
            "github_api_requests": 0,
            "retries": 0,
        },
        completed_at=datetime(2026, 10, 1, 0, 5, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@pytest.fixture
def bundle() -> ResearchBundle:
    return build_fixture_bundle()


@pytest.fixture
def bundle_dict(bundle: ResearchBundle) -> dict:
    return copy.deepcopy(bundle.model_dump(mode="json"))
