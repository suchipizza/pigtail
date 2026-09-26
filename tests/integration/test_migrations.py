"""M1-T1: forward-only migrations on a throwaway Postgres database."""

from __future__ import annotations

import shutil
from pathlib import Path

import psycopg
import pytest

from pigtail.db.migrate import MigrationError, default_migrations_dir, discover, migrate

pytestmark = pytest.mark.db

M11_DROPPED = {
    "watchlist",
    "repo_count_snapshot",
    "github_graphql_batch",
    "hn_show_screen",
    "detection_agreement",
    "repo_hourly_activity",
    "gharchive_hours",
    "holdout_unseal_log",
    "settle_lag_schedule",
    "star_history_settle_obs",
}


def test_m1_t1_migrations_apply_and_are_idempotent(pg_url):
    versions = [m.version for m in discover(default_migrations_dir())]
    assert versions[:2] == ["0001", "0002"]
    assert migrate(pg_url) == versions
    assert migrate(pg_url) == []  # second run is a no-op
    with psycopg.connect(pg_url) as c:
        tables = {
            r[0]
            for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        applied = [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert {"repos", "cases", "evidence", "runs", "repo_star_daily", "launch_mode_window"} <= tables
    # M12 (0015): brief-run provenance, stage cache, shortlist decisions (R18.4, R18.6, R4.7)
    assert {"brief_runs", "brief_stage_cache", "shortlist_decision"} <= tables
    # M11 (0014): the global-collection tables are gone (ADR-047.6, ADR-049.5)
    assert not tables & M11_DROPPED
    assert applied == versions


def test_m1_t1_forward_only_edit_of_applied_migration_rejected(pg_url, tmp_path: Path):
    d = tmp_path / "migrations"
    shutil.copytree(default_migrations_dir(), d)
    migrate(pg_url, d)
    f = d / "0001_capture_v0.sql"
    f.write_text(f.read_text() + "\n-- edited\n")
    with pytest.raises(MigrationError, match="forward-only"):
        migrate(pg_url, d)


def test_m1_t1_new_migration_applies_incrementally(pg_url, tmp_path: Path):
    d = tmp_path / "migrations"
    shutil.copytree(default_migrations_dir(), d)
    migrate(pg_url, d)
    (d / "9999_extra.sql").write_text("CREATE TABLE extra_test (id int);")
    assert migrate(pg_url, d) == ["9999"]


def test_bad_filename_rejected(tmp_path: Path):
    (tmp_path / "1_bad.sql").write_text("")
    with pytest.raises(MigrationError):
        discover(tmp_path)


def test_failed_migration_rolls_back(pg_url, tmp_path: Path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE t_ok (id int);")
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE t_bad (id int); SELECT nonsense_fn();")
    with pytest.raises(psycopg.Error):
        migrate(pg_url, tmp_path)
    with psycopg.connect(pg_url) as c:
        assert [r[0] for r in c.execute("SELECT version FROM schema_migrations")] == ["0001"]
        assert c.execute("SELECT to_regclass('t_bad')").fetchone() == (None,)


def test_m11_0014_drops_global_collection_tables_with_count_tombstones(pg_url, tmp_path: Path):
    """M11 (ADR-047.6, ADR-049.5): 0014 discards the rows of the removed features, logs one
    `purpose_limitation` tombstone per non-empty table (counts only), and keeps the per-repo
    cache (star history) and the generic append-only function used by 0012."""
    d = tmp_path / "migrations"
    shutil.copytree(default_migrations_dir(), d)
    (d / "0014_rescope_drop_global_collection.sql").unlink()
    migrate(pg_url, d)
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute(
            "INSERT INTO watchlist (repo_host_id, full_name, source, added_at, last_nominated_at)"
            " VALUES (1, 'org-a/repo-1', 'search', now(), now()),"
            " (2, 'org-a/repo-2', 'hn', now(), now())"
        )
        c.execute(
            "INSERT INTO repo_hourly_activity (repo_host_id, hour, repo_name, stars_raw)"
            " VALUES (1, '2026-09-20T00:00Z', 'org-a/repo-1', 3)"
        )
        c.execute(
            "INSERT INTO repo_star_daily (repo_host_id, day, stars_net, week_label,"
            " day_boundary_tz, is_partial, fetched_at) VALUES (1, '2026-09-20', 4, '2026-09-20',"
            " 'x', false, now())"
        )
    shutil.copy(default_migrations_dir() / "0014_rescope_drop_global_collection.sql", d)
    assert migrate(pg_url, d) == ["0014"]
    with psycopg.connect(pg_url) as c:
        left = {
            r[0]
            for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert not left & M11_DROPPED
        assert c.execute("SELECT count(*) FROM repo_star_daily").fetchone() == (1,)  # cache kept
        logged = dict(
            c.execute(
                "SELECT target, rows_affected FROM deletion_log"
                " WHERE reason = 'purpose_limitation' AND action = 'rows_deleted'"
            ).fetchall()
        )
        assert logged == {"watchlist": 2, "repo_hourly_activity": 1}  # empty tables: no row
        assert c.execute("SELECT to_regproc('pigtail_append_only')").fetchone() != (None,)
        with pytest.raises(psycopg.errors.CheckViolation):  # scope and subject must agree
            c.execute(
                "INSERT INTO launch_mode_window (scope, starts_at, ends_at)"
                " VALUES ('brief', now(), now() + interval '1 day')"
            )
