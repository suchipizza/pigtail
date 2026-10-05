"""Group launch-like events into LaunchEpisodes and compute post-launch star windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

LAUNCH_TYPES = {
    "show_hn",
    "launch_hn",
    "hacker_news_post",
    "product_hunt_launch",
    "launch",
    "announcement",
    "reddit_post",
    "release",
}
WINDOWS = (("+24h", 1), ("+48h", 2), ("+7d", 7), ("+30d", 30), ("+90d", 90))


@dataclass
class LaunchCluster:
    anchor: date
    end: date
    event_ids: list[str]


STRONG_TYPES = {"show_hn", "launch_hn", "product_hunt_launch", "launch", "announcement", "reddit_post"}


def cluster_launches(
    events: list[tuple[str, str, date]],
    *,
    gap_days: int = 4,
    notable: set[str] | None = None,
    not_before: date | None = None,
) -> list[LaunchCluster]:
    """events: (id, event_type, day). A cluster needs a strong launch event (Show HN, Product Hunt, announced
    launch, Reddit post) or an event id listed in `notable` (e.g. a Hacker News post with many points).
    Releases join a cluster but never form one alone."""
    cand = sorted(
        [e for e in events if e[1] in LAUNCH_TYPES and (not_before is None or e[2] >= not_before)], key=lambda e: e[2]
    )
    clusters: list[list[tuple[str, str, date]]] = []
    for e in cand:
        if clusters and (e[2] - clusters[-1][-1][2]).days <= gap_days:
            clusters[-1].append(e)
        else:
            clusters.append([e])
    out = []
    for c in clusters:
        if not any(e[1] in STRONG_TYPES or (notable and e[0] in notable) for e in c):
            continue
        out.append(LaunchCluster(anchor=c[0][2], end=c[-1][2], event_ids=[e[0] for e in c]))
    return out


def window_days(anchor: date, today: date) -> list[tuple[str, date]]:
    out = [("before", anchor - timedelta(days=1))]
    for label, n in WINDOWS:
        d = anchor + timedelta(days=n)
        if d <= today:
            out.append((label, d))
    return out
