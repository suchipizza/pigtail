"""HN front-page rank poller (M1-T14; R1.2; PRD §8.1 front-page minutes; codebook §6.2).

Each poll:

1. snapshots `/v0/topstories` (up to 500 ids, `project_level` evidence) and writes one
   `hn_rank_poll` row plus one `hn_rank_observation` row per id (item id, rank, observed_at).
   Ranks 1-30 are the front page (`FRONT_PAGE_RANKS`, a config assumption, codebook §6.2);
   all ids are kept;
2. fetches the items at ranks 1..`items` (default 30) through `HNRanksConnector`, stores their
   project-level metadata in `hn_story` (url, title, score, descendants, time; no `by`) and the
   score and comment count on that poll's observation row, then drops each item's raw bytes
   (they contain `by`; `pigtail.privacy.deletion.drop_after_parse`);
3. maps story URLs to GitHub repos (`github.com/owner/repo`, normalized, lowercase). When the repo
   is in `repos`, the story row and the item evidence get its `repo_id` and newest open `case_id`,
   so case evidence can attach. Repos on the refusal list (CB-13) are not linked and their story
   metadata is not stored (the rank row keeps only the id). This holds for repos opted out by
   id and, since M1-T23, by name (repos that are not in `repos`);
4. registers each stored story for deletion sync (`track_items`, platform `hn`, no author):
   when HN reports it deleted, dead or gone, `HNDeletionSource.delete_rows` clears its title and
   url (M1-T23; rank history and the project-level repo link stay). A story whose title was
   cleared is never refilled by a later poll.

An item whose JSON fails to parse is counted (`hn_ranks.parse_failed`, no content) and its raw
bytes are dropped like every other item (CB-23b).

`run_loop()` repeats polls every `interval` seconds (default 5 minutes, never under 60 s: TM-04
allows at most one `topstories` poll per minute). A failed poll is logged and the loop goes on.
Polls are idempotent: the observation time is the snapshot's fetch time.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pigtail.capture.db import CaptureDB
from pigtail.capture.repos import RepoLink, link_repo
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import FetchError
from pigtail.connectors.hn_ranks import HNRanksConnector
from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog, drop_after_parse
from pigtail.privacy.deletion_sync import HN_POLICY, track_items
from pigtail.privacy.suppression import Suppressions

FRONT_PAGE_RANKS = 30
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60  # TM-04: at most one topstories poll per minute

log = logging.getLogger("pigtail.capture.hn_ranks")


@dataclass
class PollResult:
    observed_at: datetime
    evidence_id: str
    n_ids: int
    items_fetched: int = 0
    items_failed: int = 0
    parse_failed: int = 0
    raw_dropped: int = 0
    stories_linked: list[str] = field(default_factory=list)  # repo full names mapped to repos
    stories_suppressed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "evidence_id": self.evidence_id,
            "n_ids": self.n_ids,
            "front_page": min(self.n_ids, FRONT_PAGE_RANKS),
            "items_fetched": self.items_fetched,
            "items_failed": self.items_failed,
            "parse_failed": self.parse_failed,
            "raw_dropped": self.raw_dropped,
            "stories_linked": self.stories_linked,
            "stories_suppressed": self.stories_suppressed,
        }


class RankPoller:
    def __init__(
        self,
        connector: HNRanksConnector,
        db: CaptureDB,
        *,
        run: RunRecorder | None = None,
        items: int = FRONT_PAGE_RANKS,
        suppression: Suppressions | None = None,
    ) -> None:
        if items < 0:
            raise ValueError("items must be >= 0")
        self.conn = connector
        self.db = db
        self.run = run
        self.items = items
        self.suppression = suppression or connector.suppression
        if connector.evidence_sink is None:
            connector.evidence_sink = db.upsert_evidence

    def poll_once(self) -> PollResult:
        f, ids = self.conn.fetch_topstories()
        at = f.meta.fetched_at
        run_id = self.run.id if self.run else None
        self.db.conn.execute(
            "INSERT INTO hn_rank_poll (observed_at, n_items, content_hash, evidence_id, run_id)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (observed_at) DO NOTHING",
            (at, len(ids), f.content_hash, f.evidence.id, run_id),
        )
        with self.db.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO hn_rank_observation (item_id, observed_at, rank) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [(iid, at, rank) for rank, iid in enumerate(ids, start=1)],
            )
        res = PollResult(observed_at=at, evidence_id=f.evidence.id, n_ids=len(ids))
        dlog = DeletionLog(self.db, "retention", run_id=run_id)
        for rank, iid in enumerate(ids[: self.items], start=1):
            self._story(iid, rank, at, res, dlog)
        self.db.conn.execute(
            "UPDATE hn_rank_poll SET items_fetched = %s WHERE observed_at = %s",
            (res.items_fetched, at),
        )
        if self.run is not None:
            self.run.incr("polls")
            self.run.incr("rank_observations", len(ids))
            self.run.incr("items_fetched", res.items_fetched)
            self.run.incr("items_failed", res.items_failed)
            self.run.incr("item_raw_dropped", res.raw_dropped)
        return res

    def _story(self, iid: int, rank: int, at: datetime, res: PollResult, dlog: DeletionLog) -> None:
        try:
            f = self.conn.fetch_story(iid)
        except FetchError as e:
            res.items_failed += 1
            log.warning("hn item %s: %s", iid, e.status)
            return
        try:
            res.items_fetched += 1
            try:
                rec = self.conn.story_record(f)
            except PARSE_ERRORS as e:  # CB-23b: counted without content; dropped below
                res.items_failed += 1
                res.parse_failed += 1
                if self.run is not None:
                    self.run.incr("hn_ranks.parse_failed")
                    self.run.incr(f"hn_ranks.parse_failed.{type(e).__name__}")
                return
            if rec is None:
                return
            self.db.conn.execute(
                "UPDATE hn_rank_observation SET score = %s, descendants = %s"
                " WHERE item_id = %s AND observed_at = %s",
                (_int(rec.get("score")), _int(rec.get("descendants")), iid, at),
            )
            link: RepoLink | None = None
            if rec.get("repo_full_name"):
                if self.suppression.name_suppressed(rec["repo_full_name"]):
                    res.stories_suppressed += 1  # M1-T23: opted out by name; keep the id only
                    return
                link = link_repo(self.db, rec["repo_full_name"])
                if link.repo_id and link.repo_id in self.suppression.repos:
                    res.stories_suppressed += 1  # CB-13: opted-out project, keep the id only
                    return
                if link.repo_id:
                    res.stories_linked.append(link.full_name)
                    self.db.conn.execute(
                        "UPDATE evidence SET repo_id = %s, case_id = %s WHERE id = %s",
                        (link.repo_id, link.case_id, f.evidence.id),
                    )
            self._upsert_story(rec, rank, at, f.evidence.id, link)
            # M1-T23: re-checked by deletion sync, which clears title/url once it is gone
            track_items(
                self.db,
                HN_POLICY,
                f.evidence.id,
                [str(iid)],
                seen_at=at,
                open_case=bool(link and link.case_id),
            )
        finally:
            if drop_after_parse(self.db, self.conn.store, f.evidence.id, f.content_hash, dlog):
                res.raw_dropped += 1

    def _upsert_story(
        self, rec: dict[str, Any], rank: int, at: datetime, evidence_id: str, link: RepoLink | None
    ) -> None:
        created = rec.get("time")
        self.db.conn.execute(
            """
            INSERT INTO hn_story (item_id, type, url, title, created_at, score, descendants,
                                  deleted, dead, repo_full_name, repo_id, best_rank,
                                  first_seen_at, last_seen_at, evidence_id)
            VALUES (%(id)s, %(type)s, %(url)s, %(title)s,
                    CASE WHEN %(time)s::bigint IS NULL THEN NULL
                         ELSE to_timestamp(%(time)s::bigint) END,
                    %(score)s, %(desc)s, %(deleted)s, %(dead)s, %(repo)s, %(repo_id)s, %(rank)s,
                    %(at)s, %(at)s, %(ev)s)
            ON CONFLICT (item_id) DO UPDATE SET
                type = EXCLUDED.type,
                url = CASE WHEN hn_story.content_cleared_at IS NULL THEN EXCLUDED.url END,
                title = CASE WHEN hn_story.content_cleared_at IS NULL THEN EXCLUDED.title END,
                created_at = EXCLUDED.created_at, score = EXCLUDED.score,
                descendants = EXCLUDED.descendants, deleted = EXCLUDED.deleted,
                dead = EXCLUDED.dead, repo_full_name = EXCLUDED.repo_full_name,
                repo_id = COALESCE(EXCLUDED.repo_id, hn_story.repo_id),
                best_rank = LEAST(hn_story.best_rank, EXCLUDED.best_rank),
                first_seen_at = LEAST(hn_story.first_seen_at, EXCLUDED.first_seen_at),
                last_seen_at = GREATEST(hn_story.last_seen_at, EXCLUDED.last_seen_at),
                evidence_id = EXCLUDED.evidence_id
            """,
            {
                "id": rec["item_id"],
                "type": rec.get("type"),
                "url": rec.get("url") if isinstance(rec.get("url"), str) else None,
                "title": rec.get("title") if isinstance(rec.get("title"), str) else None,
                "time": _int(created),
                "score": _int(rec.get("score")),
                "desc": _int(rec.get("descendants")),
                "deleted": bool(rec.get("deleted")),
                "dead": bool(rec.get("dead")),
                "repo": rec.get("repo_full_name"),
                "repo_id": link.repo_id if link else None,
                "rank": rank,
                "at": at,
                "ev": evidence_id,
            },
        )


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def check_interval(seconds: float) -> float:
    if seconds < MIN_INTERVAL_SECONDS:
        raise ValueError(
            f"interval must be >= {MIN_INTERVAL_SECONDS} s (TM-04: at most one topstories poll "
            "per minute)"
        )
    return seconds


def run_loop(
    poll: Callable[[], Any],
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    max_polls: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_error: Callable[[BaseException], None] | None = None,
) -> tuple[int, int]:
    """Call `poll` every `interval` seconds (start to start); return (polls, failures).

    Exceptions from a poll are passed to `on_error` (or logged) and the loop continues; only
    KeyboardInterrupt / SystemExit stop it.
    """
    check_interval(interval)
    polls = failures = 0
    while max_polls is None or polls < max_polls:
        started = monotonic()
        try:
            poll()
        except Exception as e:
            failures += 1
            if on_error is not None:
                on_error(e)
            else:
                log.warning("hn rank poll failed: %s", type(e).__name__)
        polls += 1
        if max_polls is not None and polls >= max_polls:
            break
        sleep(max(0.0, interval - (monotonic() - started)))
    return polls, failures
