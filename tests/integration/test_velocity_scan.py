"""M1-T3 / R1.1 end to end on synthetic GH Archive fixtures + Postgres (no real network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pigtail.capture.models import Coverage
from pigtail.capture.replay import replay
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.capture.velocity import (
    Confirmation,
    Evaluation,
    VelocityConfig,
    VelocityScanner,
)
from pigtail.connectors.base import TokenBucket
from pigtail.connectors.gharchive import GHArchiveConnector

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "gharchive"
T0 = datetime(2026, 9, 20, tzinfo=UTC)
H = timedelta(hours=1)
CASE_SCHEMA = json.loads((ROOT / "schemas/v0/case.schema.json").read_text())


class Archive:
    """MockTransport serving fixture hours; anything else is 404."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(str(req.url))
        name = req.url.path.rsplit("/", 1)[-1]
        path = FIX / name
        if path.exists():
            return httpx.Response(
                200, content=path.read_bytes(), headers={"Content-Type": "application/gzip"}
            )
        return httpx.Response(404)


def scanner(db: Any, tmp_path: Path, pz: Any, archive: Archive, **kw: Any) -> VelocityScanner:
    run: RunRecorder | None = kw.pop("run", None)
    conn = GHArchiveConnector(
        store=LocalSnapshotStore(tmp_path / "snapshots"),
        pseudonymizer=pz,
        http=httpx.Client(transport=httpx.MockTransport(archive)),
        env={},
        run=run,
        evidence_sink=db.upsert_evidence,
        limiter=TokenBucket(1e9, burst=1000),
    )
    return VelocityScanner(connector=conn, db=db, run=run, **kw)


def test_r1_1_scan_opens_one_case_after_bot_filtering(capture_db, tmp_path, pz):
    archive = Archive()
    with RunRecorder("capture.scan", {"t": 1}, sink=capture_db.upsert_run) as run:
        res = scanner(capture_db, tmp_path, pz, archive, run=run).scan(T0, T0 + 3 * H)
    assert (res.hours_ok, res.hours_missing) == (3, 0)
    assert len(archive.requests) == 3
    cases = capture_db.get_cases()
    assert [c.repo_id for c in cases] == ["github:1000001"]  # farm repo filtered out
    case = cases[0]
    det = case.detection
    assert det is not None
    assert det.detected_hour == T0 + H and case.opened_at == T0 + 2 * H
    assert (det.stars_48h, det.stars_48h_raw) == (130, 132)
    assert det.baseline_quality == "none" and det.baseline_hours_covered == 0
    assert det.coverage.source == "gharchive" and det.coverage.observed_stars == 132
    assert det.coverage.reference_stars is None and det.coverage.ratio is None
    assert case.status == "live" and case.trigger == "velocity" and case.run_id == run.id
    Draft202012Validator(CASE_SCHEMA, format_checker=FormatChecker()).validate(case.to_json_dict())
    # run + evidence recorded; hours linked to evidence
    c = capture_db.conn
    status, counts = c.execute(
        "SELECT status, counts FROM runs WHERE id = %s", (run.id,)
    ).fetchone()
    assert status == "succeeded" and counts["cases_opened"] == 1 and counts["hours_ok"] == 3
    assert c.execute("SELECT count(*) FROM evidence").fetchone()[0] == 3
    assert c.execute(
        "SELECT count(*) FROM gharchive_hours WHERE evidence_id IS NOT NULL AND status = 'ok'"
    ).fetchone() == (3,)
    farm = c.execute(
        "SELECT stars_raw, stars_lockstep, stars_filtered, lockstep_flag "
        "FROM repo_hourly_activity WHERE repo_host_id = 1000002 AND hour = %s",
        (T0 + H,),
    ).fetchone()
    assert farm == (120, 120, 0, True)
    assert c.execute("SELECT full_name FROM repos").fetchall() == [("org-a/repo-1",)]


def test_prd10_no_plain_logins_in_database(capture_db, tmp_path, pz):
    scanner(capture_db, tmp_path, pz, Archive()).scan(T0, T0 + 3 * H)
    dump = []
    for table in ("repo_hourly_activity", "gharchive_hours", "cases", "repos", "evidence"):
        dump += [str(r) for r in capture_db.conn.execute(f"SELECT * FROM {table}")]
    blob = "\n".join(dump)
    for login in ("user0001", "farm0001", "helper-app", "ci-bot", "dependabot"):
        assert login not in blob


