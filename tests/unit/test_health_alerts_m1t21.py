"""M1-T21 / M1-T11: health report, alert rules, alert sink (dedupe/throttle), e-mail, export,
and the `/healthz` endpoint."""

from __future__ import annotations

import json
import socketserver
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.scheduler.alerts import (
    Alert,
    AlertManager,
    EmailNotifier,
    evaluate,
    export_summary,
    parse_smtp_url,
)
from pigtail.scheduler.config import AlertConfig, JobSpec, ScheduleConfig
from pigtail.scheduler.health import (
    HealthCheck,
    HealthReport,
    Probes,
    build_report,
    disk_check,
    job_health,
    render_text,
)
from pigtail.scheduler.server import HealthServer
from pigtail.scheduler.state import JobStats

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
ACFG = AlertConfig()


def jspec(name: str, every: timedelta, enabled: bool = True) -> JobSpec:
    return JobSpec(name, "command", every, timedelta(minutes=1), enabled, ("x",))


def probes(
    stats: dict[str, JobStats] | None = None,
    *,
    db: str = "ok",
    s3: str = "ok",
    disk: float = 40.0,
    sla: str = "ok",
    doctor: Sequence[HealthCheck] = (),
) -> Probes:
    def st(jobs: Sequence[str], now: datetime) -> dict[str, JobStats]:
        if db == "fail":
            raise ConnectionError("down")
        return {j: (stats or {}).get(j, JobStats(j)) for j in jobs}

    return Probes(
        stats=st,
        database=lambda: HealthCheck("database", db, "x"),  # type: ignore[arg-type]
        object_store=lambda: HealthCheck("object_store", s3, "x"),  # type: ignore[arg-type]
        disk=lambda: HealthCheck("disk", "fail" if disk > 80 else "ok", "x", disk),
        doctor=lambda: list(doctor),
        deletion_sla=lambda now: HealthCheck("deletion_sla", sla, "2 late", 2.0),  # type: ignore[arg-type]
    )


CFG = ScheduleConfig(
    jobs=(
        jspec("hn_ranks", timedelta(minutes=5)),
        jspec("gharchive_scan", timedelta(hours=1)),
        jspec("off", timedelta(hours=1), enabled=False),
    )
)


# --- job health ---------------------------------------------------------------------------------
def test_m1t21_job_health_lag_and_stale() -> None:
    s = jspec("hn_ranks", timedelta(minutes=5))
    fresh = JobStats("scheduler.hn_ranks", last_success_at=NOW - timedelta(minutes=7))
    h = job_health(s, fresh, NOW, ACFG)
    assert h.status == "ok" and not h.stale and h.lag_seconds == 120.0
    old = JobStats("scheduler.hn_ranks", last_success_at=NOW - timedelta(minutes=16))
    h = job_health(s, old, NOW, ACFG)
    assert h.stale and h.status == "fail"  # > 3 x 5 min
    never = job_health(s, JobStats("scheduler.hn_ranks"), NOW, ACFG)
    assert never.status == "never_run" and not never.stale
    started = job_health(s, JobStats("j"), NOW, ACFG, scheduler_started_at=NOW - timedelta(hours=1))
    assert started.stale  # never succeeded in an hour of scheduler uptime
    failing = JobStats(
        "j",
        last_success_at=NOW - timedelta(minutes=1),
        consecutive_failures=1,
        last_status="failed",
    )
    assert job_health(s, failing, NOW, ACFG).status == "warn"
    assert job_health(jspec("off", timedelta(hours=1), False), JobStats("j"), NOW, ACFG).status == (
        "disabled"
    )


def test_m1t21_report_overall_status_and_text() -> None:
    ok = {
        "scheduler.hn_ranks": JobStats("x", last_success_at=NOW),
        "scheduler.gharchive_scan": JobStats("x", last_success_at=NOW),
    }
    r = build_report(CFG, probes(ok), NOW)
    assert r.status == "ok"
    d = r.to_dict()
    assert {j["job"] for j in d["jobs"]} == {"hn_ranks", "gharchive_scan", "off"}
    assert {c["name"] for c in d["checks"]} == {"database", "object_store", "disk", "deletion_sla"}
    assert "hn_ranks" in render_text(r)
    assert build_report(CFG, probes(ok, disk=91), NOW).status == "fail"
    doc = [HealthCheck("doctor.pseudonym_key", "warn", "short")]
    assert build_report(CFG, probes(ok, doctor=doc), NOW).status == "warn"


def test_m1t21_report_survives_db_down() -> None:
    r = build_report(CFG, probes(db="fail"), NOW)
    assert r.status == "fail"
    assert r.check("job_state") is not None


def test_m1t21_disk_check(tmp_path: Path) -> None:
    c = disk_check(tmp_path / "does" / "not" / "exist", 80)
    assert c.name == "disk" and c.value is not None and 0 <= c.value <= 100
    assert disk_check(tmp_path, -1).status == "fail"


