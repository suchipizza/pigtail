"""`pigtail brief pilot <id>`: the pilot of a brief's first cases (WORK_ORDER §4.5 M23; PRD §9.4,
R7.2, R7.5, R15.11, R19.8; ADR-073.1, ADR-073.2, ADR-086).

**Case rule** (`pilot-cases-v1`, `select_cases`): from the latest stored selection of the brief
version (or `--selection`), headline pairs per headline view, ordered by the winner's rank then
the pair id; a pair is the winner and its first matched loser (smallest pair distance, then
candidate ref). Exemplar pairs (the exemplar and its first matched loser) come from view A, else
view B. The cases are taken in the fixed order

    A pair 1, B pair 1, exemplar pair 1, A pair 2, B pair 2, exemplar pair 2, ...

(winner before loser), skipping a (view, repo) already taken, until N (default 5: A1 winner and
loser, B1 winner and loser, exemplar 1). When N >= 2 and no view-B (or no view-A) case made it,
the last case is replaced by that view's first winner. `launch_undeclared` (view B's
undeclared-launch sub-population) and view C are never sampled. Deterministic for a stored
selection.

**Steps**, each checkpointed on the pilot's `brief_runs` row (kind `pilot`) and resumable by
running the same command again: case evidence → coder A and coder B batches → validation →
adjudication batch → alpha → cost report and projection → private reports. The estimate is shown
first; paid steps need `--approve-paid`; `BudgetGuard` checks the brief's `budget.money_usd` and
the monthly cap before every batch (H6: stop above them); a projection above the brief's cap
ends the pilot with H6 (exit 4). When the coding failed for every case the pilot ends `failed`
(exit 7) with the API error per case and no cost model or projection; the same command redoes
the failed coding, never paying twice for what succeeded (ADR-086 addendum 1).

**Provenance of a resumed pilot** (ADR-086 addendum 3): every invocation is recorded on the pilot
(`brief_pilot.invocations`: the code commit that ran it, when, create or resume, and the steps it
did), and every stored row (codings, alpha, the cost model, evidence) carries the commit of the
invocation that wrote it. `pigtail brief pilot-annotate` appends a correction note without
changing anything recorded. `rebuild_cost_model` re-stores the measured cost model of an
existing pilot from its ledger rows under the current rules (`pigtail brief pilot-cost
--rebuild`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from pigtail.briefs.budget import BudgetGuard, BudgetStop
from pigtail.briefs.model import Brief
from pigtail.forensics import coding
from pigtail.forensics import store as fstore
from pigtail.forensics.citations import CitationStats
from pigtail.forensics.cost import (
    ADR087_AT,
    PLANNING,
    CaseCostModel,
    current_settings,
    full_brief_cases,
    ledger_by_case,
    measurable,
    measured,
    model_rows,
    projection,
)
from pigtail.forensics.frame import CODEBOOK_VERSION, FRAME_VERSION, Adjudication, CaseCoding
from pigtail.forensics.prompts import ADJUDICATOR, JOB_ADJUDICATION, JOB_CODING, fingerprints
from pigtail.forensics.store import PilotCase
from pigtail.llm import BatchPending, LLMClient
from pigtail.llm.errors import LLMError, safe_error_message
from pigtail.llm.pricing import cost_usd

CASE_RULE_VERSION = "pilot-cases-v1"
HEADLINE_VIEWS = ("follow_through", "launch")  # view A, view B
VIEW_LABEL = {"follow_through": "A", "launch": "B", "launch_undeclared": "B-undeclared"}
DEFAULT_CASES = 5
RESUMABLE = ("planned", "running", "waiting_batch", "paused_budget", "failed")

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_APPROVAL, EXIT_BUDGET, EXIT_WAITING, EXIT_BUSY = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
)
EXIT_CODING_FAILED = 7  # every case's coding failed (ADR-086 addendum 1); run again to redo it

LIMITATIONS = (
    "Project-level evidence only (ADR-073.2): HN comments and mentions, Bluesky mention text and "
    "per-repo event actors are held until CB-12 and CB-06b; Reddit and X are gap sources. "
    "Patterns resting on them are mostly `unknown`.",
    "Alpha from a pilot of a handful of cases is noisy: every field is labelled 'pilot, n = N' "
    "and 'reliability not assessed' (codebook §10.4 needs 30 pairable units).",
    "LLM-coded, not human-validated (no H3 calibration sample).",
    "Coders are blind to outcome-proximal numbers (blind-v1, ADR-086); leakage through the "
    "evidence itself (a launch's timing, wording about popularity without a number) remains "
    "(codebook §11.6: recorded, not a breach).",
    "Repository metadata is the current state, not the state at T; README at T is the README "
    "at the last commit touching the README's current path before T.",
    "Edges (C6/C7), asset categories (C8) and triggers (C9/C10) are not coded in the pilot frame.",
    "Personal names inside project text (not handles) can't be detected reliably; coders are "
    "told never to quote them.",
)


# --- the case rule -----------------------------------------------------------------------------
def _pair_loser_key(r: Mapping[str, Any]) -> tuple[float, str]:
    pair = (r.get("detail") or {}).get("pair") or {}
    d = pair.get("distance")
    return (float(d) if d is not None else math.inf, str(r["candidate_ref"]))


def _pairs(
    rows: Sequence[Mapping[str, Any]], view: str, panel: str
) -> list[list[Mapping[str, Any]]]:
    winners_role = "winner" if panel == "field" else "exemplar"
    losers_role = "matched_loser" if panel == "field" else "exemplar_matched_loser"
    by_pair: dict[int, dict[str, list[Mapping[str, Any]]]] = {}
    for r in rows:
        if r["view"] != view or r.get("pair_id") is None or r.get("pair_panel") != panel:
            continue
        slot = by_pair.setdefault(int(r["pair_id"]), {"w": [], "l": []})
        if r["role"] == winners_role:
            slot["w"].append(r)
        elif r["role"] == losers_role:
            slot["l"].append(r)
    out = []
    for pid, slot in by_pair.items():
        if not slot["w"] or not slot["l"]:
            continue
        losers = sorted(slot["l"], key=_pair_loser_key)
        if panel == "field" and not any(bool(r.get("headline")) for r in losers[:1]):
            continue  # headline pairs only
        w = slot["w"][0]
        out.append((w.get("rank") or 10**9, pid, [w, losers[0]]))
    out.sort(key=lambda t: (t[0], t[1]))
    return [members for _rank, _pid, members in out]


def _case(r: Mapping[str, Any], position: int) -> PilotCase:
    detail = dict(r.get("detail") or {})
    anchor = dict(detail.get("anchor") or {})
    if detail.get("relaunch_events"):
        anchor["relaunch_events"] = list(detail["relaunch_events"])
    flag = (detail.get("star_anomaly") or {}).get("flag")
    return PilotCase(
        case_key=f"{r['view']}:{r['candidate_ref']}",
        view=str(r["view"]),
        candidate_ref=str(r["candidate_ref"]),
        repo_full_name=str(r["repo_full_name"]),
        repo_id=detail.get("repo_id") or r.get("repo_id"),
        repo_host_id=r.get("repo_host_id"),
        position=position,
        role=str(r["role"]),
        pair_id=r.get("pair_id"),
        anchor=anchor,
        star_anomaly_flag=None if flag is None else str(flag).lower(),
    )


def select_cases(rows: Sequence[Mapping[str, Any]], n: int = DEFAULT_CASES) -> list[PilotCase]:
    """The pilot's cases by `pilot-cases-v1` (module docstring)."""
    if n < 1:
        raise ValueError("--cases must be at least 1")
    a = _pairs(rows, "follow_through", "field")
    b = _pairs(rows, "launch", "field")
    ex = _pairs(rows, "follow_through", "exemplar") or _pairs(rows, "launch", "exemplar")
    order: list[Mapping[str, Any]] = []
    for i in range(max(len(a), len(b), len(ex))):
        for lst in (a, b, ex):
            if i < len(lst):
                order.extend(lst[i])
    chosen: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in order:
        key = (str(r["view"]), str(r["candidate_ref"]))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(r)
        if len(chosen) == n:
            break
    if n >= 2:
        for view, pairs in (("launch", b), ("follow_through", a)):
            if pairs and not any(r["view"] == view for r in chosen):
                w = pairs[0][0]
                keep = [r for r in chosen[:-1] if r is not w]
                chosen = [*keep, w]
    return [_case(r, i + 1) for i, r in enumerate(chosen)]