def test_r1_1_rerun_is_idempotent_and_offline(capture_db, tmp_path, pz):
    archive = Archive()
    s = scanner(capture_db, tmp_path, pz, archive)
    first = s.scan(T0, T0 + 3 * H)
    assert len(first.cases) == 1
    rows_before = capture_db.conn.execute(
        "SELECT * FROM repo_hourly_activity ORDER BY 1, 2"
    ).fetchall()
    again = s.scan(T0, T0 + 3 * H)
    assert again.cases == [] and again.hours_skipped == 3
    forced = s.scan(T0, T0 + 3 * H, force=True)  # re-aggregates from stored snapshots
    assert forced.cases == [] and forced.hours_ok == 3
    assert len(archive.requests) == 3  # no re-download
    assert len(capture_db.get_cases()) == 1
    rows_after = capture_db.conn.execute(
        "SELECT * FROM repo_hourly_activity ORDER BY 1, 2"
    ).fetchall()
    assert rows_after == rows_before


def test_r1_1_missing_hour_recorded(capture_db, tmp_path, pz):
    res = scanner(capture_db, tmp_path, pz, Archive()).scan(T0 + 2 * H, T0 + 4 * H)
    assert (res.hours_ok, res.hours_missing) == (1, 1)
    assert capture_db.conn.execute(
        "SELECT status FROM gharchive_hours WHERE hour = %s", (T0 + 3 * H,)
    ).fetchone() == ("missing",)


def seed_busy_baseline(db: Any, repo: int, per_hour: int) -> None:
    """Pretend the 30 days before the 48 h window were scanned, `per_hour` stars every hour."""
    start = T0 - 47 * H - 720 * H
    with db.conn.transaction():
        for i in range(720):
            h = start + i * H
            db.conn.execute(
                "INSERT INTO gharchive_hours (hour, status, bot_filter_version) "
                "VALUES (%s, 'ok', 'seed')",
                (h,),
            )
            db.conn.execute(
                "INSERT INTO repo_hourly_activity (repo_host_id, hour, repo_name, stars_raw, "
                "stars_filtered) VALUES (%s, %s, 'org-a/repo-1', %s, %s)",
                (repo, h, per_hour, per_hour),
            )


def test_r1_1_busy_repo_baseline_suppresses_case(capture_db, tmp_path, pz):
    seed_busy_baseline(capture_db, 1000001, per_hour=4)  # ~192 per 48 h is normal for it
    res = scanner(capture_db, tmp_path, pz, Archive()).scan(T0, T0 + 3 * H)
    assert res.cases == []


def test_r1_1_quiet_repo_full_baseline_opens_case(capture_db, tmp_path, pz):
    seed_busy_baseline(capture_db, 1000001, per_hour=0)
    res = scanner(capture_db, tmp_path, pz, Archive()).scan(T0, T0 + 3 * H)
    assert len(res.cases) == 1
    det = res.cases[0].detection
    assert det is not None and det.baseline_quality == "full"


class Rejecting:
    def __init__(self) -> None:
        self.seen: list[Evaluation] = []

    def confirm(self, ev: Evaluation, window_start: datetime, window_end: datetime) -> Confirmation:
        self.seen.append(ev)
        cov = Coverage(
            source="gharchive",
            window_start=window_start,
            window_end=window_end,
            observed_stars=ev.candidate.stars_48h_raw,
            reference_stars=10,
            reference_source="synthetic_reference",
            ratio=ev.candidate.stars_48h_raw / 10,
        )
        return Confirmation(coverage=cov, confirmed=len(self.seen) > 1)


def test_r1_1_confirmer_hook_can_reject_and_records_reference(capture_db, tmp_path, pz):
    conf = Rejecting()
    s = scanner(capture_db, tmp_path, pz, Archive(), confirmer=conf)
    assert s.scan(T0, T0 + 2 * H).cases == []  # first candidate rejected
    res = s.scan(T0 + 2 * H, T0 + 3 * H)  # hour 02 still above threshold -> accepted now
    assert len(res.cases) == 1
    cov = res.cases[0].detection.coverage  # type: ignore[union-attr]
    assert cov.reference_source == "synthetic_reference" and cov.reference_stars == 10


