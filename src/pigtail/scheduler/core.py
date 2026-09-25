"""The scheduler loop (M1-T21).

Design (ADR candidate): a small in-process loop, not APScheduler. The persisted state is the
existing `runs` table (`scheduler.<job>` records, see `state`), overlap is prevented by a
Postgres advisory lock per job (`locks`), and each job command runs in a child process
(`runner`). This adds no dependency, survives restarts without a job store, and makes every
attempt visible in the same run log as the pipelines themselves.

Timing per job (`next_due`):
- never run: due now;
- last attempt succeeded (or was skipped, or is still running elsewhere): `started + every`;
- a `running` record found while holding the job's lock was left by a killed scheduler: it is
  closed as failed ("abandoned") first, so it counts toward retries and alerts;
- last attempt failed: retry with exponential backoff `retry_base * 2**(n-1)`, capped at
  `every`, for up to `max_retries` consecutive failures; after that, back to `every`.

`run_job` takes the job's lock, re-checks that the job is still due (another scheduler may have
just run it), plans, then runs the commands inside a `RunRecorder`, so each attempt writes one
`run` record (`succeeded`, `failed` with scrubbed error text, or `succeeded` with
`counts.skipped = 1` when a precondition such as the ADR-022 hold makes it skip).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Literal

from pigtail.capture.runs import RunRecorder, git_commit, utcnow
from pigtail.scheduler.config import JobSpec, ScheduleConfig
from pigtail.scheduler.jobs import Planner
from pigtail.scheduler.locks import JobLocks
from pigtail.scheduler.runner import Runner
from pigtail.scheduler.state import JobStats, StateStore

log = logging.getLogger("pigtail.scheduler")

Outcome = Literal["succeeded", "failed", "skipped", "locked", "not_due", "error"]


class JobFailed(RuntimeError):
    pass


def next_due(spec: JobSpec, stats: JobStats, now: datetime) -> datetime:
    if stats.last_started_at is None:
        return now
    n = stats.consecutive_failures
    if stats.last_status == "failed" and 0 < n <= spec.max_retries:
        delay: timedelta = min(spec.retry_base * (2 ** (n - 1)), spec.every)
    else:
        delay = spec.every
    return stats.last_started_at + delay


class Scheduler:
    def __init__(
        self,
        cfg: ScheduleConfig,
        *,
        store: StateStore,
        locks: JobLocks,
        planner: Planner,
        runner: Runner,
        clock: Callable[[], datetime] = utcnow,
        code_commit: str | None = None,
        on_alert_tick: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.locks = locks
        self.planner = planner
        self.runner = runner
        self.clock = clock
        self.code_commit = code_commit if code_commit is not None else git_commit()
        self.on_alert_tick = on_alert_tick
        self.started_at = clock()
        self.last_tick_at: datetime | None = None
        self.last_loop_error: str | None = None
        self._last_alert_at: datetime | None = None
        self._running: set[str] = set()
        self._mu = threading.Lock()

    # --- decisions ------------------------------------------------------------------------
    def due_jobs(self, now: datetime) -> list[JobSpec]:
        specs = self.cfg.enabled_jobs
        stats = self.store.stats([s.run_job for s in specs], now)
        with self._mu:
            running = set(self._running)
        return [
            s for s in specs if s.name not in running and next_due(s, stats[s.run_job], now) <= now
        ]

    def alive(self, now: datetime | None = None) -> bool:
        """The loop has ticked recently (for `/healthz`)."""
        if self.last_tick_at is None:
            return False
        limit = max(self.cfg.tick * 4, timedelta(seconds=60))
        return (now or self.clock()) - self.last_tick_at <= limit

    # --- execution ------------------------------------------------------------------------
    def run_job(self, spec: JobSpec) -> Outcome:
        try:
            with self.locks.hold(spec.name) as got:
                if not got:
                    log.info("job %s: another run holds the lock; not starting", spec.name)
                    return "locked"
                return self._run_locked(spec)
        except Exception as e:  # lock or state store unreachable
            log.warning("job %s could not start: %s", spec.name, type(e).__name__)
            return "error"

    def _run_locked(self, spec: JobSpec) -> Outcome:
        now = self.clock()
        if n := self.store.close_orphans(spec.run_job, now):
            log.warning("job %s: marked %d abandoned run(s) as failed", spec.name, n)
        st = self.store.stats([spec.run_job], now)[spec.run_job]
        if next_due(spec, st, now) > now:
            return "not_due"  # another scheduler finished it while we waited
        plan = self.planner.plan(spec, now)
        config: dict[str, object] = {
            "kind": spec.kind,
            "attempt": st.consecutive_failures + 1,
            "timeout_s": int(spec.timeout.total_seconds()),
            **plan.config,
        }
        if plan.skip:
            config["skipped"] = plan.skip
        rec = RunRecorder(
            spec.run_job,
            config,
            sink=self.store.record,
            clock=self.clock,
            code_commit=self.code_commit,
            detect_commit=False,
        )
        try:
            with rec as run:
                if plan.skip:
                    run.incr("skipped")
                    return "skipped"
                failures: list[str] = []
                for i, argv in enumerate(plan.commands):
                    res = self.runner(argv, spec.timeout)
                    run.incr("commands")
                    child = res.child_run_id()
                    if res.ok:
                        log.info("job %s command %d ok (child run %s)", spec.name, i, child)
                        continue
                    run.incr("commands_failed")
                    if res.timed_out:
                        run.incr("timeouts")
                    failures.append(res.summary())
                    log.warning("job %s command %d failed: %s", spec.name, i, res.summary())
                if failures:
                    raise JobFailed(f"{len(failures)}/{len(plan.commands)} failed: {failures[0]}")
        except Exception as e:
            if not isinstance(e, JobFailed):
                log.warning("job %s failed: %s", spec.name, type(e).__name__)
            return "failed"
        return "succeeded"

    def run_pending(self, executor: ThreadPoolExecutor | None = None) -> dict[str, Outcome]:
        """One tick: start every due job (inline without an executor) and maybe evaluate alerts.

        Inline mode returns the outcomes; with an executor the jobs run in the background and
        the returned dict is empty.
        """
        now = self.clock()
        self.last_tick_at = now
        try:
            due = self.due_jobs(now)
            self.last_loop_error = None
        except Exception as e:
            self.last_loop_error = type(e).__name__
            log.warning("scheduler tick: state unavailable: %s", type(e).__name__)
            due = []
        out: dict[str, Outcome] = {}
        for spec in due:
            with self._mu:
                if spec.name in self._running:
                    continue
                self._running.add(spec.name)
            if executor is None:
                try:
                    out[spec.name] = self.run_job(spec)
                finally:
                    self._release(spec.name)
            else:
                fut: Future[Outcome] = executor.submit(self.run_job, spec)
                fut.add_done_callback(self._releaser(spec.name))
        self._maybe_alert(now, background=executor is not None)
        return out

    def _releaser(self, name: str) -> Callable[[Future[Outcome]], None]:
        return lambda _f: self._release(name)

    def _release(self, name: str) -> None:
        with self._mu:
            self._running.discard(name)

    def _maybe_alert(self, now: datetime, background: bool = False) -> None:
        if self.on_alert_tick is None:
            return
        if self._last_alert_at is not None and now - self._last_alert_at < self.cfg.alert_every:
            return
        with self._mu:
            if "__alerts__" in self._running:
                return
            self._running.add("__alerts__")
        self._last_alert_at = now
        if background:  # probes (S3, SMTP) must not stall the tick loop
            threading.Thread(target=self._alert_once, name="alerts", daemon=True).start()
        else:
            self._alert_once()

    def _alert_once(self) -> None:
        assert self.on_alert_tick is not None
        try:
            self.on_alert_tick()
        except Exception as e:  # alerting must never stop the scheduler
            log.warning("alert evaluation failed: %s", type(e).__name__)
        finally:
            self._release("__alerts__")

    def serve(self, stop: threading.Event) -> None:
        """Tick until `stop` is set; running jobs are awaited on shutdown."""
        workers = max(2, len(self.cfg.enabled_jobs))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job") as pool:
            log.info(
                "scheduler started: %d jobs (%s)",
                len(self.cfg.enabled_jobs),
                ", ".join(j.name for j in self.cfg.enabled_jobs),
            )
            while not stop.is_set():
                self.run_pending(pool)
                stop.wait(self.cfg.tick.total_seconds())
            log.info("scheduler stopping; waiting for running jobs")
