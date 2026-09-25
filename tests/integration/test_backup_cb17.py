"""CB-17: encrypted backups, restore that re-applies deletions, 35-day pruning (synthetic data).

The round-trip tests need the PostgreSQL client tools (`pg_dump`, `pg_restore`, `psql`) and
`age` or `gpg`; they skip cleanly without them, unless `PIGTAIL_REQUIRE_BACKUP_TOOLS=1` (CI).
Each test generates its own throwaway keypair and database.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.capture.db import CaptureDB
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.cli import main
from pigtail.privacy import backup, requests, suppression
from pigtail.privacy.deletion import REPO_TABLES, DeletionLog, drop_raw, mark_deleted_upstream
from tests.conftest import TEST_KEY
from tests.integration.test_privacy_ops import put_ev
from tests.integration.test_repo_purge_cb13c import X, Y, rows_of, seed

pytestmark = pytest.mark.db

PG_TOOLS = ("pg_dump", "pg_restore", "psql")


def _require(what: str, ok: bool) -> None:
    if ok:
        return
    if os.environ.get("PIGTAIL_REQUIRE_BACKUP_TOOLS") == "1":
        pytest.fail(f"{what} required (PIGTAIL_REQUIRE_BACKUP_TOOLS=1) but not installed")
    pytest.skip(f"{what} not installed")


@dataclass
class Keys:
    tool: str
    recipient: str
    identity: str | None  # age identity file
    other_identity: str | None  # a second age identity (wrong key)


@pytest.fixture(params=["age", "gpg"])
def keys(request: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Keys]:
    """A throwaway keypair generated for this test (age-keygen, or gpg --batch)."""
    tool = request.param
    if tool == "age":
        _require("age", bool(shutil.which("age") and shutil.which("age-keygen")))
        ids = []
        for name in ("key.txt", "other.txt"):
            path = tmp_path / name
            subprocess.run(["age-keygen", "-o", str(path)], check=True, capture_output=True)
            ids.append(path)
        pub = subprocess.run(
            ["age-keygen", "-y", str(ids[0])], check=True, capture_output=True, text=True
        ).stdout.strip()
        yield Keys("age", pub, str(ids[0]), str(ids[1]))
        return
    if not shutil.which("gpg"):
        # gpg is optional when age is present (CI installs age; gpg is the fallback)
        pytest.skip("gpg not installed")
    home = tempfile.mkdtemp(prefix="pgpg")  # short path: gpg-agent socket length limit
    os.chmod(home, 0o700)
    monkeypatch.setenv("GNUPGHOME", home)
    uid = "pigtail backup test key"  # no e-mail: the public repo holds none
    subprocess.run(
        ["gpg", "--batch", "--passphrase", "", "--quick-gen-key", uid, "default", "default",
         "never"],
        check=True, capture_output=True,
    )  # fmt: skip
    out = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-keys", uid],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    try:
        yield Keys("gpg", fpr, None, None)
    finally:
        subprocess.run(["gpgconf", "--kill", "gpg-agent"], capture_output=True, check=False)
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def pg_tools() -> None:
    _require("PostgreSQL client tools", all(shutil.which(t) for t in PG_TOOLS))


@pytest.fixture
def env(pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(data))
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.setenv("PSEUDONYM_KEY", TEST_KEY)
    monkeypatch.delenv("BACKUP_RECIPIENT", raising=False)
    monkeypatch.delenv("BACKUP_IDENTITY", raising=False)
    return data


# --- no tools needed -----------------------------------------------------------------------------
def test_cb17_create_refuses_without_recipient(capture_db, pg_url, tmp_path):
    with pytest.raises(backup.BackupError, match="unencrypted"):
        backup.create(capture_db, pg_url, tmp_path / "out", recipient=None)
    with pytest.raises(backup.BackupError, match="unencrypted"):
        backup.create(capture_db, pg_url, tmp_path / "out", recipient="  ")
    assert not (tmp_path / "out").exists()


def test_cb17_create_refuses_output_inside_git_tree(capture_db, pg_url, tmp_path):
    (tmp_path / "checkout" / ".git").mkdir(parents=True)
    with pytest.raises(backup.BackupError, match="git working tree"):
        backup.create(
            capture_db, pg_url, tmp_path / "checkout" / "backups", recipient="age1" + "q" * 58
        )
    repo_root = Path(__file__).resolve().parents[2]
    with pytest.raises(backup.BackupError, match="git working tree"):
        backup.check_out_dir(repo_root / "data" / "backups")


def test_cb17_cli_create_without_recipient_exits_2(env, capture_db, tmp_path, capsys):
    assert main(["backup", "create", "--out", str(tmp_path / "b")]) == 2
    assert "BACKUP_RECIPIENT" in capsys.readouterr().err


def test_cb17_tool_choice_and_plaintext_refused(tmp_path):
    assert backup.tool_for_recipient("age1" + "q" * 58) == "age"
    assert backup.tool_for_recipient("ssh-ed25519 AAAA") == "age"
    assert backup.tool_for_recipient("0123456789ABCDEF0123456789ABCDEF01234567") == "gpg"
    plain = tmp_path / "pigtail-backup-20260901T000000Z.age"
    plain.write_bytes(backup.MAGIC + b"2\n{}PGDMP")
    with pytest.raises(backup.BackupError, match="not an age- or gpg-encrypted"):
        backup.detect_tool(plain)


def test_cb17_prune_keeps_35_days(tmp_path, capsys):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    names = {
        d: backup.backup_name(now - timedelta(days=d), "age" if d % 2 else "gpg")
        for d in (1, 20, 34, 36, 60)
    }
    for n in names.values():
        (tmp_path / n).write_bytes(b"x")
    stale = tmp_path / (backup.backup_name(now - timedelta(days=2), "age") + ".partial")
    fresh = tmp_path / (backup.backup_name(now - timedelta(hours=1), "age") + ".partial")
    other = tmp_path / "notes-2020.txt"
    for p in (stale, fresh, other):
        p.write_bytes(b"x")
    dry = backup.prune(tmp_path, now=now, dry_run=True)
    assert sorted(dry.deleted) == sorted([names[36], names[60], stale.name])
    assert (tmp_path / names[60]).exists()
    res = backup.prune(tmp_path, now=now)
    assert sorted(res.deleted) == sorted(dry.deleted)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == sorted([names[1], names[20], names[34], fresh.name, other.name])
    with pytest.raises(backup.BackupError, match="between 1 and 35"):
        backup.prune(tmp_path, days=36)
    assert main(["backup", "prune", "--dir", str(tmp_path), "--days", "40"]) == 2
    capsys.readouterr()
    assert main(["backup", "prune", "--dir", str(tmp_path), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def state(db: CaptureDB, evidence_id: str) -> str | None:
    row = db.conn.execute(
        "SELECT deletion_state FROM evidence WHERE id = %s", (evidence_id,)
    ).fetchone()
    return str(row[0]) if row else None


# --- round trip ----------------------------------------------------------------------------------
def _create(keys: Keys, out: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> Path:
    monkeypatch.setenv("BACKUP_RECIPIENT", keys.recipient)
    assert main(["backup", "create", "--out", str(out)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["tool"] == keys.tool
    return Path(res["path"])


def _restore(keys: Keys, path: Path, identity: str | None = None) -> int:
    args = ["backup", "restore", "--in", str(path), "--yes"]
    if keys.tool == "age":
        args += ["--identity", identity or str(keys.identity)]
    return main(args)


def test_cb17_restore_reapplies_deletions_made_after_the_backup(
    keys, pg_tools, env, capture_db, pg_url, tmp_path, monkeypatch, capsys, pz
):
    db = capture_db
    store = LocalSnapshotStore(env / "snapshots")
    t = datetime.now(UTC) - timedelta(days=2)
    x = seed(db, store, X)
    seed(db, store, Y)
    upstream = put_ev(db, store, b"hn item later deleted upstream", fetched_at=t, source="hn_fb")
    parsed = put_ev(db, store, b"search page dropped at parse", fetched_at=t, source="gh_search")
    kept = put_ev(db, store, b"project page kept", fetched_at=t, retention_class="project_level")
    blobs = {
        ev.content_hash: store.get(ev.content_hash)
        for ev in [*x["evidence"], upstream, parsed, kept]
    }
    y_before = {(r.table, r.column): rows_of(db, r, Y) for r in REPO_TABLES}

    path = _create(keys, tmp_path / "backups", monkeypatch, capsys)
    # the file is encrypted, private and holds no plaintext
    raw = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for marker in (backup.MAGIC, b"PGDMP", X[1].encode(), b"hn item later"):
        assert marker not in raw
    manifest = backup.read_manifest(path, identity=keys.identity)
    assert upstream.content_hash in manifest["snapshots"]["present_hashes"]
    assert "0011" in manifest["schema_versions"]

    # --- after the backup: an opt-out, an upstream deletion, a drop at parse, an erasure ------
    res = requests.optout_repo(db, store, platform="github", repo_key=x["key"], pz=pz)
    mark_deleted_upstream(db, store, upstream.content_hash, DeletionLog(db, "deleted_upstream"))
    drop_raw(db, store, parsed.content_hash, DeletionLog(db, "retention"))
    person = "p_" + f"{Y[0]:016x}"  # Y's synthetic event actor objects
    suppression.add(db, "pseudonym", person, platform="github", reason="erasure")
    requests.reapply_refusals(db, store, pz)
    db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:1000077', 'github', 1000077, 'org-z/after-backup', now())"
    )
    # disaster: the database *and* the snapshot bucket are restored to backup time
    for h, data in blobs.items():
        store._atomic_write(store._path(h), data)
    db.close()

    assert _restore(keys, path) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["carry_over_source"] == "live"
    assert out["carried"]["tombstones"] > 0 and out["carried"]["suppression"] >= 3

    db2 = CaptureDB.connect(pg_url)
    try:
        q = db2.conn.execute
        # the backup's data is back, rows added after it are gone ...
        assert q("SELECT count(*) FROM repos WHERE id = 'github:1000077'").fetchone() == (0,)
        assert store.exists(kept.content_hash)
        assert state(db2, kept.id) == "present"
        # ... but nothing deleted after the backup came back:
        # 1. the opted-out repo, in every registered table, and its raw bytes
        left = {(r.table, r.column): rows_of(db2, r, X) for r in REPO_TABLES}
        assert left == dict.fromkeys(left, 0), {k: v for k, v in left.items() if v}
        for ev in x["evidence"]:
            assert not store.exists(ev.content_hash)
        # 2. content deleted upstream (tombstone replay)
        assert state(db2, upstream.id) == "deleted_upstream"
        assert not store.exists(upstream.content_hash)
        # 3. a snapshot dropped at parse (tombstone replay)
        assert state(db2, parsed.id) == "raw_dropped"
        assert not store.exists(parsed.content_hash)
        # 4. the erased person's rows (opt-out list carried over and re-applied)
        assert q(
            "SELECT count(*) FROM repo_event_actor WHERE actor_pseudonym = %s", (person,)
        ).fetchone() == (0,)
        sup = suppression.load(db2, pz)
        assert x["key"] in sup.repos and person in sup.pseudonyms
        assert sup.name_suppressed(X[1])
        # the other repo is otherwise intact
        y_after = {(r.table, r.column): rows_of(db2, r, Y) for r in REPO_TABLES}
        assert y_after[("repo_event_actor", "repo_host_id")] == 0
        y_after.pop(("repo_event_actor", "repo_host_id"))
        y_before.pop(("repo_event_actor", "repo_host_id"))
        assert y_after == y_before
        # the request log and tombstones survived; the restore itself is recorded
        got = q("SELECT outcome FROM privacy_requests WHERE id = %s", (res.request_id,))
        assert got.fetchone() == ("completed",)
        assert (
            q("SELECT count(*) FROM deletion_log WHERE reason = 'deleted_upstream'").fetchone()[0]
            >= 1
        )
        jobs = {r[0] for r in q("SELECT job FROM runs")}
        assert {"backup.create", "backup.restore", "privacy.optout_purge"} <= jobs
        assert "retention.purge" in jobs
    finally:
        db2.close()

    # restoring the same backup again carries the same tombstones over again (the restore
    # replaced them) and ends in the same state; the bytes are already gone
    assert _restore(keys, path) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["carried"]["tombstones"] >= out["carried"]["tombstones"]
    assert again["replay"]["hashes_dropped"] == 0
    db3 = CaptureDB.connect(pg_url)
    try:
        left = {(r.table, r.column): rows_of(db3, r, X) for r in REPO_TABLES}
        assert left == dict.fromkeys(left, 0)
        assert state(db3, upstream.id) == "deleted_upstream"
    finally:
        db3.close()


def test_cb17_restore_with_wrong_key_changes_nothing(
    keys, pg_tools, env, capture_db, pg_url, tmp_path, monkeypatch, capsys
):
    if keys.tool != "age":
        pytest.skip("wrong-key case uses a second age identity")
    path = _create(keys, tmp_path / "backups", monkeypatch, capsys)
    capture_db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:1000088', 'github', 1000088, 'org-w/marker', now())"
    )
    assert _restore(keys, path, identity=keys.other_identity) == 1
    assert "age failed" in capsys.readouterr().err
    assert capture_db.conn.execute(
        "SELECT count(*) FROM repos WHERE id = 'github:1000088'"
    ).fetchone() == (1,)
    # a truncated file is refused and rolled back as well
    cut = path.with_name(backup.backup_name(datetime.now(UTC), "age"))
    cut.write_bytes(path.read_bytes()[: max(200, path.stat().st_size // 2)])
    assert _restore(keys, cut) == 1
    capsys.readouterr()
    assert capture_db.conn.execute(
        "SELECT count(*) FROM repos WHERE id = 'github:1000088'"
    ).fetchone() == (1,)
    assert main(["backup", "restore", "--in", str(path)]) == 2  # --yes is required
