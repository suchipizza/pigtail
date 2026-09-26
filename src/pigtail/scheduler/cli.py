"""CLI for scheduled runs (M1-T21; ADR-048, ADR-049.1): `pigtail scheduler run|plan`,
`pigtail health`, `pigtail alerts check|export`. Registered from `pigtail.cli.build_parser` via
`add_commands`.

`pigtail scheduler run --once` is the batch runner: it runs every job that is due, plus the
start-of-run jobs (one HN front-page snapshot), then exits. `pigtail scheduler run` without
`--once` is the optional long-running loop (server path, or while a launch is followed); nothing
starts it by default."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("pigtail.scheduler")


def _settings() -> Any:
    from pigtail.config import Settings

    return Settings.from_env()


def _cfg(args: argparse.Namespace) -> Any:
    from pigtail.scheduler.config import load

    return load(Path(args.config) if args.config else None)


def _alert_manager(s: Any, cfg: Any) -> Any:
    from pigtail.scheduler.alerts import AlertManager, EmailNotifier, Notifier

    notifiers: list[Notifier] = []
    try:
        email = EmailNotifier.from_env(os.environ)
    except ValueError as e:
        log.warning("alert e-mail disabled: %s", e)
        email = None
    if email is not None:
        notifiers.append(email)
    return AlertManager(
        s.data_dir / "alerts", cfg.alerts.repeat, notifiers, **_rotation(s, cfg.alerts)
    )


def _rotation(s: Any, alerts: Any) -> dict[str, Any]:
    """CB-31: alert-file rotation and retention (LOG_RETENTION_DAYS, at most 12 months)."""
    return {
        "max_bytes": alerts.file_max_bytes,
        "rotate_after": alerts.file_rotate_after,
        "retention": timedelta(days=s.log_retention_days),
    }


def _key_check(s: Any) -> int | None:
    """CB-25: refuse to start when PSEUDONYM_KEY differs from the database's key fingerprint
    (recorded on first use). Every job would fail the same check; stopping here is clearer."""
    if not s.pseudonym_key:
        return None  # jobs that need the key refuse on their own; doctor reports it
    import psycopg

    from pigtail.privacy.key_fingerprint import KeyFingerprintMismatch, verify
    from pigtail.pseudonymize import Pseudonymizer

    try:
        pz = Pseudonymizer(s.pseudonym_key)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        with psycopg.connect(s.database_url, autocommit=True, connect_timeout=10) as c:
            verify(c, pz)
    except KeyFingerprintMismatch as e:
        log.error("scheduler not started: pseudonym key fingerprint mismatch (CB-25)")
        print(str(e), file=sys.stderr)
        return 2
    return None


def cmd_scheduler_run(args: argparse.Namespace) -> int:
    from pigtail.capture.runs import utcnow
    from pigtail.db.migrate import migrate
    from pigtail.logsafe import configure_logging
    from pigtail.privacy.doctor import warn_unencrypted
    from pigtail.scheduler.alerts import evaluate
    from pigtail.scheduler.core import Scheduler
    from pigtail.scheduler.health import build_report, default_probes
    from pigtail.scheduler.jobs import Planner, pg_open_cases
    from pigtail.scheduler.launch_mode import pg_launch_mode
    from pigtail.scheduler.liveness import default_path as liveness_path
    from pigtail.scheduler.liveness import write_heartbeat
    from pigtail.scheduler.locks import PgJobLocks
    from pigtail.scheduler.runner import subprocess_runner
    from pigtail.scheduler.server import DEFAULT_PORT, HealthServer
    from pigtail.scheduler.state import PgStateStore

    configure_logging()  # CB-18: the scheduler and its threads log through RedactingFilter
    s = _settings()
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    cfg = _cfg(args)
    migrate(s.database_url)
    if (rc := _key_check(s)) is not None:
        return rc
    warn_unencrypted(s, log)  # CB-03
    probes = default_probes(s, cfg.alerts)
    alerts = _alert_manager(s, cfg)
    holder: dict[str, Scheduler] = {}

    def report() -> Any:
        sch = holder["s"]
        info = {
            "alive": sch.alive(),
            "started_at": sch.started_at.isoformat(),
            "last_tick_at": sch.last_tick_at.isoformat() if sch.last_tick_at else None,
            "last_loop_error": sch.last_loop_error,
        }
        return build_report(
            cfg, probes, utcnow(), scheduler=info, scheduler_started_at=sch.started_at
        )

    def alert_tick() -> None:
        alerts.process(evaluate(report(), cfg.alerts), utcnow())

    # M1-T26: heartbeat for the external liveness check (`pigtail health --liveness-file`)
    beat_path = Path(args.liveness_file) if args.liveness_file else liveness_path(s.data_dir)

    def heartbeat(now: Any, started_at: Any, ticks: int) -> None:
        write_heartbeat(beat_path, now, started_at=started_at, ticks=ticks)

    sched = Scheduler(
        cfg,
        store=PgStateStore(s.database_url),
        locks=PgJobLocks(s.database_url),
        planner=Planner(os.environ, pg_open_cases(s.database_url)),
        runner=subprocess_runner(),
        on_alert_tick=alert_tick,
        heartbeat=heartbeat,
        launch_mode=pg_launch_mode(s.database_url, os.environ),
    )
    holder["s"] = sched
    if args.once:
        out = sched.run_pending()
        print(json.dumps(out, indent=2))
        return 0 if all(v in ("succeeded", "skipped", "not_due") for v in out.values()) else 1

    server = None
    port = int(os.environ.get("PIGTAIL_HEALTH_PORT", DEFAULT_PORT))
    if port:
        bind = os.environ.get("PIGTAIL_HEALTH_BIND", "127.0.0.1")
        server = HealthServer(lambda: report().to_dict(), sched.alive, bind=bind, port=port)
        server.start()
    stop = threading.Event()

    def _stop(signum: int, _frame: Any) -> None:
        log.info("signal %d: stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        sched.serve(stop)
    finally:
        if server is not None:
            server.stop()
    return 0


def cmd_scheduler_plan(args: argparse.Namespace) -> int:
    """Show each job's next due time and what it would run now (no side effects)."""
    from pigtail.capture.runs import utcnow
    from pigtail.scheduler.core import next_due
    from pigtail.scheduler.jobs import Planner, pg_open_cases
    from pigtail.scheduler.launch_mode import env_override, pg_launch_mode
    from pigtail.scheduler.state import JobStats, PgStateStore

    s = _settings()
    cfg = _cfg(args)
    now = utcnow()
    stats: dict[str, JobStats] = {}
    cases = None
    launch_mode = env_override(os.environ) or False
    if s.database_url:
        try:
            stats = PgStateStore(s.database_url).stats([j.run_job for j in cfg.jobs], now)
            cases = pg_open_cases(s.database_url)
            launch_mode = pg_launch_mode(s.database_url, os.environ)(now)
        except Exception as e:
            print(f"run log unavailable: {type(e).__name__}", file=sys.stderr)
    planner = Planner(os.environ, cases)
    out: list[dict[str, Any]] = []
    for j in cfg.jobs:
        st = stats.get(j.run_job, JobStats(job=j.run_job))
        entry: dict[str, Any] = {"job": j.name, "enabled": j.enabled}
        if j.enabled:
            p = planner.plan(j, now)
            entry["next_due"] = next_due(j, st, now).isoformat()
            if j.run_at_start:
                entry["at_start"] = True
            if j.launch_mode_only and not launch_mode:
                entry["next_due"] = "next scheduler run (launch mode off)"
            entry["skip"] = p.skip
            # command lines for mentions carry repo names: show only how many
            entry["commands"] = (
                len(p.commands) if j.kind == "hn_mentions" else [list(c) for c in p.commands]
            )
        out.append(entry)
    print(json.dumps({"launch_mode": launch_mode, "jobs": out}, indent=2))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    from pigtail.capture.runs import utcnow
    from pigtail.scheduler.health import (
        build_report,
        default_probes,
        history,
        parse_days,
        render_history,
        render_text,
    )

    try:
        s = _settings()
    except ValueError as e:
        print(f"settings: {e}", file=sys.stderr)
        return 1
    now = utcnow()
    if args.liveness_file:
        return _liveness(args, s, now)
    cfg = _cfg(args)
    if args.history:
        if not s.database_url:
            print("DATABASE_URL is not set", file=sys.stderr)
            return 2
        try:
            days = parse_days(args.history)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        h = history(s.database_url, cfg, days, now)
        print(json.dumps(h, indent=2) if args.json else render_history(h))
        return 0
    r = build_report(cfg, default_probes(s, cfg.alerts), now)
    print(json.dumps(r.to_dict(), indent=2) if args.json else render_text(r))
    return 1 if r.status == "fail" else 0


