"""M1-T24 on Postgres, per-repo collectors kept after M11 (ADR-047.6): star history, per-repo
events for open cases, 30-day retention, CLI gates (ADR-032; TM-33; CB-22, CB-23). The watch
list, search sweeps, screens and detection v1 were removed in M11 (git tag
`archive/global-collection`); their tests went with them.

Synthetic data only: tests/github_fake.py and tests/fixtures/github/ (fake repos org-x/repo-n,
org-y/repo-n; fake users ghuserNNN, helper-app[bot]).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pigtail.capture.models import (
    BotFilterStatus,
    Case,
    Coverage,
    DetectionV1,
    Repo,
    case_id,
    repo_id,
)
from pigtail.capture.repo_events import EventsConfig, RepoEventsPoller
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta
from pigtail.capture.star_history import DAY_BOUNDARY_NOTE, due_case_repos, fetch_star_history
from pigtail.cli import main
from pigtail.connectors.base import ADR022_ENV, TokenBucket
from pigtail.connectors.github import (
    GitHubConnector,
    GitHubRepoEventsConnector,
    PostgresCache,
)
from pigtail.connectors.github_budget import Budget, JobCaps, PostgresLedger
from pigtail.privacy.deletion import PERSON_TABLES, DeletionLog, delete_person_rows
from pigtail.privacy.retention import RetentionConfig, purge
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY
from tests.github_fake import NOW, TODAY, TOKEN, FakeGitHub, make_events, series

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


def seed_v1_case(db: Any, host_id: int = 7000001, name: str = "org-x/repo-1") -> Case:
    """A live case as detection v1 used to open it (bot filter pending): cases it opened are
    kept after M11 and per-repo events still fill their bot-filter block."""
    rid = repo_id("github", host_id)
    db.upsert_repo(Repo(id=rid, host="github", host_id=host_id, full_name=name,
                        first_seen_at=NOW - timedelta(days=10)))  # fmt: skip
    det = DetectionV1(
        rule_version="detection-v1",
        detected_hour=NOW,
        stars_48h=320,
        stars_48h_raw=320,
        forks_48h=5,
        baseline_mean_48h=10.0,
        baseline_std_48h=2.0,
        sigma_used=3.2,
        z_score=97.0,
        baseline_hours_covered=720,
        baseline_quality="full",
        threshold_min_stars=100,
        threshold_sigma=3.0,
        bot_filter_version="bot-filter-v0",
        coverage=Coverage(
            source="github_graphql_counts",
            window_start=NOW - timedelta(hours=48),
            window_end=NOW,
            observed_stars=320,
            reference_stars=350,
            reference_source="github_star_history",
            ratio=round(320 / 350, 4),
        ),
        data_sources=["github_graphql_counts", "github_star_history"],
        window_hours_observed=48.0,
        baseline_source="github_star_history",
        baseline_days_covered=30,
        day_boundary_tz=DAY_BOUNDARY_NOTE,
        bot_filter=BotFilterStatus(status="pending", basis="repo_events", version="bot-filter-v0"),
    )
    c = Case(id=case_id(rid, "velocity", NOW), repo_id=rid, opened_at=NOW, trigger="velocity",
             status="live", detection=det)  # fmt: skip
    db.insert_case(c)
    schema = json.loads((ROOT / "schemas" / "v0" / "case.schema.json").read_text())
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    assert not list(v.iter_errors(c.to_json_dict()))  # still a valid v0 case record
    return c


# --- migrations ----------------------------------------------------------------------------------
def test_m1_t24_migration_tables_and_constraints(capture_db):
    names = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {
        "github_budget_ledger",
        "github_http_cache",
        "repo_star_daily",
        "star_history_fetch",
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
    cols = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN"
            " ('repo_star_daily', 'repo_event_daily_agg')"
        )
    }
    assert not cols & {"login", "actor", "owner_login", "actor_pseudonym"}


# --- star history --------------------------------------------------------------------------------
def test_m1_t24_star_history_paging_day_labels_and_304(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
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


def test_m11_star_history_cases_queue_is_open_cases_only(capture_db, tmp_path):
    """M11: `star-history --cases` replaces the watch-list queue: repos of open cases whose
    series was never fetched or is older than the refresh interval, and nothing else."""
    db = capture_db
    seed_v1_case(db)
    db.upsert_repo(Repo(id="github:7000002", host="github", host_id=7000002,
                        full_name="org-x/repo-2", first_seen_at=NOW))  # fmt: skip
    closed = Case(id=case_id("github:7000002", "manual", NOW), repo_id="github:7000002",
                  opened_at=NOW, trigger="manual", status="closed", detection=None)  # fmt: skip
    db.insert_case(closed)
    assert due_case_repos(db, now=NOW) == [
        (7000001, "org-x/repo-1")
    ]  # the closed case's repo is not due
    c = connector(db, FakeGitHub(), tmp_path)
    fetch_star_history(c, db, 7000001, "org-x/repo-1")
    assert due_case_repos(db, now=NOW) == []  # fetched just now
    later = NOW + timedelta(seconds=1)
    assert due_case_repos(db, refresh=timedelta(0), now=later) == [(7000001, "org-x/repo-1")]


# --- per-repo events (TM-33, CB-22, CB-23) -------------------------------------------------------
def events_setup(db: Any, fake: FakeGitHub, tmp_path: Path, clock: Clock) -> Any:
    seed_v1_case(db)
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
    for ok in ("7", "17", "30"):  # ADR-038: default 16, ceiling 30
        assert Settings.from_env(
            {"GITHUB_EVENTS_RETENTION_DAYS": ok}
        ).github_events_retention_days == int(ok)
    with pytest.raises(ValueError):
        Settings.from_env({"GITHUB_EVENTS_RETENTION_DAYS": "31"})


# --- CLI gates -----------------------------------------------------------------------------------
def test_m1_t24_cli_gates_token_and_events(pg_url, tmp_path, monkeypatch, capsys):
    for k in ("GITHUB_TOKEN", "PIGTAIL_ENABLE_GITHUB_EVENTS", ADR022_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PSEUDONYM_KEY", TEST_KEY)
    for cmd in (["star-history", "--cases"], ["star-history", "--repo", "a/b"], ["repo-events"]):
        assert main(["capture", "github", *cmd]) == 2, cmd
        assert "GITHUB_TOKEN is not set" in capsys.readouterr().err
    assert main(["capture", "github", "budget"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-token")
    assert main(["capture", "github", "star-history", "--repo", "org-q/unknown"]) == 2
    assert "unknown repo id" in capsys.readouterr().err  # resolved from `repos` only
    assert main(["capture", "github", "repo-events"]) == 2  # off by default
    assert "PIGTAIL_ENABLE_GITHUB_EVENTS" in capsys.readouterr().err
    monkeypatch.setenv("PIGTAIL_ENABLE_GITHUB_EVENTS", "1")
    assert main(["capture", "github", "repo-events"]) == 2  # held by ADR-022
    assert ADR022_ENV in capsys.readouterr().err


def test_m11_removed_github_commands_are_gone(capsys):
    """M11 acceptance: no CLI command for the watch list, sweeps, screens, detection v1 or the
    settle-lag collection remains."""
    for cmd in ("watch-add", "watchlist-counts", "search-sweep", "hn-screen", "detect-v1",
                "settle-lag"):  # fmt: skip
        with pytest.raises(SystemExit) as e:
            main(["capture", "github", cmd])
        assert e.value.code == 2, cmd
        assert "invalid choice" in capsys.readouterr().err
    for cmd in (["capture", "scan"], ["capture", "backfill-gharchive"]):
        with pytest.raises(SystemExit):
            main(cmd)
        assert "invalid choice" in capsys.readouterr().err
