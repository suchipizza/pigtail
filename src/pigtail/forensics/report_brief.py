"""`pigtail report brief <id>`: a brief's private D2 report in the owner's format (M24;
ADR-088 item 4, ADR-089; DELIVERABLES D2; PRD §5.2, F20, R18.6; ADR-073.1).

Written to `PIGTAIL_DATA_DIR/reports/<brief>/v<version>/report-<date>.{json,md}` (0600, never in
git), in this order:

(a) **one narrative per case**: every exemplar, then the ten best-ranked headline pairs of view A
    (the winner and its nearest headline matched loser; ADR-088 Interpretations): what they
    posted, where, when, the title, the assets at launch, the amplifiers and the star
    trajectory. The narrative is a **synthesis LLM job** (`report`, through `LLMClient`, batch;
    the thinking setting sent, `LLM_THINKING_SYNTHESIS`, is recorded) that may only phrase the
    case's fact sheet: every sentence must cite evidence ids of that case that resolve to a
    present snapshot, carry no number absent from the fact sheet and no handle; a sentence that
    fails is dropped and counted (`narrative-check-v1`), so 100 % of the kept claims resolve.
    The deterministic fact sheet follows each narrative.
(b) **one comparison table** across those cases (deterministic);
(c) **winner-vs-loser patterns** per view with n on both sides, the contrast, counterexamples,
    the language check, per-field alpha, transferability labels for the distribution examples,
    absolute numbers per class and, when present, the downloads as a secondary exploratory
    outcome (ADR-090; `pigtail.forensics.patterns`, deterministic);
(d) placeholders for the D3 plan (M25) and the fast-path verdict (ADR-088.6).

The header states the brief version, the data version (the selection's), the code commit, the
frame version, the models, the thinking setting and the cost (the brief's API spend to date, the
coding run's and the report run's). The label is "attention-based" unless download data exists.

The report run is a `brief_runs` row of kind `report` (its narrative batch and ledger rows key on
it; a pending batch is collected by the next invocation, never resubmitted). The estimate is shown
first; paid steps need `--approve-paid`; `BudgetGuard` and `--max-usd` stop before a batch above
the caps. `--final` marks the brief version's report final (R19.9) and then purges the
unreferenced cache (R19.10, logged).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel

from pigtail.briefs.model import Brief
from pigtail.forensics import store as fstore
from pigtail.forensics.store import PilotCase
from pigtail.llm import BatchPending, LLMClient
from pigtail.llm.client import BatchItem
from pigtail.llm.types import PromptSpec

REPORT_VERSION = "brief-report-v1"
CHECK_VERSION = "narrative-check-v1"
JOB_REPORT = "report"
TOP_PAIRS = 10
MAX_SENTENCES = 14
# planning tokens per narrative (input incl. the cached prefix, prefix, output incl. thinking)
PLAN_NARRATIVE = (5_000, 1_500, 1_500)
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_APPROVAL, EXIT_BUDGET, EXIT_WAITING = 0, 1, 2, 3, 4, 5
PYPI_ATTRIBUTION = "PyPI / Linehaul (PSF), CC BY 4.0, via pypistats.org"


class Sentence(BaseModel):
    text: str
    evidence_ids: list[str]


class Narrative(BaseModel):
    sentences: list[Sentence]


SYSTEM = (
    "You write short, factual case narratives for a private research report about how "
    "open-source projects were launched. You only restate the facts you are given; you never "
    "add, infer, compute or round a number, and you never name or quote a person or an account. "
    "Output only the JSON object."
)
CONTEXT = f"""# Case narrative ({REPORT_VERSION})

The input is one case's fact sheet (JSON): its role in the comparison, its launch events (where,
when, title), its assets at launch, its amplifiers by role and bucket, and its star trajectory
around its events with the bursts and what explains them. Every fact carries `evidence_ids`.

Write at most {MAX_SENTENCES} sentences, in this order: what the project posted, where and when
(with the title when given), which assets it had at launch, who amplified it (roles and buckets
only), and how its stars moved around those events. Rules:
1. Every sentence lists in `evidence_ids` the ids of the facts it restates, copied from the
   fact sheet; a sentence without a valid id is discarded.
2. Use only numbers, dates and titles that appear in the fact sheet, copied exactly. Do not
   compute differences, sums, percentages or ratios.
