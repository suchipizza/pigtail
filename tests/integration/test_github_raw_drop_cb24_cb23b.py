"""CB-24 (raw GitHub search pages dropped right after parsing; only the owner type is kept) and
CB-23b (unparseable per-repo events / search pages dropped at once, failure counted without
content), on Postgres with the synthetic GitHub fake (tests/github_fake.py; fake orgs org-s,
org-x; fake users ghuserNNN)."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from pigtail.capture.github_screens import SearchSweeper, SweepConfig
from pigtail.capture.github_watch import Watchlist
from pigtail.capture.repo_events import EventsConfig, RepoEventsPoller
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.github import events_url, full_url
from tests.github_fake import NOW, FakeGitHub, search_pool
from tests.integration.test_github_detection_m1t24 import (
    Clock,
    connector,
    dump_all_tables,
    events_setup,
)

pytestmark = pytest.mark.db


def test_cb24_search_pages_raw_dropped_after_parse_owner_type_only(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    fake.search_pool = search_pool(
        150, created_from=NOW - timedelta(days=3), span=timedelta(days=2)
    )
    c = connector(db, fake, tmp_path)
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = SearchSweeper(c, db, cfg=SweepConfig(new_days=7), run=run).sweep("new")
    assert res.pages == 2 and res.raw_dropped == 2 and res.parse_failed == 0
    assert Watchlist(db).counts()["active"] == 150  # every hit still nominated
    evs = db.conn.execute(
        "SELECT deletion_state, retention_class, content_hash, url FROM evidence"
        " WHERE source = 'github'"
    ).fetchall()
    assert len(evs) == 2
    assert {(e[0], e[1]) for e in evs} == {("raw_dropped", "person_level_24m")}
    assert not any(c.store.exists(e[2]) for e in evs)  # bytes gone, hash + URL kept
    assert all(e[3].startswith("https://api.github.com/search/repositories") for e in evs)
    logs = db.conn.execute(
        "SELECT reason, content_hash, evidence_id FROM deletion_log WHERE action = 'raw_dropped'"
    ).fetchall()
    assert {r[1] for r in logs} == {e[2] for e in evs} and all(r[0] == "retention" for r in logs)
    assert run.counts["search.raw_dropped"] == 2
    # only the owner *type* survives from the owner objects
    assert db.conn.execute("SELECT DISTINCT owner_type FROM watchlist").fetchall() == [
        ("Organization",)
    ]
    assert '"login"' not in dump_all_tables(db)


def test_cb23b_unparseable_search_page_dropped_and_counted(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    fake.search_pool = search_pool(30, created_from=NOW - timedelta(days=3), span=timedelta(days=2))
    body = b'{"total_count": 30, "items": [{"id": 1, "owner": {"login": "ghuser001", "ty'
    fake.script = [httpx.Response(200, content=body, headers=fake._headers("search"))]
    c = connector(db, fake, tmp_path)
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = SearchSweeper(c, db, run=run).sweep("new")
    assert res.parse_failed == 1 and res.raw_dropped == 1 and res.pages == 0
    ev = db.conn.execute(
        "SELECT deletion_state, content_hash FROM evidence WHERE source = 'github'"
    ).fetchone()
    assert ev[0] == "raw_dropped" and not c.store.exists(ev[1])
    assert run.counts["github.parse_failed"] == 1
    assert run.counts["github.parse_failed.JSONDecodeError"] == 1
    assert "ghuser" not in json.dumps(run.counts) and "ghuser" not in dump_all_tables(db)


def test_cb23b_unparseable_events_page_dropped_at_once_and_refetched(capture_db, tmp_path):
    db = capture_db
    fake = FakeGitHub()
    clock = Clock()
    ev = events_setup(db, fake, tmp_path, clock)
    bad = b'[{"id": "1", "type": "WatchEvent", "actor": {"login": "ghuser999"}, "repo": {"id'
    hdrs = fake._headers("core", {"ETag": '"bad-page"', "X-Poll-Interval": "60"})
    fake.script = [httpx.Response(200, content=bad, headers=hdrs)]
    poller = RepoEventsPoller(ev, db, cfg=EventsConfig(case_interval_min=15))
    with RunRecorder("t", {}, sink=db.upsert_run, detect_commit=False) as run:
        poller.run = run
        st = poller.poll_due(NOW)
    assert st.polled == 1 and st.parse_failed == 1 and st.raw_dropped == 1 and st.events_kept == 0
    row = db.conn.execute(
        "SELECT deletion_state, retention_class, content_hash FROM evidence"
        " WHERE source = 'github_events'"
    ).fetchone()
    assert row[:2] == ("raw_dropped", "person_level_30d") and not ev.store.exists(row[2])
    assert db.conn.execute(
        "SELECT reason, content_hash FROM deletion_log WHERE action = 'raw_dropped'"
    ).fetchall() == [("retention", row[2])]
    assert db.conn.execute("SELECT count(*) FROM repo_event_actor").fetchone() == (0,)
    counts = run.counts
    assert counts["repo_events.parse_failed"] == 1 and counts["repo_events.raw_dropped"] == 1
    assert counts["github_events.parse_failed.JSONDecodeError"] == 1
    assert "ghuser" not in json.dumps(counts) and "ghuser" not in dump_all_tables(db)
    # the ETag of the bad page is forgotten: the next poll downloads the page again
    key = full_url(events_url("org-x/repo-1"), {"per_page": 100, "page": 1})
    assert ev.cache.get(key).etag is None
    clock.t = NOW + timedelta(minutes=16)
    st2 = poller.poll_due(clock.t)
    assert st2.parse_failed == 0 and st2.events_kept > 0
    assert fake.requests[-st2.pages].headers.get("If-None-Match") is None
