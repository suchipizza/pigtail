"""Repository forensics: GitHub data, star history, releases, HN posts, growth and launch episodes.

Everything here is deterministic. Event association with growth is timing-only and is capped at
`weakly_associated`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from pigtail.domain.time import day_range, human_label, instant, iso, parse_dt
from pigtail.policies.loader import PolicyRegistry
from pigtail.providers.community.hacker_news import HackerNewsClient, HNStory
from pigtail.providers.community.reddit import RedditPost
from pigtail.providers.github.client import (
    GitHubClient,
    GitHubError,
    Release,
    RepositorySnapshot,
    StarHistory,
)
from pigtail.repository.growth_episodes import DetectedEpisode, detect_episodes
from pigtail.repository.launch_episodes import cluster_launches, window_days
from pigtail.repository.releases import select_releases
from pigtail.repository.stars import daily_series, snapshot_days, value_at
from pigtail.research.builder import BundleBuilder, EvidenceSpec, sha256_text


def _fmt(n: float) -> str:
    return f"{int(n):,}"


@dataclass
class RepoState:
    repo_obj: dict
    snap: RepositorySnapshot
    history: StarHistory
    series_claim: str | None = None
    stars_source: dict | None = None
    stars_fetch: dict | None = None
    daily: list[tuple[date, int]] = field(default_factory=list)
    episodes: list[tuple[DetectedEpisode, dict]] = field(default_factory=list)
    snapshot_ids: dict[date, str] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    hn_stories: list[HNStory] = field(default_factory=list)
    readme: tuple[str, str] | None = None
    weak_hn: list[HNStory] = field(default_factory=list)
    notable_event_ids: set[str] = field(default_factory=set)
    releases_total: int = 0


async def analyze_repository(
    b: BundleBuilder,
    snap: RepositorySnapshot,
    gh: GitHubClient,
    hn: HackerNewsClient,
    policies: PolicyRegistry,
    *,
    name: str,
    homepage: str | None,
    today: date,
) -> RepoState:
    gpol = policies.policy_for(snap.url, "github")
    now = iso(datetime.now(UTC))

    # --- repository metadata -----------------------------------------------------
    src = b.add_source(
        snap.api_url,
        surface_key="github",
        source_type="repository_api",
        policy=gpol,
        title=f"GitHub API: {snap.full_name}",
        author=snap.owner,
    )
    payload = json.dumps(
        {"id": snap.external_id, "stars": snap.stars, "forks": snap.forks, "created_at": snap.created_at},
        sort_keys=True,
    )
    f = b.add_fetch(
        src,
        status="success",
        http_status=200,
        content_hash=sha256_text(payload),
        etag=snap.raw_etag,
        parser_version="github-rest-2022-11-28",
    )

    def gh_ev(path: str, excerpt: str | None = None) -> EvidenceSpec:
        return EvidenceSpec(
            src["id"],
            f["id"],
            "third_party_measured",
            "independent_direct",
            "json_path",
            path,
            excerpt=excerpt,
        )

    repo_obj = b.add(
        "repositories",
        {
            "provider": "github",
            "owner": snap.owner,
            "name": snap.name,
            "url": snap.url,
            "external_id": snap.external_id,
            "created_at": snap.created_at,
            "default_branch": snap.default_branch,
            "language": snap.language,
            "license": snap.license,
            "is_archived": snap.is_archived,
            "observed_at": now,
            "current": {
                "stars": snap.stars,
                "forks": snap.forks,
                "watchers": snap.watchers,
                "open_issues": snap.open_issues,
                "contributors": snap.contributors,
            },
            "star_history_quality": "unavailable",
            "claim_ids": [],
        },
    )
    repo_ref = {"type": "repository", "id": repo_obj["id"]}
    st = RepoState(repo_obj=repo_obj, snap=snap, history=StarHistory(quality="unavailable"))
    b.target["primary_repository_id"] = repo_obj["id"]
    b.target["external_ids"].append({"namespace": "github:repo", "value": snap.external_id, "url": snap.url})

    created = parse_dt(snap.created_at)
    if created:
        cid = b.add_claim(
            f"The GitHub repository {snap.full_name} was created on {created:%B %d, %Y}.",
            kind="repository_created",
            time=day_range(created),
            subject_ref=repo_ref,
            evidence=[gh_ev("$.created_at")],
            certainty=1.0,
        )
        repo_obj["claim_ids"].append(cid)
        ev = b.add(
            "events",
            _event(
                "repository_created",
                f"{snap.full_name} repository created",
                f"The public GitHub repository was created on {created:%b %d, %Y}.",
                day_range(created),
                [cid],
                b,
            ),
        )
        st.events.append(ev)
    stat = (
        f"As of {datetime.now(UTC):%B %d, %Y}, {snap.full_name} has {_fmt(snap.stars)} GitHub stars and "
        f"{_fmt(snap.forks)} forks" + (f" and {_fmt(snap.contributors)} contributors." if snap.contributors else ".")
    )
    cur_claim = b.add_claim(
        stat,
        kind="metric",
        time=day_range(datetime.now(UTC)),
        subject_ref=repo_ref,
        evidence=[gh_ev("$.stargazers_count,$.forks_count")],
        certainty=1.0,
    )
    repo_obj["claim_ids"].append(cur_claim)
    b.add(
        "metric_snapshots",
        _metric(
            "github_forks",
            "GitHub forks",
            snap.forks,
            day_range(datetime.now(UTC)),
            repo_obj["id"],
            [cur_claim],
        ),
    )

    # --- star history ------------------------------------------------------------
    hist = await gh.star_history(snap.owner, snap.name, snap.stars, snap.created_at)
    st.history = hist
    repo_obj["star_history_quality"] = hist.quality
    if hist.points:
        ssrc = b.add_source(
            f"{snap.api_url}/stargazers/history",
            surface_key="github",
            source_type="star_history_api",
            policy=gpol,
            title=f"GitHub star history: {snap.full_name}",
            author=None,
        )
        digest = sha256_text(json.dumps([(d.isoformat(), n) for d, n in hist.points]))
        sf = b.add_fetch(
            ssrc, status="success", http_status=200, content_hash=digest, parser_version="github-star-history-1"
        )
        st.stars_source, st.stars_fetch = ssrc, sf
        first_d, last_d = hist.points[0][0], hist.points[-1][0]
        qual_txt = {
            "exact": "daily counts",
            "sampled": "sampled counts",
        }
        statement = (
            f"GitHub star history ({qual_txt.get(hist.quality, hist.quality)}) gives {snap.full_name} "
            f"a cumulative star count from {first_d:%b %d, %Y} ({_fmt(hist.points[0][1])}) to "
            f"{last_d:%b %d, %Y} ({_fmt(hist.points[-1][1])})."
        )
        st.series_claim = b.add_claim(
            statement,
            kind="star_history",
            subject_ref=repo_ref,
            time={
                "start": iso(datetime(first_d.year, first_d.month, first_d.day, tzinfo=UTC)),
                "end": iso(datetime(last_d.year, last_d.month, last_d.day, 23, 59, 59, tzinfo=UTC)),
                "precision": "range",
                "label": None,
            },
            evidence=[
                EvidenceSpec(
                    ssrc["id"],
                    sf["id"],
                    "third_party_measured",
                    "independent_direct",
                    "json_path",
                    "$[*].days",
                    notes=" ".join(hist.notes) or None,
                )
            ],
            certainty=1.0 if hist.quality == "exact" else 0.8,
        )
        repo_obj["claim_ids"].append(st.series_claim)
        for d, n in snapshot_days(hist.points, today, hist.quality):
            m = b.add(
                "metric_snapshots",
                _metric("github_stars", "GitHub stars", n, day_range(d), repo_obj["id"], [st.series_claim]),
            )
            st.snapshot_ids[d] = m["id"]
        if hist.quality == "exact":
            st.daily = daily_series(hist.points, today)
        else:
            st.daily = _dense_recent_daily(hist.points, today)
        if hist.quality == "sampled":
            b.gap("sampled_star_history", " ".join(hist.notes[:1]), severity="minor", surface_key="github")
    else:
        b.gap(
            "star_history_unavailable",
            "GitHub star history could not be retrieved" + (f": {hist.notes[0]}" if hist.notes else "."),
            severity="material",
            related_refs=[repo_ref],
            surface_key="github",
        )

    # --- growth episodes ------------------------------------------------------------
    if st.daily and st.series_claim:
        for ep in detect_episodes(st.daily):
            claim = b.add_claim(
                f"GitHub star history shows {snap.full_name} gained {_fmt(ep.delta)} stars between "
                f"{ep.start:%b %d, %Y} and {ep.end:%b %d, %Y}, versus about {ep.baseline_per_day:g} stars per "
                f"day in the preceding four weeks.",
                kind="star_growth_episode",
                subject_ref=repo_ref,
                time={
                    "start": iso(datetime(ep.start.year, ep.start.month, ep.start.day, tzinfo=UTC)),
                    "end": iso(datetime(ep.end.year, ep.end.month, ep.end.day, 23, 59, 59, tzinfo=UTC)),
                    "precision": "range" if ep.end != ep.start else "day",
                    "label": None,
                },
                evidence=[
                    EvidenceSpec(
                        ssrc["id"],
                        sf["id"],
                        "third_party_measured",
                        "independent_direct",
                        "json_path",
                        f"$[*].days ({ep.start} to {ep.end})",
                    )
                ],
                certainty=1.0 if hist.quality == "exact" else 0.7,
            )
            start_m = _ensure_snapshot(b, st, ep.start - timedelta(days=1))
            end_m = _ensure_snapshot(b, st, ep.end)
            g = b.add(
                "growth_episodes",
                {
                    "repository_id": repo_obj["id"],
                    "title": f"{'Sharp' if ep.delta > 5 * max(1, ep.baseline_per_day) * 7 else 'Elevated'} star growth, "
                    f"{human_label({'start': iso(datetime(ep.start.year, ep.start.month, ep.start.day, tzinfo=UTC)), 'end': None, 'precision': 'month', 'label': None})}",
                    "time": {
                        "start": iso(datetime(ep.start.year, ep.start.month, ep.start.day, tzinfo=UTC)),
                        "end": iso(datetime(ep.end.year, ep.end.month, ep.end.day, 23, 59, 59, tzinfo=UTC)),
                        "precision": "range" if ep.end != ep.start else "day",
                        "label": None,
                    },
                    "start_metric_id": start_m,
                    "end_metric_id": end_m,
                    "delta_numeric": ep.delta,
                    "related_event_ids": [],
                    "summary": f"+{_fmt(ep.delta)} stars in {(ep.end - ep.start).days + 1} day(s); peak day "
                    f"{ep.peak_day:%b %d} with +{_fmt(ep.peak_value)}." + durability(st.daily, ep),
                    "causal_attribution": "unknown",
                    "claim_ids": [claim],
                },
            )
            st.episodes.append((ep, g))

    # --- releases --------------------------------------------------------------------
    try:
        releases = await gh.releases(snap.owner, snap.name)
    except GitHubError as exc:
        releases = []
        b.gap("releases_unavailable", f"GitHub releases could not be retrieved ({exc}).", surface_key="github")
    st.releases_total = len(releases)
    if releases:
        rsrc = b.add_source(
            f"https://github.com/{snap.full_name}/releases",
            surface_key="github",
            source_type="release_list",
            policy=gpol,
            title=f"{snap.full_name} releases on GitHub",
        )
        rf = b.add_fetch(
            rsrc,
            status="success",
            http_status=200,
            content_hash=sha256_text(json.dumps([(r.tag, r.published_at) for r in releases])),
            parser_version="github-releases-1",
        )
        windows = [(ep.start, ep.end) for ep, _ in st.episodes]
        chosen = select_releases(releases, windows)
        first_stable = next((r for r in sorted(releases, key=lambda r: r.published_at or "") if not r.prerelease), None)
        for r in chosen:
            st.events.append(_release_event(b, r, snap, rsrc, rf, repo_ref, first=r is first_stable))
        rc = b.add_claim(
            f"{snap.full_name} has published {len(releases)} GitHub releases"
            + (" (at least; only the most recent 500 were read)." if len(releases) >= 500 else "."),
            kind="release_count",
            time=day_range(datetime.now(UTC)),
            subject_ref=repo_ref,
            evidence=[
                EvidenceSpec(
                    rsrc["id"],
                    rf["id"],
                    "third_party_measured",
                    "independent_direct",
                    "json_path",
                    "$.length",
                )
            ],
            certainty=1.0,
        )
        repo_obj["claim_ids"].append(rc)
    else:
        b.gap(
            "no_releases",
            f"{snap.full_name} has no GitHub releases, so version history is not shown.",
            surface_key="github",
        )

    # --- Hacker News ------------------------------------------------------------------
    hpol = policies.policy_for("https://news.ycombinator.com", "hacker_news")
    if policies.enabled("hacker_news"):
        stories = await hn.stories_for(
            name=name.split("/")[-1] if len(name.split("/")[-1]) > 3 else snap.full_name,
            repo_url=snap.url,
            homepage=homepage,
        )
        created_d = created.date() if created else None
        hp_host = (
            (homepage or "").lower().replace("https://", "").replace("http://", "").removeprefix("www.").split("/")[0]
        )
        for s in stories:
            sd = parse_dt(s.created_at)
            url_match = s.matched_by.startswith("url")
            if (
                created_d
                and sd
                and sd.date() < created_d - timedelta(days=30)
                and not (url_match and hp_host and hp_host in (s.url or ""))
            ):
                continue  # predates the project: most likely a different entity with the same name
            if s.matched_by == "title:mention":
                st.weak_hn.append(s)  # identity checked later by the model triage
                continue
            st.hn_stories.append(s)
            ev = hn_event(b, s, hpol)
            st.events.append(ev)
            if s.points >= 100:
                st.notable_event_ids.add(ev["id"])
        if hn.errors:
            b.gap(
                "source_inaccessible",
                "Hacker News search was partly unavailable during this run.",
                surface_key="hacker_news",
            )
    st.readme = await gh.readme(snap.owner, snap.name)
    return st


def durability(daily: list[tuple[date, int]], ep: DetectedEpisode) -> str:
    """Compare star pace 31–90 days after an episode with the pace before it (PRD §10.9)."""
    idx = {d: v for d, v in daily}
    a0, a1 = ep.end + timedelta(days=31), ep.end + timedelta(days=90)
    if a1 not in idx or a0 - timedelta(days=1) not in idx:
        return " Too recent to say whether the faster pace lasted."
    after = (idx[a1] - idx[a0 - timedelta(days=1)]) / 60
    before = ep.baseline_per_day
    if before < 0.5:
        verdict = f"Afterwards (days 31–90) the project averaged {after:.1f} stars/day, up from almost none before."
    else:
        ratio = after / before
        trend = "stayed well above" if ratio >= 1.5 else "returned close to" if ratio >= 0.67 else "fell below"
        verdict = (
            f"Afterwards (days 31–90) it averaged {after:.1f} stars/day, which {trend} the "
            f"{before:.1f}/day before the episode."
        )
    return " " + verdict


def _dense_recent_daily(points: list[tuple[date, int]], today: date) -> list[tuple[date, int]]:
    """For sampled histories, return the daily series only over the tail where points are ≤2 days apart."""
    if len(points) < 3:
        return []
    start = len(points) - 1
    while start > 0 and (points[start][0] - points[start - 1][0]).days <= 2:
        start -= 1
    tail = points[start + 1 :]
    return daily_series(tail, today) if len(tail) >= 14 else []


def _ensure_snapshot(b: BundleBuilder, st: RepoState, d: date) -> str | None:
    if not st.series_claim:
        return None
    if d in st.snapshot_ids:
        return st.snapshot_ids[d]
    v = value_at(st.history.points, d, interpolate=st.history.quality != "exact")
    if v is None:
        return None
    m = b.add(
        "metric_snapshots",
        _metric("github_stars", "GitHub stars", v, day_range(d), st.repo_obj["id"], [st.series_claim]),
    )
    m["time"]["label"] = None
    st.snapshot_ids[d] = m["id"]
    return m["id"]


def _metric(
    key: str,
    label: str,
    value: float,
    time: dict,
    repo_id: str | None,
    claims: list[str],
    unit: str = "count",
) -> dict:
    return {
        "metric_key": key,
        "label": label,
        "value_numeric": value,
        "value_text": None,
        "unit": unit,
        "currency": None,
        "time": time,
        "repository_id": repo_id,
        "claim_ids": claims,
        "review_state": "machine_extracted",
    }


def _event(
    etype: str,
    title: str,
    summary: str,
    time: dict,
    claims: list[str],
    b: BundleBuilder,
    surfaces: list[str] | None = None,
    review_state: str = "machine_extracted",
) -> dict:
    return {
        "event_type": etype,
        "title": title,
        "summary": summary,
        "time": time,
        "surface_ids": [b.surface(s)["id"] for s in (surfaces or [])],
        "claim_ids": claims,
        "metric_snapshot_ids": [],
        "outcome_ids": [],
        "tactic_occurrence_ids": [],
        "launch_episode_id": None,
        "review_state": review_state,
        "is_negative_sensitive": False,
    }


def _release_event(
    b: BundleBuilder, r: Release, snap: RepositorySnapshot, src: dict, f: dict, repo_ref: dict, first: bool
) -> dict:
    dt = parse_dt(r.published_at)
    assert dt is not None
    label = r.tag + (f" (“{r.name}”)" if r.name and r.name != r.tag else "")
    cid = b.add_claim(
        f"{snap.full_name} published GitHub release {label} on {dt:%B %d, %Y}.",
        kind="release",
        time=day_range(dt),
        subject_ref=repo_ref,
        evidence=[
            EvidenceSpec(
                src["id"],
                f["id"],
                "documented",
                "primary_direct",
                "json_path",
                f"{r.api_path}.published_at",
                excerpt=r.name or r.tag,
                notes=r.url or None,
            )
        ],
        certainty=1.0,
    )
    title = f"{'First release' if first else 'Release'} {r.tag}"
    return b.add(
        "events",
        _event(
            "release",
            title,
            f"GitHub release {label}" + (" (pre-release)" if r.prerelease else "") + ".",
            day_range(dt),
            [cid],
            b,
            ["github"],
        ),
    )


def hn_event(b: BundleBuilder, s: HNStory, pol) -> dict:
    src = b.add_source(
        s.item_url,
        surface_key="hacker_news",
        source_type="hn_story",
        policy=pol,
        title=s.title,
        author=s.author,
        published_at=iso(parse_dt(s.created_at)),
    )
    f = b.add_fetch(
        src,
        status="success",
        http_status=200,
        content_hash=sha256_text(json.dumps([s.id, s.title, s.created_at, s.points])),
        parser_version="hn-algolia-v1",
    )
    dt = parse_dt(s.created_at)
    assert dt is not None
    cid = b.add_claim(
        f"A Hacker News story titled “{s.title}” was posted by {s.author} on {dt:%B %d, %Y}; it had "
        f"{s.points} points and {s.num_comments} comments when Pigtail checked.",
        kind="hn_post",
        time=instant(dt, "minute"),
        evidence=[
            EvidenceSpec(
                src["id"],
                f["id"],
                "third_party_measured",
                "community_report",
                "json_path",
                f"items/{s.id}",
                excerpt=s.title,
            )
        ],
        certainty=1.0 if s.matched_by.startswith("url") else 0.8,
    )
    etype = "show_hn" if s.is_show_hn else "launch_hn" if s.is_launch_hn else "hacker_news_post"
    kind = "Show HN" if s.is_show_hn else "Launch HN" if s.is_launch_hn else "Hacker News post"
    return b.add(
        "events",
        _event(
            etype,
            s.title if (s.is_show_hn or s.is_launch_hn) else f"On Hacker News: {s.title}",
            f"{kind} by {s.author}: {s.points} points, {s.num_comments} comments.",
            instant(dt, "minute"),
            [cid],
            b,
            ["hacker_news"],
        ),
    )


REDDIT_API_PARSER = "reddit-api-v1"  # marks data from the Reddit API; the publication gate refuses it


def reddit_api_event(b: BundleBuilder, p: RedditPost, pol) -> dict:
    """A Reddit post found through the user's own Reddit API keys. Metadata only: no text, no username."""
    src = b.add_source(
        p.url,
        surface_key="reddit",
        source_type="reddit_post",
        policy=pol,
        title=p.title,
        published_at=iso(p.created_at),
    )
    f = b.add_fetch(
        src,
        status="success",
        http_status=200,
        content_hash=sha256_text(json.dumps([p.id, p.title, p.created_utc, p.score, p.num_comments])),
        parser_version=REDDIT_API_PARSER,
    )
    cid = b.add_claim(
        f"A Reddit post titled “{p.title}” was posted in r/{p.subreddit} on {p.created_at:%B %d, %Y}; it had "
        f"{p.score} upvotes and {p.num_comments} comments when Pigtail checked.",
        kind="reddit_post",
        time=instant(p.created_at, "minute"),
        evidence=[
            EvidenceSpec(
                src["id"],
                f["id"],
                "third_party_measured",
                "community_report",
                "json_path",
                f"t3_{p.id}",
            )
        ],
        certainty={"link": 1.0, "author": 0.8}.get(p.matched_by, 0.9),
    )
    return b.add(
        "events",
        _event(
            "reddit_post",
            f"On Reddit (r/{p.subreddit}): {p.title}",
            f"Reddit post in r/{p.subreddit}: {p.score} upvotes, {p.num_comments} comments.",
            instant(p.created_at, "minute"),
            [cid],
            b,
            ["reddit"],
        ),
    )