3. Say "unknown" (or leave the point out) where the fact sheet says unknown; never guess.
4. Never name, quote or describe a person, handle or account; roles and buckets only.
5. No advice, no evaluation, no comparison with other cases.
"""
TEMPLATE = "{input}\n\nReturn `sentences`: each with `text` and `evidence_ids`."
NARRATIVE = PromptSpec("case-narrative", "1", SYSTEM, TEMPLATE, CONTEXT)


# --- which cases get a narrative -----------------------------------------------------------------
def narrative_cases(
    cases: Sequence[PilotCase], sel_rows: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[tuple[PilotCase, str]]:
    """Every exemplar (one per repo, view A first), then the `TOP_PAIRS` best-ranked headline
    pairs of view A: the winner and its nearest headline matched loser. (case, label)."""
    out: list[tuple[PilotCase, str]] = []
    seen: set[str] = set()
    order = {"follow_through": 0, "launch": 1, "launch_undeclared": 2}
    for c in sorted(
        (c for c in cases if c.role == "exemplar"),
        key=lambda c: (order.get(c.view, 9), c.position),
    ):
        if c.repo_full_name in seen:
            continue
        seen.add(c.repo_full_name)
        out.append((c, f"exemplar (view {_vl(c.view)}, pair {c.pair_id})"))
    pairs: dict[int, dict[str, list[PilotCase]]] = {}
    for c in cases:
        if c.view != "follow_through" or c.pair_id is None:
            continue
        row = sel_rows.get((c.view, c.candidate_ref)) or {}
        if row.get("pair_panel", "field") != "field":
            continue
        slot = pairs.setdefault(int(c.pair_id), {"w": [], "l": []})
        if c.role == "winner":
            slot["w"].append(c)
        elif c.role == "matched_loser" and row.get("headline"):
            slot["l"].append(c)

    def dist(c: PilotCase) -> tuple[float, str]:
        d = ((sel_rows.get((c.view, c.candidate_ref)) or {}).get("detail") or {}).get("pair") or {}
        v = d.get("distance")
        return (float(v) if v is not None else float("inf"), c.candidate_ref)

    ranked = []
    for pid, slot in pairs.items():
        if not slot["w"] or not slot["l"]:
            continue
        w = slot["w"][0]
        rank = (sel_rows.get((w.view, w.candidate_ref)) or {}).get("rank")
        ranked.append((rank if rank is not None else 10**9, pid, w, sorted(slot["l"], key=dist)[0]))
    ranked.sort(key=lambda t: (t[0], t[1]))
    for rank, pid, w, lo in ranked[:TOP_PAIRS]:
        out.append((w, f"view A headline winner, rank {rank} (pair {pid})"))
        out.append((lo, f"view A headline matched loser (pair {pid})"))
    return out


def _vl(view: str) -> str:
    return {"follow_through": "A", "launch": "B", "launch_undeclared": "B-undeclared"}.get(
        view, view
    )


# --- the fact sheet (the narrative's only input) -------------------------------------------------
def fact_sheet(c: PilotCase, label: str) -> dict[str, Any]:
    f = c.facts or {}
    tr = f.get("trajectory") or {}
    tev = tr.get("evidence_id")
    per = {p["event"]: p for p in tr.get("per_event") or []}
    events = []
    for e in f.get("events") or []:
        if not e.get("launch") and e.get("kind") != "first_mention":
            continue
        key = f"{e['kind']}:{e['ref']}"
        rec: dict[str, Any] = {
            "where": e["where"],
            "when": e.get("at"),
            "title": e.get("title") or f"unknown ({e.get('title_missing') or 'not stored'})",
            "evidence_ids": sorted(
                set(e.get("evidence_ids") or [])
                | ({e["title_evidence_id"]} if e.get("title_evidence_id") else set())
            ),
        }
        p = per.get(key)
        if p is not None and tev:
            rec["stars_gained_after"] = {
                k: (v.get("value") if v.get("value") is not None else v.get("reason"))
                for k, v in (p.get("gained") or {}).items()
            }
            before = (p.get("stars_before") or {}).get("value")
            rec["stars_before"] = before if before is not None else "unknown"
            rec["evidence_ids"] = sorted({*rec["evidence_ids"], tev})
        events.append(rec)
    assets = []
    for name, a in ((f.get("assets") or {}).get("assets") or {}).items():
        assets.append(
            {
                "asset": name,
                "value": a.get("value"),
                **({"excerpt": a["excerpt"]} if a.get("excerpt") else {}),
                "evidence_ids": [a["evidence_id"]] if a.get("evidence_id") else [],
            }
        )
    rs = (f.get("assets") or {}).get("readme_structure")
    readme = None
    if rs:
        readme = {
            "headings": [h["text"] for h in rs.get("headings") or []][:15],
            "quick_start_section": rs.get("quick_start_section"),
            "features_section": rs.get("features_section"),
            "evidence_ids": [rs["evidence_id"]],
        }
    amps = [
        {
            "role": a["role"],
            "value": a["value"],
            **({"venues": a["venues"]} if a.get("venues") else {}),
            **({"reason": a["reason"]} if a.get("reason") else {}),
            "evidence_ids": list(a.get("evidence_ids") or []),
        }
        for a in f.get("amplifiers") or []
    ]
    bursts = sorted(tr.get("bursts") or [], key=lambda b: -(b.get("stars_48h") or 0))[:5]
    return {
        "case": c.coding_id,
        "role": label,
        "launch_events": events,
        "assets_at_launch": assets,
        "readme_at_launch": readme,
        "amplifiers": amps,
        "largest_bursts": [
            {
                "onset_day": b["onset_day"],
                "stars_in_48h": b["stars_48h"],
                "explained_by": b["explained_by"],
                "label": b.get("label"),
                "evidence_ids": [tev] if tev else [],
            }
            for b in bursts
        ],
        "star_label": tr.get("label"),
    }


def sheet_ids(sheet: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(sheet, dict):
        for k, v in sheet.items():
            if k == "evidence_ids":
                out |= {str(x) for x in v or []}
            else:
                out |= sheet_ids(v)
    elif isinstance(sheet, list):
        for v in sheet:
            out |= sheet_ids(v)
    return out


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z0-9_-]{2,}")


def _nums(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(text)}


def check_sentences(
    sentences: Sequence[Sentence], resolvable: set[str], sheet_text: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """`narrative-check-v1`: keep a sentence only when it cites at least one evidence id, every
    cited id resolves to a present snapshot of this case, every number in it appears in the fact
    sheet, and it names no handle. Returns (kept sentences, dropped counts by reason)."""
    allowed_nums = _nums(sheet_text)
    kept: list[dict[str, Any]] = []
    dropped: dict[str, int] = {}

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    for s in list(sentences)[:MAX_SENTENCES]:
        ids = [i.strip() for i in s.evidence_ids if i.strip()]
        text = " ".join(s.text.split())
        if not text:
            drop("empty")
        elif not ids:
            drop("no_evidence_id")
        elif any(i not in resolvable for i in ids):
            drop("evidence_id_not_resolvable")
        elif not _nums(text) <= allowed_nums:
            drop("number_not_in_facts")
        elif _HANDLE.search(text):
            drop("handle")
        else:
            kept.append({"text": text, "evidence_ids": sorted(set(ids))})
    return kept, dropped


def resolvable_ids(
    conn: psycopg.Connection[Any], snaps: Any, ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Evidence ids with a present row whose snapshot exists: id -> {source, url, hash, at}."""
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT id, source, url, content_hash, fetched_at FROM evidence WHERE id = ANY(%s)"
        " AND deletion_state = 'present'",
        (sorted(ids),),
    ).fetchall()
    out = {}
    for eid, src, url, h, at in rows:
        if snaps.exists(h):
            out[str(eid)] = {"source": src, "url": url, "content_hash": h, "fetched_at": at}
    return out


