"""settle_lag collection for the threshold calibration (K2; M4-T4).

Pre-registration `docs/preregistration/2026-09-25-threshold-calibration.md` §2.1 K2: each sampled
star-history source-day is fetched at lags ℓ ∈ {1, 3, 7, 14, 21} days after the day ended (in the
endpoint's own calendar, `star_history_day_tz`); ℓ = 21 is the reference. This module only
**collects**; the statistic (`|v(ℓ) − v(21)| ≤ max(1, 0.005·v(21))`) and the sample draw are the
calibration's job and are not computed here.

**Enrolment.** Once per endpoint day, the most recent *completed* day (yesterday in `tz`) of each
eligible repo is enrolled with five scheduled re-fetches (`settle_lag_schedule`,
`due_at = end of day + ℓ`). Eligible: star history already fetched (`star_history_fetch`), active
on the watch list, not on the refusal list, and every case of the repo is in the **calibration**
split (K2 "repos … of calibration-split cases only"; held-out repos are never enrolled). At most
`max_repos` repos are enrolled at a time (already-enrolled repos first).

**Fetching.** Every due item of a repo is served by one request (`per_page = 5` covers ≥ 29 days,
so ℓ = 21 is always inside the first page). Each value is stored as a new, append-only row in
`star_history_settle_obs` with the actual lag in hours, the week label, the evidence id of the
snapshot and whether the answer was a 304. An item not fetched within `max_late` of its due time
is marked `missed` (its lag would no longer be ℓ). `repo_star_daily` is not touched.

**Cost.** With daily enrolment the five lags of a repo fall due at the same hour, so a repo costs
about one core request per day. Default off until `GITHUB_TOKEN` is set (scheduler skip
`missing_env:GITHUB_TOKEN`).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pigtail.analysis.split import split_of
from pigtail.capture.db import CaptureDB
from pigtail.capture.runs import RunRecorder
from pigtail.capture.star_history import DAY_BOUNDARY_NOTE, DEFAULT_TZ, known_repo_id, local_today
from pigtail.connectors.base import FetchError
from pigtail.connectors.github import GitHubConnector
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.privacy.suppression import Suppressions

log = logging.getLogger("pigtail.capture.settle_lag")

LAGS: tuple[int, ...] = (1, 3, 7, 14, 21)
PER_PAGE = 5


@dataclass(frozen=True)
class SettleLagConfig:
    max_repos: int = 100  # repos enrolled at the same time
    tz: str = DEFAULT_TZ
    max_late: timedelta = timedelta(hours=24)


DEFAULT_CONFIG = SettleLagConfig()


@dataclass
class SettleLagResult:
    enrolled_repos: int = 0
    enrolled_items: int = 0
    due_repos: int = 0
    fetched_items: int = 0
    unknown_items: int = 0  # the day was not in the response (stored with stars_net NULL)
    missed_items: int = 0
    dropped_items: int = 0  # repo opted out or left the calibration split
    failed_repos: int = 0
    budget_stop: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def day_end(day: date, tz: str = DEFAULT_TZ) -> datetime:
    """End of endpoint day `day` (midnight after it, in `tz`)."""
    return datetime.combine(day + timedelta(days=1), time(0), ZoneInfo(tz))


def due_at(day: date, lag_days: int, tz: str = DEFAULT_TZ) -> datetime:
    return day_end(day, tz) + timedelta(days=lag_days)


def _calibration_only_repo(case_ids: list[str]) -> bool:
    return bool(case_ids) and all(split_of(c) == "calibration" for c in case_ids)


def eligible_repos(db: CaptureDB, suppression: Suppressions, limit: int) -> list[tuple[int, str]]:
    """(repo_host_id, full_name) of repos that may be enrolled, already-enrolled ones first."""
    rows = db.conn.execute(
        """
        SELECT w.repo_host_id, w.full_name,
               array_agg(c.id ORDER BY c.id) FILTER (WHERE c.id IS NOT NULL) AS case_ids,
               EXISTS (SELECT 1 FROM settle_lag_schedule s WHERE s.repo_host_id = w.repo_host_id
                         AND s.status = 'pending') AS enrolled,
               max(c.opened_at) AS last_case
        FROM watchlist w
        LEFT JOIN cases c ON c.repo_id = 'github:' || w.repo_host_id::text
        WHERE w.active AND w.repo_host_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM star_history_fetch f WHERE f.repo_host_id = w.repo_host_id)
        GROUP BY w.repo_host_id, w.full_name
        ORDER BY enrolled DESC, last_case DESC NULLS LAST, w.repo_host_id
        """
    ).fetchall()
    out: list[tuple[int, str]] = []
    for host_id, name, case_ids, _enrolled, _last in rows:
        if f"github:{host_id}" in suppression.repos or suppression.name_suppressed(name):
            continue
        if not _calibration_only_repo(list(case_ids or [])):
            continue
        out.append((int(host_id), str(name)))
        if len(out) >= limit:
            break
    return out


def enroll(
    db: CaptureDB,
    now: datetime,
    suppression: Suppressions,
    cfg: SettleLagConfig = DEFAULT_CONFIG,
) -> tuple[int, int]:
    """Enrol yesterday's endpoint day for each eligible repo; returns (repos, new items)."""
    day = local_today(now, cfg.tz) - timedelta(days=1)
    repos = eligible_repos(db, suppression, cfg.max_repos)
    items = 0
    with db.conn.cursor() as cur:
        for host_id, _name in repos:
            for lag in LAGS:
                cur.execute(
                    "INSERT INTO settle_lag_schedule (repo_host_id, day, lag_days, due_at,"
                    " enrolled_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (host_id, day, lag, due_at(day, lag, cfg.tz), now),
                )
                items += cur.rowcount
    return len(repos), items


