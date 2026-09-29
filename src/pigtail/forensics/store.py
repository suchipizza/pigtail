"""Postgres rows of the pilot (migration 0029; ADR-086). Every row carries its provenance: the
brief id and version, the selection id, the code commit, and for coded rows the prompt
fingerprint, model, batch id, codebook and frame versions (R7.4, R18.6). Project-level only."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

DECAY_OFFSETS_DAYS: tuple[int, ...] = (1, 7, 30)


@dataclass
class PilotCase:
    """One pilot case: a (view, repo) of the stored selection, with its blind coding id."""

    case_key: str
    view: str
    candidate_ref: str
    repo_full_name: str
    repo_id: str | None
    repo_host_id: int | None
    position: int
    role: str
    pair_id: int | None
    anchor: dict[str, Any]
    coding_id: str = ""
    star_anomaly_flag: str | None = None
    evidence_status: str = "pending"
    evidence_stats: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] | None = None  # report facts (M24, `report-facts-v1`)
    reused_from: str | None = None  # the pilot run this case's coding was copied from

    @property
    def anchor_at(self) -> datetime:
        return datetime.fromisoformat(str(self.anchor["at"]))

    @property
    def owner(self) -> str:
        return self.repo_full_name.partition("/")[0]


def coding_id_for(brief_run_id: str, case_key: str) -> str:
    """A random-looking, stable id (blinding, codebook §11.6): nothing in it names the case."""
    h = hashlib.sha256(f"pigtail-coding-id|{brief_run_id}|{case_key}".encode()).hexdigest()
    return "cod_" + h[:16]


# --- pilot run --------------------------------------------------------------------------------
def create_pilot(
    conn: psycopg.Connection[Any],
    *,
    brief_run_id: str,
    brief_id: str,
    brief_version: int,
    brief_hash: str,
    selection_id: str,
    data_version: str | None,
    cases_requested: int,
    case_rule_version: str,
    frame_version: str,
    codebook_version: str,
    code_commit: str | None,
    prompt_fingerprints: dict[str, str],
    models: dict[str, str],
    cases: Sequence[PilotCase],
) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO brief_pilot (brief_run_id, brief_id, brief_version, brief_hash,"
            " selection_id, data_version, cases_requested, case_rule_version, frame_version,"
            " codebook_version, code_commit, prompt_fingerprints, models) VALUES (%s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                brief_run_id,
                brief_id,
                brief_version,
                brief_hash,
                selection_id,
                data_version,
                cases_requested,
                case_rule_version,
                frame_version,
                codebook_version,
                code_commit,
                Jsonb(prompt_fingerprints),
                Jsonb(models),
            ),
        )
        for c in cases:
            c.coding_id = coding_id_for(brief_run_id, c.case_key)
            conn.execute(
                "INSERT INTO brief_pilot_case (brief_run_id, case_key, coding_id, view,"
                " candidate_ref, repo_full_name, repo_id, repo_host_id, position, role, pair_id,"
                " anchor) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    brief_run_id,
                    c.case_key,
                    c.coding_id,
                    c.view,
                    c.candidate_ref,
                    c.repo_full_name,
                    c.repo_id,
                    c.repo_host_id,
                    c.position,
                    c.role,
                    c.pair_id,
                    Jsonb({**c.anchor, "star_anomaly_flag": c.star_anomaly_flag}),
                ),
            )


def pilot_row(conn: psycopg.Connection[Any], brief_run_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM brief_pilot WHERE brief_run_id = %s", (brief_run_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d.name for d in cur.description or []], row, strict=True))


def find_resumable(
    conn: psycopg.Connection[Any],
    brief_id: str,
    brief_version: int,
    selection_id: str,
    cases_requested: int,
    statuses: Sequence[str],
    kind: str = "pilot",
) -> str | None:
    """The latest pilot (or full coding, `kind` `coding`) run of this brief version, selection
    and size in one of `statuses`."""
    row = conn.execute(
        "SELECT r.id FROM brief_runs r JOIN brief_pilot p ON p.brief_run_id = r.id"
        " WHERE r.kind = %s AND r.brief_id = %s AND r.brief_version = %s"
        " AND p.selection_id = %s AND p.cases_requested = %s AND r.status = ANY(%s)"
        " ORDER BY r.created_at DESC, r.id DESC LIMIT 1",
        (kind, brief_id, brief_version, selection_id, cases_requested, list(statuses)),
    ).fetchone()
    return str(row[0]) if row else None


def latest_pilot(
    conn: psycopg.Connection[Any],
    brief_id: str,
    brief_version: int | None = None,
    kind: str = "pilot",
) -> dict[str, Any] | None:
    """The latest run of `kind` (`pilot`, or `coding` for the full coding of M24)."""
    q = (
        "SELECT p.brief_run_id FROM brief_pilot p JOIN brief_runs r ON r.id = p.brief_run_id"
        " WHERE r.kind = %s AND p.brief_id = %s AND (%s::int IS NULL OR p.brief_version = %s)"
        " ORDER BY p.created_at DESC, p.brief_run_id DESC LIMIT 1"
    )
    row = conn.execute(q, (kind, brief_id, brief_version, brief_version)).fetchone()
    return pilot_row(conn, str(row[0])) if row else None


def load_cases(conn: psycopg.Connection[Any], brief_run_id: str) -> list[PilotCase]:
    rows = conn.execute(
        "SELECT case_key, view, candidate_ref, repo_full_name, repo_id, repo_host_id, position,"
        " role, pair_id, anchor, coding_id, evidence_status, evidence_stats, facts, reused_from"
        " FROM brief_pilot_case WHERE brief_run_id = %s ORDER BY position",
        (brief_run_id,),
    ).fetchall()
    out = []
    for r in rows:
        anchor = dict(r[9] or {})
        flag = anchor.pop("star_anomaly_flag", None)
        out.append(
            PilotCase(
                case_key=r[0],
                view=r[1],
                candidate_ref=r[2],
                repo_full_name=r[3],
                repo_id=r[4],
                repo_host_id=r[5],
                position=r[6],
                role=r[7],
                pair_id=r[8],
                anchor=anchor,
                coding_id=r[10],
                star_anomaly_flag=flag,
                evidence_status=r[11],
                evidence_stats=dict(r[12] or {}),
                facts=dict(r[13]) if r[13] is not None else None,
                reused_from=r[14],
            )
        )
    return out


def set_case_facts(
    conn: psycopg.Connection[Any],
    brief_run_id: str,
    case_key: str,
    facts: dict[str, Any],
    version: str,
) -> None:
    conn.execute(
        "UPDATE brief_pilot_case SET facts = %s, facts_version = %s"
        " WHERE brief_run_id = %s AND case_key = %s",
        (Jsonb(facts), version, brief_run_id, case_key),
    )


def set_case_evidence(
    conn: psycopg.Connection[Any], brief_run_id: str, case_key: str, stats: dict[str, Any]
) -> None:
    conn.execute(
        "UPDATE brief_pilot_case SET evidence_status = 'done', evidence_stats = %s"
        " WHERE brief_run_id = %s AND case_key = %s",
        (Jsonb(stats), brief_run_id, case_key),
    )


def update_summary(conn: psycopg.Connection[Any], brief_run_id: str, **items: Any) -> None:
    conn.execute(
        "UPDATE brief_pilot SET summary = summary || %s WHERE brief_run_id = %s",
        (Jsonb(items), brief_run_id),
    )


def add_batch_ids(conn: psycopg.Connection[Any], brief_run_id: str, ids: Iterable[str]) -> None:
    row = pilot_row(conn, brief_run_id)
    have = list((row or {}).get("batch_ids") or [])
    merged = have + [i for i in ids if i not in have]
    conn.execute(
        "UPDATE brief_pilot SET batch_ids = %s WHERE brief_run_id = %s",
        (Jsonb(merged), brief_run_id),
    )


def add_invocation(
    conn: psycopg.Connection[Any],
    brief_run_id: str,
    *,
    commit: str | None,
    at: datetime,
    kind: str,
    run_record_id: str | None = None,
) -> int:
    """Append one invocation of the pilot (`create` or `resume`) with the code commit that ran
    it (ADR-086 addendum 3: a resumed pilot records the commit of every invocation; the steps it
    did are added by `mark_step`). Returns its number (1-based)."""
    row = pilot_row(conn, brief_run_id) or {}
    inv = list(row.get("invocations") or [])
    entry: dict[str, Any] = {
        "n": len(inv) + 1,
        "kind": kind,
        "commit": commit,
        "at": at.isoformat(),
        "steps": [],
    }
    if run_record_id is not None:
        entry["run_record_id"] = run_record_id
    inv.append(entry)
    conn.execute(
        "UPDATE brief_pilot SET invocations = %s WHERE brief_run_id = %s",
        (Jsonb(inv), brief_run_id),
    )
    return len(inv)


def mark_step(conn: psycopg.Connection[Any], brief_run_id: str, step: str) -> None:
    """Record that the latest invocation did `step` (once)."""
    row = pilot_row(conn, brief_run_id) or {}
    inv = list(row.get("invocations") or [])
    if not inv:
        return
    steps = list(inv[-1].get("steps") or [])
    if step in steps:
        return
    inv[-1] = {**inv[-1], "steps": [*steps, step]}
    conn.execute(
        "UPDATE brief_pilot SET invocations = %s WHERE brief_run_id = %s",
        (Jsonb(inv), brief_run_id),
    )


ANNOTATION_MAX_CHARS = 1_000


def add_annotation(
    conn: psycopg.Connection[Any],
    brief_run_id: str,
    *,
    note: str,
    at: datetime,
    commit: str | None = None,
    step: str | None = None,
    annotated_by_commit: str | None = None,
) -> dict[str, Any]:
    """Append a correction note to a pilot run's provenance, never changing what was recorded
    (`pigtail brief pilot-annotate`). `commit` and `step` state, when given, which code commit a
    step's recorded work was actually made at; `annotated_by_commit` is the code that wrote the
    note."""
    text = " ".join(note.split())
    if not text:
        raise ValueError("an annotation needs a note")
    if len(text) > ANNOTATION_MAX_CHARS:
        raise ValueError(f"the note is longer than {ANNOTATION_MAX_CHARS} characters")
    row = pilot_row(conn, brief_run_id)
    if row is None:
        raise ValueError(f"no pilot run {brief_run_id}")
    notes = list(row.get("annotations") or [])
    entry: dict[str, Any] = {"n": len(notes) + 1, "at": at.isoformat(), "note": text}
    if commit:
        entry["commit"] = commit
    if step:
        entry["step"] = step
    if annotated_by_commit:
        entry["annotated_by_commit"] = annotated_by_commit
    notes.append(entry)
    conn.execute(
        "UPDATE brief_pilot SET annotations = %s WHERE brief_run_id = %s",
        (Jsonb(notes), brief_run_id),
    )
    return entry


def coding_commits(conn: psycopg.Connection[Any], brief_run_id: str) -> dict[str, dict[str, int]]:
    """pass -> code commit -> coded rows (which code coded the stored rows)."""
    rows = conn.execute(
        "SELECT pass, COALESCE(code_commit, 'unknown'), count(*) FROM brief_coding"
        " WHERE brief_run_id = %s GROUP BY 1, 2 ORDER BY 1, 2",
        (brief_run_id,),
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for p, c, n in rows:
        out.setdefault(str(p), {})[str(c)] = int(n)
    return out


# --- evidence and gaps -------------------------------------------------------------------------
@dataclass(frozen=True)
class EvidenceRow:
    kind: str
    evidence_id: str
    content_hash: str
    item_date: str
    decay_url: str | None = None
    upstream_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def add_evidence(
    conn: psycopg.Connection[Any],
    pilot: dict[str, Any],
    case: PilotCase,
    row: EvidenceRow,
    *,
    captured_at: datetime,
    code_commit: str | None,
    schedule_decay: bool = True,
) -> int:
    """Insert one case item (idempotent per case and kind) and, for a pilot, its decay schedule
    (the full coding run of M24 schedules none: the decay study is the pilot's, ADR-089)."""
    got = conn.execute(
        "INSERT INTO brief_case_evidence (brief_run_id, case_key, brief_id, brief_version,"
        " selection_id, candidate_ref, repo_full_name, repo_id, repo_host_id, kind, evidence_id,"
        " content_hash, decay_url, upstream_hash, etag, last_modified, item_date, captured_at,"
        " detail, code_commit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s) ON CONFLICT (brief_run_id, case_key, kind) DO UPDATE SET"
        " evidence_id = EXCLUDED.evidence_id RETURNING id",
        (
            pilot["brief_run_id"],
            case.case_key,
            pilot["brief_id"],
            pilot["brief_version"],
            pilot["selection_id"],
            case.candidate_ref,
            case.repo_full_name,
            case.repo_id,
            case.repo_host_id,
            row.kind,
            row.evidence_id,
            row.content_hash,
            row.decay_url,
            row.upstream_hash,
            row.etag,
            row.last_modified,
            row.item_date,
            captured_at,
            Jsonb(row.detail),
            code_commit,
        ),
    ).fetchone()
    assert got is not None
    ce_id = int(got[0])
    if row.decay_url and schedule_decay:
        for d in DECAY_OFFSETS_DAYS:
            conn.execute(
                "INSERT INTO brief_evidence_decay (case_evidence_id, brief_run_id, brief_id,"
                " brief_version, repo_full_name, repo_id, repo_host_id, kind, offset_days, due_at,"
                " code_commit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (case_evidence_id, offset_days) DO NOTHING",
                (
                    ce_id,
                    pilot["brief_run_id"],
                    pilot["brief_id"],
                    pilot["brief_version"],
                    case.repo_full_name,
                    case.repo_id,
                    case.repo_host_id,
                    row.kind,
                    d,
                    captured_at + timedelta(days=d),
                    code_commit,
                ),
            )
    return ce_id


def add_gap(
    conn: psycopg.Connection[Any],
    brief_run_id: str,
    case: PilotCase,
    source: str,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO brief_case_gap (brief_run_id, case_key, candidate_ref, repo_full_name,"
        " repo_id, repo_host_id, source, reason, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
        " %s) ON CONFLICT (brief_run_id, case_key, source) DO UPDATE SET reason ="
        " EXCLUDED.reason, detail = EXCLUDED.detail",
        (
            brief_run_id,
            case.case_key,
            case.candidate_ref,
            case.repo_full_name,
            case.repo_id,
            case.repo_host_id,
            source,
            reason,
            Jsonb(detail or {}),
        ),
    )


def evidence_rows(
    conn: psycopg.Connection[Any], brief_run_id: str, case_key: str | None = None
) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT id, case_key, kind, evidence_id, content_hash, decay_url, item_date, captured_at,"
        " detail FROM brief_case_evidence WHERE brief_run_id = %s"
        " AND (%s::text IS NULL OR case_key = %s) ORDER BY case_key, kind",
        (brief_run_id, case_key, case_key),
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def gaps(conn: psycopg.Connection[Any], brief_run_id: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT case_key, source, reason, detail FROM brief_case_gap WHERE brief_run_id = %s"
        " ORDER BY case_key, source",
        (brief_run_id,),
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# --- codings -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class CodingRow:
    case: PilotCase
    pass_: str  # A | B | adjudicator | final
    unit: str
    field: str
    value: str
    status: str
    unknown_reason: str | None = None
    evidence_ids: tuple[str, ...] = ()
    excerpts: tuple[tuple[str, str], ...] = ()
    confidence: str | None = None
    excluded: str | None = None
    reason: str | None = None
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)
    provenance: dict[str, Any] = dataclasses.field(default_factory=dict)


def save_codings(
    conn: psycopg.Connection[Any],
    pilot: dict[str, Any],
    rows: Sequence[CodingRow],
    *,
    codebook_version: str,
    frame_version: str,
    code_commit: str | None,
) -> int:
    n = 0
    with conn.transaction(), conn.cursor() as cur:
        for r in rows:
            p = r.provenance
            cur.execute(
                "INSERT INTO brief_coding (brief_run_id, brief_id, brief_version, selection_id,"
                " case_key, coding_id, candidate_ref, repo_full_name, repo_id, repo_host_id, pass,"
                " unit, field, value, unknown_reason, evidence_ids, excerpts, confidence, status,"
                " excluded, reason, detail, model, llm_backend, prompt_id, prompt_version,"
                " prompt_fingerprint, batch_id, input_hash, codebook_version, frame_version,"
                " code_commit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (brief_run_id, case_key, pass, unit) DO NOTHING",
                (
                    pilot["brief_run_id"],
                    pilot["brief_id"],
                    pilot["brief_version"],
                    pilot["selection_id"],
                    r.case.case_key,
                    r.case.coding_id,
                    r.case.candidate_ref,
                    r.case.repo_full_name,
                    r.case.repo_id,
                    r.case.repo_host_id,
                    r.pass_,
                    r.unit,
                    r.field,
                    r.value,
                    r.unknown_reason,
                    list(r.evidence_ids),
                    Jsonb([{"evidence_id": e, "quote": q} for e, q in r.excerpts]),
                    r.confidence,
                    r.status,
                    r.excluded,
                    r.reason,
                    Jsonb(r.detail),
                    p.get("model"),
                    p.get("backend"),
                    p.get("prompt_id"),
                    p.get("prompt_version"),
                    p.get("prompt_fingerprint"),
                    p.get("batch_id"),
                    p.get("input_hash"),
                    codebook_version,
                    frame_version,
                    code_commit,
                ),
            )
            n += cur.rowcount
    return n


def codings(
    conn: psycopg.Connection[Any], brief_run_id: str, pass_: str | None = None
) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT case_key, coding_id, pass, unit, field, value, unknown_reason, evidence_ids,"
        " excerpts, confidence, status, excluded, reason, model, prompt_id, prompt_fingerprint,"
        " batch_id, detail FROM brief_coding WHERE brief_run_id = %s"
        " AND (%s::text IS NULL OR pass = %s) ORDER BY case_key, pass, unit",
        (brief_run_id, pass_, pass_),
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def failed_coding_cases(conn: psycopg.Connection[Any], brief_run_id: str) -> set[str]:
    """Cases whose coding request failed in pass A or B (rows excluded as `coding_failed`)."""
    rows = conn.execute(
        "SELECT DISTINCT case_key FROM brief_coding WHERE brief_run_id = %s"
        " AND pass IN ('A', 'B') AND excluded = 'coding_failed'",
        (brief_run_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def delete_codings(
    conn: psycopg.Connection[Any], brief_run_id: str, case_keys: Iterable[str] | None = None
) -> int:
    """Delete the coded rows (every pass) of `case_keys` (all cases: None) and the run's alpha,
    so the coding of those cases can be redone (ADR-086 addendum 1). Returns the rows deleted."""
    with conn.transaction():
        if case_keys is None:
            cur = conn.execute("DELETE FROM brief_coding WHERE brief_run_id = %s", (brief_run_id,))
        else:
            cur = conn.execute(
                "DELETE FROM brief_coding WHERE brief_run_id = %s AND case_key = ANY(%s)",
                (brief_run_id, sorted(case_keys)),
            )
        conn.execute("DELETE FROM brief_reliability WHERE brief_run_id = %s", (brief_run_id,))
    return int(cur.rowcount or 0)


def set_prompt_fingerprints(
    conn: psycopg.Connection[Any], brief_run_id: str, fingerprints: dict[str, str]
) -> None:
    """Record the prompts a redone coding runs under (on the pilot and its brief run)."""
    conn.execute(
        "UPDATE brief_pilot SET prompt_fingerprints = %s WHERE brief_run_id = %s",
        (Jsonb(fingerprints), brief_run_id),
    )
    conn.execute(
        "UPDATE brief_runs SET prompt_versions = %s WHERE id = %s",
        (Jsonb(fingerprints), brief_run_id),
    )


def coded_cases(conn: psycopg.Connection[Any], brief_run_id: str, pass_: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT case_key FROM brief_coding WHERE brief_run_id = %s AND pass = %s",
        (brief_run_id, pass_),
    ).fetchall()
    return {str(r[0]) for r in rows}


# --- reliability and the cost model ------------------------------------------------------------
def save_reliability(
    conn: psycopg.Connection[Any], pilot: dict[str, Any], rows: Sequence[dict[str, Any]]
) -> None:
    with conn.transaction():
        conn.execute(
            "DELETE FROM brief_reliability WHERE brief_run_id = %s", (pilot["brief_run_id"],)
        )
        for r in rows:
            conn.execute(
                "INSERT INTO brief_reliability (brief_run_id, brief_id, brief_version,"
                " selection_id, field, statistic, alpha, n_cases, n_units, n_pairable,"
                " n_excluded, disagreements, raw_agreement, ci, assessed, labels, reason, rates,"
                " alpha_version, codebook_version, frame_version, code_commit,"
                " prompt_fingerprints, models) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    pilot["brief_run_id"],
                    pilot["brief_id"],
                    pilot["brief_version"],
                    pilot["selection_id"],
                    r["field"],
                    r["statistic"],
                    r["alpha"],
                    r["n_cases"],
                    r["n_units"],
                    r["n_pairable"],
                    r["n_excluded"],
                    r["disagreements"],
                    r["raw_agreement"],
                    Jsonb(r["ci"]),
                    r["assessed"],
                    list(r["labels"]),
                    r["reason"],
                    Jsonb(r["rates"]),
                    r["alpha_version"],
                    pilot["codebook_version"],
                    pilot["frame_version"],
                    pilot["code_commit"],
                    Jsonb(pilot["prompt_fingerprints"]),
                    Jsonb(pilot["models"]),
                ),
            )


def reliability(conn: psycopg.Connection[Any], brief_run_id: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT field, statistic, alpha, n_cases, n_units, n_pairable, n_excluded, disagreements,"
        " raw_agreement, ci, assessed, labels, reason, rates FROM brief_reliability"
        " WHERE brief_run_id = %s ORDER BY field, statistic",
        (brief_run_id,),
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def save_cost_model(
    conn: psycopg.Connection[Any],
    *,
    brief_id: str,
    brief_version: int,
    brief_run_id: str,
    model_version: str,
    n_cases: int,
    per_case: dict[str, Any],
    projection: dict[str, Any],
    h6: bool,
    prices_as_of: str | None,
    code_commit: str | None,
) -> None:
    conn.execute(
        "INSERT INTO brief_case_cost_model (brief_id, brief_version, brief_run_id, model_version,"
        " n_cases, per_case, projection, h6, prices_as_of, code_commit) VALUES (%s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s)",
        (
            brief_id,
            brief_version,
            brief_run_id,
            model_version,
            n_cases,
            Jsonb(per_case),
            Jsonb(projection),
            h6,
            prices_as_of,
            code_commit,
        ),
    )


def latest_cost_model(
    conn: psycopg.Connection[Any], brief_id: str | None = None
) -> dict[str, Any] | None:
    """The latest measured per-case cost (of `brief_id`, else of any brief on this instance) of
    the current cost-model version with a measured cost above zero (ADR-086 addendum 1: a model
    stored from zero measured cost, as the first live pilot's all-failed coding stored, is never
    used)."""
    from pigtail.forensics.cost import COST_MODEL_VERSION

    cur = conn.execute(
        "SELECT brief_id, brief_version, brief_run_id, model_version, n_cases, per_case,"
        " projection, h6, prices_as_of, created_at FROM brief_case_cost_model"
        " WHERE (%s::text IS NULL OR brief_id = %s) AND model_version = %s"
        " AND COALESCE((per_case -> 'measured_usd_per_case' ->> 'total')::numeric, 0) > 0"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (brief_id, brief_id, COST_MODEL_VERSION),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d.name for d in cur.description or []], row, strict=True))


# --- reuse of a finished pilot's cases by the full coding (M24, ADR-089) -----------------------
_EVIDENCE_COLS = (
    "brief_id, brief_version, selection_id, candidate_ref, repo_full_name, repo_id, repo_host_id,"
    " kind, evidence_id, content_hash, decay_url, upstream_hash, etag, last_modified, item_date,"
    " captured_at, detail, code_commit"
)
_GAP_COLS = "candidate_ref, repo_full_name, repo_id, repo_host_id, source, reason, detail"
_CODING_COLS = (
    "brief_id, brief_version, selection_id, case_key, candidate_ref, repo_full_name, repo_id,"
    " repo_host_id, pass, unit, field, value, unknown_reason, evidence_ids, excerpts, confidence,"
    " status, excluded, reason, model, llm_backend, prompt_id, prompt_version,"
    " prompt_fingerprint, batch_id, input_hash, codebook_version, frame_version, code_commit,"
    " coded_at"
)


def reusable_pilot(
    conn: psycopg.Connection[Any],
    *,
    brief_id: str,
    brief_version: int,
    selection_id: str,
    fingerprints: dict[str, str],
    frame_version: str,
) -> tuple[str, set[str]] | None:
    """The latest finished pilot of this selection coded under the same prompts (fingerprints,
    thinking setting included) and frame, and the keys of its cases coded by both passes with a
    final value (no failed request): the full coding copies them instead of paying again."""
    rows = conn.execute(
        "SELECT p.brief_run_id, p.prompt_fingerprints, p.frame_version FROM brief_pilot p"
        " JOIN brief_runs r ON r.id = p.brief_run_id WHERE r.kind = 'pilot'"
        " AND r.status = 'succeeded' AND p.brief_id = %s AND p.brief_version = %s"
        " AND p.selection_id = %s ORDER BY p.created_at DESC, p.brief_run_id DESC",
        (brief_id, brief_version, selection_id),
    ).fetchall()
    for rid, fps, fv in rows:
        if dict(fps or {}) != fingerprints or fv != frame_version:
            continue
        keys = conn.execute(
            "SELECT case_key FROM brief_coding WHERE brief_run_id = %s GROUP BY case_key"
            " HAVING bool_or(pass = 'A') AND bool_or(pass = 'B') AND bool_or(pass = 'final')"
            " AND NOT bool_or(excluded IS NOT DISTINCT FROM 'coding_failed')",
            (rid,),
        ).fetchall()
        done = {str(k[0]) for k in keys}
        ev = conn.execute(
            "SELECT case_key FROM brief_pilot_case WHERE brief_run_id = %s"
            " AND evidence_status = 'done'",
            (rid,),
        ).fetchall()
        return str(rid), done & {str(k[0]) for k in ev}
    return None


def copy_pilot_case(
    conn: psycopg.Connection[Any], src_run_id: str, dst_run_id: str, case: PilotCase
) -> list[str]:
    """Copy one case's evidence links, gaps and coded rows (every pass, with their original
    provenance: model, prompt fingerprint, batch id, commit) from a finished pilot into the
    full coding run, under the new run's blind coding id. Nothing is fetched or called; no decay
    check is scheduled again. Returns the case's evidence ids (for the retention link)."""
    k = case.case_key
    with conn.transaction():
        conn.execute(
            f"INSERT INTO brief_case_evidence (brief_run_id, case_key, {_EVIDENCE_COLS})"
            f" SELECT %s, case_key, {_EVIDENCE_COLS} FROM brief_case_evidence"
            " WHERE brief_run_id = %s AND case_key = %s"
            " ON CONFLICT (brief_run_id, case_key, kind) DO NOTHING",
            (dst_run_id, src_run_id, k),
        )
        conn.execute(
            f"INSERT INTO brief_case_gap (brief_run_id, case_key, {_GAP_COLS})"
            f" SELECT %s, case_key, {_GAP_COLS} FROM brief_case_gap"
            " WHERE brief_run_id = %s AND case_key = %s"
            " ON CONFLICT (brief_run_id, case_key, source) DO NOTHING",
            (dst_run_id, src_run_id, k),
        )
        conn.execute(
            f"INSERT INTO brief_coding (brief_run_id, coding_id, detail, {_CODING_COLS})"
            f" SELECT %s, %s, detail || jsonb_build_object('reused_from', %s::text),"
            f" {_CODING_COLS} FROM brief_coding WHERE brief_run_id = %s AND case_key = %s"
            " ON CONFLICT (brief_run_id, case_key, pass, unit) DO NOTHING",
            (dst_run_id, case.coding_id, src_run_id, src_run_id, k),
        )
        row = conn.execute(
            "SELECT evidence_stats FROM brief_pilot_case WHERE brief_run_id = %s AND case_key = %s",
            (src_run_id, k),
        ).fetchone()
        stats = dict(row[0] or {}) if row else {}
        conn.execute(
            "UPDATE brief_pilot_case SET evidence_status = 'done', evidence_stats = %s,"
            " reused_from = %s WHERE brief_run_id = %s AND case_key = %s",
            (Jsonb(stats), src_run_id, dst_run_id, k),
        )
    case.evidence_status = "done"
    case.evidence_stats = stats
    case.reused_from = src_run_id
    ids = conn.execute(
        "SELECT evidence_id FROM brief_case_evidence WHERE brief_run_id = %s AND case_key = %s",
        (dst_run_id, k),
    ).fetchall()
    return [str(r[0]) for r in ids]
