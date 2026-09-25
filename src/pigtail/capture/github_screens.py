"""Candidate screens that feed the watch list (M1-T24; ADR-032.1; replan §2.1, §6.1 layer 1).

**Search sweeps** (`SearchSweeper`, search bucket). GitHub Search returns at most 1,000 results per
query (10 pages of 100) and has no "starred since" qualifier, so it finds repos, it does not
measure velocity. A sweep is a set of *slices* `<date_field>:<from>..<to> stars:<lo>..<hi>`:

- `new`: repos created in the last `new_days` (default 7) with ≥ `new_min_stars` (20) stars;
- `active`: repos pushed in the last `active_days` (1) with stars in `active_bands`
  (default 50..5000, replan §2.1).

Page 1 of each slice gives `total_count`. If it is ≤ 1,000 the slice is paged to the end (stopping
at a short page). Otherwise it is split and each half swept: first by **stars** (an open upper
bound is closed with the top result's star count, since results are sorted by stars; the split
point is the geometric mean for wide ranges, the midpoint otherwise), then, once the star range
is a single value, by **time** (halves, down to `min_span`, 1 hour). A slice that still holds
more than 1,000 results is paged to 1,000 and counted as `truncated`. `incomplete_results` pages
are counted. Every hit is nominated with `source = search` (`source_ref = search:new|active`).
Search pages are snapshotted as person-level (items embed owner objects); only repo id, node id,
name, counts, dates and the owner *type* are parsed.

**HN screen** (`hn_screen`). GitHub URLs of stories the HN rank poller saw in the last `window`
(`hn_story.repo_full_name`, ADR-031) are nominated with `source = hn` (`source_ref = hn:<id>`,
or `show_hn:<id>` for "Show HN" titles). With `show = True`, the `showstories` list (up to 200
ids, project-level snapshot) is read through the project-level `hn_ranks` connector; items not
seen before are fetched once, their GitHub URL kept in `hn_show_screen`, and their raw bytes
dropped right after parsing (they contain `by`; same rule as the rank poller).

**GH Archive screen** (`gharchive_screen`): repos with ≥ `min_stars` raw GH Archive stars in the
last 48 h (`repo_hourly_activity`) are nominated with `source = gharchive`. GH Archive's ~2 %
capture makes this a weak but free screen (ADR-028, ADR-032.1).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pigtail.capture.db import CaptureDB
from pigtail.capture.github_watch import Watchlist
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import FetchError
from pigtail.connectors.github import SEARCH_MAX_RESULTS, GitHubConnector, SearchPage
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.connectors.hn import list_url, parse_id_list
from pigtail.connectors.hn_ranks import HNRanksConnector
from pigtail.privacy.deletion import DeletionLog, drop_after_parse

log = logging.getLogger("pigtail.capture.github")

PER_PAGE = 100
DateField = Literal["created", "pushed"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SearchSlice:
    date_field: DateField
    date_from: datetime
    date_to: datetime
    stars_lo: int
    stars_hi: int | None  # None = open upper bound

    def query(self) -> str:
        hi = "*" if self.stars_hi is None else str(self.stars_hi)
        return (
            f"{self.date_field}:{_iso(self.date_from)}..{_iso(self.date_to)} "
            f"stars:{self.stars_lo}..{hi}"
        )

    def split(self, top_stars: int | None, min_span: timedelta) -> list[SearchSlice] | None:
        """Two halves covering this slice, or None if it can't be split further."""
        hi = self.stars_hi if self.stars_hi is not None else top_stars
        if hi is not None and hi > self.stars_lo:
            lo = self.stars_lo
            if lo > 0 and hi / lo > 4:
                mid = max(lo, min(hi - 1, math.isqrt(lo * hi)))
            else:
                mid = lo + (hi - lo) // 2
            return [replace(self, stars_hi=mid), replace(self, stars_lo=mid + 1, stars_hi=hi)]
        if self.date_to - self.date_from > min_span:
            mid_t = self.date_from + (self.date_to - self.date_from) / 2
            mid_t = mid_t.replace(microsecond=0)
            return [
                replace(self, date_to=mid_t, stars_hi=hi),
                replace(self, date_from=mid_t + timedelta(seconds=1), stars_hi=hi),
            ]
        return None