# --- comparison table ----------------------------------------------------------------------
def comparison_row(c: PilotCase, label: str) -> dict[str, Any]:
    f = c.facts or {}
    launch = [e for e in f.get("events") or [] if e.get("launch") and e.get("at")]
    first = launch[0] if launch else None
    tr = f.get("trajectory") or {}
    per = {p["event"]: p for p in tr.get("per_event") or []}
    gains: dict[str, Any] = {}
    if first is not None:
        p = per.get(f"{first['kind']}:{first['ref']}") or {}
        gains = {k: v.get("value") for k, v in (p.get("gained") or {}).items()}
    assets = [
        a
        for a, v in ((f.get("assets") or {}).get("assets") or {}).items()
        if v.get("value") == "present"
    ]
    amps = [a["role"] for a in f.get("amplifiers") or [] if a["value"] in ("present", "claimed")]
    bursts = tr.get("bursts") or []
    return {
        "case": c.repo_full_name,
        "view": _vl(c.view),
        "role": label,
        "first_launch": None
        if first is None
        else {"where": first["where"], "when": first["at"], "title": first.get("title")},
        "launch_events": len(launch),
        "assets": assets,
        "amplifiers": amps,
        "stars_after_first_launch": gains,
        "bursts": len(bursts),
        "bursts_explained": sum(1 for b in bursts if b.get("explained_by") != "unexplained"),
        "evidence_ids": sorted(set((f.get("evidence") or {}).values())),
    }


# --- running -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReportOptions:
    approve_paid: bool = False
    dry_run: bool = False
    max_usd: float | None = None
    run_id: str | None = None  # the coding run (default: the latest succeeded one)
    wait_seconds: float | None = None
    poll_seconds: float = 60.0
    final: bool = False


@dataclass
class ReportDeps:
    conn: psycopg.Connection[Any]
    client: LLMClient
    snapshots: Any
    data_dir: Path
    month_cap_usd: float = 200.0
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], None] | None = None
    code_commit: str | None = None
    run_record_id: str | None = None


