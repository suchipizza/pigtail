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

REPORT_VERSION = "brief-report-v2"
SENSITIVE = "definition-sensitive"
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
6. A launch event with `counts: false` is an unconfirmed title match (the story does not link
   the project): say it is unconfirmed, never present it as the project's post or launch.
7. `assets_after_launch` appeared after the launch: never describe them as present at launch.
8. Describe a burst by `stars_first_7_days` (the first 7 days from its onset) and its peak. An
   `open` burst is still above its pre-burst baseline at the series' end: say so, and never
   describe `stars_so_far` as the burst's size. Say "unexplained" when no event explains it.
"""
TEMPLATE = "{input}\n\nReturn `sentences`: each with `text` and `evidence_ids`."
NARRATIVE = PromptSpec("case-narrative", "3", SYSTEM, TEMPLATE, CONTEXT)  # 3: rule 8, open


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
        counts = e.get("counts", True)
        title = e.get("title") or f"unknown ({e.get('title_missing') or 'not stored'})"
        rec: dict[str, Any] = {
            "kind": e["kind"],
            "where": e["where"],
            "when": e.get("at"),
            "confirmed": e.get("confirmed", True),
            "counts": counts,
            "title": title
            if counts or e["kind"] == "first_mention"
            else f"{title} (unconfirmed title match: the story does not link the repo or its "
            "homepage; not counted as the project's launch)",
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
    af = f.get("assets") or {}
    assets = []
    for name, a in (af.get("assets") or {}).items():
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
    bursts = sorted(
        tr.get("bursts") or [],
        key=lambda b: -(b.get("stars_first_7d") or b.get("stars_48h") or 0),
    )[:5]
    return {
        "case": c.coding_id,
        "role": label,
        "launch_events": events,
        "assets_at_launch": assets,  # a list, as report v1 (the plan reads it)
        "assets_status": "unknown (no README at T)" if af.get("all_unknown") else "detected",
        "assets_after_launch": [
            {"asset": k, "excerpt": v.get("excerpt"), "evidence_ids": [v["evidence_id"]]}
            for k, v in (af.get("after_launch") or {}).items()
        ],
        "readme_at_launch": readme,
        "amplifiers": amps,
        "largest_bursts": [
            {
                "onset_day": b["onset_day"],
                "open": bool(b.get("open")),
                "stars_first_7_days": b.get("stars_first_7d"),
                "first_7_days_complete": b.get("first_7d_complete"),
                # a closed burst's whole size; an open one's total is only "so far"
                **(
                    {
                        "open_note": "still above the pre-burst baseline at the series' end",
                        "stars_so_far": b.get("stars_total"),
                        "days_so_far": b.get("days"),
                    }
                    if b.get("open")
                    else {
                        "days": b.get("days"),
                        "stars_total": b.get("stars_total"),
                        "stars_total_complete": b.get("stars_total_complete"),
                    }
                ),
                "peak_day": b.get("peak_day"),
                "peak_stars": b.get("peak_stars"),
                "stars_in_first_48h": b["stars_48h"],
                "explained_by": b["explained_by"],
                "explained_also": b.get("explained_also") or [],
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
def comparison_row(c: PilotCase, label: str, sensitive: bool = False) -> dict[str, Any]:
    f = c.facts or {}
    launch = [e for e in f.get("events") or [] if e.get("counts", e.get("launch")) and e.get("at")]
    first = launch[0] if launch else None
    tr = f.get("trajectory") or {}
    per = {p["event"]: p for p in tr.get("per_event") or []}
    gains: dict[str, Any] = {}
    if first is not None:
        p = per.get(f"{first['kind']}:{first['ref']}") or {}
        gains = {k: v.get("value") for k, v in (p.get("gained") or {}).items()}
    af = f.get("assets") or {}
    assets = (
        ["unknown (no README at T)"]
        if af.get("all_unknown")
        else [a for a, v in (af.get("assets") or {}).items() if v.get("value") == "present"]
    )
    amps = [a["role"] for a in f.get("amplifiers") or [] if a["value"] in ("present", "claimed")]
    bursts = tr.get("bursts") or []
    return {
        "case": c.repo_full_name,
        "view": _vl(c.view),
        "role": label,
        "definition_sensitive": sensitive,
        "unconfirmed_hn_matches": sum(1 for e in f.get("events") or [] if e.get("counts") is False),
        "first_launch": None
        if first is None
        else {"where": first["where"], "when": first["at"], "title": first.get("title")},
        "launch_events": len(launch),
        "assets": assets,
        "amplifiers": amps,
        "stars_after_first_launch": gains,
        "bursts": len(bursts),
        "bursts_explained": sum(1 for b in bursts if b.get("explained_by") != "unexplained"),
        # the largest burst by its first 7 days (an open burst's total is never its size)
        "largest_burst_stars": max((b.get("stars_first_7d") or 0 for b in bursts), default=None),
        "largest_burst_basis": "stars in the first 7 days from the onset",
        "open_bursts": sum(1 for b in bursts if b.get("open")),
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


def _flags(c: PilotCase, sel_rows: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[str]:
    row = sel_rows.get((c.view, c.candidate_ref)) or {}
    flags = list(row.get("sensitivity_flags") or [])
    sens = (row.get("detail") or {}).get("sensitivity") or {}
    flags += [f for f in sens.get("flags") or [] if f not in flags]
    return flags


def _sensitive(c: PilotCase, sel_rows: Mapping[tuple[str, str], Mapping[str, Any]]) -> bool:
    return "definition_sensitive" in _flags(c, sel_rows)


def selection_diagnostics(
    conn: psycopg.Connection[Any], brief: Brief, selection_id: str
) -> dict[str, Any]:
    """The D2 header's selection record, read from the stored selection and shortlist (no new
    spend): the success definition, the reference population, the shortlist record and the
    relevance filter's precision (R4.7), the balance diagnostics per view (SMD against the
    target; dependent contrasts where a loser serves several winners) and the sensitivity check
    (verifier M24 round 1 fix 1)."""
    from pigtail.briefs.shortlist import Shortlist

    row = conn.execute(
        "SELECT params, summary, balance, sensitivity, selection_version, as_of"
        " FROM brief_selection WHERE id = %s",
        (selection_id,),
    ).fetchone()
    _params, summary, balance, sensitivity, version, as_of = row or ({}, {}, {}, {}, None, None)
    out: dict[str, Any] = {
        "selection_version": version,
        "as_of": None if as_of is None else str(as_of),
        "success_definition": brief.success.model_dump(mode="json"),
        "summary": summary or {},
    }
    try:
        sl = Shortlist(conn, brief)
        view = sl.view()
        dec: dict[str, int] = {}
        for d in sl.latest().values():
            dec[d.decision] = dec.get(d.decision, 0) + 1
        out["shortlist"] = {
            "status": view.get("status"),
            "counts": view.get("counts"),
            "decisions": dict(sorted(dec.items())),
        }
        out["relevance_precision"] = view.get("precision")
    except Exception as e:  # a brief without a stored shortlist (e.g. a synthetic test)
        out["shortlist"] = {"status": None, "error": type(e).__name__}
        out["relevance_precision"] = None
    bal: dict[str, Any] = {}
    for vkey, b in (balance or {}).items():
        if not isinstance(b, dict):
            continue
        after = b.get("after_matching") or {}
        target = b.get("target")
        bal[vkey] = {
            "target": target,
            "covariates": {
                k: {
                    "smd": v.get("smd"),
                    "meets_target": v.get("meets_target"),
                    "label": v.get("label"),
                }
                for k, v in after.items()
                if isinstance(v, dict)
            },
            "covariates_missing_target": b.get("covariates_missing_target") or [],
            "pairs": b.get("pairs"),
            "headline_pairs": b.get("headline_pairs"),
            "unmatched_winners": b.get("unmatched_winners"),
            "exact_match_ok": (b.get("exact_match") or {}).get("ok"),
        }
    dep = conn.execute(
        "SELECT view, count(*) FROM (SELECT view, candidate_ref FROM brief_selection_case"
        " WHERE selection_id = %s AND role = 'matched_loser' AND headline IS TRUE"
        " GROUP BY view, candidate_ref HAVING count(DISTINCT pair_id) > 1) x GROUP BY view",
        (selection_id,),
    ).fetchall()
    for v, n in dep:
        bal.setdefault(str(v), {})["dependent_contrasts"] = {
            "losers_in_several_pairs": int(n),
            "label": "dependent contrasts: a matched loser serves more than one winner",
        }
    out["balance"] = bal
    sens: dict[str, Any] = {}
    for vkey, sv in (sensitivity or {}).items():
        if not isinstance(sv, dict):
            continue
        sens[vkey] = {
            "ran": sv.get("ran"),
            "min_jaccard": sv.get("min_jaccard"),
            "mean_jaccard": sv.get("mean_jaccard"),
            "share_winners_stable": sv.get("share_winners_stable"),
            "definition_sensitive": sv.get("definition_sensitive"),
            "sensitive_to_star_anomaly": sv.get("sensitive_to_star_anomaly"),
            "excluded_anomaly_flagged": sv.get("excluded_anomaly_flagged"),
            "alternatives": [
                {
                    k: a.get(k)
                    for k in ("key", "label", "ran", "jaccard", "winners", "changed")
                    if k in a
                }
                for a in sv.get("alternatives") or []
                if isinstance(a, dict)
            ],
            "note": sv.get("note"),
        }
    out["sensitivity"] = sens
    return out


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
    from pigtail.forensics.facts import FACTS_VERSION

    missing = [c.case_key for c in cases if c.facts is None]
    if missing:
        return ReportOutcome(
            "refused",
            EXIT_FAILED,
            f"{len(missing)} case(s) have no report "
            "facts: run `pigtail brief code` again to finish them",
        )
    stale = [c.case_key for c in cases if (c.facts or {}).get("version") != FACTS_VERSION]
    if stale:
        return ReportOutcome(
            "refused",
            EXIT_FAILED,
            f"{len(stale)} case(s) have report facts of an older version: run `pigtail brief "
            f"code {brief.brief_id} --refresh-facts` first (no LLM call)",
        )
    sel_rows = {(r["view"], r["candidate_ref"]): r for r in sel_cases(conn, run["selection_id"])}
    chosen = [
        (c, lab + (f" [{SENSITIVE}]" if _sensitive(c, sel_rows) else ""))
        for c, lab in narrative_cases(cases, sel_rows)
    ]
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
        snaps=deps.snapshots,
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


def _present_ids(conn: psycopg.Connection[Any], ids: set[str]) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, source, url, content_hash, fetched_at FROM evidence WHERE id = ANY(%s)"
        " AND deletion_state = 'present'",
        (sorted(ids),),
    ).fetchall()
    return {
        str(r[0]): {"source": r[1], "url": r[2], "content_hash": r[3], "fetched_at": r[4]}
        for r in rows
    }


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
    snaps: Any = None,
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
    from pigtail.forensics.assets_labelled import REAL_PRECISION, measure

    REAL_SOURCE = REAL_PRECISION["source"]

    precision = {**measure(), "rule": "assets-v3"}
    flags = {c.case_key: _flags(c, sel_rows) for c in cases}
    # a full coding run's alpha rows carry "full run, n = N" (rows stored before the fix say
    # "pilot"; relabelled here, the stored rows unchanged), before the pattern step so the
    # pattern rows carry the same labels as the reliability block (M25 fix 3)
    if run.get("case_rule_version") != "pilot-cases-v1":
        rel = [
            {**r, "labels": [re.sub(r"^pilot, n = ", "full run, n = ", x) for x in r["labels"]]}
            for r in rel
        ]
    pats = run_patterns(
        cases, finals, sel_rows, rel, secondary, asset_precision=precision, flags=flags
    )
    # the download figures' evidence joins the evidence index (verifier M24 round 2 fix 5)
    dl_ids: set[str] = set()
    for v in (pats.get("views") or {}).values():
        for side in ((v.get("secondary_exploratory") or {}).get("evidence_ids") or {}).values():
            for ids in side.values():
                dl_ids |= set(ids)
    missing = dl_ids - set(resolved)
    if missing:
        resolved = {
            **resolved,
            **(
                resolvable_ids(conn, snaps, missing)
                if snaps is not None
                else _present_ids(conn, missing)
            ),
        }
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
        "comparison": [comparison_row(c, lab, _sensitive(c, sel_rows)) for c, lab in chosen],
        "selection_diagnostics": selection_diagnostics(conn, brief, run["selection_id"]),
        "asset_rule_precision": precision,
        # measured real-data precision per feature, as the plan reads it (M25)
        "rule_precision": {
            f"asset.{a}": {"precision": m.get("precision"), "source": REAL_SOURCE}
            for a, m in REAL_PRECISION["per_asset"].items()
        },
        "definition_sensitive_cases": sorted(
            c.repo_full_name + f" (view {_vl(c.view)})" for c in cases if _sensitive(c, sel_rows)
        ),
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
    "Assets are detected by rules (assets-v3) on the README at T and the release notes up to "
    "T + 1 day; images embedded as HTML without a file extension, demos on the homepage only, "
    "and assets added later are not seen at launch.",
    "Stars before an event are unknown when the star history doesn't reach the creation day.",
    "HN launch events (events-v3): a story counts only when its URL is the repo (or a former "
    "URL GitHub resolves to it) or under the recorded homepage; any other story is an "
    "unconfirmed title match, shown as such and never counted. HN posts not titled Show HN / "
    "Launch HN are not searched.",
    "Asset precision: measured by hand on real READMEs for the previous rules (assets-v2, "
    "verifier round 2, n = 65: 0.78 overall; comparison table 0.56, screenshots 0.71, "
    "benchmarks 0.70); the rules were tightened since (assets-v3) and have not been re-measured "
    "on real data; the synthetic regression set only guards the known error types.",
    "Bursts: an open burst is still above its pre-burst baseline at the series' end; its total "
    "so far is not a burst size. Bursts are compared by their first 7 days.",
    "Patterns are associations between winners and matched losers in one neighbourhood, not "
    "causes; star trajectories are the outcome itself and are never a pattern feature.",
    "LLM-coded, not human-validated (no H3 calibration sample). Narratives only restate the "
    "fact sheet; sentences that could not be checked were dropped.",
]


# --- markdown ------------------------------------------------------------------------------
def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def _diagnostics_md(d: Mapping[str, Any]) -> list[str]:
    out = ["## Selection record and diagnostics", ""]
    out.append(f"- success definition: {_cell(d.get('success_definition'))}")
    summ = d.get("summary") or {}
    out.append(f"- selection {d.get('selection_version')} as of {d.get('as_of')}: {_cell(summ)}")
    sl = d.get("shortlist") or {}
    out.append(
        f"- shortlist record: status {sl.get('status')}; counts {_cell(sl.get('counts'))}; "
        f"reviewer decisions {_cell(sl.get('decisions'))}"
    )
    pr = d.get("relevance_precision") or {}
    if pr:
        meets = pr.get("meets_target")
        out.append(
            f"- relevance-filter precision: {pr.get('value')} ({pr.get('kept')}/"
            f"{pr.get('decided')} kept) against the {pr.get('target')} target — "
            f"{'meets' if meets else 'below target' if meets is False else 'not measured'}; "
            f"{pr.get('label')}"
        )
    else:
        out.append("- relevance-filter precision: not available (no stored shortlist)")
    out += ["", "Balance after matching (SMD per covariate; target shown; no p-values):", ""]
    for v, b in (d.get("balance") or {}).items():
        covs = b.get("covariates") or {}
        cells = ", ".join(
            f"{k} {c.get('smd')}" + (" ✗" if c.get("meets_target") is False else "")
            for k, c in covs.items()
        )
        dep = b.get("dependent_contrasts")
        out.append(
            f"- view {_vl(v)} (target {b.get('target')}): {cells or '-'}; missing the target: "
            f"{', '.join(b.get('covariates_missing_target') or []) or 'none'}; headline pairs "
            f"{b.get('headline_pairs')}; exact match ok {b.get('exact_match_ok')}"
            + (f"; {dep['label']} ({dep['losers_in_several_pairs']})" if dep else "")
        )
    out += ["", "Sensitivity check (alternative success definitions; descriptive):", ""]
    for v, sv in (d.get("sensitivity") or {}).items():
        out.append(
            f"- view {_vl(v)}: {sv.get('ran')} alternatives ran; winners stable "
            f"{sv.get('share_winners_stable')}; Jaccard min {sv.get('min_jaccard')} / mean "
            f"{sv.get('mean_jaccard')}; definition-sensitive cases {sv.get('definition_sensitive')}"
            f"; sensitive to the star-anomaly filter {sv.get('sensitive_to_star_anomaly')}"
        )
        for a in sv.get("alternatives") or []:
            out.append(f"  - {_cell(a)}")
    out.append("")
    return out


def _alpha_md(rel: Sequence[Mapping[str, Any]]) -> list[str]:
    out = [
        "## Methods and data quality: per-field agreement (Krippendorff's α)",
        "",
        "| field | statistic | α | 95 % CI | pairable units | cases | labels |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rel:
        ci = r.get("ci") or {}
        a = "-" if r.get("alpha") is None else f"{r['alpha']:.3f}"
        lo, hi = ci.get("low"), ci.get("high")
        cis = "-" if lo is None else f"{lo:.2f}–{hi:.2f}"
        labels = list(r.get("labels") or [])
        if r.get("alpha") is not None and r["alpha"] < 0.70 and "low reliability" not in labels:
            labels.append("low reliability")
        out.append(
            f"| {r['field']} | {r['statistic']} | {a} | {cis} | {r.get('n_pairable')} | "
            f"{r.get('n_cases')} | {_cell('; '.join(labels))} |"
        )
    out.append("")
    return out


def _asset_precision_md(pr: Mapping[str, Any]) -> list[str]:
    out = [
        f"Asset rules ({pr.get('rule')}) measured on the synthetic labelled set {pr.get('set')} "
        f"({pr.get('note')}):",
        "",
        "| asset | examples | precision | recall |",
        "|---|---|---|---|",
    ]
    for a, m in (pr.get("per_asset") or {}).items():
        out.append(f"| {a} | {m['examples']} | {m['precision']} | {m['recall']} |")
    out.append("")
    return out


def _names(xs: Sequence[str]) -> str:
    return ", ".join(xs) if xs else "none found"


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
    ]
    if r.get("selection_diagnostics"):
        out += _diagnostics_md(r["selection_diagnostics"])
    sens = r.get("definition_sensitive_cases") or []
    out += [
        f"Definition-sensitive cases (their role changes under an alternative success "
        f"definition; flagged [{SENSITIVE}] wherever they appear): {len(sens)}.",
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
        assets = f["assets_at_launch"]
        if f.get("assets_status", "detected") != "detected":
            out.append(f"- assets at launch: {f['assets_status']}")
        else:
            present = [a for a in assets if a["value"] == "present"]
            out.append(
                "- assets at launch (README at T and release notes up to T + 1 day): "
                + (
                    ", ".join(f"{a['asset']} (“{a.get('excerpt', '')}”)" for a in present)
                    or "none detected"
                )
                + " ["
                + ", ".join(sorted({i for a in assets for i in a["evidence_ids"]}))
                + "]"
            )
        for a in f.get("assets_after_launch") or []:
            out.append(
                f"- after launch (release notes later than T + 1 day, not at launch): "
                f"{a['asset']} (“{a.get('excerpt')}”) [{', '.join(a['evidence_ids'])}]"
            )
        out.append(
            "- amplifiers: " + "; ".join(f"{a['role']}: {a['value']}" for a in f["amplifiers"])
        )
        for b in f["largest_bursts"]:
            first7 = b.get("stars_first_7_days")
            size = (
                f"{first7} stars in the first 7 days"
                + ("" if b.get("first_7_days_complete", True) else " (some days missing)")
                if first7 is not None
                else f"{b.get('stars_in_first_48h')} stars in the first 48 h"
            ) + f", peak {b.get('peak_stars')} on {b.get('peak_day')}"
            if b.get("open"):
                size += (
                    f"; open: still above the pre-burst baseline at the series' end "
                    f"({b.get('stars_so_far')} stars so far over {b.get('days_so_far')} days, "
                    "not the burst's size)"
                )
            elif b.get("stars_total") is not None:
                size += f"; {b.get('stars_total')} stars over its {b.get('days')} days"
            also = b.get("explained_also") or []
            out.append(
                f"- burst from {b['onset_day']}: {size}; explained by {b['explained_by']}"
                + (f" (with {', '.join(also)} within 24 h)" if also else "")
                + f" ({b['label']}) [{', '.join(b['evidence_ids'])}]"
            )
        out.append("")
    out += [
        "## (b) Comparison table",
        "",
        "| case | view | role | first launch (confirmed) | title | assets | amplifiers | "
        "stars +1/+7/+30 d | bursts (explained) | largest burst, first 7 days |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in r["comparison"]:
        fl = c["first_launch"] or {}
        g = c["stars_after_first_launch"]
        flag = f" [{SENSITIVE}]" if c.get("definition_sensitive") else ""
        unc = c.get("unconfirmed_hn_matches") or 0
        out.append(
            f"| {_cell(c['case'])}{flag} | {c['view']} | {_cell(c['role'])} | "
            f"{_cell(fl.get('where'))} {_cell(fl.get('when'))}"
            + (f" ({unc} unconfirmed HN match(es) not counted)" if unc else "")
            + f" | {_cell(fl.get('title'))} | "
            f"{_cell(', '.join(c['assets']))} | {_cell(', '.join(c['amplifiers']))} | "
            f"{g.get('+1d')}/{g.get('+7d')}/{g.get('+30d')} | {c['bursts']} "
            f"({c['bursts_explained']}) | {c.get('largest_burst_stars')}"
            + (f" ({c['open_bursts']} open)" if c.get("open_bursts") else "")
            + " |"
        )
    pats = r["patterns"]
    out += [
        "",
        "## (c) Winner-vs-loser patterns",
        "",
        f"Outcome dimension: attention ({pats['outcome_label']}). Minimum evidence: "
        f"{pats['min_evidence']['known_per_side']} known per side and "
        f"{pats['min_evidence']['present_total']} present in total (ADR-050.3). "
        "Unconfirmed HN title matches never count as a launch event.",
        "",
    ]
    if r.get("asset_rule_precision"):
        out += _asset_precision_md(r["asset_rule_precision"])
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
            out.append(
                f"- {f['feature']}: winners without: {_names(ce['winners_without'])}; "
                f"losers with: {_names(ce['losers_with'])}"
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
            evs = sec.get("evidence_ids") or {}
            for side in ("winners", "matched_losers"):
                for m, s in (sec.get(side) or {}).items():
                    ids = (evs.get(side) or {}).get(m) or []
                    out.append(
                        f"- {side} {m}: {s['median']} [{s['min']}–{s['max']}], n = {s['n']} "
                        f"[{', '.join(ids) or 'no evidence id stored'}]"
                    )
        out.append("")
    for vl, v in pats["distribution_examples"].items():
        out += [
            f"### Distribution examples (view {vl}): {v['exemplars']} exemplars, "
            f"{v['matched_losers']} matched losers",
            "",
            "| feature | exemplars | losers | d | α | labels | transferability |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in v["features"]:
            t = f["transferability"]
            rel = f["reliability"]
            a = "-" if rel.get("alpha") is None else f"{rel['alpha']:.2f}"
            out.append(
                f"| {f['feature']} | {f['winners']['n_present']}/{f['winners']['n_known']} | "
                f"{f['matched_losers']['n_present']}/{f['matched_losers']['n_known']} | {f['d']} "
                f"| {a} | {_cell('; '.join(f['labels'] + rel.get('labels', [])))} "
                f"| {t['label']} {_cell(', '.join(t.get('conditions') or []))} |"
            )
        out.append("")
    out += [
        "## (d) D3 plan and fast-path verdict",
        "",
        f"- D3 plan: {r['placeholders']['d3_plan']}",
        f"- Fast-path verdict: {r['placeholders']['fast_path_verdict']}",
        "",
    ]
    out += _alpha_md(r.get("reliability") or [])
    out += [
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
