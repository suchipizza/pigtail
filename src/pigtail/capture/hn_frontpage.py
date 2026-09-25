"""HN front-page minutes from the rank poller's history (M1-T22; outcome-model §1.2 A2
`att.hn_frontpage_minutes`; codebook §6.2; ADR-031.1).

Definition: the minutes a story spent at rank ≤ `max_rank` (30, one HN page; a config
assumption, unverified) in `topstories`, measured from pigtail's own polls (`hn_rank_poll`,
`hn_rank_observation`, M1-T14). Rank history can't be backfilled, so anything before the first
poll is **unknown**, never 0.

Each poll's ranks hold until the next poll (a step function):

- two consecutive polls `p < q` form a **covered** segment `[p, q)` when `q - p ≤ gap_factor ×
  interval` (default 2 × 5 min). A story on the front page at `p` is credited `q - p`;
- a longer gap is **uncovered**: nobody knows what happened in it, so it is not counted for any
  story and is reported as uncovered minutes (whole gap). For each story, `uncovered_minutes` is
  the length of the gaps that began while it was on the front page (the minutes it may have
  lost);
- the latest poll's segment runs to `now` if that is within `gap_factor × interval`, else the
  whole tail is uncovered (the poller is down);
- everything is clipped to the window `[since, until)` (default: first poll → now). Time in the
  window before the first poll is `before_polling_minutes`.

Coverage = covered minutes / window minutes. With coverage 1 the value is `verified`; below 1 it
is `estimated` and a **lower bound**; with no covered minute at all it is `unknown` (None).

Stories are matched to a repo by `hn_story.repo_full_name` (the normalized `github.com/owner/name`
of the story URL, set by the rank poller). Title-only matches (outcome model A2) are not made
here. The functions only read (no writes); `pigtail report hn-frontpage` runs them in a
read-only transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pigtail.capture.db import CaptureDB
from pigtail.capture.hn_ranks import DEFAULT_INTERVAL_SECONDS, FRONT_PAGE_RANKS

Quality = Literal["verified", "estimated", "unknown"]


@dataclass(frozen=True)
class FrontpageConfig:
    interval: timedelta = timedelta(seconds=DEFAULT_INTERVAL_SECONDS)
    max_rank: int = FRONT_PAGE_RANKS
    gap_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.interval < timedelta(minutes=1):
            raise ValueError("interval must be >= 1 minute (TM-04)")
        if self.max_rank < 1 or self.gap_factor < 1:
            raise ValueError("max_rank >= 1 and gap_factor >= 1")

    @property
    def max_gap(self) -> timedelta:
        return self.interval * self.gap_factor


@dataclass(frozen=True)
class Segment:
    start: datetime
    end: datetime
    covered: bool
    poll: datetime | None  # the poll whose ranks hold over the segment (None: before polling)

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass
class StoryMinutes:
    item_id: int
    minutes: float = 0.0
    uncovered_minutes: float = 0.0
    polls_on_front_page: int = 0
    best_rank: int | None = None
    first_on_front_page: datetime | None = None
    last_on_front_page: datetime | None = None


@dataclass
class FrontpageReport:
    repo: str | None
    window_start: datetime | None
    window_end: datetime
    polling_started_at: datetime | None
    minutes: float | None
    quality: Quality
    lower_bound: bool
    coverage: float | None
    window_minutes: float
    covered_minutes: float
    uncovered_minutes: float
    before_polling_minutes: float
    gaps: list[dict[str, Any]] = field(default_factory=list)
    stories: list[StoryMinutes] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = _json_ready(asdict(self))
        return out


def _json_ready(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_ready(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_ready(x) for x in v]
    return v


def segments(
    polls: Sequence[datetime],
    start: datetime,
    end: datetime,
    now: datetime,
    cfg: FrontpageConfig,
) -> list[Segment]:
    """Covered / uncovered segments of `[start, end)` from sorted poll times (see module doc).

    `polls` must include the last poll at or before `start` (if any) and the first poll after
    `end` (if any), so that segments crossing the window edges are known.
    """
    out: list[Segment] = []
    if end <= start:
        return out
    first = polls[0] if polls else None
    if first is None or first >= end:
        return [Segment(start, end, False, None)]
    if first > start:
        out.append(Segment(start, first, False, None))
    for i, p in enumerate(polls):
        if p >= end:
            break
        q = polls[i + 1] if i + 1 < len(polls) else None
        if q is None:
            seg_end = max(p, now)
            covered = seg_end - p <= cfg.max_gap
        else:
            seg_end = q
            covered = q - p <= cfg.max_gap
        s, e = max(p, start), min(seg_end, end)
        if e > s:
            out.append(Segment(s, e, covered, p))
    return out


def _load_polls(db: CaptureDB, start: datetime | None, end: datetime) -> list[datetime]:
    if start is None:
        lo = None
    else:
        row = db.conn.execute(
            "SELECT max(observed_at) FROM hn_rank_poll WHERE observed_at <= %s", (start,)
        ).fetchone()
        lo = row[0] if row and row[0] else start
    rows = db.conn.execute(
        "SELECT observed_at FROM hn_rank_poll WHERE (%(lo)s::timestamptz IS NULL"
        " OR observed_at >= %(lo)s) AND observed_at < %(end)s ORDER BY observed_at",
        {"lo": lo, "end": end},
    ).fetchall()
    nxt = db.conn.execute(
        "SELECT min(observed_at) FROM hn_rank_poll WHERE observed_at >= %s", (end,)
    ).fetchone()
    polls = [r[0] for r in rows]
    if nxt and nxt[0]:
        polls.append(nxt[0])
    return polls


def story_frontpage_minutes(
    db: CaptureDB,
    item_ids: Sequence[int],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    now: datetime | None = None,
    cfg: FrontpageConfig | None = None,
    repo: str | None = None,
) -> FrontpageReport:
    """`att.hn_frontpage_minutes` for a set of stories over `[since, until)` (module doc)."""
    cfg = cfg or FrontpageConfig()
    now = now or datetime.now(UTC)
    end = min(until, now) if until is not None else now
    row = db.conn.execute("SELECT min(observed_at) FROM hn_rank_poll").fetchone()
    polling_started = row[0] if row and row[0] else None
    start = since if since is not None else (polling_started or end)
    polls = _load_polls(db, start, end)
    segs = segments(polls, start, end, now, cfg)
    obs: dict[int, dict[datetime, int]] = {}
    if item_ids and polls:
        for iid, at, rank in db.conn.execute(
            "SELECT item_id, observed_at, rank FROM hn_rank_observation WHERE item_id = ANY(%s)"
            " AND rank <= %s AND observed_at >= %s AND observed_at <= %s",
            (list(item_ids), cfg.max_rank, polls[0], polls[-1]),
        ):
            obs.setdefault(int(iid), {})[at] = int(rank)
    stories = {int(i): StoryMinutes(int(i)) for i in item_ids}
    for iid, st in stories.items():
        ranks = obs.get(iid, {})
        for p, r in sorted(ranks.items()):
            if start <= p < end:
                st.polls_on_front_page += 1
                st.best_rank = r if st.best_rank is None else min(st.best_rank, r)
                st.first_on_front_page = st.first_on_front_page or p
                st.last_on_front_page = p
        for sg in segs:
            if sg.poll is None or sg.poll not in ranks:
                continue
            if sg.covered:
                st.minutes += sg.minutes
            else:
                st.uncovered_minutes += sg.minutes
        st.minutes = round(st.minutes, 2)
        st.uncovered_minutes = round(st.uncovered_minutes, 2)
    window = (end - start).total_seconds() / 60 if end > start else 0.0
    covered = sum(s.minutes for s in segs if s.covered)
    before = sum(s.minutes for s in segs if s.poll is None)
    uncovered = sum(s.minutes for s in segs if not s.covered and s.poll is not None)
    coverage = round(covered / window, 4) if window > 0 else None
    total: float | None
    quality: Quality
    if covered <= 0:
        total, quality = None, "unknown"
    else:
        total = round(sum(s.minutes for s in stories.values()), 2)
        quality = "verified" if covered >= window - 1e-9 else "estimated"
    return FrontpageReport(
        repo=repo,
        window_start=start if window > 0 else None,
        window_end=end,
        polling_started_at=polling_started,
        minutes=total,
        quality=quality,
        lower_bound=quality == "estimated",
        coverage=coverage,
        window_minutes=round(window, 2),
        covered_minutes=round(covered, 2),
        uncovered_minutes=round(uncovered, 2),
        before_polling_minutes=round(before, 2),
        gaps=[
            {"start": s.start, "end": s.end, "minutes": round(s.minutes, 2)}
            for s in segs
            if not s.covered and s.poll is not None
        ],
        stories=sorted(stories.values(), key=lambda s: (-s.minutes, s.item_id)),
        config={
            "interval_s": int(cfg.interval.total_seconds()),
            "max_rank": cfg.max_rank,
            "gap_factor": cfg.gap_factor,
            "match": "hn_story.repo_full_name (story URL)",
        },
    )


def repo_frontpage_minutes(
    db: CaptureDB,
    full_name: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    now: datetime | None = None,
    cfg: FrontpageConfig | None = None,
) -> FrontpageReport:
    """`att.hn_frontpage_minutes` summed over the stories whose URL points to `owner/name`."""
    from pigtail.privacy.suppression import normalize_repo_name

    name = normalize_repo_name(full_name)
    ids = [
        int(r[0])
        for r in db.conn.execute(
            "SELECT item_id FROM hn_story WHERE repo_full_name = %s ORDER BY item_id", (name,)
        )
    ]
    return story_frontpage_minutes(db, ids, since=since, until=until, now=now, cfg=cfg, repo=name)
