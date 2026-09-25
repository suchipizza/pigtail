"""CB-30: the health probe counts failed UI logins and snapshot hash mismatches from
`ui_audit_log` (ADR-034) and the alert rules fire on them. Synthetic audit rows only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pigtail.scheduler.config import AlertConfig
from pigtail.scheduler.health import ui_audit_checks

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def audit(db: Any, event: str, at: datetime, n: int = 1) -> None:
    for _ in range(n):
        db.conn.execute(
            "INSERT INTO ui_audit_log (at, event, route, client) VALUES (%s, %s, '/x', %s)",
            (at, event, "0" * 16),
        )


def test_cb30_probe_counts_failed_logins_and_hash_mismatches(capture_db, pg_url):
    cfg = AlertConfig(login_failures=5, login_window=timedelta(hours=1))
    probe = ui_audit_checks(pg_url, cfg)
    checks = {c.name: c for c in probe(NOW)}
    assert checks["ui_login_failures"].status == "ok"
    assert checks["snapshot_integrity"].status == "ok"
    audit(capture_db, "login_failure", NOW - timedelta(minutes=10), 3)
    audit(capture_db, "login_rate_limited", NOW - timedelta(minutes=5), 2)
    audit(capture_db, "login_failure", NOW - timedelta(hours=3), 10)  # outside the window
    audit(capture_db, "login_success", NOW - timedelta(minutes=1))
    checks = {c.name: c for c in probe(NOW)}
    c = checks["ui_login_failures"]
    assert c.status == "warn" and c.value == 5.0 and "2 rate-limited" in c.detail
    audit(capture_db, "snapshot_integrity_failure", NOW - timedelta(hours=2))
    checks = {c.name: c for c in probe(NOW)}
    assert checks["snapshot_integrity"].status == "fail"
    assert checks["snapshot_integrity"].value == 1.0
    # details carry counts only: no client hash, route or evidence id
    for c in checks.values():
        assert "0" * 16 not in c.detail and "/x" not in c.detail
    assert probe(NOW + timedelta(days=2))[1].status == "ok"  # past the 24 h window


def test_cb30_probe_without_database_or_table(pg_url):
    assert ui_audit_checks(None, AlertConfig())(NOW) == []
    [c] = ui_audit_checks(pg_url, AlertConfig())(NOW)  # empty database, not migrated
    assert c.name == "ui_audit" and c.status == "warn"
