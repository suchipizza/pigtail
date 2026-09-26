"""Launch mode, as the scheduler sees it (ADR-048.2, ADR-049.1; M11 stub, filled by M14).

A launch-mode window (`launch_mode_window`: a brief or a tracked project, `starts_at` to
`ends_at`) marks a launch that is followed closely. For now the scheduler only reads it: jobs
with `launch_mode_only = true` (the HN rank poller) run on their interval only while some window
is active, and otherwise once at the start of each scheduled run. M14 adds declaring, detecting
and ending windows.

`PIGTAIL_LAUNCH_MODE=1` forces launch mode on (manual use and tests); `0` forces it off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

log = logging.getLogger("pigtail.scheduler")

LAUNCH_MODE_ENV = "PIGTAIL_LAUNCH_MODE"

LaunchMode = Callable[[datetime], bool]


def env_override(env: Mapping[str, str]) -> bool | None:
    v = (env.get(LAUNCH_MODE_ENV) or "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return None


def active_windows(conn: psycopg.Connection[Any], now: datetime) -> int:
    """Number of launch-mode windows active at `now`."""
    row = conn.execute(
        "SELECT count(*) FROM launch_mode_window WHERE starts_at <= %s AND ends_at > %s",
        (now, now),
    ).fetchone()
    return int(row[0]) if row else 0


def pg_launch_mode(conninfo: str, env: Mapping[str, str]) -> LaunchMode:
    """`now -> bool`: the env override if set, else whether any window is active. A database
    error counts as "not in launch mode" (logged), so the scheduler keeps its batch cadence."""

    def check(now: datetime) -> bool:
        forced = env_override(env)
        if forced is not None:
            return forced
        import psycopg

        try:
            with psycopg.connect(conninfo, autocommit=True, connect_timeout=5) as conn:
                return active_windows(conn, now) > 0
        except psycopg.Error as e:
            log.warning("launch mode unknown (treated as off): %s", type(e).__name__)
            return False

    return check
