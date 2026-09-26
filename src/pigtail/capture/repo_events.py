"""Per-repo events polling for tracked cases and the bot-filter confirmation step (M1-T24;
ADR-032.2; TM-33; ADR-022/ADR-036; CB-22, CB-23).

**What is polled.** Only repos with a live case opened in the last `case_days` (default 14)
(TM-33: open cases and tracked repos; nothing else). The watch-list "pre-threshold" targets were
removed with the watch list in M11 (ADR-047.6).

**Cadence.** A repo is due when `max(interval, X-Poll-Interval)` has passed since its last poll (15
min minimum, replan §6.1), never faster than GitHub's `X-Poll-Interval` (60 s measured). Page 1 is
requested with `If-None-Match`; a `304` costs no rate limit and ends the poll. Pages 2–3 (the
300-event window) are read while the previous page is full and all its events are newer than the
newest event of the previous poll (on a repo's first poll: while pages are full, to cover as much of
the case window as GitHub keeps). If page 3 is full and still newer than the previous poll, events
may have rolled out unseen: the poll is marked `overflow` (replan §1.3, §8 M5).

**Minimisation; counts only** (CB-23; Directive §8.1, ADR-066.1, ADR-071.2). The connector keeps
only `WatchEvent` and `ForkEvent` (CB-23) and codes each actor in memory: the bot rule sets
`automated_account` (a missing login counts as automated), and a per-poll token (a keyed hash
under a random key that lives only in this process) lets the poll count each account once. The
login and the token are then discarded: **no actor, handle or pseudonym is stored**. Each events
page's raw bytes are dropped right after parsing (`drop_after_parse`: hash and URL kept, evidence
`raw_dropped`, tombstone in `deletion_log`). A page that fails to parse (malformed JSON, wrong
shape) is dropped the same way at once (CB-23b; `drop_unparseable`), its ETag is forgotten so the
next poll fetches it again, and the failure is counted in the run record
(`repo_events.parse_failed`, `github_events.parse_failed.<ExceptionType>`), never its content.

What is stored: project-level counts per repo and hour (`repo_event_hourly_agg`) and per day
(`repo_event_daily_agg`), with the bot-rule version, plus one `repo_event_poll` row per poll.
An event is new when its `(created_at, id)` is above the repo's watermark (`repo_event_poll.
watermark_at`, `newest_event_id`: the newest event counted so far), so no event is counted twice
across polls.
De-duplication of accounts happens **within one poll only**: an account that stars, unstars and
stars again across two polls counts twice (a known limitation of storing no identities).

**Never a stargazer list.** Nothing about individual stargazers is stored, so no function, CLI
command or API path can list them (tested in `tests/unit/test_github_privacy_m1t24.py`).

**Bot filter (existing heuristics, `pigtail.capture.botfilter`).** Layer 1, login rules, is
applied in memory to every event (`stars_automated`). Layer 2, the lockstep burst rule,
needs to know whether a stargazer did *anything else* on GitHub in the filter window; per-repo
events for one repo cannot show that (and CB-23 keeps only stars and forks), so treating every
stargazer of the repo as "star-only" would flag organic bursts. Layer 2 is therefore reported as
not applied (`stars_lockstep = null`, `layers = ["login_rules"]`) rather than guessed.
For each open detection-v1 case of the repo the `bot_filter` block becomes `applied` with:
stars from non-automated accounts counted in the window (`stars_seen`, sum of the hourly counts
of the hours overlapping the window), automated stars (`stars_bot`), `stars_filtered = stars_seen`,
the oldest event seen by any poll (`events_from`), whether any poll in the window overflowed,
`coverage_ratio = stars_seen / reference_stars` (star-history), and
`confirmed = reference_stars − stars_bot ≥ threshold_min_stars` (R1.1 after bot filtering).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from pigtail.capture.botfilter import BOT_FILTER_VERSION
from pigtail.capture.db import CaptureDB
from pigtail.capture.models import BotFilterStatus
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import Fetched, FetchError, NotFound, Record
from pigtail.connectors.github import GitHubRepoEventsConnector, events_url, full_url
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.privacy.deletion import (
    PARSE_ERRORS,
    DeletionLog,
    drop_after_parse,
    drop_unparseable,
)

log = logging.getLogger("pigtail.capture.repo_events")

MAX_PAGES = 3  # 300-event window at per_page=100


@dataclass(frozen=True)
class EventsConfig:
    case_interval_min: int = 15
    case_days: int = 14

    def __post_init__(self) -> None:
        if self.case_interval_min < 15:
            raise ValueError("per-repo events are polled every 15-60 min (ADR-032.2, TM-33)")


@dataclass(frozen=True)
class Target:
    repo_host_id: int
    full_name: str
    interval: timedelta
    kind: str  # "case"


@dataclass
class PollStats:
    targets: int = 0
    due: int = 0
    polled: int = 0
    not_modified: int = 0
    pages: int = 0
    events_kept: int = 0
    events_new: int = 0
    overflow: int = 0
    raw_dropped: int = 0
    parse_failed: int = 0
    not_found: int = 0
    failed: int = 0
    cases_updated: int = 0
    budget_stop: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _ts(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_id(v: Any) -> int | None:
    """GitHub event ids are increasing decimal strings; anything else is unusable."""
    if isinstance(v, str) and v.isdigit() and len(v) <= 18:
        return int(v)
    return None


@dataclass
class _Hour:
    stars: int = 0
    stars_automated: int = 0
    forks: int = 0
    forks_automated: int = 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.stars, self.stars_automated, self.forks, self.forks_automated)

    def merge(self, o: _Hour) -> None:
        self.stars += o.stars
        self.stars_automated += o.stars_automated
        self.forks += o.forks
        self.forks_automated += o.forks_automated


@dataclass
class _Counts:
    """One poll's counts, in memory. Account tokens de-duplicate within the poll and are
    dropped with this object; they are never written anywhere."""

    hours: dict[datetime, _Hour] = field(default_factory=dict)
    top: tuple[datetime, int] | None = None  # newest (created_at, id) counted in this poll
    seen_ids: set[int] = field(default_factory=set)
    _accounts: set[tuple[datetime, str, str]] = field(default_factory=set, repr=False)

    def add(self, r: Record, created: datetime) -> None:
        hour = created.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        h = self.hours.setdefault(hour, _Hour())
        star = r.get("type") == "WatchEvent"
        if r.get("automated_account"):
            if star:
                h.stars_automated += 1
            else:
                h.forks_automated += 1
            return
        token = r.get("_actor_token")
        if isinstance(token, str):
            key = (hour, str(r.get("type")), token)
            if key in self._accounts:
                return  # the same account again within this poll and hour
            self._accounts.add(key)
        if star:
            h.stars += 1
        else:
            h.forks += 1


def page_bounds(data: bytes) -> tuple[int, datetime | None, datetime | None]:
    """(events on the page, oldest and newest `created_at`) of a raw events page.

    Reads only `created_at`: no actor field is touched here.
    """
    doc = json.loads(data)
    if not isinstance(doc, list):
        return 0, None, None
    times = [t for t in (_ts(e.get("created_at")) for e in doc if isinstance(e, dict)) if t]
    return len(doc), (min(times) if times else None), (max(times) if times else None)


class RepoEventsPoller:
    def __init__(
        self,
        conn: GitHubRepoEventsConnector,
        db: CaptureDB,
        *,
        cfg: EventsConfig | None = None,
        run: RunRecorder | None = None,
    ) -> None:
        self.conn = conn
        self.db = db
        self.cfg = cfg or EventsConfig()
        self.run = run
        if conn.evidence_sink is None:
            conn.evidence_sink = db.upsert_evidence
        self.dlog = DeletionLog(db, "retention", run_id=run.id if run else None)

    # --- targets -----------------------------------------------------------------------------
    def targets(self, now: datetime) -> list[Target]:
        cfg = self.cfg
        rows = self.db.conn.execute(
            """
            SELECT DISTINCT r.host_id, r.full_name
            FROM cases c JOIN repos r ON r.id = c.repo_id AND r.host = 'github'
            WHERE c.status = 'live' AND c.opened_at >= %s - make_interval(days => %s)
            ORDER BY 1
            """,
            (now, cfg.case_days),
        ).fetchall()
        out = [
            Target(int(h), str(n), timedelta(minutes=cfg.case_interval_min), "case")
            for h, n in rows
        ]
        return out

    def is_due(self, t: Target, now: datetime) -> bool:
        key = full_url(events_url(t.full_name), {"per_page": 100, "page": 1})
        e = self.conn.cache.get(key)
        if e is None or e.fetched_at is None:
            return True
        wait = max(t.interval, timedelta(seconds=e.poll_interval_s or 0))
        return e.fetched_at + wait <= now

    # --- polling -----------------------------------------------------------------------------
    def poll_due(self, now: datetime | None = None) -> PollStats:
        now = now or self.conn.clock()
        st = PollStats()
        targets = self.targets(now)
        st.targets = len(targets)
        try:
            for t in targets:
                if not self.is_due(t, now):
                    continue
                st.due += 1
                try:
                    self.poll_repo(t, st)
                except NotFound:
                    st.not_found += 1
                except FetchError as e:
                    st.failed += 1
                    log.warning("events poll failed: %s", e.status)
        except BudgetExhausted as e:
            st.budget_stop = e.reason
        if self.run is not None:
            for k, v in st.to_dict().items():
                if isinstance(v, int):
                    self.run.incr(f"repo_events.{k}", v)
        return st

    def _previous_newest(self, repo_host_id: int) -> datetime | None:
        row = self.db.conn.execute(
            "SELECT max(newest_event_at) FROM repo_event_poll WHERE repo_host_id = %s",
            (repo_host_id,),
        ).fetchone()
        return row[0] if row else None

    def poll_repo(self, t: Target, st: PollStats) -> None:
        prev_newest = self._previous_newest(t.repo_host_id)
        watermark = self._watermark(t.repo_host_id)
        pages = kept = new = 0
        overflow = False
        newest: datetime | None = None
        oldest_seen: datetime | None = None
        status = 200
        poll_interval: int | None = None
        counts = _Counts()
        for page in range(1, MAX_PAGES + 1):
            c = self.conn.repo_events(t.full_name, page=page)
            pages += 1
            poll_interval = poll_interval or c.poll_interval_s
            if c.fetched is None:  # 304: nothing new on this page
                status = 304 if page == 1 else status
                break
            got = self._ingest(t, c.fetched, st, watermark, counts)
            if got is None:  # unparseable page: dropped (CB-23b); refetch it next poll
                prev = self.conn.cache.get(c.url)
                if prev is not None:
                    self.conn.cache.put(replace(prev, etag=None))
                break
            n, oldest, page_newest, k, nw = got
            if page_newest and (newest is None or page_newest > newest):
                newest = page_newest
            if oldest and (oldest_seen is None or oldest < oldest_seen):
                oldest_seen = oldest
            kept += k
            new += nw
            if n < 100 or oldest is None:
                break
            if prev_newest is not None and oldest <= prev_newest:
                break  # reached events seen in the previous poll
            if page == MAX_PAGES and prev_newest is not None:
                overflow = True  # the window rolled past the previous poll's newest event
        now = self.conn.clock()
        self.db.conn.execute(
            """
            INSERT INTO repo_event_poll (repo_host_id, polled_at, status, pages, events_kept,
                events_new, overflow, overflow_after, poll_interval_s, newest_event_at,
                watermark_at, newest_event_id, oldest_event_at, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (t.repo_host_id, now, status, pages, kept, new, overflow,
             prev_newest if overflow else None, poll_interval, newest,
             *(counts.top or watermark or (None, None)), oldest_seen,
             self.run.id if self.run else None),
        )  # fmt: skip
        st.polled += 1
        st.pages += pages
        st.not_modified += int(status == 304)
        st.events_kept += kept
        st.events_new += new
        st.overflow += int(overflow)
        if counts.hours:
            self._store_counts(t.repo_host_id, counts)
        if status != 304:
            st.cases_updated += self.apply_bot_filter(t.repo_host_id, now)

    def _watermark(self, repo_host_id: int) -> tuple[datetime, int] | None:
        """`(created_at, id)` of the newest event counted so far for this repo."""
        row = self.db.conn.execute(
            "SELECT watermark_at, coalesce(newest_event_id, 0) FROM repo_event_poll"
            " WHERE repo_host_id = %s AND watermark_at IS NOT NULL"
            " ORDER BY watermark_at DESC, newest_event_id DESC NULLS LAST LIMIT 1",
            (repo_host_id,),
        ).fetchone()
        return (row[0], int(row[1])) if row else None

    def _ingest(
        self,
        t: Target,
        f: Fetched,
        st: PollStats,
        watermark: tuple[datetime, int] | None,
        counts: _Counts,
    ) -> tuple[int, datetime | None, datetime | None, int, int] | None:
        """Parse one page (coded, Watch/Fork only), drop the raw bytes, count new events.

        Returns (events on the page, oldest, newest, kept, new), or None if the page could not
        be parsed: its raw bytes are then dropped at once (CB-23b). Nothing per event or per
        account is stored: the counts accumulate in `counts` (in memory) until the poll ends.
        """
        store = self.conn.store
        try:
            n, oldest, newest = page_bounds(f.data)
            recs = list(self.conn.records(f.data, f.meta))
        except PARSE_ERRORS as e:
            st.parse_failed += 1
            if drop_unparseable(
                self.db,
                store,
                f.evidence.id,
                f.content_hash,
                self.dlog,
                source=self.conn.name,
                error=e,
                run=self.run,
            ):
                st.raw_dropped += 1
            return None
        if drop_after_parse(self.db, store, f.evidence.id, f.content_hash, self.dlog):
            st.raw_dropped += 1
        kept = new = 0
        for r in recs:
            created = _ts(r.get("created_at"))
            if created is None or int(r["repo_id"]) != t.repo_host_id:
                continue
            kept += 1
            eid = _event_id(r.get("event_id"))
            if eid is None:
                continue  # no usable id: cannot be de-duplicated across polls
            key = (created, eid)
            if watermark is not None and key <= watermark:
                continue  # counted by an earlier poll
            if eid in counts.seen_ids:
                continue  # pages overlap when new events arrive between page requests
            counts.seen_ids.add(eid)
            counts.top = max(counts.top, key) if counts.top else key
            new += 1
            counts.add(r, created)
        return n, oldest, newest, kept, new

    def _store_counts(self, repo_host_id: int, c: _Counts) -> None:
        """Add one poll's counts to the hourly and daily aggregates (project-level)."""
        with self.db.conn.cursor() as cur:
            for hour, v in sorted(c.hours.items()):
                cur.execute(
                    """
                    INSERT INTO repo_event_hourly_agg (repo_host_id, hour, stars,
                        stars_automated, forks, forks_automated, bot_rule_version, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (repo_host_id, hour) DO UPDATE SET
                        stars = repo_event_hourly_agg.stars + EXCLUDED.stars,
                        stars_automated = repo_event_hourly_agg.stars_automated
                                          + EXCLUDED.stars_automated,
                        forks = repo_event_hourly_agg.forks + EXCLUDED.forks,
                        forks_automated = repo_event_hourly_agg.forks_automated
                                          + EXCLUDED.forks_automated,
                        bot_rule_version = EXCLUDED.bot_rule_version,
                        updated_at = now()
                    """,
                    (repo_host_id, hour, *v.as_tuple(), BOT_FILTER_VERSION),
                )
            days: dict[date, _Hour] = {}
            for hour, v in c.hours.items():
                days.setdefault(hour.date(), _Hour()).merge(v)
            for day, v in sorted(days.items()):
                cur.execute(
                    """
                    INSERT INTO repo_event_daily_agg (repo_host_id, day, stars_seen, stars_bot,
                        forks_seen, forks_bot, bot_rule_version, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (repo_host_id, day) DO UPDATE SET
                        stars_seen = repo_event_daily_agg.stars_seen + EXCLUDED.stars_seen,
                        stars_bot = repo_event_daily_agg.stars_bot + EXCLUDED.stars_bot,
                        forks_seen = repo_event_daily_agg.forks_seen + EXCLUDED.forks_seen,
                        forks_bot = repo_event_daily_agg.forks_bot + EXCLUDED.forks_bot,
                        bot_rule_version = EXCLUDED.bot_rule_version,
                        updated_at = now()
                    """,
                    (repo_host_id, day, *v.as_tuple(), BOT_FILTER_VERSION),
                )

    # --- bot filter confirmation -------------------------------------------------------------
    def window_aggregate(self, repo_host_id: int, start: datetime, end: datetime) -> dict[str, Any]:
        """Aggregate counts for one repo and window: the hours overlapping `[start, end)`
        (hour precision; the hourly counts are all that is stored)."""
        row = self.db.conn.execute(
            """
            SELECT coalesce(sum(stars), 0), coalesce(sum(stars_automated), 0),
                   (SELECT min(oldest_event_at) FROM repo_event_poll WHERE repo_host_id = %(r)s)
            FROM repo_event_hourly_agg
            WHERE repo_host_id = %(r)s AND hour >= date_trunc('hour', %(s)s::timestamptz)
              AND hour < %(e)s
            """,
            {"r": repo_host_id, "s": start, "e": end},
        ).fetchone()
        # an overflow matters for this window if its unseen gap began before the window ended
        ov = self.db.conn.execute(
            "SELECT count(*), bool_or(overflow AND overflow_after < %s) FROM repo_event_poll"
            " WHERE repo_host_id = %s AND polled_at >= %s",
            (end, repo_host_id, start),
        ).fetchone()
        assert row is not None and ov is not None
        return {
            "stars_seen": int(row[0]),
            "stars_bot": int(row[1]),
            "events_from": row[2],
            "polls": int(ov[0]),
            "overflow": bool(ov[1]) if ov[1] is not None else False,
        }

    def apply_bot_filter(self, repo_host_id: int, now: datetime) -> int:
        """Fill the `bot_filter` block of this repo's open detection-v1 cases. Returns count."""
        cases = self.db.conn.execute(
            """
            SELECT c.id, c.detection FROM cases c JOIN repos r ON r.id = c.repo_id
            WHERE r.host = 'github' AND r.host_id = %s AND c.status = 'live'
              AND c.detection ->> 'rule_version' = 'detection-v1'
            """,
            (repo_host_id,),
        ).fetchall()
        n = 0
        for cid, det in cases:
            cov = det["coverage"]
            start = _ts(cov["window_start"])
            end = _ts(cov["window_end"])
            if start is None or end is None:
                continue
            agg = self.window_aggregate(repo_host_id, start, end)
            reference = cov.get("reference_stars")
            ref = int(reference) if reference is not None else int(det["stars_48h"])
            bf = BotFilterStatus(
                status="applied",
                basis="repo_events",
                confirmed=(ref - agg["stars_bot"]) >= int(det["threshold_min_stars"]),
                version=BOT_FILTER_VERSION,
                layers=["login_rules"],
                updated_at=now,
                stars_seen=agg["stars_seen"],
                stars_bot=agg["stars_bot"],
                stars_lockstep=None,
                stars_filtered=agg["stars_seen"],
                lockstep_hours=None,
                events_from=agg["events_from"],
                window_overflow=agg["overflow"],
                coverage_ratio=round(agg["stars_seen"] / ref, 4) if ref > 0 else None,
            )
            self.db.conn.execute(
                "UPDATE cases SET detection = jsonb_set(detection, '{bot_filter}', %s)"
                " WHERE id = %s",
                (Jsonb(bf.model_dump(mode="json")), cid),
            )
            n += 1
        return n
