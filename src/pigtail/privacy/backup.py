"""Encrypted backups, restore with deletions re-applied, 35-day pruning (DPIA CB-17).

retention-policy.md §2 ("Backups": 35-day rolling, deletions re-applied after a restore) and §5.

**Create** (`create()`, `pigtail backup create --out DIR`): one file
`pigtail-backup-<UTC yyyymmddThhmmssZ>.<age|gpg>`, mode 0600, holding

    b"PIGTAIL-BACKUP 1\\n" + b"<n>\\n" + <n bytes of manifest JSON> + <pg_dump custom-format dump>

encrypted as one stream to the public key in `BACKUP_RECIPIENT` (`age1…` or `ssh-…`: `age`;
anything else, preferably a full fingerprint: `gpg`). Nothing is written unencrypted: `pg_dump`
writes straight into the encryptor's stdin, the result must start with an age header or an
OpenPGP packet, and there is no option to skip encryption. The output directory must be outside
any git working tree (the repository is public). The manifest records the creation time, the
applied migrations, the size and last id of `deletion_log`, and the snapshot store manifest: the
hashes of every `present` evidence record, whose raw bytes live in the snapshot bucket (its
replicas are backed up by the bucket, not here).

**Restore** (`restore()`, `pigtail backup restore --in FILE --yes`) replaces the database at
`DATABASE_URL` and then re-applies every deletion, so a restore cannot bring deleted data back:

1. *Carry-over*: before anything changes, the live database's `deletion_log`, refusal list
   (`privacy_suppression`), request log and the run records they reference are read.
2. The file is decrypted (age identity file `BACKUP_IDENTITY`, or the gpg keyring) and the dump
   is replayed by `pg_restore | psql` inside **one transaction** that first drops and recreates
   schema `public`; the transaction commits only if decryption, `pg_restore` and `psql` all
   succeed, so a wrong key or a truncated file leaves the database untouched.
3. Pending migrations are applied; the carried-over rows are written back (tombstones logged
   after the backup, opt-outs added after it).
4. `replay_tombstones()`: every `raw_dropped` tombstone deletes its hash's raw bytes from the
   snapshot store again (the bucket may have been restored too) and moves evidence fetched up to
   the tombstone to `raw_dropped` (`deleted_upstream` for deletion-sync tombstones, whose tracked
   upstream items are also made due for re-check, so the next `deletion-sync` removes their
   rows); every `evidence_deleted` tombstone deletes its evidence row again (and its LLM cache).
   Bytes are kept when a *newer* present capture of the same content exists.
5. The whole refusal list is re-applied (`requests.reapply_refusals`, as `privacy optout purge`).
6. The retention purge runs (`retention.purge`, as `retention purge`).

Row-level tombstones (`rows_deleted`, `fields_cleared`, `cache_purged`, `error_text_cleared`)
carry no row keys; steps 5 and 6 recompute them from the refusal list and the time limits.

**Prune** (`prune()`, `pigtail backup prune --dir DIR`): deletes backup files older than 35 days
(the policy maximum; `--days` may only shorten it) and stale partial files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from pigtail.logsafe import scrub

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.snapshots import SnapshotStore
    from pigtail.llm.store import LLMStore

MAGIC = b"PIGTAIL-BACKUP 1\n"
AGE_HEADER = b"age-encryption.org/v1\n"
RECIPIENT_ENV = "BACKUP_RECIPIENT"
IDENTITY_ENV = "BACKUP_IDENTITY"
BACKUP_RETENTION_DAYS = 35  # retention-policy.md §2
NAME_RE = re.compile(r"^pigtail-backup-(\d{8}T\d{6}Z)\.(age|gpg)$")
PARTIAL_SUFFIX = ".partial"
CHUNK = 1 << 20
MAX_MANIFEST = 1 << 30

Tool = Literal["age", "gpg"]


class BackupError(RuntimeError):
    """A backup, restore or prune was refused or failed; nothing unsafe was written."""


# --- tools ---------------------------------------------------------------------------------------
def which(name: str) -> str | None:
    return shutil.which(name)


def tool_for_recipient(recipient: str) -> Tool:
    """`age` for age / SSH recipients, `gpg` for anything else (key id, fingerprint, e-mail)."""
    r = recipient.strip()
    return "age" if r.startswith(("age1", "ssh-ed25519 ", "ssh-rsa ")) else "gpg"


def available_tools() -> list[Tool]:
    """The encryption CLIs installed here, `age` first (preferred)."""
    out: list[Tool] = []
    if which("age"):
        out.append("age")
    if which("gpg"):
        out.append("gpg")
    return out


def _need(name: str) -> str:
    path = which(name)
    if path is None:
        raise BackupError(f"`{name}` is not installed (needed for backups, CB-17)")
    return path


def _encrypt_cmd(tool: Tool, recipient: str, out: Path) -> list[str]:
    if tool == "age":
        return [_need("age"), "--encrypt", "--recipient", recipient, "--output", str(out)]
    # --trust-model always: the operator named this exact key in BACKUP_RECIPIENT; the host
    # holds only the public key, which gpg would otherwise refuse as "untrusted" in batch mode.
    return [
        _need("gpg"), "--batch", "--yes", "--quiet", "--trust-model", "always", "--encrypt",
        "--recipient", recipient, "--output", str(out),
    ]  # fmt: skip


def _decrypt_cmd(tool: Tool, path: Path, identity: str | None) -> list[str]:
    if tool == "age":
        if not identity:
            raise BackupError(f"{IDENTITY_ENV} (path to the age identity file) is not set")
        return [_need("age"), "--decrypt", "--identity", identity, str(path)]
    return [_need("gpg"), "--batch", "--quiet", "--decrypt", str(path)]


def detect_tool(path: Path) -> Tool:
    """Which tool encrypted `path` (age header, else an OpenPGP packet); refuses plaintext."""
    with path.open("rb") as f:
        head = f.read(len(AGE_HEADER))
    if head.startswith(AGE_HEADER):
        return "age"
    if head[:1] and head[0] & 0x80 and not head.startswith(MAGIC[:4]):
        return "gpg"
    raise BackupError(f"{path.name} is not an age- or gpg-encrypted pigtail backup")


def _pg_env(conninfo: str) -> dict[str, str]:
    """libpq environment for `conninfo` (keeps the password off the command line)."""
    keys = {
        "host": "PGHOST", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD",
        "dbname": "PGDATABASE", "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY", "connect_timeout": "PGCONNECT_TIMEOUT",
    }  # fmt: skip
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    for k, v in conninfo_to_dict(conninfo).items():
        if k in keys and v is not None:
            env[keys[k]] = str(v)
    return env


def _major(version_output: str) -> int:
    m = re.search(r"(\d+)(?:\.\d+)?", version_output)
    if not m:
        raise BackupError(f"cannot read the version from {version_output!r}")
    return int(m.group(1))


def check_pg_dump_version(conninfo: str) -> None:
    """`pg_dump` must be at least the server's major version (it refuses older ones anyway)."""
    out = subprocess.run(
        [_need("pg_dump"), "--version"], capture_output=True, text=True, check=True
    ).stdout
    with psycopg.connect(conninfo) as c:
        row = c.execute("SHOW server_version_num").fetchone()
    server = int(row[0]) // 10000 if row else 0
    if _major(out) < server:
        raise BackupError(f"pg_dump {_major(out)} is older than the server ({server})")


