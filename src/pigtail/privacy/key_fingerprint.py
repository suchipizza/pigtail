"""Pseudonym-key change detection (DPIA CB-25, ADR-043).

Refusal-list entries and stored pseudonyms are keyed hashes of `PSEUDONYM_KEY`. Under a different
key they silently stop matching: people and repos that opted out would be collected again, and
erasure would miss their earlier rows. So the database remembers which key it was built with.

- The fingerprint is `Pseudonymizer.fingerprint()`: `kfp1_` + 32 hex of
  HMAC-SHA256(key, "pigtail-key-fingerprint-v1"). It does not reveal the key and cannot compute
  pseudonyms. The key itself is never stored, printed or logged.
- It is stored in the one-row table `pseudonym_key_fingerprint` (migration 0012) **on first use**:
  the first command that pseudonymizes or matches opt-outs with a key records it
  (`verify(..., record=True)`, event `recorded` in `pseudonym_key_fingerprint_log`).
- Every later use compares it and **refuses** on a mismatch (`KeyFingerprintMismatch`, fail
  closed). Checked by: `suppression.load()` (every collector and privacy path that loads the
  refusal list), the connector base (`Connector.__init__` compares its pseudonymizer with the
  fingerprint the refusal list was loaded under), the privacy commands (`_Ctx`), the
  data-subject request functions (`requests.access`, `erasure`, `optout_repo*`,
  `reapply_refusals`, `rekey_unkeyed_names`), backup restore (before anything is replaced) and
  `pigtail scheduler run` at startup.
- `pigtail doctor` reports `pseudonym_key_fingerprint`: ok / fail (mismatch) / warn (unset).
- After a documented compromise rotation (runbook `key-rotation.md` §4) the operator re-records
  it with `pigtail privacy key-fingerprint --reset --confirm-rotation` (event `reset`, with a
  `runs` record `privacy.key_fingerprint_reset`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg

from pigtail.pseudonymize import Pseudonymizer

log = logging.getLogger("pigtail.privacy.key_fingerprint")

Status = Literal["ok", "mismatch", "unset"]
RESET_COMMAND = "pigtail privacy key-fingerprint --reset --confirm-rotation"
RUNBOOK = "docs/compliance/runbooks/key-rotation.md"


class KeyFingerprintMismatch(RuntimeError):
    """The running `PSEUDONYM_KEY` is not the key this database's pseudonyms were made with."""

    def __init__(self) -> None:
        super().__init__(
            "PSEUDONYM_KEY does not match the key this database was built with (CB-25): "
            "opt-outs and stored pseudonyms would silently stop matching, so pigtail refuses to "
            "run. Restore the original key. If the key was rotated on purpose after a "
            f"compromise, follow {RUNBOOK} §4 and then run `{RESET_COMMAND}`."
        )


@dataclass(frozen=True)
class Stored:
    fingerprint: str
    set_at: datetime
    set_by: str


def stored(conn: psycopg.Connection[Any]) -> Stored | None:
    row = conn.execute(
        "SELECT fingerprint, set_at, set_by FROM pseudonym_key_fingerprint"
    ).fetchone()
    return Stored(str(row[0]), row[1], str(row[2])) if row else None


def status(conn: psycopg.Connection[Any], pz: Pseudonymizer) -> Status:
    """Compare only; never writes."""
    s = stored(conn)
    if s is None:
        return "unset"
    return "ok" if s.fingerprint == pz.fingerprint() else "mismatch"


def verify(
    conn: psycopg.Connection[Any],
    pz: Pseudonymizer,
    *,
    record: bool = True,
    run_id: str | None = None,
) -> Status:
    """Raise `KeyFingerprintMismatch` unless `pz` holds the recorded key. With `record`, the
    first use stores the fingerprint and returns "ok"; on a read-only connection (or
    `record=False`) an unset fingerprint returns "unset"."""
    fp = pz.fingerprint()
    s = stored(conn)
    if s is None and record:
        try:
            with conn.transaction():  # savepoint inside a caller's transaction
                n = conn.execute(
                    "INSERT INTO pseudonym_key_fingerprint (fingerprint, set_by, run_id)"
                    " VALUES (%s, 'first_use', %s) ON CONFLICT (singleton) DO NOTHING",
                    (fp, _run_ref(conn, run_id)),
                ).rowcount
                if n:
                    conn.execute(
                        "INSERT INTO pseudonym_key_fingerprint_log (event, new_fingerprint,"
                        " run_id) VALUES ('recorded', %s, %s)",
                        (fp, _run_ref(conn, run_id)),
                    )
                    log.info("CB-25: pseudonym key fingerprint recorded on first use")
        except psycopg.errors.ReadOnlySqlTransaction:
            return "unset"
        s = stored(conn)  # a concurrent first use may have won the insert
    if s is None:
        return "unset"
    if s.fingerprint != fp:
        raise KeyFingerprintMismatch()
    return "ok"


def reset(
    conn: psycopg.Connection[Any], pz: Pseudonymizer, *, run_id: str | None = None
) -> tuple[str | None, str]:
    """Record `pz`'s key as the database's key (compromise rotation). Returns (old, new)."""
    fp = pz.fingerprint()
    with conn.transaction():
        old = stored(conn)
        conn.execute(
            "INSERT INTO pseudonym_key_fingerprint (fingerprint, set_at, set_by, run_id)"
            " VALUES (%s, now(), 'reset', %s) ON CONFLICT (singleton) DO UPDATE SET"
            " fingerprint = EXCLUDED.fingerprint, set_at = EXCLUDED.set_at,"
            " set_by = EXCLUDED.set_by, run_id = EXCLUDED.run_id",
            (fp, _run_ref(conn, run_id)),
        )
        conn.execute(
            "INSERT INTO pseudonym_key_fingerprint_log (event, old_fingerprint, new_fingerprint,"
            " run_id) VALUES ('reset', %s, %s, %s)",
            (old.fingerprint if old else None, fp, _run_ref(conn, run_id)),
        )
    log.warning("CB-25: pseudonym key fingerprint reset (compromise rotation)")
    return (old.fingerprint if old else None, fp)


def _run_ref(conn: psycopg.Connection[Any], run_id: str | None) -> str | None:
    """`run_id` if that run record exists (FK), else None."""
    if run_id is None:
        return None
    row = conn.execute("SELECT 1 FROM runs WHERE id = %s", (run_id,)).fetchone()
    return run_id if row else None


def history(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT logged_at, event, old_fingerprint, new_fingerprint, run_id"
        " FROM pseudonym_key_fingerprint_log ORDER BY id"
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
