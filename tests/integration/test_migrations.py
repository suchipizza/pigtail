"""M1-T1: forward-only migrations on a throwaway Postgres database."""

from __future__ import annotations

import shutil
from pathlib import Path

import psycopg
import pytest

from pigtail.db.migrate import MigrationError, default_migrations_dir, discover, migrate

pytestmark = pytest.mark.db


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
    assert {"repos", "cases", "evidence", "runs", "repo_hourly_activity", "gharchive_hours"} <= (
        tables
    )
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