# --- alert rules --------------------------------------------------------------------------------
def rules(r: HealthReport) -> set[tuple[str, str]]:
    return {(a.rule, a.subject) for a in evaluate(r, ACFG)}


def test_m1t21_alert_rules_each_fire() -> None:
    stats = {
        "scheduler.hn_ranks": JobStats(
            "x",
            last_success_at=NOW - timedelta(hours=1),
            consecutive_failures=3,
            last_status="failed",
        ),
        "scheduler.gharchive_scan": JobStats("x", last_success_at=NOW),
    }
    doc = [
        HealthCheck("doctor.snapshot_bucket_encryption", "warn", "no SSE"),
        HealthCheck("doctor.postgres_volume_encryption", "ok", "MANUAL: …"),
    ]
    r = build_report(CFG, probes(stats, s3="fail", disk=85, sla="fail", doctor=doc), NOW)
    got = rules(r)
    assert got == {
        ("job_stale", "hn_ranks"),
        ("job_failing", "hn_ranks"),
        ("s3_down", "object_store"),
        ("disk_high", "data_dir"),
        ("deletion_sla", "deletion_sync"),
        ("doctor", "doctor.snapshot_bucket_encryption"),
    }
    assert ("db_down", "database") in rules(build_report(CFG, probes(db="fail"), NOW))


def test_m1t21_no_alerts_when_healthy() -> None:
    ok = {
        "scheduler.hn_ranks": JobStats("x", last_success_at=NOW),
        "scheduler.gharchive_scan": JobStats("x", last_success_at=NOW),
    }
    assert evaluate(build_report(CFG, probes(ok), NOW), ACFG) == []


# --- sink: dedupe, throttle, resolve, no personal data -------------------------------------
def test_m1t21_alert_manager_dedupes_throttles_and_resolves(tmp_path: Path) -> None:
    sent: list[list[str]] = []
    mgr = AlertManager(
        tmp_path / "alerts", timedelta(hours=6), [lambda ev: sent.append([e.kind for e in ev])]
    )
    a = Alert("job_failing", "hn_ranks", "critical", "job hn_ranks: 3 consecutive failures")
    assert [e.kind for e in mgr.process([a], NOW)] == ["firing"]
    assert mgr.process([a], NOW + timedelta(minutes=5)) == []  # deduped
    assert mgr.process([a], NOW + timedelta(hours=5, minutes=59)) == []  # throttled
    assert [e.kind for e in mgr.process([a], NOW + timedelta(hours=6))] == ["repeat"]
    assert [e.kind for e in mgr.process([], NOW + timedelta(hours=7))] == ["resolved"]
    assert mgr.process([], NOW + timedelta(hours=8)) == []
    assert sent == [["firing"], ["repeat"], ["resolved"]]
    md = (tmp_path / "alerts" / "ALERTS.md").read_text()
    assert md.startswith("# pigtail alerts (host-local)")
    assert md.count("FIRING") == 1 and md.count("REPEAT") == 1 and md.count("RESOLVED") == 1
    assert len((tmp_path / "alerts" / "alerts.jsonl").read_text().splitlines()) == 3
    assert oct((tmp_path / "alerts" / "ALERTS.md").stat().st_mode)[-3:] == "600"


def test_m1t21_alert_sink_scrubs_personal_data(tmp_path: Path) -> None:
    """The sink never stores e-mails or handles, even if a message were to carry one (CB-18)."""
    mgr = AlertManager(tmp_path)
    a = Alert("job_failing", "x", "warning", "failed for someone@example.org and @octocat")
    mgr.process([a], NOW)
    for f in ("ALERTS.md", "alerts.jsonl"):
        text = (tmp_path / f).read_text()
        assert "someone@example.org" not in text and "@octocat" not in text


def test_m1t21_notifier_failure_does_not_break_the_sink(tmp_path: Path) -> None:
    def broken(_: Any) -> None:
        raise OSError("smtp down")

    mgr = AlertManager(tmp_path, notifiers=[broken])
    assert len(mgr.process([Alert("db_down", "database", "critical", "down")], NOW)) == 1
    assert "FIRING" in (tmp_path / "ALERTS.md").read_text()


def test_m1t21_export_is_sanitized(tmp_path: Path) -> None:
    mgr = AlertManager(tmp_path)
    mgr.process([Alert("job_stale", "hn_ranks", "critical", "secret detail text")], NOW)
    mgr.process([Alert("doctor", "Weird Subject!", "warning", "x")], NOW + timedelta(hours=1))
    text = export_summary(tmp_path, NOW + timedelta(hours=2), timedelta(days=7))
    assert "| job_stale | hn_ranks | critical |" in text
    assert "| doctor | redacted | warning |" in text  # unexpected subject text never exported
    assert "secret detail text" not in text  # messages are never exported
    assert "firing" in text
    empty = export_summary(tmp_path / "none", NOW)
    assert "No alerts" in empty


