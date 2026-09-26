"""Per-brief pre-registration before the outcome sort (PRD R8.2; Directive §7, ADR-065;
outcome-model v2.1 §5.8; WORK_ORDER §6; migration 0023; ADR-078).

A brief's hypotheses and tests are pre-registered in `docs/preregistration/` **before** its
outcome sort. The selection stage refuses to run (`PreregistrationMissing`, `pigtail run` exit
code 7) until the brief version has a recorded pre-registration: nothing is computed or stored
and no star history is fetched.

`pigtail brief preregister <id> --file <path> [--commit <sha>]` records one
`brief_preregistration` row: brief id, version and content hash, the SHA-256 of the brief's
success definition and of its selection parameters (`hashes`), the pre-registration file's path
and SHA-256, and the git commit that holds it, if given. The gate matches the exact brief
version, content hash and selection-parameter hash, so an edited brief (a new version) or a new
selection rule (a `SELECTION_VERSION` bump) needs a new pre-registration. A pre-registration is
refused once the brief version has a stored selection (the outcome sort is the point of no
return, §5.8).

Pre-registration files are public and never contain brief content (R8.2, pre-registration README
rule 5): they commit the SHA-256 values `--print-hashes` shows instead. `record` refuses a file
that quotes any free-text value of the brief (a best-effort check, not a proof).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from pigtail.briefs.model import Brief, sha256_json
from pigtail.briefs.selection import (
    OUTCOME_MODEL_VERSION,
    SELECTION_VERSION,
    Context,
    Definition,
)

EXIT_NOT_PREREGISTERED = 7
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
MIN_QUOTE = 16  # free-text brief values at least this long must not appear in the file
ENUM_RE = re.compile(r"^[a-z0-9_.@:]+$")  # schema choices (method terms), not brief content
TEMPLATE = "docs/preregistration/TEMPLATE-brief.md"


class PreregistrationError(ValueError):
    pass


class PreregistrationMissing(PreregistrationError):
    """R8.2: the brief version has no recorded pre-registration; the outcome sort is refused."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def hashes(brief: Brief) -> dict[str, Any]:
    """The values a public pre-registration commits instead of brief content (R8.2): the
    brief version's content hash, the SHA-256 of its success definition (primary, thresholds,
    metrics, minimums, weights, fallbacks' effect as `Definition`) and of its selection
    parameters (`Context.params()`: N, exact match, SMD target and headline rule, calipers,
    matching rule, widening, fallback steps, exemplars, sensitivity alternatives, versions).
    None of these reveal the brief's text."""
    if brief.version is None:
        raise PreregistrationError("pre-register a stored brief version")
    return {
        "brief_id": brief.brief_id,
        "brief_version": brief.version,
        "brief_sha256": brief.content_hash(),
        "success_definition_sha256": sha256_json(Definition.from_brief(brief).to_dict()),
        "selection_params_sha256": sha256_json(Context.from_brief(brief).params()),
        "selection_version": SELECTION_VERSION,
        "outcome_model_version": OUTCOME_MODEL_VERSION,
    }


def _brief_strings(brief: Brief) -> set[str]:
    """Free-text values of the brief long enough to be recognisable if quoted."""
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, str):
            s = " ".join(x.split())
            if len(s) >= MIN_QUOTE and not ENUM_RE.match(s):
                out.add(s.lower())
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list | tuple):
            for v in x:
                walk(v)

    walk(brief.content())
    return out


def quotes_brief(text: str, brief: Brief) -> bool:
    """True when `text` contains a free-text value of the brief (whitespace and case ignored)."""
    flat = " ".join(text.split()).lower()
    return any(s in flat for s in _brief_strings(brief))


@dataclass(frozen=True)
class Recorded:
    id: int
    brief_id: str
    brief_version: int
    brief_hash: str
    success_sha256: str
    selection_params_sha256: str
    file_path: str
    file_sha256: str
    git_commit: str | None
    recorded_at: datetime

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["recorded_at"] = self.recorded_at.isoformat()
        return d


