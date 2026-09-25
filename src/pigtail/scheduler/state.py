"""Per-job scheduler state, derived from `run` records (M1-T21).

Every scheduler attempt writes a `runs` row with `job = 'scheduler.<name>'` through the existing
`RunRecorder`. That table is the persisted state: last attempt, last success, consecutive
failures and error counts are computed from it, so a restarted scheduler (or `pigtail health` in
another process) sees exactly what happened. No extra table or migration is needed.

`compute_stats` is pure; `PgStateStore` feeds it rows from Postgres, `MemoryStateStore` from a
list (tests).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from pigtail.capture.models import Run

HISTORY_ROWS = 200  # rows per job used to count consecutive failures
ORPHAN_ERROR = "abandoned: the scheduler stopped during this run"


@dataclass(frozen=True)
class RunRow:
    status: str  # running | succeeded | failed
    started_at: datetime
    finished_at: datetime | None = None
    skipped: bool = False
    error: str | None = None


@dataclass(frozen=True)
class JobStats:
    job: str
    last_started_at: datetime | None = None
    last_status: str | None = None
    last_finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_skipped: bool = False
    first_seen_at: datetime | None = None  # oldest attempt in the fetched window
    consecutive_failures: int = 0
    failures_24h: int = 0
    runs_24h: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        def iso(d: datetime | None) -> str | None:
            return d.isoformat() if d else None

        return {
            "last_started_at": iso(self.last_started_at),
            "last_status": ("skipped" if self.last_skipped else self.last_status),
            "last_finished_at": iso(self.last_finished_at),
            "last_success_at": iso(self.last_success_at),
            "consecutive_failures": self.consecutive_failures,
            "failures_24h": self.failures_24h,
            "runs_24h": self.runs_24h,
        }


def compute_stats(
    job: str, rows: Sequence[RunRow], now: datetime, last_success_at: datetime | None = None
) -> JobStats:
    """`rows` newest first. `last_success_at` covers successes older than the fetched rows."""
    if not rows:
        return JobStats(job=job, last_success_at=last_success_at)
    last = rows[0]
    consecutive = 0
    for r in rows:
        if r.status == "succeeded":
            break
        if r.status == "failed":
            consecutive += 1
    succ = [r.finished_at or r.started_at for r in rows if r.status == "succeeded"]
    last_ok = max([*succ, *([last_success_at] if last_success_at else [])], default=None)
    day = now - timedelta(days=1)
    recent = [r for r in rows if r.started_at >= day]
    return JobStats(
        job=job,
        last_started_at=last.started_at,
        last_status=last.status,
        last_finished_at=last.finished_at,
        last_success_at=last_ok,
        last_skipped=last.skipped,
        first_seen_at=rows[-1].started_at,
        consecutive_failures=consecutive,
        failures_24h=sum(1 for r in recent if r.status == "failed"),
        runs_24h=len(recent),
        last_error=next((r.error for r in rows if r.status == "failed" and r.error), None),
    )


class StateStore(Protocol):
    def stats(self, jobs: Sequence[str], now: datetime) -> dict[str, JobStats]: ...

    def record(self, run: Run) -> None: ...

    def close_orphans(self, job: str, now: datetime) -> int:
        """Mark `running` records of `job` as failed. Call only while holding the job's lock:
        then no live run of that job exists, so they were left by a killed scheduler."""
        ...


class MemoryStateStore:
    """In-memory run log (tests, dry runs)."""

    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def record(self, run: Run) -> None:
        with self._lock:
            self.runs[run.id] = run

    def rows(self, job: str) -> list[RunRow]:
        with self._lock:
            runs = [r for r in self.runs.values() if r.job == job]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return [_row(r.status, r.started_at, r.finished_at, r.counts, r.error) for r in runs]

    def stats(self, jobs: Sequence[str], now: datetime) -> dict[str, JobStats]:
        return {j: compute_stats(j, self.rows(j), now) for j in jobs}

    def close_orphans(self, job: str, now: datetime) -> int:
        with self._lock:
            stale = [r for r in self.runs.values() if r.job == job and r.status == "running"]
            for r in stale:
                self.runs[r.id] = r.model_copy(
                    update={"status": "failed", "finished_at": now, "error": ORPHAN_ERROR}
                )
        return len(stale)


def _row(
    status: str,
    started: datetime,
    finished: datetime | None,
    counts: Any,
    error: str | None,
) -> RunRow:
    skipped = bool(isinstance(counts, dict) and counts.get("skipped"))
    return RunRow(status, started, finished, skipped, error)


class PgStateStore:
    """Reads and writes `runs` with a short-lived connection per call (thread-safe)."""

    def __init__(self, conninfo: str, connect_timeout: int = 5) -> None:
        self.conninfo = conninfo
        self.connect_timeout = connect_timeout

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.conninfo, autocommit=True, connect_timeout=self.connect_timeout)

    def record(self, run: Run) -> None:
        from pigtail.capture.db import CaptureDB

        with self._connect() as conn:
            CaptureDB(conn).upsert_run(run)

    def close_orphans(self, job: str, now: datetime) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'failed', finished_at = %s, error = %s "
                "WHERE job = %s AND status = 'running'",
                (now, ORPHAN_ERROR, job),
            )
            return int(cur.rowcount or 0)

    def stats(self, jobs: Sequence[str], now: datetime) -> dict[str, JobStats]:
        out: dict[str, JobStats] = {}
        with self._connect() as conn:
            for job in jobs:
                rows = conn.execute(
                    "SELECT status, started_at, finished_at, counts, error FROM runs "
                    "WHERE job = %s ORDER BY started_at DESC LIMIT %s",
                    (job, HISTORY_ROWS),
                ).fetchall()
                ok = conn.execute(
                    "SELECT max(coalesce(finished_at, started_at)) FROM runs "
                    "WHERE job = %s AND status = 'succeeded'",
                    (job,),
                ).fetchone()
                out[job] = compute_stats(job, [_row(*r) for r in rows], now, ok[0] if ok else None)
        return out
