"""Rotate `PSEUDONYM_KEY` by re-deriving every stored pseudonym (DPIA CB-26, ADR-043).

`pigtail privacy rekey --old-key-env OLD_PSEUDONYM_KEY` (new key in `PSEUDONYM_KEY`).

**The problem.** A pseudonym is a one-way keyed hash of a handle (`p_` + HMAC-SHA256, and `rk_`
for repo-name opt-outs). pigtail stores no handles (ADR-030.4), so a stored pseudonym cannot be
recomputed under a new key from the database alone: the handle is needed. Every mapping
old -> new therefore goes through a handle (or repo name) that pigtail sees *transiently*, from
one of three sources, and the handle itself is never stored, logged or printed:

1. **The operator's handles file** (`--handles-file`, the runbook's "old-key mapping"): the
   handles and repo names from the original opt-out / erasure requests, one per line
   (`github <handle>`, `hn <handle>`, `bluesky <handle>`, `v2ex <handle>`, `repo <owner/name>`;
   `#` comments). It must be outside any git working tree and readable by its owner only
   (mode 0600). It only *maps* entries: nothing in it is added to the refusal list.
2. **Names pigtail already holds** (HN mentions, story links, the Show HN screen, the watch list,
   `repos`) for keyed repo-name opt-outs (`rk_`), as `privacy optout rekey` does for legacy ones.
   After an opt-out purge those names are usually gone, so the handles file is the main source.
3. **Retained raw snapshots** (which contain handles): each connector re-parses them with a
   pseudonymizer that computes the old *and* the new pseudonym of every handle it meets and
   keeps the pair only for pseudonyms that are stored. This is how person-level rows
   (`PERSON_TABLES`) are mapped. The scan stops as soon as everything needed is mapped
   (`--no-snapshot-scan` skips it).

**What is re-derived** (all in `PERSON_TABLES`, the refusal list, or nothing):

- refusal list, kind `pseudonym`: mapped (sources 1, 3); **refuses** if any is left unmapped;
- refusal list, kind `repo_name` (`rk_`): mapped (sources 1, 2); **refuses** if any is left;
- refusal list, kinds `repo` (`<host>:<id>`) and `repo_name_unkeyed` (`rn_`): unchanged (no key);
- `PERSON_TABLES` pseudonym columns: mapped (sources 1, 3); unmapped rows **refuse** unless
  `--drop-unmapped` (those rows are deleted; `upstream_items` only loses its author, so deletion
  sync keeps tracking the item) or `--purge-person-level` (every row, mapped or not);
- LLM cache (SQLite): cleared (outputs may quote old pseudonyms; keys hash redacted input);
- request log, deletion log, UI audit log, runs: hold no pseudonym (ADR-030.4, ADR-034.2);
- key fingerprint (CB-25): set to the new key's, event `rekey`, as the last write.

An unmapped **opt-out** is never dropped and has no override: after the rotation it would stop
matching and collection about someone who objected would resume. The operator must supply the
handle (from the original request; the refusal shows each entry's platform, request id and
date, never a pseudonym) or keep the old key. Deleting pseudonymous person-level data is always
allowed, so unmapped *rows* can be dropped. After a successful run no pseudonym under the old key
remains in the database or the LLM cache.

**Atomicity.** Everything happens in one Postgres transaction: the refusal list, the key
fingerprint and every `PERSON_TABLES` table are locked against writes (`SHARE ROW EXCLUSIVE`,
readers continue) before anything is read, the old key is verified against the stored
fingerprint, the mapping is built, rows are rewritten or deleted (tombstones of reason
`key_rotation`), the LLM cache is cleared and the fingerprint is switched to the new key, then
the transaction commits. Any refusal or error rolls everything back; `--dry-run` always rolls
back and leaves the LLM cache alone. The command refuses to start while other sessions are
connected to the database (a collector that loaded the refusal list under the old key could
still write old pseudonyms after the commit); every command started later checks the new
fingerprint (CB-25). This is why no dual-key matching window (CB-27) is needed.

Not reached (outside the database): JSONL exports made with `--include-person-level` and
backups taken before the rotation still hold old pseudonyms. `backup restore` refuses a backup
taken before a `rekey` or `reset` (`backup.check_rotation`); delete old person-level exports.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from psycopg import sql

from pigtail.capture.snapshots import SnapshotMeta, SnapshotNotFound
from pigtail.connectors.base import Connector, ConnectorError
from pigtail.connectors.registry import CONNECTORS
from pigtail.privacy import key_fingerprint, suppression
from pigtail.privacy.deletion import PARSE_ERRORS, PERSON_TABLES, DeletionLog, PersonTable
from pigtail.pseudonymize import PLATFORM_NAMESPACES, Pseudonymizer

if TYPE_CHECKING:
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.runs import RunRecorder
    from pigtail.capture.snapshots import SnapshotStore
    from pigtail.llm.store import LLMStore

log = logging.getLogger("pigtail.privacy.rekey")

_SCAN_ERRORS: tuple[type[BaseException], ...] = (*PARSE_ERRORS, ConnectorError)
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
KEY_ENV = "PSEUDONYM_KEY"


class RekeyRefused(RuntimeError):
    """The rotation would lose an opt-out or keep unmappable data; nothing was changed."""

    def __init__(self, message: str, report: RekeyReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class HandlesFileError(ValueError):
    """The handles file is unsafe or malformed (messages cite line numbers, never content)."""


@dataclass(frozen=True)
class HandleEntry:
    kind: Literal["handle", "repo"]
    platform: str  # a PLATFORM_NAMESPACES key for handles; "repo" for repo names
    value: str = field(repr=False)  # never shown: it is a handle or a repo name


@dataclass
class RekeyReport:
    dry_run: bool
    old_fingerprint: str
    new_fingerprint: str
    counts: dict[str, int] = field(default_factory=dict)
    # unmapped opt-outs: kind, platform, request id, date added (never the stored value)
    unmapped_optouts: list[dict[str, Any]] = field(default_factory=list)
    committed: bool = False

    def incr(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "committed": self.committed,
            "old_fingerprint": self.old_fingerprint,
            "new_fingerprint": self.new_fingerprint,
            "counts": dict(sorted(self.counts.items())),
            "unmapped_optouts": self.unmapped_optouts,
        }


# --- inputs ------------------------------------------------------------------------------------
def old_key_from_env(name: str, env: Mapping[str, str] | None = None) -> Pseudonymizer:
    """The old key, read from the environment variable *named* `name` (never an argument).

    Error messages never echo `name` unless it is a valid variable name, so a key pasted by
    mistake in place of the name is not printed."""
    e = os.environ if env is None else env
    if not ENV_NAME_RE.match(name):
        raise ValueError(
            "--old-key-env takes the NAME of an environment variable holding the old key "
            "(e.g. OLD_PSEUDONYM_KEY), never the key itself"
        )
    if name == KEY_ENV:
        raise ValueError(f"--old-key-env must name a variable other than {KEY_ENV} (the new key)")
    value = e.get(name, "")
    if not value:
        raise ValueError(f"environment variable {name} is not set or empty")
    try:
        return Pseudonymizer(value)
    except ValueError:
        raise ValueError(f"the key in {name} is shorter than 16 characters") from None


def _git_worktree(path: Path) -> Path | None:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def read_handles_file(path: Path) -> list[HandleEntry]:
    """Parse the operator's private handles file (module docstring, source 1)."""
    resolved = path.expanduser().resolve()
    if (tree := _git_worktree(resolved.parent)) is not None:
        raise HandlesFileError(
            f"refusing a handles file inside a git working tree ({tree}): it holds handles of "
            "people who objected; keep it outside any repository"
        )
    try:
        st = resolved.stat()
    except OSError:
        raise HandlesFileError(f"cannot read the handles file {resolved}") from None
    if not stat.S_ISREG(st.st_mode):
        raise HandlesFileError(f"the handles file {resolved} is not a regular file")
    if st.st_mode & 0o077:
        raise HandlesFileError(
            f"the handles file {resolved} is readable by others; run `chmod 600` on it"
        )
    out: list[HandleEntry] = []
    text = resolved.read_text(encoding="utf-8")
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise HandlesFileError(
                f"handles file line {i}: expected '<platform> <handle>' or 'repo <owner/name>'"
            )
        kind, value = parts
        if kind == "repo":
            try:
                suppression.normalize_repo_name(value)
            except ValueError:
                raise HandlesFileError(f"handles file line {i}: not an owner/name") from None
            out.append(HandleEntry("repo", "repo", value))
        elif kind in PLATFORM_NAMESPACES:
            if not value.lstrip("@"):
                raise HandlesFileError(f"handles file line {i}: empty handle")
            out.append(HandleEntry("handle", kind, value))
        else:
            known = ", ".join(sorted(PLATFORM_NAMESPACES))
            raise HandlesFileError(
                f"handles file line {i}: the first field must be one of {known} or 'repo'"
            )
    return out