_COLS = (
    "id, brief_id, brief_version, brief_hash, success_sha256, selection_params_sha256,"
    " file_path, file_sha256, git_commit, recorded_at"
)


def record(
    conn: psycopg.Connection[Any],
    brief: Brief,
    file: Path,
    *,
    commit: str | None = None,
    now: datetime | None = None,
) -> Recorded:
    """Record the pre-registration of a brief version (module docstring)."""
    h = hashes(brief)
    if commit is not None and not COMMIT_RE.match(commit):
        raise PreregistrationError("--commit takes a git commit hash (7-40 lowercase hex digits)")
    if not file.is_file():
        raise PreregistrationError(f"no such file: {file}")
    data = file.read_bytes()
    if not data.strip():
        raise PreregistrationError("the pre-registration file is empty")
    if quotes_brief(data.decode("utf-8", errors="replace"), brief):
        raise PreregistrationError(
            "the pre-registration file quotes brief content; commit the SHA-256 values from "
            f"`pigtail brief preregister {brief.brief_id} --print-hashes` instead (R8.2)"
        )
    done = conn.execute(
        "SELECT count(*) FROM brief_selection WHERE brief_id = %s AND brief_version = %s",
        (brief.brief_id, brief.version),
    ).fetchone()
    if done and done[0]:
        raise PreregistrationError(
            f"{brief.brief_id} v{brief.version} already has an outcome sort; a pre-registration "
            "written now would not precede it (outcome-model §5.8). Edit the brief to start a new "
            "version, or label the analysis exploratory"
        )
    row = conn.execute(
        f"INSERT INTO brief_preregistration (brief_id, brief_version, brief_hash, success_sha256,"
        f" selection_params_sha256, file_path, file_sha256, git_commit, recorded_at)"
        f" VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLS}",
        (
            brief.brief_id,
            brief.version,
            h["brief_sha256"],
            h["success_definition_sha256"],
            h["selection_params_sha256"],
            str(file),
            hashlib.sha256(data).hexdigest(),
            commit,
            now or utcnow(),
        ),
    ).fetchone()
    assert row is not None
    return Recorded(*row)


def latest(conn: psycopg.Connection[Any], brief: Brief) -> Recorded | None:
    """The latest pre-registration recorded for this exact brief version and content."""
    row = conn.execute(
        f"SELECT {_COLS} FROM brief_preregistration WHERE brief_id = %s AND brief_version = %s"
        " AND brief_hash = %s ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (brief.brief_id, brief.version, brief.content_hash()),
    ).fetchone()
    return None if row is None else Recorded(*row)


def require(conn: psycopg.Connection[Any], brief: Brief) -> Recorded:
    """The gate before the outcome sort (R8.2). Raises `PreregistrationMissing` with what to do."""
    rec = latest(conn, brief)
    todo = (
        f"write it from {TEMPLATE} (no brief content: paste the hashes from `pigtail brief "
        f"preregister {brief.brief_id} --print-hashes`), commit and push it, then run `pigtail "
        f"brief preregister {brief.brief_id} --file <path> --commit <sha>`"
    )
    if rec is None:
        raise PreregistrationMissing(
            f"{brief.brief_id} v{brief.version} has no recorded pre-registration; the outcome "
            f"sort is refused until it has one (R8.2, ADR-065): {todo}. Nothing was computed or "
            "fetched"
        )
    if rec.selection_params_sha256 != hashes(brief)["selection_params_sha256"]:
        raise PreregistrationMissing(
            f"the pre-registration of {brief.brief_id} v{brief.version} was recorded for other "
            f"selection parameters (the selection rule changed since, now {SELECTION_VERSION}); "
            f"pre-register again: {todo}. Nothing was computed or fetched"
        )
    return rec
