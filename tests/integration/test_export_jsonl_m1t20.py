"""M1-T20 (PRD §7): JSONL export alongside the database.

Synthetic data only: fake repos `org-a/repo-1`, `org-b/repo-2`, a fake pseudonym, fake HN ids.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.cli import main
from pigtail.export.jsonl import (
    EXPORT_FORMAT,
    REPO_ROOT,
    TABLE_LEVELS,
    ExportError,
    check_out_dir,
    export_jsonl,
)
from pigtail.privacy.deletion import PERSON_TABLES

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)
PSEUDO = "p_0123456789abcdef"


def seed(db: Any) -> None:
    x = db.conn.execute
    # inserted out of key order on purpose: the export must sort by primary key
    x(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at) VALUES"
        " ('github:1000002', 'github', 1000002, 'org-b/repo-2', %s),"
        " ('github:1000001', 'github', 1000001, 'org-a/repo-1', %s)",
        (T0, T0 - timedelta(hours=1)),
    )
    x(
        "INSERT INTO cases (id, repo_id, opened_at, trigger, status, detection) VALUES"
        " ('case_00000000000000000001', 'github:1000001', %s, 'velocity', 'live',"
        '  \'{"z_score": 4.5, "rule_version": "velocity-v0"}\')',
        (T0,),
    )
    x(
        "INSERT INTO launch_mode_window (scope, repo_id, starts_at, ends_at, created_at)"
        " VALUES ('tracked_project', 'github:1000001', %s, %s, %s)",
        (T0 - timedelta(days=7), T0, T0),
    )
    x(
        "INSERT INTO hn_mention (repo_full_name, item_id, item_type, author_role, author_bucket,"
        " automated_account, bot_rule_version, role_rule_version, match_kind, first_seen_at,"
        " last_seen_at) VALUES ('org-a/repo-1', 9000001, 'story', 'account', 'r0', false,"
        " 'bot-filter-v0', 'roles-v1', 'url', %s, %s)",
        (T0, T0),
    )
    x(
        "INSERT INTO privacy_suppression (kind, value, platform, reason)"
        " VALUES ('person', %s, 'github', 'objection')",
        (PSEUDO,),
    )
    x(
        "INSERT INTO ui_sessions (token_hash, created_at, last_seen_at, expires_at)"
        " VALUES (%s, %s, %s, %s)",
        ("f" * 64, T0, T0, T0 + timedelta(hours=12)),
    )


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_m1_t20_every_table_is_classified(capture_db):
    tables = {
        r[0]
        for r in capture_db.conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    }
    assert tables == set(TABLE_LEVELS)
    for t in PERSON_TABLES:  # registered person-level tables are never project-level here
        assert TABLE_LEVELS[t.table] in ("person", "never")
    assert "repo_event_actor" not in TABLE_LEVELS  # dropped in 0017 (Directive §8.1)
    assert TABLE_LEVELS["repo_event_hourly_agg"] == "project"  # counts only
    assert TABLE_LEVELS["privacy_suppression"] == "person"
    assert TABLE_LEVELS["ui_sessions"] == TABLE_LEVELS["ui_audit_log"] == "never"


def test_m1_t20_default_export_is_project_level_sorted_and_versioned(capture_db, pg_url, tmp_path):
    seed(capture_db)
    out = tmp_path / "export"
    res = export_jsonl(pg_url, out, now=T0)
    assert "hn_mention" not in res.tables and res.skipped["hn_mention"] == "person_level"
    assert res.skipped["ui_sessions"] == "never_exported"
    assert not (out / "hn_mention.jsonl").exists() and not (out / "ui_sessions.jsonl").exists()
    repos = lines(out / "repos.jsonl")
    assert [r["id"] for r in repos] == ["github:1000001", "github:1000002"]  # primary-key order
    assert all(r["schema_version"] == "v0" for r in repos)  # the row's own version
    (win,) = lines(out / "launch_mode_window.jsonl")
    assert win["schema_version"] == f"db-{res.db_schema_version}"
    assert win["ends_at"] == "2026-09-20T12:00:00+00:00"  # UTC ISO 8601
    assert win["starts_at"] == "2026-09-13T12:00:00+00:00"
    (case,) = lines(out / "cases.jsonl")
    assert case["detection"] == {"rule_version": "velocity-v0", "z_score": 4.5}
    raw = (out / "cases.jsonl").read_text()
    assert raw.index('"created_at"') < raw.index('"detection"')  # keys sorted
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["format"] == EXPORT_FORMAT and manifest["include_person_level"] is False
    assert manifest["tables"]["repos"]["rows"] == 2
    assert manifest["tables"]["repos"]["order_by"] == ["id"]
    assert (out / "repos.jsonl").stat().st_mode & 0o777 == 0o600
    assert out.stat().st_mode & 0o777 == 0o700
    assert PSEUDO not in "".join(p.read_text() for p in out.glob("*.jsonl"))
    # deterministic: a second export of the same state is byte-identical
    out2 = tmp_path / "export2"
    res2 = export_jsonl(pg_url, out2, now=T0)
    assert res2.tables == res.tables
    for p in out.glob("*.jsonl"):
        assert (out2 / p.name).read_bytes() == p.read_bytes()


def test_m1_t20_person_level_needs_flag_and_a_dir_outside_git(capture_db, pg_url, tmp_path):
    seed(capture_db)
    tree = tmp_path / "some-repo"
    (tree / ".git").mkdir(parents=True)
    with pytest.raises(ExportError, match="git working tree"):
        export_jsonl(pg_url, tree / "out", include_person_level=True)
    assert not (tree / "out").exists()
    # project-level exports may go into a (private) git working tree
    export_jsonl(pg_url, tree / "project", tables=["repos"])
    res = export_jsonl(pg_url, tmp_path / "private", include_person_level=True)
    (m,) = lines(tmp_path / "private" / "hn_mention.jsonl")
    assert m["author_role"] == "account" and "author" not in m
    assert res.tables["hn_mention"]["level"] == "person"
    (o,) = lines(tmp_path / "private" / "privacy_suppression.jsonl")
    assert o["value"] == PSEUDO  # the opt-out fingerprint is person-level (ADR-071.1)
    for t in ("ui_sessions", "ui_audit_log"):
        assert t not in res.tables and res.skipped[t] == "never_exported"


def test_m1_t20_never_into_the_source_tree():
    for flag in (False, True):
        with pytest.raises(ExportError, match="source tree"):
            check_out_dir(REPO_ROOT / "data" / "export", include_person_level=flag)
        with pytest.raises(ExportError, match="source tree"):
            check_out_dir(REPO_ROOT / "docs" / ".." / "tmp-export", include_person_level=flag)


def test_m1_t20_table_selection_and_unclassified_tables(capture_db, pg_url, tmp_path):
    seed(capture_db)
    res = export_jsonl(pg_url, tmp_path / "sel", tables=["cases", "repos", "hn_mention"])
    assert set(res.tables) == {"cases", "repos"} and res.skipped == {"hn_mention": "person_level"}
    with pytest.raises(ExportError, match="unknown table"):
        export_jsonl(pg_url, tmp_path / "bad", tables=["nope"])
    capture_db.conn.execute("CREATE TABLE zz_unclassified (id int PRIMARY KEY)")
    with pytest.raises(ExportError, match="unclassified"):
        export_jsonl(pg_url, tmp_path / "bad2")


def test_m1_t20_cli(capture_db, pg_url, tmp_path, monkeypatch, capsys):
    seed(capture_db)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    out = tmp_path / "cli"
    assert main(["export", "jsonl", "--out", str(out), "--tables", "repos,cases"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert set(body["tables"]) == {"cases", "repos"} and (out / "repos.jsonl").exists()
    run = capture_db.conn.execute(
        "SELECT config, counts FROM runs WHERE job = 'export.jsonl'"
    ).fetchone()
    assert run[0] == {"tables": ["repos", "cases"], "include_person_level": False}
    assert str(tmp_path) not in json.dumps(run[0])  # no output path in the run record
    assert run[1]["rows.repos"] == 2
    bad = ["export", "jsonl", "--out", str(REPO_ROOT / "data" / "x"), "--include-person-level"]
    assert main(bad) == 2 and "source tree" in capsys.readouterr().err
