"""GitHub API budget accounting per resource bucket (ADR-032 item 4; detection-replan §6.2, §8 M7).

One operator token (TM-02: no pooling, no App-plus-PAT doubling) has three separate buckets:

| resource  | GitHub limit                  | default cap here (≤ 70 %, replan §8 M7)  |
|-----------|-------------------------------|------------------------------------------|
| `core`    | 5,000 requests/h              | 3,500 requests/h                         |
| `graphql` | 5,000 points/h                | 3,500 points/h                           |
| `search`  | 30 requests/min (1,800/h)     | 1,260 requests/h (and 21/min, limiter)   |

The replan's steady state (§6.2) is core ≈ 3,055/h, GraphQL ≈ 700/h, search ≈ 210/h, so the
defaults leave the §6.2 plan room while never spending more than 70 % of any bucket.

Three **hard stops**, checked before every request (`Budget.before`):

1. **hourly cap** per resource (`GITHUB_BUDGET_<RESOURCE>_PER_HOUR`): the units spent in the
   current UTC hour, summed over *all* pigtail processes through the shared ledger
   (`github_budget_ledger`), may not exceed it;
2. **job cap** (`JobCaps`, per run of a job, e.g. `--max-points 600`): a single job run can't eat
   the hour;
3. **server reserve** (`GITHUB_BUDGET_RESERVE_FRACTION`, default 0.30): when GitHub reports
   `X-RateLimit-Remaining` below `reserve × X-RateLimit-Limit` for that resource, stop, unless
   the window resets within `max_wait_seconds` (then sleep until the reset). This also protects
   the token against use by other tools on the same account.

A stop raises `BudgetExhausted`; jobs catch it, record `budget_stop` on their run and exit
cleanly with partial work (all work is idempotent and resumable).

Units: `core` and `search` = 1 per request, except `304 Not Modified` answers to conditional
requests, which GitHub does not charge (REST best practices) and which are counted separately.
`graphql` = the query's `rateLimit.cost` (estimated as 1 before the call, corrected after).

The ledger also stores the last `X-RateLimit-*` headers seen per resource and hour (M7 budget
ledger: steady state ≤ 70 % per bucket).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pigtail.connectors.base import ConnectorError

Resource = Literal["core", "graphql", "search"]
RESOURCES: tuple[Resource, ...] = ("core", "graphql", "search")

# GitHub's documented per-token limits (replan §1.1, §2.2, §6.2).
GITHUB_LIMITS_PER_HOUR: dict[Resource, int] = {"core": 5000, "graphql": 5000, "search": 1800}
DEFAULT_CAP_FRACTION = 0.70  # replan §8 M7: steady state ≤ 70 % per bucket
DEFAULT_RESERVE_FRACTION = 0.30


class BudgetExhausted(ConnectorError):
    """A hard stop was hit; no request was sent."""

    def __init__(self, resource: str, reason: str, detail: str = "") -> None:
        super().__init__(f"GitHub {resource} budget stop ({reason}) {detail}".strip())
        self.resource = resource
        self.reason = reason


@dataclass(frozen=True)
class RateState:
    """Last `X-RateLimit-*` values seen for one resource."""

    limit: int | None = None
    remaining: int | None = None
    used: int | None = None
    reset: datetime | None = None


def parse_rate_headers(headers: Mapping[str, str]) -> tuple[str | None, RateState]:
    def _int(name: str) -> int | None:
        v = headers.get(name)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    reset = _int("X-RateLimit-Reset")
    return headers.get("X-RateLimit-Resource"), RateState(
        limit=_int("X-RateLimit-Limit"),
        remaining=_int("X-RateLimit-Remaining"),
        used=_int("X-RateLimit-Used"),
        reset=datetime.fromtimestamp(reset, UTC) if reset is not None else None,
    )


def hour_of(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


class BudgetLedger(Protocol):
    """Shared hourly spend per resource (Postgres in production; in-memory in tests)."""

    def used(self, hour: datetime, resource: str) -> int: ...

    def add(
        self,
        hour: datetime,
        resource: str,
        *,
        units: int,
        requests: int,
        not_modified: int = 0,
        limited: int = 0,
        state: RateState | None = None,
    ) -> None: ...


@dataclass
class MemoryLedger:
    rows: dict[tuple[datetime, str], dict[str, Any]] = field(default_factory=dict)

    def used(self, hour: datetime, resource: str) -> int:
        return int(self.rows.get((hour, resource), {}).get("units", 0))

    def add(
        self,
        hour: datetime,
        resource: str,
        *,
        units: int,
        requests: int,
        not_modified: int = 0,
        limited: int = 0,
        state: RateState | None = None,
    ) -> None:
        r = self.rows.setdefault(
            (hour, resource), {"units": 0, "requests": 0, "not_modified": 0, "limited": 0}
        )
        r["units"] += units
        r["requests"] += requests
        r["not_modified"] += not_modified
        r["limited"] += limited
        if state is not None and state.remaining is not None:
            r["state"] = state


class PostgresLedger:
    """`github_budget_ledger` (migration 0007): one row per UTC hour and resource."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def used(self, hour: datetime, resource: str) -> int:
        row = self.conn.execute(
            "SELECT units FROM github_budget_ledger WHERE hour = %s AND resource = %s",
            (hour, resource),
        ).fetchone()
        return int(row[0]) if row else 0

    def add(
        self,
        hour: datetime,
        resource: str,
        *,
        units: int,
        requests: int,
        not_modified: int = 0,
        limited: int = 0,
        state: RateState | None = None,
    ) -> None:
        s = state or RateState()
        self.conn.execute(
            """
            INSERT INTO github_budget_ledger (hour, resource, units, requests, not_modified,
                rate_limited, last_limit, last_remaining, last_used, last_reset, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (hour, resource) DO UPDATE SET
                units = github_budget_ledger.units + EXCLUDED.units,
                requests = github_budget_ledger.requests + EXCLUDED.requests,
                not_modified = github_budget_ledger.not_modified + EXCLUDED.not_modified,
                rate_limited = github_budget_ledger.rate_limited + EXCLUDED.rate_limited,
                last_limit = COALESCE(EXCLUDED.last_limit, github_budget_ledger.last_limit),
                last_remaining = COALESCE(EXCLUDED.last_remaining,
                                          github_budget_ledger.last_remaining),
                last_used = COALESCE(EXCLUDED.last_used, github_budget_ledger.last_used),
                last_reset = COALESCE(EXCLUDED.last_reset, github_budget_ledger.last_reset),
                updated_at = now()
            """,
            (
                hour,
                resource,
                units,
                requests,
                not_modified,
                limited,
                s.limit,
                s.remaining,
                s.used,
                s.reset,
            ),
        )


