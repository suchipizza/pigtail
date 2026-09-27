"""The shared Product Hunt topic-listing cache (owner decision 2026-09-27: "reuse the category
scan across runs and briefs"; ADR-085 addendum 4; migration 0027).

Product Hunt's API allows about 230 requests an hour (measured 2026-09-27), and the selection's
topic scan (`launch_sources.run_product_hunt`, route (b)) reads every page of
`posts(topic, postedAfter, postedBefore, order: NEWEST)` for the brief's window: about ten hours
for the two default topics. The listing is the same for every brief of the instance, so it is
kept once per instance and reused:

- `ph_topic_post`: one row per (topic, post id) with the minimum the matching needs: the SHA-256
  of the post's product-slot key (`name_key_sha256` = sha256(`ph_name_key(name)`), lowercase
  hex), createdAt, featuredAt, and when it was first and last seen. **Nothing readable of the
  listing's content**: no name, slug, tagline, description, count, maker or user (Product Hunt's
  site terms bar storing "any significant portion of the Content"; ADR-085 addendum 4). A repo is
  matched by hashing its own key the same way (`ph_key_hash(ph_repo_key(...))`); the name,
  tagline and description of a hit come from a fresh `post(id:)` read, in memory only.
- `ph_topic_scan`: one row per scanned interval of one topic, with its status (`running`,
  `complete`, `truncated`, `failed`), the cursor after the last page read (resumable), the pages
  read, when it started and finished, and the cache rule (`PH_TOPIC_CACHE_RULE`). Every gap is
  scanned as **calendar-month intervals** (UTC month boundaries, clipped to the gap;
  `month_intervals`), each its own row with its own page cap, so a busy topic never truncates the
  whole window and complete months are reused.

**The gap rule** (`gaps`, `usable_scans`). For a brief's window [s, e] and a topic, an interval
is covered by a scan row of the current cache rule whose status is `complete` and whose
`finished_at` is at most `PH_TOPIC_CACHE_MAX_AGE_DAYS` (14) days before now; a scan covers its
`[posted_after, min(posted_before, started_at)]` (a post created after the scan started can't
have been listed by it). Coverage is the union of such rows. Only the parts of [s, e] no such row
covers are scanned (the "gaps"), month by month (postedAfter / postedBefore = the gap's part in
one calendar month). `truncated` and `failed` rows never cover anything.

**The index** (`window_posts`). A brief's name index is built from the cached rows of the topic
whose `created_at` lies in [s, e] (Product Hunt filters postedAfter / postedBefore on the
creation date, so this is the scan's own filter) and inside one of the scan rows the run used
(reused or scanned), seen by it (`last_seen_at` at or after the row's `started_at`): a post a
newer scan no longer lists, or one only an expired scan saw, is left out.

What changes between runs is the data fetched, not the rule: the rule (the maximum age, the gap
rule, the month split, the cache rule label) is in the pre-registered parameters
(`cache_params`), and the scan intervals a selection used (topic, interval, status, pages,
finished_at) are recorded in the fetch result and the selection's summary, so a report can say
which listing snapshot it used.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

PH_TOPIC_CACHE_RULE = "ph-topic-cache-v1"
PH_TOPIC_CACHE_MAX_AGE_DAYS = 14  # a complete scan is reused for 14 days after it finished
PH_TOPIC_CACHE_RETENTION_DAYS = 90  # cached rows not seen (scans not started) for 90 days go
GAP_RULE = (
    "for the window [s, e] and each topic: the parts of [s, e] not covered by the union of the "
    "scan rows of this cache rule whose status is complete and whose finished_at is at most "
    "max_age_days old; a scan covers [posted_after, min(posted_before, started_at)]; truncated "
    "and failed scans never cover; each gap is scanned as calendar-month intervals (UTC month "
    "boundaries, clipped to the gap), one scan row each with its own page cap "
    "(postedAfter/postedBefore = the interval), every post of every page stored, the cursor "
    "stored after each page (resumable)"
)
INDEX_RULE = (
    "the cached posts of the topic whose created_at is in [s, e] and inside a scan row the run "
    "used (reused or scanned) with last_seen_at at or after that row's started_at, keyed by "
    "name_key_sha256 (SHA-256 of the product-slot key, lowercase hex), looked up with the same "
    "hash of each repo's key; topic hits are then read fresh by post(id:)"
)

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class ScanRow:
    id: int
    topic: str
    posted_after: datetime
    posted_before: datetime
    status: str
    cursor: str | None
    pages: int
    started_at: datetime
    finished_at: datetime | None
    rule: str

    @property
    def covered(self) -> Interval:
        """What a complete scan saw: its interval up to the time it started."""
        return self.posted_after, min(self.posted_before, self.started_at)

    def to_dict(self, *, reused: bool) -> dict[str, Any]:
        """The provenance of one interval a selection used (ids are instance-local)."""
        return {
            "topic": self.topic,
            "posted_after": self.posted_after.isoformat(),
            "posted_before": self.posted_before.isoformat(),
            "status": self.status,
            "pages": self.pages,
            "started_at": self.started_at.isoformat(),
            "finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
            "reused": reused,
        }


_COLS = (
    "id, topic, posted_after, posted_before, status, cursor, pages, started_at, finished_at, rule"
)


def _row(r: Sequence[Any]) -> ScanRow:
    return ScanRow(
        int(r[0]), str(r[1]), r[2], r[3], str(r[4]), r[5], int(r[6]), r[7], r[8], str(r[9])
    )


def ph_key_hash(key: str) -> str:
    """SHA-256 (lowercase hex) of a product-slot key (`launch_sources.ph_name_key` of a post's
    name, `ph_repo_key` of a repo): the only form in which a listed name is stored."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def month_intervals(gap: Interval) -> list[Interval]:
    """`gap` split at UTC calendar-month boundaries (each part clipped to the gap; neighbours
    share their boundary instant, like the gaps themselves)."""

    a, b = gap
    out: list[Interval] = []
    cur = a
    while cur < b:
        u = cur.astimezone(UTC)
        nxt = datetime(u.year + (u.month == 12), u.month % 12 + 1, 1, tzinfo=UTC)
        out.append((cur, min(nxt, b)))
        cur = nxt
    return out