def _liveness(args: argparse.Namespace, s: Any, now: Any) -> int:
    """M1-T26: check the scheduler heartbeat (run from another schedule, or another host)."""
    from pigtail.scheduler.alerts import AlertManager, EmailNotifier, Notifier
    from pigtail.scheduler.config import parse_duration
    from pigtail.scheduler.liveness import alerts_for, check

    try:
        max_age = parse_duration(args.max_age)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    result = check(args.liveness_file, now, max_age)
    out: dict[str, Any] = result.to_dict()
    if args.alert:
        from pigtail.logsafe import configure_logging

        configure_logging()
        notifiers: list[Notifier] = []
        try:
            if (email := EmailNotifier.from_env(os.environ)) is not None:
                notifiers.append(email)
        except ValueError as e:
            log.warning("alert e-mail disabled: %s", e)
        from pigtail.scheduler.config import AlertConfig

        mgr = AlertManager(
            s.data_dir / "alerts" / "liveness",
            timedelta(hours=1),
            notifiers,
            **_rotation(s, AlertConfig()),
        )
        out["alert_events"] = [e.kind for e in mgr.process(alerts_for(result), now)]
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"[{'OK' if result.ok else 'FAIL'}] scheduler liveness: {result.detail}")
    return 0 if result.ok else 1