@dataclass
class ReportOutcome:
    status: str
    exit_code: int
    message: str
    estimate: dict[str, Any] | None = None
    report_paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def coding_run(
    conn: psycopg.Connection[Any], brief: Brief, run_id: str | None
) -> dict[str, Any] | None:
    if run_id is not None:
        row = fstore.pilot_row(conn, run_id)
        return row if row is not None and row["brief_id"] == brief.brief_id else None
    r = conn.execute(
        "SELECT p.brief_run_id FROM brief_pilot p JOIN brief_runs r ON r.id = p.brief_run_id"
        " WHERE r.kind = 'coding' AND r.status = 'succeeded' AND p.brief_id = %s"
        " AND p.brief_version = %s ORDER BY p.created_at DESC LIMIT 1",
        (brief.brief_id, brief.version),
    ).fetchone()
    return fstore.pilot_row(conn, str(r[0])) if r else None


def narrative_estimate(client: LLMClient, n: int) -> dict[str, Any]:
    from pigtail.forensics.cost import CONTINGENCY_FACTOR
    from pigtail.llm.pricing import TokenUsage, cost_usd

    model = client.model_for(JOB_REPORT)
    batch = client.batches_for(JOB_REPORT)
    tin, prefix, out = PLAN_NARRATIVE
    per = cost_usd(
        model, TokenUsage(input=tin - prefix, output=out, cache_read=prefix), batch=batch
    )
    api = client.backend_for(JOB_REPORT).name == "api"
    total = None if per is None else per * n
    return {
        "label": "estimate",
        "narratives": n,
        "model": model,
        "mode": "batch" if batch and api else "standard",
        "planning_tokens": {"input": tin, "cached_prefix": prefix, "output": out},
        "per_narrative_usd": None if per is None else round(per, 4),
        "total_usd": None if total is None or not api else round(total, 4),
        "total_with_contingency_usd": None
        if total is None or not api
        else round(total * CONTINGENCY_FACTOR, 4),
        "requires_approval": api and n > 0,
    }


def _report_run(conn: psycopg.Connection[Any], brief: Brief) -> str | None:
    r = conn.execute(
        "SELECT id FROM brief_runs WHERE kind = 'report' AND brief_id = %s AND brief_version = %s"
        " AND status IN ('planned', 'running', 'waiting_batch', 'paused_budget', 'failed')"
        " ORDER BY created_at DESC LIMIT 1",
        (brief.brief_id, brief.version),
    ).fetchone()
    return str(r[0]) if r else None


def _set(conn: psycopg.Connection[Any], rid: str, status: str, **cols: Any) -> None:
    from psycopg.types.json import Jsonb

    sets: list[str] = ["status = %s"]
    vals: list[Any] = [status]
    if status == "running":
        sets.append("started_at = COALESCE(started_at, now())")
    if status in ("succeeded", "failed"):
        sets.append("finished_at = now()")
    for k, v in cols.items():
        sets.append(f"{k} = %s")
        vals.append(Jsonb(v) if isinstance(v, dict | list) else v)
    conn.execute(f"UPDATE brief_runs SET {', '.join(sets)} WHERE id = %s", (*vals, rid))