# --- estimate ---------------------------------------------------------------------------------
def estimate(
    brief: Brief,
    n_cases: int,
    model: CaseCostModel,
    *,
    extraction_model: str,
    synthesis_model: str,
    full_cases: int,
    brief_spent_usd: float,
    month_spent_usd: float,
    month_cap_usd: float,
    batch: bool = True,
) -> dict[str, Any]:
    """The pilot's cost estimate (shown before any work) and the full-brief projection it
    implies (planning or the latest measured per-case model)."""
    per = model.usd_per_case(extraction_model, batch=batch)
    api = brief.budget.llm_backend == "api"
    total = None if per["total"] is None else per["total"] * n_cases
    gh = {k: math.ceil(v * n_cases) for k, v in model.github_per_case().items()}
    proj = projection(
        model,
        extraction_model=extraction_model,
        synthesis_model=synthesis_model,
        full_cases=full_cases,
        coded_cases=0,
        brief_spent_usd=brief_spent_usd,
        cap_usd=brief.budget.money_usd,
        month_spent_usd=month_spent_usd,
        month_cap_usd=month_cap_usd,
        batch=batch,
    )
    return {
        "label": "estimate",
        "cases": n_cases,
        "cost_model": model.to_dict(),
        "extraction_model": extraction_model,
        "mode": "batch" if batch and api else "standard",
        "per_case_usd": {k: None if v is None else round(v, 4) for k, v in per.items()},
        "llm_calls": {
            "coder_a": n_cases,
            "coder_b": n_cases,
            "adjudication": math.ceil(n_cases * model.adjudication_share),
        },
        "total_usd": None if total is None or not api else round(total, 4),
        "github_requests": gh,
        "requires_approval": api and (total is None or total > 0),
        "caps": {
            "brief_usd": {"cap": brief.budget.money_usd, "spent": round(brief_spent_usd, 4)},
            "month_usd": {"cap": month_cap_usd, "spent": round(month_spent_usd, 4)},
        },
        "full_brief_projection": proj,
    }


def render_estimate(e: dict[str, Any]) -> str:
    p = e["full_brief_projection"]
    per = e["per_case_usd"]
    lines = [
        f"Pilot estimate ({e['cost_model']['source']} cost model, {e['extraction_model']}, "
        f"{e['mode']}): {e['cases']} cases",
        f"  per case: coder A ${per['coder_a']}, coder B ${per['coder_b']}, adjudication "
        f"${per['adjudication']} (share {e['cost_model']['adjudication_share']}), total "
        f"${per['total']}",
        f"  LLM calls: {e['llm_calls']}; total ${e['total_usd']}",
        f"  GitHub requests: {e['github_requests']}",
        f"  caps: brief ${e['caps']['brief_usd']['spent']} spent of "
        f"${e['caps']['brief_usd']['cap']}; month ${e['caps']['month_usd']['spent']} of "
        f"${e['caps']['month_usd']['cap']}",
        f"Full brief ({p['full_brief_cases']} cases): projected ${p['projected_total_usd']} of "
        f"the ${p['cap_usd']} cap" + (" — over the cap (H6)" if p["h6"] else " (within)"),
    ]
    return "\n".join(lines)


# --- running ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class PilotOptions:
    cases: int = DEFAULT_CASES
    approve_paid: bool = False
    selection_id: str | None = None
    wait_seconds: float | None = None
    poll_seconds: float = 60.0
    bootstrap_resamples: int = 10_000


@dataclass
class PilotDeps:
    conn: psycopg.Connection[Any]  # autocommit
    client: LLMClient
    snapshots: Any
    data_dir: Path
    github: Any = None
    pages: Any = None
    month_cap_usd: float = 200.0
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], None] | None = None
    run_record_id: str | None = None
    extraction_model: str | None = None
    synthesis_model: str = "claude-opus-5-5"
    code_commit: str | None = None  # the commit running this invocation (default: git HEAD)


@dataclass
class PilotOutcome:
    brief_run_id: str | None
    status: str
    exit_code: int
    message: str
    estimate: dict[str, Any] | None = None
    report_paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    stop: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _cost_model(conn: psycopg.Connection[Any], brief_id: str) -> CaseCostModel:
    row = fstore.latest_cost_model(conn, brief_id) or fstore.latest_cost_model(conn)
    return CaseCostModel.from_dict(row["per_case"]) if row is not None else PLANNING


