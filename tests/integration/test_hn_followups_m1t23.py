"""M1-T23 (HN follow-ups) and the HN side of CB-23b on Postgres.

- deletion sync clears `hn_story` title/url of stories deleted upstream (rank history stays);
- a repo opt-out by name reaches HN data about repos that are not in `repos`;
- unparseable HN pages/items are dropped at once and only counted.

Synthetic data only (tests/fixtures/hn/, fake usernames hnuserNNN / hnfillNNN, fake repos).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest

from pigtail.capture.github_watch import Watchlist
from pigtail.capture.hn_ranks import RankPoller
from pigtail.capture.mentions import RepoSuppressed, capture_hn_mentions
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.cli import main
from pigtail.connectors.hn import HNAlgoliaConnector, HNFirebaseConnector
from pigtail.connectors.hn_ranks import HNRanksConnector
from pigtail.privacy import requests, suppression
from pigtail.privacy.deletion_sync import HNDeletionSource, sync
from tests.hn_fake import FakeHN
from tests.integration.test_hn_capture import (
    HANDLE_MARKERS,
    NOW,
    ON,
    add_repo_and_case,
    conn_kw,
    dump_all_tables,
)

pytestmark = pytest.mark.db


def poll(db: Any, store: Any, fake: FakeHN, *, items: int = 30, clock: Any = lambda: NOW) -> Any:
    conn = HNRanksConnector(
        pseudonymizer=None,
        env={},
        suppression=suppression.load(db),
        **conn_kw(store, fake, db, clock=clock),
    )
    with RunRecorder("capture.hn_ranks", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = RankPoller(conn, db, run=run, items=items).poll_once()
    return res, run


# --- M1-T23: deletion sync clears story titles/urls ---------------------------------------------
def test_m1_t23_rank_stories_tracked_and_cleared_when_deleted_upstream(capture_db, tmp_path, pz):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()
    poll(db, store, fake, items=4)
    tracked = db.conn.execute(
        "SELECT count(*), bool_and(author_pseudonym IS NULL), min(next_check_at)"
        " FROM upstream_items WHERE platform = 'hn'"
    ).fetchone()
    assert tracked == (4, True, NOW + timedelta(days=30))  # no open case: monthly re-check
    # upstream: the org-b story is deleted, the essay killed (dead)
    fake.items[9000004] = {"id": 9000004, "type": "story", "deleted": True, "time": 1790000100}
    fake.items[9000002] = {**(fake.items[9000002] or {}), "dead": True}
    fb = HNFirebaseConnector(
        store=store, pseudonymizer=pz, http=fake.client(), enabled=False, env={}
    )
    with RunRecorder("privacy.deletion_sync", {}, sink=db.upsert_run, detect_commit=False) as run:
        rep = sync(db, store, HNDeletionSource(fb), now=NOW + timedelta(days=31), run=run)
    assert rep.gone == {"deleted": 1, "dead": 1} and rep.items_acted == 2
    rows = dict(
        (r[0], r[1:])
        for r in db.conn.execute(
            "SELECT item_id, title, url, repo_full_name, content_cleared_at IS NOT NULL"
            " FROM hn_story ORDER BY item_id"
        )
    )
    assert rows[9000004] == (None, None, "org-b/repo-2", True)  # project-level link kept
    assert rows[9000002] == (None, None, None, True)
    assert rows[9000001][0] == "Show HN: Repo-1, a synthetic tool" and rows[9000001][3] is False
    ev = db.conn.execute(
        "SELECT deletion_state FROM evidence WHERE url LIKE '%%/item/9000004.json'"
    ).fetchall()
    assert ev == [("deleted_upstream",)]  # hash kept, overrides raw_dropped (ADR-031.3)
    assert db.conn.execute(
        "SELECT reason, rows_affected FROM deletion_log WHERE action = 'fields_cleared'"
        " AND target = 'hn_story'"
    ).fetchall() == [("deleted_upstream", 2)]
    # rank history is untouched (front-page minutes stay computable)
    assert db.conn.execute(
        "SELECT count(*) FROM hn_rank_observation WHERE item_id = 9000004"
    ).fetchone() == (1,)
    # a later poll that still sees the old content never refills the cleared title
    fake.items[9000004] = {
        "id": 9000004,
        "type": "story",
        "by": "hnuser002",
        "title": "Repo-2: synthetic project",
        "url": "https://github.com/org-b/repo-2",
        "time": 1790000100,
    }
    poll(db, store, fake, items=4, clock=lambda: NOW + timedelta(days=31, minutes=5))
    assert db.conn.execute(
        "SELECT title, url FROM hn_story WHERE item_id = 9000004"
    ).fetchone() == (None, None)
    assert not any(m in dump_all_tables(db) for m in HANDLE_MARKERS)


# --- M1-T23: opt-out by name for repos not in `repos` ------------------------------------------
def test_m1_t23_name_optout_suppresses_hn_for_repo_not_in_repos(capture_db, tmp_path, pz):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    assert db.conn.execute("SELECT count(*) FROM repos").fetchone() == (0,)
    res = requests.optout_repo_name(db, store, platform="github", full_name="Org-B/Repo-2.git")
    assert res.outcome == "completed" and res.counts["suppression_added"] == 1
    entries = suppression.entries(db)
    assert [e["kind"] for e in entries] == ["repo_name"]
    assert entries[0]["value"] == suppression.repo_name_key("org-b/repo-2")
    assert "repo-2" not in json.dumps(entries, default=str)  # the name itself is not stored
    # rank poller: the story's metadata is not stored (id and rank only), others are
    pres, _ = poll(db, store, FakeHN(), items=4)
    assert pres.stories_suppressed == 1
    assert db.conn.execute("SELECT 1 FROM hn_story WHERE item_id = 9000004").fetchone() is None
    assert db.conn.execute("SELECT 1 FROM hn_story WHERE item_id = 9000001").fetchone()
    assert db.conn.execute(
        "SELECT count(*) FROM hn_rank_observation WHERE item_id = 9000004"
    ).fetchone() == (1,)
    # mention capture refuses before any request; the watch list skips it
    fake = FakeHN()
    kw = conn_kw(store, fake, db, pseudonymizer=pz, env=ON, suppression=suppression.load(db))
    with pytest.raises(RepoSuppressed):
        capture_hn_mentions(HNAlgoliaConnector(**kw), db, "org-b/repo-2")
    assert fake.requests == []
    w = Watchlist(db, suppression.load(db))
    assert w.nominate("hn", "Org-B/Repo-2", source_ref="hn:9000004") == "skipped"
    assert w.nominate("hn", "org-a/repo-1", source_ref="hn:9000001") == "added"


def test_m1_t23_name_optout_purges_existing_hn_data_and_reapplies(capture_db, tmp_path, pz):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()
    poll(db, store, fake, items=4)
    kw = conn_kw(store, fake, db, pseudonymizer=pz, env=ON)
    capture_hn_mentions(
        HNAlgoliaConnector(**kw), db, "org-a/repo-1", firebase=HNFirebaseConnector(**kw)
    )
    Watchlist(db).nominate("hn", "org-a/repo-1", source_ref="hn:9000001")
    mention_evs = db.conn.execute(
        "SELECT DISTINCT e.id, e.content_hash FROM hn_mention m JOIN evidence e"
        " ON e.id IN (m.evidence_id, m.item_evidence_id)"
    ).fetchall()
    assert mention_evs and all(store.exists(h) for _i, h in mention_evs)
    assert db.conn.execute("SELECT count(*) FROM repos").fetchone() == (0,)  # not tracked
    res = requests.optout_repo_name(db, store, platform="github", full_name="org-a/repo-1")
    c = res.counts
    assert c["names_matched"] == 1 and c["mention_rows_deleted"] == 5
    assert c["story_rows_cleared"] == 1 and c["watchlist_deactivated"] == 1
    assert c["evidence_deleted"] == len(mention_evs)
    assert db.conn.execute("SELECT count(*) FROM hn_mention").fetchone() == (0,)
    assert not any(store.exists(h) for _i, h in mention_evs)
    story = db.conn.execute(
        "SELECT title, url, repo_full_name, repo_id FROM hn_story WHERE item_id = 9000001"
    ).fetchone()
    assert story == (None, None, None, None)
    assert db.conn.execute(
        "SELECT active, deactivated_reason FROM watchlist WHERE full_name = 'org-a/repo-1'"
    ).fetchone() == (False, "opted_out")
    other = db.conn.execute("SELECT title FROM hn_story WHERE item_id = 9000004").fetchone()
    assert other == ("Repo-2: synthetic project",)  # other repos untouched
    log = dict(
        db.conn.execute(
            "SELECT target, sum(rows_affected) FROM deletion_log WHERE reason = 'objection'"
            " AND action IN ('rows_deleted', 'fields_cleared') GROUP BY 1"
        ).fetchall()
    )
    assert log == {"hn_mention": 5, "hn_story": 1}
    # re-applying the list (e.g. after a restore) is idempotent: nothing more to remove
    totals = requests.reapply_refusals(db, store, pz)
    for k in ("mention_rows_deleted", "story_rows_cleared", "watchlist_deactivated"):
        assert totals[f"repo_name_{k}"] == 0


def test_m1_t23_name_optout_of_a_tracked_repo_also_purges_by_id(capture_db, tmp_path):
    db = capture_db
    add_repo_and_case(db)
    store = LocalSnapshotStore(tmp_path / "snap")
    poll(db, store, FakeHN(), items=4)
    res = requests.optout_repo(db, store, platform="github", repo_key="github:1000001")
    assert res.counts["name_suppression_added"] == 1 and res.counts["cases_deleted"] == 1
    s = suppression.load(db)
    assert "github:1000001" in s.repos and s.name_suppressed("github.com/org-a/repo-1")
    assert db.conn.execute(
        "SELECT title, repo_full_name FROM hn_story WHERE item_id = 9000001"
    ).fetchone() == (None, None)


def test_m1_t23_cli_optout_by_name_and_remove(capture_db, pg_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", "test-key-not-secret-0123456789")
    assert main(["privacy", "optout", "add", "--platform", "github", "--repo", "org-z/new"]) == 0
    capsys.readouterr()
    run = capture_db.conn.execute(
        "SELECT config FROM runs WHERE job = 'privacy.optout'"
    ).fetchone()[0]
    assert run == {"platform": "github", "kind": "repo_name"}  # no name in the run record
    assert suppression.load(capture_db).name_suppressed("org-z/new")
    assert main(["privacy", "optout", "remove", "--platform", "github", "--repo", "org-z/new"]) == 0
    assert json.loads(capsys.readouterr().out) == {"removed": True}
    assert not suppression.load(capture_db)


# --- CB-23b: unparseable HN pages and items -------------------------------------------------------
def test_cb23b_unparseable_algolia_page_and_item_dropped_at_once(capture_db, tmp_path, pz):
    db = capture_db
    add_repo_and_case(db)
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "hn.algolia.com" and req.url.params.get("query") == "org-a/repo-1":
            return httpx.Response(200, content=b'{"hits": [{"objectID": "9000012", "author": "hnu')
        if req.url.path == "/v0/item/9000011.json":
            return httpx.Response(200, content=b'{"id": 9000011, "by": "hnuser004", "text"')
        return fake(req)

    kw = conn_kw(store, fake, db, pseudonymizer=pz, env=ON)
    kw["http"] = httpx.Client(transport=httpx.MockTransport(handler))
    with RunRecorder("capture.mentions", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = capture_hn_mentions(
            HNAlgoliaConnector(**kw), db, "org-a/repo-1", firebase=HNFirebaseConnector(**kw),
            run=run,
        )  # fmt: skip
    assert res.items_failed == 1  # the malformed item; the rest was captured
    bad = db.conn.execute(
        "SELECT source, deletion_state, content_hash FROM evidence WHERE deletion_state <>"
        " 'present' ORDER BY source"
    ).fetchall()
    assert [(b[0], b[1]) for b in bad] == [
        ("hn_algolia", "raw_dropped"),
        ("hn_firebase", "raw_dropped"),
    ]
    assert not any(store.exists(b[2]) for b in bad)
    assert db.conn.execute(
        "SELECT count(*) FROM deletion_log WHERE action = 'raw_dropped' AND reason = 'retention'"
    ).fetchone() == (2,)
    counts, err = db.conn.execute(
        "SELECT counts, error FROM runs WHERE id = %s", (run.id,)
    ).fetchone()
    assert counts["hn_algolia.parse_failed"] == 1 and counts["hn_firebase.parse_failed"] == 1
    assert counts["hn_firebase.parse_failed.JSONDecodeError"] == 1 and err is None
    assert "hnu" not in json.dumps(counts)  # counts only, never content


def test_cb23b_unparseable_rank_item_counted_and_dropped(capture_db, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v0/item/9000004.json":
            return httpx.Response(200, content=b'{"id": 9000004, "by": "hnuser002", "tit')
        return fake(req)

    conn = HNRanksConnector(pseudonymizer=None, env={}, **conn_kw(store, fake, db))
    conn.http = httpx.Client(transport=httpx.MockTransport(handler))
    with RunRecorder("capture.hn_ranks", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = RankPoller(conn, db, run=run, items=4).poll_once()
    assert res.parse_failed == 1 and res.raw_dropped == 4
    assert run.counts["hn_ranks.parse_failed"] == 1
    assert run.counts["hn_ranks.parse_failed.JSONDecodeError"] == 1
    ev = db.conn.execute(
        "SELECT deletion_state, content_hash FROM evidence WHERE url LIKE '%%/item/9000004.json'"
    ).fetchone()
    assert ev[0] == "raw_dropped" and not store.exists(ev[1])