def run_report(brief: Brief, deps: ReportDeps, opts: ReportOptions) -> ReportOutcome:
    """Plan, estimate, check approval and caps, then write the report (module docstring)."""
    from pigtail.briefs.budget import BudgetGuard, BudgetStop
    from pigtail.briefs.cache import BriefRuns
    from pigtail.briefs.selection_store import cases as sel_cases
    from pigtail.capture.runs import git_commit
    from pigtail.forensics.pilot import _check_run_cap, run_spend
    from pigtail.llm.batch import PgCostLedger
    from pigtail.llm.thinking import label, mode_for

    conn = deps.conn
    assert brief.version is not None
    run = coding_run(conn, brief, opts.run_id)
    if run is None:
        return ReportOutcome(
            "refused",
            EXIT_FAILED,
            "no finished coding run of this brief version: run `pigtail brief code` first",
        )
    rid = str(run["brief_run_id"])
    cases = fstore.load_cases(conn, rid)
    missing = [c.case_key for c in cases if c.facts is None]
    if missing:
        return ReportOutcome(
            "refused",
            EXIT_FAILED,
            f"{len(missing)} case(s) have no report "
            "facts: run `pigtail brief code` again to finish them",
        )
    sel_rows = {(r["view"], r["candidate_ref"]): r for r in sel_cases(conn, run["selection_id"])}
    chosen = narrative_cases(cases, sel_rows)
    est = narrative_estimate(deps.client, len(chosen))
    est["max_usd"] = opts.max_usd
    if opts.dry_run:
        from pigtail.capture.db import CaptureDB
        from pigtail.privacy.cache_purge import purge_unreferenced, report_refs

        preview = purge_unreferenced(
            CaptureDB(conn), deps.snapshots, apply=False, extra_refs=report_refs(deps.data_dir)
        )
        return ReportOutcome(
            "dry_run",
            EXIT_OK,
            "DRY RUN: nothing was started, called or written",
            estimate=est,
            summary={
                "coding_run": rid,
                "narratives": [lab for _c, lab in chosen],
                "cache_purge_preview": preview.to_dict(),  # what --final would purge (R19.10)
            },
        )
    commit = deps.code_commit if deps.code_commit is not None else git_commit()
    runs = BriefRuns(conn)
    report_rid = _report_run(conn, brief)
    prior = runs.get(report_rid) if report_rid else None
    approved = opts.approve_paid or bool(prior and prior.get("approved_paid"))
    if est["requires_approval"] and not approved:
        return ReportOutcome(
            "needs_approval",
            EXIT_APPROVAL,
            "the narratives are paid LLM calls "
            "(api backend); nothing was started: approve the estimate with "
            "--approve-paid",
            estimate=est,
        )
    guard = BudgetGuard(
        brief.budget,
        deps.client.store,
        month_cap_usd=deps.month_cap_usd,
        approved_paid=approved,
        clock=deps.clock,
        brief_ledger=lambda: PgCostLedger(conn).brief_total(brief.brief_id),
        month_ledger=lambda since: PgCostLedger(conn).month_total(since),
    )
    synth = deps.client.model_for(JOB_REPORT)
    thinking = label(mode_for(JOB_REPORT, deps.client.thinking_synthesis), synth)
    if report_rid is None:
        try:
            guard.check_llm("report estimate", est_usd=est["total_with_contingency_usd"])
            _check_run_cap(opts.max_usd, 0.0, est["total_with_contingency_usd"], "report")
        except BudgetStop as e:
            return ReportOutcome("refused_budget", EXIT_BUDGET, str(e), estimate=est)
        report_rid = runs.create(
            brief,
            data_version=run.get("data_version"),
            estimate=est,
            approved_paid=approved,
            kind="report",
            code_commit=commit,
            prompt_versions={
                "narrative": f"{NARRATIVE.id}@{NARRATIVE.version}#{NARRATIVE.fingerprint}"
                f"+thinking:{thinking}"
            },
            model_versions={"synthesis": synth},
            run_id=deps.run_record_id,
        ).id
    _set(conn, report_rid, "running")
    # (a) narratives
    sheets = {c.coding_id: fact_sheet(c, lab) for c, lab in chosen}
    texts = {
        k: json.dumps(v, sort_keys=True, ensure_ascii=False, indent=1) for k, v in sheets.items()
    }
    items = [BatchItem(ref=k, input_text=texts[k], case_ref=k) for k in sorted(sheets)]
    paid = deps.client.backend_for(JOB_REPORT).name == "api"
    rr = report_rid

    def before(job: str, requests: int, est_usd: float | None) -> None:
        guard.before_submit(job, requests, est_usd)
        _check_run_cap(opts.max_usd, run_spend(conn, rr), est_usd, f"{job} batch")

    kw: dict[str, Any] = {} if deps.sleep is None else {"sleep": deps.sleep}
    try:
        br = deps.client.run_batch(
            NARRATIVE,
            items,
            Narrative,
            job=JOB_REPORT,
            brief_run_id=report_rid,
            before_submit=before if paid else None,
            est_usd_per_item=est["per_narrative_usd"],
            timeout_seconds=opts.wait_seconds,
            poll_seconds=opts.poll_seconds,
            **kw,
        )
    except BatchPending as e:
        _set(conn, report_rid, "waiting_batch")
        return ReportOutcome(
            "waiting_batch",
            EXIT_WAITING,
            f"{len(e.batch_ids)} Message "
            "Batch(es) still running; run the same command again to collect "
            "them (never resubmitted)",
            estimate=est,
        )
    except BudgetStop as e:
        _set(conn, report_rid, "paused_budget", stop=e.to_dict())
        return ReportOutcome("paused_budget", EXIT_BUDGET, str(e), estimate=est)
    all_ids: set[str] = set()
    for s in sheets.values():
        all_ids |= sheet_ids(s)
    resolved = resolvable_ids(conn, deps.snapshots, all_ids)
    narratives: dict[str, Any] = {}
    for c, _lab in chosen:
        res = br.results.get(c.coding_id)
        ok_ids = sheet_ids(sheets[c.coding_id]) & set(resolved)
        if res is None:
            err = (br.errors.get(c.coding_id) or {}).get("type") or br.failed.get(c.coding_id)
            narratives[c.coding_id] = {"sentences": [], "dropped": {}, "failed": str(err)}
            continue
        kept, dropped = check_sentences(res.output.sentences, ok_ids, texts[c.coding_id])
        narratives[c.coding_id] = {
            "sentences": kept,
            "dropped": dropped,
            "provenance": res.provenance(),
        }
    # (b)-(c) deterministic parts
    report = build_report(
        conn,
        brief,
        run,
        cases,
        chosen,
        sheets,
        narratives,
        resolved,
        sel_rows,
        report_rid=report_rid,
        commit=commit,
        thinking=thinking,
        synth=synth,
        now=deps.clock(),
    )
    from pigtail.forensics.report import write_report

    paths = write_report(
        deps.data_dir,
        brief.brief_id,
        brief.version,
        "report",
        report,
        report_markdown(report),
        day=deps.clock().date(),
    )
    summary = {
        "coding_run": rid,
        "report_run": report_rid,
        "narratives": len(chosen),
        "sentences_kept": sum(len(n["sentences"]) for n in narratives.values()),
        "sentences_dropped": sum(sum(n["dropped"].values()) for n in narratives.values()),
        "narratives_failed": sum(1 for n in narratives.values() if n.get("failed")),
        "claims_resolving": "100%",
        "label": report["provenance"]["label"],
    }
    if opts.final:
        summary["final"] = finalize(conn, deps, brief, report_rid, paths)
    _set(conn, report_rid, "succeeded", spend={"api_usd": round(run_spend(conn, report_rid), 6)})
    return ReportOutcome(
        "succeeded",
        EXIT_OK,
        f"report written ({len(chosen)} narratives)",
        estimate=est,
        report_paths=paths,
        summary=summary,
    )


