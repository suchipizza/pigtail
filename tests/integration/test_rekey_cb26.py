"""CB-26 (ADR-043): `pigtail privacy rekey` re-derives every stored keyed value under a new key in
one transaction, or refuses and changes nothing. Since M21a (Directive §8.1, ADR-071.1, migration
0017) those values are the opt-out entries only (person fingerprints and repo-name keys): no
table holds pseudonyms any more. CB-27 (dual-key window) is not needed because
of that atomicity. Synthetic handles (`user0001`, `hnuser01`, …) and repos (`org-a/repo-1`) only.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.cli import main
from pigtail.llm.store import LLMStore
from pigtail.privacy import key_fingerprint, rekey, requests, suppression
from pigtail.privacy.backup import BackupError, CarryOver, check_rotation
from pigtail.privacy.deletion import PERSON_TABLES
from pigtail.privacy.key_fingerprint import KeyFingerprintMismatch
from pigtail.privacy.rekey import HandleEntry, RekeyRefused
from pigtail.pseudonymize import Pseudonymizer
from tests.conftest import TEST_KEY
from tests.integration.test_privacy_ops import ingested  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

NEW_KEY = "new-test-key-not-secret-2468013579"  # gitleaks:allow
NOW = datetime(2026, 9, 25, tzinfo=UTC)
OLD = Pseudonymizer(TEST_KEY)
NEW = Pseudonymizer(NEW_KEY)


# --- helpers ------------------------------------------------------------------------------------
class mock_any:
    """Equal to anything (for fields like dates)."""

    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = None  # type: ignore[assignment]


def gh(pz: Pseudonymizer, handle: str) -> str:
    return suppression.subject_pseudonym(pz, "github", handle)


def hn(pz: Pseudonymizer, handle: str) -> str:
    return suppression.subject_pseudonym(pz, "hn", handle)


def private_handles(tmp_path: Path, lines: list[str]) -> Path:
    """A handles file outside any git tree (pytest's tmp dir), mode 0600."""
    d = tmp_path / "private"
    d.mkdir(exist_ok=True)
    p = d / "handles.txt"
    p.write_text("\n".join(["# old-key mapping (synthetic)", *lines]) + "\n", encoding="utf-8")
    os.chmod(p, 0o600)
    return p


def column(db: Any, table: str, col: str) -> list[Any]:
    return [r[0] for r in db.conn.execute(f"SELECT {col} FROM {table} ORDER BY 1 NULLS LAST")]


def optouts(db: Any) -> set[tuple[str, str]]:
    return {(e["kind"], e["value"]) for e in suppression.entries(db)}


@pytest.fixture
def seeded(capture_db: Any) -> tuple[Any, LLMStore]:
    """Opt-outs under the OLD key, fingerprint recorded, LLM cache."""
    db = capture_db
    suppression.load(db, OLD)  # records the old key's fingerprint (CB-25)
    suppression.add(db, "person", gh(OLD, "user0001"), platform="github", reason="objection")
    suppression.add(db, "person", hn(OLD, "hnuser01"), platform="hn", reason="erasure")
    suppression.add(
        db, "repo_name", suppression.repo_name_key("org-z/optout-1", OLD),
        platform="github", reason="objection",
    )  # fmt: skip
    suppression.add(db, "repo", "github:4242", platform="github", reason="objection")
    llm = LLMStore(":memory:", clock=lambda: NOW)
    llm.cache_put("k1", {"q": f"thanks @{gh(OLD, 'user0002')}"}, "m")
    return db, llm


FULL_HANDLES = [
    "github user0001",
    "hn hnuser01",
    "repo org-z/optout-1",
    "github user0002",
    "hn hnuser02",
]


# --- migration 0013 -----------------------------------------------------------------------------
def test_cb26_migration_0013_allows_rekey_event_and_key_rotation_reason(capture_db):
    db = capture_db
    db.conn.execute(
        "INSERT INTO pseudonym_key_fingerprint_log (event, new_fingerprint) VALUES ('rekey', %s)",
        (NEW.fingerprint(),),
    )
    db.conn.execute(
        "INSERT INTO deletion_log (reason, action, target) VALUES ('key_rotation',"
        " 'rows_deleted', 'hn_mention')"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.conn.execute(
            "INSERT INTO deletion_log (reason, action, target) VALUES ('rotation', 'x', 'y')"
        )


def test_cb26_every_pseudonym_column_is_registered(capture_db):
    """Completeness of the rotation: every column constrained to hold pseudonyms (`^p_…`) is
    the refusal list or a registered PERSON_TABLES column; no other table holds pseudonyms."""
    rows = capture_db.conn.execute(
        "SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid) FROM pg_constraint c"
        " WHERE c.contype = 'c' AND pg_get_constraintdef(c.oid) LIKE '%p_[0-9a-f]{16}%'"
    ).fetchall()
    tables = {t for t, _ in rows}
    assert tables == {t.table for t in PERSON_TABLES} | {"privacy_suppression"}
    for t in PERSON_TABLES:
        assert any(t.pseudonym_column in d for tab, d in rows if tab == t.table), t


# --- inputs -------------------------------------------------------------------------------------
def test_cb26_old_key_only_from_a_named_env_var_and_never_echoed():
    key = "old-test-key-not-secret-1357913579"  # gitleaks:allow
    got = rekey.old_key_from_env("OLD_PSEUDONYM_KEY", {"OLD_PSEUDONYM_KEY": key})
    assert got.fingerprint() == Pseudonymizer(key).fingerprint()
    with pytest.raises(ValueError, match="NAME of an environment variable") as e:
        rekey.old_key_from_env(key, {})  # the key pasted in place of the name
    assert key not in str(e.value)
    for new_name in ("PSEUDONYM_KEY", "OPTOUT_KEY"):  # the new key and its alias
        with pytest.raises(ValueError, match="other than OPTOUT_KEY"):
            rekey.old_key_from_env(new_name, {new_name: key})
    with pytest.raises(ValueError, match="not set"):
        rekey.old_key_from_env("OLD_PSEUDONYM_KEY", {})
    with pytest.raises(ValueError, match="shorter than 16") as e:
        rekey.old_key_from_env("OLD_PSEUDONYM_KEY", {"OLD_PSEUDONYM_KEY": "short"})
    assert "short" not in str(e.value).replace("shorter", "")


def test_cb26_handles_file_private_outside_git_and_errors_cite_lines_only(tmp_path):
    p = private_handles(tmp_path, ["github user0001", "repo org-a/repo-1", "", "hn hnuser01"])
    got = rekey.read_handles_file(p)
    assert [(e.kind, e.platform) for e in got] == [
        ("handle", "github"),
        ("repo", "repo"),
        ("handle", "hn"),
    ]
    assert "user0001" not in repr(got)  # values are never in a repr
    os.chmod(p, 0o644)
    with pytest.raises(rekey.HandlesFileError, match="chmod 600"):
        rekey.read_handles_file(p)
    os.chmod(p, 0o600)
    p.write_text("github user0001 extra\n")
    with pytest.raises(rekey.HandlesFileError, match="line 1") as e:
        rekey.read_handles_file(p)
    assert "user0001" not in str(e.value)
    p.write_text("mastodon user0001\n")
    with pytest.raises(rekey.HandlesFileError, match="line 1") as e:
        rekey.read_handles_file(p)
    assert "user0001" not in str(e.value) and "mastodon" not in str(e.value)
    p.write_text("repo not-a-repo-name\n")
    with pytest.raises(rekey.HandlesFileError, match="not an owner/name"):
        rekey.read_handles_file(p)
    in_repo = Path(__file__).resolve().parent / "handles-never-created.txt"
    with pytest.raises(rekey.HandlesFileError, match="git working tree"):
        rekey.read_handles_file(in_repo)


# --- the rotation -------------------------------------------------------------------------------
def test_cb26_rekey_maps_every_optout_and_row_in_one_transaction(seeded, tmp_path):
    db, llm = seeded
    handles = rekey.read_handles_file(private_handles(tmp_path, FULL_HANDLES))
    rep = rekey.rekey(db, None, OLD, NEW, handles=handles, llm_store=llm, require_exclusive=False)
    assert rep.committed and not rep.unmapped_optouts
    # refusal list: every keyed entry under the new key; repo ids unchanged
    assert optouts(db) == {
        ("person", gh(NEW, "user0001")),
        ("person", hn(NEW, "hnuser01")),
        ("repo_name", suppression.repo_name_key("org-z/optout-1", NEW)),
        ("repo", "github:4242"),
    }
    reasons = {(e["kind"], e["reason"]) for e in suppression.entries(db)}
    assert ("person", "erasure") in reasons  # platform, reason and date are kept
    # the fingerprint now names the new key (event `rekey`), and the new key works everywhere
    assert key_fingerprint.status(db.conn, NEW) == "ok"
    events = [r["event"] for r in key_fingerprint.history(db.conn)]
    assert events == ["recorded", "rekey"]
    sup = suppression.load(db, NEW)
    assert gh(NEW, "user0001") in sup.persons
    assert sup.name_suppressed("org-z/optout-1")
    with pytest.raises(KeyFingerprintMismatch):
        suppression.load(db, OLD)
    # LLM cache cleared (it quoted an old pseudonym); no old pseudonym is left anywhere
    assert llm.cache_count() == 0
    dump = db.conn.execute(
        "SELECT string_agg(t::text, ' ') FROM (SELECT * FROM privacy_suppression) t"
    ).fetchone()[0] + " ".join(
        str(v) for t in PERSON_TABLES for v in column(db, t.table, t.pseudonym_column)
    )
    for p in (gh(OLD, "user0001"), hn(OLD, "hnuser01"), gh(OLD, "user0002"), hn(OLD, "hnuser02")):
        assert p not in dump
    assert rep.counts["optouts_pseudonym_mapped"] == 2
    assert rep.counts["optouts_repo_name_mapped"] == 1
    assert rep.counts["person_pseudonyms"] == 0  # PERSON_TABLES is empty since 0017
    assert rep.counts["llm_cache_rows_deleted"] == 1


def test_cb26_erasure_under_new_key_matches_optouts_mapped_by_rekey(seeded, tmp_path):
    db, llm = seeded
    handles = rekey.read_handles_file(private_handles(tmp_path, FULL_HANDLES))
    rekey.rekey(db, None, OLD, NEW, handles=handles, llm_store=llm, require_exclusive=False)
    from pigtail.capture.snapshots import LocalSnapshotStore

    res = requests.erasure(
        db, LocalSnapshotStore(tmp_path / "s"), NEW, platform="hn", handle="hnuser01"
    )
    assert res.counts["suppression_added"] == 0  # already on the list under the new key
    assert res.counts["person_rows_deleted"] == 0


def test_cb26_refuses_when_an_optout_cannot_be_mapped_and_changes_nothing(seeded, tmp_path):
    db, llm = seeded
    before_optouts = optouts(db)
    # the hn opt-out's handle is missing
    lines = [x for x in FULL_HANDLES if x != "hn hnuser01"]
    handles = rekey.read_handles_file(private_handles(tmp_path, lines))
    with pytest.raises(RekeyRefused, match="1 opt-out") as e:
        rekey.rekey(
            db,
            None,
            OLD,
            NEW,
            handles=handles,
            llm_store=llm,
            purge_person_level=True,
            drop_unmapped=True,
            require_exclusive=False,
        )  # fmt: skip  (no flag overrides a lost opt-out)
    rep = e.value.report
    assert rep is not None and not rep.committed
    assert rep.unmapped_optouts == [
        {"kind": "person", "platform": "hn", "request_id": None, "added_at": mock_any()}
    ]
    assert hn(OLD, "hnuser01") not in json.dumps(rep.to_dict(), default=str)  # no pseudonym shown
    # rolled back: list, rows, fingerprint, fingerprint log and cache untouched
    assert optouts(db) == before_optouts
    assert key_fingerprint.status(db.conn, OLD) == "ok"
    assert [r["event"] for r in key_fingerprint.history(db.conn)] == ["recorded"]
    assert llm.cache_count() == 1


def test_cb26_m21a_person_table_options_have_nothing_left_to_do(seeded, tmp_path):
    """No table holds pseudonyms since 0017: once every opt-out is mapped the rotation commits
    without --drop-unmapped, and --purge-person-level deletes nothing."""
    db, _llm = seeded
    lines = ["github user0001", "hn hnuser01", "repo org-z/optout-1"]
    handles = rekey.read_handles_file(private_handles(tmp_path, lines))
    rep = rekey.rekey(
        db, None, OLD, NEW, handles=handles, purge_person_level=True, require_exclusive=False
    )
    assert rep.committed and rep.counts["person_pseudonyms_unmapped"] == 0
    assert ("person", gh(NEW, "user0001")) in optouts(db)  # opt-outs are still mapped
    assert db.conn.execute(
        "SELECT count(*) FROM deletion_log WHERE reason = 'key_rotation'"
    ).fetchone() == (0,)


def test_cb26_dry_run_reports_and_rolls_back(seeded, tmp_path):
    db, llm = seeded
    before = optouts(db)
    handles = rekey.read_handles_file(private_handles(tmp_path, FULL_HANDLES))
    rep = rekey.rekey(
        db, None, OLD, NEW, handles=handles, llm_store=llm, dry_run=True, require_exclusive=False
    )
    assert rep.dry_run and not rep.committed
    assert rep.counts["optouts_pseudonym_mapped"] == 2
    assert rep.counts["llm_cache_rows_deleted"] == 1  # would be deleted
    assert optouts(db) == before and llm.cache_count() == 1
    assert key_fingerprint.status(db.conn, OLD) == "ok"


def test_cb26_repo_name_mapped_from_names_held_locally(capture_db):
    db = capture_db
    suppression.load(db, OLD)
    suppression.add(
        db, "repo_name", suppression.repo_name_key("org-a/repo-9", OLD),
        platform="github", reason="objection",
    )  # fmt: skip
    db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:1000009', 'github', 1000009, 'Org-A/Repo-9', now())"
    )
    rep = rekey.rekey(db, None, OLD, NEW, require_exclusive=False)
    assert rep.counts["mapped_from_local_names"] == 1
    assert optouts(db) == {("repo_name", suppression.repo_name_key("org-a/repo-9", NEW))}


def test_cb26_optouts_mapped_from_retained_snapshots(ingested, pz):  # noqa: F811
    """No handles file: the GH Archive snapshots (which contain handles) map the opt-outs, the
    fingerprints being computed in memory while they are re-parsed."""
    db, store, _fetched, llm = ingested
    suppression.load(db, pz)
    suppression.add(db, "person", gh(pz, "user0001"), platform="github", reason="objection")
    rep = rekey.rekey(db, store, pz, NEW, llm_store=llm, require_exclusive=False)
    assert rep.committed
    assert optouts(db) == {("person", gh(NEW, "user0001"))}
    assert rep.counts["mapped_from_snapshots"] == 1
    assert rep.counts["snapshots_scanned"] >= 1


def test_cb26_snapshot_scan_can_be_skipped(ingested, pz):  # noqa: F811
    db, store, _f, _llm = ingested
    suppression.load(db, pz)
    suppression.add(db, "person", gh(pz, "user0001"), platform="github", reason="objection")
    with pytest.raises(RekeyRefused, match="cannot be mapped"):
        rekey.rekey(db, store, pz, NEW, scan_snapshots=False, require_exclusive=False)


def test_cb26_wrong_old_key_same_key_and_already_rotated_are_refused(seeded, tmp_path):
    db, _llm = seeded
    other = Pseudonymizer("another-test-key-not-secret-000")  # gitleaks:allow
    with pytest.raises(KeyFingerprintMismatch):
        rekey.rekey(db, None, other, NEW, require_exclusive=False)
    with pytest.raises(RekeyRefused, match="same"):
        rekey.rekey(db, None, OLD, OLD, require_exclusive=False)
    handles = rekey.read_handles_file(private_handles(tmp_path, FULL_HANDLES))
    rekey.rekey(db, None, OLD, NEW, handles=handles, require_exclusive=False)
    with pytest.raises(RekeyRefused, match="already keyed with the new key"):
        rekey.rekey(db, None, OLD, NEW, handles=handles, require_exclusive=False)


def test_cb26_refuses_while_other_sessions_are_connected(seeded, pg_url):
    db, _llm = seeded
    with (
        psycopg.connect(pg_url),  # e.g. a running collector
        pytest.raises(RekeyRefused, match="other session"),
    ):
        rekey.rekey(db, None, OLD, NEW, handles=[HandleEntry("handle", "github", "x")])
    assert key_fingerprint.status(db.conn, OLD) == "ok"


# --- CLI ----------------------------------------------------------------------------------------
def test_cb26_cli_dry_run_then_rotation_never_prints_keys_or_handles(
    seeded, pg_url, tmp_path, monkeypatch, capsys
):
    db, _llm = seeded
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", NEW_KEY)
    monkeypatch.setenv("OLD_PSEUDONYM_KEY", TEST_KEY)
    hf = str(private_handles(tmp_path, FULL_HANDLES))
    db.close()  # the command refuses while other sessions are connected
    base = ["privacy", "rekey", "--old-key-env", "OLD_PSEUDONYM_KEY", "--handles-file", hf]
    assert main(base) == 2  # needs --dry-run or --confirm-rotation
    assert "--confirm-rotation" in capsys.readouterr().err
    assert main([*base, "--dry-run"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["committed"] is False
    assert main([*base, "--confirm-rotation"]) == 0
    out2 = capsys.readouterr()
    rep = json.loads(out2.out)
    assert rep["committed"] is True and rep["counts"]["optouts_unmapped"] == 0
    for text in (out.out, out.err, out2.out, out2.err):
        for secret in (TEST_KEY, NEW_KEY, "user0001", "hnuser01", "org-z/optout-1"):
            assert secret not in text
    assert main(["privacy", "key-fingerprint"]) == 0  # the new key is now the database's key
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "ok" and status["history"][-1]["event"] == "rekey"
    with psycopg.connect(pg_url) as c:
        run = c.execute(
            "SELECT status, config FROM runs WHERE job = 'privacy.rekey' ORDER BY started_at"
        ).fetchall()
    assert [r[0] for r in run] == ["succeeded", "succeeded"]
    assert hf not in json.dumps([r[1] for r in run])  # the run config holds no path


def test_cb26_cli_refusal_exits_2_with_report(seeded, pg_url, tmp_path, monkeypatch, capsys):
    db, _llm = seeded
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", NEW_KEY)
    monkeypatch.setenv("OLD_PSEUDONYM_KEY", TEST_KEY)
    db.close()
    rc = main(["privacy", "rekey", "--old-key-env", "OLD_PSEUDONYM_KEY", "--confirm-rotation"])
    assert rc == 2
    out = capsys.readouterr()
    assert "opt-out(s) cannot be mapped" in out.err
    assert json.loads(out.out)["counts"]["optouts_unmapped"] == 3
    rc = main(["privacy", "rekey", "--old-key-env", TEST_KEY, "--dry-run"])  # key as the name
    assert rc == 2 and TEST_KEY not in capsys.readouterr().err


# --- backups taken before a rotation (restore guard) --------------------------------------------
def test_cb26_restore_refuses_a_backup_taken_before_the_rotation():
    at = datetime(2026, 9, 20, 12, tzinfo=UTC)
    co = CarryOver(key_fingerprint_log=[{"event": "rekey", "logged_at": at}])
    with pytest.raises(BackupError, match="before the pseudonym key was rotated"):
        check_rotation(co, {"created_at": (at - timedelta(days=1)).isoformat()})
    with pytest.raises(BackupError):
        check_rotation(co, {})  # unknown creation time: refuse
    check_rotation(co, {"created_at": (at + timedelta(hours=1)).isoformat()})
    check_rotation(CarryOver(), {"created_at": at.isoformat()})  # no rotation: fine


def test_cb35_restore_refuses_a_backup_taken_before_a_bare_fingerprint_reset():
    """CB-35 (ADR-046.4): a bare `privacy key-fingerprint --reset` changes the key as surely as
    a rekey, so a backup taken before it is refused the same way."""
    at = datetime(2026, 9, 20, 12, tzinfo=UTC)
    co = CarryOver(key_fingerprint_log=[{"event": "reset", "logged_at": at}])
    with pytest.raises(BackupError, match=r"before the pseudonym key was rotated.*--reset"):
        check_rotation(co, {"created_at": (at - timedelta(days=1)).isoformat()})
    with pytest.raises(BackupError, match="--reset"):
        check_rotation(co, {})  # unknown creation time: refuse
    check_rotation(co, {"created_at": (at + timedelta(hours=1)).isoformat()})
    # the first-use `recorded` event is not a key change
    first = CarryOver(key_fingerprint_log=[{"event": "recorded", "logged_at": at}])
    check_rotation(first, {"created_at": (at - timedelta(days=1)).isoformat()})


def test_cb35_restore_guard_uses_the_most_recent_rekey_or_reset():
    rekey_at = datetime(2026, 9, 10, tzinfo=UTC)
    reset_at = datetime(2026, 9, 20, tzinfo=UTC)
    co = CarryOver(
        key_fingerprint_log=[
            {"event": "recorded", "logged_at": rekey_at - timedelta(days=30)},
            {"event": "rekey", "logged_at": rekey_at},
            {"event": "reset", "logged_at": reset_at},
        ]
    )
    between = {"created_at": (rekey_at + timedelta(days=1)).isoformat()}
    with pytest.raises(BackupError, match="--reset"):  # after the rekey, before the reset
        check_rotation(co, between)
    check_rotation(co, {"created_at": (reset_at + timedelta(minutes=1)).isoformat()})
    co.key_fingerprint_log.append({"event": "rekey", "logged_at": reset_at + timedelta(days=1)})
    with pytest.raises(BackupError, match="privacy rekey"):
        check_rotation(co, {"created_at": (reset_at + timedelta(hours=1)).isoformat()})


def test_cb35_restore_refuses_before_replacing_anything_after_a_reset(
    capture_db, pg_url, tmp_path, monkeypatch
):
    """End to end through `backup.restore()` without age/gpg: the manifest reader is stubbed,
    and the database must not be touched (restore_database never runs)."""
    from pigtail.capture.snapshots import LocalSnapshotStore
    from pigtail.privacy import backup

    db = capture_db
    key_fingerprint.verify(db.conn, OLD)  # recorded on first use
    key_fingerprint.reset(db.conn, NEW)  # bare reset: event `reset`
    before = key_fingerprint.history(db.conn)
    assert [e["event"] for e in before] == ["recorded", "reset"]

    path = tmp_path / "pigtail-backup-20260901T000000Z.age"
    path.write_bytes(b"age-encryption.org/v1\n")  # header only: never decrypted here
    monkeypatch.setattr(backup, "detect_tool", lambda p: "age")
    monkeypatch.setattr(
        backup,
        "read_manifest",
        lambda p, identity=None: {"created_at": "2026-09-01T00:00:00+00:00"},
    )

    def must_not_run(*a: Any, **k: Any) -> Any:
        raise AssertionError("restore_database ran despite a pre-reset backup")

    monkeypatch.setattr(backup, "restore_database", must_not_run)
    with pytest.raises(BackupError, match="key-fingerprint --reset"):
        backup.restore(
            path,
            pg_url,
            LocalSnapshotStore(tmp_path / "snap"),
            reapply=lambda _db: {},
            retention=lambda _db: {},
        )
    assert key_fingerprint.history(db.conn) == before
    assert key_fingerprint.stored(db.conn).fingerprint == NEW.fingerprint()  # type: ignore[union-attr]