def plan(
    conn: psycopg.Connection[Any],
    brief: Brief,
    opts: PilotOptions,
    deps: PilotDeps,
) -> tuple[dict[str, Any] | None, list[PilotCase], dict[str, Any] | None, str | None]:
    """(selection, cases, estimate, error) — database reads only (the dry run stops here)."""
    from pigtail.briefs.selection_store import cases as sel_cases
    from pigtail.briefs.selection_store import latest
    from pigtail.llm.batch import PgCostLedger

    assert brief.version is not None
    sel = latest(conn, brief.brief_id, brief.version)
    if opts.selection_id is not None:
        row = conn.execute(
            "SELECT id FROM brief_selection WHERE id = %s AND brief_id = %s",
            (opts.selection_id, brief.brief_id),
        ).fetchone()
        sel = {"id": row[0]} if row else None
    if sel is None:
        return (
            None,
            [],
            None,
            (f"no stored selection for {brief.brief_id} v{brief.version}: run the selection first"),
        )
    cases = select_cases(sel_cases(conn, sel["id"]), opts.cases)
    if not cases:
        return sel, [], None, "the selection has no headline pair to pilot"
    model = _cost_model(conn, brief.brief_id)
    from pigtail.briefs.budget import month_spend

    # the larger of the local usage store and the Postgres cost ledger (OPS-2)
    month = float(
        month_spend(
            deps.client.store,
            lambda since: PgCostLedger(conn).month_total(since),
            deps.clock(),
        )["usd"]
    )
    ext = deps.extraction_model or deps.client.model_for(JOB_CODING)
    est = estimate(
        brief,
        len(cases),
        model,
        extraction_model=ext,
        synthesis_model=deps.synthesis_model,
        full_cases=full_brief_cases(conn, sel["id"])["total"],
        brief_spent_usd=PgCostLedger(conn).brief_total(brief.brief_id),
        month_spent_usd=month,
        month_cap_usd=deps.month_cap_usd,
        batch=deps.client.batches_for(JOB_CODING),
    )
    return sel, cases, est, None


def _lock(conn: psycopg.Connection[Any], brief_id: str) -> bool:
    row = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s))", (f"pigtail-brief-pilot:{brief_id}",)
    ).fetchone()
    return bool(row and row[0])


def _unlock(conn: psycopg.Connection[Any], brief_id: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"pigtail-brief-pilot:{brief_id}",))


def run_pilot(brief: Brief, deps: PilotDeps, opts: PilotOptions) -> PilotOutcome:
    """Plan, check approval and caps, then run or resume the pilot (module docstring)."""
    conn = deps.conn
    sel, cases, est, err = plan(conn, brief, opts, deps)
    if err is not None or sel is None or est is None:
        return PilotOutcome(None, "refused", EXIT_FAILED, err or "nothing to pilot")
    if not _lock(conn, brief.brief_id):
        return PilotOutcome(
            None, "busy", EXIT_BUSY, "another pilot of this brief is running", estimate=est
        )
    try:
        return _run_locked(brief, deps, opts, sel, cases, est)
    finally:
        _unlock(conn, brief.brief_id)


def _run_locked(
    brief: Brief,
    deps: PilotDeps,
    opts: PilotOptions,
    sel: dict[str, Any],
    cases: list[PilotCase],
    est: dict[str, Any],
) -> PilotOutcome:
    from pigtail.briefs.cache import BriefRuns
    from pigtail.capture.runs import git_commit
    from pigtail.llm.batch import PgCostLedger

    conn = deps.conn
    assert brief.version is not None
    rid = fstore.find_resumable(
        conn, brief.brief_id, brief.version, sel["id"], opts.cases, RESUMABLE
    )
    runs = BriefRuns(conn)
    if rid is None:  # a finished pilot of the same selection and size is not paid for again
        done = fstore.find_resumable(
            conn, brief.brief_id, brief.version, sel["id"], opts.cases, ("succeeded",)
        )
        row = (fstore.pilot_row(conn, done) or {}) if done is not None else {}
        if done is not None and int((row.get("summary") or {}).get("coding_failed_cases") or 0):
            rid = done  # finished with some cases' coding failed: redo those (addendum 1)
        elif done is not None:
            return PilotOutcome(
                done,
                "complete",
                EXIT_OK,
                f"this pilot is done (run {done}); nothing to do. Its report is in the private "
                "report directory; `pigtail brief decay` follows its evidence",
                estimate=est,
                summary=dict(row.get("summary") or {}),
            )
    prior = runs.get(rid) if rid else None
    approved = opts.approve_paid or bool(prior and prior.get("approved_paid"))
    if est["requires_approval"] and not approved:
        return PilotOutcome(
            rid,
            "needs_approval",
            EXIT_APPROVAL,
            "the pilot has paid steps (LLM calls on the api backend); nothing "
            "was started: approve the estimate with --approve-paid (H6 above "
            "the caps)",
            estimate=est,
        )
    guard = BudgetGuard(
        brief.budget,
        deps.client.store,
        month_cap_usd=deps.month_cap_usd,
        approved_paid=approved,
        clock=deps.clock,
        brief_ledger=lambda: PgCostLedger(conn).brief_total(brief.brief_id),
        month_ledger=lambda since: PgCostLedger(conn).month_total(since),  # OPS-2
    )
    commit = deps.code_commit if deps.code_commit is not None else git_commit()
    if rid is None:
        try:  # the whole pilot must fit both caps before anything starts
            guard.check_llm("pilot estimate", est_usd=est["total_usd"])
        except BudgetStop as e:
            return PilotOutcome(
                None, "refused_budget", EXIT_BUDGET, str(e), estimate=est, stop=e.to_dict()
            )
        ext = deps.extraction_model or deps.client.model_for(JOB_CODING)
        run = runs.create(
            brief,
            data_version=None,
            estimate=est,
            approved_paid=approved,
            kind="pilot",
            code_commit=commit,
            codebook_version=CODEBOOK_VERSION,
            prompt_versions=fingerprints(),
            model_versions={"extraction": ext},
            run_id=deps.run_record_id,
        )
        rid = run.id
        fstore.create_pilot(
            conn,
            brief_run_id=rid,
            brief_id=brief.brief_id,
            brief_version=brief.version,
            brief_hash=brief.content_hash(),
            selection_id=sel["id"],
            data_version=_sel_data_version(conn, sel["id"]),
            cases_requested=opts.cases,
            case_rule_version=CASE_RULE_VERSION,
            frame_version=FRAME_VERSION,
            codebook_version=CODEBOOK_VERSION,
            code_commit=commit,
            prompt_fingerprints=fingerprints(),
            models={"coder_a": ext, "coder_b": ext, "adjudicator": ext},
            cases=cases,
        )
        kind = "create"
    else:
        conn.execute(
            "UPDATE brief_runs SET resumes = resumes + 1, approved_paid = approved_paid OR %s"
            " WHERE id = %s",
            (opts.approve_paid, rid),
        )
        kind = "resume"
    fstore.add_invocation(
        conn, rid, commit=commit, at=deps.clock(), kind=kind, run_record_id=deps.run_record_id
    )
    return _steps(brief, deps, opts, rid, guard, est, commit)


