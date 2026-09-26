"""Schedule configuration (`infra/schedule.toml`, M1-T21). Parsed with stdlib `tomllib`.

```toml
[scheduler]
tick = "15s"                 # how often the loop checks for due jobs
alert_every = "5m"           # how often alert rules are evaluated

[alerts]
stale_factor = 3             # job stale when no success for stale_factor x its interval
consecutive_failures = 3
disk_percent = 80
repeat = "6h"                # re-notify a still-firing alert at most this often
login_failures = 10          # CB-30: failed UI logins (incl. rate-limited) per login_window
login_window = "1h"
integrity_window = "24h"     # CB-30: snapshot hash mismatches seen in the UI
file_max_bytes = 1000000     # CB-31: rotate ALERTS.md / alerts.jsonl at this size ...
file_rotate_after = "30d"    # ... or when their first event is this old

[jobs.hn_ranks]
kind = "command"             # command | hn_mentions
command = ["capture", "hn-ranks", "--once"]
every = "5m"
timeout = "4m"
run_at_start = true          # ADR-049.1: once at the start of every scheduler run, due or not
launch_mode_only = true      # ADR-049.1: between starts, due on `every` only in launch mode
```
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

JobKind = Literal["command", "hn_mentions"]
JOB_KINDS: tuple[JobKind, ...] = ("command", "hn_mentions")
_NAME = re.compile(r"^[a-z0-9_]{1,48}$")
_DUR = re.compile(r"(\d+)\s*([smhd])")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}
SCHEDULE_ENV = "PIGTAIL_SCHEDULE"


class ScheduleError(ValueError):
    pass


def parse_duration(value: str | int | float) -> timedelta:
    """`"90s"`, `"5m"`, `"2h"`, `"1d"`, `"1h30m"`, or a number of seconds."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return timedelta(seconds=float(value))
    text = str(value).strip().lower()
    parts = _DUR.findall(text)
    if not parts or _DUR.sub("", text).strip():
        raise ScheduleError(f"bad duration {value!r}; use e.g. 90s, 5m, 2h, 1d")
    return timedelta(seconds=sum(int(n) * _UNIT[u] for n, u in parts))


def fmt_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s and s % size == 0:
            return f"{s // size}{unit}"
    return f"{s}s"


@dataclass(frozen=True)
class JobSpec:
    name: str
    kind: JobKind
    every: timedelta
    timeout: timedelta
    enabled: bool = True
    command: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
    max_retries: int = 5  # backoff retries before falling back to the normal interval
    retry_base: timedelta = timedelta(minutes=1)
    # ADR-049.1: run once at the start of every scheduler run (`scheduler run --once`, or the
    # first tick of the loop), whether due or not
    run_at_start: bool = False
    # ADR-049.1: apart from the start-of-run execution, due on `every` only while a launch-mode
    # window is active (`pigtail.scheduler.launch_mode`)
    launch_mode_only: bool = False

    @property
    def run_job(self) -> str:
        """The `runs.job` value of this job's scheduler records."""
        return f"scheduler.{self.name}"

    def param_duration(self, key: str, default: str) -> timedelta:
        return parse_duration(self.params.get(key, default))


@dataclass(frozen=True)
class AlertConfig:
    stale_factor: float = 3.0
    consecutive_failures: int = 3
    disk_percent: float = 80.0
    repeat: timedelta = timedelta(hours=6)
    # CB-30: alert rules on the UI audit log (ui_audit_log)
    login_failures: int = 10
    login_window: timedelta = timedelta(hours=1)
    integrity_window: timedelta = timedelta(hours=24)
    # CB-31: rotation of the host alert files (retention: LOG_RETENTION_DAYS, max 12 months)
    file_max_bytes: int = 1_000_000
    file_rotate_after: timedelta = timedelta(days=30)


@dataclass(frozen=True)
class ScheduleConfig:
    jobs: tuple[JobSpec, ...]
    alerts: AlertConfig = AlertConfig()
    tick: timedelta = timedelta(seconds=15)
    alert_every: timedelta = timedelta(minutes=5)

    def job(self, name: str) -> JobSpec:
        for j in self.jobs:
            if j.name == name:
                return j
        raise KeyError(name)

    @property
    def enabled_jobs(self) -> tuple[JobSpec, ...]:
        return tuple(j for j in self.jobs if j.enabled)