@dataclass(frozen=True)
class BudgetConfig:
    per_hour: Mapping[str, int] = field(
        default_factory=lambda: {
            r: int(GITHUB_LIMITS_PER_HOUR[r] * DEFAULT_CAP_FRACTION) for r in RESOURCES
        }
    )
    reserve_fraction: float = DEFAULT_RESERVE_FRACTION
    max_wait_seconds: float = 120.0  # sleep for a window reset only if it is this close

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BudgetConfig:
        caps = dict(cls().per_hour)
        for r in RESOURCES:
            raw = (env.get(f"GITHUB_BUDGET_{r.upper()}_PER_HOUR") or "").strip()
            if raw:
                v = int(raw)
                if not 0 <= v <= GITHUB_LIMITS_PER_HOUR[r]:
                    raise ValueError(
                        f"GITHUB_BUDGET_{r.upper()}_PER_HOUR must be 0..{GITHUB_LIMITS_PER_HOUR[r]}"
                    )
                caps[r] = v
        raw = (env.get("GITHUB_BUDGET_RESERVE_FRACTION") or "").strip()
        reserve = float(raw) if raw else DEFAULT_RESERVE_FRACTION
        if not 0 <= reserve < 1:
            raise ValueError("GITHUB_BUDGET_RESERVE_FRACTION must be in [0, 1)")
        return cls(per_hour=caps, reserve_fraction=reserve)