# --- paths ---------------------------------------------------------------------------------------
def _git_worktree(path: Path) -> Path | None:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def check_out_dir(out: Path) -> Path:
    """Resolve `out`; refuse any directory inside a git working tree (the repo is public)."""
    resolved = out.expanduser().resolve()
    if (tree := _git_worktree(resolved)) is not None:
        raise BackupError(
            f"refusing to write a backup inside a git working tree ({tree}); choose a "
            "directory outside any repository"
        )
    return resolved


def backup_name(at: datetime, tool: Tool) -> str:
    return f"pigtail-backup-{at.astimezone(UTC):%Y%m%dT%H%M%SZ}.{tool}"


def backup_time(name: str) -> datetime | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


# --- manifest ------------------------------------------------------------------------------------
def build_manifest(db: CaptureDB, *, now: datetime, snapshot_backend: str) -> dict[str, Any]:
    q = db.conn.execute
    versions = [str(r[0]) for r in q("SELECT version FROM schema_migrations ORDER BY 1")]
    log = q("SELECT count(*), COALESCE(max(id), 0) FROM deletion_log").fetchone()
    hashes = [
        str(r[0])
        for r in q(
            "SELECT DISTINCT content_hash FROM evidence WHERE deletion_state = 'present' ORDER BY 1"
        )
    ]
    return {
        "format": 1,
        "created_at": now.astimezone(UTC).isoformat(),
        "schema_versions": versions,
        "deletion_log": {"rows": int(log[0]) if log else 0, "max_id": int(log[1]) if log else 0},
        "snapshots": {"backend": snapshot_backend, "present_hashes": hashes},
    }


