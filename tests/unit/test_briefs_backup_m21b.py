"""The briefs directory in the encrypted backup (ADR-071.3, PRD R18.9; DPIA CB-17; M21b).

A fake `age` (a Python one-liner that writes the age header and passes bytes through) stands
in for the real tool, so the archive format, the restore rules and pruning are tested without
`age`/`gpg` installed; `test_backup_cb17.py` covers the real tools. Synthetic briefs only.
"""

from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs import backup as bb
from pigtail.briefs.model import load_brief_text
from pigtail.briefs.store import BriefStore
from pigtail.privacy.backup import AGE_HEADER, BackupError

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"
N = len(AGE_HEADER)
ENC = (
    "import sys; o=open(sys.argv[1],'wb'); "
    f"o.write({AGE_HEADER!r}); o.write(sys.stdin.buffer.read())"
)
DEC = f"import sys; d=open(sys.argv[1],'rb').read(); sys.stdout.buffer.write(d[{N}:])"


@pytest.fixture(autouse=True)
def fake_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bb, "_encrypt_cmd", lambda tool, r, out: [sys.executable, "-c", ENC, str(out)]
    )
    monkeypatch.setattr(bb, "_decrypt_cmd", lambda tool, p, i: [sys.executable, "-c", DEC, str(p)])


def make_store(root: Path, *ids: str) -> BriefStore:
    s = BriefStore(root)
    for bid in ids:
        b = load_brief_text(EXAMPLE.read_text()).model_copy(update={"brief_id": bid})
        stored = s.create(b)
        s.save_version(stored.brief.model_copy(update={"notes": "synthetic edit"}), base_version=1)
    (root / "notes.txt").write_text("not a brief")  # never archived
    return s


def test_adr_071_3_archive_is_encrypted_and_holds_only_version_files(tmp_path: Path):
    src = tmp_path / "briefs"
    make_store(src, "synthetic-a", "synthetic-b")
    res = bb.create_briefs_archive(
        src, tmp_path / "out", recipient="age1synthetic", stamp="20260926T120000Z"
    )
    p = Path(res.path)
    assert p.name == "pigtail-briefs-20260926T120000Z.age"
    assert (res.briefs, res.versions) == (2, 4)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    raw = p.read_bytes()
    assert raw.startswith(AGE_HEADER)  # the output is "encrypted" (never plaintext on disk)
    body = raw[len(AGE_HEADER) :]
    assert body.startswith(bb.MAGIC)
    with tarfile.open(fileobj=io.BytesIO(body[len(bb.MAGIC) :]), mode="r|") as tar:
        names = sorted(m.name for m in tar)
    assert names == [f"briefs/synthetic-{x}/v000{n}.yaml" for x in "ab" for n in (1, 2)]
    assert not list(tmp_path.glob("out/*.partial"))


def test_adr_071_3_restore_adds_missing_versions_and_never_overwrites(tmp_path: Path):
    src = tmp_path / "briefs"
    make_store(src, "synthetic-a")
    res = bb.create_briefs_archive(src, tmp_path / "out", recipient="age1synthetic")
    dst = tmp_path / "restored"
    got = bb.restore_briefs_archive(Path(res.path), dst)
    assert (got.restored, got.already_present, got.conflicts) == (2, 0, 0)
    restored = BriefStore(dst)
    assert [v.version for v in restored.versions("synthetic-a")] == [1, 2]
    assert stat.S_IMODE(os.stat(dst / "synthetic-a").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(dst / "synthetic-a" / "v0001.yaml").st_mode) == 0o600
    # a second restore finds everything present; a changed file is a conflict and kept
    (dst / "synthetic-a" / "v0002.yaml").write_text("locally different")
    got = bb.restore_briefs_archive(Path(res.path), dst)
    assert (got.restored, got.already_present, got.conflicts) == (0, 1, 1)
    assert (dst / "synthetic-a" / "v0002.yaml").read_text() == "locally different"


