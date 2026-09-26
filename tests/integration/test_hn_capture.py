"""M1-T14 rank poller, M1-T4 mention capture and CB-02 deletion sync on Postgres.

Synthetic data only (tests/fixtures/hn/, fake usernames hnuserNNN / hnfillNNN).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest

from pigtail.capture import scope
from pigtail.capture.hn_ranks import RankPoller
from pigtail.capture.mentions import NotShortlisted, RepoSuppressed, capture_hn_mentions
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.cli import main
from pigtail.connectors.base import ADR022_ENV, TokenBucket
from pigtail.connectors.hn import HNAlgoliaConnector, HNFirebaseConnector
from pigtail.connectors.hn_ranks import HNRanksConnector
from pigtail.llm.store import LLMStore
from pigtail.privacy import requests, suppression
from pigtail.privacy.deletion import PERSON_TABLES
from pigtail.privacy.deletion_sync import HNDeletionSource, sync
from tests.hn_fake import FakeHN

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
ON = {"PIGTAIL_ENABLE_HN": "1", ADR022_ENV: "1"}
HANDLE_MARKERS = ("hnuser", "hnfill")


def conn_kw(store: Any, fake: FakeHN, db: Any, clock: Any = lambda: NOW, **kw: Any) -> Any:
    return {
        "store": store,
        "http": fake.client(),
        "limiter": TokenBucket(1000, burst=100),
        "clock": clock,
        "evidence_sink": db.upsert_evidence,
        **kw,
    }


def dump_all_tables(db: Any) -> str:
    """Every public table as JSON text: no raw handle may appear anywhere."""
    tables = [
        r[0]
        for r in db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    ]
    out = []
    for t in tables:
        row = db.conn.execute(f'SELECT json_agg(t)::text FROM "{t}" t').fetchone()
        out.append(row[0] or "")
    return "\n".join(out)


def add_repo_and_case(db: Any, status: str = "live") -> None:
    db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:1000001', 'github', 1000001, 'org-a/repo-1', %s)",
        (NOW - timedelta(days=3),),
    )
    db.conn.execute(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status)"
        " VALUES ('case_00000000000000000001', 'github:1000001', %s, 'manual', %s)",
        (NOW - timedelta(days=1), status),
    )
    # Directive §8.3: mentions are captured for shortlisted projects only
    scope.set_entries(db, "brief_synthetic", 1, ["org-a/repo-1"], "in_review")


# --- migrations ----------------------------------------------------------------------------------
def test_m1_t14_m1_t4_migrations_create_tables(capture_db):
    names = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {
        "hn_rank_poll",
        "hn_rank_observation",
        "hn_story",
        "hn_mention",
        "upstream_items",
        "evidence_upstream_items",
    } <= names
    # M21a (Directive §8.1, migration 0017): no table holds handles or pseudonyms any more
    assert PERSON_TABLES == ()
    cols = {
        (r[0], r[1])
        for r in capture_db.conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_name IN ('hn_mention', 'upstream_items')"
        )
    }
    assert ("hn_mention", "author") not in cols and (
        "upstream_items",
        "author_pseudonym",
    ) not in cols
    assert ("hn_mention", "author_role") in cols and ("hn_mention", "author_bucket") in cols
    with pytest.raises(psycopg.errors.CheckViolation):  # roles only, never a handle
        capture_db.conn.execute(
            "INSERT INTO hn_mention (repo_full_name, item_id, item_type, author_role,"
            " author_bucket, bot_rule_version, role_rule_version, match_kind, first_seen_at,"
            " last_seen_at) VALUES ('o/r', 1, 'story', 'hnuser001', 'r0', 'v', 'v', 'url',"
            " now(), now())"
        )
    assert "by" not in {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN"
            " ('hn_story', 'hn_rank_observation', 'hn_rank_poll')"
        )
    }


# --- M1-T14 rank poller --------------------------------------------------------------------------
def test_m1_t14_poll_once_stores_ranks_and_project_level_metadata(capture_db, tmp_path):
    db = capture_db
    add_repo_and_case(db)
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()
    with RunRecorder("capture.hn_ranks", {}, sink=db.upsert_run, detect_commit=False) as run:
        conn = HNRanksConnector(pseudonymizer=None, env={}, run=run, **conn_kw(store, fake, db))
        res = RankPoller(conn, db, run=run).poll_once()
    assert res.n_ids == 500 and res.items_fetched == 30 and res.raw_dropped == 30
    assert res.stories_linked == ["org-a/repo-1"]
    n = db.conn.execute("SELECT count(*), min(rank), max(rank) FROM hn_rank_observation")
    assert n.fetchone() == (500, 1, 500)
    top = db.conn.execute(
        "SELECT item_id, rank, score FROM hn_rank_observation WHERE rank <= 2 ORDER BY rank"
    ).fetchall()
    assert top == [(9000001, 1, 120), (9000004, 2, 30)]
    assert db.conn.execute("SELECT count(*) FROM hn_story").fetchone() == (30,)
    story = db.conn.execute(
        "SELECT url, title, score, descendants, repo_full_name, repo_id, best_rank, created_at"
        " FROM hn_story WHERE item_id = 9000001"
    ).fetchone()
    assert story == (
        "https://github.com/org-a/repo-1",
        "Show HN: Repo-1, a synthetic tool",
        120,
        4,
        "org-a/repo-1",
        "github:1000001",
        1,
        datetime.fromtimestamp(1790000000, UTC),
    )
    r2 = db.conn.execute("SELECT repo_full_name, repo_id FROM hn_story WHERE item_id = 9000004")
    assert r2.fetchone() == ("org-b/repo-2", None)  # mapped, but not a tracked repo
    # topstories snapshot: project-level and kept; item snapshots: parsed then raw dropped
    poll = db.conn.execute("SELECT evidence_id, content_hash FROM hn_rank_poll").fetchone()
    ev = db.conn.execute(
        "SELECT retention_class, deletion_state FROM evidence WHERE id = %s", (poll[0],)
    ).fetchone()
    assert ev == ("project_level", "present") and store.exists(poll[1])
    items = db.conn.execute(
        "SELECT content_hash, deletion_state, retention_class, repo_id, case_id FROM evidence"
        " WHERE source = 'hn_ranks' AND url LIKE '%%/item/%%'"
    ).fetchall()
    assert len(items) == 30
    assert all(r[1] == "raw_dropped" and not store.exists(r[0]) for r in items)
    assert all(r[2] == "person_level_24m" for r in items)
    assert ("github:1000001", "case_00000000000000000001") in {(r[3], r[4]) for r in items}
    logs = db.conn.execute("SELECT reason, action, count(*) FROM deletion_log GROUP BY 1, 2")
    assert logs.fetchall() == [("retention", "raw_dropped", 30)]
    dump = dump_all_tables(db)
    assert not any(m in dump for m in HANDLE_MARKERS)  # `by` never stored, not even hashed
    counts = db.conn.execute("SELECT counts FROM runs WHERE id = %s", (run.id,)).fetchone()[0]
    assert counts["rank_observations"] == 500 and counts["hn_ranks.snapshots"] == 31


def test_m1_t14_poll_is_idempotent_and_skips_opted_out_repos(capture_db, tmp_path):
    db = capture_db
    add_repo_and_case(db)
    suppression.add(db, "repo", "github:1000001", platform="github", reason="objection")
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()
    fake.fail_items = {8_000_003}
    conn = HNRanksConnector(
        pseudonymizer=None,
        env={},
        suppression=suppression.load(db),
        **conn_kw(store, fake, db),
    )
    conn.retry = type(conn.retry)(max_retries=0)
    res = RankPoller(conn, db, items=10).poll_once()
    assert res.stories_suppressed == 1 and res.items_failed == 1 and res.stories_linked == []
    assert db.conn.execute("SELECT 1 FROM hn_story WHERE item_id = 9000001").fetchone() is None
    RankPoller(conn, db, items=10).poll_once()  # same clock: same observation time
    assert db.conn.execute("SELECT count(*) FROM hn_rank_observation").fetchone() == (500,)
    assert db.conn.execute("SELECT count(*) FROM hn_rank_poll").fetchone() == (1,)


# --- M1-T4 mention capture ------------------------------------------------------------------------
@pytest.fixture
def mentions(capture_db, tmp_path, pz):
    db = capture_db
    add_repo_and_case(db)
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()
    kw = conn_kw(store, fake, db, pseudonymizer=pz, env=ON)
    alg, fb = HNAlgoliaConnector(**kw), HNFirebaseConnector(**kw)
    with RunRecorder("capture.mentions", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = capture_hn_mentions(alg, db, "org-a/repo-1", firebase=fb, run=run)
    return db, store, fake, fb, res


def test_m1_t4_mentions_stored_with_evidence_linked_to_case(mentions, pz):
    db, _store, _fake, _fb, res = mentions
    assert (res.repo_id, res.case_id) == ("github:1000001", "case_00000000000000000001")
    assert res.mentions == {"url": 2, "full_name": 2, "name_and_owner": 1}
    assert res.name_only_skipped == 0  # the bare-name query only runs with --loose
    rows = db.conn.execute(
        "SELECT item_id, match_kind, item_type, author_role, case_id, repo_id, evidence_id,"
        " item_evidence_id, title, author_bucket, automated_account, bot_rule_version,"
        " role_rule_version FROM hn_mention ORDER BY item_id"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        (9000001, "url"),
        (9000003, "full_name"),
        (9000011, "url"),
        (9000012, "full_name"),
        (9000014, "name_and_owner"),
    ]
    by_id = {r[0]: r for r in rows}
    # Directive §8.1 / ADR-071.2: a role and bucket, the bot flag and rule versions; no handle
    assert by_id[9000012][3] == "account" and by_id[9000012][9] == "r0"
    assert all(r[10] is False and r[11] == "bot-filter-v0" and r[12] == "roles-v1" for r in rows)
    assert pz.pseudonym("hnuser005", "hn") not in dump_all_tables(db)
    assert by_id[9000001][8] == "Show HN: Repo-1, a synthetic tool"
    assert by_id[9000012][8] is None  # comments: no title, no text
    assert all(r[4] == "case_00000000000000000001" and r[5] == "github:1000001" for r in rows)
    assert all(r[6] and r[7] for r in rows)
    evs = db.conn.execute(
        "SELECT source, retention_class, case_id FROM evidence WHERE source LIKE 'hn_%%'"
    ).fetchall()
    assert {e[0] for e in evs} == {"hn_algolia", "hn_firebase"}
    assert all(e[1] == "person_level_24m" and e[2] == "case_00000000000000000001" for e in evs)
    tracked = db.conn.execute(
        "SELECT count(*), min(next_check_at) FROM upstream_items WHERE platform = 'hn'"
    ).fetchone()
    assert tracked[0] == 5  # every item on a snapshotted page, mention or not
    assert tracked[1] == NOW + timedelta(days=1)  # open case: daily re-check
    assert not any(m in dump_all_tables(db) for m in HANDLE_MARKERS)


def test_m1_t4_loose_mode_and_opted_out_repo(capture_db, tmp_path, pz):
    db = capture_db
    add_repo_and_case(db)
    store = LocalSnapshotStore(tmp_path / "snap")
    kw = conn_kw(store, FakeHN(), db, pseudonymizer=pz, env=ON)
    res = capture_hn_mentions(HNAlgoliaConnector(**kw), db, "org-a/repo-1", loose=True)
    assert res.mentions.get("name") == 1 and res.items_snapshotted == 0
    suppression.add(db, "repo", "github:1000001", platform="github", reason="objection")
    alg = HNAlgoliaConnector(**{**kw, "suppression": suppression.load(db)})
    with pytest.raises(RepoSuppressed):
        capture_hn_mentions(alg, db, "org-a/repo-1")


def test_m21a_directive_8_3_mentions_only_for_shortlisted_repos(capture_db, tmp_path, pz):
    """Directive §8.3 / ADR-066.3: no request is made for a repo on no in-review or final
    shortlist; `removed` takes it out of scope again."""
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    fake = FakeHN()
    kw = conn_kw(store, fake, db, pseudonymizer=pz, env=ON)
    with pytest.raises(NotShortlisted):
        capture_hn_mentions(HNAlgoliaConnector(**kw), db, "org-a/repo-1")
    assert fake.requests == []
    scope.set_entries(db, "brief_synthetic", 1, ["Org-A/Repo-1"], "final")
    assert scope.in_scope(db, "org-a/repo-1")
    capture_hn_mentions(HNAlgoliaConnector(**kw), db, "org-a/repo-1")
    assert fake.requests
    scope.set_entries(db, "brief_synthetic", 1, ["org-a/repo-1"], "removed")
    with pytest.raises(NotShortlisted):
        capture_hn_mentions(HNAlgoliaConnector(**kw), db, "org-a/repo-1")


# --- CB-02 deletion sync --------------------------------------------------------------------------
def hn_source(fake: FakeHN, store: Any, pz: Any) -> HNDeletionSource:
    fb = HNFirebaseConnector(
        store=store,
        pseudonymizer=pz,
        http=fake.client(),
        enabled=False,
        env={},
        limiter=TokenBucket(1000, burst=100),
    )
    return HNDeletionSource(fb)


def snapshots_holding(db: Any, item_id: int) -> list[tuple[str, str]]:
    return db.conn.execute(
        "SELECT e.content_hash, e.deletion_state FROM evidence_upstream_items l"
        " JOIN evidence e ON e.id = l.evidence_id WHERE l.item_id = %s ORDER BY 1",
        (str(item_id),),
    ).fetchall()


def test_cb02_deleted_item_drops_raw_keeps_hash_and_logs(mentions, pz):
    db, store, fake, _fb, _res = mentions
    llm = LLMStore(":memory:")
    held = snapshots_holding(db, 9000012)
    assert len(held) >= 2  # search pages + its own item snapshot
    ev_ids = [
        r[0]
        for r in db.conn.execute(
            "SELECT l.evidence_id FROM evidence_upstream_items l WHERE l.item_id = '9000012'"
        )
    ]
    llm.cache_put("k_del", {"q": "coded"}, "m", evidence_id=ev_ids[0])
    fake.items[9000012] = {"id": 9000012, "type": "comment", "deleted": True, "time": 1}
    fake.items[9000014] = {**(fake.items[9000014] or {}), "dead": False}  # only one item gone
    later = NOW + timedelta(days=2)

    dry = sync(db, store, hn_source(fake, store, pz), now=later, dry_run=True, llm_store=llm)
    assert dry.gone == {"deleted": 1} and dry.items_acted == 1
    assert all(store.exists(h) for h, _ in held)
    assert db.conn.execute("SELECT count(*) FROM deletion_log").fetchone() == (0,)
    assert db.conn.execute("SELECT count(*) FROM hn_mention WHERE item_id = 9000012").fetchone()[0]

    with RunRecorder("privacy.deletion_sync", {}, sink=db.upsert_run, detect_commit=False) as run:
        rep = sync(db, store, hn_source(fake, store, pz), now=later, llm_store=llm, run=run)
    assert rep.checked == 5 and rep.still_present == 4 and rep.gone == {"deleted": 1}
    assert rep.snapshots_dropped == len(held) and rep.rows_deleted == 1
    assert rep.llm_cache_rows_deleted == 1 and llm.cache_get("k_del") is None
    for h, _ in held:
        assert not store.exists(h)
    states = snapshots_holding(db, 9000012)
    assert {s for _, s in states} == {"deleted_upstream"}
    assert [h for h, _ in states] == [h for h, _ in held]  # hash kept
    # parsed person-level row of the deleted item is gone; others stay
    ids = [r[0] for r in db.conn.execute("SELECT item_id FROM hn_mention ORDER BY 1")]
    assert ids == [9000001, 9000003, 9000011, 9000014]
    item = db.conn.execute(
        "SELECT state, detected_at, acted_at FROM upstream_items WHERE item_id = '9000012'"
    ).fetchone()
    assert item == ("deleted", later, later)
    nxt = db.conn.execute(
        "SELECT DISTINCT next_check_at FROM upstream_items WHERE state = 'present'"
    ).fetchall()
    assert nxt == [(later + timedelta(days=1),)]  # open case: re-check daily
    logs = db.conn.execute(
        "SELECT reason, action, target, run_id FROM deletion_log ORDER BY id"
    ).fetchall()
    assert {(r[0], r[1], r[2]) for r in logs} == {
        ("deleted_upstream", "raw_dropped", "snapshot"),
        ("deleted_upstream", "rows_deleted", "hn_mention"),
        ("deleted_upstream", "cache_purged", "llm_cache"),
    }
    assert {r[3] for r in logs} == {run.id}
    # idempotent: nothing due, nothing to re-apply
    again = sync(db, store, hn_source(fake, store, pz), now=later)
    assert again.checked == 0 and again.snapshots_dropped == 0 and again.reapplied_items == 0


def test_cb02_dead_and_missing_items_are_gone_and_closed_cases_monthly(mentions, pz):
    db, store, fake, _fb, _res = mentions
    db.conn.execute("UPDATE cases SET status = 'closed'")
    fake.items[9000011] = None  # missing upstream
    fake.fail_items = {9000003}  # transient error: unknown, retried next run
    later = NOW + timedelta(days=2)
    src = hn_source(fake, store, pz)
    src.conn.retry = type(src.conn.retry)(max_retries=0)
    rep = sync(db, store, src, now=later)
    # 9000014 is `dead` in the fixture
    assert rep.gone == {"missing": 1, "dead": 1} and rep.unknown == 1
    st = dict(db.conn.execute("SELECT item_id, state FROM upstream_items").fetchall())
    assert st["9000011"] == "missing" and st["9000014"] == "dead" and st["9000003"] == "present"
    nxt = dict(db.conn.execute("SELECT item_id, next_check_at FROM upstream_items").fetchall())
    assert nxt["9000001"] == later + timedelta(days=30)  # closed case: monthly
    assert nxt["9000003"] == NOW + timedelta(days=1)  # unknown: still due, retried


def test_cb02_reapplies_to_stale_recapture(mentions, tmp_path, pz):
    """Algolia may still serve a deleted item; a new capture of it is dropped on the next sync."""
    db, store, fake, _fb, _res = mentions
    fake.items[9000012] = {"id": 9000012, "deleted": True}
    fake.items[9000014] = {**(fake.items[9000014] or {}), "dead": False}
    later = NOW + timedelta(days=2)
    sync(db, store, hn_source(fake, store, pz), now=later)
    kw = conn_kw(store, fake, db, clock=lambda: later, pseudonymizer=pz, env=ON)
    capture_hn_mentions(HNAlgoliaConnector(**kw), db, "org-a/repo-1")  # stale index
    fresh = [h for h, s in snapshots_holding(db, 9000012) if s == "present"]
    assert fresh and all(store.exists(h) for h in fresh)
    rep = sync(db, store, hn_source(fake, store, pz), now=later)
    assert rep.reapplied_items == 1 and rep.checked == 0
    assert all(s == "deleted_upstream" for _, s in snapshots_holding(db, 9000012))
    assert not any(store.exists(h) for h in fresh)
    assert (
        db.conn.execute("SELECT count(*) FROM hn_mention WHERE item_id = 9000012").fetchone()[0]
        == 0
    )


def test_cb02_overdue_items_are_reported(mentions, pz):
    db, store, fake, _fb, _res = mentions
    rep = sync(db, store, hn_source(fake, store, pz), now=NOW + timedelta(days=30), dry_run=True)
    assert rep.overdue_before_run == 5  # due at NOW+1d, SLA 7 d
    assert rep.detected_not_acted == 1 and rep.gone == {"dead": 1}


# --- CB-08 reaches HN data ------------------------------------------------------------------------
def test_cb08_m21a_erasure_searches_snapshots_in_memory_and_adds_fingerprint(mentions, pz):
    """M21a: coded rows hold no handle, so erasure finds the person in the retained snapshots
    (in memory), deletes every snapshot containing them and adds the opt-out fingerprint."""
    db, store, _fake, _fb, _res = mentions
    p = pz.person_fingerprint("hnuser005", "hn")
    res = requests.erasure(db, store, pz, platform="hn", handle="hnuser005")
    assert res.outcome == "completed" and res.counts["snapshots_raw_dropped"] >= 2
    assert res.counts["records_found"] >= 1
    assert res.counts["person_rows_deleted"] == 0  # PERSON_TABLES is empty since 0017
    assert p in suppression.load(db).persons
    row = db.conn.execute(
        "SELECT kind, value FROM privacy_suppression WHERE kind = 'person'"
    ).fetchone()
    assert row == ("person", p)
    for (h,) in db.conn.execute(
        "SELECT DISTINCT content_hash FROM evidence WHERE deletion_state = 'present'"
    ).fetchall():
        assert b"hnuser005" not in store.get(h)


# --- CLI ------------------------------------------------------------------------------------------
@pytest.fixture
def cli_env(capture_db, pg_url, tmp_path, monkeypatch):
    fake = FakeHN()
    real_client = httpx.Client

    def client(*a: Any, **kw: Any) -> httpx.Client:
        kw.pop("transport", None)
        return real_client(transport=httpx.MockTransport(fake), **kw)

    monkeypatch.setattr(httpx, "Client", client)
    for cls in (HNRanksConnector, HNFirebaseConnector, HNAlgoliaConnector):
        monkeypatch.setattr(cls, "rate_per_second", 1000.0)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", "test-key-not-secret-0123456789")
    for k in ("PIGTAIL_ENABLE_HN", ADR022_ENV, "OPTOUT_KEY"):
        monkeypatch.delenv(k, raising=False)
    return capture_db, fake


def test_m1_t14_cli_hn_ranks_once(cli_env, capsys):
    db, _fake = cli_env
    assert main(["capture", "hn-ranks", "--once", "--items", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_ids"] == 500 and out["front_page"] == 30 and out["items_fetched"] == 5
    job = db.conn.execute("SELECT job, status FROM runs WHERE id = %s", (out["run_id"],))
    assert job.fetchone() == ("capture.hn_ranks", "succeeded")
    assert main(["capture", "hn-ranks", "--loop", "--interval-minutes", "0.5"]) == 2
    assert main(["capture", "hn-ranks", "--loop", "--max-polls", "1", "--items", "0"]) == 0


def test_m1_t4_cli_mentions_gated(cli_env, capsys, monkeypatch):
    db, _fake = cli_env
    add_repo_and_case(db)
    assert main(["capture", "mentions", "--repo", "org-a/repo-1"]) == 2
    assert "PIGTAIL_ENABLE_HN" in capsys.readouterr().err
    monkeypatch.setenv("PIGTAIL_ENABLE_HN", "1")
    assert main(["capture", "mentions", "--repo", "org-a/repo-1"]) == 2
    assert ADR022_ENV in capsys.readouterr().err
    monkeypatch.setenv(ADR022_ENV, "1")
    assert main(["capture", "mentions", "--repo", "org-a/repo-1", "--since", "2026-09-01"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["case_id"] == "case_00000000000000000001" and out["items_snapshotted"] == 5
    assert main(["capture", "mentions", "--repo", "nope"]) == 2
    assert main(["capture", "mentions", "--repo", "org-b/elsewhere"]) == 2  # not shortlisted
    assert "shortlisted projects only" in capsys.readouterr().err


def test_cb02_cli_deletion_sync(cli_env, capsys, monkeypatch):
    db, fake = cli_env
    add_repo_and_case(db)
    monkeypatch.setenv("PIGTAIL_ENABLE_HN", "1")
    monkeypatch.setenv(ADR022_ENV, "1")
    assert main(["capture", "mentions", "--repo", "org-a/repo-1", "--no-items"]) == 0
    capsys.readouterr()
    db.conn.execute("UPDATE upstream_items SET next_check_at = now() - interval '1 hour'")
    monkeypatch.setenv("PIGTAIL_ENABLE_HN", "0")  # sync still runs with collection off
    assert main(["privacy", "deletion-sync", "--source", "hn", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sources"][0]["dry_run"] is True and out["sources"][0]["gone"] == {"dead": 1}
    assert main(["privacy", "deletion-sync"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sources"][0]["items_acted"] == 1
    job = db.conn.execute("SELECT job FROM runs WHERE id = %s", (out["run_id"],)).fetchone()
    assert job == ("privacy.deletion_sync",)
    assert fake.requests  # checks went through the (fake) Firebase API
