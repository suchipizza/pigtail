"""Reddit posts that reach Pigtail are dated from search results and lined up with star growth."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

from pigtail.domain.time import range_from_page_age
from pigtail.policies.loader import default_registry
from pigtail.research.builder import BundleBuilder
from pigtail.research.discovery import build_plan
from pigtail.research.repository_analysis import events_near, reddit_post_event
from pigtail.research.target_resolution import ResolvedTarget

NOW = datetime(2026, 10, 9, 8, 44, tzinfo=UTC)


def test_page_age_formats():
    assert range_from_page_age("September 24, 2026", NOW)["start"] == "2026-09-24T00:00:00Z"
    assert range_from_page_age("24 Sep 2026", NOW)["precision"] == "day"
    assert range_from_page_age("2026-09-24", NOW)["precision"] == "day"
    assert range_from_page_age("3 days ago", NOW)["start"] == "2026-10-06T00:00:00Z"
    two_weeks = range_from_page_age("2 weeks ago", NOW)
    assert two_weeks["precision"] == "range"
    assert (two_weeks["start"], two_weeks["end"]) == ("2026-09-19T00:00:00Z", "2026-09-25T23:59:59Z")
    for unknown in (None, "", "yesterday", "Feb 30, 2026"):
        assert range_from_page_age(unknown, NOW) is None


def test_plan_keeps_one_reddit_query():
    t = ResolvedTarget(
        kind="repository",
        name="brag",
        canonical_url="https://github.com/latent-spaces/brag",
        domain=None,
        repo=SimpleNamespace(full_name="latent-spaces/brag", owner="latent-spaces"),  # type: ignore[arg-type]
    )
    queries = [q.text for q in build_plan(t, 12).queries]
    assert '"brag" reddit latent-spaces' in queries
    assert not any(q.startswith("site:reddit.com") for q in queries)  # web search has no Reddit pages


def _builder() -> tuple[BundleBuilder, dict, dict]:
    target = {
        "id": "t",
        "kind": "repository",
        "name": "brag",
        "canonical_url": "https://github.com/latent-spaces/brag",
        "domain": None,
        "description": None,
        "primary_repository_id": None,
        "external_ids": [],
        "aliases": [],
    }
    b = BundleBuilder(target=target)
    url = "https://www.reddit.com/r/SideProject/comments/1wp7jgm/my_side_project_crossed_7000_github_stars/"
    pol = default_registry().policy_for(url, "reddit")
    s = b.add_source(url, surface_key="reddit", source_type="community", policy=pol, title="My side project…")
    f = b.add_fetch(s, status="skipped_policy", error_code="link_only_policy", parser_version="none")
    return b, s, f


def test_reddit_post_becomes_a_timeline_event_without_reading_the_post():
    b, s, f = _builder()
    when = range_from_page_age("September 24, 2026", NOW)
    assert when is not None
    ev = reddit_post_event(b, s, f, "My side project crossed 7,000 GitHub stars", when)
    assert ev["event_type"] == "reddit_post" and ev["time"]["precision"] == "day"
    claim = b.c["claims"][-1]
    assert "September 24, 2026" in claim["statement"] and "did not read" in claim["statement"]
    assert all(el["excerpt"] is None for el in b.c["evidence_links"])  # Reddit allows no excerpts


def test_reddit_posts_line_up_with_growth_episodes():
    b, s, f = _builder()
    day = reddit_post_event(b, s, f, "post A", range_from_page_age("September 24, 2026", NOW))  # type: ignore[arg-type]
    week = reddit_post_event(b, s, f, "post B", range_from_page_age("2 weeks ago", NOW))  # type: ignore[arg-type]
    old = reddit_post_event(b, s, f, "post C", range_from_page_age("June 1, 2026", NOW))  # type: ignore[arg-type]
    near = events_near(b.c["events"], date(2026, 9, 16), date(2026, 10, 6))
    assert {e["id"] for e in near} == {day["id"], week["id"]}
    assert old not in near