# --- mapping -----------------------------------------------------------------------------------
class _MappingPseudonymizer(Pseudonymizer):
    """Behaves as the OLD key's pseudonymizer (so connectors parse exactly as they did) and, for
    every pseudonym in `wanted`, records the new key's pseudonym of the same handle."""

    def __init__(
        self, old_key: str, new: Pseudonymizer, wanted: set[str], sink: dict[str, str]
    ) -> None:
        super().__init__(old_key)
        self._new = new
        self._wanted = wanted
        self._sink = sink

    def pseudonym(self, handle: str, namespace: str = "generic") -> str:
        old = super().pseudonym(handle, namespace)
        if old in self._wanted and old not in self._sink:
            self._sink[old] = self._new.pseudonym(handle, namespace)
        return old


def _scan_snapshots(
    db: CaptureDB,
    store: SnapshotStore,
    mp: _MappingPseudonymizer,
    needed: set[str],
    sink: dict[str, str],
    rep: RekeyReport,
    connectors: Mapping[str, type[Connector]],
) -> None:
    """Re-parse retained snapshots of every connector with handle fields until `needed` is
    covered by `sink` (source 3)."""
    for name, cls in sorted(connectors.items()):
        if not cls.handle_fields:
            continue
        if needed <= sink.keys():
            return
        rows = db.conn.execute(
            "SELECT content_hash, url, fetched_at, collector_version, terms_basis, content_type"
            " FROM evidence WHERE source = %s AND deletion_state = 'present'"
            " ORDER BY fetched_at DESC, id",
            (name,),
        ).fetchall()
        seen: set[str] = set()
        with httpx.Client() as http:
            conn = cls(store=store, pseudonymizer=mp, http=http, enabled=False, env={})
            for h, url, fetched_at, version, terms, ctype in rows:
                if h in seen:
                    continue
                seen.add(h)
                if needed <= sink.keys():
                    return
                try:
                    data = store.get(h)
                except SnapshotNotFound:
                    rep.incr("snapshots_missing")
                    continue
                rep.incr("snapshots_scanned")
                meta = SnapshotMeta(name, url, fetched_at, version, terms, ctype)
                try:
                    for _ in conn.records(data, meta):
                        pass
                except _SCAN_ERRORS as e:
                    rep.incr("snapshots_unparseable")
                    log.warning(
                        "%s: snapshot %s could not be parsed during rekey (%s); skipped",
                        name,
                        h[:12],
                        type(e).__name__,
                    )


