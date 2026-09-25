"""CB-28 (LLM cache clear), CB-30 (alert rules on the UI audit log), CB-31 (rotation and
retention of the host alert files) and CB-17b (doctor backup checks, optional backup jobs).
Synthetic data only."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.cli import main
from pigtail.llm.store import LLMStore, UsageRow
from pigtail.privacy.doctor import backup_checks, run_checks
from pigtail.scheduler.alerts import Alert, AlertManager, evaluate, export_summary
from pigtail.scheduler.config import ScheduleError, load, parse
from pigtail.scheduler.health import HealthCheck, Probes, build_report
from pigtail.scheduler.state import JobStats

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


# --- CB-28: LLM cache clear ---------------------------------------------------------------------
def _store(path: Path | str = ":memory:") -> LLMStore:
    clock = [NOW - timedelta(days=40)]
    s = LLMStore(path, clock=lambda: clock[0])
    s.cache_put("old", {"q": "a"}, "m", evidence_id="ev_old")
    clock[0] = NOW - timedelta(days=5)
    s.cache_put("new", {"q": "b"}, "m", evidence_id="ev_new")
    s.record(UsageRow("subscription", "job", "m", "p", "1", "ok"))
    clock[0] = NOW
    return s


def test_cb28_clear_older_than_keeps_newer_rows_and_the_ledger():
    s = _store()
    assert s.clear(older_than=timedelta(days=30), dry_run=True) == 1
    assert s.cache_count() == 2
    assert s.clear(older_than=timedelta(days=30)) == 1
    assert s.cache_get("old") is None and s.cache_get("new") is not None
    assert s.cache_evidence("old") == [] and s.cache_evidence("new") == ["ev_new"]
    assert s.summary()["subscription"]["sessions"] == 1  # usage ledger kept
    with pytest.raises(ValueError):
        s.clear(older_than=timedelta(days=-1))


def test_cb28_clear_all():
    s = _store()
    assert s.clear() == 2
    assert s.cache_count() == 0 and s.cache_evidence("new") == []
    assert s.summary()["subscription"]["sessions"] == 1


def test_cb28_cli_llm_cache_clear(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    _store(tmp_path / "llm.sqlite3")
    assert main(["llm", "cache", "clear"]) == 2  # one of --older-than / --all
    assert main(["llm", "cache", "clear", "--all", "--older-than", "3"]) == 2
    assert main(["llm", "cache", "clear", "--all"]) == 2  # --all needs --yes
    assert "--yes" in capsys.readouterr().err
    assert main(["llm", "cache", "clear", "--older-than", "-1"]) == 2
    assert main(["llm", "cache", "clear", "--older-than", "30"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["cache_rows_deleted"] == 1 and out["cache_rows_left"] == 1
    assert main(["llm", "cache", "clear", "--all", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["cache_rows_matching"] == 1
    assert main(["llm", "cache", "clear", "--all", "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["cache_rows_left"] == 0


# --- CB-30: alert rules on the UI audit log ----------------------------------------------------
def _probes(ui: Sequence[HealthCheck]) -> Probes:
    def stats(jobs: Sequence[str], now: datetime) -> dict[str, JobStats]:
        return {j: JobStats(j) for j in jobs}

    return Probes(
        stats=stats,
        database=lambda: HealthCheck("database", "ok", "x"),
        object_store=lambda: HealthCheck("object_store", "ok", "x"),
        disk=lambda: HealthCheck("disk", "ok", "x", 10.0),
        doctor=lambda: [],
        deletion_sla=lambda now: HealthCheck("deletion_sla", "ok", "x"),
        ui_audit=lambda now: list(ui),
    )


CFG = parse({"jobs": {"a": {"command": ["x"], "every": "1h"}}})


def test_cb30_login_failures_and_integrity_raise_alerts():
    quiet = [
        HealthCheck("ui_login_failures", "ok", "2 failed", 2.0),
        HealthCheck("snapshot_integrity", "ok", "0", 0.0),
    ]
    assert evaluate(build_report(CFG, _probes(quiet), NOW), CFG.alerts) == []
    noisy = [
        HealthCheck("ui_login_failures", "warn", "12 failed UI login attempt(s)", 12.0),
        HealthCheck("snapshot_integrity", "fail", "1 snapshot hash mismatch(es)", 1.0),
    ]
    r = build_report(CFG, _probes(noisy), NOW)
    assert r.status == "fail"
    got = {(a.rule, a.subject, a.severity) for a in evaluate(r, CFG.alerts)}
    assert got == {
        ("login_failures", "ui", "warning"),
        ("snapshot_integrity", "snapshots", "critical"),
    }


def test_cb30_default_probes_without_ui_audit_are_unchanged():
    p = _probes([])  # built without `ui_audit`: the default (no checks) applies
    p2 = Probes(p.stats, p.database, p.object_store, p.disk, p.doctor, p.deletion_sla)
    assert [c.name for c in build_report(CFG, p2, NOW).checks] == [
        "database",
        "object_store",
        "disk",
        "deletion_sla",
    ]


def test_cb30_cb31_alert_config_parsing_and_bounds():
    cfg = parse(
        {
            "jobs": {"a": {"command": ["x"]}},
            "alerts": {"login_failures": 3, "login_window": "30m", "file_rotate_after": "7d"},
        }
    ).alerts
    assert cfg.login_failures == 3 and cfg.login_window == timedelta(minutes=30)
    assert cfg.file_rotate_after == timedelta(days=7)
    for bad in ({"login_failures": 0}, {"file_max_bytes": 10}, {"file_rotate_after": "1h"}):
        with pytest.raises(ScheduleError):
            parse({"jobs": {"a": {"command": ["x"]}}, "alerts": bad})
    shipped = load()  # infra/schedule.toml
    assert shipped.alerts.login_failures == 10
    assert shipped.alerts.integrity_window == timedelta(hours=24)


# --- CB-31: rotation and retention of the host alert files ------------------------------------
def _fire(mgr: AlertManager, at: datetime, subject: str = "s") -> None:
    mgr.process([Alert("job_failing", subject, "critical", "job x: 3 failures")], at)
    mgr.process([], at + timedelta(minutes=1))  # resolved


def test_cb31_rotates_by_age_and_prunes_archives_past_retention(tmp_path):
    d = tmp_path / "alerts"
    mgr = AlertManager(d, rotate_after=timedelta(days=30), retention=timedelta(days=365))
    t0 = NOW - timedelta(days=400)
    _fire(mgr, t0)
    _fire(mgr, t0 + timedelta(days=31))  # first event now > 30 d old: rotated, then written
    archives = sorted(p.name for p in d.iterdir() if p.name not in ("state.json",))
    stamp = t0.strftime("%Y%m%dT%H%M%SZ")
    assert f"ALERTS.{stamp}.md" in archives and f"alerts.{stamp}.jsonl" in archives
    assert (d / "alerts.jsonl").exists() and (d / "ALERTS.md").exists()
    for p in d.iterdir():
        assert (p.stat().st_mode & 0o777) == 0o600 or p.is_dir()
    # a year later the first archive (first event > 365 d ago) is deleted on the next process
    mgr.process([], NOW)
    names = {p.name for p in d.iterdir()}
    assert f"alerts.{stamp}.jsonl" not in names and f"ALERTS.{stamp}.md" not in names
    # every remaining line is within the retention
    for p in d.glob("alerts*.jsonl"):
        for line in p.read_text().splitlines():
            assert NOW - datetime.fromisoformat(json.loads(line)["at"]) <= timedelta(days=365)


def test_cb31_rotates_by_size(tmp_path):
    d = tmp_path / "alerts"
    mgr = AlertManager(d, max_bytes=1024)
    for i in range(20):
        _fire(mgr, NOW + timedelta(hours=i), subject=f"s{i}")
    assert len([p for p in d.iterdir() if p.name.startswith("alerts.2")]) >= 2
    assert (d / "alerts.jsonl").stat().st_size < 2048
    # export reads the archives too: every notification is counted once
    text = export_summary(d, NOW + timedelta(days=1))
    assert text.count("| job_failing |") == 20


def test_cb31_retention_is_capped_at_12_months(tmp_path):
    with pytest.raises(ValueError, match="365"):
        AlertManager(tmp_path, retention=timedelta(days=366))
    mgr = AlertManager(tmp_path, rotate_after=timedelta(days=90), retention=timedelta(days=10))
    assert mgr.rotate_after == timedelta(days=10)  # never keeps a live file past the retention


def test_cb31_scheduler_alert_manager_uses_log_retention(tmp_path, monkeypatch):
    from pigtail.config import Settings
    from pigtail.scheduler.cli import _alert_manager

    s = Settings.from_env({"PIGTAIL_DATA_DIR": str(tmp_path), "LOG_RETENTION_DAYS": "90"})
    monkeypatch.delenv("SMTP_URL", raising=False)
    mgr = _alert_manager(s, CFG)
    assert mgr.retention == timedelta(days=90)
    assert mgr.max_bytes == CFG.alerts.file_max_bytes


# --- CB-17b: backup checks in doctor, optional backup jobs ------------------------------------
def _touch(d: Path, at: datetime, ext: str = "age") -> None:
    (d / f"pigtail-backup-{at:%Y%m%dT%H%M%SZ}.{ext}").write_bytes(b"x")


def _by_name(checks: list[Any]) -> dict[str, Any]:
    return {c.name: c for c in checks}


def test_cb17b_backup_recipient_and_dir_unset_warn():
    c = _by_name(backup_checks({}, NOW))
    assert c["backup_recipient"].status == "warn"
    assert c["backup_age"].status == "warn" and "BACKUP_DIR" in c["backup_age"].detail
    c = _by_name(backup_checks({"BACKUP_RECIPIENT": "age1synthetic"}, NOW))
    assert c["backup_recipient"].status == "ok"
    assert "age1synthetic" not in c["backup_recipient"].detail  # value not shown


@pytest.mark.parametrize(
    ("age", "status"),
    [(timedelta(hours=5), "ok"), (timedelta(days=3), "warn"), (timedelta(days=8), "fail")],
)
def test_cb17b_backup_age_thresholds(tmp_path, age, status):
    _touch(tmp_path, NOW - timedelta(days=30))
    _touch(tmp_path, NOW - age)  # the newest one counts
    (tmp_path / f"pigtail-backup-{NOW:%Y%m%dT%H%M%SZ}.age.partial").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not a backup")
    c = _by_name(backup_checks({"BACKUP_DIR": str(tmp_path)}, NOW))["backup_age"]
    assert c.status == status


def test_cb17b_no_backup_or_missing_dir_fails(tmp_path):
    assert _by_name(backup_checks({"BACKUP_DIR": str(tmp_path)}, NOW))["backup_age"].status == (
        "fail"
    )
    missing = str(tmp_path / "nope")
    assert _by_name(backup_checks({"BACKUP_DIR": missing}, NOW))["backup_age"].status == "fail"


def test_cb17b_doctor_includes_backup_checks(tmp_path):
    from pigtail.config import Settings

    _touch(tmp_path, datetime.now(UTC) - timedelta(hours=1))
    env = {"BACKUP_DIR": str(tmp_path), "BACKUP_RECIPIENT": "age1synthetic"}
    names = _by_name(run_checks(Settings(pseudonym_key="k" * 32), db_check=False, env=env))
    assert names["backup_age"].status == "ok" and names["backup_recipient"].status == "ok"


def test_cb17b_backup_jobs_ship_disabled_and_default_to_backup_dir(tmp_path, monkeypatch, capsys):
    cfg = load()
    create, prune = cfg.job("backup_create"), cfg.job("backup_prune")
    assert not create.enabled and not prune.enabled
    assert create.command == ("backup", "create") and prune.command == ("backup", "prune")
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    assert main(["backup", "prune"]) == 2
    assert "BACKUP_DIR" in capsys.readouterr().err
    old = tmp_path / f"pigtail-backup-{datetime.now(UTC) - timedelta(days=40):%Y%m%dT%H%M%SZ}.age"
    old.write_bytes(b"x")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    assert main(["backup", "prune"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] == [old.name]
    assert not old.exists()
    monkeypatch.delenv("BACKUP_DIR")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost:1/x")
    assert main(["backup", "create"]) == 2  # no --out and no BACKUP_DIR
    assert "BACKUP_DIR" in capsys.readouterr().err
    assert os.environ.get("BACKUP_DIR") is None
