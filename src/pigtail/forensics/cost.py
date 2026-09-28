"""Per-case cost model of the coding step, its calibration from the pilot, and the projection
for the full brief (PRD R15.8-R15.11, R18.5; Directive §6.4; ADR-064.4, ADR-086).

A case costs, on the extraction model through the Message Batches API (50 %), with the frame
and system prompt read from or written to the prompt cache:

    coder A call + coder B call + adjudication_share x adjudication call

plus its GitHub requests (budget, not money). `PLANNING` holds the assumed tokens per call before
any pilot has run; `measured(...)` builds the model from the pilot's cost ledger (average tokens
per call, prompt-cache reads and writes included, as billed) and stores it
(`brief_case_cost_model`), and `pigtail.briefs.estimate` uses the latest measured model for every
later estimate of the coding stages (item 9 of M23).

**Projection** (R15.11): `brief spent so far + per-case cost x (full-brief cases - pilot cases
already coded) + synthesis (planning)`, against the brief's `budget.money_usd`; above it the
pilot stops and says H6. The full brief's cases are every stored selection row with role
`winner`, `matched_loser`, `exemplar` or `exemplar_matched_loser`, in every view (a repo in two
views is two cases: their anchors can differ; identical inputs are served from the result cache,
so this is an upper bound).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pigtail.llm.pricing import PRICES_AS_OF, TokenUsage, cost_usd

COST_MODEL_VERSION = "case-cost-v1"
SELECTED_ROLES = ("winner", "matched_loser", "exemplar", "exemplar_matched_loser")
BATCH_CACHE_HIT_SHARE = 0.5  # as the estimate assumes for batches (estimate.CACHE_HIT_SHARE)


@dataclass(frozen=True)
class CallTokens:
    """Average tokens of one call. `input` excludes the cached prefix (read or written)."""

    input: float
    output: float
    cache_write: float = 0.0
    cache_read: float = 0.0

    @classmethod
    def planned(cls, total_in: int, prefix: int, out: int, hit: float) -> CallTokens:
        return cls(total_in - prefix, out, prefix * (1 - hit), prefix * hit)

    def times(self, n: float) -> TokenUsage:
        return TokenUsage(
            round(self.input * n),
            round(self.output * n),
            round(self.cache_write * n),
            round(self.cache_read * n),
        )

    @property
    def total_input(self) -> float:
        return self.input + self.cache_write + self.cache_read

    def to_dict(self) -> dict[str, float]:
        return {k: round(v, 1) for k, v in asdict(self).items()}


# Planning assumptions (before the first pilot; replaced by the measured model): the frame and
# the system prompt ~2,800 tokens (the cached prefix; `CODEBOOK_CONTEXT` is ~10,400 chars), a
# case's trimmed evidence <= 30,000 characters (~7,500 tokens) plus headers and instructions,
# ~4,000 output tokens for ~50 coded values with citations; the adjudicator sees the evidence
# plus the disagreeing units (~12 units, ~3,000 tokens) and writes ~1,500; 90 % of cases have at
# least one disagreement among their ~50 units.
PLAN_CODER = (11_000, 2_800, 4_000)  # (input incl. prefix, prefix, output)
PLAN_ADJ = (13_500, 2_800, 1_500)
PLAN_ADJ_SHARE = 0.9
PLAN_GITHUB = {"core": 6.0, "graphql": 1.0, "other": 2.0}


@dataclass(frozen=True)
class CaseCostModel:
    source: Literal["planning", "measured"]
    coder: CallTokens
    adjudication: CallTokens
    adjudication_share: float
    github: dict[str, float] = field(default_factory=lambda: dict(PLAN_GITHUB))
    n_cases: int = 0
    measured_usd: dict[str, float] | None = None  # per case, by stage (measured models)
    batch_share: float = 1.0  # share of calls that went through a batch (measured)
    model_version: str = COST_MODEL_VERSION

    def usd_per_case(self, model: str, *, batch: bool = True) -> dict[str, float | None]:
        """USD per case by stage on `model` (list price); None when the price is unknown."""
        a = cost_usd(model, self.coder.times(1), batch=batch)
        adj = cost_usd(model, self.adjudication.times(1), batch=batch)
        adj_case = None if adj is None else adj * self.adjudication_share
        total = None if a is None or adj_case is None else 2 * a + adj_case
        return {"coder_a": a, "coder_b": a, "adjudication": adj_case, "total": total}

    def github_per_case(self) -> dict[str, float]:
        return dict(self.github)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model_version": self.model_version,
            "coder_call": self.coder.to_dict(),
            "adjudication_call": self.adjudication.to_dict(),
            "adjudication_share": round(self.adjudication_share, 4),
            "github_per_case": {k: round(v, 2) for k, v in self.github.items()},
            "n_cases": self.n_cases,
            "measured_usd_per_case": self.measured_usd,
            "batch_share": round(self.batch_share, 4),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CaseCostModel:
        return cls(
            source="measured" if d.get("source") == "measured" else "planning",
            coder=CallTokens(**d["coder_call"]),
            adjudication=CallTokens(**d["adjudication_call"]),
            adjudication_share=float(d["adjudication_share"]),
            github={k: float(v) for k, v in (d.get("github_per_case") or {}).items()},
            n_cases=int(d.get("n_cases") or 0),
            measured_usd=d.get("measured_usd_per_case"),
            batch_share=float(d.get("batch_share", 1.0)),
        )


PLANNING = CaseCostModel(
    "planning",
    CallTokens.planned(*PLAN_CODER, hit=BATCH_CACHE_HIT_SHARE),
    CallTokens.planned(*PLAN_ADJ, hit=BATCH_CACHE_HIT_SHARE),
    PLAN_ADJ_SHARE,
)


# --- measured -------------------------------------------------------------------------------
@dataclass
class StageTotals:
    calls: int = 0
    batched: int = 0
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    usd: float = 0.0

    def add(self, row: Mapping[str, Any]) -> None:
        self.calls += int(row.get("calls") or 0)
        self.batched += int(row.get("batched") or 0)
        self.input += int(row.get("input_tokens") or 0)
        self.output += int(row.get("output_tokens") or 0)
        self.cache_write += int(row.get("cache_write_tokens") or 0)
        self.cache_read += int(row.get("cache_read_tokens") or 0)
        self.usd += float(row.get("cost_usd") or 0.0)

    def per_call(self) -> CallTokens:
        n = max(self.calls, 1)
        return CallTokens(
            self.input / n, self.output / n, self.cache_write / n, self.cache_read / n
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "batched_calls": self.batched,
            "mode": "batch"
            if self.calls and self.batched == self.calls
            else ("standard" if not self.batched else "mixed"),
            "input_tokens": self.input,
            "output_tokens": self.output,
            "cache_write_tokens": self.cache_write,
            "cache_read_tokens": self.cache_read,
            "usd": round(self.usd, 6),
        }


STAGE_OF_PROMPT = {
    "case-coder-a": "coder_a",
    "case-coder-b": "coder_b",
    "case-adjudicator": "adjudication",
}


def ledger_by_case(conn: Any, brief_run_id: str) -> dict[str, dict[str, StageTotals]]:
    """Actual cost per case (coding id) and stage from `llm_cost_ledger` (every model call of the
    pilot run; cached results cost nothing and have no row)."""
    rows = conn.execute(
        "SELECT COALESCE(case_ref, '-'), prompt_id, count(*), count(batch_id), sum(input_tokens),"
        " sum(output_tokens), sum(cache_write_tokens), sum(cache_read_tokens), sum(cost_usd)"
        " FROM llm_cost_ledger WHERE brief_run_id = %s GROUP BY 1, 2 ORDER BY 1, 2",
        (brief_run_id,),
    ).fetchall()
    out: dict[str, dict[str, StageTotals]] = {}
    for case_ref, prompt_id, calls, batched, tin, tout, cw, cr, usd in rows:
        stage = STAGE_OF_PROMPT.get(str(prompt_id), str(prompt_id))
        t = out.setdefault(str(case_ref), {}).setdefault(stage, StageTotals())
        t.add(
            {
                "calls": calls,
                "batched": batched,
                "input_tokens": tin,
                "output_tokens": tout,
                "cache_write_tokens": cw,
                "cache_read_tokens": cr,
                "cost_usd": usd,
            }
        )
    return out


def measured(
    by_case: Mapping[str, Mapping[str, StageTotals]],
    github_by_case: Mapping[str, Mapping[str, int]],
    n_cases: int,
) -> CaseCostModel:
    """The per-case model measured by a pilot of `n_cases` cases."""
    coder, adj = StageTotals(), StageTotals()
    usd = {"coder_a": 0.0, "coder_b": 0.0, "adjudication": 0.0}
    adjudicated = 0
    for stages in by_case.values():
        for name, t in stages.items():
            if name in ("coder_a", "coder_b"):
                coder.add(_row(t))
            if name == "adjudication":
                adj.add(_row(t))
                adjudicated += 1 if t.calls else 0
            if name in usd:
                usd[name] += t.usd
    n = max(n_cases, 1)
    gh: dict[str, float] = {}
    for reqs in github_by_case.values():
        for k, v in reqs.items():
            gh[k] = gh.get(k, 0.0) + v
    calls = coder.calls + adj.calls
    return CaseCostModel(
        "measured",
        coder.per_call() if coder.calls else PLANNING.coder,
        adj.per_call() if adj.calls else PLANNING.adjudication,
        adjudicated / n,
        {k: v / n for k, v in sorted(gh.items())} or dict(PLAN_GITHUB),
        n_cases,
        {**{k: round(v / n, 6) for k, v in usd.items()}, "total": round(sum(usd.values()) / n, 6)},
        (coder.batched + adj.batched) / calls if calls else 1.0,
    )


def _row(t: StageTotals) -> dict[str, Any]:
    return {
        "calls": t.calls,
        "batched": t.batched,
        "input_tokens": t.input,
        "output_tokens": t.output,
        "cache_write_tokens": t.cache_write,
        "cache_read_tokens": t.cache_read,
        "cost_usd": t.usd,
    }


# --- projection -------------------------------------------------------------------------------
def full_brief_cases(conn: Any, selection_id: str) -> dict[str, int]:
    """Cases of the full brief in a stored selection: per view and in total, and distinct repos."""
    rows = conn.execute(
        "SELECT view, count(*), count(DISTINCT candidate_ref) FROM brief_selection_case"
        " WHERE selection_id = %s AND role = ANY(%s) GROUP BY view ORDER BY view",
        (selection_id, list(SELECTED_ROLES)),
    ).fetchall()
    out = {f"view:{v}": int(n) for v, n, _d in rows}
    out["total"] = sum(int(n) for _v, n, _d in rows)
    row = conn.execute(
        "SELECT count(DISTINCT candidate_ref) FROM brief_selection_case"
        " WHERE selection_id = %s AND role = ANY(%s)",
        (selection_id, list(SELECTED_ROLES)),
    ).fetchone()
    out["distinct_repos"] = int(row[0]) if row else 0
    return out


def synthesis_usd(synthesis_model: str, *, batch: bool = True) -> float | None:
    """The patterns, report and plan stages at the estimate's planning numbers."""
    from pigtail.briefs.estimate import PATTERN_CALLS, PLAN_CALLS, REPORT_CALLS, TOKENS, price_stage

    total = 0.0
    for stage, calls in (
        ("patterns", PATTERN_CALLS),
        ("report", REPORT_CALLS),
        ("plan", PLAN_CALLS),
    ):
        usd, _r, _w = price_stage(
            stage, calls, TOKENS[stage], model=synthesis_model, batch=batch, api=True
        )
        if usd is None:
            return None
        total += usd
    return total


