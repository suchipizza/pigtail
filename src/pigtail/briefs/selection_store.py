"""The selection stage of `pigtail run --brief` and its stored results (PRD R4.3, R4.8, R4.9,
R18.6, R19.1; outcome-model v2.1 §5.7; migration 0022; ADR-077).

`run_stage` runs after the shortlist is final: it fetches the outcome data it can
(`outcomes.fetch_outcome_data`, checkpointed), builds the inputs from stored data
(`outcomes.load_inputs`), runs the pure selection (`selection.select`) and stores one
`brief_selection` row with its provenance (brief id, version and content hash, brief run, data
version after the fetch with `as_of` folded in as `dv1-…@YYYY-MM-DD`, `as_of`, selection,
outcome-model and analysis-params versions, code commit, inputs and result hashes) plus one
`brief_selection_case` row per shortlisted repo. It first requires the brief version's
pre-registration (R8.2, `preregistration.require`) and the Show HN connector for the launch
lookup (ADR-082: without it the selection is refused before anything is fetched or stored).

Since selection-v5 (ADR-083, migration 0025) one selection holds **views A and B** and the
context view C (`selection.select_views`): `brief_selection` keeps the overall result hash and
summary, `balance` and `sensitivity` per view (keyed by view), `views` (each view's summary,
steps and result hash) and `context`; `brief_selection_case` has one row per shortlisted repo
**per view** (`view` = `follow_through` or `launch`; rows of older selections are `plain`).

`view` / `latest` read the stored result back for the CLI (`pigtail brief selection show`).
Selections are append-only history: a new run (for example `--incremental`) adds a row, and the
latest per brief version is shown.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from pigtail.analysis.params import PARAMS_VERSION
from pigtail.briefs.candidates import Candidate
from pigtail.briefs.confirm import Confirmer
from pigtail.briefs.model import Brief
from pigtail.briefs.selection import (
    OUTCOME_MODEL_VERSION,
    SELECTION_VERSION,
    Context,
    Definition,
    Selections,
    select_views,
)


def new_selection_id() -> str:
    return "sel_" + secrets.token_hex(10)


@dataclass
class StageResult:
    selection_id: str
    fetch: dict[str, Any]
    counts: dict[str, Any]
    data_version: str
    result_hash: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "fetch": self.fetch,
            "data_version": self.data_version,
            "result_hash": self.result_hash,
            **self.counts,
        }


def save(
    conn: psycopg.Connection[Any],
    sel: Selections,
    brief: Brief,
    cands: dict[str, Candidate],
    *,
    brief_run_id: str | None,
    data_version: str,
    as_of: date,
    code_commit: str | None,
    now: datetime,
) -> str:
    """Store one selection (both views and the context view) and its case rows, one per repo
    and view, in one transaction."""
    sid = new_selection_id()
    views = {
        k: {
            "label": v.summary["view_label"],
            "result_hash": v.result_hash,
            "summary": v.summary,
        }
        for k, v in sel.views.items()
    }
    with conn.transaction():
        conn.execute(
            "INSERT INTO brief_selection (id, brief_id, brief_version, brief_hash, brief_run_id,"
            " data_version, as_of, selection_version, outcome_model_version, params_version,"
            " code_commit, inputs_hash, result_hash, params, summary, balance, sensitivity,"
            " views, context, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                sid,
                brief.brief_id,
                brief.version,
                brief.content_hash(),
                brief_run_id,
                data_version,
                as_of,
                SELECTION_VERSION,
                OUTCOME_MODEL_VERSION,
                PARAMS_VERSION,
                code_commit,
                sel.inputs_hash,
                sel.result_hash,
                Jsonb(sel.params),
                Jsonb(sel.summary),
                Jsonb({k: v.balance for k, v in sel.views.items()}),
                Jsonb({k: v.sensitivity for k, v in sel.views.items()}),
                Jsonb(views),
                Jsonb(sel.context),
                now,
            ),
        )
        rows = []
        for vkey, vsel in sel.views.items():
            for case in vsel.cases:
                rows.append(_case_row(sid, vkey, case, cands[case["candidate_ref"]]))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO brief_selection_case (selection_id, view, candidate_ref,"
                " repo_full_name, repo_host_id, repo_id, panel, distance, named_index,"
                " is_reference, role, rank, pair_id, pair_panel, headline, sensitivity_flags,"
                " detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s)",
                rows,
            )
    return sid


def _case_row(sid: str, vkey: str, case: dict[str, Any], c: Candidate) -> tuple[Any, ...]:
    pair = case["pair"] or {}
    sens = case["sensitivity"] or {}
    return (
        sid,
        vkey,
        case["candidate_ref"],
        c.repo_full_name,
        c.repo_host_id,
        c.repo_id,
        case["panel"],
        case["distance"],
        case["named_index"],
        case["is_reference"],
        case["role"],
        case["rank"],
        pair.get("pair_id"),
        pair.get("panel"),
        pair.get("headline"),
        list(sens.get("flags") or []),
        Jsonb(case),
    )


def run_stage(
    conn: psycopg.Connection[Any],
    brief: Brief,
    *,
    brief_run_id: str | None,
    github: Any,
    checkpoint: dict[str, Any],
    save_checkpoint: Callable[[dict[str, Any]], None],
    run_date: date,
    clock: Callable[[], datetime],
    recorder: Any = None,
    hn: Any = None,
    confirmer: Confirmer | None = None,
) -> StageResult:
    """The selection stage (module docstring). Raises `PreregistrationMissing` before anything
    is fetched or computed when the brief version has no recorded pre-registration (R8.2,
    ADR-065), `LaunchLookupUnavailable` likewise when the Show HN connector is off or missing
    (the pre-registered rule includes the launch lookup, ADR-082), `SelectionError` when the
    shortlist isn't final; `BudgetExhausted` from the GitHub
    budget pauses it (the checkpoint keeps the repos done). `confirmer` runs the paid Haiku
    check of title-only launch matches (ADR-083 E); without it, or without approval, they are
    excluded (fail closed); a Haiku batch still running raises `BatchPending` (resumable)."""
    from pigtail.briefs.cache import data_version
    from pigtail.briefs.discovery import window_bounds
    from pigtail.briefs.outcomes import (
        LaunchLookupUnavailable,
        ambiguous_title_matches,
        fetch_outcome_data,
        launch_lookup_blocked,
        load_inputs,
        shortlisted,
    )
    from pigtail.briefs.preregistration import require
    from pigtail.capture.runs import git_commit

    cands = shortlisted(conn, brief)  # SelectionError unless the shortlist is final
    require(conn, brief)  # outcome-model §5.8: the point of no return needs a pre-registration
    if (why := launch_lookup_blocked(hn)) is not None:  # ADR-082: the lookup is pre-registered
        raise LaunchLookupUnavailable(why)
    if "as_of" not in checkpoint:  # fixed for the whole run, so a resume judges `pending` alike
        checkpoint["as_of"] = clock().date().isoformat()
        save_checkpoint(checkpoint)
    as_of = date.fromisoformat(checkpoint["as_of"])
    window = window_bounds(brief, run_date)
    fetch_cp = checkpoint.setdefault("fetch", {})
    fetch = fetch_outcome_data(
        conn,
        brief,
        github,
        cands,
        window_start=window[0].date(),
        as_of=as_of,
        checkpoint=fetch_cp,
        save=lambda _cp: save_checkpoint(checkpoint),
        brief_run_id=brief_run_id,
        now=clock(),
        recorder=recorder,
        hn=hn,
        window=window,
        confirmer=confirmer,
    )
    cands = shortlisted(conn, brief)  # metadata and looked-up launches may have been added
    inputs = load_inputs(conn, brief, cands, window=window, as_of=as_of)
    notes = lookup_notes(fetch.launch_lookup or {})
    if shared := ambiguous_title_matches(cands):
        notes.append(
            f"launch lookup: {len(shared)} title matches dropped as claimed by more than one "
            "shortlisted repo or linked by URL to another (ADR-082 rule e)"
        )
    sel = select_views(inputs, Context.from_brief(brief), Definition.from_brief(brief), notes=notes)
    # R4.8 determinism is keyed on (brief version, data version); `as_of` decides `pending`, so
    # it is folded into the selection's data version (M22 verifier round 2): same data, another
    # day -> another data version.
    dv = f"{data_version(conn)}@{as_of.isoformat()}"
    sid = save(
        conn,
        sel,
        brief,
        {c.ref: c for c in cands},
        brief_run_id=brief_run_id,
        data_version=dv,
        as_of=as_of,
        code_commit=git_commit(),
        now=clock(),
    )
    counts = {
        "views": {
            k: {
                "roles": v.summary["roles"],
                "winners": v.summary["counts"]["winners"],
                "matched_losers": v.summary["counts"]["matched_losers"],
                "headline_pairs": v.balance["headline_pairs"],
                "exemplar_pairs": v.summary["exemplars"]["pairs"],
                "final_distance": v.summary["final_distance"],
                "steps": len(v.summary["steps"]),
                "result_hash": v.result_hash,
            }
            for k, v in sel.views.items()
        },
        "warnings": len(sel.summary["warnings"]),
    }
    return StageResult(sid, fetch.to_dict(), counts, dv, sel.result_hash, fetch.evidence_ids)


def lookup_notes(lk: dict[str, Any]) -> list[str]:
    """Selection warnings from the launch lookup's counts (ADR-082, ADR-083 E): title-only
    candidates the title rule rejected, and title matches the E rule did not confirm, by
    reason (counts only; no title is kept)."""
    notes: list[str] = []
    if lk.get("title_rejected_total"):
        reasons = ", ".join(f"{k} {v}" for k, v in sorted(lk["title_rejected"].items()))
        notes.append(
            f"launch lookup: {lk['title_rejected_total']} title-only candidates rejected by the "
            f"title rule ({reasons}; ADR-082)"
        )
    if lk.get("title_unconfirmed_total"):
        reasons = ", ".join(f"{k} {v}" for k, v in sorted(lk["title_unconfirmed"].items()))
        notes.append(
            f"launch lookup: {lk['title_unconfirmed_total']} title-only matches not confirmed "
            f"and excluded ({reasons}; ADR-083 E)"
        )
    if lk.get("title_confirmed"):
        how = ", ".join(f"{k} {v}" for k, v in sorted(lk["title_confirmed"].items()))
        notes.append(f"launch lookup: title-only matches confirmed ({how}; ADR-083 E)")
    return notes


# --- reading back -------------------------------------------------------------------------------
_SEL_COLS = (
    "id, brief_id, brief_version, brief_hash, brief_run_id, data_version, as_of,"
    " selection_version, outcome_model_version, params_version, code_commit, inputs_hash,"
    " result_hash, params, summary, balance, sensitivity, views, context, created_at"
)


def latest(
    conn: psycopg.Connection[Any], brief_id: str, brief_version: int
) -> dict[str, Any] | None:
    cur = conn.execute(
        f"SELECT {_SEL_COLS} FROM brief_selection WHERE brief_id = %s AND brief_version = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (brief_id, brief_version),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d.name for d in cur.description or []]
    return dict(zip(cols, row, strict=True))


def cases(
    conn: psycopg.Connection[Any], selection_id: str, view: str | None = None
) -> list[dict[str, Any]]:
    """Case rows of a selection, per view (`view` None: every view)."""
    cur = conn.execute(
        "SELECT view, candidate_ref, repo_full_name, panel, distance, is_reference, role, rank,"
        " pair_id, pair_panel, headline, sensitivity_flags, detail FROM brief_selection_case"
        " WHERE selection_id = %s AND (%s::text IS NULL OR view = %s)"
        " ORDER BY view, pair_id NULLS LAST, rank NULLS LAST, candidate_ref",
        (selection_id, view, view),
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def view(conn: psycopg.Connection[Any], brief_id: str, brief_version: int) -> dict[str, Any]:
    """The latest stored selection of a brief version, with its cases (CLI `show`): `cases`
    holds every row, `cases_by_view` the rows per view (`follow_through`, `launch`; `plain`
    for a selection made before selection-v5)."""
    sel = latest(conn, brief_id, brief_version)
    if sel is None:
        return {"brief_id": brief_id, "brief_version": brief_version, "selection": None}
    rows = cases(conn, sel["id"])
    by_view: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_view.setdefault(r["view"], []).append(r)
    return {
        "brief_id": brief_id,
        "brief_version": brief_version,
        "selection": sel,
        "cases": rows,
        "cases_by_view": by_view,
    }
