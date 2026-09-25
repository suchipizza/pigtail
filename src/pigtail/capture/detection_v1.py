"""Breakout detection v1 (M1-T24; R1.1; ADR-032.2; replan §6.3). GH Archive velocity-v0 stays on,
unchanged, as a control; agreement between the two is recorded.

**Screen (hourly).** For every active watch-list repo, the last public star-count snapshot `L`
(taken within `fresh_hours` of `at`) is compared with the earliest snapshot `F` taken no earlier
than `L − 48 h` (5 minutes of slack for polling jitter). `stars_48h = L.stars − F.stars` is net of
un-stars (a public counter). A repo is a **candidate** when `stars_48h ≥ min_stars_48h` (100).
For a repo that joined the watch list less than 48 h ago the window is shorter
(`window_hours_observed` is recorded); a shorter window can only under-count, so a candidate is
still a candidate.

**Confirmation with the star-history endpoint** (daily, endpoint day labels; see
`pigtail.capture.star_history`). If the stored series is older than `baseline_max_age_hours` it
is refreshed first (1 request, `per_page = 6`).

- *Window days*: the endpoint days from the day of `L − 48 h` to the day of `L` in `tz` (up to
  3 days: two complete days plus today so far, replan §6.3). `reference_stars` = their sum.
  The candidate is **rejected** when `reference_stars < reference_share × min_stars_48h`
  (default 0.9: allows for un-stars and day-boundary noise); it is **left for the next run**
  when no star-history day of the window is stored (fetch failed or budget stop).
- *Baseline*: the 30 endpoint days before the first window day, excluding days before the repo's
  creation. The statistics are those of velocity-v0 (ADR-027.3) with days in place of hours:
  `baseline_mean_48h` = mean stars per observed day × 2 (floored at 0, since net days can be
  negative); `baseline_std_48h` = sample std of the 15 non-overlapping 2-day block sums whose
  two days are both observed (v0's "≥ 90 % of the block observed" for 2-day blocks), when ≥ 3
  such blocks exist, else 0; `sigma_used = max(std, √mean_48h, min_sigma)`;
  `baseline_quality` = `full` (≥ 27 of 30 days), `partial` (some), `none` (no day: the rule is
  then the absolute threshold only, as in v0). `baseline_hours_covered` = days × 24.
- The case **fires** when `stars_48h ≥ min_stars_48h` and
  `z = (stars_48h − baseline_mean_48h) / sigma_used ≥ sigma` (3).

**Case.** Trigger `velocity`, deterministic id `case_id(repo, "velocity", opened_at)` with
`opened_at` = end of the UTC hour of `L`, and the shared 14-day cooldown per repo and trigger (so
v0 and v1 never open two cases for one breakout; re-running is a no-op). The `detection` block
(`DetectionV1`) records the data sources, the baseline, `coverage` (source
`github_graphql_counts`, observed = `stars_48h`, reference = star-history window sum, ratio) and
`bot_filter`: status `pending`, basis `repo_events`, `confirmed` null when per-repo events
polling is enabled (the poller fills it in), else status `unavailable`, basis `none`. Bot
filtering is a confirmation step (ADR-032.2): the case opens on net counts and records the state
of that step.

**Agreement with velocity-v0.** Each firing (opened or suppressed by cooldown) writes a
`detection_agreement` row: the v0 case within the cooldown if any, and the GH Archive filtered
stars and scanned hours for the same window. `agreement_stats()` summarises v1-only, both, and
v0-only cases for repos on the watch list.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pigtail.capture.botfilter import BOT_FILTER_VERSION
from pigtail.capture.db import CaptureDB
from pigtail.capture.models import (
    BaselineQuality,
    BotFilterStatus,
    Case,
    Coverage,
    DetectionV1,
    Repo,
    case_id,
    repo_id,
)
from pigtail.capture.runs import RunRecorder
from pigtail.capture.star_history import DAY_BOUNDARY_NOTE, DEFAULT_TZ, daily_series, local_today
from pigtail.connectors.base import FetchError
from pigtail.connectors.github_budget import BudgetExhausted

log = logging.getLogger("pigtail.capture.detection_v1")

RULE_VERSION = "detection-v1"
COUNTS_SOURCE = "github_graphql_counts"
HISTORY_SOURCE = "github_star_history"
HOUR = timedelta(hours=1)
JITTER = timedelta(minutes=5)


@dataclass(frozen=True)
class DetectionV1Config:
    min_stars_48h: int = 100
    sigma: float = 3.0
    window_hours: int = 48
    baseline_days: int = 30
    min_sigma: float = 1.0
    full_baseline_coverage: float = 0.9
    min_full_blocks: int = 3
    cooldown_hours: int = 14 * 24
    fresh_hours: int = 3
    baseline_max_age_hours: float = 2.0
    reference_share: float = 0.9
    tz: str = DEFAULT_TZ


@dataclass(frozen=True)
class DailyBaseline:
    mean_48h: float
    std_48h: float
    sigma_used: float
    days_covered: int
    quality: BaselineQuality


def daily_baseline(
    series: Mapping[date, int],
    start: date,
    cfg: DetectionV1Config,
    not_before: date | None = None,
) -> DailyBaseline:
    """velocity-v0 baseline statistics on endpoint days (module docstring)."""
    n = cfg.baseline_days
    days = [start + timedelta(days=i) for i in range(n)]
    obs = {d for d in days if d in series and (not_before is None or d >= not_before)}
    if not obs:
        return DailyBaseline(0.0, 0.0, cfg.min_sigma, 0, "none")
    mean = max(0.0, sum(series[d] for d in obs) / len(obs) * 2)
    blocks = [
        float(series[days[i]] + series[days[i + 1]])
        for i in range(0, n - 1, 2)
        if days[i] in obs and days[i + 1] in obs
    ]
    std = statistics.stdev(blocks) if len(blocks) >= max(2, cfg.min_full_blocks) else 0.0
    quality: BaselineQuality = "full" if len(obs) >= cfg.full_baseline_coverage * n else "partial"
    return DailyBaseline(mean, std, max(std, math.sqrt(mean), cfg.min_sigma), len(obs), quality)


@dataclass(frozen=True)
class V1Candidate:
    repo_host_id: int
    full_name: str
    last_at: datetime
    first_at: datetime
    stars_48h: int
    forks_48h: int
    created_at: datetime | None
    added_at: datetime

    @property
    def window_hours(self) -> float:
        return round((self.last_at - self.first_at).total_seconds() / 3600, 3)

    @property
    def detected_hour(self) -> datetime:
        return self.last_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


@dataclass
class DetectResult:
    candidates: int = 0
    fired: int = 0
    opened: list[Case] = field(default_factory=list)
    suppressed_by_cooldown: int = 0
    rejected_z: int = 0
    rejected_by_star_history: int = 0
    unconfirmed_no_star_history: int = 0
    history_fetched: int = 0
    budget_stop: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["opened"] = [c.id for c in self.opened]
        return d


HistoryFetcher = Callable[[int, str], None]


class DetectorV1:
    def __init__(
        self,
        db: CaptureDB,
        *,
        cfg: DetectionV1Config | None = None,
        fetch_history: HistoryFetcher | None = None,
        events_enabled: bool = False,
        run: RunRecorder | None = None,
    ) -> None:
        self.db = db
        self.cfg = cfg or DetectionV1Config()
        self.fetch_history = fetch_history
        self.events_enabled = events_enabled
        self.run = run

    def _incr(self, key: str, n: int = 1) -> None:
        if self.run is not None:
            self.run.incr(key, n)

    def candidates(self, at: datetime) -> list[V1Candidate]:
        cfg = self.cfg
        rows = self.db.conn.execute(
            """
            WITH last AS (
                SELECT DISTINCT ON (s.repo_host_id) s.repo_host_id, s.observed_at, s.stars, s.forks
                FROM repo_count_snapshot s
                JOIN watchlist w ON w.repo_host_id = s.repo_host_id AND w.active
                WHERE s.observed_at <= %(t)s
                  AND s.observed_at > %(t)s - make_interval(hours => %(fresh)s)
                ORDER BY s.repo_host_id, s.observed_at DESC
            ), first AS (
                SELECT DISTINCT ON (s.repo_host_id) s.repo_host_id, s.observed_at, s.stars, s.forks
                FROM repo_count_snapshot s JOIN last l ON l.repo_host_id = s.repo_host_id
                WHERE s.observed_at >= l.observed_at - make_interval(hours => %(w)s) - %(jit)s
                ORDER BY s.repo_host_id, s.observed_at ASC
            )
            SELECT l.repo_host_id, w.full_name, l.observed_at, f.observed_at,
                   l.stars - f.stars, l.forks - f.forks, w.created_at_gh, w.added_at
            FROM last l JOIN first f ON f.repo_host_id = l.repo_host_id
            JOIN watchlist w ON w.repo_host_id = l.repo_host_id
            WHERE l.stars - f.stars >= %(m)s
            ORDER BY l.repo_host_id
            """,
            {
                "t": at,
                "fresh": cfg.fresh_hours,
                "w": cfg.window_hours,
                "jit": JITTER,
                "m": cfg.min_stars_48h,
            },
        ).fetchall()
        return [V1Candidate(*r) for r in rows]

    def detect(self, at: datetime | None = None) -> DetectResult:
        at = at or datetime.now(UTC)
        res = DetectResult()
        cands = self.candidates(at)
        res.candidates = len(cands)
        for c in cands:
            try:
                self._evaluate(c, res)
            except BudgetExhausted as e:
                res.budget_stop = e.reason
                log.warning("detect-v1 stopped by budget: %s", e.reason)
                break
        for k, v in res.to_dict().items():
            if isinstance(v, int):
                self._incr(f"detect_v1.{k}", v)
        return res

    def _history_age_ok(self, c: V1Candidate) -> bool:
        row = self.db.conn.execute(
            "SELECT baseline_fetched_at FROM watchlist WHERE repo_host_id = %s",
            (c.repo_host_id,),
        ).fetchone()
        fetched = row[0] if row else None
        return fetched is not None and fetched >= c.last_at - timedelta(
            hours=self.cfg.baseline_max_age_hours
        )

    def _evaluate(self, c: V1Candidate, res: DetectResult) -> None:
        cfg = self.cfg
        if self.fetch_history is not None and not self._history_age_ok(c):
            try:
                self.fetch_history(c.repo_host_id, c.full_name)
                res.history_fetched += 1
            except FetchError as e:
                log.warning("star history fetch failed: %s", e.status)
        first_day = local_today(c.last_at - timedelta(hours=cfg.window_hours), cfg.tz)
        last_day = local_today(c.last_at, cfg.tz)
        base_start = first_day - timedelta(days=cfg.baseline_days)
        series = daily_series(self.db, c.repo_host_id, base_start, last_day)
        window_days = [
            first_day + timedelta(days=i) for i in range((last_day - first_day).days + 1)
        ]
        if not any(d in series for d in window_days):
            res.unconfirmed_no_star_history += 1
            return
        reference = sum(series.get(d, 0) for d in window_days)
        if reference < cfg.reference_share * cfg.min_stars_48h:
            res.rejected_by_star_history += 1
            return
        created = local_today(c.created_at, cfg.tz) if c.created_at else None
        b = daily_baseline(series, base_start, cfg, not_before=created)
        z = (c.stars_48h - b.mean_48h) / b.sigma_used
        if not (c.stars_48h >= cfg.min_stars_48h and z >= cfg.sigma):
            res.rejected_z += 1
            return
        res.fired += 1
        opened_at = c.detected_hour + HOUR
        rid = repo_id("github", c.repo_host_id)
        det = DetectionV1(
            rule_version="detection-v1",
            detected_hour=c.detected_hour,
            stars_48h=c.stars_48h,
            stars_48h_raw=c.stars_48h,
            forks_48h=c.forks_48h,
            baseline_mean_48h=round(b.mean_48h, 4),
            baseline_std_48h=round(b.std_48h, 4),
            sigma_used=round(b.sigma_used, 4),
            z_score=round(z, 4),
            baseline_hours_covered=b.days_covered * 24,
            baseline_quality=b.quality,
            threshold_min_stars=cfg.min_stars_48h,
            threshold_sigma=cfg.sigma,
            bot_filter_version=BOT_FILTER_VERSION,
            coverage=Coverage(
                source=COUNTS_SOURCE,
                window_start=c.first_at,
                window_end=c.last_at,
                observed_stars=max(0, c.stars_48h),
                reference_stars=max(0, reference),
                reference_source=HISTORY_SOURCE,
                ratio=round(c.stars_48h / reference, 4) if reference > 0 else None,
            ),
            data_sources=[COUNTS_SOURCE, HISTORY_SOURCE],
            window_hours_observed=c.window_hours,
            baseline_source=HISTORY_SOURCE,
            baseline_days_covered=b.days_covered,
            day_boundary_tz=DAY_BOUNDARY_NOTE,
            bot_filter=BotFilterStatus(
                status="pending" if self.events_enabled else "unavailable",
                basis="repo_events" if self.events_enabled else "none",
                version=BOT_FILTER_VERSION,
            ),
        )
        case = Case(
            id=case_id(rid, "velocity", opened_at),
            repo_id=rid,
            opened_at=opened_at,
            trigger="velocity",
            status="live",
            run_id=self.run.id if self.run else None,
            detection=det,
        )
        near = self._case_near(rid, opened_at)
        opened = False
        if near is None:
            with self.db.conn.transaction():
                self.db.upsert_repo(
                    Repo(
                        id=rid,
                        host="github",
                        host_id=c.repo_host_id,
                        full_name=c.full_name,
                        first_seen_at=c.added_at,
                        created_at=c.created_at,
                    )
                )
                opened = self.db.insert_case(case)
            if opened:
                res.opened.append(case)
        else:
            res.suppressed_by_cooldown += 1
        self._record_agreement(c, rid, case.id if opened else None, opened_at)

    def _case_near(self, rid: str, at: datetime) -> str | None:
        row = self.db.conn.execute(
            "SELECT id FROM cases WHERE repo_id = %s AND trigger = 'velocity'"
            " AND opened_at > %s - make_interval(hours => %s)"
            " AND opened_at < %s + make_interval(hours => %s) ORDER BY opened_at LIMIT 1",
            (rid, at, self.cfg.cooldown_hours, at, self.cfg.cooldown_hours),
        ).fetchone()
        return str(row[0]) if row else None

    def _record_agreement(
        self, c: V1Candidate, rid: str, v1_case: str | None, opened_at: datetime
    ) -> None:
        cd = self.cfg.cooldown_hours
        v0 = self.db.conn.execute(
            "SELECT id, opened_at FROM cases WHERE repo_id = %s AND trigger = 'velocity'"
            " AND detection ->> 'rule_version' = 'velocity-v0'"
            " AND opened_at > %s - make_interval(hours => %s)"
            " AND opened_at < %s + make_interval(hours => %s) ORDER BY opened_at LIMIT 1",
            (rid, opened_at, cd, opened_at, cd),
        ).fetchone()
        win_end = c.detected_hour
        win_start = win_end - timedelta(hours=self.cfg.window_hours - 1)
        gh = self.db.conn.execute(
            "SELECT (SELECT sum(stars_filtered) FROM repo_hourly_activity WHERE repo_host_id = %s"
            " AND hour >= %s AND hour <= %s), (SELECT count(*) FROM gharchive_hours WHERE"
            " status = 'ok' AND hour >= %s AND hour <= %s)",
            (c.repo_host_id, win_start, win_end, win_start, win_end),
        ).fetchone()
        self.db.conn.execute(
            """
            INSERT INTO detection_agreement (repo_host_id, detected_hour, v1_case_id, v0_case_id,
                v0_opened_at, gharchive_stars_48h, gharchive_hours_ok, v1_stars_48h)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_host_id, detected_hour) DO UPDATE SET
                v1_case_id = COALESCE(detection_agreement.v1_case_id, EXCLUDED.v1_case_id),
                v0_case_id = COALESCE(EXCLUDED.v0_case_id, detection_agreement.v0_case_id),
                v0_opened_at = COALESCE(EXCLUDED.v0_opened_at, detection_agreement.v0_opened_at)
            """,
            (c.repo_host_id, c.detected_hour, v1_case, v0[0] if v0 else None,
             v0[1] if v0 else None, int(gh[0]) if gh and gh[0] is not None else None,
             int(gh[1]) if gh else 0, c.stars_48h),
        )  # fmt: skip


def agreement_stats(db: CaptureDB, since: datetime) -> dict[str, Any]:
    """v1 vs velocity-v0 since `since` (repos on the watch list only, for v0-only)."""
    row = db.conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE v0_case_id IS NOT NULL),
               count(*) FILTER (WHERE v0_case_id IS NULL),
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY gharchive_stars_48h::float / NULLIF(v1_stars_48h, 0))
        FROM detection_agreement WHERE detected_hour >= %s
        """,
        (since,),
    ).fetchone()
    v0_only = db.conn.execute(
        """
        SELECT count(*) FROM cases c JOIN watchlist w ON c.repo_id = 'github:' || w.repo_host_id
        WHERE c.trigger = 'velocity' AND c.detection ->> 'rule_version' = 'velocity-v0'
          AND c.opened_at >= %s
          AND NOT EXISTS (SELECT 1 FROM detection_agreement a WHERE a.v0_case_id = c.id)
        """,
        (since,),
    ).fetchone()
    assert row is not None and v0_only is not None
    return {
        "since": since.isoformat(),
        "v1_fired": int(row[0]),
        "both": int(row[1]),
        "v1_only": int(row[2]),
        "v0_only_on_watchlist": int(v0_only[0]),
        "median_gharchive_to_v1_ratio": round(float(row[3]), 4) if row[3] is not None else None,
    }
