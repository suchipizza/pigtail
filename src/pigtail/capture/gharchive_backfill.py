"""Retry and backfill missing GH Archive hours (M1-T19; R1.1; ADR-027.1, ADR-027.3).

An hour is marked `missing` in `gharchive_hours` when its dump was not available at scan time:
a 404 (not published yet, or never), a 5xx/429 after the connector's own retries, a transport
error, or a dump that failed to decompress (`unparseable`, raw bytes dropped at once, CB-23b).
The scan records the reason, the attempt count and `next_retry_at` (exponential backoff: 1 h,
2 h, 4 h, ... capped at 24 h by default; `VelocityScanner.retry_base` / `retry_cap`). A missing
hour is *unknown* to the baseline, never zero (ADR-027.3), so recovering it matters.

`backfill_missing()`:

1. selects missing hours from the last `max_days` (default 7) whose `next_retry_at` has passed,
   oldest first, at most `max_hours` per run;
2. probes each one with a single download (snapshotted, content-addressed as usual). A failure
   bumps its attempt count and pushes `next_retry_at` back; nothing else changes;
3. re-aggregates every UTC day in which at least one hour came back (`scan(force=True)` over the
   span already scanned that day: per-actor lockstep features are per day, so the whole day is
   recomputed; the other hours are re-read from the snapshot store, missing ones are tried once
   more). The downloaded bytes are reused, not fetched twice;
4. re-runs detection for the 47 hours after each re-aggregated day, whose 48-hour windows
   include the recovered hours. Case opening is idempotent (deterministic ids, cooldown).

Hours older than `max_days` are no longer retried; they are reported as `expired` and stay
`missing` (unknown), so the baseline keeps treating them as unobserved. Idempotent: running it
twice retries nothing that is not due.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pigtail.capture.velocity import HOUR, Missing, VelocityScanner, floor_hour, hours_between

log = logging.getLogger("pigtail.capture.gharchive_backfill")

DEFAULT_MAX_DAYS = 7
MAX_DAYS_CAP = 30  # the raw-dump retention: older days can't be re-aggregated from the store


@dataclass(frozen=True)
class BackfillConfig:
    max_days: int = DEFAULT_MAX_DAYS
    max_hours: int = 48  # downloads per run (each hourly dump is large)

    def __post_init__(self) -> None:
        if not 1 <= self.max_days <= MAX_DAYS_CAP:
            raise ValueError(f"max_days must be 1..{MAX_DAYS_CAP}")
        if self.max_hours < 1:
            raise ValueError("max_hours must be >= 1")


@dataclass
class BackfillResult:
    due: int = 0
    tried: int = 0
    recovered: int = 0
    still_missing: int = 0
    not_due: int = 0
    expired: int = 0
    days_rescanned: int = 0
    cases_opened: list[str] = field(default_factory=list)
    still_missing_by_reason: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def due_hours(scanner: VelocityScanner, now: datetime, cfg: BackfillConfig) -> list[datetime]:
    rows = scanner.db.conn.execute(
        "SELECT hour FROM gharchive_hours WHERE status = 'missing' AND hour >= %s"
        " AND (next_retry_at IS NULL OR next_retry_at <= %s) ORDER BY hour",
        (floor_hour(now) - timedelta(days=cfg.max_days), now),
    ).fetchall()
    return [r[0] for r in rows]


def _count(scanner: VelocityScanner, cond: str, params: tuple[Any, ...]) -> int:
    row = scanner.db.conn.execute(
        f"SELECT count(*) FROM gharchive_hours WHERE status = 'missing' AND {cond}", params
    ).fetchone()
    return int(row[0]) if row else 0


def backfill_missing(
    scanner: VelocityScanner, *, now: datetime | None = None, cfg: BackfillConfig | None = None
) -> BackfillResult:
    cfg = cfg or BackfillConfig()
    now = now or scanner.connector.clock()
    res = BackfillResult()
    horizon = floor_hour(now) - timedelta(days=cfg.max_days)
    res.expired = _count(scanner, "hour < %s", (horizon,))
    res.not_due = _count(scanner, "hour >= %s AND next_retry_at > %s", (horizon, now))
    due = due_hours(scanner, now, cfg)
    res.due = len(due)
    days: set[datetime] = set()
    for h in due[: cfg.max_hours]:
        res.tried += 1
        got = scanner.fetch_hour(h)
        if isinstance(got, Missing):
            scanner.record_missing_attempt(h, got)
            continue
        scanner.prefetched[h] = got
        days.add(h.replace(hour=0))
    conn = scanner.db.conn
    for day in sorted(days):
        row = conn.execute(
            "SELECT max(hour) FROM gharchive_hours WHERE hour >= %s AND hour < %s",
            (day, day + timedelta(days=1)),
        ).fetchone()
        end = (row[0] if row and row[0] else day) + HOUR
        scan = scanner.scan(day, end, force=True)
        res.days_rescanned += 1
        res.cases_opened += [c.id for c in scan.cases]
        last = conn.execute("SELECT max(hour) FROM gharchive_hours WHERE status = 'ok'").fetchone()
        last_ok = last[0] if last and last[0] else end - HOUR
        stop = min(end + timedelta(hours=scanner.cfg.window_hours - 1), last_ok + HOUR)
        for h in hours_between(end, stop):
            res.cases_opened += [c.id for c in scanner.detect(h)]
    scanner.prefetched.clear()
    after = conn.execute(
        "SELECT hour, status, missing_reason FROM gharchive_hours WHERE hour = ANY(%s)",
        (due[: cfg.max_hours],),
    ).fetchall()
    for _h, status, reason in after:
        if status == "ok":
            res.recovered += 1
        else:
            res.still_missing += 1
            key = str(reason or "unknown")
            res.still_missing_by_reason[key] = res.still_missing_by_reason.get(key, 0) + 1
    if scanner.run is not None:
        for k in ("due", "tried", "recovered", "still_missing", "expired", "days_rescanned"):
            scanner.run.incr(f"backfill.{k}", getattr(res, k))
    log.info(
        "GH Archive backfill: %d due, %d recovered, %d still missing, %d expired",
        res.due,
        res.recovered,
        res.still_missing,
        res.expired,
    )
    return res