def _person_pseudonyms(db: CaptureDB, tables: Sequence[PersonTable]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t in tables:
        q = sql.SQL("SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL").format(
            c=sql.Identifier(t.pseudonym_column), t=sql.Identifier(t.table)
        )
        out[t.table] = {str(r[0]) for r in db.conn.execute(q).fetchall()}
    return out


def _drop_rows(
    db: CaptureDB, t: PersonTable, pseudonyms: Iterable[str] | None, dl: DeletionLog
) -> int:
    """Apply `t.rekey_unmapped` to rows with one of `pseudonyms` (None: every row with one)."""
    col, tab = sql.Identifier(t.pseudonym_column), sql.Identifier(t.table)
    if pseudonyms is None:
        cond = sql.SQL("{} IS NOT NULL").format(col)
        params: tuple[Any, ...] = ()
    else:
        ps = sorted(set(pseudonyms))
        if not ps:
            return 0
        cond = sql.SQL("{} = ANY(%s)").format(col)
        params = (ps,)
    if t.rekey_unmapped == "null":
        q = sql.SQL("UPDATE {t} SET {c} = NULL WHERE ").format(t=tab, c=col) + cond
        n = db.conn.execute(q, params).rowcount
        if n:
            dl.write("fields_cleared", f"{t.table}.{t.pseudonym_column}", rows=n)
        return n
    n = db.conn.execute(sql.SQL("DELETE FROM {} WHERE ").format(tab) + cond, params).rowcount
    if n:
        dl.write("rows_deleted", t.table, rows=n)
    return n


def _map_rows(db: CaptureDB, t: PersonTable, mapping: Mapping[str, str]) -> int:
    if not mapping:
        return 0
    olds = sorted(mapping)
    q = sql.SQL(
        "UPDATE {t} SET {c} = m.new FROM unnest(%s::text[], %s::text[]) AS m(old, new)"
        " WHERE {t}.{c} = m.old"
    ).format(t=sql.Identifier(t.table), c=sql.Identifier(t.pseudonym_column))
    return db.conn.execute(q, (olds, [mapping[o] for o in olds])).rowcount


def _move_entry(db: CaptureDB, kind: str, old: str, new: Iterable[str]) -> None:
    """Replace a refusal-list entry by its new-key value(s), keeping platform, reason, request
    id and date."""
    for v in sorted(set(new)):
        db.conn.execute(
            "INSERT INTO privacy_suppression (kind, value, platform, reason, request_id, added_at)"
            " SELECT kind, %s, platform, reason, request_id, added_at FROM privacy_suppression"
            " WHERE kind = %s AND value = %s ON CONFLICT (kind, value) DO NOTHING",
            (v, kind, old),
        )
    db.conn.execute("DELETE FROM privacy_suppression WHERE kind = %s AND value = %s", (kind, old))


def other_sessions(db: CaptureDB) -> int:
    """Client sessions connected to this database other than ours."""
    row = db.conn.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        " AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
    ).fetchone()
    return int(row[0]) if row else 0