def default_path() -> Path:
    """`PIGTAIL_SCHEDULE`, else `infra/schedule.toml` at the repo root."""
    env = os.environ.get(SCHEDULE_ENV)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "infra" / "schedule.toml"


def load(path: Path | None = None) -> ScheduleConfig:
    p = path or default_path()
    with p.open("rb") as f:
        return parse(tomllib.load(f))


def _job(name: str, raw: Mapping[str, Any]) -> JobSpec:
    if not _NAME.match(name):
        raise ScheduleError(f"job name {name!r} must match {_NAME.pattern}")
    kind = raw.get("kind", "command")
    if kind not in JOB_KINDS:
        raise ScheduleError(f"job {name}: kind must be one of {JOB_KINDS}, got {kind!r}")
    command = tuple(str(x) for x in raw.get("command", ()))
    if kind == "command" and not command:
        raise ScheduleError(f"job {name}: kind 'command' needs a non-empty `command` list")
    every = parse_duration(raw.get("every", "1h"))
    if every < timedelta(minutes=1):
        raise ScheduleError(f"job {name}: interval under 1 minute is refused (TM-04)")
    timeout = parse_duration(raw.get("timeout", fmt_duration(every)))
    if timeout <= timedelta(0):
        raise ScheduleError(f"job {name}: timeout must be positive")
    known = {
        "kind",
        "command",
        "every",
        "timeout",
        "enabled",
        "max_retries",
        "retry_base",
        "run_at_start",
        "launch_mode_only",
    }
    for flag in ("run_at_start", "launch_mode_only"):
        if not isinstance(raw.get(flag, False), bool):
            raise ScheduleError(f"job {name}: `{flag}` must be true or false")
    params = {k: v for k, v in raw.items() if k not in known}
    return JobSpec(
        name=name,
        kind=kind,
        every=every,
        timeout=timeout,
        enabled=bool(raw.get("enabled", True)),
        command=command,
        params=params,
        max_retries=int(raw.get("max_retries", 5)),
        retry_base=parse_duration(raw.get("retry_base", "1m")),
        run_at_start=bool(raw.get("run_at_start", False)),
        launch_mode_only=bool(raw.get("launch_mode_only", False)),
    )


def parse(data: Mapping[str, Any]) -> ScheduleConfig:
    jobs_raw = data.get("jobs", {})
    if not isinstance(jobs_raw, Mapping) or not jobs_raw:
        raise ScheduleError("schedule has no [jobs.<name>] tables")
    jobs = tuple(_job(n, r) for n, r in jobs_raw.items())
    a = data.get("alerts", {})
    alerts = AlertConfig(
        stale_factor=float(a.get("stale_factor", 3)),
        consecutive_failures=int(a.get("consecutive_failures", 3)),
        disk_percent=float(a.get("disk_percent", 80)),
        repeat=parse_duration(a.get("repeat", "6h")),
        login_failures=int(a.get("login_failures", 10)),
        login_window=parse_duration(a.get("login_window", "1h")),
        integrity_window=parse_duration(a.get("integrity_window", "24h")),
        file_max_bytes=int(a.get("file_max_bytes", 1_000_000)),
        file_rotate_after=parse_duration(a.get("file_rotate_after", "30d")),
    )
    if alerts.login_failures < 1:
        raise ScheduleError("alerts.login_failures must be >= 1")
    if alerts.file_max_bytes < 1024:
        raise ScheduleError("alerts.file_max_bytes must be >= 1024")
    if alerts.file_rotate_after < timedelta(days=1):
        raise ScheduleError("alerts.file_rotate_after must be at least 1d")
    s = data.get("scheduler", {})
    return ScheduleConfig(
        jobs=jobs,
        alerts=alerts,
        tick=parse_duration(s.get("tick", "15s")),
        alert_every=parse_duration(s.get("alert_every", "5m")),
    )