def finalize(
    conn: psycopg.Connection[Any],
    deps: ReportDeps,
    brief: Brief,
    report_rid: str,
    paths: Mapping[str, str],
) -> dict[str, Any]:
    """Mark the report final (R19.9 anchor) and purge the unreferenced cache (R19.10, logged)."""
    from pigtail.capture.db import CaptureDB
    from pigtail.privacy.cache_purge import purge_unreferenced, report_refs
    from pigtail.privacy.snapshot_retention import mark_report_final

    assert brief.version is not None
    db = CaptureDB(conn)
    mark_report_final(
        db,
        brief.brief_id,
        brief.version,
        at=deps.clock(),
        brief_run_id=report_rid,
        run_id=deps.run_record_id,
    )
    # the report just written sits under data_dir, so its citations are kept too (M24-T7)
    res = purge_unreferenced(
        db,
        deps.snapshots,
        apply=True,
        run_id=deps.run_record_id,
        extra_refs=report_refs(deps.data_dir),
    )
    out = {"report_final": True, "cache_purge": res.to_dict(), "report": dict(paths)}
    return out


def build_report(
    conn: psycopg.Connection[Any],
    brief: Brief,
    run: Mapping[str, Any],
    cases: Sequence[PilotCase],
    chosen: Sequence[tuple[PilotCase, str]],
    sheets: Mapping[str, Any],
    narratives: Mapping[str, Any],
    resolved: Mapping[str, Mapping[str, Any]],
    sel_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    report_rid: str,
    commit: str | None,
    thinking: str,
    synth: str,
    now: datetime,
) -> dict[str, Any]:
    from pigtail.briefs.downloads import secondary_outcomes
    from pigtail.forensics.patterns import run_patterns
    from pigtail.forensics.pilot import run_spend
    from pigtail.llm.batch import PgCostLedger

    rid = str(run["brief_run_id"])
    finals: dict[str, dict[str, str]] = {}
    for r in fstore.codings(conn, rid, "final"):
        finals.setdefault(r["case_key"], {})[r["unit"]] = r["value"]
    rel = fstore.reliability(conn, rid)
    try:
        secondary = secondary_outcomes(conn, run["selection_id"])
    except psycopg.Error:
        secondary = {}
    pats = run_patterns(cases, finals, sel_rows, rel, secondary)
    reused = sorted({c.reused_from for c in cases if c.reused_from})
    reused_usd = sum(run_spend(conn, r) for r in reused)
    return {
        "provenance": {
            "report_version": REPORT_VERSION,
            "brief_id": brief.brief_id,
            "brief_version": brief.version,
            "brief_hash": brief.content_hash(),
            "selection_id": run["selection_id"],
            "data_version": run.get("data_version"),
            "code_commit": commit,
            "coding_run": rid,
            "coding_run_commit": run.get("code_commit"),
            "report_run": report_rid,
            "frame_version": run["frame_version"],
            "codebook_version": run["codebook_version"],
            "prompt_fingerprints": run["prompt_fingerprints"],
            "models": {**dict(run["models"]), "narrative": synth},
            "narrative_prompt": f"{NARRATIVE.id}@{NARRATIVE.version}#{NARRATIVE.fingerprint}",
            "narrative_thinking": thinking,
            "narrative_check": CHECK_VERSION,
            "patterns_version": pats["version"],
            "date": now.date().isoformat(),
            "label": pats["outcome_label"],
            "coding_label": "LLM-coded, not human-validated",
            "star_label": "unfiltered, anomaly-checked; day-level attribution",
            "cost_usd": {
                "brief_total_to_date": round(PgCostLedger(conn).brief_total(brief.brief_id), 4),
                "coding_run": round(run_spend(conn, rid), 4),
                "reused_pilot_runs": round(reused_usd, 4),
                "report_run": round(run_spend(conn, report_rid), 4),
            },
        },
        "narratives": [
            {
                "coding_id": c.coding_id,
                "case": c.repo_full_name,
                "label": lab,
                "narrative": narratives.get(c.coding_id),
                "facts": sheets[c.coding_id],
            }
            for c, lab in chosen
        ],
        "comparison": [comparison_row(c, lab) for c, lab in chosen],
        "patterns": {k: v for k, v in pats.items() if k != "case_features"},
        "reliability": rel,
        "evidence_index": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in resolved.items()},
        "placeholders": {
            "d3_plan": "M25: the D3 plan is written from this report (not yet).",
            "fast_path_verdict": "ADR-088.6: the verifier's comparison with the fast path "
            "(not yet).",
        },
        "limitations": LIMITATIONS,
    }


