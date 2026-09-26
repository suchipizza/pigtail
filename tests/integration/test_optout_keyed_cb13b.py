"""CB-13b (ADR-040.4): repo-name opt-outs use a keyed hash (HMAC-SHA256 with PSEUDONYM_KEY,
namespace `repo_name`), and migration 0009 keeps legacy unkeyed entries matched until converted.

Synthetic names only (`org-a/repo-1`, `org-z/…`).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.capture.db import CaptureDB
from pigtail.cli import main
from pigtail.config import Settings
from pigtail.db.migrate import default_migrations_dir, migrate
from pigtail.privacy import requests, suppression
from pigtail.privacy.doctor import run_checks
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY

SRC = Path(__file__).resolve().parents[2] / "src" / "pigtail"


# --- unit: the key itself -------------------------------------------------------------------
def test_cb13b_repo_name_key_is_hmac_sha256_in_repo_name_namespace(pz):
    expected = hmac.new(TEST_KEY.encode(), b"repo_name:org-a/repo-1", hashlib.sha256).hexdigest()
    assert suppression.repo_name_key("org-a/repo-1", pz) == "rk_" + expected[:32]
    # normalized, lowercase owner/name: every spelling of the same repo gives one key
    for spelling in ("Org-A/Repo-1", "https://github.com/org-a/repo-1.git", "org-a/repo-1/"):
        assert suppression.repo_name_key(spelling, pz) == "rk_" + expected[:32]
    other = Pseudonymizer("another-test-key-not-secret-000")
    assert suppression.repo_name_key("org-a/repo-1", other) != "rk_" + expected[:32]
    # without the key, the unkeyed dictionary hash does not match the stored value
    assert suppression.legacy_repo_name_key("org-a/repo-1")[3:] != expected[:32]
    assert suppression.repo_name_key("org-a/repo-1", pz, host="gitlab") != (
        suppression.repo_name_key("org-a/repo-1", pz)
    )


def test_cb13b_suppressions_match_keyed_and_legacy_and_fail_closed_without_key(pz):
    keyed = suppression.Suppressions(
        repo_names=frozenset({suppression.repo_name_key("org-a/repo-1", pz)}),
        legacy_repo_names=frozenset({suppression.legacy_repo_name_key("org-b/repo-2")}),
        name_key=suppression.name_keyer(pz),
    )
    assert keyed.name_suppressed("github.com/Org-A/Repo-1")
    assert keyed.name_suppressed("org-b/repo-2")  # legacy entry still honoured
    assert not keyed.name_suppressed("org-c/repo-3")
    assert not keyed.name_suppressed("not a name")
    no_key = suppression.Suppressions(repo_names=keyed.repo_names)
    with pytest.raises(suppression.MissingNameKey):
        no_key.name_suppressed("org-a/repo-1")


def test_cb13b_unkeyed_values_are_refused_by_add():
    with pytest.raises(ValueError):
        suppression._check("repo_name", suppression.legacy_repo_name_key("org-a/repo-1"))
    with pytest.raises(ValueError, match="legacy"):
        suppression._check("repo_name_unkeyed", suppression.legacy_repo_name_key("org-a/x"))


def test_cb13b_every_enforcement_point_uses_the_keyed_hash():
    """The unkeyed function is only used to match/convert legacy rows, never to write."""
    users = sorted(
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "legacy_repo_name_key" in p.read_text(encoding="utf-8")
    )
    assert users == ["privacy/requests.py", "privacy/suppression.py"]
    # (mypy also rejects a call without the Pseudonymizer; this catches `# type: ignore`d ones)

    old_shape = re.compile(
        r"(?<!legacy_)repo_name_key\([a-z_][\w.]*(?:, *(?:host|platform|args\.platform))?\)"
    )
    for p in SRC.rglob("*.py"):
        assert not old_shape.search(p.read_text(encoding="utf-8")), p  # unkeyed call shape


# --- migration 0009 ---------------------------------------------------------------------------
def _migrate_to_0008(url: str, tmp_path: Path) -> None:
    d = tmp_path / "mig"
    d.mkdir()
    for p in sorted(default_migrations_dir().glob("*.sql")):
        if p.name[:4] <= "0008":
            shutil.copy(p, d / p.name)
    migrate(url, d)


@pytest.mark.db
def test_cb13b_migration_0009_keeps_legacy_rows_matched_and_warns(pg_url, tmp_path, caplog, pz):
    _migrate_to_0008(pg_url, tmp_path)
    legacy_a = suppression.legacy_repo_name_key("org-a/repo-1")
    legacy_z = suppression.legacy_repo_name_key("org-z/unknown")
    with psycopg.connect(pg_url, autocommit=True) as c:
        for v in (legacy_a, legacy_z):
            c.execute(
                "INSERT INTO privacy_suppression (kind, value, platform, reason)"
                " VALUES ('repo_name', %s, 'github', 'objection')",
                (v,),
            )
    with caplog.at_level(logging.WARNING, logger="pigtail.db.migrate"):
        assert "0009" in migrate(pg_url)
    assert any(
        "2 repo-name opt-out(s) still use the unkeyed hash" in r.message for r in caplog.records
    )
    db = CaptureDB.connect(pg_url)
    try:
        kinds = db.conn.execute("SELECT kind, value FROM privacy_suppression ORDER BY value")
        assert sorted(kinds.fetchall()) == sorted(
            [("repo_name_unkeyed", legacy_a), ("repo_name_unkeyed", legacy_z)]
        )
        s = suppression.load(db, pz)
        assert s.name_suppressed("Org-A/Repo-1")  # still honoured after the migration
        # no new unkeyed rows, and no unkeyed value under the keyed kind
        for kind, value in (
            ("repo_name_unkeyed", suppression.legacy_repo_name_key("org-q/new")),
            ("repo_name", suppression.legacy_repo_name_key("org-q/new")),
        ):
            with pytest.raises(psycopg.errors.Error), db.conn.transaction():
                db.conn.execute(
                    "INSERT INTO privacy_suppression (kind, value, platform, reason)"
                    " VALUES (%s, %s, 'github', 'objection')",
                    (kind, value),
                )
        # doctor warns while legacy rows are left
        settings = Settings.from_env({"DATABASE_URL": pg_url, "PSEUDONYM_KEY": TEST_KEY})
        doc = {c.name: c for c in run_checks(settings)}
        assert doc["optout_name_keys"].status == "warn"

        # rekey: org-a/repo-1 is known locally (watch list), org-z/unknown is not
        db.conn.execute(
            "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
            " VALUES ('github:1000001', 'github', 1000001, 'org-a/repo-1', now())"
        )
        counts = requests.rekey_unkeyed_names(db, pz)
        assert counts == {"converted": 1, "remaining": 1}
        rows = dict(db.conn.execute("SELECT value, kind FROM privacy_suppression").fetchall())
        assert rows == {
            suppression.repo_name_key("org-a/repo-1", pz): "repo_name",
            legacy_z: "repo_name_unkeyed",
        }
        assert suppression.load(db, pz).name_suppressed("org-a/repo-1")
        # re-adding the unknown name converts it
        res = requests.optout_repo_name(
            db, _NoStore(), platform="github", full_name="org-z/unknown", pz=pz, purge=False
        )
        assert res.counts == {"suppression_added": 1, "legacy_name_replaced": 1}
        assert suppression.legacy_count(db) == 0
        assert {c.name: c for c in run_checks(settings)}["optout_name_keys"].status == "ok"
    finally:
        db.close()


class _NoStore:
    """A snapshot store that must not be touched (purge=False)."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"store.{name} called")


