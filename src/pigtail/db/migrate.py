"""Tiny forward-only migration runner (M1-T1, PRD §7).

Applies `migrations/NNNN_name.sql` in order, each in its own transaction, and records the version
and a SHA-256 of the file in `schema_migrations`. Running it twice is a no-op. Editing a file that
has already been applied is an error (forward-only: add a new migration instead). A Postgres
advisory lock serialises concurrent runners.

Usage: `pigtail db migrate` or `python -m pigtail.db.migrate [DATABASE_URL]`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

log = logging.getLogger("pigtail.db.migrate")
_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_ID = 0x9197A11  # arbitrary, constant advisory-lock key for pigtail migrations

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    filename   text NOT NULL,
    sha256     text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def default_migrations_dir() -> Path:
    """`PIGTAIL_MIGRATIONS_DIR`, else `migrations/` at the repo root (src layout)."""
    env = os.environ.get("PIGTAIL_MIGRATIONS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "migrations"


def discover(directory: Path) -> list[Migration]:
    out: list[Migration] = []
    for p in sorted(directory.iterdir()):
        if p.suffix != ".sql":
            continue
        m = _FILE.match(p.name)
        if not m:
            raise MigrationError(f"bad migration filename {p.name!r}; expected NNNN_name.sql")
        out.append(Migration(m.group(1), p))
    versions = [m.version for m in out]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration versions in {directory}")
    return out


def _log_notice(diag: psycopg.errors.Diagnostic) -> None:
    """Migration `RAISE WARNING`s (e.g. CB-13b in 0009) reach the operator's log."""
    if diag.severity_nonlocalized in ("WARNING", "ERROR"):
        log.warning("migration: %s", diag.message_primary)


def migrate(conninfo: str, directory: Path | None = None) -> list[str]:
    """Apply pending migrations; return the versions applied by this call."""
    migrations = discover(directory or default_migrations_dir())
    applied_now: list[str] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.add_notice_handler(_log_notice)
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute(_BOOTSTRAP)
            done = {
                v: sha for v, sha in conn.execute("SELECT version, sha256 FROM schema_migrations")
            }
            for mig in migrations:
                if mig.version in done:
                    if done[mig.version] != mig.sha256:
                        raise MigrationError(
                            f"migration {mig.path.name} changed after it was applied "
                            "(forward-only: add a new migration instead)"
                        )
                    continue
                with conn.transaction():
                    conn.execute(mig.sql.encode())
                    conn.execute(
                        "INSERT INTO schema_migrations (version, filename, sha256) "
                        "VALUES (%s, %s, %s)",
                        (mig.version, mig.path.name, mig.sha256),
                    )
                applied_now.append(mig.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied_now


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    url = args[0] if args else os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    applied = migrate(url)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
