"""Health report for `pigtail health` and `/healthz` (M1-T21, M1-T11).

Per job: last success, lag behind schedule, consecutive failures, failures in the last 24 h,
stale flag (no success for `stale_factor` x interval). Checks: database reachable and migrated,
object store reachable, disk usage of `PIGTAIL_DATA_DIR`, `pigtail doctor` results, and the
deletion-sync SLA (CB-02: act within 7 days). Everything is job names, counts, times and fixed
check texts: the report carries no personal data.

Each probe is injectable (`Probes`) so the report logic is tested without a database.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pigtail.scheduler.config import AlertConfig, JobSpec, ScheduleConfig, fmt_duration
from pigtail.scheduler.state import JobStats, PgStateStore

if TYPE_CHECKING:
    from pigtail.config import Settings
    from pigtail.privacy.doctor import Check

Status = Literal["ok", "warn", "fail"]
_RANK: dict[str, int] = {"ok": 0, "warn": 1, "fail": 2}


def worst(statuses: Sequence[str]) -> Status:
    top = max((_RANK.get(s, 0) for s in statuses), default=0)
    return "fail" if top == 2 else "warn" if top == 1 else "ok"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    status: Status
    detail: str
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.value is not None:
            d["value"] = self.value
        return d


@dataclass(frozen=True)
class JobHealth:
    spec: JobSpec
    stats: JobStats
    status: str  # ok | warn | fail | disabled | never_run
    stale: bool
    lag_seconds: float | None
    since_success_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.spec.name,
            "kind": self.spec.kind,
            "enabled": self.spec.enabled,
            "every": fmt_duration(self.spec.every),
            "status": self.status,
            "stale": self.stale,
            "lag_seconds": self.lag_seconds,
            "since_success_seconds": self.since_success_seconds,
            **self.stats.to_dict(),
        }


@dataclass
class HealthReport:
    generated_at: datetime
    jobs: list[JobHealth]
    checks: list[HealthCheck]
    doctor: list[HealthCheck] = field(default_factory=list)
    scheduler: dict[str, Any] | None = None

    @property
    def status(self) -> Status:
        job_st = [
            "fail" if j.status == "fail" else "warn" if j.status in ("warn", "never_run") else "ok"
            for j in self.jobs
        ]
        return worst([*job_st, *(c.status for c in self.checks), *(c.status for c in self.doctor)])

    def check(self, name: str) -> HealthCheck | None:
        return next((c for c in self.checks if c.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "generated_at": self.generated_at.isoformat(),
            "jobs": [j.to_dict() for j in self.jobs],
            "checks": [c.to_dict() for c in self.checks],
            "doctor": [c.to_dict() for c in self.doctor],
        }
        if self.scheduler is not None:
            out["scheduler"] = self.scheduler
        return out


def job_health(
    spec: JobSpec,
    st: JobStats,
    now: datetime,
    alerts: AlertConfig,
    scheduler_started_at: datetime | None = None,
) -> JobHealth:
    if not spec.enabled:
        return JobHealth(spec, st, "disabled", False, None, None)
    base = st.last_success_at or st.first_seen_at or scheduler_started_at
    since = (now - st.last_success_at).total_seconds() if st.last_success_at else None
    lag = None
    if st.last_success_at is not None:
        lag = max(0.0, (now - (st.last_success_at + spec.every)).total_seconds())
    if base is None:
        return JobHealth(spec, st, "never_run", False, lag, since)
    stale = now - base > spec.every * alerts.stale_factor
    if stale or st.consecutive_failures >= alerts.consecutive_failures:
        status = "fail"
    elif st.consecutive_failures > 0:
        status = "warn"
    else:
        status = "ok"
    return JobHealth(spec, st, status, stale, lag, since)


# --- probes ------------------------------------------------------------------------------------
@dataclass
class Probes:
    stats: Callable[[Sequence[str], datetime], dict[str, JobStats]]
    database: Callable[[], HealthCheck]
    object_store: Callable[[], HealthCheck]
    disk: Callable[[], HealthCheck]
    doctor: Callable[[], list[HealthCheck]]
    deletion_sla: Callable[[datetime], HealthCheck]


def disk_check(path: Path, threshold: float) -> HealthCheck:
    p = path.resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    u = shutil.disk_usage(p)
    pct = round(100.0 * u.used / u.total, 1) if u.total else 0.0
    status: Status = "fail" if pct > threshold else "ok"
    return HealthCheck(
        "disk", status, f"PIGTAIL_DATA_DIR volume {pct}% used (limit {threshold:g}%)", pct
    )


def database_check(url: str | None) -> HealthCheck:
    if not url:
        return HealthCheck("database", "fail", "DATABASE_URL is not set")
    from pigtail.privacy.doctor import _db_check

    c = _db_check(url)
    return HealthCheck("database", c.status if c.status != "manual" else "warn", c.detail)


def object_store_check(s: Settings) -> HealthCheck:
    if s.snapshot_backend != "s3":
        return HealthCheck("object_store", "ok", "SNAPSHOT_BACKEND=local (no object store used)")
    try:
        from pigtail.capture.snapshots import S3SnapshotStore

        store = S3SnapshotStore.from_settings(s)
        store.client.head_bucket(Bucket=store.bucket)
    except Exception as e:
        detail = f"snapshot bucket unreachable: {type(e).__name__}"
        return HealthCheck("object_store", "fail", detail)
    return HealthCheck("object_store", "ok", "snapshot bucket reachable")


def doctor_checks(s: Settings) -> list[HealthCheck]:
    """`pigtail doctor` without its database check (covered by `database`). MANUAL is info."""
    from pigtail.privacy.doctor import run_checks

    try:
        checks: list[Check] = run_checks(s, db_check=False)
    except Exception as e:
        return [HealthCheck("doctor", "warn", f"doctor could not run: {type(e).__name__}")]
    out: list[HealthCheck] = []
    for c in checks:
        if c.name == "database":
            continue
        st: Status = "fail" if c.status == "fail" else "warn" if c.status == "warn" else "ok"
        detail = f"MANUAL: {c.detail}" if c.status == "manual" else c.detail
        out.append(HealthCheck(f"doctor.{c.name}", st, detail))
    return out


def deletion_sla_check(
    url: str | None, act_within: timedelta = timedelta(days=7)
) -> Callable[[datetime], HealthCheck]:
    """CB-02: re-checks more than `act_within` late, and deletions not acted on in time."""

    def probe(now: datetime) -> HealthCheck:
        if not url:
            return HealthCheck("deletion_sla", "warn", "DATABASE_URL is not set")
        import psycopg

        try:
            with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
                row = conn.execute(
                    "SELECT count(*) FILTER (WHERE state = 'present' AND next_check_at < %s), "
                    "count(*) FILTER (WHERE state <> 'present' AND acted_at IS NULL "
                    "AND coalesce(detected_at, next_check_at) < %s) FROM upstream_items",
                    (now - act_within, now - act_within),
                ).fetchone()
        except psycopg.errors.UndefinedTable:
            detail = "upstream_items missing (pigtail db migrate)"
            return HealthCheck("deletion_sla", "warn", detail)
        except psycopg.Error as e:
            return HealthCheck("deletion_sla", "warn", f"not checked: {type(e).__name__}")
        overdue, not_acted = (int(row[0]), int(row[1])) if row else (0, 0)
        st: Status = "fail" if overdue or not_acted else "ok"
        days = act_within.days
        return HealthCheck(
            "deletion_sla",
            st,
            f"{overdue} re-checks more than {days} d late, {not_acted} deletions not acted "
            f"on within {days} d",
            float(overdue + not_acted),
        )

    return probe


def default_probes(s: Settings, alerts: AlertConfig) -> Probes:
    def stats(jobs: Sequence[str], now: datetime) -> dict[str, JobStats]:
        if not s.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        return PgStateStore(s.database_url).stats(jobs, now)

    return Probes(
        stats=stats,
        database=lambda: database_check(s.database_url),
        object_store=lambda: object_store_check(s),
        disk=lambda: disk_check(s.data_dir, alerts.disk_percent),
        doctor=lambda: doctor_checks(s),
        deletion_sla=deletion_sla_check(s.database_url),
    )


def build_report(
    cfg: ScheduleConfig,
    probes: Probes,
    now: datetime,
    *,
    scheduler: dict[str, Any] | None = None,
    scheduler_started_at: datetime | None = None,
) -> HealthReport:
    checks = [probes.database()]
    jobs: list[JobHealth] = []
    try:
        stats = probes.stats([j.run_job for j in cfg.jobs], now)
    except Exception as e:
        checks.append(HealthCheck("job_state", "fail", f"run log unavailable: {type(e).__name__}"))
        stats = {}
    for spec in cfg.jobs:
        st = stats.get(spec.run_job, JobStats(job=spec.run_job))
        jobs.append(job_health(spec, st, now, cfg.alerts, scheduler_started_at))
    checks += [probes.object_store(), probes.disk(), probes.deletion_sla(now)]
    return HealthReport(now, jobs, checks, probes.doctor(), scheduler)


def render_text(r: HealthReport) -> str:
    at = r.generated_at.isoformat(timespec="seconds")
    lines = [f"pigtail health: {r.status.upper()} at {at}"]
    if r.scheduler:
        lines.append(f"scheduler: {r.scheduler}")
    lines.append("jobs:")
    for j in r.jobs:
        st = j.stats
        last_ok = st.last_success_at.isoformat(timespec="seconds") if st.last_success_at else "-"
        lag = f"{j.lag_seconds / 60:.0f}m" if j.lag_seconds is not None else "-"
        lines.append(
            f"  [{j.status.upper():>9}] {j.spec.name:<16} every {fmt_duration(j.spec.every):<4}"
            f" last ok {last_ok}  lag {lag}  fails {st.consecutive_failures} in a row,"
            f" {st.failures_24h}/{st.runs_24h} in 24h"
            + ("  STALE" if j.stale else "")
            + ("  (last run skipped)" if st.last_skipped else "")
        )
    lines.append("checks:")
    for c in [*r.checks, *r.doctor]:
        lines.append(f"  [{c.status.upper():>4}] {c.name}: {c.detail}")
    return "\n".join(lines)


# --- history (M1 acceptance: 7 consecutive days of scans) -------------------------------------
def parse_days(value: str) -> int:
    v = value.strip().lower()
    n = int(v[:-1]) if v.endswith("d") else int(v)
    if not 1 <= n <= 366:
        raise ValueError("history must be 1d..366d")
    return n


def history(conninfo: str, cfg: ScheduleConfig, days: int, now: datetime) -> dict[str, Any]:
    """Per UTC day: scheduler runs per job (succeeded/failed/skipped), GH Archive hours covered
    (ok + missing out of 24), HN rank polls; plus the streak of complete scan days.

    A day counts toward the streak when all 24 GH Archive hours were scanned (status `ok` or
    `missing` upstream) and at least one scheduled `gharchive_scan` run succeeded that day.
    Today is excluded (incomplete by definition, the scan lags ~2 h).
    """
    import psycopg

    today = now.astimezone(UTC).date()
    first = today - timedelta(days=days)
    start = datetime(first.year, first.month, first.day, tzinfo=UTC)
    per_day: dict[date, dict[str, Any]] = {
        first + timedelta(days=i): {"jobs": {}, "gharchive_hours": 0, "hn_rank_polls": 0}
        for i in range(days + 1)
    }
    with psycopg.connect(conninfo, autocommit=True, connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT (started_at AT TIME ZONE 'UTC')::date, job, status, "
            "coalesce((counts->>'skipped')::numeric, 0) > 0, count(*) FROM runs "
            "WHERE job LIKE 'scheduler.%%' AND started_at >= %s GROUP BY 1, 2, 3, 4",
            (start,),
        ).fetchall()
        for d, job, status, skipped, n in rows:
            if d not in per_day:
                continue
            key = "skipped" if skipped else status
            j = per_day[d]["jobs"].setdefault(job.removeprefix("scheduler."), {})
            j[key] = j.get(key, 0) + int(n)
        for table, col, key in (
            ("gharchive_hours", "hour", "gharchive_hours"),
            ("hn_rank_poll", "observed_at", "hn_rank_polls"),
        ):
            try:
                got = conn.execute(
                    f"SELECT ({col} AT TIME ZONE 'UTC')::date, count(*) FROM {table} "
                    f"WHERE {col} >= %s GROUP BY 1",
                    (start,),
                ).fetchall()
            except psycopg.errors.UndefinedTable:
                got = []
            for d, n in got:
                if d in per_day:
                    per_day[d][key] = int(n)
    scan_job = next((j.name for j in cfg.jobs if j.kind == "gharchive_scan"), "gharchive_scan")
    out_days = []
    for d in sorted(per_day):
        info = per_day[d]
        scans_ok = int(info["jobs"].get(scan_job, {}).get("succeeded", 0))
        complete = d < today and info["gharchive_hours"] >= 24 and scans_ok > 0
        out_days.append({"date": d.isoformat(), "scan_day_complete": complete, **info})
    streak = 0
    for day in reversed([x for x in out_days if x["date"] < today.isoformat()]):
        if not day["scan_day_complete"]:
            break
        streak += 1
    return {"days": out_days, "scan_streak_days": streak, "generated_at": now.isoformat()}


def render_history(h: dict[str, Any]) -> str:
    lines = ["date        scan-day  gha-hours  hn-polls  jobs (succeeded/failed/skipped)"]
    for d in h["days"]:
        jobs = ", ".join(
            f"{name} {v.get('succeeded', 0)}/{v.get('failed', 0)}/{v.get('skipped', 0)}"
            for name, v in sorted(d["jobs"].items())
        )
        mark = "yes" if d["scan_day_complete"] else "no"
        lines.append(
            f"{d['date']}  {mark:<8}  {d['gharchive_hours']:>9}  {d['hn_rank_polls']:>8}  "
            f"{jobs or '-'}"
        )
    lines.append(f"consecutive complete scan days (excluding today): {h['scan_streak_days']}")
    return "\n".join(lines)
