"""M1-T19: missing GH Archive hours (404 / 5xx / transport / undecodable dump) are recorded with a
reason and a retry time, and `backfill_missing()` retries them with backoff up to N days back.

Synthetic GH Archive fixtures only (tests/fixtures/gharchive/, fake logins and repos)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from pigtail.capture.gharchive_backfill import BackfillConfig, backfill_missing
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.capture.velocity import VelocityScanner
from pigtail.cli import main
from pigtail.connectors.base import RetryPolicy, TokenBucket
from pigtail.connectors.gharchive import GHArchiveConnector
from pigtail.scheduler import config as sconfig

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "gharchive"
T0 = datetime(2026, 9, 20, tzinfo=UTC)
H = timedelta(hours=1)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Archive:
    """Serves fixture hours; `mode[name]` overrides one file: an int status, 'transport' or
    'corrupt' (a truncated gzip)."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.mode: dict[str, Any] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        self.requests.append(name)
        m = self.mode.get(name)
        if m == "transport":
            raise httpx.ConnectError("synthetic outage", request=req)
        if isinstance(m, int):
            return httpx.Response(m)
        path = FIX / name
        if not path.exists():
            return httpx.Response(404)
        data = path.read_bytes()
        if m == "corrupt":
            data = data[: len(data) // 2]
        return httpx.Response(200, content=data, headers={"Content-Type": "application/gzip"})


def scanner(db: Any, tmp_path: Path, pz: Any, archive: Archive, clock: Clock, run: Any = None):
    conn = GHArchiveConnector(
        store=LocalSnapshotStore(tmp_path / "snapshots"),
        pseudonymizer=pz,
        http=httpx.Client(transport=httpx.MockTransport(archive)),
        env={},
        run=run,
        evidence_sink=db.upsert_evidence,
        limiter=TokenBucket(1e9, burst=1000),
        retry=RetryPolicy(max_retries=0),
        sleep=lambda s: None,
        clock=clock,
    )
    return VelocityScanner(connector=conn, db=db, run=run)


def hours(db: Any) -> dict[datetime, tuple[Any, ...]]:
    return {
        r[0]: r[1:]
        for r in db.conn.execute(
            "SELECT hour, status, missing_reason, http_status, attempts, next_retry_at"
            " FROM gharchive_hours ORDER BY hour"
        )
    }


def test_m1_t19_missing_hours_recorded_with_reason_and_backoff(capture_db, tmp_path, pz):
    db = capture_db
    clock = Clock(T0 + 3 * H)
    a = Archive()
    a.mode = {"2026-09-20-1.json.gz": 503, "2026-09-20-2.json.gz": "transport"}
    res = scanner(db, tmp_path, pz, a, clock).scan(T0, T0 + 4 * H)
    assert (res.hours_ok, res.hours_missing) == (1, 3)
    hs = hours(db)
    assert hs[T0][0] == "ok" and hs[T0][4] is None
    assert hs[T0 + H] == ("missing", "server_error", 503, 1, clock.t + H)
    assert hs[T0 + 2 * H] == ("missing", "transport_error", None, 1, clock.t + H)
    assert hs[T0 + 3 * H] == ("missing", "not_found", 404, 1, clock.t + H)
    # a forced re-scan that fails again doubles the wait (1 h, 2 h, 4 h ... capped at 24 h)
    clock.t += 2 * H
    scanner(db, tmp_path, pz, a, clock).scan(T0, T0 + 4 * H, force=True)
    assert hours(db)[T0 + H][3:] == (2, clock.t + 2 * H)


def test_m1_t19_backfill_retries_with_backoff_and_recovers_the_day(capture_db, tmp_path, pz):
    db = capture_db
    clock = Clock(T0 + 3 * H)
    a = Archive()
    a.mode = {"2026-09-20-1.json.gz": 502}
    s = scanner(db, tmp_path, pz, a, clock)
    s.scan(T0, T0 + 3 * H)
    before_cases = [c.id for c in db.get_cases()]
    assert before_cases == []  # without hour 1 the burst stays under the threshold
    # not due yet: nothing is downloaded
    n = len(a.requests)
    res = backfill_missing(s, now=clock.t)
    assert (res.due, res.tried, res.not_due, len(a.requests)) == (0, 0, 1, n)
    # due, still failing (now a 404): attempt counted, next retry pushed back (2 h)
    a.mode = {"2026-09-20-1.json.gz": 404}
    clock.t += H
    res = backfill_missing(s, now=clock.t)
    assert (res.tried, res.recovered, res.still_missing) == (1, 0, 1)
    assert res.still_missing_by_reason == {"not_found": 1}
    assert hours(db)[T0 + H] == ("missing", "not_found", 404, 2, clock.t + 2 * H)
    # available now: the hour is downloaded once, the day re-aggregated, detection re-run
    a.mode = {}
    clock.t += 2 * H
    n = len(a.requests)
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        s.run = run
        res = backfill_missing(s, now=clock.t)
    assert (res.tried, res.recovered, res.still_missing, res.days_rescanned) == (1, 1, 0, 1)
    assert a.requests[n:] == ["2026-09-20-1.json.gz"]  # other hours re-read from the store
    assert all(v[0] == "ok" and v[4] is None for v in hours(db).values())
    cases = [c.repo_id for c in db.get_cases()]
    assert "github:1000001" in cases  # the burst in org-a/repo-1 needs the recovered hour
    assert set(res.cases_opened) == {c.id for c in db.get_cases()} - set(before_cases)
    assert run.counts["backfill.recovered"] == 1
    total = db.conn.execute(
        "SELECT sum(stars_raw) FROM repo_hourly_activity WHERE hour = %s", (T0 + H,)
    ).fetchone()
    assert total[0] > 0
    # idempotent: nothing due any more
    assert backfill_missing(s, now=clock.t).due == 0


def test_m1_t19_backfill_window_expired_hours_and_cap(capture_db, tmp_path, pz):
    db = capture_db
    clock = Clock(T0 + 3 * H)
    a = Archive()
    s = scanner(db, tmp_path, pz, a, clock)
    old = T0 - timedelta(days=10)
    db.conn.execute(
        "INSERT INTO gharchive_hours (hour, status, bot_filter_version, missing_reason,"
        " next_retry_at) VALUES (%s, 'missing', 'x', 'not_found', %s)",
        (old, old),
    )
    res = backfill_missing(s, now=clock.t, cfg=BackfillConfig(max_days=7))
    assert (res.expired, res.due, res.tried) == (1, 0, 0) and a.requests == []
    with pytest.raises(ValueError):
        BackfillConfig(max_days=31)


def test_m1_t19_cb23b_undecodable_dump_is_missing_and_dropped(capture_db, tmp_path, pz):
    db = capture_db
    clock = Clock(T0 + 3 * H)
    a = Archive()
    a.mode = {"2026-09-20-2.json.gz": "corrupt"}
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        s = scanner(db, tmp_path, pz, a, clock, run=run)
        res = s.scan(T0, T0 + 3 * H)
    assert (res.hours_ok, res.hours_missing) == (2, 1)
    assert hours(db)[T0 + 2 * H][:3] == ("missing", "unparseable", 200)
    ev = db.conn.execute(
        "SELECT deletion_state, content_hash FROM evidence WHERE url LIKE '%%2026-09-20-2.json.gz'"
    ).fetchone()
    assert ev[0] == "raw_dropped" and not s.connector.store.exists(ev[1])
    assert db.conn.execute(
        "SELECT count(*) FROM repo_hourly_activity WHERE hour = %s", (T0 + 2 * H,)
    ).fetchone() == (0,)  # no partial counts from the half-read dump
    assert (
        run.counts["gharchive.parse_failed"] == 1 and run.counts["hours_missing.unparseable"] == 1
    )
    # the next retry gets a good copy
    a.mode = {}
    clock.t += H
    assert backfill_missing(s, now=clock.t).recovered == 1


def test_m1_t19_cli_and_schedule(capture_db, pg_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", "test-key-not-secret-0123456789")
    assert main(["capture", "backfill-gharchive", "--days", "0"]) == 2
    assert main(["capture", "backfill-gharchive", "--days", "7"]) == 0  # nothing missing
    out = json.loads(capsys.readouterr().out)
    assert out["due"] == 0 and out["run_id"].startswith("run_")
    job = capture_db.conn.execute("SELECT job, status FROM runs WHERE id = %s", (out["run_id"],))
    assert job.fetchone() == ("capture.backfill_gharchive", "succeeded")
    spec = sconfig.load(ROOT / "infra" / "schedule.toml").job("gharchive_backfill")
    assert spec.command == ("capture", "backfill-gharchive", "--days", "7")
    assert spec.every == timedelta(hours=1) and spec.params["requires"] == ["gharchive"]
