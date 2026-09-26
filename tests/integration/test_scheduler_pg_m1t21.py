"""M1-T21 on Postgres: advisory-lock no-overlap, run-log state, real child processes, health
history (per-day scheduled runs and HN rank polls) and the deletion-sync SLA probe."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from pigtail.capture.runs import utcnow
from pigtail.cli import main
from pigtail.db.migrate import migrate
from pigtail.scheduler.config import JobSpec, ScheduleConfig
from pigtail.scheduler.core import Scheduler
from pigtail.scheduler.health import deletion_sla_check, history, render_history
from pigtail.scheduler.jobs import Planner, pg_open_cases
from pigtail.scheduler.locks import PgJobLocks
from pigtail.scheduler.runner import CommandResult, subprocess_runner
from pigtail.scheduler.state import PgStateStore

pytestmark = pytest.mark.db


def job(name: str = "hn_ranks", argv: Sequence[str] = ("x",), every: str = "5m") -> JobSpec:
    from pigtail.scheduler.config import parse_duration

    return JobSpec(name, "command", parse_duration(every), timedelta(minutes=2), True, tuple(argv))


@pytest.fixture
def db(pg_url: str) -> str:
    migrate(pg_url)
    return pg_url


def test_m1t21_advisory_lock_is_exclusive_across_connections(db: str) -> None:
    a, b = PgJobLocks(db), PgJobLocks(db)
    with a.hold("hn_ranks") as got_a:
        assert got_a
        with b.hold("hn_ranks") as got_b:
            assert not got_b  # same job: busy
        with b.hold("retention_purge") as got_other:
            assert got_other  # other jobs are independent
    with b.hold("hn_ranks") as again:
        assert again  # released on exit


def test_m1t21_two_schedulers_never_overlap_on_postgres(db: str) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow(argv: Sequence[str], timeout: timedelta) -> CommandResult:
        calls.append(threading.current_thread().name)
        started.set()
        release.wait(10)
        return CommandResult(0, "{}")

    cfg = ScheduleConfig(jobs=(job(),))

    def sched() -> Scheduler:
        return Scheduler(
            cfg,
            store=PgStateStore(db),
            locks=PgJobLocks(db),
            planner=Planner({}),
            runner=slow,
            code_commit="abc1234",
        )

    s1, s2 = sched(), sched()
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.update(a=s1.run_pending()), name="s1")
    t.start()
    assert started.wait(10)
    # s1's `running` record already makes the job not due for s2 ...
    assert s2.run_pending() == {}
    # ... and even a forced start cannot overlap: s1 holds the advisory lock
    assert s2.run_job(cfg.jobs[0]) == "locked"
    release.set()
    t.join(10)
    assert out["a"] == {"hn_ranks": "succeeded"}
    assert calls == ["s1"]
    assert s2.run_pending() == {}  # after s1 finished, the run log says: not due
    with psycopg.connect(db) as c:
        rows = c.execute("SELECT job, status FROM runs").fetchall()
    assert rows == [("scheduler.hn_ranks", "succeeded")]


def test_m1t21_pg_state_store_stats(db: str) -> None:
    results = [CommandResult(1, "", "e1"), CommandResult(1, "", "e2")]
    t = [datetime(2026, 9, 25, 10, tzinfo=UTC)]
    sch = Scheduler(
        ScheduleConfig(jobs=(job(every="1h"),)),
        store=PgStateStore(db),
        locks=PgJobLocks(db),
        planner=Planner({}),
        runner=lambda argv, timeout: results.pop(0) if results else CommandResult(0),
        clock=lambda: t[0],
        code_commit="abc1234",
    )
    assert sch.run_pending() == {"hn_ranks": "failed"}
    t[0] += timedelta(minutes=1)
    assert sch.run_pending() == {"hn_ranks": "failed"}
    st = PgStateStore(db).stats(["scheduler.hn_ranks"], t[0])["scheduler.hn_ranks"]
    assert st.consecutive_failures == 2 and st.last_status == "failed"
    assert st.last_success_at is None and st.failures_24h == 2
    t[0] += timedelta(minutes=2)
    assert sch.run_pending() == {"hn_ranks": "succeeded"}
    st = PgStateStore(db).stats(["scheduler.hn_ranks"], t[0])["scheduler.hn_ranks"]
    assert st.consecutive_failures == 0 and st.last_success_at is not None


def test_m1t21_real_child_process_writes_both_run_records(db: str) -> None:
    """A scheduled `retention purge --dry-run` runs in a child and records its own run too."""
    env = {**os.environ, "DATABASE_URL": db}
    runner = subprocess_runner(env)
    res = runner(["retention", "purge", "--dry-run"], timedelta(minutes=2))
    assert res.ok, res.stderr
    assert res.child_run_id() is not None
    sch = Scheduler(
        ScheduleConfig(jobs=(job("retention_purge", ("retention", "purge", "--dry-run"), "1d"),)),
        store=PgStateStore(db),
        locks=PgJobLocks(db),
        planner=Planner({}),
        runner=runner,
        code_commit="abc1234",
    )
    assert sch.run_pending() == {"retention_purge": "succeeded"}
    with psycopg.connect(db) as c:
        jobs = sorted(r[0] for r in c.execute("SELECT job FROM runs WHERE status = 'succeeded'"))
    assert jobs == ["retention.purge", "retention.purge", "scheduler.retention_purge"]
    bad = runner(
        ["report", "hn-frontpage", "--repo", "a/b", "--since", "nope"],
        timedelta(minutes=1),
    )
    assert not bad.ok and bad.returncode == 2


def _conn(db: str) -> Any:
    return psycopg.connect(db, autocommit=True)


def test_m1t21_open_cases_window(db: str) -> None:
    now = utcnow()
    with _conn(db) as c:
        c.execute(
            "INSERT INTO repos (id, host, host_id, full_name, first_seen_at) VALUES "
            "('github:1', 'github', 1, 'acme/one', now()), ('github:2', 'github', 2, 'acme/two', "
            "now())"
        )
        c.execute(
            "INSERT INTO cases (id, repo_id, opened_at, trigger, status) VALUES "
            "('case_00000000000000000001', 'github:1', %s, 'velocity', 'live'), "
            "('case_00000000000000000002', 'github:2', %s, 'velocity', 'live'), "
            "('case_00000000000000000003', 'github:2', %s, 'manual', 'closed')",
            (now - timedelta(hours=3), now - timedelta(days=5), now - timedelta(hours=1)),
        )
    got = pg_open_cases(db)(now - timedelta(hours=48))
    assert [(c.case_id, c.repo_full_name) for c in got] == [
        ("case_00000000000000000001", "acme/one")
    ]


def test_m1t21_deletion_sla_probe(db: str) -> None:
    now = utcnow()
    probe = deletion_sla_check(db)
    assert probe(now).status == "ok"
    with _conn(db) as c:
        c.execute(
            "INSERT INTO upstream_items (platform, item_id, first_seen_at, last_seen_at, "
            "next_check_at) VALUES ('hn', '1', %s, %s, %s)",
            (now, now, now - timedelta(days=8)),
        )
    got = probe(now)
    assert got.status == "fail" and got.value == 1.0 and "1 re-checks" in got.detail


def test_m1t21_history_counts_runs_and_hn_polls_per_day(db: str) -> None:
    """Per-day history of scheduled runs and HN rank polls (the GH Archive scan streak went
    with the scan in M11, ADR-047.6; the poller runs once per scheduled run, ADR-049.1)."""
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    cfg = ScheduleConfig(jobs=(job("hn_ranks", every="5m"),))
    with _conn(db) as c:
        for day in (23, 24):
            c.execute(
                "INSERT INTO runs (id, job, started_at, finished_at, status, counts) VALUES "
                "(%s, 'scheduler.hn_ranks', %s, %s, 'succeeded', '{}'), "
                "(%s, 'scheduler.hn_ranks', %s, %s, 'failed', '{}')",
                (
                    f"run_{day:032d}",
                    datetime(2026, 9, day, 5, tzinfo=UTC),
                    datetime(2026, 9, day, 5, 1, tzinfo=UTC),
                    f"run_{day + 100:032d}",
                    datetime(2026, 9, day, 6, tzinfo=UTC),
                    datetime(2026, 9, day, 6, 1, tzinfo=UTC),
                ),
            )
            c.execute(
                "INSERT INTO hn_rank_poll (observed_at, n_items, content_hash) VALUES (%s, 30, %s)",
                (datetime(2026, 9, day, 5, 0, 30, tzinfo=UTC), "a" * 64),
            )
    h = history(db, cfg, 7, now)
    by = {d["date"]: d for d in h["days"]}
    assert by["2026-09-24"]["jobs"]["hn_ranks"] == {"succeeded": 1, "failed": 1}
    assert by["2026-09-24"]["hn_rank_polls"] == 1
    assert by["2026-09-22"] == {"date": "2026-09-22", "jobs": {}, "hn_rank_polls": 0}
    assert "scan_streak_days" not in h
    assert "2026-09-23" in render_history(h)


def test_m1t21_cli_health_json_and_history(
    db: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Any
) -> None:
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PSEUDONYM_KEY", "k" * 40)
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    main(["health", "--json"])
    rep = json.loads(capsys.readouterr().out)
    assert {j["job"] for j in rep["jobs"]} >= {"hn_ranks", "gh_star_history", "deletion_sync"}
    assert next(c for c in rep["checks"] if c["name"] == "database")["status"] == "ok"
    assert main(["health", "--history", "7d"]) == 0
    assert "hn-polls" in capsys.readouterr().out
    assert main(["health", "--history", "0d"]) == 2
