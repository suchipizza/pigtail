"""M1-T21: scheduler logic with a fake clock (due-ness, backoff, skips, locks, run records)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigtail.scheduler import config as sconfig
from pigtail.scheduler.config import JobSpec, ScheduleConfig, ScheduleError, parse, parse_duration
from pigtail.scheduler.core import Scheduler, next_due
from pigtail.scheduler.jobs import CaseRef, Planner, connector_gate
from pigtail.scheduler.locks import MemoryJobLocks, lock_key
from pigtail.scheduler.runner import CommandResult
from pigtail.scheduler.state import JobStats, MemoryStateStore, RunRow, compute_stats

T0 = datetime(2026, 9, 25, 10, 7, 30, tzinfo=UTC)
REPO_SCHEDULE = Path(__file__).resolve().parents[2] / "infra" / "schedule.toml"


class FakeClock:
    def __init__(self, t: datetime = T0) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> None:
        self.t += timedelta(**kw)


class FakeRunner:
    """Scripted command results; records every argv it was asked to run."""

    def __init__(self, results: Sequence[CommandResult] = ()) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str], timeout: timedelta) -> CommandResult:
        self.calls.append(tuple(argv))
        if self.results:
            return self.results.pop(0)
        return CommandResult(0, '{"run_id": "run_' + "0" * 32 + '"}')


def spec(name: str = "hn_ranks", every: str = "5m", **kw: object) -> JobSpec:
    return JobSpec(
        name=name,
        kind="command",
        every=parse_duration(every),
        timeout=timedelta(minutes=1),
        command=("capture", "hn-ranks", "--once"),
        **kw,  # type: ignore[arg-type]
    )


def make(
    jobs: Sequence[JobSpec],
    runner: FakeRunner | None = None,
    env: dict[str, str] | None = None,
    clock: FakeClock | None = None,
    launch_mode: Callable[[datetime], bool] | None = None,
) -> tuple[Scheduler, MemoryStateStore, FakeRunner, FakeClock]:
    store = MemoryStateStore()
    r = runner or FakeRunner()
    c = clock or FakeClock()
    s = Scheduler(
        ScheduleConfig(jobs=tuple(jobs)),
        store=store,
        locks=MemoryJobLocks(),
        planner=Planner(env or {}),
        runner=r,
        clock=c,
        code_commit="abc1234",
        launch_mode=launch_mode,
    )
    return s, store, r, c


# --- config ---------------------------------------------------------------------------------
def test_m1t21_repo_schedule_parses_with_required_jobs() -> None:
    cfg = sconfig.load(REPO_SCHEDULE)
    names = {j.name: j for j in cfg.jobs}
    assert set(names) >= {
        "hn_ranks",
        "retention_purge",
        "deletion_sync",
        "hn_mentions",
        "gh_star_history",
    }
    hn = names["hn_ranks"]
    assert hn.every == timedelta(minutes=5) and hn.enabled
    assert hn.run_at_start and hn.launch_mode_only  # ADR-049.1: no always-on poller
    for daily in ("retention_purge", "deletion_sync", "gh_star_history"):
        assert names[daily].every == timedelta(days=1)
    assert names["hn_mentions"].kind == "hn_mentions"
    assert cfg.alerts.stale_factor == 3 and cfg.alerts.disk_percent == 80


@pytest.mark.parametrize(
    ("text", "seconds"), [("90s", 90), ("5m", 300), ("2h", 7200), ("1d", 86400), ("1h30m", 5400)]
)
def test_m1t21_parse_duration(text: str, seconds: int) -> None:
    assert parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("bad", ["", "5x", "m5", "5m junk"])
def test_m1t21_parse_duration_rejects(bad: str) -> None:
    with pytest.raises(ScheduleError):
        parse_duration(bad)


def test_m1t21_config_rejects_sub_minute_interval_and_bad_names() -> None:
    with pytest.raises(ScheduleError, match="TM-04"):
        parse({"jobs": {"x": {"command": ["a"], "every": "30s"}}})
    with pytest.raises(ScheduleError, match="must match"):
        parse({"jobs": {"Bad-Name": {"command": ["a"]}}})
    with pytest.raises(ScheduleError, match="command"):
        parse({"jobs": {"x": {"kind": "command"}}})
    with pytest.raises(ScheduleError, match="no \\[jobs"):
        parse({})


# --- state / timing -------------------------------------------------------------------------
def test_m1t21_compute_stats_counts_consecutive_failures_and_24h() -> None:
    rows = [
        RunRow("failed", T0 - timedelta(minutes=5), T0, error="boom"),
        RunRow("failed", T0 - timedelta(minutes=10), T0),
        RunRow("succeeded", T0 - timedelta(hours=2), T0 - timedelta(hours=2)),
        RunRow("failed", T0 - timedelta(days=2), T0 - timedelta(days=2)),
    ]
    st = compute_stats("scheduler.x", rows, T0)
    assert st.consecutive_failures == 2
    assert st.failures_24h == 2 and st.runs_24h == 3
    assert st.last_success_at == T0 - timedelta(hours=2)
    assert st.last_error == "boom"


def test_m1t21_next_due_interval_and_backoff() -> None:
    s = spec(every="1h", retry_base=timedelta(minutes=1), max_retries=3)
    assert next_due(s, JobStats("j"), T0) == T0  # never run: due now
    ok = JobStats("j", last_started_at=T0, last_status="succeeded")
    assert next_due(s, ok, T0) == T0 + timedelta(hours=1)
    for n, delay in ((1, 1), (2, 2), (3, 4)):
        st = JobStats("j", last_started_at=T0, last_status="failed", consecutive_failures=n)
        assert next_due(s, st, T0) == T0 + timedelta(minutes=delay)
    # past max_retries: back to the normal interval
    st = JobStats("j", last_started_at=T0, last_status="failed", consecutive_failures=4)
    assert next_due(s, st, T0) == T0 + timedelta(hours=1)
    # backoff never exceeds the interval
    s2 = spec(every="5m", retry_base=timedelta(minutes=4), max_retries=5)
    st = JobStats("j", last_started_at=T0, last_status="failed", consecutive_failures=3)
    assert next_due(s2, st, T0) == T0 + timedelta(minutes=5)


# --- scheduler with a fake clock ------------------------------------------------------------
def test_m1t21_runs_due_job_once_per_interval_and_writes_run_records() -> None:
    sch, store, runner, clock = make([spec(every="5m")])
    assert sch.run_pending() == {"hn_ranks": "succeeded"}
    assert sch.run_pending() == {}  # not due again yet
    clock.advance(minutes=4, seconds=59)
    assert sch.run_pending() == {}
    clock.advance(seconds=1)
    assert sch.run_pending() == {"hn_ranks": "succeeded"}
    assert runner.calls == [("capture", "hn-ranks", "--once")] * 2
    runs = sorted(store.runs.values(), key=lambda r: r.started_at)
    assert [r.job for r in runs] == ["scheduler.hn_ranks"] * 2
    assert all(r.status == "succeeded" and r.code_commit == "abc1234" for r in runs)
    assert runs[0].config["argv"] == ["capture", "hn-ranks", "--once"]
    assert runs[0].counts == {"commands": 1}


def test_m1t21_failure_retries_with_backoff_and_error_is_scrubbed() -> None:
    fail = CommandResult(1, "", "boom for user@example.org and @someone")
    s = spec(every="1h", retry_base=timedelta(minutes=1), max_retries=5)
    sch, store, _, clock = make([s], FakeRunner([fail, fail, fail]))
    assert sch.run_pending() == {"hn_ranks": "failed"}
    clock.advance(seconds=59)
    assert sch.run_pending() == {}
    clock.advance(seconds=1)  # 1 min after the first failure
    assert sch.run_pending() == {"hn_ranks": "failed"}
    clock.advance(minutes=1)
    assert sch.run_pending() == {}  # second retry waits 2 min
    clock.advance(minutes=1)
    assert sch.run_pending() == {"hn_ranks": "failed"}
    clock.advance(minutes=4)
    assert sch.run_pending() == {"hn_ranks": "succeeded"}
    stats = store.stats(["scheduler.hn_ranks"], clock())["scheduler.hn_ranks"]
    assert stats.consecutive_failures == 0 and stats.failures_24h == 3
    failed = [r for r in store.runs.values() if r.status == "failed"]
    assert len(failed) == 3
    for r in failed:  # CB-18: the error text in runs.error carries no e-mail or handle
        assert r.error is not None and "user@example.org" not in r.error
        assert "@someone" not in r.error and "exit 1" in r.error
        assert r.counts["commands_failed"] == 1
    assert [r.config["attempt"] for r in sorted(failed, key=lambda r: r.started_at)] == [1, 2, 3]


def test_m1t21_timeout_counts_as_failure() -> None:
    sch, store, _, _ = make([spec()], FakeRunner([CommandResult(-9, timed_out=True)]))
    assert sch.run_pending() == {"hn_ranks": "failed"}
    (run,) = store.runs.values()
    assert run.counts["timeouts"] == 1 and run.error and "timed out" in run.error


def test_m1t21_runner_exception_is_recorded_as_failed() -> None:
    class Boom(FakeRunner):
        def __call__(self, argv: Sequence[str], timeout: timedelta) -> CommandResult:
            raise OSError("no python")

    sch, store, _, _ = make([spec()], Boom())
    assert sch.run_pending() == {"hn_ranks": "failed"}
    (run,) = store.runs.values()
    assert run.status == "failed" and run.error and "OSError" in run.error


def test_m1t21_busy_lock_prevents_overlap() -> None:
    sch, store, runner, _ = make([spec()])
    locks = sch.locks
    assert isinstance(locks, MemoryJobLocks)
    with locks.hold("hn_ranks") as got:
        assert got
        assert sch.run_pending() == {"hn_ranks": "locked"}
    assert runner.calls == [] and store.runs == {}
    assert sch.run_pending() == {"hn_ranks": "succeeded"}


def test_m1t21_rechecks_due_after_lock_so_a_peer_run_is_not_repeated() -> None:
    """Two schedulers on one run log: the second one, arriving just after, does not re-run."""
    shared = MemoryStateStore()
    clock = FakeClock()
    runner = FakeRunner()
    cfg = ScheduleConfig(jobs=(spec(),))
    a, b = (
        Scheduler(
            cfg,
            store=shared,
            locks=MemoryJobLocks(),
            planner=Planner({}),
            runner=runner,
            clock=clock,
            code_commit="abc1234",
        )
        for _ in range(2)
    )
    due_b = b.due_jobs(clock())  # b decided the job was due ...
    assert a.run_pending() == {"hn_ranks": "succeeded"}  # ... but a ran it first
    assert b.run_job(due_b[0]) == "not_due"
    assert len(runner.calls) == 1


def test_m1t21_disabled_jobs_never_run() -> None:
    sch, _, runner, _ = make([spec(enabled=False)])
    assert sch.run_pending() == {} and runner.calls == []


def test_m1t21_state_store_error_does_not_crash_the_tick() -> None:
    class Down(MemoryStateStore):
        def stats(self, jobs: Sequence[str], now: datetime) -> dict[str, JobStats]:
            raise ConnectionError("db down")

    sch, _, _, _ = make([spec()])
    sch.store = Down()
    assert sch.run_pending() == {}
    assert sch.last_loop_error == "ConnectionError"
    assert sch.alive()


def test_m1t21_alert_tick_runs_every_alert_interval() -> None:
    sch, _, _, clock = make([spec()])
    calls: list[datetime] = []
    sch.on_alert_tick = lambda: calls.append(clock())
    sch.run_pending()
    clock.advance(minutes=1)
    sch.run_pending()
    clock.advance(minutes=4)
    sch.run_pending()
    assert calls == [T0, T0 + timedelta(minutes=5)]


# --- planner ----------------------------------------------------------------------------------
def test_m1t21_command_job_skips_when_required_connector_disabled() -> None:
    s = JobSpec("x", "command", timedelta(hours=1), timedelta(minutes=5), command=("a",),
                params={"requires": ["gharchive"]})  # fmt: skip
    plan = Planner({"PIGTAIL_CONNECTOR_GHARCHIVE_ENABLED": "false"}).plan(s, T0)
    assert plan.skip == "connector_disabled" and plan.commands == ()


def test_m11_removed_job_kinds_and_jobs_are_gone() -> None:
    """M11 acceptance: no scheduler job for the watch list, sweeps, screens, detection, the GH
    Archive scan or settle-lag remains, and the scan job kind is refused."""
    names = {j.name for j in sconfig.load(REPO_SCHEDULE).jobs}
    assert not names & {
        "gharchive_scan",
        "gharchive_backfill",
        "gh_watchlist_counts",
        "gh_search_sweep",
        "gh_hn_screen",
        "gh_star_history_confirm",
        "gh_detect_v1",
        "gh_settle_lag",
    }
    text = REPO_SCHEDULE.read_text()
    for argv in ("watchlist-counts", "search-sweep", "hn-screen", "detect-v1", "settle-lag",
                 "backfill-gharchive", '"scan"'):  # fmt: skip
        assert argv not in text.split("[jobs.", 1)[1], argv
    with pytest.raises(ScheduleError, match="kind"):
        parse({"jobs": {"s": {"kind": "gharchive_scan", "every": "1h"}}})


# --- batch runs and launch mode (ADR-049.1) ----------------------------------------------------
def hn_spec() -> JobSpec:
    return spec("hn_ranks", "5m", run_at_start=True, launch_mode_only=True)


def test_adr049_1_hn_ranks_runs_once_per_scheduled_run_outside_launch_mode() -> None:
    s, store, runner, clock = make([hn_spec()], launch_mode=lambda now: False)
    assert s.run_pending() == {"hn_ranks": "succeeded"}  # start of the run: one snapshot
    for _ in range(4):  # the loop keeps ticking: no always-on polling
        clock.advance(minutes=6)
        assert s.run_pending() == {}
    assert len(runner.calls) == 1
    (rec,) = [r for r in store.runs.values() if r.job == "scheduler.hn_ranks"]
    assert rec.config["at_start"] is True
    # the next scheduled run (a new `scheduler run --once`) takes one more snapshot, even if the
    # last one was a minute ago
    s2 = Scheduler(
        ScheduleConfig(jobs=(hn_spec(),)),
        store=store,
        locks=MemoryJobLocks(),
        planner=Planner({}),
        runner=runner,
        clock=clock,
        code_commit="abc1234",
        launch_mode=lambda now: False,
    )
    assert s2.run_pending() == {"hn_ranks": "succeeded"}
    assert len(runner.calls) == 2


def test_adr049_1_hn_ranks_polls_continuously_in_launch_mode() -> None:
    on = {"v": False}
    s, _store, runner, clock = make([hn_spec()], launch_mode=lambda now: on["v"])
    s.run_pending()
    on["v"] = True
    for _ in range(3):
        clock.advance(minutes=5, seconds=1)  # past the 1-minute launch-mode cache too
        assert s.run_pending() == {"hn_ranks": "succeeded"}
    on["v"] = False
    clock.advance(minutes=5, seconds=1)
    assert s.run_pending() == {}
    assert len(runner.calls) == 4


def test_adr049_1_launch_mode_check_failure_means_batch_cadence() -> None:
    def boom(now: datetime) -> bool:
        raise RuntimeError("db down")

    s, _store, _runner, clock = make([hn_spec(), spec("other", "5m")], launch_mode=boom)
    assert s.run_pending() == {"hn_ranks": "succeeded", "other": "succeeded"}
    clock.advance(minutes=6)
    assert s.run_pending() == {"other": "succeeded"}  # other jobs keep their interval


def test_adr049_1_schedule_flags_must_be_booleans() -> None:
    job = {"command": ["a"], "every": "5m"}
    cfg = parse({"jobs": {"a": {**job, "run_at_start": True, "launch_mode_only": True}}})
    assert cfg.jobs[0].run_at_start and cfg.jobs[0].launch_mode_only
    with pytest.raises(ScheduleError, match="run_at_start"):
        parse({"jobs": {"a": {**job, "run_at_start": "yes"}}})


def test_adr049_1_launch_mode_env_override() -> None:
    from pigtail.scheduler.launch_mode import env_override, pg_launch_mode

    assert env_override({}) is None
    assert env_override({"PIGTAIL_LAUNCH_MODE": "1"}) is True
    assert env_override({"PIGTAIL_LAUNCH_MODE": "off"}) is False
    # the override wins without touching the database (the URL is never connected to)
    assert pg_launch_mode("postgresql://invalid", {"PIGTAIL_LAUNCH_MODE": "1"})(T0) is True


def _mentions_spec() -> JobSpec:
    return JobSpec(
        "hn_mentions",
        "hn_mentions",
        timedelta(hours=3),
        timedelta(hours=1),
        params={"window": "48h", "since_before_open": "14d"},
    )


def test_m1t21_mentions_skipped_while_hn_disabled_by_default() -> None:
    plan = Planner({}, lambda since: [CaseRef("case_1", "o/r", T0)]).plan(_mentions_spec(), T0)
    assert plan.skip == "connector_disabled"


def test_m1t21_mentions_respect_person_source_hold(caplog: pytest.LogCaptureFixture) -> None:
    """ADR-022 / ADR-031.2: HN enabled without the operator flag → skip and log, never run."""
    caplog.set_level(logging.WARNING, logger="pigtail.scheduler")
    cases = lambda since: [CaseRef("case_1", "o/r", T0)]  # noqa: E731
    plan = Planner({"PIGTAIL_ENABLE_HN": "1"}, cases).plan(_mentions_spec(), T0)
    assert plan.skip == "person_source_hold" and plan.commands == ()
    assert "ADR-022" in caplog.text

    sch, store, runner, _ = make([_mentions_spec()], env={"PIGTAIL_ENABLE_HN": "1"})
    assert sch.run_pending() == {"hn_mentions": "skipped"}
    assert runner.calls == []
    (run,) = store.runs.values()
    assert run.status == "succeeded" and run.counts == {"skipped": 1}
    assert run.config["skipped"] == "person_source_hold"


def test_m1t21_mentions_run_per_new_case_when_allowed() -> None:
    env = {"PIGTAIL_ENABLE_HN": "1", "PIGTAIL_ADR022_PERSON_SOURCES_OK": "1"}
    seen: list[datetime] = []

    def cases(since: datetime) -> list[CaseRef]:
        seen.append(since)
        return [CaseRef("case_a", "acme/tool", datetime(2026, 9, 24, 13, tzinfo=UTC))]

    plan = Planner(env, cases).plan(_mentions_spec(), T0)
    assert seen == [T0 - timedelta(hours=48)]
    assert plan.commands == (
        ("capture", "mentions", "--repo", "acme/tool", "--since", "2026-09-10T13:00"),
    )
    assert plan.config == {"cases": ["case_a"]}  # no repo names in the run config
    assert Planner(env, lambda s: []).plan(_mentions_spec(), T0).skip == "no_new_cases"


def test_m1t21_connector_gate() -> None:
    assert connector_gate(["hn_ranks"], {}) is None  # on by default (ADR-031.1)
    assert connector_gate(["hn_ranks"], {"PIGTAIL_CONNECTOR_HN_RANKS_ENABLED": "0"}) == (
        "connector_disabled"
    )
    assert connector_gate(["nope"], {}) == "unknown_connector:nope"


def test_m1t21_lock_key_stable_and_distinct() -> None:
    assert lock_key("hn_ranks") == lock_key("hn_ranks")
    assert lock_key("hn_ranks") != lock_key("retention_purge")
    assert -(2**63) <= lock_key("x") < 2**63


def test_m1t21_command_result_child_run_id() -> None:
    rid = "run_" + "a" * 32
    assert CommandResult(0, f'{{\n  "run_id": "{rid}"\n}}\n').child_run_id() == rid
    assert CommandResult(0, f'log line\n{{"run_id": "{rid}"}}\n').child_run_id() == rid
    assert CommandResult(0, "not json").child_run_id() is None


def test_m1t21_child_installs_redacting_filter(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CB-18: every scheduled job logs through RedactingFilter, even commands that don't set it."""
    import pigtail.cli
    from pigtail.logsafe import RedactingFilter
    from pigtail.scheduler import child

    def fake_main(argv: list[str]) -> int:
        logging.getLogger("pigtail.job").warning("contact someone@example.org now")
        return 7

    monkeypatch.setattr(pigtail.cli, "main", fake_main)
    caplog.set_level(logging.INFO)
    assert child.main(["retention", "purge"]) == 7
    root = logging.getLogger()
    assert any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters)
    assert "someone@example.org" not in caplog.text


def test_m1t21_abandoned_running_record_is_closed_and_retried() -> None:
    from pigtail.capture.models import Run

    sch, store, runner, clock = make([spec(every="1h", retry_base=timedelta(minutes=1))])
    store.record(
        Run(id="run_" + "1" * 32, job="scheduler.hn_ranks", started_at=T0, status="running")
    )
    assert sch.run_pending() == {}  # a running record counts as started: not due yet
    clock.advance(hours=1)
    assert sch.run_pending() == {"hn_ranks": "succeeded"}
    old = store.runs["run_" + "1" * 32]
    assert old.status == "failed" and old.error and "abandoned" in old.error
    assert len(runner.calls) == 1
