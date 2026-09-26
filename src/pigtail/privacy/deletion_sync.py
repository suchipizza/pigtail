"""Deletion sync (PRD R1.5; DPIA CB-02; retention-policy.md §4).

Person-level snapshots from sources with deletion duties (or, for HN, a courtesy duty) are
tracked item by item: capture jobs call `track_items()` for every upstream item whose content sits
in a snapshot (`upstream_items`, `evidence_upstream_items`; migration 0005). `sync()` then:

1. asks the platform's `DeletionSource` for `UpstreamSignal`s about items that are due for a
   re-check (`next_check_at <= now`);
2. for every item signalled `deleted`, `dead` or `missing` (per the source's `SyncPolicy.act_on`),
   acts at once: the raw bytes of every snapshot holding it are dropped and all evidence with
   those hashes moves to `deletion_state = 'deleted_upstream'` (hash, URL, fetch time, terms basis
   and coded facts, which hold no person identifiers, stay; R1.5), the item's coded rows are
   deleted (`DeletionSource.delete_rows`), LLM cache rows derived from the evidence are purged
   (CB-05), and tombstones go to the append-only `deletion_log` (reason `deleted_upstream`);
3. re-applies step 2 to any evidence still linked to an item already known to be gone (a later
   capture of a stale index, or a backup restore: CB-17);
4. reschedules items that are still present: `recheck_open` when linked to an open case,
   `recheck_closed` otherwise;
5. reports SLA state: items whose re-check is later than `act_within` past due, and items
   detected as gone but not yet acted on.

Two kinds of source plug in through the same `DeletionSource` interface:

- **poll** sources (HN): `signals(due)` re-fetches each due item (`HNDeletionSource` uses
  `Connector.check()`, which stores nothing) and returns one signal per item;
- **push** sources (Bluesky, to be added under CB-02 with a ≤ 48 h window): `signals(due)` ignores
  `due` and drains tombstones received since its saved cursor (Jetstream delete events, account
  deactivate/delete events). `BLUESKY_POLICY` holds its SLA. Items are tracked **by id only**
  (Directive §8.1, migration 0017: no author is stored), so an account-level signal
  (`account=...`, the raw account id, in memory) cannot be resolved from the database: it is
  counted (`account_signals`) and needs the push source to resolve it to item ids itself, or an
  in-memory scan of retained snapshots like an erasure (follow-up with the Bluesky source).

`dry_run=True` computes the same report and changes nothing.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pigtail.privacy.deletion import DeletionLog, mark_deleted_upstream

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import SnapshotStore
    from pigtail.connectors.hn import HNFirebaseConnector
    from pigtail.llm.store import LLMStore


class UpstreamState(StrEnum):
    PRESENT = "present"
    DELETED = "deleted"
    DEAD = "dead"
    MISSING = "missing"
    UNKNOWN = "unknown"  # the check failed; retried on the next run


GONE = frozenset({UpstreamState.DELETED, UpstreamState.DEAD, UpstreamState.MISSING})


@dataclass(frozen=True)
class UpstreamSignal:
    """What a source says about one item, or about every item of one account."""

    state: UpstreamState
    item_id: str | None = None
    account: str | None = None  # push sources only (account deleted/deactivated); never stored

    def __post_init__(self) -> None:
        if (self.item_id is None) == (self.account is None):
            raise ValueError("a signal names exactly one of item_id / account")


@dataclass(frozen=True)
class SyncPolicy:
    """Per-source schedule and SLA (retention-policy.md §4)."""

    platform: str
    mode: Literal["poll", "push"]
    recheck_open: timedelta  # items linked to an open case
    recheck_closed: timedelta  # everything else
    act_within: timedelta  # from detection (or due re-check) to raw bytes dropped
    act_on: frozenset[UpstreamState] = GONE


HN_POLICY = SyncPolicy(
    platform="hn",
    mode="poll",
    recheck_open=timedelta(days=1),
    recheck_closed=timedelta(days=30),
    act_within=timedelta(days=7),
)
# Planned (CB-02): Bluesky stays off until its push source exists and is tested.
BLUESKY_POLICY = SyncPolicy(
    platform="bluesky",
    mode="push",
    recheck_open=timedelta(days=1),
    recheck_closed=timedelta(days=30),
    act_within=timedelta(hours=48),
)


@dataclass(frozen=True)
class TrackedItem:
    platform: str
    item_id: str
    next_check_at: datetime


class DeletionSource(ABC):
    """A platform's way of learning about upstream deletions."""

    policy: ClassVar[SyncPolicy]

    @abstractmethod
    def signals(self, due: Sequence[TrackedItem]) -> Iterable[UpstreamSignal]:
        """Poll sources: one signal per due item. Push sources: drained tombstones."""

    def delete_rows(self, db: CaptureDB, item_ids: Sequence[str], log: DeletionLog) -> int:
        """Delete the parsed person-level rows of gone items (none by default)."""
        return 0