# --- create --------------------------------------------------------------------------------------
@dataclass
class CreateResult:
    path: str
    tool: str
    created_at: str
    bytes: int
    schema_versions: list[str]
    deletion_log_rows: int
    snapshot_hashes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create(
    db: CaptureDB,
    conninfo: str,
    out_dir: Path,
    *,
    recipient: str | None,
    snapshot_backend: str = "local",
    now: datetime | None = None,
) -> CreateResult:
    """Write one encrypted backup of the database at `conninfo` into `out_dir` (CB-17)."""
    if not recipient or not recipient.strip():
        raise BackupError(f"{RECIPIENT_ENV} is not set: refusing to write an unencrypted backup")
    recipient = recipient.strip()
    tool = tool_for_recipient(recipient)
    out = check_out_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    check_pg_dump_version(conninfo)
    now = now or datetime.now(UTC)
    manifest = build_manifest(db, now=now, snapshot_backend=snapshot_backend)
    final = out / backup_name(now, tool)
    if final.exists():
        raise BackupError(f"{final.name} already exists")
    partial = final.with_name(final.name + PARTIAL_SUFFIX)
    partial.touch(mode=0o600)
    os.chmod(partial, 0o600)
    body = json.dumps(manifest, sort_keys=True).encode()
    try:
        with tempfile.TemporaryFile() as enc_err, tempfile.TemporaryFile() as dump_err:
            enc = subprocess.Popen(
                _encrypt_cmd(tool, recipient, partial),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=enc_err,
                bufsize=0,
            )
            assert enc.stdin is not None
            try:
                enc.stdin.write(MAGIC + f"{len(body)}\n".encode() + body)
                dump = subprocess.Popen(
                    [_need("pg_dump"), "--format=custom", "--no-owner", "--no-privileges"],
                    stdin=subprocess.DEVNULL,
                    stdout=enc.stdin,
                    stderr=dump_err,
                    env=_pg_env(conninfo),
                )
            finally:
                enc.stdin.close()  # the encryptor sees EOF when pg_dump exits
            dump_rc = dump.wait()
            enc_rc = enc.wait()
            if dump_rc != 0:
                raise BackupError(f"pg_dump failed ({dump_rc}): {_tail(dump_err)}")
            if enc_rc != 0:
                raise BackupError(f"{tool} failed ({enc_rc}): {_tail(enc_err)}")
        os.chmod(partial, 0o600)  # gpg may recreate the file
        if detect_tool(partial) != tool:
            raise BackupError("the output is not encrypted as expected")
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return CreateResult(
        path=str(final),
        tool=tool,
        created_at=manifest["created_at"],
        bytes=final.stat().st_size,
        schema_versions=manifest["schema_versions"],
        deletion_log_rows=manifest["deletion_log"]["rows"],
        snapshot_hashes=len(manifest["snapshots"]["present_hashes"]),
    )