def reddit_post_event(b: BundleBuilder, src: dict, fetch: dict, title: str, time: dict) -> dict:
    """A Reddit post known only from web search (title + page date). Reddit is link-only: nothing is read."""
    when = time["label"] or f"{parse_dt(time['start']):%B %d, %Y}"
    cid = b.add_claim(
        f"A Reddit post titled “{title}” appeared {'on ' if time['precision'] == 'day' else ''}{when}, according to "
        "web search results. Pigtail did not read the post.",
        kind="reddit_post",
        time=time,
        evidence=[
            EvidenceSpec(
                src["id"],
                fetch["id"],
                "third_party_reported",
                "community_report",
                "other",
                "web search result: title and page date",
                inference_strength="moderate_inference",
            )
        ],
        certainty=0.6,
    )
    return b.add(
        "events",
        _event(
            "reddit_post",
            f"On Reddit: {title}",
            "Found by web search. Pigtail records only the title, link and date of Reddit posts.",
            time,
            [cid],
            b,
            ["reddit"],
        ),
    )


# ----------------------------------------------------------------- phase B (after reconstruction)
def events_near(events: list[dict], start: date, end: date) -> list[dict]:
    """Events dated from 3 days before a growth episode to its end (timing only, never cause).

    Day-precise events count by their day. Events only known to within a week (a search result's
    "2 weeks ago") count when that week overlaps the window.
    """
    lo = start - timedelta(days=3)
    out = []
    for e in events:
        if e["event_type"] == "repository_created":
            continue
        t = e["time"]
        s0, s1 = parse_dt(t["start"]), parse_dt(t["end"])
        if not s0:
            continue
        precise = t["precision"] in ("second", "minute", "hour", "day")
        within_a_week = t["precision"] == "range" and s1 is not None and s1 - s0 <= timedelta(days=7)
        if (precise and lo <= s0.date() <= end) or (
            within_a_week and s1 is not None and s0.date() <= end and s1.date() >= lo
        ):
            out.append(e)
    return out