def _hn_state(status: int | None, data: bytes) -> UpstreamState:
    if status is None or status >= 500 or status == 429:
        return UpstreamState.UNKNOWN
    if status == 404:
        return UpstreamState.MISSING
    if status != 200:
        return UpstreamState.UNKNOWN
    try:
        item = json.loads(data)
    except ValueError:
        return UpstreamState.UNKNOWN
    if item is None:
        return UpstreamState.MISSING
    if not isinstance(item, dict):
        return UpstreamState.UNKNOWN
    if item.get("deleted"):
        return UpstreamState.DELETED
    if item.get("dead"):
        return UpstreamState.DEAD
    return UpstreamState.PRESENT


class HNDeletionSource(DeletionSource):
    """HN (TM-03/TM-04): re-fetch each item from Firebase; `deleted`, `dead` or `null` = gone.

    Uses `HNFirebaseConnector.check()`: nothing is snapshotted, and it works while HN collection
    is disabled. HN has no stated deletion duty; this is the courtesy sync of retention-policy §4.

    Gone items lose their `hn_mention` rows, and stories seen by the rank poller (`hn_story`) lose
    their title and url (M1-T23; `content_cleared_at` is set, logged as `fields_cleared`). The
    rank history and the project-level repo link stay: they record the front page, not the post.
    """

    policy: ClassVar[SyncPolicy] = HN_POLICY

    def __init__(self, connector: HNFirebaseConnector) -> None:
        self.conn = connector

    def signals(self, due: Sequence[TrackedItem]) -> Iterable[UpstreamSignal]:
        from pigtail.connectors.hn import item_url

        for it in due:
            res = self.conn.check(item_url(it.item_id))
            yield UpstreamSignal(_hn_state(res.status, res.data), item_id=it.item_id)

    def delete_rows(self, db: CaptureDB, item_ids: Sequence[str], log: DeletionLog) -> int:
        ids = [int(i) for i in item_ids]
        if log.dry_run:
            row = db.conn.execute(
                "SELECT count(*) FROM hn_mention WHERE item_id = ANY(%s)", (ids,)
            ).fetchone()
            n = int(row[0]) if row else 0
        else:
            n = db.conn.execute("DELETE FROM hn_mention WHERE item_id = ANY(%s)", (ids,)).rowcount
        if n:
            log.write("rows_deleted", "hn_mention", rows=n)
        return n + clear_hn_story_content(db, ids, log)


def clear_hn_story_content(db: CaptureDB, item_ids: Sequence[int], log: DeletionLog) -> int:
    """M1-T23: clear title and url of `hn_story` rows deleted upstream. Returns rows changed."""
    cond = "item_id = ANY(%s) AND content_cleared_at IS NULL"
    ids = list(item_ids)
    if log.dry_run:
        row = db.conn.execute(f"SELECT count(*) FROM hn_story WHERE {cond}", (ids,)).fetchone()
        n = int(row[0]) if row else 0
    else:
        n = db.conn.execute(
            "UPDATE hn_story SET title = NULL, url = NULL, content_cleared_at = now()"
            f" WHERE {cond}",
            (ids,),
        ).rowcount
    if n:
        log.write("fields_cleared", "hn_story", rows=n)
    return n


