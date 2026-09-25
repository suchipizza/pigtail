"""What each scheduled job runs (M1-T21). A `Planner` turns a `JobSpec` into a `Plan`: the
`pigtail` command lines to run now, or a skip reason.

Kinds:
- `command`: a fixed argv, e.g. `capture hn-ranks --once`, `retention purge`.
- `gharchive_scan`: `capture scan` over [midnight UTC `catchup_days` before the target hour,
  `now - lag`). Complete UTC days already scanned are skipped by the scanner itself (ADR-027.1,
  `gharchive_hours`), the current partial day is re-aggregated with its growing window. So the
  job is idempotent and catches up after downtime of up to `catchup_days`.
- `hn_mentions`: `capture mentions --repo … --since …` for every live case opened within
  `window` (default 48 h). Person-level: it runs only when the HN connectors are enabled *and*
  `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` (ADR-022, ADR-031.2); otherwise it is skipped and logged,
  never failed.

`requires` (list of connector names) makes any job skip while one of them is disabled, held
by ADR-022 (`PersonSourceHold`), or missing an environment variable it needs for live calls
(`requires_env`, e.g. `GITHUB_TOKEN` for the GitHub connectors: skip reason
`missing_env:GITHUB_TOKEN`, logged; M1-T24).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pigtail.scheduler.config import JobSpec

log = logging.getLogger("pigtail.scheduler")

DEFAULT_REQUIRES: dict[str, tuple[str, ...]] = {
    "gharchive_scan": ("gharchive",),
    "hn_mentions": ("hn_algolia",),
}


@dataclass(frozen=True)
class CaseRef:
    case_id: str
    repo_full_name: str
    opened_at: datetime


@dataclass(frozen=True)
class Plan:
    commands: tuple[tuple[str, ...], ...] = ()
    skip: str | None = None  # reason code: connector_disabled, person_source_hold, ...
    config: dict[str, Any] = field(default_factory=dict)


OpenCases = Callable[[datetime], Sequence[CaseRef]]


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _hour(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def connector_gate(names: Sequence[str], env: Mapping[str, str]) -> str | None:
    """Skip reason if a required connector is disabled or held by ADR-022, else None."""
    from pigtail.connectors.base import ADR022_ENV
    from pigtail.connectors.registry import CONNECTORS

    for name in names:
        cls = CONNECTORS.get(name)
        if cls is None:
            return f"unknown_connector:{name}"
        if not cls.enabled_from_env(env):
            return "connector_disabled"
        if cls.person_level_hold and env.get(ADR022_ENV, "").strip() != "1":
            return "person_source_hold"
        for var in cls.requires_env:
            if not (env.get(var) or "").strip():
                return f"missing_env:{var}"
    return None


class Planner:
    def __init__(self, env: Mapping[str, str], open_cases: OpenCases | None = None) -> None:
        self.env = env
        self.open_cases = open_cases

    def plan(self, spec: JobSpec, now: datetime) -> Plan:
        requires = tuple(spec.params.get("requires", DEFAULT_REQUIRES.get(spec.kind, ())))
        reason = connector_gate(requires, self.env)
        if reason is not None:
            if reason == "person_source_hold":
                log.warning(
                    "job %s skipped: person-level connector held by ADR-022 "
                    "(PIGTAIL_ADR022_PERSON_SOURCES_OK is not 1)",
                    spec.name,
                )
            elif reason.startswith("missing_env:"):
                log.warning(
                    "job %s skipped: %s is not set (no live call without it)",
                    spec.name,
                    reason.split(":", 1)[1],
                )
            else:
                log.info("job %s skipped: %s", spec.name, reason)
            return Plan(skip=reason, config={"requires": list(requires)})
        if spec.kind == "command":
            return Plan(commands=(spec.command,), config={"argv": list(spec.command)})
        if spec.kind == "gharchive_scan":
            return self._scan(spec, now)
        return self._mentions(spec, now)

    def _scan(self, spec: JobSpec, now: datetime) -> Plan:
        lag = spec.param_duration("lag", "2h")
        catchup = int(spec.params.get("catchup_days", 2))
        end = floor_hour(now - lag)
        start = end.replace(hour=0) - timedelta(days=catchup)
        if end <= start:
            return Plan(skip="nothing_to_scan")
        extra = [str(a) for a in spec.params.get("args", ())]
        argv: tuple[str, ...] = ("capture", "scan", "--start", _hour(start), "--end", _hour(end))
        argv = (*argv, *extra)
        return Plan(commands=(argv,), config={"argv": list(argv)})

    def _mentions(self, spec: JobSpec, now: datetime) -> Plan:
        if self.open_cases is None:
            return Plan(skip="no_case_source")
        window = spec.param_duration("window", "48h")
        before = spec.param_duration("since_before_open", "14d")
        cases = list(self.open_cases(now - window))
        if not cases:
            return Plan(skip="no_new_cases")
        extra = ("--loose",) if spec.params.get("loose") else ()
        cmds = tuple(
            (
                "capture",
                "mentions",
                "--repo",
                c.repo_full_name,
                "--since",
                _hour(c.opened_at - before),
                *extra,
            )
            for c in cases
        )
        # Repo names can contain a personal account name: the run config keeps case ids only.
        return Plan(commands=cmds, config={"cases": [c.case_id for c in cases]})


def pg_open_cases(conninfo: str) -> OpenCases:
    """Live cases opened since a cutoff, with their repo names (for `hn_mentions`)."""

    def fetch(since: datetime) -> list[CaseRef]:
        import psycopg

        with psycopg.connect(conninfo, autocommit=True, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT c.id, r.full_name, c.opened_at FROM cases c JOIN repos r "
                "ON r.id = c.repo_id WHERE c.status = 'live' AND c.opened_at >= %s "
                "ORDER BY c.opened_at",
                (since,),
            ).fetchall()
        return [CaseRef(str(a), str(b), c) for a, b, c in rows]

    return fetch