def _sel_data_version(conn: psycopg.Connection[Any], sid: str) -> str | None:
    row = conn.execute("SELECT data_version FROM brief_selection WHERE id = %s", (sid,)).fetchone()
    return str(row[0]) if row else None


def _set_run(conn: psycopg.Connection[Any], rid: str, status: str, **cols: Any) -> None:
    sets = ["status = %s"]
    vals: list[Any] = [status]
    if status == "running":
        sets.append("started_at = COALESCE(started_at, now())")
    if status in ("succeeded", "failed"):
        sets.append("finished_at = now()")
    for k, v in cols.items():
        sets.append(f"{k} = %s")
        vals.append(Jsonb(v) if isinstance(v, dict | list) else v)
    conn.execute(f"UPDATE brief_runs SET {', '.join(sets)} WHERE id = %s", (*vals, rid))


def _steps(
    brief: Brief,
    deps: PilotDeps,
    opts: PilotOptions,
    rid: str,
    guard: BudgetGuard,
    est: dict[str, Any],
    commit: str | None = None,
) -> PilotOutcome:
    from pigtail.briefs.candidates import CandidateStore
    from pigtail.connectors.github_budget import BudgetExhausted
    from pigtail.forensics.evidence import EvidenceStage, rendered_items

    conn = deps.conn
    pilot = fstore.pilot_row(conn, rid)
    assert pilot is not None and brief.version is not None
    cases = fstore.load_cases(conn, rid)
    _set_run(conn, rid, "running", stop=None)
    # 1. case evidence
    cands = {c.ref: c for c in CandidateStore(conn, brief.brief_id, brief.version).all()}
    stage = EvidenceStage(
        conn,
        snapshots=deps.snapshots,
        github=deps.github,
        pages=deps.pages,
        clock=deps.clock,
        run_id=deps.run_record_id,
        code_commit=commit,
    )
    try:
        if any(c.evidence_status != "done" for c in cases):
            fstore.mark_step(conn, rid, "case_evidence")
        stage.run(pilot, cases, cands)
    except BudgetExhausted as e:
        stop: dict[str, Any] = {"kind": "github_budget", "step": "case evidence", "detail": str(e)}
        _set_run(conn, rid, "paused_budget", stop=stop)
        return PilotOutcome(
            rid,
            "paused_budget",
            EXIT_BUDGET,
            f"GitHub request budget stop during case evidence ({e}); run the "
            "same command again to resume",
            estimate=est,
            stop=stop,
        )
    # 2-4. double coding, validation, adjudication
    model = CaseCostModel.from_dict(est["cost_model"])
    ext = pilot["models"]["coder_a"]
    per = model.usd_per_case(ext, batch=deps.client.batches_for(JOB_CODING))
    coder_usd = per["coder_a"]
    adj_usd = cost_usd(
        ext, model.adjudication.times(1), batch=deps.client.batches_for(JOB_ADJUDICATION)
    )
    inputs = coding.build_inputs(cases, lambda c: rendered_items(conn, deps.snapshots, rid, c))
    # money checks for the api backend only (the subscription backend costs no money; its usage
    # limits pause the queue, R15.5)
    paid = deps.client.backend_for(JOB_CODING).name == "api"
    before = guard.before_submit if paid else None
    recode = _prepare_recode(conn, rid, pilot)
    try:
        coded = fstore.coded_cases(conn, rid, "B")
        todo = {k: ci for k, ci in inputs.items() if k not in coded}
        if todo:
            fstore.mark_step(conn, rid, "double_coding")
            runs = coding.submit_and_collect(
                deps.client,
                todo,
                brief_run_id=rid,
                before_submit=before,
                est_usd_per_call=coder_usd,
                wait_seconds=opts.wait_seconds,
                poll_seconds=opts.poll_seconds,
                sleep=deps.sleep,
                on_batches=lambda ids: fstore.add_batch_ids(conn, rid, ids),
            )
            for p, r in runs.items():
                coding.link_cache(
                    deps.client, coding.PASS_PROMPT[p], CaseCoding, JOB_CODING, r, todo
                )
            known = (fstore.pilot_row(conn, rid) or {}).get("batch_ids") or []
            by_case: dict[str, dict[str, CitationStats]] = {}
            rows, _stats = coding.validate(
                todo, runs, coding.batch_of_cases(deps.client, known), per_case=by_case
            )
            fstore.save_codings(
                conn,
                pilot,
                rows,
                codebook_version=CODEBOOK_VERSION,
                frame_version=FRAME_VERSION,
                code_commit=commit,  # the commit that coded these rows (addendum 3)
            )
            _save_citations(conn, rid, by_case, {c.coding_id for c in cases})
        rows = _load_rows(conn, rid, cases)
        errors = coding_errors(rows)
        coded_ok = _coded_ok(rows)
        fstore.update_summary(
            conn,
            rid,
            coding_errors=errors,
            coding_failed_cases=len(cases) - len(_fully_coded(rows)),
            **({"recoded": recode} if recode else {}),
        )
        if not coded_ok:  # every coder call failed: nothing to adjudicate, measure or project
            return _coding_failed(brief, deps, rid, cases, rows, errors, est)

        final_done = fstore.coded_cases(conn, rid, "final")
        need = {k: ci for k, ci in inputs.items() if k not in final_done}
        if need:
            fstore.mark_step(conn, rid, "adjudication")
            sub = [r for r in rows if r.case.case_key in need]
            dis = coding.disagreements(sub)
            items, texts = coding.adjudication_items(need, dis)
            try:
                adj = coding.run_adjudication(
                    deps.client,
                    items,
                    brief_run_id=rid,
                    before_submit=before,
                    est_usd_per_call=adj_usd,
                    wait_seconds=opts.wait_seconds,
                    poll_seconds=opts.poll_seconds,
                    sleep=deps.sleep,
                )
            except BatchPending as e:
                fstore.add_batch_ids(conn, rid, e.batch_ids)
                raise
            fstore.add_batch_ids(conn, rid, adj.batch_ids)
            coding.link_cache(deps.client, ADJUDICATOR, Adjudication, JOB_ADJUDICATION, adj, need)
            known = (fstore.pilot_row(conn, rid) or {}).get("batch_ids") or []
            final = coding.finalize(
                need, sub, dis, adj, texts, coding.batch_of_cases(deps.client, known)
            )
            fstore.save_codings(
                conn,
                pilot,
                final,
                codebook_version=CODEBOOK_VERSION,
                frame_version=FRAME_VERSION,
                code_commit=commit,  # the commit that coded these rows (addendum 3)
            )
            rows = _load_rows(conn, rid, cases)
            adjudicated = [r for r in rows if r.pass_ == "adjudicator"]
            fstore.update_summary(
                conn,
                rid,
                adjudicated_cases=len({r.case.case_key for r in adjudicated}),
                adjudicated_units=len(adjudicated),
            )
    except BatchPending as e:
        _set_run(conn, rid, "waiting_batch")
        return PilotOutcome(
            rid,
            "waiting_batch",
            EXIT_WAITING,
            f"{len(e.batch_ids)} Message Batch(es) still running; run the same "
            "command again to collect them (they are never resubmitted)",
            estimate=est,
        )
    except BudgetStop as e:
        _set_run(conn, rid, "paused_budget", stop=e.to_dict())
        return PilotOutcome(
            rid, "paused_budget", EXIT_BUDGET, str(e), estimate=est, stop=e.to_dict()
        )
    except LLMError as e:
        stop = {
            "kind": "llm",
            "detail": type(e).__name__,
            "message": safe_error_message(e),
        }
        _set_run(conn, rid, "failed", stop=stop)
        return PilotOutcome(
            rid,
            "failed",
            EXIT_FAILED,
            f"LLM step failed ({type(e).__name__}: {stop['message']}); run again to resume",
            estimate=est,
            stop=stop,
        )
    # 5. alpha
    fstore.mark_step(conn, rid, "alpha")
    rel = coding.reliability_rows(rows, len(cases), resamples=opts.bootstrap_resamples)
    fstore.save_reliability(conn, {**pilot, "code_commit": commit}, rel)
    # 6. cost and projection
    return _finish(brief, deps, rid, pilot, cases, rows, rel, guard, est, commit)


