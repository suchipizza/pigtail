"""M4-T4 / K2: settle_lag collection (star-history re-fetches at +1, +3, +7, +14, +21 days).

Synthetic data only: tests/github_fake.py (fake repos org-x/repo-1 = 7000001, org-x/repo-2 =
7000002, org-y/repo-3 = 7000003) and synthetic case ids searched by split.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.analysis.split import split_of
from pigtail.capture.settle_lag import LAGS, SettleLagConfig, collect, day_end, due_at
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.capture.star_history import fetch_star_history
from pigtail.privacy import suppression
from pigtail.privacy.deletion import DeletionLog
from pigtail.privacy.requests import purge_repo
from pigtail.scheduler.config import load as load_schedule
from pigtail.scheduler.jobs import Planner
from tests.github_fake import NOW, FakeGitHub
from tests.integration.test_github_detection_m1t24 import Clock, connector

pytestmark = pytest.mark.db

REPOS = {7000001: "org-x/repo-1", 7000002: "org-x/repo-2", 7000003: "org-y/repo-3"}


def _case_id(split_name: str) -> str:
    for i in itertools.count():
        cid = f"case_synth_{i:05d}"
        if split_of(cid) == split_name:
            return cid
    raise AssertionError


def seed(db: Any, fake: FakeGitHub, tmp_path: Path, clk: Clock) -> None:
    for host_id, name in REPOS.items():
        db.conn.execute(
            "INSERT INTO watchlist (repo_host_id, full_name, source, added_at, last_nominated_at)"
            " VALUES (%s, %s, 'manual', %s, %s)",
            (host_id, name, NOW - timedelta(days=3), NOW - timedelta(days=3)),
        )
        db.conn.execute(
            "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
            " VALUES (%s, 'github', %s, %s, %s)",
            (f"github:{host_id}", host_id, name, NOW - timedelta(days=3)),
        )
        fetch_star_history(connector(db, fake, tmp_path, clock=clk), db, host_id, name)
    # 7000001: a calibration-split case; 7000002: an H-sealed case; 7000003: no case at all
    for host_id, split_name in ((7000001, "calibration"), (7000002, "h_sealed")):
        db.conn.execute(
            "INSERT INTO cases (id, repo_id, opened_at, trigger, status)"
            " VALUES (%s, %s, %s, 'velocity', 'live')",
            (_case_id(split_name), f"github:{host_id}", NOW - timedelta(days=2)),
        )


def obs(db: Any) -> list[tuple[Any, ...]]:
    return db.conn.execute(
        "SELECT repo_host_id, day, lag_days, stars_net, not_modified, lag_hours_actual"
        " FROM star_history_settle_obs ORDER BY id"
    ).fetchall()


def test_m4_t4_k2_due_times_follow_the_endpoint_day():
    # 2026-09-24 ends at 2026-09-25 00:00 US Pacific (PDT) = 07:00 UTC
    assert day_end(date(2026, 9, 24)).isoformat() == "2026-09-25T00:00:00-07:00"
    assert [due_at(date(2026, 9, 24), lag).day for lag in LAGS] == [26, 28, 2, 9, 16]
    # the DST change (2026-11-01) is handled by the zone, not by a fixed offset
    assert day_end(date(2026, 11, 1)).utcoffset() == timedelta(hours=-8)


def test_m4_t4_k2_enrols_calibration_repos_and_stores_every_fetch_version(capture_db, tmp_path):
    db, fake, clk = capture_db, FakeGitHub(), Clock()
    seed(db, fake, tmp_path, clk)
    sup = suppression.load(db)

    # day 0 (2026-09-25 11:00 PDT): yesterday (09-24) is enrolled, nothing due yet
    before = len(fake.requests)
    r = collect(connector(db, fake, tmp_path, clock=clk), db, sup, now=clk.t)
    assert (r.enrolled_repos, r.enrolled_items, r.due_repos) == (1, 5, 0)
    assert len(fake.requests) == before  # no request when nothing is due
    rows = db.conn.execute("SELECT DISTINCT repo_host_id, day FROM settle_lag_schedule").fetchall()
    assert rows == [(7000001, date(2026, 9, 24))]  # not the H-sealed repo, not the case-less one
    # re-running is idempotent
    assert collect(connector(db, fake, tmp_path, clock=clk), db, sup, now=clk.t).enrolled_items == 0

    # +1 day, one hour after the lag-1 due time: one request serves the item
    clk.t = datetime.fromisoformat("2026-09-26T08:00:00+00:00")
    before = len(fake.requests)
    r = collect(connector(db, fake, tmp_path, clock=clk), db, sup, now=clk.t)
    assert r.fetched_items == 1 and r.enrolled_items == 5  # 09-25 enrolled too
    assert len(fake.requests) == before + 1
    v1 = fake.daily[7000001][-2]  # the fake's 2026-09-24 value
    (row,) = obs(db)
    assert row[:5] == (7000001, date(2026, 9, 24), 1, v1, False)
    assert row[5] == pytest.approx(25.0)

    # stars drift (un-stars) before the lag-3 fetch; the lag-1 item of 09-25 is missed
    fake.daily[7000001][-2] = v1 - 2
    clk.t = datetime.fromisoformat("2026-09-28T08:00:00+00:00")
    r = collect(connector(db, fake, tmp_path, clock=clk), db, sup, now=clk.t)
    assert r.missed_items == 1 and r.fetched_items == 1
    got = obs(db)
    assert [(g[2], g[3]) for g in got] == [(1, v1), (3, v1 - 2)]  # both versions kept
    status = dict(
        db.conn.execute(
            "SELECT lag_days, status FROM settle_lag_schedule WHERE day = '2026-09-25'"
        ).fetchall()
    )
    assert status[1] == "missed" and status[3] == "pending"

    # append-only: an observation cannot be rewritten
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.conn.execute("UPDATE star_history_settle_obs SET stars_net = 0")

    # an opt-out drops pending items, and the repo purge removes the collected versions
    suppression.add(db, "repo", "github:7000001", platform="github", reason="objection")
    r = collect(
        connector(db, fake, tmp_path, clock=clk), db, suppression.load(db), now=clk.t,
        cfg=SettleLagConfig(max_repos=10),
    )  # fmt: skip
    assert r.dropped_items > 0 and r.enrolled_items == 0
    purge_repo(
        db, LocalSnapshotStore(tmp_path / "snap"), "github:7000001", DeletionLog(db, "objection")
    )
    assert obs(db) == []
    assert db.conn.execute(
        "SELECT count(*) FROM settle_lag_schedule WHERE repo_host_id = 7000001"
    ).fetchone() == (0,)


def test_m4_t4_k2_scheduler_job_is_off_until_github_token_exists():
    cfg = load_schedule()
    job = cfg.job("gh_settle_lag")
    assert job.enabled and job.command == ("capture", "github", "settle-lag")
    plan = Planner({}).plan(job, NOW)
    assert plan.skip == "missing_env:GITHUB_TOKEN" and plan.commands == ()
    plan = Planner({"GITHUB_TOKEN": "fake-token-for-tests"}).plan(job, NOW)
    assert plan.skip is None and plan.commands == (("capture", "github", "settle-lag"),)
