"""JSONL export alongside the database (M1-T20; PRD §7 "a mergeable, diffable format").

`pigtail export jsonl --out DIR [--tables T ...] [--include-person-level]` writes one
`<table>.jsonl` per entity plus `manifest.json`:

- **One JSON object per line**, keys sorted, UTF-8, `\\n` line ends. Every record carries
  `schema_version`: the row's own column where the table has one (`v0` records: repos, cases,
  evidence, runs), else `db-<latest migration>` (the table layout it was read with).
- **Deterministic:** rows ordered by the table's primary key; timestamps in UTC ISO 8601; the
  same database state gives byte-identical files (`manifest.json` carries the export time, the
  per-file SHA-256 and row counts). All tables are read in one `REPEATABLE READ, READ ONLY`
  transaction, so the files are a consistent snapshot.
- **Classification (fail closed):** every table must be listed in `TABLE_LEVELS`. `project`
  tables are exported by default; `person` tables (pseudonymous person-level rows, the refusal
  list) only with `--include-person-level` **and** an output directory outside any git working
  tree; `never` tables are never exported: `repo_event_actor` (per-repo star/fork actors, read
  only in aggregate, CB-23), UI sessions and the UI audit log. A table in the database
  that is not classified stops the export (a new migration must classify its tables).
- **Never into the source tree:** an output directory inside the pigtail repository (public) is
  refused, whatever the flags. Files are written with mode 0600 in a 0700 directory, atomically
  (temp file + rename).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.range import Range

Level = Literal["project", "person", "never"]
EXPORT_FORMAT = "pigtail-jsonl/1"
REPO_ROOT = Path(__file__).resolve().parents[3]

TABLE_LEVELS: dict[str, Level] = {
    # project-level: counts, ids, repo names, evidence metadata, run records
    "cases": "project",
    "deletion_log": "project",
    "evidence": "project",
    "github_budget_ledger": "project",
    "github_http_cache": "project",
    "hn_rank_observation": "project",
    "hn_rank_poll": "project",
    "hn_story": "project",
    "launch_mode_window": "project",
    "privacy_requests": "project",  # no handle, no pseudonym (CB-08)
    "repo_event_daily_agg": "project",
    "repo_event_poll": "project",
    "repo_star_daily": "project",
    "repos": "project",
    "runs": "project",
    "schema_migrations": "project",
    "star_history_fetch": "project",
    # person-level: pseudonyms (PERSON_TABLES), upstream item links, the refusal list
    "hn_mention": "person",
    "upstream_items": "person",
    "evidence_upstream_items": "person",
    "privacy_suppression": "person",
    # never: per-repo star/fork actors (read only in aggregate, CB-23; a dump would list a repo's
    # stargazers), operator session hashes and the UI audit trail
    "repo_event_actor": "never",
    "ui_sessions": "never",
    "ui_audit_log": "never",
    # the pseudonym-key fingerprint and its history (CB-25): key-derived, useless outside
    "pseudonym_key_fingerprint": "never",
    "pseudonym_key_fingerprint_log": "never",
}


class ExportError(ValueError):
    pass


def _git_worktree(path: Path) -> Path | None:
    """The git working tree containing `path` (any `.git` entry above it), else None."""
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def check_out_dir(out: Path, *, include_person_level: bool, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve `out` and refuse unsafe destinations (see module docstring)."""
    resolved = out.expanduser().resolve()
    root = repo_root.resolve()
    if (root / "pyproject.toml").exists() and resolved.is_relative_to(root):
        raise ExportError(
            f"refusing to export into the pigtail source tree ({root}): the repository is public"
        )
    if include_person_level and (tree := _git_worktree(resolved)) is not None:
        raise ExportError(
            f"refusing person-level export inside a git working tree ({tree}); choose a "
            "directory outside any repository"
        )
    return resolved


def jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return (v.astimezone(UTC) if v.tzinfo else v).isoformat()
    if isinstance(v, date | time):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return bytes(v).hex()
    if isinstance(v, Range):
        if v.isempty:
            return "empty"
        lo = "" if v.lower is None else jsonable(v.lower)
        hi = "" if v.upper is None else jsonable(v.upper)
        return f"{v.bounds[0]}{lo},{hi}{v.bounds[1]}"
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return v


