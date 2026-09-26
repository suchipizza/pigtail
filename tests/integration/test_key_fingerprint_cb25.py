"""CB-25 (ADR-043): the database remembers its pseudonym key's fingerprint and every path that
pseudonymizes or matches opt-outs refuses to run under a different key. Also CB-33 (UI audit
purge in `retention purge`) and CB-34 (unparseable snapshots in subject scans). Synthetic data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.cli import _restore_key_precheck, main
from pigtail.config import Settings
from pigtail.connectors.base import TokenBucket
from pigtail.connectors.gharchive import GHArchiveConnector, hour_url
from pigtail.privacy import key_fingerprint, requests, suppression
from pigtail.privacy.backup import CarryOver, write_carry_over
from pigtail.privacy.doctor import run_checks
from pigtail.privacy.key_fingerprint import KeyFingerprintMismatch
from pigtail.privacy.retention import purge
from pigtail.privacy.suppression import subject_pseudonym
from pigtail.pseudonymize import Pseudonymizer
from pigtail.scheduler.cli import _key_check
from tests.conftest import TEST_KEY
from tests.integration.test_privacy_ops import Archive, put_ev, state

pytestmark = pytest.mark.db

OTHER_KEY = "other-test-key-not-secret-987654321"
NOW = datetime(2026, 9, 25, tzinfo=UTC)
YOUNG = NOW - timedelta(days=100)


def fp_rows(db: Any) -> list[tuple[Any, ...]]:
    return db.conn.execute("SELECT fingerprint, set_by FROM pseudonym_key_fingerprint").fetchall()


def fp_log(db: Any) -> list[tuple[Any, ...]]:
    return db.conn.execute(
        "SELECT event, old_fingerprint, new_fingerprint FROM pseudonym_key_fingerprint_log"
        " ORDER BY id"
    ).fetchall()


def cli_env(monkeypatch: Any, pg_url: str, tmp_path: Path, key: str | None) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    if key is None:
        monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    else:
        monkeypatch.setenv("PSEUDONYM_KEY", key)


# --- storage ------------------------------------------------------------------------------------
def test_cb25_migration_0012_single_row_and_append_only_log(capture_db, pz):
    db = capture_db
    db.conn.execute(
        "INSERT INTO pseudonym_key_fingerprint (fingerprint, set_by) VALUES (%s, 'first_use')",
        (pz.fingerprint(),),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.conn.execute(
            "INSERT INTO pseudonym_key_fingerprint (fingerprint, set_by) VALUES (%s, 'reset')",
            (Pseudonymizer(OTHER_KEY).fingerprint(),),
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # never a raw key
        db.conn.execute(
            "INSERT INTO pseudonym_key_fingerprint_log (event, new_fingerprint)"
            " VALUES ('recorded', %s)",
            (TEST_KEY,),
        )
    db.conn.execute(
        "INSERT INTO pseudonym_key_fingerprint_log (event, new_fingerprint)"
        " VALUES ('recorded', %s)",
        (pz.fingerprint(),),
    )
    for stmt in (
        "UPDATE pseudonym_key_fingerprint_log SET event = 'reset'",
        "DELETE FROM pseudonym_key_fingerprint_log",
        "TRUNCATE pseudonym_key_fingerprint_log",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            db.conn.execute(stmt)


def test_cb25_first_use_records_fingerprint_never_the_key(capture_db, pz):
    db = capture_db
    assert key_fingerprint.status(db.conn, pz) == "unset"
    sup = suppression.load(db, pz)
    assert sup.key_fingerprint == pz.fingerprint()
    assert fp_rows(db) == [(pz.fingerprint(), "first_use")]
    assert fp_log(db) == [("recorded", None, pz.fingerprint())]
    dump = db.conn.execute(
        "SELECT (SELECT json_agg(t)::text FROM pseudonym_key_fingerprint t) ||"
        " (SELECT json_agg(t)::text FROM pseudonym_key_fingerprint_log t)"
    ).fetchone()[0]
    assert TEST_KEY not in dump
    suppression.load(db, pz)  # same key: no new row, no new event
    assert len(fp_log(db)) == 1 and key_fingerprint.status(db.conn, pz) == "ok"


def test_cb25_read_only_connection_does_not_record(pg_url, capture_db, pz):
    conn = psycopg.connect(pg_url, autocommit=True, options="-c default_transaction_read_only=on")
    with conn:
        assert key_fingerprint.verify(conn, pz) == "unset"
    assert fp_rows(capture_db) == []


# --- refusal on mismatch ------------------------------------------------------------------------
def test_cb25_suppression_load_refuses_another_key(capture_db, pz, monkeypatch):
    db = capture_db
    suppression.load(db, pz)
    with pytest.raises(KeyFingerprintMismatch, match="CB-25"):
        suppression.load(db, Pseudonymizer(OTHER_KEY))
    monkeypatch.setenv("PSEUDONYM_KEY", OTHER_KEY)  # the key from the environment, too
    with pytest.raises(KeyFingerprintMismatch):
        suppression.load(db)


def test_cb25_connector_base_refuses_a_pseudonymizer_with_another_key(capture_db, pz, tmp_path):
    sup = suppression.load(capture_db, pz)
    store = LocalSnapshotStore(tmp_path / "s")
    GHArchiveConnector(store=store, pseudonymizer=pz, env={}, suppression=sup)  # same key: ok
    with pytest.raises(KeyFingerprintMismatch):
        GHArchiveConnector(
            store=store, pseudonymizer=Pseudonymizer(OTHER_KEY), env={}, suppression=sup
        )


def test_cb25_privacy_requests_refuse_another_key(capture_db, pz, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "s")
    suppression.load(db, pz)
    other = Pseudonymizer(OTHER_KEY)
    calls = [
        lambda: requests.erasure(db, store, other, platform="github", handle="user0001"),
        lambda: requests.access(
            db, store, other, platform="github", handle="user0001", out_dir=tmp_path
        ),
        lambda: requests.optout_repo_name(
            db, store, platform="github", full_name="org-a/repo-1", pz=other
        ),
        lambda: requests.optout_repo(db, store, platform="github", repo_key="github:1", pz=other),
        lambda: requests.reapply_refusals(db, store, other),
        lambda: requests.rekey_unkeyed_names(db, other),
    ]
    for call in calls:
        with pytest.raises(KeyFingerprintMismatch):
            call()
    # refused before anything was written: no request row, no refusal-list entry
    assert db.conn.execute("SELECT count(*) FROM privacy_requests").fetchone() == (0,)
    assert suppression.entries(db) == []


def test_cb25_scheduler_startup_and_restore_precheck_refuse(capture_db, pg_url, pz, capsys):
    suppression.load(capture_db, pz)
    ok = Settings.from_env({"DATABASE_URL": pg_url, "PSEUDONYM_KEY": TEST_KEY})
    bad = Settings.from_env({"DATABASE_URL": pg_url, "PSEUDONYM_KEY": OTHER_KEY})
    assert _key_check(ok) is None
    assert _key_check(bad) == 2
    assert "CB-25" in capsys.readouterr().err
    assert _restore_key_precheck(pg_url, pz) is None
    assert _restore_key_precheck(pg_url, Pseudonymizer(OTHER_KEY)) == 2


def test_cb25_scheduler_startup_records_on_first_use(capture_db, pg_url, pz):
    assert (
        _key_check(Settings.from_env({"DATABASE_URL": pg_url, "PSEUDONYM_KEY": TEST_KEY})) is None
    )
    assert fp_rows(capture_db) == [(pz.fingerprint(), "first_use")]


# --- doctor -------------------------------------------------------------------------------------
def test_cb25_doctor_reports_unset_ok_mismatch(capture_db, pg_url, pz):
    def check(key: str) -> tuple[str, str]:
        s = Settings.from_env({"DATABASE_URL": pg_url, "PSEUDONYM_KEY": key})
        c = {c.name: c for c in run_checks(s, env={})}["pseudonym_key_fingerprint"]
        assert key not in c.detail
        return c.status, c.detail

    assert check(TEST_KEY)[0] == "warn"
    assert fp_rows(capture_db) == []  # doctor never records
    suppression.load(capture_db, pz)
    assert check(TEST_KEY)[0] == "ok"
    status, detail = check(OTHER_KEY)
    assert status == "fail" and "--reset --confirm-rotation" in detail


# --- CLI, including the compromise-rotation reset ----------------------------------------------
def test_cb25_cli_refuses_then_reset_after_rotation(
    capture_db, pg_url, tmp_path, monkeypatch, capsys, pz
):
    cli_env(monkeypatch, pg_url, tmp_path, TEST_KEY)
    add = ["privacy", "optout", "add", "--platform", "github", "--handle", "user0002"]
    assert main([*add, "--no-purge"]) == 0
    capsys.readouterr()
    assert fp_rows(capture_db) == [(pz.fingerprint(), "first_use")]

    cli_env(monkeypatch, pg_url, tmp_path, OTHER_KEY)  # the key changed
    other = Pseudonymizer(OTHER_KEY)
    access = ["privacy", "request", "access", "--platform", "github", "--handle", "user0001"]
    for argv in (
        access,
        ["privacy", "optout", "purge"],
        ["privacy", "deletion-sync", "--dry-run"],
        ["capture", "hn-ranks", "--once"],  # the refusal list is loaded before any request
    ):
        assert main(argv) == 2, argv
        out = capsys.readouterr()
        assert "CB-25" in out.err and OTHER_KEY not in out.err + out.out
    assert main(["doctor", "--json"]) == 1
    doc = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert doc["pseudonym_key_fingerprint"]["status"] == "fail"

    assert main(["privacy", "key-fingerprint"]) == 1
    st = json.loads(capsys.readouterr().out)
    assert st["status"] == "mismatch" and st["fingerprint"] == pz.fingerprint()
    assert st["running_key_fingerprint"] == other.fingerprint()
    assert main(["privacy", "key-fingerprint", "--reset"]) == 2  # needs --confirm-rotation
    assert "--confirm-rotation" in capsys.readouterr().err
    assert fp_rows(capture_db) == [(pz.fingerprint(), "first_use")]

    assert main(["privacy", "key-fingerprint", "--reset", "--confirm-rotation"]) == 0
    out = capsys.readouterr()
    res = json.loads(out.out)
    assert TEST_KEY not in out.out + out.err and OTHER_KEY not in out.out + out.err
    assert (res["old_fingerprint"], res["fingerprint"]) == (pz.fingerprint(), other.fingerprint())
    assert fp_rows(capture_db) == [(other.fingerprint(), "reset")]
    assert fp_log(capture_db)[-1] == ("reset", pz.fingerprint(), other.fingerprint())
    run = capture_db.conn.execute(
        "SELECT job, status FROM runs WHERE id = %s", (res["run_id"],)
    ).fetchone()
    assert run == ("privacy.key_fingerprint_reset", "succeeded")
    ref = capture_db.conn.execute(
        "SELECT run_id FROM pseudonym_key_fingerprint_log WHERE event = 'reset'"
    ).fetchone()
    assert ref == (res["run_id"],)

    assert main(access) == 0  # the new key is now the database's key
    capsys.readouterr()


# --- backup carry-over --------------------------------------------------------------------------
def test_cb25_restore_carries_the_live_fingerprint_over(capture_db, pz):
    db = capture_db
    key_fingerprint.verify(db.conn, Pseudonymizer(OTHER_KEY))  # the "restored" (older) key
    live = {"fingerprint": pz.fingerprint(), "set_at": NOW, "set_by": "reset", "run_id": None}
    log = [
        {
            "logged_at": NOW,
            "event": "reset",
            "old_fingerprint": Pseudonymizer(OTHER_KEY).fingerprint(),
            "new_fingerprint": pz.fingerprint(),
            "run_id": None,
        }
    ]
    co = CarryOver(key_fingerprint=live, key_fingerprint_log=log, source="live")
    counts = write_carry_over(db, co)
    assert counts["key_fingerprint"] == 1 and counts["key_fingerprint_log"] == 1
    assert key_fingerprint.status(db.conn, pz) == "ok"
    assert write_carry_over(db, co)["key_fingerprint_log"] == 0  # idempotent


# --- CB-33: UI audit rows in the retention purge ------------------------------------------------
def test_cb33_retention_purge_deletes_old_ui_audit_rows(capture_db, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "s")
    old, young = NOW - timedelta(days=400), NOW - timedelta(days=10)
    for at in (old, young):
        db.conn.execute(
            "INSERT INTO ui_audit_log (at, event, route, status) VALUES (%s, 'logout', '/x', 200)",
            (at,),
        )
    db.conn.execute(
        "INSERT INTO ui_sessions (token_hash, expires_at) VALUES (%s, %s), (%s, %s)",
        ("a" * 64, NOW - timedelta(hours=1), "b" * 64, NOW + timedelta(hours=1)),
    )
    dry = purge(db, store, now=NOW, dry_run=True)
    assert (dry.ui_audit_rows_deleted, dry.ui_sessions_expired_deleted) == (1, 1)
    assert db.conn.execute("SELECT count(*) FROM ui_audit_log").fetchone() == (2,)
    with RunRecorder("retention.purge", {}, sink=db.upsert_run, detect_commit=False) as run:
        rep = purge(db, store, now=NOW, run=run)
    assert (rep.ui_audit_rows_deleted, rep.ui_sessions_expired_deleted) == (1, 1)
    assert db.conn.execute("SELECT at FROM ui_audit_log").fetchall() == [(young,)]
    assert db.conn.execute("SELECT token_hash FROM ui_sessions").fetchall() == [("b" * 64,)]
    logs = db.conn.execute(
        "SELECT reason, action, target, rows_affected FROM deletion_log ORDER BY id"
    ).fetchall()
    assert ("retention", "rows_deleted", "ui_audit_log", 1) in logs
    counts = db.conn.execute("SELECT counts FROM runs WHERE id = %s", (run.id,)).fetchone()[0]
    assert counts["ui_audit_rows_deleted"] == 1
    assert purge(db, store, now=NOW).ui_audit_rows_deleted == 0  # idempotent


def test_cb33_scheduled_daily_by_the_retention_purge_job():
    from pigtail.scheduler.config import load

    job = load(Path(__file__).resolve().parents[2] / "infra" / "schedule.toml").job(
        "retention_purge"
    )
    assert job.command == ("retention", "purge") and job.every == timedelta(days=1)
    assert job.enabled


# --- CB-34: unparseable snapshots do not abort subject scans ------------------------------------
@pytest.fixture
def with_unparseable(capture_db, tmp_path, pz):
    """A real GH Archive hour plus two corrupt `gharchive` snapshots (person-, project-level)."""
    store = LocalSnapshotStore(tmp_path / "snapshots")
    conn = GHArchiveConnector(
        store=store,
        pseudonymizer=pz,
        http=httpx.Client(transport=httpx.MockTransport(Archive())),
        env={},
        evidence_sink=capture_db.upsert_evidence,
        limiter=TokenBucket(1000, burst=10),
        clock=lambda: YOUNG,
    )
    good = conn.fetch(hour_url(datetime(2026, 9, 20, 0, tzinfo=UTC)))
    bad_person = put_ev(
        capture_db, store, b"\x1f\x8b truncated gzip", fetched_at=YOUNG, source="gharchive"
    )
    bad_project = put_ev(
        capture_db,
        store,
        b"not gzip at all",
        fetched_at=YOUNG,
        source="gharchive",
        retention_class="project_level",
    )
    return capture_db, store, good, bad_person, bad_project


def test_cb34_access_skips_counts_and_reports_unparseable(with_unparseable, pz, tmp_path):
    db, store, _good, bad_person, bad_project = with_unparseable
    res = requests.access(
        db, store, pz, platform="github", handle="user0001", out_dir=tmp_path / "out"
    )
    assert res.outcome == "completed" and res.counts["records"] > 0
    assert res.counts["snapshots_unparseable"] == 2
    assert res.export_path is not None
    export = json.loads(res.export_path.read_text())
    listed = {u["content_hash"] for u in export["unparseable_snapshots"]}
    assert listed == {bad_person.content_hash, bad_project.content_hash}
    assert all(u["error"] for u in export["unparseable_snapshots"])
    assert store.exists(bad_person.content_hash)  # access changes nothing


def test_cb34_erasure_drops_unparseable_person_level_snapshots(with_unparseable, pz):
    db, store, good, bad_person, bad_project = with_unparseable
    with RunRecorder("privacy.erasure", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = requests.erasure(db, store, pz, platform="github", handle="user0001", run=run)
    assert res.outcome == "completed"
    assert res.counts["snapshots_unparseable"] == 2
    assert res.counts["snapshots_unparseable_raw_dropped"] == 1
    assert not store.exists(good.content_hash)  # the person's records were found and dropped
    assert state(db, bad_person) == "raw_dropped" and not store.exists(bad_person.content_hash)
    assert state(db, bad_project) == "present" and store.exists(bad_project.content_hash)
    logs = db.conn.execute(
        "SELECT reason, action, content_hash, request_id FROM deletion_log"
    ).fetchall()
    assert ("erasure", "raw_dropped", bad_person.content_hash, res.request_id) in logs
    counts = db.conn.execute("SELECT counts FROM runs WHERE id = %s", (run.id,)).fetchone()[0]
    assert counts["snapshots_unparseable_raw_dropped"] == 1


def test_cb34_erasure_with_only_unparseable_data_still_completes(capture_db, tmp_path, pz):
    store = LocalSnapshotStore(tmp_path / "s")
    bad = put_ev(capture_db, store, b"garbage", fetched_at=YOUNG, source="gharchive")
    res = requests.erasure(capture_db, store, pz, platform="github", handle="user0009")
    assert res.outcome == "completed" and res.counts["snapshots_unparseable_raw_dropped"] == 1
    assert state(capture_db, bad) == "raw_dropped"
    p = subject_pseudonym(pz, "github", "user0009")
    assert p in suppression.load(capture_db, pz).persons
