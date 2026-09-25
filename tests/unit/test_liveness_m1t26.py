"""M1-T26: external liveness check (heartbeat written each scheduler tick, checked elsewhere)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pigtail.cli import main
from pigtail.scheduler.config import ScheduleConfig
from pigtail.scheduler.core import Scheduler
from pigtail.scheduler.jobs import Planner
from pigtail.scheduler.liveness import (
    alerts_for,
    check,
    check_text,
    default_path,
    write_heartbeat,
)
from pigtail.scheduler.locks import MemoryJobLocks
from pigtail.scheduler.state import MemoryStateStore
from tests.unit.test_scheduler_m1t21 import FakeClock, FakeRunner, spec

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
INFRA = Path(__file__).resolve().parents[2] / "infra" / "systemd"


def test_m1_t26_heartbeat_fresh_stale_missing_invalid(tmp_path):
    p = tmp_path / "hb" / "liveness.json"
    assert check(str(p), T0).state == "missing"
    write_heartbeat(p, T0, started_at=T0 - timedelta(hours=1), ticks=240)
    body = json.loads(p.read_text())
    assert set(body) == {"at", "started_at", "ticks", "pid"}  # times and counts only
    assert check(str(p), T0 + timedelta(minutes=4)).state == "ok"
    stale = check(str(p), T0 + timedelta(minutes=6), max_age=timedelta(minutes=5))
    assert stale.state == "stale" and stale.age_seconds == 360
    assert check_text("{not json", T0, timedelta(minutes=5)).state == "invalid"
    assert check_text('{"at": "2026-09-25T10:00:00"}', T0, timedelta(minutes=5)).state == "invalid"
    assert check_text("", T0, timedelta(minutes=5)).state == "missing"
    (a,) = alerts_for(stale)
    assert (a.rule, a.subject, a.severity) == ("scheduler_dead", "liveness", "critical")
    assert alerts_for(check(str(p), T0)) == []


def test_m1_t26_default_path(tmp_path, monkeypatch):
    monkeypatch.delenv("PIGTAIL_LIVENESS_FILE", raising=False)
    assert default_path(tmp_path) == tmp_path / "liveness.json"
    monkeypatch.setenv("PIGTAIL_LIVENESS_FILE", str(tmp_path / "x.json"))
    assert default_path(tmp_path) == tmp_path / "x.json"


def test_m1_t26_scheduler_writes_a_heartbeat_every_tick(tmp_path):
    p = tmp_path / "liveness.json"
    clock = FakeClock(T0)
    s = Scheduler(
        ScheduleConfig(jobs=(spec(),)),
        store=MemoryStateStore(),
        locks=MemoryJobLocks(),
        planner=Planner({}),
        runner=FakeRunner(),
        clock=clock,
        code_commit="abc1234",
        heartbeat=lambda now, started, ticks: write_heartbeat(
            p, now, started_at=started, ticks=ticks
        ),
    )
    s.run_pending()
    assert json.loads(p.read_text())["ticks"] == 1
    clock.advance(seconds=15)
    s.run_pending()
    body = json.loads(p.read_text())
    assert body["ticks"] == 2 and body["at"] == (T0 + timedelta(seconds=15)).isoformat()


def test_m1_t26_heartbeat_failure_does_not_stop_the_loop():
    def boom(*_a: object) -> None:
        raise OSError("disk full")

    s = Scheduler(
        ScheduleConfig(jobs=(spec(),)),
        store=MemoryStateStore(),
        locks=MemoryJobLocks(),
        planner=Planner({}),
        runner=FakeRunner(),
        clock=FakeClock(T0),
        code_commit="abc1234",
        heartbeat=boom,
    )
    assert s.run_pending() == {"hn_ranks": "succeeded"}


def test_m1_t26_cli_check_alerts_and_resolves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SMTP_URL", raising=False)
    hb = tmp_path / "liveness.json"
    write_heartbeat(hb, T0, started_at=T0, ticks=1)  # long ago: stale now
    argv = ["health", "--liveness-file", str(hb), "--max-age", "5m", "--alert", "--json"]
    assert main(argv) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "stale" and out["alert_events"] == ["firing"]
    log = (tmp_path / "data" / "alerts" / "liveness" / "alerts.jsonl").read_text()
    assert "scheduler_dead" in log
    assert (
        main(argv) == 1 and json.loads(capsys.readouterr().out)["alert_events"] == []
    )  # throttled
    write_heartbeat(hb, datetime.now(UTC), started_at=T0, ticks=2)
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["alert_events"] == ["resolved"]
    # the scheduler's own alert state is untouched
    assert not (tmp_path / "data" / "alerts" / "state.json").exists()


def test_m1_t26_cli_reads_heartbeat_from_stdin_for_a_second_host(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    beat = json.dumps({"at": datetime.now(UTC).isoformat()})
    monkeypatch.setattr("sys.stdin", io.StringIO(beat))
    assert main(["health", "--liveness-file", "-"]) == 0
    assert "[OK] scheduler liveness" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # e.g. ssh failed: host down
    assert main(["health", "--liveness-file", "-"]) == 1


def test_m1_t26_systemd_timer_and_service():
    svc = (INFRA / "pigtail-liveness.service").read_text()
    timer = (INFRA / "pigtail-liveness.timer").read_text()
    assert "Type=oneshot" in svc and "health --liveness-file" in svc and "--alert" in svc
    assert "OnUnitActiveSec=5min" in timer and "Unit=pigtail-liveness.service" in timer