def _prepare_recode(
    conn: psycopg.Connection[Any], rid: str, pilot: dict[str, Any]
) -> dict[str, Any] | None:
    """Make a failed coding redoable (ADR-086 addendum 1). Codings made under other prompts
    than the current ones are all redone (one frame per pilot; the recorded fingerprints follow);
    otherwise only the cases whose coding request failed in a pass are, and the result cache
    serves the other pass of such a case for free. Successful cases are never paid for again."""
    fps = fingerprints()
    have = fstore.coded_cases(conn, rid, "A") | fstore.coded_cases(conn, rid, "B")
    if dict(pilot.get("prompt_fingerprints") or {}) != fps:
        fstore.set_prompt_fingerprints(conn, rid, fps)
        pilot["prompt_fingerprints"] = fps
        if have:
            fstore.delete_codings(conn, rid)
            return {"reason": "prompts_changed", "cases": len(have)}
        return None
    failed = fstore.failed_coding_cases(conn, rid)
    if failed:
        fstore.delete_codings(conn, rid, failed)
        return {"reason": "coding_failed", "cases": len(failed)}
    return None


def _save_citations(
    conn: psycopg.Connection[Any],
    rid: str,
    by_case: Mapping[str, Mapping[str, CitationStats]],
    coding_ids: set[str],
) -> None:
    """Per-case citation stats (kept across a redone coding) and their totals per pass."""
    prev = dict(
        (fstore.pilot_row(conn, rid) or {}).get("summary", {}).get("citations_by_case") or {}
    )
    merged = {cid: v for cid, v in prev.items() if cid in coding_ids}
    for cid, passes in by_case.items():
        merged[cid] = {p: s.to_dict() for p, s in passes.items()}
    totals: dict[str, dict[str, Any]] = {}
    for passes in merged.values():
        for p, s in passes.items():
            t = totals.setdefault(p, {"reasons": {}})
            for k, v in s.items():
                if k == "reasons":
                    for r, n in v.items():
                        t["reasons"][r] = t["reasons"].get(r, 0) + n
                else:
                    t[k] = t.get(k, 0) + v
    fstore.update_summary(conn, rid, citations_by_case=merged, citations=totals)