@dataclass(frozen=True)
class SweepConfig:
    new_days: int = 7
    new_min_stars: int = 20
    active_days: int = 1
    active_bands: tuple[tuple[int, int], ...] = ((50, 5000),)
    min_span: timedelta = timedelta(hours=1)
    max_depth: int = 16

    def slices(self, kind: Literal["new", "active", "all"], now: datetime) -> list[SearchSlice]:
        out: list[SearchSlice] = []
        if kind in ("new", "all"):
            out.append(
                SearchSlice(
                    "created", now - timedelta(days=self.new_days), now, self.new_min_stars, None
                )
            )
        if kind in ("active", "all"):
            for lo, hi in self.active_bands:
                out.append(
                    SearchSlice("pushed", now - timedelta(days=self.active_days), now, lo, hi)
                )
        return out


@dataclass
class SweepResult:
    slices: int = 0
    splits: int = 0
    truncated: int = 0
    pages: int = 0
    incomplete_pages: int = 0
    hits: int = 0
    added: int = 0
    reactivated: int = 0
    skipped: int = 0
    failed_pages: int = 0
    budget_stop: str | None = None
    queries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("queries")
        return d


class SearchSweeper:
    def __init__(
        self,
        conn: GitHubConnector,
        db: CaptureDB,
        *,
        cfg: SweepConfig | None = None,
        run: RunRecorder | None = None,
    ) -> None:
        self.conn = conn
        self.db = db
        self.cfg = cfg or SweepConfig()
        self.run = run
        self.watch = Watchlist(db, conn.suppression)
        if conn.evidence_sink is None:
            conn.evidence_sink = db.upsert_evidence

    def sweep(self, kind: Literal["new", "active", "all"] = "all") -> SweepResult:
        res = SweepResult()
        now = self.conn.clock().replace(microsecond=0)
        try:
            for sl in self.cfg.slices(kind, now):
                ref = "search:new" if sl.date_field == "created" else "search:active"
                self._slice(sl, 0, ref, res)
        except BudgetExhausted as e:
            res.budget_stop = e.reason
            log.warning("search sweep stopped by budget: %s", e.reason)
        if self.run is not None:
            for k, v in res.to_dict().items():
                if isinstance(v, int):
                    self.run.incr(f"search.{k}", v)
            if res.budget_stop:
                self.run.incr(f"budget_stop.{res.budget_stop}")
        return res

    def _page(self, sl: SearchSlice, page: int, ref: str, res: SweepResult) -> SearchPage | None:
        try:
            _, sp = self.conn.search_repositories(sl.query(), page=page, per_page=PER_PAGE)
        except FetchError as e:
            if e.status == 422 and page > 1:  # past the end of the results
                return None
            res.failed_pages += 1
            log.warning("search page %d failed: %s", page, e.status)
            return None
        res.pages += 1
        res.incomplete_pages += int(sp.incomplete_results)
        for it in sp.items:
            res.hits += 1
            got = self.watch.nominate(
                "search",
                it.full_name,
                repo_host_id=it.id,
                node_id=it.node_id,
                owner_type=it.owner_type,
                created_at=it.created_at,
                source_ref=ref,
            )
            if got == "added":
                res.added += 1
            elif got == "reactivated":
                res.reactivated += 1
            elif got == "skipped":
                res.skipped += 1
        return sp

    def _slice(self, sl: SearchSlice, depth: int, ref: str, res: SweepResult) -> None:
        res.slices += 1
        first = self._page(sl, 1, ref, res)
        if first is None:
            return
        total = first.total_count
        res.queries.append({"q": sl.query(), "total": total, "depth": depth})
        if total > SEARCH_MAX_RESULTS and depth < self.cfg.max_depth:
            top = first.items[0].stars if first.items else None
            halves = sl.split(top, self.cfg.min_span)
            if halves is not None:
                res.splits += 1
                for h in halves:
                    self._slice(h, depth + 1, ref, res)
                return
        if total > SEARCH_MAX_RESULTS:
            res.truncated += 1
        pages = math.ceil(min(total, SEARCH_MAX_RESULTS) / PER_PAGE)
        last = first
        for p in range(2, pages + 1):
            if len(last.items) < PER_PAGE:
                break
            nxt = self._page(sl, p, ref, res)
            if nxt is None:
                break
            last = nxt


# --- HN screen ---------------------------------------------------------------------------------
@dataclass
class HNScreenResult:
    stories: int = 0
    nominated: int = 0
    show_ids: int = 0
    show_items_fetched: int = 0
    show_with_repo: int = 0
    raw_dropped: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _is_show_hn(title: Any) -> bool:
    return isinstance(title, str) and title.lower().startswith("show hn")