# --- tracking (called by capture jobs) ----------------------------------------------------------
def track_items(
    db: CaptureDB,
    policy: SyncPolicy,
    evidence_id: str,
    items: Iterable[str],
    *,
    seen_at: datetime,
    open_case: bool,
) -> int:
    """Register the upstream item ids contained in the snapshot `evidence_id` (no author)."""
    rows = sorted({str(i) for i in items})
    if not rows:
        return 0
    nxt = seen_at + (policy.recheck_open if open_case else policy.recheck_closed)
    with db.conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO upstream_items (platform, item_id, first_seen_at, last_seen_at,
                                        next_check_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (platform, item_id) DO UPDATE SET
                last_seen_at = GREATEST(upstream_items.last_seen_at, EXCLUDED.last_seen_at),
                next_check_at = LEAST(upstream_items.next_check_at, EXCLUDED.next_check_at)
            """,
            [(policy.platform, i, seen_at, seen_at, nxt) for i in rows],
        )
        cur.executemany(
            "INSERT INTO evidence_upstream_items (evidence_id, platform, item_id)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(evidence_id, policy.platform, i) for i in rows],
        )
    return len(rows)


# --- the sync job -------------------------------------------------------------------------------
@dataclass
class SyncReport:
    platform: str
    dry_run: bool
    now: str
    checked: int = 0
    still_present: int = 0
    unknown: int = 0
    gone: dict[str, int] = field(default_factory=dict)  # state -> items
    account_signals: int = 0
    items_acted: int = 0
    reapplied_items: int = 0
    snapshots_dropped: int = 0
    evidence_marked: int = 0
    rows_deleted: int = 0
    llm_cache_rows_deleted: int = 0
    overdue_before_run: int = 0  # present items whose re-check was > act_within late
    detected_not_acted: int = 0  # after this run (non-zero only in dry-run or on failure)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _due(db: CaptureDB, platform: str, now: datetime, limit: int | None) -> list[TrackedItem]:
    q = (
        "SELECT platform, item_id, next_check_at FROM upstream_items"
        " WHERE platform = %s AND state = 'present' AND next_check_at <= %s"
        " ORDER BY next_check_at, item_id"
    )
    params: tuple[Any, ...] = (platform, now)
    if limit is not None:
        q += " LIMIT %s"
        params = (*params, limit)
    return [TrackedItem(*r) for r in db.conn.execute(q, params).fetchall()]


def _act(
    db: CaptureDB,
    store: SnapshotStore,
    src: DeletionSource,
    item_ids: Sequence[str],
    log: DeletionLog,
    rep: SyncReport,
    llm_store: LLMStore | None,
) -> None:
    """Drop every snapshot holding these items; mark evidence; delete rows and cache."""
    if not item_ids:
        return
    platform = src.policy.platform
    hashes = [
        r[0]
        for r in db.conn.execute(
            "SELECT DISTINCT e.content_hash FROM evidence_upstream_items l"
            " JOIN evidence e ON e.id = l.evidence_id"
            " WHERE l.platform = %s AND l.item_id = ANY(%s) AND e.deletion_state <>"
            " 'deleted_upstream' ORDER BY 1",
            (platform, list(item_ids)),
        )
    ]
    evidence_ids: list[str] = []
    for h in hashes:
        evidence_ids += mark_deleted_upstream(db, store, h, log)
    rep.snapshots_dropped += len(hashes)
    rep.evidence_marked += len(evidence_ids)
    rep.rows_deleted += src.delete_rows(db, item_ids, log)
    if llm_store is not None and evidence_ids:
        n = llm_store.purge_for_evidence(evidence_ids, dry_run=log.dry_run)
        rep.llm_cache_rows_deleted += n
        if n:
            log.write("cache_purged", "llm_cache", rows=n)


def sync(
    db: CaptureDB,
    store: SnapshotStore,
    src: DeletionSource,
    *,
    now: datetime,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> SyncReport:
    pol = src.policy
    log = DeletionLog(db, "deleted_upstream", run_id=run.id if run else None, dry_run=dry_run)
    rep = SyncReport(platform=pol.platform, dry_run=dry_run, now=now.isoformat())
    row = db.conn.execute(
        "SELECT count(*) FROM upstream_items WHERE platform = %s AND state = 'present'"
        " AND next_check_at < %s",
        (pol.platform, now - pol.act_within),
    ).fetchone()
    rep.overdue_before_run = int(row[0]) if row else 0

    due = _due(db, pol.platform, now, limit)
    gone: dict[str, UpstreamState] = {}
    present: list[str] = []
    for sig in src.signals(due):
        targets: list[str]
        if sig.account is not None:
            # no author is stored (Directive §8.1): the source must resolve accounts to items
            rep.account_signals += 1
            continue
        else:
            assert sig.item_id is not None
            targets = [sig.item_id]
            rep.checked += 1
        if sig.state in pol.act_on:
            for t in targets:
                gone[t] = sig.state
        elif sig.state is UpstreamState.PRESENT:
            present += targets
        else:
            rep.unknown += len(targets)
    for st in gone.values():
        rep.gone[st.value] = rep.gone.get(st.value, 0) + 1
    rep.still_present = len(present)

    # 2. act on newly gone items
    _act(db, store, src, sorted(gone), log, rep, llm_store)
    rep.items_acted = len(gone)
    if not dry_run:
        with db.conn.cursor() as cur:
            cur.executemany(
                "UPDATE upstream_items SET state = %s, last_checked_at = %s,"
                " detected_at = COALESCE(detected_at, %s), acted_at = %s"
                " WHERE platform = %s AND item_id = %s",
                [(st.value, now, now, now, pol.platform, i) for i, st in sorted(gone.items())],
            )
            # 4. reschedule present items
            cur.executemany(
                """
                UPDATE upstream_items u SET last_checked_at = %(now)s,
                    next_check_at = %(now)s + CASE WHEN EXISTS (
                        SELECT 1 FROM evidence_upstream_items l
                        JOIN evidence e ON e.id = l.evidence_id
                        JOIN cases c ON c.id = e.case_id
                        WHERE l.platform = u.platform AND l.item_id = u.item_id
                          AND c.status IN ('live', 'pre_launch'))
                    THEN %(open)s ELSE %(closed)s END
                WHERE platform = %(p)s AND item_id = %(i)s
                """,
                [
                    {
                        "now": now,
                        "open": pol.recheck_open,
                        "closed": pol.recheck_closed,
                        "p": pol.platform,
                        "i": i,
                    }
                    for i in present
                ],
            )

    # 3. re-apply to evidence still linked to items known to be gone (stale index, restore)
    stale = [
        r[0]
        for r in db.conn.execute(
            "SELECT DISTINCT u.item_id FROM upstream_items u"
            " JOIN evidence_upstream_items l ON l.platform = u.platform AND l.item_id = u.item_id"
            " JOIN evidence e ON e.id = l.evidence_id"
            " WHERE u.platform = %s AND u.state <> 'present'"
            " AND e.deletion_state <> 'deleted_upstream' AND NOT (u.item_id = ANY(%s))"
            " ORDER BY 1",
            (pol.platform, sorted(gone)),
        )
    ]
    _act(db, store, src, stale, log, rep, llm_store)
    rep.reapplied_items = len(stale)

    row = db.conn.execute(
        "SELECT count(*) FROM upstream_items WHERE platform = %s AND state <> 'present'"
        " AND acted_at IS NULL",
        (pol.platform,),
    ).fetchone()
    rep.detected_not_acted = (int(row[0]) if row else 0) + (len(gone) if dry_run else 0)

    if run is not None:
        for k in (
            "checked",
            "still_present",
            "unknown",
            "items_acted",
            "reapplied_items",
            "snapshots_dropped",
            "evidence_marked",
            "rows_deleted",
            "llm_cache_rows_deleted",
            "overdue_before_run",
        ):
            run.incr(f"{pol.platform}.{k}", getattr(rep, k))
    return rep