def coding_errors(rows: Sequence[fstore.CodingRow]) -> dict[str, dict[str, dict[str, Any]]]:
    """coding id -> pass -> the failure of that pass's request ({failure, type, message})."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.pass_ not in ("A", "B") or r.excluded != "coding_failed":
            continue
        slot = out.setdefault(r.case.coding_id, {})
        if r.pass_ in slot:
            continue
        err = dict(r.detail.get("error") or {})
        slot[r.pass_] = {
            "failure": r.detail.get("failure"),
            "type": err.get("type") or r.detail.get("failure"),
            "message": err.get("message"),
        }
    return out


def _coded_ok(rows: Sequence[fstore.CodingRow]) -> set[str]:
    """Cases with at least one pass whose coding request succeeded."""
    return {
        r.case.case_key for r in rows if r.pass_ in ("A", "B") and r.excluded != "coding_failed"
    }


def _fully_coded(rows: Sequence[fstore.CodingRow]) -> set[str]:
    """Cases coded by both passes (no request of theirs failed)."""
    failed = {r.case.case_key for r in rows if r.excluded == "coding_failed"}
    return {r.case.case_key for r in rows if r.pass_ in ("A", "B")} - failed


def _coding_failed(
    brief: Brief,
    deps: PilotDeps,
    rid: str,
    cases: Sequence[PilotCase],
    rows: Sequence[fstore.CodingRow],
    errors: dict[str, dict[str, dict[str, Any]]],
    est: dict[str, Any],
) -> PilotOutcome:
    """Every case's coding failed: the pilot ends `failed` (EXIT_CODING_FAILED), with the
    errors per case in the run and a private failure report, and no cost model, projection or
    alpha (nothing was measured). Running the same command again redoes the coding."""
    from pigtail.forensics.report import pilot_markdown, write_report

    conn = deps.conn
    pilot = fstore.pilot_row(conn, rid) or {}
    kinds = _count(
        f"{e.get('type')}: {e.get('message') or '-'}"
        for passes in errors.values()
        for e in passes.values()
    )
    top = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    stop = {
        "kind": "coding_failed",
        "cases": len(cases),
        "errors": [{"error": k, "requests": n} for k, n in top],
    }
    by_case = ledger_by_case(conn, rid)
    total = sum(t.usd for stages in by_case.values() for t in stages.values())
    now = deps.clock()
    assert brief.version is not None
    fstore.mark_step(conn, rid, "report")
    pilot = fstore.pilot_row(conn, rid) or pilot
    proj = _no_projection("every coder call failed: no cost was measured")
    cost = _cost_report(cases, by_case, {}, None)
    report = _report(brief, pilot, cases, rows, [], cost, proj, 0, now, errors=errors, conn=conn)
    report["outcome"] = {"status": "failed", "reason": "coding_failed", "stop": stop}
    paths = write_report(
        deps.data_dir,
        brief.brief_id,
        brief.version,
        "pilot",
        report,
        pilot_markdown(report),
        day=now.date(),
    )
    fstore.update_summary(
        conn, rid, report_written=False, failure_report_written=True, projection=proj
    )
    _set_run(conn, rid, "failed", stop=stop, spend={"api_usd": round(total, 6), "h6": False})
    first = top[0][0] if top else "unknown error"
    return PilotOutcome(
        rid,
        "failed",
        EXIT_CODING_FAILED,
        f"coding failed for every case ({len(cases)}; e.g. {first}); no cost model or "
        "projection was written. Fix the cause and run the same command again: the failed "
        "coding is redone, nothing that succeeded is paid for twice",
        estimate=est,
        report_paths=paths,
        summary={"coding_errors": errors, "cases": len(cases)},
        stop=stop,
    )


def _no_projection(reason: str) -> dict[str, Any]:
    return {"label": "projection", "skipped": reason, "h6": False, "within_cap": None}


def _load_rows(
    conn: psycopg.Connection[Any], rid: str, cases: Sequence[PilotCase]
) -> list[fstore.CodingRow]:
    by_key = {c.case_key: c for c in cases}
    out = []
    for r in fstore.codings(conn, rid):
        out.append(
            fstore.CodingRow(
                by_key[r["case_key"]],
                r["pass"],
                r["unit"],
                r["field"],
                r["value"],
                r["status"],
                unknown_reason=r["unknown_reason"],
                evidence_ids=tuple(r["evidence_ids"] or ()),
                excerpts=tuple((e["evidence_id"], e["quote"]) for e in r["excerpts"] or []),
                confidence=r["confidence"],
                excluded=r["excluded"],
                reason=r["reason"],
                detail=dict(r.get("detail") or {}),
                provenance={
                    "model": r["model"],
                    "prompt_id": r["prompt_id"],
                    "prompt_fingerprint": r["prompt_fingerprint"],
                    "batch_id": r["batch_id"],
                },
            )
        )
    return out


ACTUAL_BASIS = (
    "actual spend of this pilot's cases: every billed ledger row of the run (failed attempts "
    "under earlier settings included), averaged over all the pilot's cases"
)


def _cost_report(
    cases: Sequence[PilotCase],
    by_case: Mapping[str, Mapping[str, Any]],
    gh: Mapping[str, Mapping[str, int]],
    model: CaseCostModel | None,
    *,
    model_per_case: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The actual cost per case and stage (every billed row), with per-stage averages that add
    up to `per_case_usd` (ADR-086 addendum 3), and the cost model's per-case figure beside it
    (the projection's basis: current settings only)."""
    per_case: dict[str, Any] = {}
    stage_sum: dict[str, float] = {"coder_a": 0.0, "coder_b": 0.0, "adjudication": 0.0}
    for c in cases:
        stages = {k: v.to_dict() for k, v in by_case.get(c.coding_id, {}).items()}
        usd = 0.0
        for k, st in stages.items():
            stage_sum[k] = stage_sum.get(k, 0.0) + float(st["usd"])
            usd += float(st["usd"])
        per_case[c.coding_id] = {
            "stages": stages,
            "usd": round(usd, 6),
            "github_requests": dict(gh.get(c.coding_id, {})),
        }
    n = max(len(cases), 1)
    stage_avg = {k: round(v / n, 6) for k, v in stage_sum.items()}
    unattributed = sum(t.usd for t in by_case.get("-", {}).values())
    modes = {s.get("mode") for c in per_case.values() for s in c["stages"].values()}
    return {
        "basis": ACTUAL_BASIS,
        "cases": len(cases),
        "total_usd": round(sum(stage_sum.values()), 6),
        "per_case_usd": round(sum(stage_avg.values()), 6),
        "per_case_usd_by_stage": stage_avg,
        "unattributed_usd": round(unattributed, 6),
        "cost_model_per_case_usd": dict(model_per_case) if model_per_case else None,
        "per_case": per_case,
        "mode": "batch"
        if modes <= {"batch"}
        else ("standard" if modes <= {"standard"} else "mixed"),
        "github_per_case": {}
        if model is None
        else {k: round(v, 2) for k, v in model.github.items()},
        "cost_model": None if model is None else model.to_dict(),
    }