@pytest.mark.db
def test_cb13b_load_fails_closed_without_key(capture_db, pz, monkeypatch):
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    suppression.add(
        capture_db, "repo_name", suppression.repo_name_key("org-a/repo-1", pz),
        platform="github", reason="objection",
    )  # fmt: skip
    with pytest.raises(suppression.MissingNameKey):
        suppression.load(capture_db)
    monkeypatch.setenv("PSEUDONYM_KEY", TEST_KEY)
    assert suppression.load(capture_db).name_suppressed("org-a/repo-1")


@pytest.mark.db
def test_cb13b_cli_rekey(capture_db, pg_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", TEST_KEY)
    assert main(["privacy", "optout", "rekey"]) == 0
    out = capsys.readouterr()
    assert '"converted": 0' in out.out and '"remaining": 0' in out.out


@pytest.mark.db
def test_cb13b_purge_by_legacy_key_still_finds_the_name(capture_db, pz):
    """Until converted, a legacy entry still reaches data held by name (reapply after restore)."""
    capture_db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:1000001', 'github', 1000001, 'org-a/repo-1', now())"
    )
    legacy = suppression.legacy_repo_name_key("org-a/repo-1")
    assert requests.names_for_key(capture_db, legacy, requests._keyer(legacy, pz, "github")) == [
        "org-a/repo-1"
    ]
    keyed = suppression.repo_name_key("org-a/repo-1", pz)
    assert requests.names_for_key(capture_db, keyed, requests._keyer(keyed, pz, "github")) == [
        "org-a/repo-1"
    ]
