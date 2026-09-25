"""External liveness check for the scheduler (M1-T26).

A dead scheduler cannot alert about itself, and `/healthz` answers only while its process runs.
So the scheduler writes a **heartbeat file** at every tick (`pigtail scheduler run
--liveness-file PATH`, default `$PIGTAIL_LIVENESS_FILE` or `$PIGTAIL_DATA_DIR/liveness.json`),
and a separate process on a separate schedule checks it:

    pigtail health --liveness-file PATH [--max-age 5m] [--alert]

It exits 0 while the heartbeat is fresh and 1 when it is stale, missing or unreadable. With
`--alert` it raises (and later resolves) a critical `scheduler_dead` alert through the usual
alert sink (host-local files and optional e-mail, `pigtail.scheduler.alerts`), with its own
state directory (`$PIGTAIL_DATA_DIR/alerts/liveness/`) so it never resolves the scheduler's own
alerts. `PATH` may be `-` to read the heartbeat from stdin, e.g. over SSH from a second host
(docs/guides/operator.md "External liveness check"). Run it from a systemd timer
(`infra/systemd/pigtail-liveness.{service,timer}`) or cron.

The heartbeat holds only times and a tick count: `{"at", "started_at", "ticks", "pid"}`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pigtail.scheduler.alerts import Alert

LIVENESS_ENV = "PIGTAIL_LIVENESS_FILE"
DEFAULT_MAX_AGE = timedelta(minutes=5)
State = Literal["ok", "stale", "missing", "invalid"]


def default_path(data_dir: Path) -> Path:
    env = os.environ.get(LIVENESS_ENV, "").strip()
    return Path(env) if env else data_dir / "liveness.json"


def write_heartbeat(path: Path, now: datetime, *, started_at: datetime, ticks: int) -> None:
    """Atomic write (temp file + rename): a reader never sees a half-written heartbeat."""
    body = {
        "at": now.isoformat(),
        "started_at": started_at.isoformat(),
        "ticks": ticks,
        "pid": os.getpid(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class Liveness:
    state: State
    detail: str
    at: datetime | None = None
    age_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "heartbeat_at": self.at.isoformat() if self.at else None,
            "age_seconds": self.age_seconds,
        }


def read_text(path: str) -> str | None:
    """The heartbeat text from a file, or from stdin for `-`; None if it cannot be read."""
    try:
        if path == "-":
            return sys.stdin.read()
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def check_text(text: str | None, now: datetime, max_age: timedelta) -> Liveness:
    if not text or not text.strip():
        return Liveness("missing", "no heartbeat (file missing, empty, or host unreachable)")
    try:
        at = datetime.fromisoformat(str(json.loads(text)["at"]))
    except (ValueError, KeyError, TypeError):
        return Liveness("invalid", "heartbeat is not valid JSON with an ISO `at` time")
    if at.tzinfo is None:
        return Liveness("invalid", "heartbeat time has no time zone")
    age = (now - at).total_seconds()
    if age > max_age.total_seconds():
        return Liveness(
            "stale", f"last heartbeat {int(age)} s ago (> {int(max_age.total_seconds())} s)",
            at, age,
        )  # fmt: skip
    return Liveness("ok", f"last heartbeat {max(0, int(age))} s ago", at, age)


def check(path: str, now: datetime, max_age: timedelta = DEFAULT_MAX_AGE) -> Liveness:
    return check_text(read_text(path), now, max_age)


def alerts_for(result: Liveness) -> list[Alert]:
    if result.ok:
        return []
    return [
        Alert(
            "scheduler_dead",
            "liveness",
            "critical",
            f"scheduler heartbeat {result.state}: {result.detail}",
        )
    ]
