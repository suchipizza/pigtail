"""M1-T24 on Postgres: watch list + GraphQL counts, search sweeps, HN screen, star history,
detection v1, per-repo events, 30-day retention, CLI gates (ADR-032; TM-02, TM-33; CB-22, CB-23).

Synthetic data only: tests/github_fake.py and tests/fixtures/github/ (fake repos org-x/repo-n,
org-y/repo-n, org-s/search-NNNN; fake users ghuserNNN, helper-app[bot]).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pigtail.capture.detection_v1 import DetectorV1, agreement_stats
from pigtail.capture.github_screens import SearchSweeper, SweepConfig, gharchive_screen, hn_screen
from pigtail.capture.github_watch import CountSnapshotter, Watchlist, WatchPolicy
from pigtail.capture.models import Case, DetectionV1
from pigtail.capture.repo_events import EventsConfig, RepoEventsPoller
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta
from pigtail.capture.star_history import DAY_BOUNDARY_NOTE, fetch_star_history
from pigtail.cli import main
from pigtail.connectors.base import ADR022_ENV, TokenBucket
from pigtail.connectors.github import (
    GitHubConnector,
    GitHubRepoEventsConnector,
    PostgresCache,
)
from pigtail.connectors.github_budget import Budget, JobCaps, PostgresLedger
from pigtail.connectors.hn_ranks import HNRanksConnector
from pigtail.privacy import suppression
from pigtail.privacy.deletion import PERSON_TABLES, DeletionLog, delete_person_rows
from pigtail.privacy.retention import RetentionConfig, purge
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY
from tests.github_fake import NOW, TODAY, TOKEN, FakeGitHub, make_events, search_pool, series

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]
EVENTS_ON = {"PIGTAIL_ENABLE_GITHUB_EVENTS": "1", ADR022_ENV: "1"}


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def limiters() -> dict[str, TokenBucket]:
    return {r: TokenBucket(1000, burst=1000) for r in ("core", "graphql", "search")}


def connector(
    db: Any,
    fake: FakeGitHub,
    tmp_path: Path,
    *,
    clock: Clock | None = None,
    job: JobCaps | None = None,
    cls: type[Any] = GitHubConnector,
    run: Any = None,
    **kw: Any,
) -> Any:
    clk = clock or Clock()
    return cls(
        store=LocalSnapshotStore(tmp_path / "snap"),
        http=fake.client(),
        token=TOKEN,
        env=kw.pop("env", {}),
        budget=Budget(ledger=PostgresLedger(db.conn), job=job, clock=clk, sleep=lambda s: None),
        cache=PostgresCache(db.conn),
        limiters=limiters(),
        sleep=lambda s: None,
        clock=clk,
        evidence_sink=db.upsert_evidence,
        run=run,
        **kw,
    )


def dump_all_tables(db: Any) -> str:
    tables = [
        r[0]
        for r in db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    ]
    return "\n".join(
        (db.conn.execute(f'SELECT json_agg(t)::text FROM "{t}" t').fetchone() or [""])[0] or ""
        for t in tables
    )


# --- migrations ----------------------------------------------------------------------------------
def test_m1_t24_migration_tables_and_constraints(capture_db):
    names = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {
        "watchlist",
        "repo_count_snapshot",
        "github_graphql_batch",
        "github_budget_ledger",
        "github_http_cache",
        "repo_star_daily",
        "star_history_fetch",
        "detection_agreement",
        "hn_show_screen",
        "repo_event_actor",
        "repo_event_poll",
        "repo_event_daily_agg",
    } <= names
    with pytest.raises(psycopg.errors.CheckViolation):  # pseudonyms only, never handles
        capture_db.conn.execute(
            "INSERT INTO repo_event_actor (repo_host_id, event_id, event_type, actor_pseudonym,"
            " is_bot, created_at, observed_at) VALUES (1, 'e', 'WatchEvent', 'ghuser001', false,"
            " now(), now())"
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # CB-23: stars and forks only
        capture_db.conn.execute(
            "INSERT INTO repo_event_actor (repo_host_id, event_id, event_type, is_bot, created_at,"
            " observed_at) VALUES (1, 'e', 'PushEvent', true, now(), now())"
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        capture_db.conn.execute(
            "INSERT INTO watchlist (full_name, source, added_at, last_nominated_at)"
            " VALUES ('a/b', 'trending', now(), now())"
        )
    cols = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN"
            " ('watchlist', 'repo_count_snapshot', 'repo_star_daily', 'repo_event_daily_agg')"
        )
    }
    assert not cols & {"login", "actor", "owner_login", "actor_pseudonym"}


# --- watch list + GraphQL counts -----------------------------------------------------------------
def test_m1_t24_graphql_batches_counts_cost_and_resolution(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    fake.search_pool = search_pool(
        145, created_from=NOW - timedelta(days=3), span=timedelta(days=2)
    )
    w = Watchlist(db)
    for r in fake.search_pool:
        assert w.nominate("search", r["full_name"], repo_host_id=r["id"], node_id=r["node_id"]) == (
            "added"
        )
    w.nominate("search", "org-x/old-name", repo_host_id=7000002, node_id="R_fake_7000002")
    w.nominate("hn", "org-x/repo-2", source_ref="hn:1")  # same repo, current name, no id yet
    w.nominate("hn", "org-x/repo-1", source_ref="show_hn:2")
    w.nominate("hn", "org-y/gone", source_ref="hn:3")
    w.nominate("hn", "ORG-X/REPO-1", source_ref="hn:4")  # case-insensitive: same row
    assert w.counts() == {"active": 149, "inactive": 0}
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        c = connector(db, fake, tmp_path, run=run)
        res = CountSnapshotter(c, db, run=run).snapshot_counts()
    assert res.batches == 2 and res.batches_failed == 0 and res.repos_missing == 1
    assert res.merged == 1 and res.points == 2
    assert db.conn.execute(
        "SELECT count(*), sum(cost), sum(n_aliases) FROM github_graphql_batch"
    ).fetchone() == (2, 2, 149)
    led = db.conn.execute(
        "SELECT units, requests FROM github_budget_ledger WHERE resource = 'graphql'"
    ).fetchone()
    assert led == (2, 2)
    row = db.conn.execute(
        "SELECT full_name, sources, pinned, last_stars FROM watchlist WHERE repo_host_id = 7000002"
    ).fetchone()
    assert row[0] == "org-x/repo-2" and set(row[1]) == {"search", "hn"} and row[3] == 9000
    one = db.conn.execute(
        "SELECT repo_host_id, node_id, owner_type, created_at_gh FROM watchlist"
        " WHERE lower(full_name) = 'org-x/repo-1'"
    ).fetchone()
    assert one[:3] == (7000001, "R_fake_7000001", "Organization") and one[3] is not None
    gone = db.conn.execute(
        "SELECT active, deactivated_reason FROM watchlist WHERE full_name = 'org-y/gone'"
    ).fetchone()
    assert gone == (False, "not_found")
    assert db.conn.execute("SELECT count(*) FROM repo_count_snapshot").fetchone() == (147,)
    ev = db.conn.execute("SELECT DISTINCT retention_class, source FROM evidence").fetchall()
    assert ev == [("project_level", "github")]
    assert run.counts["counts.batches"] == 2


def test_m1_t24_counts_budget_stop_split_and_optout(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    fake.search_pool = search_pool(
        250, created_from=NOW - timedelta(days=3), span=timedelta(days=2)
    )
    w = Watchlist(db)
    for r in fake.search_pool:
        w.nominate("search", r["full_name"], repo_host_id=r["id"], node_id=r["node_id"])
    w.nominate("search", "org-y/repo-4", repo_host_id=7000004, node_id="R_fake_7000004")
    # hard stop: job cap of 2 points -> 2 of 3 batches, then budget_stop
    c = connector(db, fake, tmp_path, job=JobCaps({"graphql": 2}))
    res = CountSnapshotter(c, db).snapshot_counts()
    assert res.batches == 2 and res.budget_stop == "job_cap"
    # a heavy batch that fails (502) is split in half once
    fake.fail_graphql_over = 60
    c2 = connector(db, fake, tmp_path)
    sup = suppression.Suppressions(repos=frozenset({"github:7000004"}))
    res2 = CountSnapshotter(
        c2, db, suppression=sup, policy=WatchPolicy(batch_size=100)
    ).snapshot_counts()
    # the opted-out repo is dropped before batching (250 rows: 100 + 100 + 50); each batch over
    # 60 aliases fails with 502 and is split in two
    assert res2.policy["opted_out"] == 1
    assert res2.batches == 5 and res2.batches_failed == 0 and res2.repos_counted == 250
    assert db.conn.execute(
        "SELECT active, deactivated_reason FROM watchlist WHERE repo_host_id = 7000004"
    ).fetchone() == (False, "opted_out")
    assert w.nominate("hn", "org-y/repo-4") == "skipped"  # stays out


def test_m1_t24_watchlist_policy_stale_and_cap(capture_db):
    db = capture_db
    w = Watchlist(db)
    old = NOW - timedelta(days=40)
    for i in range(5):
        w.nominate("search", f"org-z/r{i}", repo_host_id=900 + i, at=old)
    w.nominate("manual", "org-z/pinned", repo_host_id=990, at=old)
    # r0 grew by 50 stars this week: kept although stale
    for h, stars in ((NOW - timedelta(days=6), 100), (NOW, 150)):
        db.conn.execute(
            "INSERT INTO repo_count_snapshot (repo_host_id, observed_at, stars, forks)"
            " VALUES (900, %s, %s, 0)",
            (h, stars),
        )
    w.nominate("search", "org-z/fresh", repo_host_id=995, at=NOW)
    out = w.enforce_policy(WatchPolicy(cap=10), NOW)
    assert out["stale"] == 4  # r1..r4; r0 (growth) and the pinned manual row stay
    out = w.enforce_policy(WatchPolicy(cap=2), NOW)
    assert out["over_cap"] == 1
    active = {r.full_name for r in w.active_rows()}
    assert active == {"org-z/pinned", "org-z/r0"}  # pinned first, then 7-day growth
    assert w.nominate("search", "org-z/r3", repo_host_id=903, at=NOW) == "reactivated"


# --- search sweeps -------------------------------------------------------------------------------
def test_m1_t24_search_sweep_splits_ranges_under_1000_cap(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    fake.search_pool = search_pool(
        2600, created_from=NOW - timedelta(days=6), span=timedelta(days=5)
    )
    c = connector(db, fake, tmp_path)
    res = SearchSweeper(c, db, cfg=SweepConfig(new_days=7, new_min_stars=20)).sweep("new")
    assert res.splits > 0 and res.truncated == 0 and res.failed_pages == 0
    assert Watchlist(db).counts()["active"] == 2600  # every repo found despite the cap
    assert all(page <= 10 for _q, page, _t in fake.search_queries)
    paged = [(q, t) for q, page, t in fake.search_queries if page > 1]
    assert paged and all(t <= 1000 for _q, t in paged)  # only slices under the cap are paged
    assert db.conn.execute(
        "SELECT DISTINCT source, source_ref, owner_type FROM watchlist"
    ).fetchall() == [("search", "search:new", "Organization")]
    ev = db.conn.execute("SELECT DISTINCT retention_class FROM evidence").fetchall()
    assert ev == [("person_level_24m",)]  # search items embed owner objects
    led = db.conn.execute(
        "SELECT units FROM github_budget_ledger WHERE resource = 'search'"
    ).fetchone()
    assert led[0] == res.pages
    # budget hard stop on the search bucket
    c2 = connector(db, fake, tmp_path, job=JobCaps({"search": 3}))
    res2 = SearchSweeper(c2, db).sweep("all")
    assert res2.budget_stop == "job_cap" and res2.pages == 3


# --- HN screen -----------------------------------------------------------------------------------
def _hn_http() -> httpx.Client:
    items = {
        9100001: {
            "id": 9100001,
            "type": "story",
            "by": "hnuser901",
            "time": 1790000000,
            "title": "Show HN: Repo-3",
            "url": "https://github.com/org-y/repo-3",
            "score": 5,
        },
        9100002: {
            "id": 9100002,
            "type": "story",
            "by": "hnuser902",
            "time": 1790000001,
            "title": "Show HN: a site",
            "url": "https://example.org/x",
            "score": 3,
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v0/showstories.json":
            return httpx.Response(200, json=list(items))
        iid = int(req.url.path.rsplit("/", 1)[1].split(".")[0])
        return httpx.Response(200, json=items[iid])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_m1_t24_hn_screen_show_hn_and_gharchive(capture_db, tmp_path):
    db = capture_db
    rows = [
        (1, "https://github.com/org-x/repo-1", "Show HN: Repo-1", "org-x/repo-1", False),
        (2, "https://github.com/org-x/repo-2", "Repo-2 released", "org-x/repo-2", False),
        (3, "https://example.org/a", "Not GitHub", None, False),
        (4, "https://github.com/org-y/dead", "Dead story", "org-y/dead", True),
    ]
    for iid, url, title, repo, dead in rows:
        db.conn.execute(
            "INSERT INTO hn_story (item_id, url, title, repo_full_name, dead, first_seen_at,"
            " last_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (iid, url, title, repo, dead, NOW - timedelta(hours=3), NOW - timedelta(hours=1)),
        )
    store = LocalSnapshotStore(tmp_path / "snap")
    hn = HNRanksConnector(
        store=store,
        pseudonymizer=None,
        http=_hn_http(),
        env={},
        limiter=TokenBucket(1000, burst=100),
        clock=lambda: NOW,
        evidence_sink=db.upsert_evidence,
    )
    w = Watchlist(db)
    res = hn_screen(db, w, hn=hn, now=NOW)
    assert res.stories == 2 and res.show_items_fetched == 2 and res.show_with_repo == 1
    assert res.raw_dropped == 2  # item JSON holds `by`: dropped right after parsing
    refs = dict(db.conn.execute("SELECT full_name, source_ref FROM watchlist").fetchall())
    assert refs == {
        "org-x/repo-1": "show_hn:1",
        "org-x/repo-2": "hn:2",
        "org-y/repo-3": "show_hn:9100001",
    }
    assert "hnuser9" not in dump_all_tables(db)
    again = hn_screen(db, w, hn=hn, now=NOW)
    assert again.show_items_fetched == 0  # seen items are not refetched
    # GH Archive nominations (control screen)
    for h in range(3):
        db.conn.execute(
            "INSERT INTO repo_hourly_activity (repo_host_id, hour, repo_name, stars_raw)"
            " VALUES (7000004, %s, 'org-y/repo-4', 5)",
            (NOW - timedelta(hours=h),),
        )
    assert gharchive_screen(db, w, min_stars=10, now=NOW) == 1
    assert db.conn.execute(
        "SELECT source FROM watchlist WHERE repo_host_id = 7000004"
    ).fetchone() == ("gharchive",)


# --- star history --------------------------------------------------------------------------------
def test_m1_t24_star_history_paging_day_labels_and_304(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    Watchlist(db).nominate("search", "org-x/repo-1", repo_host_id=7000001)
    clock = Clock()
    c = connector(db, fake, tmp_path, clock=clock)
    r = fetch_star_history(c, db, 7000001, "org-x/repo-1")
    assert r.pages == 1 and r.weeks == 6 and not r.complete
    req = fake.requests[0]
    assert req.url.params["per_page"] == "6" and req.url.params["page"] == "1"
    rows = db.conn.execute(
        "SELECT day, stars_net, is_partial, day_boundary_tz, week_label FROM repo_star_daily"
        " WHERE repo_host_id = 7000001 ORDER BY day"
    ).fetchall()
    assert rows[-1][0] == TODAY and rows[-1][2] is True  # today is still filling
    assert all(d <= TODAY for d, *_ in rows)  # future days of this week are not stored
    assert {x[3] for x in rows} == {DAY_BOUNDARY_NOTE}
    vals = series("burst")
    assert [x[1] for x in rows[-3:]] == vals[-3:]
    assert rows[0][4] == rows[0][0].isoformat()  # the endpoint's own label, verbatim
    # 304: free, re-read from the stored snapshot
    clock.t = NOW + timedelta(minutes=10)
    r2 = fetch_star_history(c, db, 7000001, "org-x/repo-1")
    assert r2.not_modified == 1 and r2.days_stored == len(rows)
    # full history back to the creation week, unix-timestamp labels give the same days
    fake.label = "unix"
    clock.t = NOW + timedelta(minutes=20)
    r3 = fetch_star_history(c, db, 7000001, "org-x/repo-1", per_page=4, max_pages=100)
    assert r3.complete and r3.pages == 3  # 11 weeks of data, 4 per page
    assert db.conn.execute(
        "SELECT sum(stars_net) FROM repo_star_daily WHERE repo_host_id = 7000001"
    ).fetchone() == (sum(vals),)
    assert db.conn.execute("SELECT count(*) FROM star_history_fetch").fetchone() == (3,)


# --- detection v1 --------------------------------------------------------------------------------
def snapshots(db: Any, host_id: int, start_stars: int, gain: int, *, hours: int = 60) -> None:
    """Hourly count snapshots ending at NOW; `gain` stars spread over the last 48 h."""
    for k in range(hours, -1, -1):
        t = NOW - timedelta(hours=k)
        s = start_stars + (0 if k >= 48 else round(gain * (48 - k) / 48))
        db.conn.execute(
            "INSERT INTO repo_count_snapshot (repo_host_id, observed_at, stars, forks)"
            " VALUES (%s, %s, %s, %s)",
            (host_id, t, s, 10 + (0 if k >= 48 else 5)),
        )


def watch(db: Any, fake: FakeGitHub, host_id: int) -> None:
    r = fake.repos[host_id]
    Watchlist(db).nominate(
        "search",
        r["full_name"],
        repo_host_id=host_id,
        node_id=r["node_id"],
        created_at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
        at=NOW - timedelta(days=10),
    )


def detector(db: Any, fake: FakeGitHub, tmp_path: Path, **kw: Any) -> DetectorV1:
    c = connector(db, fake, tmp_path)

    def fetch(host_id: int, name: str) -> None:
        fetch_star_history(c, db, host_id, name)

    return DetectorV1(db, fetch_history=fetch, **kw)


def test_m1_t24_detection_v1_true_positive_flat_short_and_rejections(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    fake.daily[7000004] = series("fake_jump")
    fake.search_pool = [
        {
            "id": 7000005,
            "node_id": "R_fake_7000005",
            "full_name": "org-y/repo-5",
            "stars": 500,
            "created_at": "2025-01-01T00:00:00Z",
        }
    ]
    for hid in (7000001, 7000002, 7000003, 7000004):
        watch(db, fake, hid)
    Watchlist(db).nominate("search", "org-y/repo-5", repo_host_id=7000005)
    snapshots(db, 7000001, 3600, 320)  # breakout
    snapshots(db, 7000002, 8900, 100)  # busy but flat (~50/day)
    snapshots(db, 7000003, 60, 200, hours=40)  # 5 days old, short baseline
    snapshots(db, 7000004, 100, 200)  # counts jump, star history disagrees
    snapshots(db, 7000005, 300, 150)  # no star history (endpoint 404)
    snapshots(db, 9999999, 0, 900)  # not on the watch list: ignored
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        det = detector(db, fake, tmp_path, run=run)
        res = det.detect(NOW)
    assert res.candidates == 5
    assert len(res.opened) == 2 and res.rejected_z == 1
    assert res.rejected_by_star_history == 1 and res.unconfirmed_no_star_history == 1
    cases = {c.repo_id: c for c in db.get_cases()}
    assert set(cases) == {"github:7000001", "github:7000003"}
    d1 = cases["github:7000001"].detection
    assert isinstance(d1, DetectionV1)
    assert d1.stars_48h == 320 and d1.baseline_quality == "full" and d1.z_score > 50
    assert d1.baseline_mean_48h == pytest.approx(10.0, abs=0.5)
    assert d1.coverage.reference_stars == 350 and d1.coverage.reference_source == (
        "github_star_history"
    )
    assert d1.data_sources == ["github_graphql_counts", "github_star_history"]
    assert d1.bot_filter.status == "unavailable" and d1.bot_filter.basis == "none"
    assert d1.bot_filter.confirmed is None and d1.day_boundary_tz == DAY_BOUNDARY_NOTE
    assert d1.window_hours_observed == 48.0
    assert cases["github:7000001"].opened_at == NOW + timedelta(hours=1)
    d3 = cases["github:7000003"].detection
    assert isinstance(d3, DetectionV1)
    # created 2026-09-20 (endpoint day): 09-20 (0 stars), 09-21, 09-22 precede the window
    assert d3.baseline_quality == "partial" and d3.baseline_days_covered == 3
    assert d3.window_hours_observed == 40.0
    # the stored case validates against the JSON Schema
    schema = json.loads((ROOT / "schemas" / "v0" / "case.schema.json").read_text())
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    for c in cases.values():
        assert not list(v.iter_errors(c.to_json_dict()))
    # idempotent: a re-run opens nothing and adds no agreement row
    n_agree = db.conn.execute("SELECT count(*) FROM detection_agreement").fetchone()
    res2 = detector(db, fake, tmp_path).detect(NOW)
    assert res2.opened == [] and res2.suppressed_by_cooldown == 2
    assert db.conn.execute("SELECT count(*) FROM detection_agreement").fetchone() == n_agree
    assert len(db.get_cases()) == 2


def test_m1_t24_detection_v1_agreement_with_velocity_v0_control(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    watch(db, fake, 7000001)
    snapshots(db, 7000001, 3600, 320)
    example = json.loads((ROOT / "schemas" / "v0" / "examples" / "case.example.json").read_text())
    v0_det = {
        k: example["detection"][k]
        for k in (
            "detected_hour",
            "stars_48h",
            "stars_48h_raw",
            "forks_48h",
            "baseline_mean_48h",
            "baseline_std_48h",
            "sigma_used",
            "z_score",
            "baseline_hours_covered",
            "baseline_quality",
            "threshold_min_stars",
            "threshold_sigma",
            "bot_filter_version",
            "coverage",
        )
    }
    v0_det["rule_version"] = "velocity-v0"
    v0_det["coverage"] = {**v0_det["coverage"], "source": "gharchive"}
    db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at) VALUES"
        " ('github:7000001', 'github', 7000001, 'org-x/repo-1', %s)",
        (NOW - timedelta(days=2),),
    )
    v0 = Case(
        id="case_00000000000000000777",
        repo_id="github:7000001",
        opened_at=NOW - timedelta(hours=5),
        trigger="velocity",
        status="live",
        detection=v0_det,
    )
    db.insert_case(v0)
    db.conn.execute(
        "INSERT INTO repo_hourly_activity (repo_host_id, hour, repo_name, stars_raw,"
        " stars_filtered) VALUES (7000001, %s, 'org-x/repo-1', 8, 7)",
        (NOW - timedelta(hours=3),),
    )
    res = detector(db, fake, tmp_path).detect(NOW)
    assert res.fired == 1 and res.opened == [] and res.suppressed_by_cooldown == 1
    row = db.conn.execute(
        "SELECT v1_case_id, v0_case_id, gharchive_stars_48h, v1_stars_48h FROM detection_agreement"
    ).fetchone()
    assert row == (None, "case_00000000000000000777", 7, 320)
    stats = agreement_stats(db, NOW - timedelta(days=30))
    assert stats["both"] == 1 and stats["v1_only"] == 0
    assert stats["median_gharchive_to_v1_ratio"] == pytest.approx(7 / 320, abs=1e-4)


# --- per-repo events (TM-33, CB-22, CB-23) -------------------------------------------------------
def events_setup(db: Any, fake: FakeGitHub, tmp_path: Path, clock: Clock) -> Any:
    watch(db, fake, 7000001)
    snapshots(db, 7000001, 3600, 320)
    res = detector(db, fake, tmp_path, events_enabled=True).detect(NOW)
    assert len(res.opened) == 1
    det = db.get_cases()[0].detection
    assert isinstance(det, DetectionV1)
    assert det.bot_filter.status == "pending" and det.bot_filter.basis == "repo_events"
    page = json.loads((ROOT / "tests" / "fixtures" / "github" / "events_page.json").read_text())
    for e in page:  # move the fixture page into the detection window
        e["created_at"] = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gen = make_events(
        150,
        repo_id=7000001,
        name="org-x/repo-1",
        newest=NOW - timedelta(hours=2),
        first_id=1000,
        step_s=300,
        login=lambda i: "helper-app[bot]" if i % 50 == 0 else f"ghuser{i:03d}",
    )
    fake.events["org-x/repo-1"] = page + gen
    pz = Pseudonymizer(TEST_KEY)
    return connector(
        db,
        fake,
        tmp_path,
        clock=clock,
        cls=GitHubRepoEventsConnector,
        pseudonymizer=pz,
        env=EVENTS_ON,
    )


def test_m1_t24_repo_events_pseudonymized_minimised_and_bot_filter_applied(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    clock = Clock()
    ev = events_setup(db, fake, tmp_path, clock)
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        st = RepoEventsPoller(ev, db, run=run).poll_due(NOW)
    assert st.targets == 1 and st.polled == 1 and st.pages == 2 and st.overflow == 0
    kinds = dict(
        db.conn.execute("SELECT event_type, count(*) FROM repo_event_actor GROUP BY 1").fetchall()
    )
    assert kinds == {"WatchEvent": 153, "ForkEvent": 1}  # CB-23: Push/Issues/PR events dropped
    assert st.events_kept == 154
    text = dump_all_tables(db)
    assert "ghuser" not in text and "helper-app" not in text  # no raw handle anywhere
    assert db.conn.execute(
        "SELECT count(*) FROM repo_event_actor WHERE is_bot AND actor_pseudonym IS NULL"
    ).fetchone() == (4,)  # 3 generated bot stars + 1 fixture bot star; logins never hashed
    # raw events bytes dropped right after parsing (hash + URL kept, tombstones written)
    evs = db.conn.execute(
        "SELECT deletion_state, retention_class, content_hash FROM evidence"
        " WHERE source = 'github_events'"
    ).fetchall()
    assert len(evs) == 2 and {(e[0], e[1]) for e in evs} == {("raw_dropped", "person_level_30d")}
    assert not any(ev.store.exists(e[2]) for e in evs)
    assert db.conn.execute(
        "SELECT count(*) FROM deletion_log WHERE action = 'raw_dropped'"
    ).fetchone() == (2,)
    det = db.get_cases()[0].detection
    assert isinstance(det, DetectionV1)
    bf = det.bot_filter
    assert bf.status == "applied" and bf.basis == "repo_events" and bf.layers == ["login_rules"]
    assert bf.stars_bot == 4 and bf.stars_seen == 149 and bf.stars_lockstep is None
    assert bf.confirmed is True  # 350 star-history stars - 4 bot stars >= 100
    assert bf.coverage_ratio == pytest.approx(149 / 350, abs=1e-4)
    assert db.conn.execute("SELECT sum(stars_seen) FROM repo_event_daily_agg").fetchone()[0] > 0


def test_m1_t24_repo_events_etag_poll_interval_and_overflow(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    clock = Clock()
    ev = events_setup(db, fake, tmp_path, clock)
    poller = RepoEventsPoller(ev, db, cfg=EventsConfig(case_interval_min=15))
    assert poller.poll_due(NOW).polled == 1
    assert poller.poll_due(NOW + timedelta(minutes=5)).due == 0  # not before 15 min
    clock.t = NOW + timedelta(minutes=16)
    n_req = len(fake.requests)
    st = poller.poll_due(clock.t)
    assert st.polled == 1 and st.not_modified == 1 and st.events_new == 0
    assert len(fake.requests) == n_req + 1 and fake.requests[-1].headers.get("If-None-Match")
    led = db.conn.execute(
        "SELECT sum(not_modified) FROM github_budget_ledger WHERE resource = 'core'"
    ).fetchone()
    assert led == (1,)
    # GitHub asks for a longer interval: respected
    fake.poll_interval = 1800
    clock.t = NOW + timedelta(minutes=32)
    poller.poll_due(clock.t)  # 304 again, records X-Poll-Interval 1800
    assert poller.poll_due(NOW + timedelta(minutes=50)).due == 0
    # 350 new events since the last poll: the 300-event window overflowed
    new = make_events(
        350,
        repo_id=7000001,
        name="org-x/repo-1",
        newest=NOW + timedelta(hours=1),
        first_id=5000,
        step_s=5,
    )
    fake.events["org-x/repo-1"] = new + fake.events["org-x/repo-1"]
    clock.t = NOW + timedelta(hours=2)
    st = poller.poll_due(clock.t)
    assert st.pages == 3 and st.overflow == 1 and st.events_new == 300
    det = db.get_cases()[0].detection
    # the unseen gap starts at the previous poll's newest event (NOW - 1 h), inside the case
    # window, so the case's bot-filter record says its event coverage may be incomplete
    assert isinstance(det, DetectionV1) and det.bot_filter.window_overflow is True


# --- retention (CB-22) ---------------------------------------------------------------------------
def test_m1_t24_cb22_repo_event_rows_and_raw_purged_after_30_days(capture_db, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    pz = Pseudonymizer(TEST_KEY)
    p_old, p_new = pz.pseudonym("ghuser801", "github"), pz.pseudonym("ghuser802", "github")
    for p, age in ((p_old, 40), (p_new, 5)):
        db.conn.execute(
            "INSERT INTO repo_event_actor (repo_host_id, event_id, event_type, actor_pseudonym,"
            " is_bot, created_at, observed_at) VALUES (7000001, %s, 'WatchEvent', %s, false,"
            " %s, %s)",
            (p, p, NOW - timedelta(days=age), NOW - timedelta(days=age)),
        )
    from pigtail.capture.models import Evidence, evidence_id

    def evidence(data: bytes, cls: str, age: int) -> str:
        meta = SnapshotMeta(
            source="github_events",
            url=f"https://api.github.com/x/{cls}",
            fetched_at=NOW - timedelta(days=age),
            collector_version="t/1",
            terms_basis="TM-33",
        )
        h = store.put(data, meta)
        db.upsert_evidence(
            Evidence(
                id=evidence_id("github_events", meta.url, h),
                source="github_events",
                url=meta.url,
                fetched_at=meta.fetched_at,
                content_hash=h,
                snapshot_ref=store.ref(h),
                reliability="high",
                terms_basis="TM-33",
                retention_class=cls,  # type: ignore[arg-type]
                collector_version="t/1",
                case_id=None,
                repo_id=None,
            )
        )
        return h

    h30 = evidence(b"[1]", "person_level_30d", 40)
    h24 = evidence(b"[2]", "person_level_24m", 40)
    rep = purge(db, store, cfg=RetentionConfig(), now=NOW)
    assert rep.person_rows_deleted["repo_event_actor"] == 1
    assert rep.person_level_30d_hashes_dropped == 1 and not store.exists(h30)
    assert store.exists(h24)  # 24-month class untouched at 40 days
    left = db.conn.execute("SELECT actor_pseudonym FROM repo_event_actor").fetchall()
    assert left == [(p_new,)]
    # erasure by pseudonym reaches the table too (CB-08)
    log = DeletionLog(db, "erasure")
    assert delete_person_rows(db, PERSON_TABLES, log, pseudonyms=[p_new])["repo_event_actor"] == 1
    # a second purge is a no-op
    rep2 = purge(db, store, cfg=RetentionConfig(), now=NOW)
    assert rep2.person_level_30d_hashes_dropped == 0 and not any(rep2.person_rows_deleted.values())


def test_m1_t24_cb22_retention_setting_capped_at_30_days():
    from pigtail.config import Settings

    assert Settings.from_env({}).github_events_retention_days == 16
    assert (
        Settings.from_env({"GITHUB_EVENTS_RETENTION_DAYS": "7"}).github_events_retention_days == 7
    )
    with pytest.raises(ValueError):
        Settings.from_env({"GITHUB_EVENTS_RETENTION_DAYS": "31"})


# --- CLI gates -----------------------------------------------------------------------------------
def test_m1_t24_cli_gates_token_and_events(pg_url, tmp_path, monkeypatch, capsys):
    for k in ("GITHUB_TOKEN", "PIGTAIL_ENABLE_GITHUB_EVENTS", ADR022_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PSEUDONYM_KEY", TEST_KEY)
    for cmd in (
        ["watchlist-counts"],
        ["search-sweep"],
        ["star-history", "--candidates"],
        ["repo-events"],
        ["detect-v1"],
    ):
        assert main(["capture", "github", *cmd]) == 2, cmd
        assert "GITHUB_TOKEN is not set" in capsys.readouterr().err
    assert main(["capture", "github", "detect-v1", "--no-fetch"]) == 0  # stored data only
    assert main(["capture", "github", "watch-add", "--repo", "org-x/repo-1"]) == 0
    assert main(["capture", "github", "budget"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-token")
    assert main(["capture", "github", "repo-events"]) == 2  # off by default
    assert "PIGTAIL_ENABLE_GITHUB_EVENTS" in capsys.readouterr().err
    monkeypatch.setenv("PIGTAIL_ENABLE_GITHUB_EVENTS", "1")
    assert main(["capture", "github", "repo-events"]) == 2  # held by ADR-022
    assert ADR022_ENV in capsys.readouterr().err