def gaps(window: Interval, covered: Iterable[Interval]) -> list[Interval]:
    """The parts of `window` no interval of `covered` covers, in order (closed intervals; a gap
    shares its bounds with the covered intervals next to it)."""
    s, e = window
    out: list[Interval] = []
    cur = s
    for a, b in sorted((max(a, s), min(b, e)) for a, b in covered if b >= s and a <= e):
        if b < a:
            continue
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < e:
        out.append((cur, e))
    return out


def usable_scans(
    conn: psycopg.Connection[Any],
    topic: str,
    *,
    now: datetime,
    max_age_days: int = PH_TOPIC_CACHE_MAX_AGE_DAYS,
    rule: str = PH_TOPIC_CACHE_RULE,
) -> list[ScanRow]:
    """The scan rows of `topic` that count as coverage now (the gap rule)."""
    rows = conn.execute(
        f"SELECT {_COLS} FROM ph_topic_scan WHERE topic = %s AND rule = %s AND status = 'complete'"
        " AND finished_at IS NOT NULL AND finished_at >= %s AND finished_at <= %s"
        " ORDER BY posted_after, id",
        (topic, rule, now - timedelta(days=max_age_days), now),
    ).fetchall()
    return [_row(r) for r in rows]


def scan_rows(conn: psycopg.Connection[Any], ids: Sequence[int]) -> list[ScanRow]:
    if not ids:
        return []
    rows = conn.execute(
        f"SELECT {_COLS} FROM ph_topic_scan WHERE id = ANY(%s) ORDER BY id", (list(ids),)
    ).fetchall()
    return [_row(r) for r in rows]


def start_scan(
    conn: psycopg.Connection[Any],
    topic: str,
    gap: Interval,
    *,
    now: datetime,
    max_age_days: int = PH_TOPIC_CACHE_MAX_AGE_DAYS,
    rule: str = PH_TOPIC_CACHE_RULE,
) -> ScanRow:
    """The scan row for one gap: an unfinished scan of exactly this interval (a crashed or failed
    run's, started within the maximum age) is resumed from its cursor, else a new row."""
    r = conn.execute(
        f"SELECT {_COLS} FROM ph_topic_scan WHERE topic = %s AND rule = %s"
        " AND status IN ('running', 'failed') AND posted_after = %s AND posted_before = %s"
        " AND started_at >= %s ORDER BY started_at DESC, id DESC LIMIT 1",
        (topic, rule, gap[0], gap[1], now - timedelta(days=max_age_days)),
    ).fetchone()
    if r is not None:
        return _row(r)
    r = conn.execute(
        "INSERT INTO ph_topic_scan (topic, posted_after, posted_before, status, pages,"
        f" started_at, rule) VALUES (%s, %s, %s, 'running', 0, %s, %s) RETURNING {_COLS}",
        (topic, gap[0], gap[1], now, rule),
    ).fetchone()
    assert r is not None
    return _row(r)


def save_page(
    conn: psycopg.Connection[Any],
    scan: ScanRow,
    posts: Sequence[Any],
    *,
    cursor: str | None,
    status: str,
    now: datetime,
) -> ScanRow:
    """Store one page: every post (project-level fields only) and the scan's progress, in one
    transaction (a crash leaves the cursor at the last page stored)."""
    from pigtail.briefs.launch_sources import ph_name_key

    with conn.transaction():
        for p in posts:  # the name is hashed here and never stored
            conn.execute(
                "INSERT INTO ph_topic_post (topic, post_id, name_key_sha256, created_at,"
                " featured_at, first_seen_at, last_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (topic, post_id) DO UPDATE SET"
                " name_key_sha256 = EXCLUDED.name_key_sha256, created_at = EXCLUDED.created_at,"
                " featured_at = EXCLUDED.featured_at, last_seen_at = EXCLUDED.last_seen_at",
                (scan.topic, p.id, ph_key_hash(ph_name_key(p.name)), p.created_at,
                 p.featured_at, now, now),
            )  # fmt: skip
        r = conn.execute(
            "UPDATE ph_topic_scan SET pages = pages + 1, cursor = %s, status = %s,"
            f" finished_at = %s WHERE id = %s RETURNING {_COLS}",
            (cursor, status, now if status != "running" else None, scan.id),
        ).fetchone()
    assert r is not None
    return _row(r)