def _finish(
    brief: Brief,
    deps: PilotDeps,
    rid: str,
    pilot: dict[str, Any],
    cases: Sequence[PilotCase],
    rows: Sequence[fstore.CodingRow],
    rel: Sequence[dict[str, Any]],
    guard: BudgetGuard,
    est: dict[str, Any],
    commit: str | None = None,
) -> PilotOutcome:
    from pigtail.forensics.report import pilot_markdown, write_report

    conn = deps.conn
    assert brief.version is not None
    fstore.mark_step(conn, rid, "cost_and_projection")
    by_case = ledger_by_case(conn, rid)
    gh = {c.coding_id: dict(c.evidence_stats.get("requests") or {}) for c in cases}
    model, proj = measure_and_project(
        conn,
        brief,
        pilot,
        cases,
        rows,
        synthesis_model=deps.synthesis_model,
        batch=deps.client.batches_for(JOB_CODING),
        backend=deps.client.backend_for(JOB_CODING).name,
        month_spent_usd=guard.month_spent(),
        month_cap_usd=deps.month_cap_usd,
        commit=commit,
    )
    cost = _cost_report(cases, by_case, gh, model, model_per_case=proj.get("per_case_usd"))
    total = float(cost["total_usd"])
    coded = [c for c in cases if c.case_key in _fully_coded(rows)]
    decay = conn.execute(
        "SELECT count(*) FROM brief_evidence_decay WHERE brief_run_id = %s", (rid,)
    ).fetchone()
    decay_n = int(decay[0]) if decay else 0
    now = deps.clock()
    fstore.mark_step(conn, rid, "report")
    pilot = fstore.pilot_row(conn, rid) or pilot  # with every batch id recorded so far
    errors = coding_errors(rows)
    report = _report(
        brief, pilot, cases, rows, rel, cost, proj, decay_n, now, errors=errors, conn=conn
    )
    paths = write_report(
        deps.data_dir,
        brief.brief_id,
        brief.version,
        "pilot",
        report,
        pilot_markdown(report),
        day=now.date(),
    )
    rel_summary = {
        "fields": len({r["field"] for r in rel}),
        "statistics": len(rel),
        "below_070": sum(1 for r in rel if r["alpha"] is not None and r["alpha"] < 0.70),
        "undefined": sum(1 for r in rel if r["alpha"] is None),
        "assessed": sum(1 for r in rel if r["assessed"]),
        "pooled_patterns": pooled_patterns(rel),
    }
    summary = {
        "cases": len(cases),
        "cases_coded": len(coded),
        "coding_failed_cases": len(cases) - len(coded),
        "adjudication_share": None if model is None else round(model.adjudication_share, 4),
        "views": sorted({VIEW_LABEL.get(c.view, c.view) for c in cases}),
        "roles": _count(c.role for c in cases),
        "gaps": _count(g["reason"] for g in fstore.gaps(conn, rid)),
        "reliability": rel_summary,
        "cost": {k: v for k, v in cost.items() if k != "per_case"},
        "projection": proj,
        "decay_scheduled": decay_n,
        "report_written": True,
    }
    fstore.update_summary(conn, rid, **summary)
    conn.execute("UPDATE brief_pilot SET finished_at = now() WHERE brief_run_id = %s", (rid,))
    spend = {"api_usd": round(total, 6), "h6": bool(proj["h6"])}
    _set_run(conn, rid, "succeeded", spend=spend)
    failed_note = (
        f"; coding failed for {len(cases) - len(coded)} case(s), see the report"
        if len(coded) < len(cases)
        else ""
    )
    if proj["h6"]:
        msg = (
            f"H6: the full-brief projection (USD {proj['projected_total_usd']}) exceeds the "
            f"brief's cap (USD {proj['cap_usd']}); stop — going on needs the owner's approval "
            "of spend above the cap (Directive §6.4)" + failed_note
        )
        return PilotOutcome(
            rid, "succeeded_h6", EXIT_BUDGET, msg, estimate=est, report_paths=paths, summary=summary
        )
    proj_text = (
        f"projection USD {proj['projected_total_usd']} of cap USD {proj['cap_usd']}"
        if "skipped" not in proj
        else f"no projection ({proj['skipped']})"
    )
    return PilotOutcome(
        rid,
        "succeeded",
        EXIT_OK,
        f"pilot done: {len(cases)} cases, API USD {total:.4f}; {proj_text}{failed_note}",
        estimate=est,
        report_paths=paths,
        summary=summary,
    )


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def _report(
    brief: Brief,
    pilot: dict[str, Any],
    cases: Sequence[PilotCase],
    rows: Sequence[fstore.CodingRow],
    rel: Sequence[dict[str, Any]],
    cost: dict[str, Any],
    proj: dict[str, Any],
    decay_n: int,
    now: datetime,
    *,
    errors: Mapping[str, Any] | None = None,
    conn: psycopg.Connection[Any] | None = None,
) -> dict[str, Any]:
    conn_rows: dict[str, dict[str, dict[str, fstore.CodingRow]]] = {}
    for r in rows:
        conn_rows.setdefault(r.case.case_key, {}).setdefault(r.unit, {})[r.pass_] = r
    out_cases = []
    for c in cases:
        units = []
        for unit, passes in sorted(conn_rows.get(c.case_key, {}).items()):
            f = passes.get("final")
            units.append(
                {
                    "unit": unit,
                    "final": f.value if f else None,
                    "A": passes["A"].value if "A" in passes else None,
                    "B": passes["B"].value if "B" in passes else None,
                    "adjudicated": "adjudicator" in passes,
                    "reason": passes["adjudicator"].reason if "adjudicator" in passes else None,
                    "status": f.status if f else None,
                    "unknown_reason": f.unknown_reason if f else None,
                    "evidence_ids": list(f.evidence_ids) if f else [],
                    "excerpts": [
                        {"evidence_id": e, "quote": q} for e, q in (f.excerpts if f else ())
                    ],
                }
            )
        out_cases.append(
            {
                "position": c.position,
                "coding_id": c.coding_id,
                "view": VIEW_LABEL.get(c.view, c.view),
                "role": c.role,
                "pair_id": c.pair_id,
                "repo": c.repo_full_name,
                "anchor": {k: v for k, v in c.anchor.items() if k != "relaunch_events"},
                "evidence": c.evidence_stats.get("items", []),
                "gaps": [
                    {"source": s, "reason": r}
                    for s, r in (c.evidence_stats.get("gaps") or {}).items()
                ],
                "coding_errors": dict((errors or {}).get(c.coding_id) or {}),
                "units": units,
            }
        )
    return {
        "provenance": {
            "brief_id": brief.brief_id,
            "brief_version": brief.version,
            "brief_hash": brief.content_hash(),
            "selection_id": pilot["selection_id"],
            "data_version": pilot.get("data_version"),
            "pilot_run": pilot["brief_run_id"],
            "code_commit": pilot["code_commit"],
            "code_commit_note": (
                "the commit that created the run; the commit of every invocation is in "
                "`invocations` and each coded row's in `coding_commits`"
            ),
            "invocations": list(pilot.get("invocations") or []),
            "coding_commits": {} if conn is None else fstore.coding_commits(conn, rid_of(pilot)),
            "annotations": list(pilot.get("annotations") or []),
            "codebook_version": pilot["codebook_version"],
            "frame_version": pilot["frame_version"],
            "case_rule_version": pilot["case_rule_version"],
            "prompt_fingerprints": pilot["prompt_fingerprints"],
            "models": pilot["models"],
            "batch_ids": pilot.get("batch_ids") or [],
            "date": now.date().isoformat(),
            "label": "LLM-coded, not human-validated",
        },
        "cases": out_cases,
        "reliability": list(rel),
        "cost": cost,
        "projection": proj,
        "decay": {"scheduled": decay_n, "offsets_days": list(fstore.DECAY_OFFSETS_DAYS)},
        "limitations": list(LIMITATIONS),
    }