@dataclass
class JobCaps:
    """Per-run caps (units per resource); None = no job cap."""

    caps: dict[str, int | None] = field(default_factory=dict)
    spent: dict[str, int] = field(default_factory=dict)

    def remaining(self, resource: str) -> int | None:
        cap = self.caps.get(resource)
        return None if cap is None else cap - self.spent.get(resource, 0)


class Budget:
    """Pre-request hard stops plus post-response accounting. Thread-safe; one per process."""

    def __init__(
        self,
        cfg: BudgetConfig | None = None,
        ledger: BudgetLedger | None = None,
        *,
        job: JobCaps | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg or BudgetConfig()
        self.ledger: BudgetLedger = ledger if ledger is not None else MemoryLedger()
        self.job = job or JobCaps()
        self.clock = clock
        self.sleep = sleep
        self.state: dict[str, RateState] = {}
        self.stops: dict[str, int] = {}
        self._lock = threading.Lock()

    def _stop(self, resource: str, reason: str, detail: str = "") -> BudgetExhausted:
        self.stops[reason] = self.stops.get(reason, 0) + 1
        return BudgetExhausted(resource, reason, detail)

    def before(self, resource: str, est_units: int = 1) -> None:
        """Raise `BudgetExhausted` (or sleep for a close window reset) before a request."""
        with self._lock:
            now = self.clock()
            cap = self.cfg.per_hour.get(resource)
            if cap is not None and self.ledger.used(hour_of(now), resource) + est_units > cap:
                raise self._stop(resource, "hourly_cap", f"cap {cap}/h")
            left = self.job.remaining(resource)
            if left is not None and est_units > left:
                raise self._stop(resource, "job_cap", f"cap {self.job.caps.get(resource)}")
            st = self.state.get(resource)
            if st is None or st.remaining is None or st.limit is None or st.reset is None:
                return
            if st.reset <= now:
                self.state.pop(resource, None)  # window has reset; the next answer refreshes
                return
            reserve = self.cfg.reserve_fraction * st.limit
            if st.remaining - est_units >= reserve:
                return
            wait = (st.reset - now).total_seconds() + 1
            if wait > self.cfg.max_wait_seconds:
                raise self._stop(
                    resource,
                    "server_reserve",
                    f"remaining {st.remaining}/{st.limit}, resets {st.reset.isoformat()}",
                )
        self.sleep(wait)
        with self._lock:
            self.state.pop(resource, None)

    def after(
        self,
        resource: str,
        headers: Mapping[str, str],
        *,
        units: int,
        not_modified: bool = False,
        limited: bool = False,
    ) -> str:
        """Account one response; return the resource GitHub says it used."""
        hdr_resource, st = parse_rate_headers(headers)
        res = hdr_resource or resource
        with self._lock:
            if st.remaining is not None:
                self.state[res] = st
            self.job.spent[res] = self.job.spent.get(res, 0) + units
            self.ledger.add(
                hour_of(self.clock()),
                res,
                units=units,
                requests=1,
                not_modified=int(not_modified),
                limited=int(limited),
                state=st,
            )
        return res

    def adjust(self, resource: str, extra_units: int) -> None:
        """Correct an estimate (GraphQL: actual `rateLimit.cost` minus the estimate)."""
        if extra_units == 0:
            return
        with self._lock:
            self.job.spent[resource] = self.job.spent.get(resource, 0) + extra_units
            self.ledger.add(hour_of(self.clock()), resource, units=extra_units, requests=0)

    def set_graphql_state(self, limit: int, remaining: int, reset: datetime) -> None:
        with self._lock:
            self.state["graphql"] = RateState(limit=limit, remaining=remaining, reset=reset)

    def next_hour(self) -> datetime:
        return hour_of(self.clock()) + timedelta(hours=1)