def _tail(f: IO[bytes], limit: int = 600) -> str:
    """The end of a tool's stderr, scrubbed of identifiers (CB-18): it can quote data."""
    f.seek(0)
    text = f.read().decode("utf-8", "replace").strip()
    return scrub(text[-limit:], limit=limit)


# --- read ----------------------------------------------------------------------------------------
def _read_exact(fd: int, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        got = os.read(fd, n - len(buf))
        if not got:
            raise BackupError("backup ended early (truncated file or wrong key)")
        buf += got
    return bytes(buf)


def _read_line(fd: int, limit: int = 32) -> bytes:
    buf = bytearray()
    while not buf.endswith(b"\n"):
        if len(buf) > limit:
            raise BackupError("not a pigtail backup (bad header)")
        got = os.read(fd, 1)
        if not got:
            raise BackupError("backup ended early (truncated file or wrong key)")
        buf += got
    return bytes(buf)


def _read_header(fd: int) -> dict[str, Any]:
    if _read_line(fd) != MAGIC:
        raise BackupError("not a pigtail backup (bad magic)")
    try:
        n = int(_read_line(fd).strip())
    except ValueError as e:
        raise BackupError("not a pigtail backup (bad manifest length)") from e
    if not 0 < n <= MAX_MANIFEST:
        raise BackupError("not a pigtail backup (bad manifest length)")
    manifest: dict[str, Any] = json.loads(_read_exact(fd, n))
    if manifest.get("format") != 1:
        raise BackupError(f"unsupported backup format {manifest.get('format')!r}")
    return manifest


def read_manifest(path: Path, *, identity: str | None = None) -> dict[str, Any]:
    """Decrypt only as far as the manifest."""
    tool = detect_tool(path)
    with tempfile.TemporaryFile() as err:
        dec = subprocess.Popen(
            _decrypt_cmd(tool, path, identity), stdout=subprocess.PIPE, stderr=err, bufsize=0
        )
        assert dec.stdout is not None
        try:
            return _read_header(dec.stdout.fileno())
        finally:
            dec.stdout.close()
            dec.kill()
            dec.wait()


# --- carry-over ----------------------------------------------------------------------------------
@dataclass
class CarryOver:
    """Rows of the live database that must survive a restore (read before it is replaced)."""

    deletion_log: list[dict[str, Any]] = field(default_factory=list)
    suppression: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    source: str = "none"


def _rows(conn: psycopg.Connection[Any], query: str, params: Any = None) -> list[dict[str, Any]]:
    cur = conn.execute(query, params)
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def read_carry_over(conninfo: str) -> CarryOver:
    """Tombstones, refusal list and request log of the live database (empty if it has none)."""
    try:
        conn = psycopg.connect(conninfo, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError:
        return CarryOver(source="unreachable")
    with conn:
        row = conn.execute("SELECT to_regclass('public.deletion_log') IS NOT NULL").fetchone()
        if not row or not row[0]:
            return CarryOver(source="empty")
        co = CarryOver(source="live")
        co.deletion_log = _rows(
            conn,
            "SELECT logged_at, reason, action, target, content_hash, evidence_id,"
            " rows_affected, run_id, request_id FROM deletion_log ORDER BY id",
        )
        co.suppression = _rows(
            conn,
            "SELECT kind, value, platform, reason, request_id, added_at FROM privacy_suppression",
        )
        co.requests = _rows(
            conn,
            "SELECT id, type, platform, received_at, completed_at, outcome, counts, run_id"
            " FROM privacy_requests",
        )
        run_ids = {r["run_id"] for r in co.deletion_log + co.requests if r["run_id"]}
        co.runs = _rows(
            conn,
            "SELECT id, schema_version, job, started_at, finished_at, status, code_commit,"
            " config, counts, prompt_versions, model_versions, error FROM runs"
            " WHERE id = ANY(%s)",
            (sorted(run_ids),),
        )
    return co


def _json(v: Any) -> Any:
    return Jsonb(v) if isinstance(v, dict | list) else v


def write_carry_over(db: CaptureDB, co: CarryOver) -> dict[str, int]:
    """Write carried-over rows missing from the restored database. Tombstones are matched on
    their content (ids differ between databases); opt-outs are added, never removed."""
    q = db.conn.execute
    counts = {"runs": 0, "requests": 0, "suppression": 0, "tombstones": 0}
    for r in co.runs:
        cols = list(r)
        counts["runs"] += q(
            "INSERT INTO runs ({}) VALUES ({}) ON CONFLICT (id) DO NOTHING".format(
                ", ".join(cols), ", ".join(["%s"] * len(cols))
            ),
            [_json(r[c]) for c in cols],
        ).rowcount
    for r in co.requests:
        counts["requests"] += q(
            "INSERT INTO privacy_requests (id, type, platform, received_at, completed_at,"
            " outcome, counts, run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET completed_at = EXCLUDED.completed_at,"
            " outcome = EXCLUDED.outcome, counts = EXCLUDED.counts"
            " WHERE privacy_requests.outcome = 'pending'",
            (
                r["id"], r["type"], r["platform"], r["received_at"], r["completed_at"],
                r["outcome"], Jsonb(r["counts"]), r["run_id"],
            ),
        ).rowcount  # fmt: skip
    for r in co.suppression:
        if r["kind"] == "repo_name_unkeyed":  # legacy: the database refuses new ones (CB-13b)
            continue
        counts["suppression"] += q(
            "INSERT INTO privacy_suppression (kind, value, platform, reason, request_id,"
            " added_at) VALUES (%s, %s, %s, %s,"
            " (SELECT id FROM privacy_requests WHERE id = %s), %s)"
            " ON CONFLICT (kind, value) DO NOTHING",
            (r["kind"], r["value"], r["platform"], r["reason"], r["request_id"], r["added_at"]),
        ).rowcount
    for r in co.deletion_log:
        counts["tombstones"] += q(
            """
            INSERT INTO deletion_log (logged_at, reason, action, target, content_hash,
                evidence_id, rows_affected, run_id, request_id)
            SELECT %(logged_at)s, %(reason)s, %(action)s, %(target)s, %(content_hash)s,
                %(evidence_id)s, %(rows_affected)s,
                (SELECT id FROM runs WHERE id = %(run_id)s),
                (SELECT id FROM privacy_requests WHERE id = %(request_id)s)
            WHERE NOT EXISTS (
                SELECT 1 FROM deletion_log d WHERE d.logged_at = %(logged_at)s
                  AND d.reason = %(reason)s AND d.action = %(action)s
                  AND d.target = %(target)s
                  AND d.content_hash IS NOT DISTINCT FROM %(content_hash)s
                  AND d.evidence_id IS NOT DISTINCT FROM %(evidence_id)s
                  AND d.rows_affected = %(rows_affected)s)
            """,
            r,
        ).rowcount
    return counts


# --- tombstone replay ----------------------------------------------------------------------------
@dataclass
class ReplayReport:
    tombstones: int = 0
    hashes_dropped: int = 0
    hashes_kept_newer_capture: int = 0
    evidence_raw_dropped: int = 0
    evidence_deleted_upstream: int = 0
    evidence_deleted: int = 0
    upstream_items_rechecked: int = 0
    llm_cache_rows_deleted: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def replay_tombstones(
    db: CaptureDB, store: SnapshotStore, *, llm_store: LLMStore | None = None
) -> ReplayReport:
    """Re-apply every snapshot- and evidence-level tombstone in `deletion_log` (CB-17).

    Idempotent. Content captured again *after* a tombstone (same bytes, newer present evidence)
    is left alone: the tombstone deleted an older capture, not the content forever."""
    rep = ReplayReport()
    q = db.conn.execute
    rows = q(
        "SELECT logged_at, reason, action, content_hash, evidence_id FROM deletion_log"
        " WHERE (action = 'raw_dropped' AND content_hash IS NOT NULL)"
        " OR (action = 'evidence_deleted' AND evidence_id IS NOT NULL) ORDER BY id"
    ).fetchall()
    rep.tombstones = len(rows)
    dropped: set[str] = set()
    deleted_ids: list[str] = []
    for logged_at, reason, action, h, eid in rows:
        if action == "evidence_deleted":
            q("UPDATE gharchive_hours SET evidence_id = NULL WHERE evidence_id = %s", (eid,))
            n = q("DELETE FROM evidence WHERE id = %s", (eid,)).rowcount
            rep.evidence_deleted += n
            if n:
                deleted_ids.append(eid)
            continue
        if reason == "deleted_upstream":
            ids = [
                r[0]
                for r in q(
                    "UPDATE evidence SET deletion_state = 'deleted_upstream'"
                    " WHERE content_hash = %s AND fetched_at <= %s"
                    " AND deletion_state <> 'deleted_upstream' RETURNING id",
                    (h, logged_at),
                ).fetchall()
            ]
            rep.evidence_deleted_upstream += len(ids)
            if ids:
                deleted_ids += ids
                rep.upstream_items_rechecked += q(
                    "UPDATE upstream_items u SET next_check_at = now() FROM"
                    " evidence_upstream_items l WHERE l.platform = u.platform"
                    " AND l.item_id = u.item_id AND l.evidence_id = ANY(%s)"
                    " AND u.state = 'present'",
                    (ids,),
                ).rowcount
        else:
            rep.evidence_raw_dropped += q(
                "UPDATE evidence SET deletion_state = 'raw_dropped' WHERE content_hash = %s"
                " AND fetched_at <= %s AND deletion_state = 'present'",
                (h, logged_at),
            ).rowcount
        if h in dropped:
            continue
        newer = q(
            "SELECT 1 FROM evidence WHERE content_hash = %s AND deletion_state = 'present' LIMIT 1",
            (h,),
        ).fetchone()
        if newer:
            rep.hashes_kept_newer_capture += 1
            continue
        dropped.add(h)
        if store.delete(h):
            rep.hashes_dropped += 1
    if llm_store is not None and deleted_ids:
        rep.llm_cache_rows_deleted = llm_store.purge_for_evidence(deleted_ids)
    return rep


# --- restore -------------------------------------------------------------------------------------
@dataclass
class RestoreResult:
    manifest: dict[str, Any]
    carry_over_source: str
    carried: dict[str, int]
    migrations_applied: list[str]
    replay: dict[str, int]
    refusals: dict[str, int]
    retention: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        snaps = dict(d["manifest"].get("snapshots", {}))
        snaps["present_hashes"] = len(snaps.get("present_hashes", []))
        d["manifest"] = {**d["manifest"], "snapshots": snaps}
        return d


def restore_database(path: Path, conninfo: str, *, identity: str | None = None) -> dict[str, Any]:
    """Replace schema `public` of `conninfo` with the backup's dump, in one transaction.

    Returns the manifest. Nothing changes unless decryption and the whole restore succeed."""
    tool = detect_tool(path)
    pg_restore, psql = _need("pg_restore"), _need("psql")
    env = _pg_env(conninfo)
    with (
        tempfile.TemporaryFile() as dec_err,
        tempfile.TemporaryFile() as res_err,
        tempfile.TemporaryFile() as sql_err,
    ):
        dec = subprocess.Popen(
            _decrypt_cmd(tool, path, identity), stdout=subprocess.PIPE, stderr=dec_err, bufsize=0
        )
        assert dec.stdout is not None
        try:
            manifest = _read_header(dec.stdout.fileno())
        except BaseException:
            dec.stdout.close()
            dec.kill()
            dec.wait()
            if dec.returncode not in (0, None, -9):
                raise BackupError(f"{tool} failed: {_tail(dec_err)}") from None
            raise
        res = subprocess.Popen(
            [pg_restore, "--no-owner", "--no-privileges", "--file=-"],
            stdin=dec.stdout,
            stdout=subprocess.PIPE,
            stderr=res_err,
            env=env,
        )
        dec.stdout.close()
        assert res.stdout is not None
        sql = subprocess.Popen(
            [psql, "--no-psqlrc", "--quiet", "--set=ON_ERROR_STOP=1", "--file=-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=sql_err,
            env=env,
        )
        assert sql.stdin is not None
        committed = False
        try:
            sql.stdin.write(
                b"BEGIN;\nDROP SCHEMA IF EXISTS public CASCADE;\nCREATE SCHEMA public;\n"
            )
            while chunk := res.stdout.read(CHUNK):
                sql.stdin.write(chunk)
            res_rc, dec_rc = res.wait(), dec.wait()
            if res_rc == 0 and dec_rc == 0:
                sql.stdin.write(b"COMMIT;\n")
                committed = True
        except BrokenPipeError:
            pass
        finally:
            try:
                sql.stdin.close()
            except BrokenPipeError:
                committed = False
            res.stdout.close()
            for p in (res, dec):
                if p.poll() is None:
                    p.kill()
                    p.wait()
            sql_rc = sql.wait()
        if dec.returncode != 0:
            raise BackupError(f"{tool} failed ({dec.returncode}): {_tail(dec_err)}")
        if res.returncode != 0:
            raise BackupError(f"pg_restore failed ({res.returncode}): {_tail(res_err)}")
        if not committed or sql_rc != 0:
            raise BackupError(f"restore rolled back (psql {sql_rc}): {_tail(sql_err)}")
    return manifest


def restore(
    path: Path,
    conninfo: str,
    store: SnapshotStore,
    *,
    reapply: Callable[[CaptureDB], dict[str, int]],
    retention: Callable[[CaptureDB], Mapping[str, Any]],
    identity: str | None = None,
    llm_store: LLMStore | None = None,
    migrations_dir: Path | None = None,
) -> RestoreResult:
    """Restore `path` into `conninfo` and re-apply every deletion (module docstring, steps 1-6).

    `reapply` re-applies the refusal list and `retention` runs the retention purge on the
    restored database (the CLI binds the pseudonym key, run record and settings)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.db.migrate import migrate

    if not path.is_file():
        raise BackupError(f"no such backup file: {path}")
    detect_tool(path)
    co = read_carry_over(conninfo)
    manifest = restore_database(path, conninfo, identity=identity)
    applied = migrate(conninfo, migrations_dir)
    db = CaptureDB.connect(conninfo)
    try:
        carried = write_carry_over(db, co)
        replay = replay_tombstones(db, store, llm_store=llm_store)
        refusals = reapply(db)
        ret = dict(retention(db))
    finally:
        db.close()
    return RestoreResult(
        manifest=manifest,
        carry_over_source=co.source,
        carried=carried,
        migrations_applied=applied,
        replay=replay.to_dict(),
        refusals=refusals,
        retention=ret,
    )


# --- prune ---------------------------------------------------------------------------------------
@dataclass
class PruneResult:
    kept: list[str]
    deleted: list[str]
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prune(
    directory: Path,
    *,
    days: int = BACKUP_RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> PruneResult:
    """Delete backups older than `days` (at most 35, retention-policy §2) and partial files
    older than a day. Files that are not pigtail backups are never touched."""
    if not 1 <= days <= BACKUP_RETENTION_DAYS:
        raise BackupError(f"--days must be between 1 and {BACKUP_RETENTION_DAYS} (CB-17)")
    if not directory.is_dir():
        raise BackupError(f"no such directory: {directory}")
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    kept: list[str] = []
    deleted: list[str] = []
    for p in sorted(directory.iterdir()):
        name = p.name
        partial = name.endswith(PARTIAL_SUFFIX)
        at = backup_time(name.removesuffix(PARTIAL_SUFFIX))
        if at is None or not p.is_file():
            continue
        limit = now - timedelta(days=1) if partial else cutoff
        if at < limit:
            deleted.append(name)
            if not dry_run:
                p.unlink()
        else:
            kept.append(name)
    return PruneResult(kept=kept, deleted=deleted, dry_run=dry_run)