def rid_of(pilot: Mapping[str, Any]) -> str:
    return str(pilot["brief_run_id"])


def pooled_patterns(rel: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The pooled C11a statistic (`pattern.*`) for the counts-only summary."""
    for r in rel:
        if r["field"] == "pattern.*" and r["statistic"] == "nominal":
            ci = dict(r.get("ci") or {})
            return {
                "alpha": r["alpha"],
                "n_pairable": r["n_pairable"],
                "n_cases": r["n_cases"],
                "assessed": bool(r["assessed"]),
                "reason": r.get("reason"),
                "ci_low": ci.get("low"),
                "ci_high": ci.get("high"),
                "ci_resampled": ci.get("resampled", "units"),
            }
    return None


def measure_and_project(
    conn: psycopg.Connection[Any],
    brief: Brief,
    pilot: Mapping[str, Any],
    cases: Sequence[PilotCase],
    rows: Sequence[fstore.CodingRow],
    *,
    synthesis_model: str,
    batch: bool,
    backend: str,
    month_spent_usd: float,
    month_cap_usd: float,
    commit: str | None,
    superseded_before: datetime = ADR087_AT,
    store: bool = True,
) -> tuple[CaseCostModel | None, dict[str, Any]]:
    """The measured per-case model (`case-cost-v3`: per call, current settings only; ADR-086
    addendum 3) and the full-brief projection, stored when `store` (never from zero cost)."""
    from pigtail.llm.batch import PgCostLedger
    from pigtail.llm.pricing import PRICES_AS_OF

    assert brief.version is not None
    rid = rid_of(pilot)
    ok = _fully_coded(rows)
    coded = [c for c in cases if c.case_key in ok]
    gh = {c.coding_id: dict(c.evidence_stats.get("requests") or {}) for c in cases}
    settings = current_settings(backend=backend, superseded_before=superseded_before)
    by_case, counts = model_rows(conn, rid, settings)
    if not (coded and measurable(by_case)):  # never a model or projection from zero cost
        proj = _no_projection("no case coded by both passes at a measured cost: no cost model")
        proj["ledger_rows"] = counts
        return None, proj
    model = measured(
        by_case, {c.coding_id: gh[c.coding_id] for c in coded}, len(coded), rows=counts
    )
    full = full_brief_cases(conn, pilot["selection_id"])
    proj = projection(
        model,
        extraction_model=pilot["models"]["coder_a"],
        synthesis_model=synthesis_model,
        full_cases=full["total"],
        coded_cases=len(coded),
        brief_spent_usd=PgCostLedger(conn).brief_total(brief.brief_id),
        cap_usd=brief.budget.money_usd,
        month_spent_usd=month_spent_usd,
        month_cap_usd=month_cap_usd,
        batch=batch,
    )
    proj["full_brief_cases_detail"] = full
    if store:
        fstore.save_cost_model(
            conn,
            brief_id=brief.brief_id,
            brief_version=brief.version,
            brief_run_id=rid,
            model_version=model.model_version,
            n_cases=len(coded),
            per_case=model.to_dict(),
            projection=proj,
            h6=bool(proj["h6"]),
            prices_as_of=PRICES_AS_OF,
            code_commit=commit,
        )
    return model, proj


def rebuild_cost_model(
    conn: psycopg.Connection[Any],
    brief: Brief,
    brief_run_id: str,
    *,
    synthesis_model: str,
    batch: bool,
    backend: str,
    month_spent_usd: float,
    month_cap_usd: float,
    commit: str | None,
    at: datetime,
    superseded_before: datetime = ADR087_AT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-store an existing pilot's measured cost model from its ledger rows under the current
    rules (`pigtail brief pilot-cost --rebuild`; ADR-086 addendum 3). Nothing is called or
    fetched. The pilot's summary gets the new projection; the one it replaces is kept in
    `summary.cost_model_history`. Returns the model, the projection and the rows used."""
    pilot = fstore.pilot_row(conn, brief_run_id)
    if pilot is None:
        raise ValueError(f"no pilot run {brief_run_id}")
    cases = fstore.load_cases(conn, brief_run_id)
    rows = _load_rows(conn, brief_run_id, cases)
    model, proj = measure_and_project(
        conn,
        brief,
        pilot,
        cases,
        rows,
        synthesis_model=synthesis_model,
        batch=batch,
        backend=backend,
        month_spent_usd=month_spent_usd,
        month_cap_usd=month_cap_usd,
        commit=commit,
        superseded_before=superseded_before,
        store=not dry_run,
    )
    by_case = ledger_by_case(conn, brief_run_id)
    gh = {c.coding_id: dict(c.evidence_stats.get("requests") or {}) for c in cases}
    cost = _cost_report(cases, by_case, gh, model, model_per_case=proj.get("per_case_usd"))
    out = {
        "brief_run_id": brief_run_id,
        "cost_model": None if model is None else model.to_dict(),
        "projection": proj,
        "cost": {k: v for k, v in cost.items() if k != "per_case"},
        "stored": model is not None and not dry_run,
        "dry_run": dry_run,
    }
    summary = dict(pilot.get("summary") or {})
    if not dry_run and summary.get("reliability"):
        # the pooled C11a statistic for the STATUS wording (summaries written before it)
        rs = dict(summary["reliability"])
        rs["pooled_patterns"] = pooled_patterns(fstore.reliability(conn, brief_run_id))
        fstore.update_summary(conn, brief_run_id, reliability=rs)
    if model is not None and not dry_run:
        old = summary.get("projection") or {}
        history = list(summary.get("cost_model_history") or [])
        old_model = (summary.get("cost") or {}).get("cost_model")
        history.append(
            {
                "replaced_at": at.isoformat(),
                "by_commit": commit,
                "previous_cost_model_version": (
                    old_model.get("model_version") if isinstance(old_model, dict) else None
                ),
                "previous_projected_total_usd": old.get("projected_total_usd"),
                "previous_per_case_usd": (old.get("per_case_usd") or {}).get("total"),
            }
        )
        fstore.update_summary(
            conn,
            brief_run_id,
            projection=proj,
            cost=out["cost"],
            adjudication_share=round(model.adjudication_share, 4),
            cost_model_history=history,
        )
    return out