def end_scan(
    conn: psycopg.Connection[Any], scan: ScanRow, status: str, *, now: datetime
) -> ScanRow:
    """Close a scan without a new page: `truncated` (page cap) or `failed` (a page that could not
    be parsed; the cursor stays, so a later run retries that page); or reopen a failed one
    (`running`)."""
    r = conn.execute(
        f"UPDATE ph_topic_scan SET status = %s, finished_at = %s WHERE id = %s RETURNING {_COLS}",
        (status, None if status == "running" else now, scan.id),
    ).fetchone()
    assert r is not None
    return _row(r)


def window_posts(
    conn: psycopg.Connection[Any], topic: str, window: Interval, used: Sequence[ScanRow]
) -> list[tuple[str, str]]:
    """(post id, name_key_sha256) of the cached posts the brief's index is built from
    (`INDEX_RULE`), ordered by post id."""
    s, e = window
    rows = conn.execute(
        "SELECT post_id, name_key_sha256, created_at, last_seen_at FROM ph_topic_post"
        " WHERE topic = %s AND created_at >= %s AND created_at <= %s ORDER BY post_id",
        (topic, s, e),
    ).fetchall()
    out = []
    for pid, key, created, seen in rows:
        if any(u.posted_after <= created <= u.posted_before and seen >= u.started_at for u in used):
            out.append((str(pid), str(key)))
    return out


def days(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 86400)


def coverage(
    conn: psycopg.Connection[Any],
    topics: Sequence[str],
    window: Interval,
    *,
    now: datetime,
    max_age_days: int = PH_TOPIC_CACHE_MAX_AGE_DAYS,
) -> dict[str, dict[str, Any]]:
    """Per topic, for the estimate: the window's days, the days a usable scan covers, the
    month intervals of the gaps (in days: what would be scanned, one capped scan each), the pages
    already read by unfinished scans of exactly those intervals, and the cached density (pages
    per day of the complete scans of this rule, any age; None without one)."""
    out: dict[str, dict[str, Any]] = {}
    for t in topics:
        use = usable_scans(conn, t, now=now, max_age_days=max_age_days)
        gs = gaps(window, [u.covered for u in use])
        parts = [m for g in gs for m in month_intervals(g)]
        done = conn.execute(
            "SELECT posted_after, posted_before, started_at, pages FROM ph_topic_scan"
            " WHERE topic = %s AND rule = %s AND status = 'complete'",
            (t, PH_TOPIC_CACHE_RULE),
        ).fetchall()
        span = sum(days(a, min(b, st)) for a, b, st, _p in done)
        pages = sum(int(p) for *_x, p in done)
        started = {
            (a, b): int(p)
            for a, b, p in conn.execute(
                "SELECT posted_after, posted_before, pages FROM ph_topic_scan WHERE topic = %s"
                " AND rule = %s AND status IN ('running', 'failed') AND started_at >= %s",
                (t, PH_TOPIC_CACHE_RULE, now - timedelta(days=max_age_days)),
            ).fetchall()
        }
        total = days(*window)
        out[t] = {
            "window_days": total,
            "reused_days": max(0.0, total - sum(days(a, b) for a, b in gs)),
            "gaps_days": [days(a, b) for a, b in parts],
            "gap_pages_done": [started.get(m, 0) for m in parts],
            "pages_per_day": (pages / span) if span > 0 and pages > 0 else None,
        }
    return out


def cache_params() -> dict[str, Any]:
    """The cache rule as it goes into the pre-registered parameters (`Context.params()`)."""
    return {
        "rule": PH_TOPIC_CACHE_RULE,
        "shared": "one cache per instance, reused across runs and briefs",
        "max_age_days": PH_TOPIC_CACHE_MAX_AGE_DAYS,
        "gap_rule": GAP_RULE,
        "index_rule": INDEX_RULE,
        "stored": "per topic and post: id, name_key_sha256 (SHA-256 of the product-slot key), "
        "createdAt, featuredAt, first and last seen; per scanned interval: topic, postedAfter, "
        "postedBefore, status, cursor, pages, started and finished at, rule; nothing readable "
        "of the listing's content (no name, slug, tagline, description, count or person)",
        "scan_interval": "one calendar month (UTC), clipped to the gap, each with its own page cap",
        "provenance": "the intervals a selection used (topic, interval, status, pages, "
        "finished_at, reused or scanned) are recorded in the fetch result and the selection "
        "summary (product_hunt_listing)",
    }