def hn_screen(
    db: CaptureDB,
    watch: Watchlist,
    *,
    window: timedelta = timedelta(hours=48),
    hn: HNRanksConnector | None = None,
    max_show_items: int = 200,
    now: datetime | None = None,
    run: RunRecorder | None = None,
) -> HNScreenResult:
    res = HNScreenResult()
    now = now or datetime.now(UTC)
    rows = db.conn.execute(
        "SELECT item_id, repo_full_name, title FROM hn_story WHERE repo_full_name IS NOT NULL"
        " AND NOT deleted AND NOT dead AND last_seen_at >= %s ORDER BY item_id",
        (now - window,),
    ).fetchall()
    for item_id, full_name, title in rows:
        res.stories += 1
        ref = f"{'show_hn' if _is_show_hn(title) else 'hn'}:{item_id}"
        _nominate(watch, full_name, ref, now, res)
    if hn is not None and hn.enabled:
        _show_hn(db, watch, hn, max_show_items, now, res, run)
    if run is not None:
        for k, v in res.to_dict().items():
            run.incr(f"hn_screen.{k}", v)
    return res


def _nominate(watch: Watchlist, full_name: str, ref: str, now: datetime, res: Any) -> None:
    try:
        got = watch.nominate("hn", full_name, source_ref=ref, at=now)
    except ValueError:
        return
    if got == "skipped":
        res.skipped += 1
    else:
        res.nominated += 1


def _show_hn(
    db: CaptureDB,
    watch: Watchlist,
    hn: HNRanksConnector,
    max_items: int,
    now: datetime,
    res: HNScreenResult,
    run: RunRecorder | None,
) -> None:
    if hn.evidence_sink is None:
        hn.evidence_sink = db.upsert_evidence
    try:
        f = hn.fetch(list_url("showstories"), retention_class="project_level")
    except FetchError as e:
        log.warning("showstories fetch failed: %s", e.status)
        return
    ids = parse_id_list(f.data)
    res.show_ids = len(ids)
    seen = {
        r[0]
        for r in db.conn.execute(
            "SELECT item_id FROM hn_show_screen WHERE item_id = ANY(%s)"
            " UNION SELECT item_id FROM hn_story WHERE item_id = ANY(%s)",
            (ids, ids),
        )
    }
    dlog = DeletionLog(db, "retention", run_id=run.id if run else None)
    for iid in [i for i in ids if i not in seen][:max_items]:
        try:
            item = hn.fetch_story(iid)
        except FetchError:
            continue
        res.show_items_fetched += 1
        try:
            rec = hn.story_record(item)
        except ValueError:
            rec = None
        finally:
            if drop_after_parse(db, hn.store, item.evidence.id, item.content_hash, dlog):
                res.raw_dropped += 1
        full_name = rec.get("repo_full_name") if rec else None
        db.conn.execute(
            "INSERT INTO hn_show_screen (item_id, repo_full_name, seen_at, evidence_id)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (item_id) DO NOTHING",
            (iid, full_name, now, item.evidence.id),
        )
        if full_name:
            res.show_with_repo += 1
            _nominate(watch, full_name, f"show_hn:{iid}", now, res)


# --- GH Archive screen -------------------------------------------------------------------------
def gharchive_screen(
    db: CaptureDB,
    watch: Watchlist,
    *,
    min_stars: int = 10,
    window: timedelta = timedelta(hours=48),
    now: datetime | None = None,
    run: RunRecorder | None = None,
) -> int:
    now = now or datetime.now(UTC)
    rows = db.conn.execute(
        "SELECT repo_host_id, (array_agg(repo_name ORDER BY hour DESC))[1], sum(stars_raw)"
        " FROM repo_hourly_activity WHERE hour > %s AND hour <= %s GROUP BY repo_host_id"
        " HAVING sum(stars_raw) >= %s ORDER BY repo_host_id",
        (now - window, now, min_stars),
    ).fetchall()
    n = 0
    for host_id, name, _stars in rows:
        try:
            if watch.nominate("gharchive", name, repo_host_id=host_id, at=now) != "skipped":
                n += 1
        except ValueError:
            continue
    if run is not None:
        run.incr("gharchive_screen.nominated", n)
    return n