def test_m1t21_cli_alerts_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pigtail.cli import main

    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    AlertManager(tmp_path / "alerts").process([Alert("db_down", "database", "critical", "d")], NOW)
    out = tmp_path / "ALERTS.md"
    assert main(["alerts", "export", "--to", str(out), "--since", "3650d"]) == 0
    assert "| db_down | database | critical | firing |" in out.read_text()


# --- e-mail -------------------------------------------------------------------------------------
class _SMTPHandler(socketserver.StreamRequestHandler):
    """Minimal SMTP server stub: EHLO/HELO, MAIL, RCPT, DATA, QUIT (no TLS, no AUTH)."""

    received: list[dict[str, Any]]

    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        msg: dict[str, Any] = {"rcpt": []}
        self._send("220 fake ESMTP")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            cmd = raw.decode().strip()
            up = cmd.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                self._send("250-fake\r\n250 8BITMIME")
            elif up.startswith("MAIL FROM"):
                msg["from"] = cmd.split(":", 1)[1].strip()
                self._send("250 ok")
            elif up.startswith("RCPT TO"):
                msg["rcpt"].append(cmd.split(":", 1)[1].strip())
                self._send("250 ok")
            elif up == "DATA":
                self._send("354 go")
                lines = []
                while (line := self.rfile.readline().decode()) not in (".\r\n", ""):
                    lines.append(line)
                msg["data"] = "".join(lines)
                self.received.append(msg)
                msg = {"rcpt": []}
                self._send("250 queued")
            elif up == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("250 ok")


@pytest.fixture
def smtp_server() -> Iterator[tuple[int, list[dict[str, Any]]]]:
    received: list[dict[str, Any]] = []
    handler = type("H", (_SMTPHandler,), {"received": received})
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1], received
    finally:
        srv.shutdown()
        srv.server_close()


def test_m1t21_email_via_fake_smtp(
    smtp_server: tuple[int, list[dict[str, Any]]], tmp_path: Path
) -> None:
    port, received = smtp_server
    env = {"SMTP_URL": f"smtp://127.0.0.1:{port}", "ALERT_EMAIL": "ops@example.com"}
    notifier = EmailNotifier.from_env(env)
    assert notifier is not None
    mgr = AlertManager(tmp_path, notifiers=[notifier])
    mgr.process([Alert("job_stale", "hn_ranks", "critical", "job hn_ranks: stale")], NOW)
    assert len(received) == 1
    m = received[0]
    assert m["rcpt"] == ["<ops@example.com>"]
    assert "Subject: [pigtail] 1 alert(s) firing, 0 resolved" in m["data"]
    assert "job_stale `hn_ranks`" in m["data"]


def test_m1t21_email_not_configured_and_url_parsing() -> None:
    assert EmailNotifier.from_env({}) is None
    assert EmailNotifier.from_env({"SMTP_URL": "smtp://h"}) is None
    t = parse_smtp_url("smtps://u%40x:p%3Aw@smtp.test.example")
    assert (t.host, t.port, t.user, t.password, t.mode) == (
        "smtp.test.example",
        465,
        "u@x",
        "p:w",
        "ssl",
    )
    assert parse_smtp_url("smtp+starttls://mail.example.com").port == 587
    with pytest.raises(ValueError):
        parse_smtp_url("http://mail.example.com")


def test_m1t21_email_refuses_plaintext_login_to_remote_host(
    smtp_server: tuple[int, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pigtail.scheduler import alerts
    from pigtail.scheduler.alerts import AlertEvent

    port, received = smtp_server
    monkeypatch.setattr(alerts, "LOCAL_HOSTS", ())  # treat the stub as a remote host
    n = EmailNotifier(f"smtp://user:pw@127.0.0.1:{port}", ["ops@example.com"])
    with pytest.raises(RuntimeError, match="without TLS"):
        n([AlertEvent("firing", NOW, "db_down", "database", "critical", "down")])
    assert received == []


# --- /healthz ------------------------------------------------------------------------------------
def _get(url: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_m1t21_healthz_endpoint() -> None:
    state = {"alive": True, "db": "ok", "calls": 0}

    def report() -> dict[str, Any]:
        state["calls"] = int(state["calls"]) + 1
        return {"status": "warn", "checks": [{"name": "database", "status": state["db"]}]}

    srv = HealthServer(report, lambda: bool(state["alive"]), port=0, cache_seconds=0)
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        code, body = _get(base + "/healthz")
        assert code == 200 and body["status"] == "warn" and body["alive"] is True
        assert _get(base + "/livez") == (200, {"alive": True})
        state["db"] = "fail"
        assert _get(base + "/healthz")[0] == 503
        state["db"], state["alive"] = "ok", False
        assert _get(base + "/healthz")[0] == 503
        assert _get(base + "/livez")[0] == 503
        assert _get(base + "/nope")[0] == 404
    finally:
        srv.stop()


def test_m1t21_healthz_caches_report() -> None:
    calls: list[int] = []
    srv = HealthServer(lambda: calls.append(1) or {"status": "ok"}, lambda: True, port=0)
    srv.healthz()
    srv.healthz()
    assert len(calls) == 1