class _DryRun(Exception):
    pass


# --- the rotation ------------------------------------------------------------------------------
def rekey(
    db: CaptureDB,
    store: SnapshotStore | None,
    old: Pseudonymizer,
    new: Pseudonymizer,
    *,
    handles: Sequence[HandleEntry] = (),
    purge_person_level: bool = False,
    drop_unmapped: bool = False,
    scan_snapshots: bool = True,
    dry_run: bool = False,
    llm_store: LLMStore | None = None,
    run: RunRecorder | None = None,
    require_exclusive: bool = True,
    lock_timeout: str = "10s",
    person_tables: Sequence[PersonTable] = PERSON_TABLES,
    connectors: Mapping[str, type[Connector]] = CONNECTORS,
) -> RekeyReport:
    """CB-26: re-derive every stored pseudonym under `new` in one transaction (module
    docstring). Raises `RekeyRefused` (nothing changed) or `KeyFingerprintMismatch` (`old` is
    not the key the database was built with)."""
    old_fp, new_fp = old.fingerprint(), new.fingerprint()
    rep = RekeyReport(dry_run=dry_run, old_fingerprint=old_fp, new_fingerprint=new_fp)
    if old_fp == new_fp:
        raise RekeyRefused("the old and the new key are the same: nothing to rotate", rep)
    if not re.match(r"^\d{1,4}(ms|s|min)$", lock_timeout):
        raise ValueError("lock_timeout must look like 10s, 500ms or 1min")
    run_id = run.id if run is not None else None
    try:
        with db.conn.transaction():
            _rekey_tx(
                db, store, old, new, rep,
                handles=handles, purge_person_level=purge_person_level,
                drop_unmapped=drop_unmapped, scan_snapshots=scan_snapshots,
                llm_store=llm_store, run_id=run_id, require_exclusive=require_exclusive,
                lock_timeout=lock_timeout, person_tables=person_tables, connectors=connectors,
            )  # fmt: skip
            if dry_run:
                raise _DryRun()
            rep.committed = True
    except _DryRun:
        pass
    if run is not None:
        for k, v in rep.counts.items():
            run.incr(k, v)
    return rep


