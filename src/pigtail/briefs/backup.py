"""The briefs directory in the encrypted backup (PRD R18.9, ADR-071.3; DPIA CB-17).

`pigtail backup create` writes, next to the database backup and with the same timestamp, one
encrypted archive of the briefs directory:

    pigtail-briefs-<UTC yyyymmddThhmmssZ>.<age|gpg>  =  encrypt(b"PIGTAIL-BRIEFS 1\\n" + tar)

The tar stream holds only brief version files (`briefs/<id>/vNNNN.yaml`), is written straight
into the encryptor (`age` or `gpg`, to the same `BACKUP_RECIPIENT` as the database backup), and
is never on disk unencrypted. `pigtail backup restore` decrypts it and restores the versions
into `PIGTAIL_BRIEFS_DIR`: versions are immutable, so a file that already exists is never
overwritten (same bytes: skipped; different bytes: a conflict, reported and left alone).
`pigtail backup prune` deletes archives older than the backup retention (35 days).

The database backup format (`pigtail.privacy.backup`, `PIGTAIL-BACKUP 1`) is unchanged; this
archive is a second encrypted stream beside it (M21b; folding it into one stream is an ADR
candidate for the privacy track). Nothing here reads or prints brief content: counts only.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from pigtail.briefs.store import DIR_MODE, FILE_MODE
from pigtail.privacy.backup import (
    BACKUP_RETENTION_DAYS,
    BackupError,
    _decrypt_cmd,
    _encrypt_cmd,
    check_out_dir,
    detect_tool,
    tool_for_recipient,
)

MAGIC = b"PIGTAIL-BRIEFS 1\n"
NAME_RE = re.compile(r"^pigtail-briefs-(\d{8}T\d{6}Z)\.(age|gpg)$")
DB_NAME_RE = re.compile(r"^pigtail-backup-(\d{8}T\d{6}Z)\.(age|gpg)$")
MEMBER_RE = re.compile(r"^briefs/([a-z0-9][a-z0-9-]{1,62}[a-z0-9])/(v\d{4,}\.yaml)$")
MAX_MEMBER = 1 << 20  # a brief version is a few KB; refuse anything absurd
PARTIAL_SUFFIX = ".partial"


def archive_name(stamp: str, tool: str) -> str:
    return f"pigtail-briefs-{stamp}.{tool}"


def stamp_of(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def companion_of(db_backup: Path) -> Path | None:
    """The briefs archive taken with a database backup (same timestamp and tool), if any."""
    m = DB_NAME_RE.match(db_backup.name)
    if not m:
        return None
    return db_backup.with_name(archive_name(m.group(1), m.group(2)))


def version_files(briefs_dir: Path) -> list[tuple[str, Path]]:
    """(archive name, path) of every brief version file under `briefs_dir`."""
    out: list[tuple[str, Path]] = []
    if not briefs_dir.is_dir():
        return out
    for d in sorted(briefs_dir.iterdir()):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            arc = f"briefs/{d.name}/{p.name}"
            if p.is_file() and not p.is_symlink() and MEMBER_RE.match(arc):
                out.append((arc, p))
    return out


@dataclass(frozen=True)
class BriefsBackupResult:
    path: str
    tool: str
    briefs: int
    versions: int
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_briefs_archive(
    briefs_dir: Path,
    out_dir: Path,
    *,
    recipient: str | None,
    stamp: str | None = None,
    now: datetime | None = None,
) -> BriefsBackupResult:
    """Write the encrypted archive of `briefs_dir` into `out_dir` (never inside a git tree)."""
    if not recipient or not recipient.strip():
        raise BackupError("BACKUP_RECIPIENT is not set: refusing to write an unencrypted backup")
    recipient = recipient.strip()
    tool = tool_for_recipient(recipient)
    out = check_out_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = out / archive_name(stamp or stamp_of(now or datetime.now(UTC)), tool)
    if final.exists():
        raise BackupError(f"{final.name} already exists")
    files = version_files(briefs_dir)
    partial = final.with_name(final.name + PARTIAL_SUFFIX)
    partial.touch(mode=0o600)
    os.chmod(partial, 0o600)
    try:
        with tempfile.TemporaryFile() as enc_err:
            enc = subprocess.Popen(
                _encrypt_cmd(tool, recipient, partial),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=enc_err,
                bufsize=0,
            )
            assert enc.stdin is not None
            try:
                enc.stdin.write(MAGIC)
                with tarfile.open(fileobj=enc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                    for arc, p in files:
                        info = tarfile.TarInfo(arc)
                        data = p.read_bytes()
                        info.size = len(data)
                        info.mode = FILE_MODE
                        info.mtime = int(p.stat().st_mtime)
                        tar.addfile(info, io.BytesIO(data))
            finally:
                enc.stdin.close()
            rc = enc.wait()
            if rc != 0:
                enc_err.seek(0)
                tail = enc_err.read()[-600:].decode(errors="replace")
                raise BackupError(f"{tool} failed ({rc}): {tail}")
        os.chmod(partial, 0o600)
        if detect_tool(partial) != tool:
            raise BackupError("the briefs archive is not encrypted as expected")
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return BriefsBackupResult(
        path=str(final),
        tool=tool,
        briefs=len({arc.split("/")[1] for arc, _ in files}),
        versions=len(files),
        bytes=final.stat().st_size,
    )


@dataclass
class BriefsRestoreResult:
    restored: int = 0
    already_present: int = 0
    conflicts: int = 0
    rejected_members: int = 0
    conflict_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_magic(f: IO[bytes]) -> None:
    head = f.read(len(MAGIC))
    if head != MAGIC:
        raise BackupError("not a pigtail briefs archive")


def restore_members(
    stream: IO[bytes], briefs_dir: Path, *, dry_run: bool = False
) -> BriefsRestoreResult:
    """Restore brief versions from a decrypted archive stream (after the magic line)."""
    res = BriefsRestoreResult()
    root = briefs_dir.expanduser()
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for m in tar:
            match = MEMBER_RE.match(m.name)
            if not match or not m.isfile() or m.size > MAX_MEMBER:
                res.rejected_members += 1
                continue
            fobj = tar.extractfile(m)
            if fobj is None:
                res.rejected_members += 1
                continue
            data = fobj.read()
            brief_id, fname = match.group(1), match.group(2)
            target = root / brief_id / fname
            if target.exists():
                if target.read_bytes() == data:
                    res.already_present += 1
                else:
                    res.conflicts += 1
                    res.conflict_paths.append(f"{brief_id}/{fname}")
                continue
            res.restored += 1
            if dry_run:
                continue
            for d in (root, root / brief_id):
                d.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
                os.chmod(d, DIR_MODE)
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".yaml", dir=root / brief_id)
            try:
                os.fchmod(fd, FILE_MODE)
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.link(tmp, target)  # never overwrites: versions are immutable
            finally:
                Path(tmp).unlink(missing_ok=True)
    return res


def restore_briefs_archive(
    path: Path, briefs_dir: Path, *, identity: str | None = None, dry_run: bool = False
) -> BriefsRestoreResult:
    """Decrypt `path` and restore its brief versions into `briefs_dir` (never overwriting)."""
    if not NAME_RE.match(path.name):
        raise BackupError(f"{path.name} is not a pigtail briefs archive name")
    tool = detect_tool(path)
    with tempfile.TemporaryFile() as dec_err:
        dec = subprocess.Popen(
            _decrypt_cmd(tool, path, identity),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=dec_err,
        )
        assert dec.stdout is not None
        err: Exception | None = None
        res = BriefsRestoreResult()
        try:
            _read_magic(dec.stdout)
            res = restore_members(dec.stdout, briefs_dir, dry_run=dry_run)
        except (tarfile.TarError, BackupError) as e:
            err = e
        finally:
            dec.stdout.close()
            rc = dec.wait()
        if rc == 0 and err is not None:
            raise BackupError(f"briefs archive unreadable: {type(err).__name__}: {err}")
        if rc != 0:
            dec_err.seek(0)
            tail = dec_err.read()[-600:].decode(errors="replace")
            raise BackupError(f"{tool} failed to decrypt the briefs archive ({rc}): {tail}")
    return res


@dataclass(frozen=True)
class BriefsPruneResult:
    kept: list[str]
    deleted: list[str]
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prune_briefs_archives(
    directory: Path,
    *,
    days: int = BACKUP_RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> BriefsPruneResult:
    """Delete briefs archives older than `days` (the backup retention) and stale partials."""
    if not 1 <= days <= BACKUP_RETENTION_DAYS:
        raise BackupError(f"--days must be between 1 and {BACKUP_RETENTION_DAYS} (CB-17)")
    if not directory.is_dir():
        raise BackupError(f"no such directory: {directory}")
    now = now or datetime.now(UTC)
    kept: list[str] = []
    deleted: list[str] = []
    for p in sorted(directory.iterdir()):
        partial = p.name.endswith(PARTIAL_SUFFIX)
        m = NAME_RE.match(p.name.removesuffix(PARTIAL_SUFFIX))
        if m is None or not p.is_file():
            continue
        at = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        limit = now - timedelta(days=1 if partial else days)
        if at < limit:
            deleted.append(p.name)
            if not dry_run:
                p.unlink()
        else:
            kept.append(p.name)
    return BriefsPruneResult(kept=kept, deleted=deleted, dry_run=dry_run)