def dumps_row(row: Mapping[str, Any], schema_version: str) -> str:
    rec = {k: jsonable(v) for k, v in row.items()}
    rec.setdefault("schema_version", schema_version)
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class ExportResult:
    out_dir: Path
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    db_schema_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "skipped": self.skipped,
            "db_schema_version": self.db_schema_version,
        }


def _db_tables(conn: psycopg.Connection[Any]) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
        )
    ]


def _pk_columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT a.attname FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        ORDER BY array_position(i.indkey::int2[], a.attnum)
        """,
        (f"public.{table}",),
    ).fetchall()
    return [str(r[0]) for r in rows]


def select_tables(
    present: Sequence[str], requested: Iterable[str] | None, include_person_level: bool
) -> tuple[list[str], dict[str, str]]:
    """(tables to export, {table: reason skipped}). Raises on unknown/unclassified tables."""
    unclassified = sorted(t for t in present if t not in TABLE_LEVELS)
    if unclassified:
        raise ExportError(
            f"unclassified table(s) {unclassified}: add them to TABLE_LEVELS "
            "(pigtail.export.jsonl) before exporting"
        )
    wanted = list(dict.fromkeys(requested)) if requested else list(present)
    unknown = sorted(t for t in wanted if t not in present)
    if unknown:
        raise ExportError(f"unknown table(s) {unknown}")
    out: list[str] = []
    skipped: dict[str, str] = {}
    for t in sorted(wanted):
        level = TABLE_LEVELS[t]
        if level == "never":
            skipped[t] = "never_exported"
        elif level == "person" and not include_person_level:
            skipped[t] = "person_level"
        else:
            out.append(t)
    return out, skipped


def export_jsonl(
    conninfo: str,
    out: Path,
    *,
    tables: Iterable[str] | None = None,
    include_person_level: bool = False,
    repo_root: Path = REPO_ROOT,
    code_commit: str | None = None,
    now: datetime | None = None,
) -> ExportResult:
    out_dir = check_out_dir(out, include_person_level=include_person_level, repo_root=repo_root)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    res = ExportResult(out_dir)
    with psycopg.connect(conninfo) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        conn.commit()
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            present = _db_tables(conn)
            chosen, res.skipped = select_tables(present, tables, include_person_level)
            row = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
            res.db_schema_version = str(row[0]) if row and row[0] else None
            default_version = f"db-{res.db_schema_version or 'unknown'}"
            for t in chosen:
                res.tables[t] = _export_table(conn, t, out_dir, default_version)
    manifest = {
        "format": EXPORT_FORMAT,
        "db_schema_version": res.db_schema_version,
        "code_commit": code_commit,
        "exported_at": (now or datetime.now(UTC)).isoformat(),
        "include_person_level": include_person_level,
        "tables": res.tables,
        "skipped": res.skipped,
    }
    _write_atomic(out_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return res


def _export_table(
    conn: psycopg.Connection[Any], table: str, out_dir: Path, default_version: str
) -> dict[str, Any]:
    pk = _pk_columns(conn, table)
    if not pk:
        raise ExportError(f"table {table} has no primary key: no deterministic order")
    query = sql.SQL("SELECT * FROM {} ORDER BY {}").format(
        sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(c) for c in pk)
    )
    path = out_dir / f"{table}.jsonl"
    tmp = out_dir / f".{table}.jsonl.tmp"
    h = hashlib.sha256()
    n = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with (
        os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f,
        conn.cursor(name=f"export_{table}") as cur,
    ):
        cur.itersize = 2000
        cur.execute(query)
        cols = [d.name for d in cur.description or ()]
        for values in cur:
            line = dumps_row(dict(zip(cols, values, strict=True)), default_version) + "\n"
            f.write(line)
            h.update(line.encode("utf-8"))
            n += 1
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return {"rows": n, "sha256": h.hexdigest(), "level": TABLE_LEVELS[table], "order_by": pk}


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    tmp.replace(path)
