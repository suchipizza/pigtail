"""Per-repo events polling for tracked cases and the bot-filter confirmation step (M1-T24;
ADR-032.2; TM-33; ADR-022/ADR-036; CB-22, CB-23).

**What is polled.** Only repos with an open detection case opened in the last `case_days`
(default 14), plus, if `include_prethreshold`, watch-list repos whose public star count grew by
≥ `prethreshold_stars_24h` (30) in 24 h (TM-33: open cases, tracked repos, or above the
pre-threshold; nothing else).

**Cadence.** A repo is due when `max(interval, X-Poll-Interval)` has passed since its last poll:
15 min for cases, 60 min for pre-threshold repos (replan §6.1), never faster than GitHub's
`X-Poll-Interval` (60 s measured). Page 1 is requested with `If-None-Match`; a `304` costs no
rate limit and ends the poll. Pages 2–3 (the 300-event window) are read while the previous page
is full and all its events are newer than the newest event of the previous poll (on a repo's
first poll: while pages are full, to cover as much of the case window as GitHub keeps). If page 3
is full and still newer than the previous poll, events may have rolled out unseen: the poll is
marked `overflow` (replan §1.3, §8 M5).

**Minimisation.** The connector keeps only `WatchEvent` and `ForkEvent` (CB-23), drops bot logins
before hashing, and pseudonymizes actors (namespace `github`). Each events page's raw bytes are
dropped right after parsing (`drop_after_parse`: hash and URL kept, evidence `raw_dropped`,
tombstone in `deletion_log`). Rows go to `repo_event_actor` (person-level, pseudonyms only,
registered in `PERSON_TABLES` with a 30-day cap; `pigtail retention purge` deletes older rows)
and to project-level daily aggregates `repo_event_daily_agg`, which outlive them.

**Never a stargazer list.** `repo_event_actor` is read only through the aggregate queries in
this module (counts per hour/day, `min`/`bool_or`), and only to fill a case's `bot_filter` block
and the daily aggregates. No function, CLI command or API path returns the pseudonyms of the
people who starred a repo (tested in `tests/unit/test_github_privacy_m1t24.py`).

**Bot filter (existing heuristics, `pigtail.capture.botfilter`).** Layer 1, login rules, is
applied to every event before pseudonymization (`stars_bot`). Layer 2, the lockstep burst rule,
needs to know whether a stargazer did *anything else* on GitHub in the filter window; per-repo
events for one repo cannot show that (and CB-23 keeps only stars and forks), so treating every
stargazer of the repo as "star-only" would flag organic bursts. Layer 2 is therefore reported as
not applied (`stars_lockstep = null`, `layers = ["login_rules"]`) rather than guessed.
For each open detection-v1 case of the repo the `bot_filter` block becomes `applied` with:
distinct non-bot stargazers seen in the window (`stars_seen`), bot stars (`stars_bot`),
`stars_filtered = stars_seen`, the earliest event seen (`events_from`), whether any poll in the
window overflowed, `coverage_ratio = stars_seen / reference_stars` (star-history), and
`confirmed = reference_stars − stars_bot ≥ threshold_min_stars` (R1.1 after bot filtering).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from pigtail.capture.botfilter import BOT_FILTER_VERSION
from pigtail.capture.db import CaptureDB
from pigtail.capture.models import BotFilterStatus
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import Fetched, FetchError, NotFound
from pigtail.connectors.github import GitHubRepoEventsConnector, events_url, full_url
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.privacy.deletion import DeletionLog, drop_after_parse

log = logging.getLogger("pigtail.capture.repo_events")

MAX_PAGES = 3  # 300-event window at per_page=100


@dataclass(frozen=True)
class EventsConfig:
    case_interval_min: int = 15
    prethreshold_interval_min: int = 60
    case_days: int = 14
    include_prethreshold: bool = False
    prethreshold_stars_24h: int = 30

    def __post_init__(self) -> None:
        if self.case_interval_min < 15 or self.prethreshold_interval_min < 15:
            raise ValueError("per-repo events are polled every 15-60 min (ADR-032.2, TM-33)")


@dataclass(frozen=True)
class Target:
    repo_host_id: int
    full_name: str
    interval: timedelta
    kind: str  # "case" | "prethreshold"


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
            SELECT DISTINCT r.host_id, COALESCE(w.full_name, r.full_name)
            FROM cases c JOIN repos r ON r.id = c.repo_id AND r.host = 'github'
            LEFT JOIN watchlist w ON w.repo_host_id = r.host_id
            WHERE c.status = 'live' AND c.trigger = 'velocity'
              AND c.opened_at >= %s - make_interval(days => %s)
            ORDER BY 1
            """,
            (now, cfg.case_days),
        ).fetchall()
        out = [
            Target(int(h), str(n), timedelta(minutes=cfg.case_interval_min), "case")
            for h, n in rows
        ]
        if cfg.include_prethreshold:
            seen = {t.repo_host_id for t in out}
            pre = self.db.conn.execute(
                """
                SELECT w.repo_host_id, w.full_name FROM watchlist w
                WHERE w.active AND w.repo_host_id IS NOT NULL AND (
                    SELECT max(s.stars) - min(s.stars) FROM repo_count_snapshot s
                    WHERE s.repo_host_id = w.repo_host_id AND s.observed_at > %s - interval '24 h'
                ) >= %s
                ORDER BY 1
                """,
                (now, cfg.prethreshold_stars_24h),
            ).fetchall()
            out += [
                Target(int(h), str(n), timedelta(minutes=cfg.prethreshold_interval_min), "pre")
                for h, n in pre
                if int(h) not in seen
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
        pages = kept = new = 0
        overflow = False
        newest: datetime | None = None
        status = 200
        poll_interval: int | None = None
        days: set[date] = set()
        for page in range(1, MAX_PAGES + 1):
            c = self.conn.repo_events(t.full_name, page=page)
            pages += 1
            poll_interval = poll_interval or c.poll_interval_s
            if c.fetched is None:  # 304: nothing new on this page
                status = 304 if page == 1 else status
                break
            n, oldest, page_newest = page_bounds(c.fetched.data)
            if page_newest and (newest is None or page_newest > newest):
                newest = page_newest
            k, nw, ds = self._ingest(t, c.fetched)
            kept += k
            new += nw
            days |= ds
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
                events_new, overflow, overflow_after, poll_interval_s, newest_event_at, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (t.repo_host_id, now, status, pages, kept, new, overflow,
             prev_newest if overflow else None, poll_interval, newest,
             self.run.id if self.run else None),
        )  # fmt: skip
        st.polled += 1
        st.pages += pages
        st.not_modified += int(status == 304)
        st.events_kept += kept
        st.events_new += new
        st.overflow += int(overflow)
        if days:
            self._update_daily(t.repo_host_id, days)
        if status != 304:
            st.cases_updated += self.apply_bot_filter(t.repo_host_id, now)

    def _ingest(self, t: Target, f: Fetched) -> tuple[int, int, set[date]]:
        """Parse one page (pseudonymized, Watch/Fork only), store rows, drop the raw bytes."""
        try:
            recs = list(self.conn.records(f.data, f.meta))
        finally:
            dropped = drop_after_parse(
                self.db, self.conn.store, f.evidence.id, f.content_hash, self.dlog
            )
            if dropped and self.run is not None:
                self.run.incr("repo_events.raw_dropped")
        at = f.meta.fetched_at
        rows = []
        days: set[date] = set()
        for r in recs:
            created = _ts(r.get("created_at"))
            if created is None or int(r["repo_id"]) != t.repo_host_id:
                continue
            days.add(created.astimezone(UTC).date())
            rows.append((t.repo_host_id, r["event_id"], r["type"], r.get("actor"),
                         bool(r.get("is_bot")), created, at))  # fmt: skip
        new = 0
        with self.db.conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    "INSERT INTO repo_event_actor (repo_host_id, event_id, event_type,"
                    " actor_pseudonym, is_bot, created_at, observed_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    row,
                )
                new += cur.rowcount
        return len(rows), new, days

    def _update_daily(self, repo_host_id: int, days: set[date]) -> None:
        self.db.conn.execute(
            """
            INSERT INTO repo_event_daily_agg (repo_host_id, day, stars_seen, stars_bot,
                forks_seen, forks_bot, updated_at)
            SELECT repo_host_id, (created_at AT TIME ZONE 'UTC')::date,
                count(DISTINCT actor_pseudonym) FILTER (WHERE event_type = 'WatchEvent'
                                                        AND NOT is_bot),
                count(*) FILTER (WHERE event_type = 'WatchEvent' AND is_bot),
                count(DISTINCT actor_pseudonym) FILTER (WHERE event_type = 'ForkEvent'
                                                        AND NOT is_bot),
                count(*) FILTER (WHERE event_type = 'ForkEvent' AND is_bot),
                now()
            FROM repo_event_actor
            WHERE repo_host_id = %s AND (created_at AT TIME ZONE 'UTC')::date = ANY(%s)
            GROUP BY 1, 2
            ON CONFLICT (repo_host_id, day) DO UPDATE SET
                stars_seen = GREATEST(repo_event_daily_agg.stars_seen, EXCLUDED.stars_seen),
                stars_bot = GREATEST(repo_event_daily_agg.stars_bot, EXCLUDED.stars_bot),
                forks_seen = GREATEST(repo_event_daily_agg.forks_seen, EXCLUDED.forks_seen),
                forks_bot = GREATEST(repo_event_daily_agg.forks_bot, EXCLUDED.forks_bot),
                updated_at = now()
            """,
            (repo_host_id, sorted(days)),
        )

    # --- bot filter confirmation -------------------------------------------------------------
    def window_aggregate(self, repo_host_id: int, start: datetime, end: datetime) -> dict[str, Any]:
        """Aggregate counts only (never pseudonyms) for one repo and window."""
        row = self.db.conn.execute(
            """
            SELECT count(DISTINCT actor_pseudonym) FILTER (WHERE NOT is_bot),
                   count(*) FILTER (WHERE is_bot),
                   (SELECT min(created_at) FROM repo_event_actor WHERE repo_host_id = %(r)s)
            FROM repo_event_actor
            WHERE repo_host_id = %(r)s AND event_type = 'WatchEvent'
              AND created_at >= %(s)s AND created_at < %(e)s
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