def projection(
    model: CaseCostModel,
    *,
    extraction_model: str,
    synthesis_model: str,
    full_cases: int,
    coded_cases: int,
    brief_spent_usd: float,
    cap_usd: float,
    month_spent_usd: float,
    month_cap_usd: float,
    batch: bool = True,
) -> dict[str, Any]:
    """The full brief's projected API spend against its cap; `h6` when above it (R15.11)."""
    per = model.usd_per_case(extraction_model, batch=batch)
    per_case = per["total"]
    synth = synthesis_usd(synthesis_model, batch=batch)
    remaining = max(0, full_cases - coded_cases)
    coding = None if per_case is None else per_case * remaining
    total = None if coding is None or synth is None else brief_spent_usd + coding + synth
    h6 = total is None or total > cap_usd + 1e-9
    month_room = month_cap_usd - month_spent_usd
    return {
        "label": "projection",
        "cost_model": model.source,
        "extraction_model": extraction_model,
        "synthesis_model": synthesis_model,
        "prices_as_of": PRICES_AS_OF,
        "batch": batch,
        "per_case_usd": {k: None if v is None else round(v, 6) for k, v in per.items()},
        "full_brief_cases": full_cases,
        "cases_already_coded": coded_cases,
        "cases_remaining": remaining,
        "coding_usd_remaining": None if coding is None else round(coding, 4),
        "synthesis_usd_planning": None if synth is None else round(synth, 4),
        "brief_spent_usd": round(brief_spent_usd, 4),
        "projected_total_usd": None if total is None else round(total, 4),
        "cap_usd": cap_usd,
        "within_cap": not h6,
        "h6": h6,
        "month_room_usd": round(month_room, 4),
        "fits_this_month": None if coding is None else (coding + (synth or 0)) <= month_room,
        "github_requests_remaining": {
            k: math.ceil(v * remaining) for k, v in model.github_per_case().items()
        },
    }
