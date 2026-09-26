"""M21a (Owner Directive 001 §8.1-8.3; ADR-066.1-3, ADR-071.1-2; PRD R5.3, §7, R19.9): roles and
buckets instead of handles, opt-out fingerprints only, migration 0017 purge, snapshot retention
anchored on final reports, and the schema-wide "no handle columns" guard.

Synthetic data only (`user0001`, `hnuser01`, `org-a/repo-1`).
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.cli import main
from pigtail.db.migrate import default_migrations_dir, migrate
from pigtail.privacy import snapshot_retention
from pigtail.privacy.retention import RetentionConfig, purge
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY
from tests.integration.test_privacy_ops import put_ev

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 25, tzinfo=UTC)
PZ = Pseudonymizer(TEST_KEY)
M21A = (
    "0017_roles_buckets_handle_purge.sql",
    "0018_snapshot_retention_report_final.sql",
    "0019_mention_scope_shortlist.sql",
)
# Column names that would hold a handle, a personal name or a pseudonym of an individual.
HANDLE_COLUMN = re.compile(
    r"(^|_)(author|actor|login|handle|username|user_name|by|person|display_name)($|_)"
    r"|pseudonym",
)
# Allowed: coded roles and flags, and the opt-out key's fingerprint tables (CB-25, a key hash).
ALLOWED = {
    ("hn_mention", "author_role"),
    ("hn_mention", "author_bucket"),
    ("pseudonym_key_fingerprint", "fingerprint"),
}


def _tables_columns(url: str) -> set[tuple[str, str]]:
    with psycopg.connect(url) as c:
        return {
            (str(t), str(col))
            for t, col in c.execute(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'public'"
            )
        }


# --- schema guard -------------------------------------------------------------------------------
def test_m21a_directive_8_1_no_column_holds_handles_or_pseudonyms(capture_db, pg_url):
    """Directive §8.1 / ADR-066.1: after the migrations no table has a handle-like column; the
    only per-person value is the opt-out fingerprint in `privacy_suppression.value`."""
    bad = sorted(
        (t, c)
        for t, c in _tables_columns(pg_url)
        if HANDLE_COLUMN.search(c) and (t, c) not in ALLOWED and not t.startswith("pseudonym_key")
    )
    assert bad == []
    with psycopg.connect(pg_url) as c:
        checks = {
            str(r[0])
            for r in c.execute(
                "SELECT conrelid::regclass::text FROM pg_constraint WHERE contype = 'c'"
                " AND pg_get_constraintdef(oid) LIKE '%p_[0-9a-f]{16}%'"
            )
        }
    assert checks == {"privacy_suppression"}  # opt-out fingerprints only


# --- migration 0017: transform, then purge --------------------------------------------------------
def test_m21a_0017_migrates_rows_to_roles_and_purges_pseudonyms(pg_url, tmp_path: Path):
    d = tmp_path / "migrations"
    shutil.copytree(default_migrations_dir(), d)
    for name in M21A:
        (d / name).unlink()
    for p in d.glob("*.sql"):  # other engineers' later migrations are not needed here
        if p.name[:4] > "0019":
            p.unlink()
    migrate(pg_url, d)
    p1, p2 = PZ.pseudonym("hnuser01", "hn"), PZ.pseudonym("user0001", "github")
    p3 = PZ.pseudonym("user0002", "github")
    t = NOW - timedelta(days=2)
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute(
            "INSERT INTO hn_mention (repo_full_name, item_id, item_type, author, match_kind,"
            " first_seen_at, last_seen_at) VALUES ('org-a/repo-1', 1, 'story', %s, 'url', %s, %s),"
            " ('org-a/repo-1', 2, 'comment', NULL, 'url', %s, %s)",
            (p1, t, t, t, t),
        )
        c.execute(
            "INSERT INTO upstream_items (platform, item_id, author_pseudonym, first_seen_at,"
            " last_seen_at, next_check_at) VALUES ('hn', '1', %s, %s, %s, %s),"
            " ('hn', '2', NULL, %s, %s, %s)",
            (p1, t, t, t, t, t, t),
        )
        c.execute(
            "INSERT INTO repo_event_poll (repo_host_id, polled_at, status, pages)"
            " VALUES (7, %s, 200, 1)",
            (t,),
        )
        c.execute(
            "INSERT INTO repo_event_actor (repo_host_id, event_id, event_type, actor_pseudonym,"
            " is_bot, created_at, observed_at) VALUES"
            " (7, '101', 'WatchEvent', %s, false, %s, %s),"
            " (7, '102', 'WatchEvent', %s, false, %s, %s),"
            " (7, '103', 'WatchEvent', %s, false, %s, %s),"  # the same account again
            " (7, '104', 'WatchEvent', NULL, true, %s, %s),"
            " (7, '105', 'ForkEvent', %s, false, %s, %s)",
            (p2, t, t, p3, t, t, p2, t, t, t, t, p3, t, t),
        )
        c.execute(
            "INSERT INTO privacy_suppression (kind, value, platform, reason)"
            " VALUES ('pseudonym', %s, 'github', 'objection')",
            (p2,),
        )
    for name in M21A:
        shutil.copy(default_migrations_dir() / name, d)
    assert migrate(pg_url, d) == ["0017", "0018", "0019"]
    with psycopg.connect(pg_url) as c:
        q = c.execute
        assert q(
            "SELECT item_id, author_role, author_bucket, automated_account, bot_rule_version,"
            " role_rule_version FROM hn_mention ORDER BY 1"
        ).fetchall() == [
            (1, "account", "r0", None, "migrated-0017", "migrated-0017"),
            (2, "account", "r0", None, "migrated-0017", "migrated-0017"),
        ]
        assert q("SELECT count(*) FROM upstream_items").fetchone() == (2,)  # still tracked
        assert q("SELECT to_regclass('repo_event_actor')").fetchone() == (None,)
        assert q(
            "SELECT stars, stars_automated, forks, forks_automated, bot_rule_version"
            " FROM repo_event_hourly_agg WHERE repo_host_id = 7"
        ).fetchall() == [(2, 1, 1, 0, "bot-filter-v0")]
        assert q("SELECT watermark_at, newest_event_id FROM repo_event_poll").fetchone() == (
            t,
            105,
        )
        assert q("SELECT kind, value FROM privacy_suppression").fetchall() == [("person", p2)]
        logged = dict(
            q(
                "SELECT target, rows_affected FROM deletion_log"
                " WHERE reason = 'directive_001_handle_purge'"
            ).fetchall()
        )
        assert logged == {
            "hn_mention.author": 1,
            "upstream_items.author_pseudonym": 1,
            "repo_event_actor": 5,
        }
        # count-only tombstones: no value, no hash, no content
        assert q(
            "SELECT count(*) FROM deletion_log WHERE reason = 'directive_001_handle_purge'"
            " AND (content_hash IS NOT NULL OR evidence_id IS NOT NULL)"
        ).fetchone() == (0,)
        # no pseudonym is left anywhere except the opt-out entry
        dump = " ".join(
            str(r[0])
            for r in q(
                "SELECT string_agg(t::text, ' ') FROM (SELECT * FROM hn_mention) t"
                " UNION ALL SELECT string_agg(t::text, ' ') FROM (SELECT * FROM upstream_items) t"
                " UNION ALL SELECT string_agg(t::text, ' ') FROM (SELECT * FROM repo_event_poll) t"
                " UNION ALL SELECT string_agg(t::text, ' ') FROM (SELECT * FROM deletion_log) t"
            )
        )
        for p in (p1, p2, p3):
            assert p not in dump


# --- R19.9 snapshot retention ---------------------------------------------------------------------
def _brief_run(db: Any, brief_id: str, version: int, n: int) -> str:
    rid = f"brun_{n:020x}"
    db.conn.execute(
        "INSERT INTO brief_runs (id, brief_id, brief_version, brief_hash, status)"
        " VALUES (%s, %s, %s, %s, 'succeeded')",
        (rid, brief_id, version, "a" * 64),
    )
    return rid


def test_r19_9_snapshots_kept_until_report_final_plus_12_months(capture_db, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    fetched = NOW - timedelta(days=500)
    used = put_ev(db, store, b"person page used by the brief", fetched_at=fetched)
    other = put_ev(db, store, b"person page no brief used", fetched_at=NOW - timedelta(days=100))
    old = put_ev(db, store, b"person page, old and unused", fetched_at=NOW - timedelta(days=800))
    run_a = _brief_run(db, "brief_synthetic", 1, 1)
    assert snapshot_retention.link(db, run_a, [used.id, used.id]) == 1
    cfg = RetentionConfig()

    # report not final: kept (ceiling 730 d from fetch not reached); the old unused one goes
    rep = purge(db, store, cfg=cfg, now=NOW)
    assert rep.snapshots_dropped_ceiling == 1 and not store.exists(old.content_hash)
    assert store.exists(used.content_hash) and store.exists(other.content_hash)
    assert rep.snapshots_held_pending_report == 1

    # report final 11 months ago: still kept; 13 months ago: dropped, hash and row kept
    snapshot_retention.mark_report_final(
        db, "brief_synthetic", 1, at=NOW - timedelta(days=330), brief_run_id=run_a
    )
    assert purge(db, store, cfg=cfg, now=NOW).person_level_hashes_dropped == 0
    snapshot_retention.mark_report_final(db, "brief_synthetic", 1, at=NOW - timedelta(days=400))
    with RunRecorder("retention.purge", {}, sink=db.upsert_run, detect_commit=False) as run:
        rep = purge(db, store, cfg=cfg, now=NOW, run=run)
    assert rep.snapshots_dropped_report_final == 1 and not store.exists(used.content_hash)
    row = db.conn.execute(
        "SELECT deletion_state, content_hash FROM evidence WHERE id = %s", (used.id,)
    ).fetchone()
    assert row == ("raw_dropped", used.content_hash)  # coded facts + hash are the record
    assert db.conn.execute(
        "SELECT reason, action, content_hash, run_id FROM deletion_log WHERE content_hash = %s",
        (used.content_hash,),
    ).fetchall() == [("retention", "raw_dropped", used.content_hash, run.id)]
    assert run.counts["snapshots_dropped_report_final"] == 1
    assert store.exists(other.content_hash)  # unreferenced: ceiling not reached


def test_r19_9_a_pending_report_holds_a_shared_snapshot_until_the_ceiling(capture_db, tmp_path):
    db = capture_db
    store = LocalSnapshotStore(tmp_path / "snap")
    ev = put_ev(db, store, b"page used by two briefs", fetched_at=NOW - timedelta(days=600))
    a, b = _brief_run(db, "brief_a", 1, 1), _brief_run(db, "brief_b", 2, 2)
    snapshot_retention.link(db, a, [ev.id])
    snapshot_retention.link(db, b, [ev.id])
    snapshot_retention.mark_report_final(db, "brief_a", 1, at=NOW - timedelta(days=500))
    got = snapshot_retention.person_snapshots(db.conn, after_report_days=365, ceiling_days=730)
    (s,) = got
    assert s.basis == "ceiling_pending_report"
    assert s.due_at == ev.fetched_at + timedelta(days=730)  # later of ceiling and final+365
    assert purge(db, store, now=NOW).person_level_hashes_dropped == 0
    snapshot_retention.mark_report_final(db, "brief_b", 2, at=NOW - timedelta(days=366))
    assert purge(db, store, now=NOW).snapshots_dropped_report_final == 1


def test_r19_9_due_at_rules():
    t = NOW
    kw = {"after_report_days": 365, "ceiling_days": 730}
    assert snapshot_retention.due_at(
        newest_fetch=t, referenced=False, pending=False, last_final=None, **kw
    ) == (t + timedelta(days=730), "ceiling_unreferenced")
    assert snapshot_retention.due_at(
        newest_fetch=t, referenced=True, pending=False, last_final=t + timedelta(days=900), **kw
    ) == (t + timedelta(days=1265), "report_final")  # final + 12 months may pass 24 months
    assert snapshot_retention.due_at(
        newest_fetch=t, referenced=True, pending=True, last_final=None, **kw
    ) == (t + timedelta(days=730), "ceiling_pending_report")


def test_r19_9_cli_report_final_and_retention_setting(
    capture_db, pg_url, tmp_path, monkeypatch, capsys
):
    from pigtail.config import Settings

    assert Settings.from_env({}).snapshot_after_report_days == 365
    with pytest.raises(ValueError, match="SNAPSHOT_AFTER_REPORT_DAYS"):
        Settings.from_env({"SNAPSHOT_AFTER_REPORT_DAYS": "400"})  # ceiling: 12 months
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    argv = ["retention", "report-final", "--brief", "brief_synthetic", "--version", "3"]
    assert main([*argv, "--at", "2026-09-20T10"]) == 0
    capsys.readouterr()
    row = capture_db.conn.execute(
        "SELECT brief_id, brief_version, report_final_at, run_id IS NOT NULL"
        " FROM brief_report_final"
    ).fetchone()
    assert row == ("brief_synthetic", 3, datetime(2026, 9, 20, 10, tzinfo=UTC), True)
    cfg = capture_db.conn.execute(
        "SELECT config::text FROM runs WHERE job = 'retention.report_final'"
    ).fetchone()
    assert cfg is not None and "brief_synthetic" not in cfg[0]  # ids stay out of run records


# --- mention scope (Directive §8.3) ---------------------------------------------------------------
def test_m21a_cli_shortlist_scope(capture_db, pg_url, tmp_path, monkeypatch, capsys):
    from pigtail.capture import scope

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    base = ["capture", "shortlist", "set", "--brief", "brief_synthetic", "--version", "1"]
    assert main([*base, "--status", "in_review", "--repo", "Org-A/Repo-1", "--repo", "b/c"]) == 0
    assert scope.in_scope(capture_db, "org-a/repo-1") and scope.in_scope(capture_db, "B/C")
    assert main([*base, "--status", "removed", "--repo", "b/c"]) == 0
    assert not scope.in_scope(capture_db, "b/c")
    assert main([*base, "--status", "final", "--repo", "not a repo"]) == 2
    capsys.readouterr()
    assert main(["capture", "shortlist", "list"]) == 0
    assert "org-a/repo-1" in capsys.readouterr().out