def link_episodes_and_launches(b: BundleBuilder, st: RepoState, today: date) -> None:
    """Associate events with growth episodes (timing only), group launches, compute outcome windows."""
    dated = []
    for e in b.c["events"]:
        s = parse_dt(e["time"]["start"])
        if s and e["time"]["precision"] in ("second", "minute", "hour", "day"):
            dated.append((e, s.date()))
    for ep, g in st.episodes:
        related = events_near(b.c["events"], ep.start, ep.end)
        g["related_event_ids"] = [e["id"] for e in related]
        if related:
            g["causal_attribution"] = "weakly_associated"
            g["claim_ids"] = list(dict.fromkeys(g["claim_ids"] + [c for e in related for c in e["claim_ids"]]))
            g["summary"] += f" {len(related)} public event(s) observed within this window (timing only)."
        else:
            g["summary"] += " No high-confidence public event found."
            b.gap(
                "unexplained_growth_episode",
                f"No high-confidence public event found for the star growth of {ep.start:%b %d, %Y}"
                f" (+{_fmt(ep.delta)} stars).",
                severity="material",
                related_refs=[{"type": "growth_episode", "id": g["id"]}],
            )
        b.attach("growth_episode", g, g["claim_ids"][:1])

    first_star = st.history.points[0][0] if st.history.points else None
    created = parse_dt(st.snap.created_at)
    not_before = (
        min(x for x in [first_star, created.date() if created else None] if x) if (first_star or created) else None
    )
    clusters = cluster_launches(
        [(e["id"], e["event_type"], d) for e, d in dated], notable=st.notable_event_ids, not_before=not_before
    )
    ev_by_id = {e["id"]: e for e in b.c["events"]}
    prior_launches = 0
    for cl in clusters:
        evs = [ev_by_id[i] for i in cl.event_ids]
        kinds = sorted({e["event_type"].replace("_", " ") for e in evs})
        start = datetime(cl.anchor.year, cl.anchor.month, cl.anchor.day, tzinfo=UTC)
        end = datetime(cl.end.year, cl.end.month, cl.end.day, 23, 59, 59, tzinfo=UTC)
        claims = list(dict.fromkeys(c for e in evs for c in e["claim_ids"]))
        state = _pre_state(st, cl.anchor, prior_launches)
        le = b.add(
            "launch_episodes",
            {
                "title": _launch_title(evs, cl.anchor),
                "time": {
                    "start": iso(start),
                    "end": iso(end),
                    "precision": "range" if cl.end != cl.anchor else "day",
                    "label": None,
                },
                "event_ids": cl.event_ids,
                "claim_ids": claims,
                "summary": f"{len(evs)} related public event(s): {', '.join(kinds)}. {state}",
                "review_state": "machine_inferred",
            },
        )
        prior_launches += 1
        for e in evs:
            e["launch_episode_id"] = le["id"]
        if st.series_claim and st.history.points and (st.history.quality == "exact" or st.daily):
            _outcome_windows(b, st, le, evs[0], cl.anchor, today)


