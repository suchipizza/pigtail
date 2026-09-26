"""Star-history daily series (M1-T24; ADR-032.3; TM-33; replan §0, §2.2).

`GET /repos/{o}/{r}/stargazers/history` returns weeks, most recent first (`per_page` ≤ 30 weeks,
`page` ≤ 100), each with `week`, `total` and `days[7]`: net counts of the *current* stargazers
bucketed by star date (sum of all weeks = current `stargazers_count`, replan §0). No identities.

**Day labels.** GitHub documents only that "Week and day boundaries are not guaranteed to align
with UTC"; small tests suggest US-Pacific days (replan §0, to be confirmed by validation M2 around
the DST change on 2026-11-01). pigtail therefore stores the endpoint's **own** labels: `day =
week label + index` in `repo_star_daily.day`, the verbatim `week_label`, and a `day_boundary_tz`
note on every row (`DAY_BOUNDARY_NOTE`). Days are never converted to UTC. `is_partial` marks the
day that was still filling when fetched ("today" in `tz`, default America/Los_Angeles), and days
after it (future days of the current week) are not stored.

**Costs.** One core request per page. `per_page = 6` covers the 30-day baseline before a 48 h
window in one call (`per_page = 5` covers only 29–35 days depending on the weekday; replan §2.2).
A full history pages backwards to the creation week (1–33 pages). Requests are conditional
(ETag): a `304` is free and re-reads the stored snapshot. Snapshot before parse, project-level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pigtail.capture.db import CaptureDB
from pigtail.capture.repos import OPEN_CASE_STATUSES
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.github import STAR_HISTORY_MAX_PAGE, GitHubConnector, StarWeek

log = logging.getLogger("pigtail.capture.github")

DEFAULT_TZ = "America/Los_Angeles"
DAY_BOUNDARY_NOTE = (
    "github-star-history-day: endpoint label, not UTC-aligned (docs); inferred US Pacific "
    "(replan §0), unconfirmed until validation M2"
)
BASELINE_PER_PAGE = 6


@dataclass
class StarHistoryResult:
    repo_host_id: int
    pages: int = 0
    weeks: int = 0
    days_stored: int = 0
    not_modified: int = 0
    total_net: int = 0
    complete: bool = False
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def local_today(now: datetime, tz: str = DEFAULT_TZ) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def known_repo_id(db: CaptureDB, repo_host_id: int) -> str | None:
    rid = f"github:{repo_host_id}"
    row = db.conn.execute("SELECT 1 FROM repos WHERE id = %s", (rid,)).fetchone()
    return rid if row else None


def store_weeks(
    db: CaptureDB,
    repo_host_id: int,
    weeks: list[StarWeek],
    *,
    fetched_at: datetime,
    evidence_id: str | None,
    tz: str = DEFAULT_TZ,
) -> int:
    """Upsert the days of `weeks` into `repo_star_daily`; returns the number of day rows."""
    today = local_today(fetched_at, tz)
    rows = []
    for w in weeks:
        for i, n in enumerate(w.days):
            day = w.week_start + timedelta(days=i)
            if day > today:
                continue  # not happened yet (current week)
            rows.append((repo_host_id, day, n, w.week_label, DAY_BOUNDARY_NOTE, day == today,
                         fetched_at, evidence_id))  # fmt: skip
    with db.conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label,
                day_boundary_tz, is_partial, fetched_at, evidence_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_host_id, day) DO UPDATE SET stars_net = EXCLUDED.stars_net,
                week_label = EXCLUDED.week_label, day_boundary_tz = EXCLUDED.day_boundary_tz,
                is_partial = EXCLUDED.is_partial, fetched_at = EXCLUDED.fetched_at,
                evidence_id = COALESCE(EXCLUDED.evidence_id, repo_star_daily.evidence_id)
            WHERE repo_star_daily.fetched_at <= EXCLUDED.fetched_at
            """,
            rows,
        )
    return len(rows)


def fetch_star_history(
    conn: GitHubConnector,
    db: CaptureDB,
    repo_host_id: int,
    full_name: str,
    *,
    per_page: int = BASELINE_PER_PAGE,
    max_pages: int = 1,
    tz: str = DEFAULT_TZ,
    run: RunRecorder | None = None,
) -> StarHistoryResult:
    """Fetch `max_pages` pages (or until the creation week) and store the daily series.

    Raises `BudgetExhausted` / `FetchError` from the connector; pages stored before that stay.
    """
    if conn.evidence_sink is None:
        conn.evidence_sink = db.upsert_evidence
    res = StarHistoryResult(repo_host_id)
    rid = known_repo_id(db, repo_host_id)
    max_pages = min(max_pages, STAR_HISTORY_MAX_PAGE)
    for page in range(1, max_pages + 1):
        c, weeks = conn.star_history(full_name, per_page=per_page, page=page, repo_id=rid)
        res.pages += 1
        prev_ev = c.previous.evidence_id if c.previous else None
        ev = c.fetched.evidence.id if c.fetched else prev_ev
        at = c.fetched.meta.fetched_at if c.fetched else conn.clock()
        if c.not_modified:
            res.not_modified += 1
        if ev:
            res.evidence_ids.append(ev)
        res.weeks += len(weeks)
        res.total_net += sum(w.total for w in weeks)
        res.days_stored += store_weeks(db, repo_host_id, weeks, fetched_at=at, evidence_id=ev,
                                       tz=tz)  # fmt: skip
        if len(weeks) < per_page:
            res.complete = True  # "Pages move backward toward the repository's creation week"
            break
    now = conn.clock()
    db.conn.execute(
        "INSERT INTO star_history_fetch (repo_host_id, fetched_at, per_page, pages, weeks,"
        " not_modified, total_net, complete, run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT DO NOTHING",
        (repo_host_id, now, per_page, res.pages, res.weeks, res.not_modified, res.total_net,
         res.complete, run.id if run else None),
    )  # fmt: skip
    if run is not None:
        run.incr("star_history.pages", res.pages)
        run.incr("star_history.not_modified", res.not_modified)
        run.incr("star_history.repos")
    return res


def due_case_repos(
    db: CaptureDB, *, refresh: timedelta = timedelta(hours=20), now: datetime | None = None
) -> list[tuple[int, str]]:
    """GitHub repos of open cases whose star history was never fetched or is older than
    `refresh` (`capture github star-history --cases`). Most recently opened cases first.
    `now` defaults to the current time; tests pass their fixed clock."""
    rows = db.conn.execute(
        """
        SELECT r.host_id, r.full_name FROM repos r
        WHERE r.host = 'github' AND r.host_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM cases c WHERE c.repo_id = r.id AND c.status = ANY(%s))
          AND coalesce((SELECT max(f.fetched_at) FROM star_history_fetch f
                        WHERE f.repo_host_id = r.host_id), '-infinity') < %s
        ORDER BY (SELECT max(c.opened_at) FROM cases c WHERE c.repo_id = r.id) DESC, r.host_id
        """,
        (list(OPEN_CASE_STATUSES), (now or datetime.now(UTC)) - refresh),
    ).fetchall()
    return [(int(h), str(n)) for h, n in rows]


def daily_series(db: CaptureDB, repo_host_id: int, start: date, end: date) -> dict[date, int]:
    """Stored endpoint days in [start, end] (inclusive): day label -> net stars."""
    rows = db.conn.execute(
        "SELECT day, stars_net FROM repo_star_daily WHERE repo_host_id = %s"
        " AND day >= %s AND day <= %s",
        (repo_host_id, start, end),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}