def _drop_ineligible(db: CaptureDB, suppression: Suppressions, now: datetime) -> int:
    """Pending items of repos that opted out or now have a held-out case are dropped."""
    rows = db.conn.execute(
        """
        SELECT s.repo_host_id, w.full_name,
               array_agg(DISTINCT c.id) FILTER (WHERE c.id IS NOT NULL)
        FROM settle_lag_schedule s
        LEFT JOIN watchlist w ON w.repo_host_id = s.repo_host_id
        LEFT JOIN cases c ON c.repo_id = 'github:' || s.repo_host_id::text
        WHERE s.status = 'pending'
        GROUP BY s.repo_host_id, w.full_name
        """
    ).fetchall()
    bad = [
        int(h)
        for h, name, cases in rows
        if f"github:{h}" in suppression.repos
        or suppression.name_suppressed(name)
        or not _calibration_only_repo(list(cases or []))
    ]
    if not bad:
        return 0
    return db.conn.execute(
        "UPDATE settle_lag_schedule SET status = 'dropped', done_at = %s"
        " WHERE status = 'pending' AND repo_host_id = ANY(%s)",
        (now, bad),
    ).rowcount


def _full_name(db: CaptureDB, host_id: int) -> str | None:
    row = db.conn.execute(
        "SELECT full_name FROM watchlist WHERE repo_host_id = %s UNION ALL"
        " SELECT full_name FROM repos WHERE host = 'github' AND host_id = %s LIMIT 1",
        (host_id, host_id),
    ).fetchone()
    return str(row[0]) if row else None


def collect(
    conn: GitHubConnector,
    db: CaptureDB,
    suppression: Suppressions,
    *,
    cfg: SettleLagConfig = DEFAULT_CONFIG,
    run: RunRecorder | None = None,
    now: datetime | None = None,
) -> SettleLagResult:
    """One scheduler run: enrol, mark late items missed, fetch everything due."""
    if conn.evidence_sink is None:
        conn.evidence_sink = db.upsert_evidence
    now = now or conn.clock()
    res = SettleLagResult()
    res.dropped_items = _drop_ineligible(db, suppression, now)
    res.enrolled_repos, res.enrolled_items = enroll(db, now, suppression, cfg)
    res.missed_items = db.conn.execute(
        "UPDATE settle_lag_schedule SET status = 'missed', done_at = %s"
        " WHERE status = 'pending' AND due_at < %s",
        (now, now - cfg.max_late),
    ).rowcount
    due: dict[int, list[tuple[date, int, datetime]]] = defaultdict(list)
    for host_id, day, lag, at in db.conn.execute(
        "SELECT repo_host_id, day, lag_days, due_at FROM settle_lag_schedule"
        " WHERE status = 'pending' AND due_at <= %s ORDER BY due_at, repo_host_id, day, lag_days",
        (now,),
    ).fetchall():
        due[int(host_id)].append((day, int(lag), at))
    res.due_repos = len(due)
    for host_id, items in due.items():
        name = _full_name(db, host_id)
        if name is None:
            continue
        try:
            c, weeks = conn.star_history(
                name, per_page=PER_PAGE, page=1, repo_id=known_repo_id(db, host_id)
            )
        except BudgetExhausted as e:
            res.budget_stop = e.reason
            break
        except FetchError:  # one bad repo (404, 5xx) must not stop the queue; retried next run
            res.failed_repos += 1
            if run is not None:
                run.incr("settle_lag.failed")
            continue
        prev_ev = c.previous.evidence_id if c.previous else None
        ev = c.fetched.evidence.id if c.fetched else prev_ev
        at = c.fetched.meta.fetched_at if c.fetched else conn.clock()
        today = local_today(at, cfg.tz)
        by_day: dict[date, tuple[int, str]] = {}
        for w in weeks:
            for i, n in enumerate(w.days):
                by_day[w.week_start + timedelta(days=i)] = (n, w.week_label)
        with db.conn.transaction():
            for day, lag, due_time in items:
                got = by_day.get(day)
                if got is None:
                    res.unknown_items += 1
                lag_h = (at - day_end(day, cfg.tz)).total_seconds() / 3600
                db.conn.execute(
                    "INSERT INTO star_history_settle_obs (repo_host_id, day, lag_days, due_at,"
                    " fetched_at, lag_hours_actual, stars_net, week_label, is_partial,"
                    " not_modified, day_boundary_tz, evidence_id, run_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (repo_host_id, day, lag_days) DO NOTHING",
                    (host_id, day, lag, due_time, at, round(lag_h, 3),
                     got[0] if got else None, got[1] if got else None,
                     day == today if got else None, c.not_modified, DAY_BOUNDARY_NOTE, ev,
                     run.id if run else None),
                )  # fmt: skip
                db.conn.execute(
                    "UPDATE settle_lag_schedule SET status = 'fetched', done_at = %s"
                    " WHERE repo_host_id = %s AND day = %s AND lag_days = %s",
                    (at, host_id, day, lag),
                )
                res.fetched_items += 1
    if run is not None:
        for k in ("enrolled_items", "fetched_items", "missed_items", "dropped_items"):
            run.incr(f"settle_lag.{k}", getattr(res, k))
    return res