LIMITATIONS = [
    "Project-level evidence only (ADR-073.2): HN comments and mentions, Bluesky post texts and "
    "per-repo event actors are held; amplification by accounts (by follower bucket) is unknown.",
    "HN front page: Algolia's front_page tag only; its absence is unknown, not absent.",
    "Assets are detected by rules on the README at T and the release notes (assets-v1); images "
    "embedded as HTML without a file extension, demos on the homepage only, and assets added "
    "after T are not seen.",
    "Stars before an event are unknown when the star history doesn't reach the creation day.",
    "Patterns are associations between winners and matched losers in one neighbourhood, not "
    "causes; star trajectories are the outcome itself and are never a pattern feature.",
    "LLM-coded, not human-validated (no H3 calibration sample). Narratives only restate the "
    "fact sheet; sentences that could not be checked were dropped.",
]


# --- markdown ------------------------------------------------------------------------------
def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def report_markdown(r: Mapping[str, Any]) -> str:
    p = r["provenance"]
    cost = p["cost_usd"]
    out = [
        f"# Neighbourhood report — brief {p['brief_id']} v{p['brief_version']} ({p['date']})",
        "",
        "Private (ADR-073.1): never commit, publish or share this file.",
        "",
        f"**{p['label']}** · {p['coding_label']} · stars: {p['star_label']}",
        "",
        "| Provenance | |",
        "|---|---|",
        f"| brief version | v{p['brief_version']} (hash {p['brief_hash'][:12]}) |",
        f"| data version | {p['data_version']} (selection {p['selection_id']}) |",
        f"| code commit | {p['code_commit']} (coding run at {p['coding_run_commit']}) |",
        f"| frame version | {p['frame_version']} (codebook {p['codebook_version']}) |",
        f"| models | {_cell(p['models'])} |",
        f"| narrative prompt | {p['narrative_prompt']}, thinking {p['narrative_thinking']}, "
        f"check {p['narrative_check']} |",
        f"| cost (API, USD) | brief to date {cost['brief_total_to_date']}; coding run "
        f"{cost['coding_run']}; reused pilot {cost['reused_pilot_runs']}; this report "
        f"{cost['report_run']} |",
        "",
        "## (a) Case narratives",
        "",
    ]
    for n in r["narratives"]:
        out += [f"### {n['case']} — {n['label']}", ""]
        nar = n["narrative"] or {}
        if nar.get("failed"):
            out.append(f"_Narrative unavailable ({nar['failed']}); the facts below stand._")
        for s in nar.get("sentences") or []:
            out.append(f"{s['text']} [{', '.join(s['evidence_ids'])}]")
        if nar.get("dropped"):
            out.append(f"_Sentences dropped by the check: {_cell(nar['dropped'])}_")
        f = n["facts"]
        out += ["", "Facts:", ""]
        for e in f["launch_events"]:
            gain = e.get("stars_gained_after")
            out.append(
                f"- {e['where']} — {e['when']} — “{e['title']}”"
                + (f" — stars before {e.get('stars_before')}, gained {gain}" if gain else "")
                + f" [{', '.join(e['evidence_ids'])}]"
            )
        present = [a for a in f["assets_at_launch"] if a["value"] == "present"]
        out.append(
            "- assets at launch: "
            + (
                ", ".join(f"{a['asset']} (“{a.get('excerpt', '')}”)" for a in present)
                or "none detected"
            )
            + " ["
            + ", ".join(sorted({i for a in f["assets_at_launch"] for i in a["evidence_ids"]}))
            + "]"
        )
        out.append(
            "- amplifiers: " + "; ".join(f"{a['role']}: {a['value']}" for a in f["amplifiers"])
        )
        for b in f["largest_bursts"]:
            out.append(
                f"- burst {b['onset_day']}: {b['stars_in_48h']} stars in 48 h, explained by "
                f"{b['explained_by']} ({b['label']}) [{', '.join(b['evidence_ids'])}]"
            )
        out.append("")
    out += [
        "## (b) Comparison table",
        "",
        "| case | view | role | first launch | title | assets | amplifiers | stars +1/+7/+30 d "
        "| bursts (explained) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in r["comparison"]:
        fl = c["first_launch"] or {}
        g = c["stars_after_first_launch"]
        out.append(
            f"| {_cell(c['case'])} | {c['view']} | {_cell(c['role'])} | "
            f"{_cell(fl.get('where'))} {_cell(fl.get('when'))} | {_cell(fl.get('title'))} | "
            f"{_cell(', '.join(c['assets']))} | {_cell(', '.join(c['amplifiers']))} | "
            f"{g.get('+1d')}/{g.get('+7d')}/{g.get('+30d')} | {c['bursts']} "
            f"({c['bursts_explained']}) |"
        )
    pats = r["patterns"]
    out += [
        "",
        "## (c) Winner-vs-loser patterns",
        "",
        f"Outcome dimension: attention ({pats['outcome_label']}). Minimum evidence: "
        f"{pats['min_evidence']['known_per_side']} known per side and "
        f"{pats['min_evidence']['present_total']} present in total (ADR-050.3).",
        "",
    ]
    for vl, v in pats["views"].items():
        out += [
            f"### View {vl} ({v['view']}): {v['winners']} winners, {v['matched_losers']} "
            f"headline matched losers, {v['same_language_group_pairs']} same-language-group "
            "pairs",
            "",
            "| feature | winners (present/known) | losers (present/known) | d "
            "| pairs W-only/L-only | α | language check | labels |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for f in v["features"]:
            w, lo = f["winners"], f["matched_losers"]
            rel = f["reliability"]
            a = "-" if rel.get("alpha") is None else f"{rel['alpha']:.2f}"
            out.append(
                f"| {f['feature']} | {w['n_present']}/{w['n_known']} | "
                f"{lo['n_present']}/{lo['n_known']} | {f['d']} | {f['pairs']['winner_only']}/"
                f"{f['pairs']['loser_only']} | {a} | "
                f"{(f.get('language_check') or {}).get('result', '-')} | "
                f"{_cell('; '.join(f['labels'] + rel.get('labels', [])))} |"
            )
        out += ["", "Counterexamples (sufficient-evidence features):", ""]
        for f in v["features"]:
            if not f["sufficient"]:
                continue
            ce = f["counterexamples"]
            if ce["none_found"]:
                out.append(f"- {f['feature']}: none found")
            else:
                out.append(
                    f"- {f['feature']}: winners without: {', '.join(ce['winners_without']) or '-'}"
                    f"; losers with: {', '.join(ce['losers_with']) or '-'}"
                )
        out += ["", "Absolute numbers (median [min–max], n):", ""]
        for side, nums in v["absolute_numbers"].items():
            for m, s in nums.items():
                out.append(f"- {side} {m}: {s['median']} [{s['min']}–{s['max']}], n = {s['n']}")
        sec = v.get("secondary_exploratory") or {}
        if sec:
            out += [
                "",
                "Secondary exploratory outcome: downloads (ADR-088.3, ADR-090; not "
                f"pre-registered, not used by the sort). PyPI data: {PYPI_ATTRIBUTION}.",
                "",
            ]
            for side, nums in sec.items():
                for m, s in nums.items():
                    out.append(f"- {side} {m}: {s['median']} [{s['min']}–{s['max']}], n = {s['n']}")
        out.append("")
    for vl, v in pats["distribution_examples"].items():
        out += [
            f"### Distribution examples (view {vl}): {v['exemplars']} exemplars, "
            f"{v['matched_losers']} matched losers",
            "",
            "| feature | exemplars | losers | d | transferability |",
            "|---|---|---|---|---|",
        ]
        for f in v["features"]:
            t = f["transferability"]
            out.append(
                f"| {f['feature']} | {f['winners']['n_present']}/{f['winners']['n_known']} | "
                f"{f['matched_losers']['n_present']}/{f['matched_losers']['n_known']} | {f['d']} "
                f"| {t['label']} {_cell(', '.join(t.get('conditions') or []))} |"
            )
        out.append("")
    out += [
        "## (d) D3 plan and fast-path verdict",
        "",
        f"- D3 plan: {r['placeholders']['d3_plan']}",
        f"- Fast-path verdict: {r['placeholders']['fast_path_verdict']}",
        "",
        "## Limitations",
        "",
        *[f"- {x}" for x in r["limitations"]],
        "",
        "## Evidence index",
        "",
        "| evidence id | source | content hash | captured |",
        "|---|---|---|---|",
        *[
            f"| {k} | {v.get('source')} | {str(v.get('content_hash'))[:16]} | "
            f"{str(v.get('fetched_at'))[:19]} |"
            for k, v in sorted(r["evidence_index"].items())
        ],
        "",
    ]
    return "\n".join(out)