def cmd_alerts_check(args: argparse.Namespace) -> int:
    """Evaluate the alert rules once (for hosts that run jobs from cron instead)."""
    from pigtail.capture.runs import utcnow
    from pigtail.logsafe import configure_logging
    from pigtail.scheduler.alerts import evaluate
    from pigtail.scheduler.health import build_report, default_probes

    configure_logging()
    s = _settings()
    cfg = _cfg(args)
    r = build_report(cfg, default_probes(s, cfg.alerts), utcnow())
    events = _alert_manager(s, cfg).process(evaluate(r, cfg.alerts), utcnow())
    print(json.dumps([e.to_json() for e in events], indent=2))
    return 0


def cmd_alerts_export(args: argparse.Namespace) -> int:
    from pigtail.capture.runs import utcnow
    from pigtail.scheduler.alerts import export_summary
    from pigtail.scheduler.config import parse_duration

    s = _settings()
    since: timedelta | None = parse_duration(args.since) if args.since else None
    text = export_summary(s.data_dir / "alerts", utcnow(), since)
    if args.to == "-":
        print(text, end="")
    else:
        Path(args.to).write_text(text, encoding="utf-8")
        print(f"wrote {args.to}")
    return 0


def add_commands(sub: Any) -> None:
    """Register `scheduler`, `health` and `alerts` on the top-level subparsers."""

    def cfg_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--config", help="schedule file (default PIGTAIL_SCHEDULE or infra/schedule.toml)"
        )

    sch = sub.add_parser("scheduler", help="batch runner for scheduled jobs (M1-T21, ADR-048)")
    sch_sub = sch.add_subparsers(dest="scheduler_command", required=True)
    run = sch_sub.add_parser(
        "run", help="--once: one batch run of due jobs; without it: the optional loop + /healthz"
    )
    cfg_arg(run)
    run.add_argument(
        "--once", action="store_true", help="run due and start-of-run jobs once, then exit"
    )
    run.add_argument(
        "--liveness-file",
        help="heartbeat written every tick (default PIGTAIL_LIVENESS_FILE or "
        "PIGTAIL_DATA_DIR/liveness.json; M1-T26)",
    )
    run.set_defaults(func=cmd_scheduler_run)
    plan = sch_sub.add_parser("plan", help="next due time and planned commands per job")
    cfg_arg(plan)
    plan.set_defaults(func=cmd_scheduler_plan)

    h = sub.add_parser("health", help="job lag, failures, DB/S3/disk, doctor (M1-T21)")
    cfg_arg(h)
    h.add_argument("--json", action="store_true")
    h.add_argument("--history", metavar="Nd", help="per-day run history, e.g. 7d")
    h.add_argument(
        "--liveness-file",
        metavar="PATH",
        help="M1-T26: only check the scheduler heartbeat at PATH ('-' = stdin); exit 1 if stale",
    )
    h.add_argument("--max-age", default="5m", help="--liveness-file: stale after (default 5m)")
    h.add_argument(
        "--alert", action="store_true", help="--liveness-file: raise/resolve a scheduler_dead alert"
    )
    h.set_defaults(func=cmd_health)

    al = sub.add_parser("alerts", help="alert rules and export (M1-T21)")
    al_sub = al.add_subparsers(dest="alerts_command", required=True)
    chk = al_sub.add_parser("check", help="evaluate alert rules once and notify")
    cfg_arg(chk)
    chk.set_defaults(func=cmd_alerts_check)
    ex = al_sub.add_parser("export", help="sanitized summary for the repo (ops/ALERTS.md)")
    ex.add_argument("--to", required=True, help="output path, or - for stdout")
    ex.add_argument("--since", help="window, e.g. 7d (default: everything recorded)")
    ex.set_defaults(func=cmd_alerts_export)