def _rekey_tx(
    db: CaptureDB,
    store: SnapshotStore | None,
    old: Pseudonymizer,
    new: Pseudonymizer,
    rep: RekeyReport,
    *,
    handles: Sequence[HandleEntry],
    purge_person_level: bool,
    drop_unmapped: bool,
    scan_snapshots: bool,
    llm_store: LLMStore | None,
    run_id: str | None,
    require_exclusive: bool,
    lock_timeout: str,
    person_tables: Sequence[PersonTable],
    connectors: Mapping[str, type[Connector]],
) -> None:
    q = db.conn.execute
    q(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(lock_timeout)))
    if require_exclusive and (n := other_sessions(db)):
        raise RekeyRefused(
            f"{n} other session(s) are connected to the database. Stop every writer (scheduler, "
            "app, ui, cron jobs) first: a collector that loaded the refusal list under the old "
            "key could write old pseudonyms after the rotation (runbook key-rotation.md §4.1)",
            rep,
        )
    tables = ["pseudonym_key_fingerprint", "privacy_suppression", *(t.table for t in person_tables)]
    q(
        sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in dict.fromkeys(tables))
        )
    )
    stored = key_fingerprint.stored(db.conn)
    if stored is not None and stored.fingerprint == rep.new_fingerprint:
        raise RekeyRefused(
            "the database is already keyed with the new key (PSEUDONYM_KEY): nothing to rotate",
            rep,
        )
    key_fingerprint.verify(db.conn, old, record=True, run_id=run_id)  # `old` must be the DB key

    # --- what is stored under the old key
    optout_rows = q(
        "SELECT kind, value, platform, request_id, added_at FROM privacy_suppression"
        " WHERE kind IN ('pseudonym', 'repo_name') ORDER BY added_at, kind, value"
    ).fetchall()
    optout_ps = {str(r[1]) for r in optout_rows if r[0] == "pseudonym"}
    name_entries = [(str(r[1]), str(r[2])) for r in optout_rows if r[0] == "repo_name"]
    person = _person_pseudonyms(db, person_tables)
    row_ps = set().union(*person.values()) if person else set()

    # --- mapping: 1. handles file
    pmap: dict[str, str] = {}
    nmap: dict[str, set[str]] = {}
    hosts = {h for _, h in name_entries} or {"github"}
    name_values = {v for v, _ in name_entries}
    for e in handles:
        if e.kind == "handle":
            ns = PLATFORM_NAMESPACES[e.platform]
            po = old.pseudonym(e.value, ns)
            if po in optout_ps or po in row_ps:
                pmap.setdefault(po, new.pseudonym(e.value, ns))
                rep.incr("mapped_from_handles_file")
        else:
            for host in hosts:
                ko = suppression.repo_name_key(e.value, old, host)
                if ko in name_values:
                    nmap.setdefault(ko, set()).add(suppression.repo_name_key(e.value, new, host))
                    rep.incr("mapped_from_handles_file")
    # 2. names held locally
    from pigtail.privacy.requests import names_for_key

    for value, host in name_entries:
        if value in nmap:
            continue

        def keyer(n: str, h: str = host) -> str:
            return suppression.repo_name_key(n, old, h)

        names = names_for_key(db, value, keyer, host)
        if names:
            nmap[value] = {suppression.repo_name_key(n, new, host) for n in names}
            rep.incr("mapped_from_local_names")
    # 3. retained raw snapshots
    needed = set(optout_ps) | (set() if purge_person_level else row_ps)
    if scan_snapshots and store is not None and not needed <= pmap.keys():
        before = len(pmap)
        wanted = needed - pmap.keys()
        found: dict[str, str] = {}
        # the old key goes only into the mapping pseudonymizer; it is never logged or stored
        mp = _MappingPseudonymizer(old._key.decode(), new, wanted, found)
        _scan_snapshots(db, store, mp, wanted, found, rep, connectors)
        for k, v in found.items():
            pmap.setdefault(k, v)
        rep.incr("mapped_from_snapshots", len(pmap) - before)

    # --- refusals (before any write)
    unmapped_optouts = [
        {"kind": str(r[0]), "platform": str(r[2]), "request_id": r[3], "added_at": str(r[4])}
        for r in optout_rows
        if (r[0] == "pseudonym" and r[1] not in pmap) or (r[0] == "repo_name" and r[1] not in nmap)
    ]
    rep.unmapped_optouts = unmapped_optouts
    rep.counts["optouts_pseudonym"] = len(optout_ps)
    rep.counts["optouts_repo_name"] = len(name_entries)
    rep.counts["optouts_unmapped"] = len(unmapped_optouts)
    unmapped_rows = {t: ps - pmap.keys() for t, ps in person.items()}
    n_unmapped_rows = sum(len(v) for v in unmapped_rows.values())
    rep.counts["person_pseudonyms"] = len(row_ps)
    rep.counts["person_pseudonyms_unmapped"] = 0 if purge_person_level else n_unmapped_rows
    if unmapped_optouts:
        raise RekeyRefused(
            f"{len(unmapped_optouts)} opt-out(s) cannot be mapped to the new key: they would "
            "stop matching and collection about people or repos that objected would resume. "
            "Add their handles or repo names (from the original requests; see request_id and "
            "added_at) to a --handles-file and run again. Nothing was changed.",
            rep,
        )
    if n_unmapped_rows and not (purge_person_level or drop_unmapped):
        raise RekeyRefused(
            f"{n_unmapped_rows} pseudonym(s) in person-level tables cannot be mapped (no handle "
            "in the handles file or in retained snapshots). Re-run with --drop-unmapped (delete "
            "those rows) or --purge-person-level (delete all person-level rows). Nothing was "
            "changed.",
            rep,
        )

    # --- writes
    for r in optout_rows:
        kind, value = str(r[0]), str(r[1])
        if kind == "pseudonym":
            _move_entry(db, kind, value, [pmap[value]])
            rep.incr("optouts_pseudonym_mapped")
        else:
            _move_entry(db, kind, value, nmap[value])
            rep.incr("optouts_repo_name_mapped")
    dl = DeletionLog(db, "key_rotation", run_id=key_fingerprint._run_ref(db.conn, run_id))
    for t in person_tables:
        if purge_person_level:
            n = _drop_rows(db, t, None, dl)
            mapped = 0
        else:
            n = _drop_rows(db, t, unmapped_rows[t.table], dl) if drop_unmapped else 0
            mapped = _map_rows(db, t, {p: pmap[p] for p in person[t.table] if p in pmap})
        verb = "pseudonyms_cleared" if t.rekey_unmapped == "null" else "rows_deleted"
        rep.counts[f"{t.table}_rows_mapped"] = mapped
        rep.counts[f"{t.table}_{verb}"] = n
    left = _person_pseudonyms(db, person_tables)
    stale = sum(1 for ps in left.values() for p in ps if p in row_ps and p not in pmap.values())
    if stale:  # defensive: every old pseudonym is mapped or gone at this point
        raise RekeyRefused(f"{stale} old pseudonym(s) still present after mapping; rolled back")
    if llm_store is not None:
        if rep.dry_run:
            rep.counts["llm_cache_rows_deleted"] = llm_store.cache_count()
        else:
            n = llm_store.clear()
            rep.counts["llm_cache_rows_deleted"] = n
            if n:
                dl.write("cache_purged", "llm_cache", rows=n)
    key_fingerprint.reset(db.conn, new, run_id=run_id, event="rekey")


__all__ = [
    "HandleEntry",
    "HandlesFileError",
    "RekeyRefused",
    "RekeyReport",
    "old_key_from_env",
    "other_sessions",
    "read_handles_file",
    "rekey",
]