def _pre_state(st: RepoState, anchor: date, prior: int) -> str:
    parts = []
    stars = value_at(st.history.points, anchor - timedelta(days=1), interpolate=st.history.quality != "exact")
    if stars is not None and st.history.points:
        parts.append(f"About {_fmt(stars)} stars the day before")
    created = parse_dt(st.snap.created_at)
    if created:
        age = (anchor - created.date()).days
        parts.append(f"repository {age} days old" if age < 730 else f"repository {age / 365:.1f} years old")
    if prior:
        parts.append(f"{prior} earlier launch episode(s)")
    else:
        parts.append("first launch episode Pigtail found")
    return "State before: " + "; ".join(parts) + "." if parts else ""


def _launch_title(evs: list[dict], anchor: date) -> str:
    types = [e["event_type"] for e in evs]
    if "product_hunt_launch" in types:
        name = "Product Hunt launch"
    elif "show_hn" in types or "launch_hn" in types:
        name = "Hacker News launch"
    elif "launch" in types or "announcement" in types:
        name = "Public launch"
    elif "hacker_news_post" in types:
        name = "Hacker News attention"
    else:
        name = "Launch activity"
    return f"{name}, {anchor:%b %Y}"


def _outcome_windows(b: BundleBuilder, st: RepoState, le: dict, anchor_event: dict, anchor: date, today: date) -> None:
    series_claim = st.series_claim
    if series_claim is None:
        return
    exact = st.history.quality == "exact"
    metric_ids = []
    values = {}
    for label, d in window_days(anchor, today):
        if not exact and not (st.daily and st.daily[0][0] <= d):
            continue
        v = value_at(st.history.points, d, interpolate=not exact)
        if v is None:
            continue
        values[label] = v
        m = b.add(
            "metric_snapshots",
            _metric(
                "github_stars",
                f"Stars {label}" if label != "before" else "Stars before",
                v,
                day_range(d),
                st.repo_obj["id"],
                [series_claim],
            ),
        )
        m["metric_key"] = "github_stars_window"
        metric_ids.append(m["id"])
    if "before" not in values or len(values) < 2:
        return
    base = values["before"]
    desc = ", ".join(f"{k}: +{_fmt(v - base)}" for k, v in values.items() if k != "before")
    o = b.add(
        "outcomes",
        {
            "summary": f"Stars went from {_fmt(base)} the day before to "
            + f"{_fmt(list(values.values())[-1])} ({desc}). Observed after the launch; not proof that the "
            "launch caused the change.",
            "event_id": anchor_event["id"],
            "tactic_occurrence_id": None,
            "metric_snapshot_ids": metric_ids,
            "causal_attribution": "weakly_associated",
            "claim_ids": list(dict.fromkeys([series_claim, *anchor_event["claim_ids"]])),
            "review_state": "machine_inferred",
        },
    )
    anchor_event["outcome_ids"].append(o["id"])
    if st.history.quality != "exact":
        b.gap("incomplete_outcome_window", f"Post-launch star windows for {le['title']} use sampled star data.")