def test_r1_1_threshold_configurable(capture_db, tmp_path, pz):
    cfg = VelocityConfig(min_stars_48h=10)
    res = scanner(capture_db, tmp_path, pz, Archive(), cfg=cfg).scan(T0, T0 + 3 * H)
    # repo-1 now fires at hour 00; repo-3 (9 stars) and repo-2 (5 filtered) stay below
    assert [(c.repo_id, c.detection.detected_hour) for c in res.cases] == [  # type: ignore[union-attr]
        ("github:1000001", T0)
    ]


def test_m1_t10_replay_hook_on_gharchive_snapshot(capture_db, tmp_path, pz):
    s = scanner(capture_db, tmp_path, pz, Archive())
    s.scan(T0, T0 + H)
    h = capture_db.conn.execute(
        "SELECT content_hash FROM gharchive_hours WHERE hour = %s", (T0,)
    ).fetchone()[0]
    data = s.connector.store.get(h)
    stored = list(s.connector.records(data, s.connector.store.meta(h)))
    assert replay(s.connector, h) == stored and len(stored) > 100


# --- DPIA CB-04: raw retention, purge, replay by re-download -----------------------------------
def test_cb04_purge_respects_retention(capture_db, tmp_path, pz):
    from pigtail.capture.retention import purge_raw

    s = scanner(capture_db, tmp_path, pz, Archive())
    s.scan(T0, T0 + 3 * H)
    store = s.connector.store
    assert purge_raw(capture_db, store, source="gharchive", retention_days=30) == 0
    hashes = [r[0] for r in capture_db.conn.execute("SELECT content_hash FROM evidence").fetchall()]
    assert all(store.exists(h) for h in hashes)
    later = datetime.now(UTC) + timedelta(days=31)
    assert purge_raw(capture_db, store, source="gharchive", retention_days=30, now=later) == 3
    assert not any(store.exists(h) for h in hashes)
    states = capture_db.conn.execute("SELECT DISTINCT deletion_state FROM evidence").fetchall()
    assert states == [("raw_dropped",)]
    assert all(store.meta(h).url.startswith("https://data.gharchive.org/") for h in hashes)
    assert purge_raw(capture_db, store, source="gharchive", retention_days=0) == 0  # idempotent


def test_cb04_replay_and_rescan_after_purge_redownload_and_verify(capture_db, tmp_path, pz):
    from pigtail.capture.retention import purge_raw
    from pigtail.capture.snapshots import SnapshotIntegrityError

    archive = Archive()
    s = scanner(capture_db, tmp_path, pz, archive)
    s.scan(T0, T0 + 3 * H)
    h = capture_db.conn.execute(
        "SELECT content_hash FROM gharchive_hours WHERE hour = %s", (T0,)
    ).fetchone()[0]
    stored = replay(s.connector, h)
    rows_before = capture_db.conn.execute(
        "SELECT * FROM repo_hourly_activity ORDER BY 1, 2"
    ).fetchall()
    purge_raw(capture_db, s.connector.store, source="gharchive", retention_days=0)
    assert replay(s.connector, h) == stored  # re-downloaded, hash-verified
    assert not s.connector.store.exists(h)  # and not re-stored
    res = s.scan(T0, T0 + 3 * H, force=True)
    assert res.hours_ok == 3 and len(capture_db.get_cases()) == 1
    rows_after = capture_db.conn.execute(
        "SELECT * FROM repo_hourly_activity ORDER BY 1, 2"
    ).fetchall()
    assert rows_after == rows_before
    # upstream bytes changed -> refuse
    s.connector.http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"changed"))
    )
    with pytest.raises(SnapshotIntegrityError):
        replay(s.connector, h)


def test_cb04_derived_table_holds_no_actor_fields(capture_db):
    cols = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'repo_hourly_activity'"
        )
    }
    assert cols == {
        "repo_host_id", "hour", "repo_name", "stars_raw", "stars_bot", "stars_lockstep",
        "stars_filtered", "forks_raw", "forks_filtered", "lockstep_flag",
    }  # fmt: skip