def test_adr_071_3_restore_rejects_unsafe_members(tmp_path: Path):
    buf = io.BytesIO()
    buf.write(bb.MAGIC)
    with tarfile.open(fileobj=buf, mode="w|") as tar:
        for name in ("../evil.yaml", "/abs/v0001.yaml", "briefs/Bad_ID/v0001.yaml",
                     "briefs/synthetic-ok/v0001.yaml"):  # fmt: skip
            info = tarfile.TarInfo(name)
            info.size = 3
            tar.addfile(info, io.BytesIO(b"abc"))
    buf.seek(len(bb.MAGIC))
    got = bb.restore_members(buf, tmp_path / "dst")
    assert (got.restored, got.rejected_members) == (1, 3)
    assert not (tmp_path / "evil.yaml").exists()


def test_adr_071_3_not_an_archive_and_missing_recipient_are_refused(tmp_path: Path):
    with pytest.raises(BackupError):
        bb.create_briefs_archive(tmp_path, tmp_path / "out", recipient=None)
    bogus = tmp_path / "pigtail-briefs-20260926T120000Z.age"
    bogus.write_bytes(AGE_HEADER + b"not the magic")
    with pytest.raises(BackupError):
        bb.restore_briefs_archive(bogus, tmp_path / "dst")
    with pytest.raises(BackupError):
        bb.restore_briefs_archive(tmp_path / "other-name.age", tmp_path / "dst")


def test_adr_071_3_companion_name_and_prune_35_days(tmp_path: Path):
    db = tmp_path / "pigtail-backup-20260926T120000Z.age"
    assert bb.companion_of(db) == tmp_path / "pigtail-briefs-20260926T120000Z.age"
    assert bb.companion_of(tmp_path / "x.age") is None
    now = datetime(2026, 9, 26, tzinfo=UTC)
    old = bb.archive_name(bb.stamp_of(now - timedelta(days=40)), "age")
    new = bb.archive_name(bb.stamp_of(now - timedelta(days=2)), "age")
    for n in (old, new, "unrelated.txt"):
        (tmp_path / n).write_text("x")
    res = bb.prune_briefs_archives(tmp_path, now=now)
    assert res.deleted == [old] and res.kept == [new]
    assert (tmp_path / "unrelated.txt").exists()


def test_adr_071_3_cli_backup_helpers_create_and_restore_briefs(tmp_path, monkeypatch, capsys):
    from argparse import Namespace

    from pigtail.cli import _backup_briefs, _restore_briefs
    from pigtail.config import Settings

    src = tmp_path / "briefs"
    make_store(src, "synthetic-a")
    monkeypatch.setenv("BACKUP_RECIPIENT", "age1synthetic")
    s = Settings.from_env({"PIGTAIL_BRIEFS_DIR": str(src)})
    out_dir = tmp_path / "backups"
    out_dir.mkdir()
    db_backup = out_dir / "pigtail-backup-20260926T120000Z.age"
    out: dict[str, Any] = {}
    assert _backup_briefs(s, db_backup, out) == 0
    assert out["briefs"]["versions"] == 2
    assert (out_dir / "pigtail-briefs-20260926T120000Z.age").is_file()
    target = Settings.from_env({"PIGTAIL_BRIEFS_DIR": str(tmp_path / "restored")})
    args = Namespace(no_briefs=False, briefs_in=None, identity=None)
    got = _restore_briefs(args, target, db_backup)
    assert got is not None and got["status"] == "restored" and got["restored"] == 2
    assert (tmp_path / "restored" / "synthetic-a" / "v0002.yaml").is_file()
    assert (
        _restore_briefs(Namespace(no_briefs=True, briefs_in=None, identity=None), target, db_backup)
        is None
    )
    missing = _restore_briefs(args, target, out_dir / "pigtail-backup-20250101T000000Z.age")
    assert missing == {"status": "not_found"}
    assert "not restored" in capsys.readouterr().err
