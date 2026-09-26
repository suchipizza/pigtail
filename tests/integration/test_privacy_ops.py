"""DPIA CB-01, CB-05, CB-08, CB-13, CB-18 end to end on Postgres (synthetic data only)."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from pigtail.capture.models import Evidence, Run, evidence_id
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore, SnapshotMeta
from pigtail.cli import main
from pigtail.connectors.base import TokenBucket
from pigtail.connectors.gharchive import GHArchiveConnector, hour_url
from pigtail.llm.store import LLMStore
from pigtail.privacy import requests, suppression
from pigtail.privacy.deletion import PersonTable
from pigtail.privacy.retention import RetentionConfig, purge
from pigtail.privacy.suppression import subject_pseudonym

pytestmark = pytest.mark.db

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gharchive"
NOW = datetime(2026, 9, 25, tzinfo=UTC)
OLD = NOW - timedelta(days=800)  # past 24 months
YOUNG = NOW - timedelta(days=100)
SEED_RUN = "run_" + "a" * 32


def put_ev(
    db: Any,
    store: LocalSnapshotStore,
    data: bytes,
    *,
    fetched_at: datetime,
    retention_class: str = "person_level_24m",
    source: str = "testsrc",
    url: str | None = None,
    repo_id: str | None = None,
    run_id: str | None = None,
) -> Evidence:
    url = url or f"https://example.org/{source}/{fetched_at:%Y%m%d}/{len(data)}"
    meta = SnapshotMeta(source, url, fetched_at, f"{source}/0", "test terms")
    h = store.put(data, meta)
    ev = Evidence(
        id=evidence_id(source, url, h),
        source=source,
        url=url,
        fetched_at=fetched_at,
        content_hash=h,
        snapshot_ref=store.ref(h),
        reliability="high",
        terms_basis="test terms",
        retention_class=retention_class,  # type: ignore[arg-type]
        collector_version=f"{source}/0",
        case_id=None,
        repo_id=repo_id,
        run_id=run_id,
    )
    db.upsert_evidence(ev)
    return ev


def state(db: Any, ev: Evidence) -> str:
    row = db.conn.execute("SELECT deletion_state FROM evidence WHERE id = %s", (ev.id,)).fetchone()
    return str(row[0])


def log_rows(db: Any) -> list[tuple[Any, ...]]:
    return db.conn.execute(
        "SELECT reason, action, target, content_hash, rows_affected, run_id, request_id"
        " FROM deletion_log ORDER BY id"
    ).fetchall()


# --- migration 0003 ------------------------------------------------------------------------------
def test_cb01_migration_0003_tables_and_append_only_log(capture_db):
    db = capture_db
    tables = {
        r[0]
        for r in db.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {"privacy_suppression", "privacy_requests", "deletion_log"} <= tables
    db.conn.execute(
        "INSERT INTO deletion_log (reason, action, target) VALUES ('retention', 'raw_dropped', 's')"
    )
    for stmt in (
        "UPDATE deletion_log SET target = 'x'",
        "DELETE FROM deletion_log",
        "TRUNCATE deletion_log",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            db.conn.execute(stmt)
    assert len(log_rows(db)) == 1


def test_cb13_suppression_table_rejects_raw_handles(capture_db):
    with pytest.raises(psycopg.errors.CheckViolation):
        capture_db.conn.execute(
            "INSERT INTO privacy_suppression (kind, value, platform, reason)"
            " VALUES ('person', 'user0001', 'github', 'objection')"
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # kind renamed in 0017 (ADR-071.1)
        capture_db.conn.execute(
            "INSERT INTO privacy_suppression (kind, value, platform, reason)"
            " VALUES ('pseudonym', 'p_0123456789abcdef', 'github', 'objection')"
        )
    with pytest.raises(ValueError, match="never handles"):
        suppression.add(capture_db, "person", "user0001", platform="github", reason="objection")


# --- CB-01 retention purge -----------------------------------------------------------------------
@pytest.fixture
def seeded(capture_db, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snapshots")
    run = Run(id=SEED_RUN, job="seed", started_at=OLD, status="succeeded", error="boom @x")
    db.upsert_run(run)
    evs = {
        "old": put_ev(db, store, b"old person-level", fetched_at=OLD),
        "young": put_ev(db, store, b"young person-level", fetched_at=YOUNG),
        "project": put_ev(
            db, store, b"project page", fetched_at=OLD, retention_class="project_level"
        ),
        # the same bytes captured twice: once long ago, once recently -> must be kept
        "shared_old": put_ev(db, store, b"shared", fetched_at=OLD, url="https://example.org/a"),
        "shared_new": put_ev(db, store, b"shared", fetched_at=YOUNG, url="https://example.org/b"),
    }
    llm = LLMStore(":memory:", clock=lambda: NOW)
    llm.cache_put("k_old", {"q": "quoted span"}, "m", evidence_id=evs["old"].id)
    llm.cache_put("k_young", {"q": "other"}, "m", evidence_id=evs["young"].id)
    return db, store, evs, llm


def test_cb01_dry_run_changes_nothing(seeded):
    db, store, evs, llm = seeded
    rep = purge(db, store, llm_store=llm, now=NOW, dry_run=True)
    # the shared blob's newest fetch is young, so it is not due (R19.9 anchors per hash)
    assert rep.person_level_hashes_dropped == 1 and rep.blocked_shared == 0
    assert rep.snapshots_dropped_ceiling == 1 and rep.snapshots_dropped_report_final == 0
    assert rep.llm_cache_rows_for_evidence == 1 and rep.run_errors_cleared == 1
    assert state(db, evs["old"]) == "present" and store.exists(evs["old"].content_hash)
    assert llm.cache_get("k_old") is not None
    assert log_rows(db) == []


def test_cb01_purge_drops_raw_keeps_hash_and_logs(seeded):
    db, store, evs, llm = seeded
    with RunRecorder("retention.purge", {}, sink=db.upsert_run, detect_commit=False) as run:
        rep = purge(db, store, llm_store=llm, now=NOW, run=run)
    old = evs["old"]
    assert state(db, old) == "raw_dropped"
    assert not store.exists(old.content_hash)
    assert store.meta(old.content_hash).url == old.url  # sidecar (hash + url) kept
    row = db.conn.execute(
        "SELECT content_hash, url, fetched_at FROM evidence WHERE id = %s", (old.id,)
    ).fetchone()
    assert row == (old.content_hash, old.url, OLD)
    for k in ("young", "project", "shared_old", "shared_new"):
        assert state(db, evs[k]) == "present", k
        assert store.exists(evs[k].content_hash), k
    assert rep.dropped_hashes == [old.content_hash]
    # CB-05: cache derived from the dropped evidence is gone; the rest stays
    assert llm.cache_get("k_old") is None and llm.cache_get("k_young") is not None
    # CB-18: old run error text cleared
    err = db.conn.execute("SELECT error FROM runs WHERE id = %s", (SEED_RUN,)).fetchone()
    assert err == (None,)
    logs = log_rows(db)
    assert ("retention", "raw_dropped", "snapshot", old.content_hash, 1, run.id, None) in logs
    assert {r[1] for r in logs} == {"raw_dropped", "cache_purged", "error_text_cleared"}
    rec = db.conn.execute("SELECT status, counts FROM runs WHERE id = %s", (run.id,)).fetchone()
    assert rec[0] == "succeeded" and rec[1]["person_level_hashes_dropped"] == 1
    # idempotent
    again = purge(db, store, llm_store=llm, now=NOW)
    assert again.person_level_hashes_dropped == 0 and len(log_rows(db)) == len(logs)


def test_cb01_person_level_tables_are_purged_by_age(seeded):
    db, store, _evs, _llm = seeded
    db.conn.execute("CREATE TABLE t_actor (pseudonym text, seen_at timestamptz)")
    db.conn.execute(
        "INSERT INTO t_actor VALUES ('p_0000000000000001', %s), ('p_0000000000000002', %s)",
        (OLD, YOUNG),
    )
    tables = [PersonTable("t_actor", "pseudonym", "seen_at")]
    rep = purge(db, store, now=NOW, person_tables=tables)
    assert rep.person_rows_deleted == {"t_actor": 1}
    assert db.conn.execute("SELECT pseudonym FROM t_actor").fetchall() == [("p_0000000000000002",)]


def test_cb01_shorter_retention_is_configurable(seeded):
    db, store, evs, _llm = seeded
    rep = purge(db, store, cfg=RetentionConfig(person_level_days=30), now=NOW)
    assert state(db, evs["young"]) == "raw_dropped"
    assert state(db, evs["shared_new"]) == "raw_dropped"
    assert state(db, evs["project"]) == "present"
    assert rep.person_level_hashes_dropped == 3


def test_cb01_cli_retention_purge_dry_run(capture_db, pg_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    assert main(["retention", "purge", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["run_id"].startswith("run_")
    job = capture_db.conn.execute(
        "SELECT job, config->>'dry_run' FROM runs WHERE id = %s", (out["run_id"],)
    ).fetchone()
    assert job == ("retention.purge", "true")


# --- CB-08 / CB-13 on GH Archive fixtures ---------------------------------------------------------
class Archive:
    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = FIX / req.url.path.rsplit("/", 1)[-1]
        if path.exists():
            return httpx.Response(200, content=path.read_bytes())
        return httpx.Response(404)


@pytest.fixture
def ingested(capture_db, tmp_path, pz):
    """Two synthetic GH Archive hours snapshotted with evidence by the GH Archive connector."""
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
    fetched = [conn.fetch(hour_url(datetime(2026, 9, 20, h, tzinfo=UTC))) for h in (0, 2)]
    llm = LLMStore(":memory:", clock=lambda: NOW)
    p = subject_pseudonym(pz, "github", "user0001")
    llm.cache_put("k_mention", {"q": f"thanks @{p}"}, "m")
    llm.cache_put("k_hour", {"q": "summary"}, "m", evidence_id=fetched[0].evidence.id)
    llm.cache_put("k_other", {"q": "unrelated"}, "m")
    return capture_db, store, fetched, llm


def test_cb08_m21a_access_searches_snapshots_for_the_handle_in_memory(ingested, pz, tmp_path):
    """M21a: stored data has no handles; access finds the person's records in the temporary
    evidence copies (snapshots) and exports them coded, without handle or fingerprint."""
    db, store, _fetched, llm = ingested
    with RunRecorder("privacy.access", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = requests.access(
            db,
            store,
            pz,
            platform="github",
            handle="user0001",
            out_dir=tmp_path / "out",
            llm_store=llm,
            run=run,
        )
    assert res.outcome == "completed" and res.export_path is not None
    assert stat.S_IMODE(res.export_path.stat().st_mode) == 0o600
    export = json.loads(res.export_path.read_text())
    p = subject_pseudonym(pz, "github", "user0001")
    assert "pseudonym" not in export and export["request_id"] == res.request_id
    assert export["counts"]["records"] > 0 and export["counts"]["person_rows"] == 0
    recs = [r for s in export["snapshots"] for r in s["records"]]
    assert recs and all(r["actor"] is None and "actor_role" in r for r in recs)
    assert p not in json.dumps(export["snapshots"])
    assert [r["key"] for r in export["llm_cache"]] == ["k_mention"]
    assert "user0001" not in res.export_path.read_text()
    # request log: type, dates, outcome; no handle and no pseudonym anywhere in the row
    row = db.conn.execute(
        "SELECT type, platform, outcome, completed_at IS NOT NULL, run_id, counts::text"
        " FROM privacy_requests WHERE id = %s",
        (res.request_id,),
    ).fetchone()
    assert row[:5] == ("access", "github", "completed", True, run.id)
    assert "user0001" not in row[5] and p not in row[5]


def test_cb08_access_for_unknown_handle_is_no_data(ingested, pz, tmp_path):
    db, store, _f, llm = ingested
    res = requests.access(
        db, store, pz, platform="github", handle="nobody-here", out_dir=tmp_path, llm_store=llm
    )
    assert res.outcome == "no_data"


def test_cb08_erasure_purges_and_suppresses(ingested, pz, tmp_path):
    db, store, fetched, llm = ingested
    p = subject_pseudonym(pz, "github", "user0001")
    with RunRecorder("privacy.erasure", {}, sink=db.upsert_run, detect_commit=False) as run:
        res = requests.erasure(
            db, store, pz, platform="github", handle="user0001", llm_store=llm, run=run
        )
    assert res.outcome == "completed" and res.counts["suppression_added"] == 1
    # user0001 appears in hour 0 only (synthetic scenario): that blob is dropped, hour 2 kept
    h0, h2 = fetched[0].content_hash, fetched[1].content_hash
    assert not store.exists(h0) and store.exists(h2)
    assert state(db, fetched[0].evidence) == "raw_dropped"
    assert state(db, fetched[1].evidence) == "present"
    assert llm.cache_get("k_mention") is None and llm.cache_get("k_hour") is None
    assert llm.cache_get("k_other") is not None
    assert p in suppression.load(db, pz).persons
    assert suppression.entries(db)[0]["kind"] == "person"
    entry = suppression.entries(db)[0]
    assert (entry["reason"], entry["request_id"]) == ("erasure", res.request_id)
    logs = log_rows(db)
    assert ("erasure", "raw_dropped", "snapshot", h0, 1, run.id, res.request_id) in logs
    # nothing left to find; replay-style re-ingest drops the person at ingest
    after = requests.access(
        db, store, pz, platform="github", handle="user0001", out_dir=tmp_path, llm_store=llm
    )
    assert after.outcome == "no_data"
    conn = GHArchiveConnector(
        store=store, pseudonymizer=pz, env={}, suppression=suppression.load(db, pz)
    )
    data = (FIX / "2026-09-20-0.json.gz").read_bytes()
    kept = list(conn.records(data, fetched[0].meta))
    base = GHArchiveConnector(store=store, pseudonymizer=pz, env={})
    subject = [r for r, fps in base.subject_records(data, fetched[0].meta) if p in fps]
    assert len(kept) == len(list(base.records(data, fetched[0].meta))) - len(subject) > 0
    # no raw handle stored anywhere in the privacy tables
    dump = db.conn.execute(
        "SELECT (SELECT json_agg(t)::text FROM privacy_suppression t) ||"
        " (SELECT json_agg(t)::text FROM privacy_requests t) ||"
        " (SELECT json_agg(t)::text FROM deletion_log t)"
    ).fetchone()[0]
    assert "user0001" not in dump


def test_cb13_optout_repo_purges_project_rows(capture_db, tmp_path, pz):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snapshots")
    db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:1000009', 'github', 1000009, 'org-a/repo-9', %s)",
        (YOUNG,),
    )
    db.conn.execute(
        "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label, day_boundary_tz,"
        " is_partial, fetched_at) VALUES (1000009, %s, 5, 'w', 'x', false, %s),"
        " (1000003, %s, 3, 'w', 'x', false, %s)",
        (YOUNG.date(), YOUNG, YOUNG.date(), YOUNG),
    )
    db.conn.execute(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status)"
        " VALUES ('case_x', 'github:1000009', %s, 'manual', 'live')",
        (YOUNG,),
    )
    ev = put_ev(
        db,
        store,
        b"repo page",
        fetched_at=YOUNG,
        retention_class="project_level",
        repo_id="github:1000009",
    )
    llm = LLMStore(":memory:")
    llm.cache_put("k", {"q": 1}, "m", evidence_id=ev.id)
    res = requests.optout_repo(
        db, store, platform="github", repo_key="github:1000009", pz=pz, llm_store=llm
    )
    assert {
        "suppression_added": 1,
        "repo_star_daily_rows_deleted": 1,
        "evidence_deleted": 1,
        "cases_deleted": 1,
        "llm_cache_rows_deleted": 1,
    }.items() <= res.counts.items()
    assert res.counts["name_suppression_added"] == 1  # M1-T23: the repo's name, as a hash
    assert not store.exists(ev.content_hash)
    assert db.conn.execute("SELECT count(*) FROM repos").fetchone() == (0,)
    assert db.conn.execute("SELECT count(*) FROM repo_star_daily").fetchone() == (1,)
    assert "github:1000009" in suppression.load(db, pz).repos


def test_cb13_reapply_refusals_after_restore(ingested, pz):
    """A restore brings raw data back; `optout purge` re-applies the whole list (CB-13/CB-17)."""
    db, store, fetched, llm = ingested
    p = subject_pseudonym(pz, "github", "user0001")
    suppression.add(db, "person", p, platform="github", reason="objection")
    totals = requests.reapply_refusals(db, store, pz, llm_store=llm)
    assert totals["snapshots_raw_dropped"] == 1
    assert not store.exists(fetched[0].content_hash)
    assert requests.reapply_refusals(db, store, pz, llm_store=llm)["snapshots_raw_dropped"] == 0


def test_cb08_cb13_cli_optout_and_requests(ingested, pg_url, tmp_path, monkeypatch, capsys, pz):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", "test-key-not-secret-0123456789")
    rc = main(
        ["privacy", "optout", "add", "--platform", "github", "--handle", "user0002", "--no-purge"]
    )
    assert rc == 0
    added = json.loads(capsys.readouterr().out)
    assert added["suppression_added"] == 1
    assert main(["privacy", "optout", "list"]) == 0
    listed = capsys.readouterr().out
    assert "user0002" not in listed and subject_pseudonym(pz, "github", "user0002") in listed
    assert (
        main(["privacy", "request", "access", "--platform", "github", "--handle", "user0001"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert Path(out["export_path"]).parent == tmp_path / "requests"
    assert main(["privacy", "requests"]) == 0
    log = json.loads(capsys.readouterr().out)
    assert [r["type"] for r in log] == ["objection", "access"]
    assert "user000" not in json.dumps(log)
    # M1-T23: a repo not in the database is opted out by name (stored as a hash only)
    assert main(["privacy", "optout", "add", "--platform", "github", "--repo", "no/such"]) == 0
    assert json.loads(capsys.readouterr().out)["suppression_added"] == 1
    assert main(["privacy", "optout", "list"]) == 0
    assert "no/such" not in capsys.readouterr().out
    assert main(["privacy", "optout", "add", "--platform", "github", "--repo", "not a repo"]) == 2
    monkeypatch.delenv("PSEUDONYM_KEY")
    assert main(["privacy", "request", "erasure", "--platform", "github", "--handle", "x"]) == 2
