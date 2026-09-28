"""Per-case cost model of the coding step, its calibration from the pilot, and the projection
for the full brief (PRD R15.8-R15.11, R18.5; Directive §6.4; ADR-064.4, ADR-086).

A case costs, on the extraction model through the Message Batches API (50 %), with the frame
and system prompt read from or written to the prompt cache:

    coder A call + coder B call + adjudication_share x adjudication call

plus its GitHub requests (budget, not money). `PLANNING` holds the assumed tokens per call before
any pilot has run; `measured(...)` builds the model from the pilot's cost ledger (average tokens
per call, prompt-cache reads and writes included, as billed) and stores it
(`brief_case_cost_model`), and `pigtail.briefs.estimate` uses the latest measured model for every
later estimate of the coding stages (item 9 of M23). Calls that failed (ledger status
`error`: no tokens, no money) are not calls of the model: they are left out of the averages,
and a pilot whose coder calls measured no cost stores no model and no projection (ADR-086
addendum 1: the first live pilot's calls all failed, and a projection from zero cost is
meaningless). `case-cost-v2` (addendum 1): the planning numbers of the flat coder output
(schema 2.0.0); stored `case-cost-v1` models are not used any more.

**`case-cost-v3` (ADR-086 addendum 3, verifier M23 round 1).** The measured model is built
**per call** from the ledger rows that are calls of the current job settings only
(`model_rows`, `row_exclusion`):

- status in `COST_MODEL_STATUSES` (`ok`, `invalid_output`, `error_billed`): never `error` (no
  call billed) and never `diagnostic` (a call made by hand outside a product job);
- a row covering several batch requests (`llm_cost_ledger.requests`, a hand back-fill) counts
  as that many calls, so its tokens are spread over them;
- the row's prompt version equals the current one of its prompt, and its thinking label
  (`llm_cost_ledger.thinking`, ADR-087) equals the label the job sends now on the row's model;
- rows written before migration 0031 have no thinking label. For them one narrow back-compat
  rule applies (`LEGACY_RULE`): a `double_coding` row with at least
  `LEGACY_SUPERSEDED_OUTPUT_TOKENS` (16,000, the coder's `max_tokens`) output tokens written
  before the ADR-087 commit (8f441b3, `ADR087_AT`) was made with adaptive thinking on (it spent
  its whole output budget) and is superseded; any other legacy row is taken as made under the
  current setting.

Billed failures under the current setting (`error_billed` with the current thinking label) stay
in: what failures cost is part of a case's cost (ADR-087). Coder calls per case are measured
too (`coder_calls_per_case`, 2 when every case was coded once per pass). The stored v2 models
(averaged over every row) are no longer read; `pigtail brief pilot-cost --rebuild` stores a v3
model from an existing pilot's ledger rows.

**Contingency** (`CONTINGENCY_FACTOR` = 1.25): the projection shows the base figure and the
figure with the future spend (remaining coding and synthesis) times 1.25; H6 is decided on the
figure with contingency (a pilot of a handful of cases is a small sample; the cap is a hard
stop, so the conservative figure gates).

**Adjudication share**: adjudication requests per coded case. A case with at least one A/B
disagreement gets one adjudication request, so without retries it is the share of coded cases
that went to adjudication (0.8 when 4 of 5 did); the per-case adjudication cost is this share
times the cost of one adjudication call.

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
from datetime import UTC, datetime
from typing import Any, Literal

from pigtail.llm.pricing import PRICES_AS_OF, TokenUsage, cost_usd
from pigtail.llm.store import COST_MODEL_STATUSES

COST_MODEL_VERSION = "case-cost-v3"
CONTINGENCY_FACTOR = 1.25  # on the projected future spend (module docstring)
CODERS_PER_CASE = 2.0  # coder A and coder B, one call each
# The back-compat rule for ledger rows without a thinking label (module docstring).
ADR087_COMMIT = "8f441b3"
ADR087_AT = datetime(2026, 9, 28, 18, 56, 14, tzinfo=UTC)  # the commit time of 8f441b3
LEGACY_SUPERSEDED_OUTPUT_TOKENS = 16_000  # the coder's max_tokens: the whole budget spent
LEGACY_RULE = (
    "rows without a thinking label (before migration 0031): a double_coding row with >= "
    f"{LEGACY_SUPERSEDED_OUTPUT_TOKENS} output tokens written before the ADR-087 commit "
    f"({ADR087_COMMIT}) is superseded (adaptive thinking); other legacy rows count as current"
)
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
# the system prompt ~3,000 tokens (the cached prefix; `CODEBOOK_CONTEXT` is ~11,300 chars), a
# case's trimmed evidence <= 30,000 characters (~7,500 tokens) plus headers and instructions,
# ~4,500 output tokens for ~45 flat units with citations (each unit repeats its key: ~500 more
# than the nested 1.0.0 form); the adjudicator sees the evidence plus the disagreeing units
# (~12 units, ~3,000 tokens) and writes ~1,500; 90 % of cases have at least one disagreement.
# One call per coder pass and case (the flat schema fits one call; ADR-086 addendum 1).
# Observed 2026-09-28 (one live coder request, thinking disabled, ADR-087): 9,083 input tokens,
# 4,941 output tokens, `end_turn`. The planning numbers are kept (one request is not a
# measurement; the pilot's measured model replaces them).
PLAN_CODER = (11_200, 3_000, 4_500)  # (input incl. prefix, prefix, output)
PLAN_ADJ = (13_700, 3_000, 1_500)
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
    coder_calls_per_case: float = CODERS_PER_CASE  # both passes (measured: retries included)
    rows: dict[str, Any] | None = None  # ledger rows used and excluded, by reason (measured)

    def usd_per_case(self, model: str, *, batch: bool = True) -> dict[str, float | None]:
        """USD per case by stage on `model` (list price); None when the price is unknown. The
        stages add up to `total`."""
        call = cost_usd(model, self.coder.times(1), batch=batch)
        a = None if call is None else call * self.coder_calls_per_case / CODERS_PER_CASE
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
            "coder_calls_per_case": round(self.coder_calls_per_case, 4),
            **({"ledger_rows": self.rows} if self.rows is not None else {}),
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
            model_version=str(d.get("model_version") or COST_MODEL_VERSION),
            coder_calls_per_case=float(d.get("coder_calls_per_case", CODERS_PER_CASE)),
            rows=d.get("ledger_rows"),
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
    """Actual cost per case (coding id) and stage from `llm_cost_ledger`: every billed row of
    the pilot run, whatever its settings (cached results cost nothing and have no row; failed
    requests (`error`) made no model call and are left out; billed failures (`error_billed`)
    and hand-recorded `diagnostic` calls were paid for and count). Calls are weighted by the
    requests a row covers. This is the spend report; the cost model reads `model_rows`."""
    rows = conn.execute(
        "SELECT COALESCE(case_ref, '-'), prompt_id, sum(requests),"
        " COALESCE(sum(requests) FILTER (WHERE batch_id IS NOT NULL), 0), sum(input_tokens),"
        " sum(output_tokens), sum(cache_write_tokens), sum(cache_read_tokens), sum(cost_usd)"
        " FROM llm_cost_ledger WHERE brief_run_id = %s AND status <> 'error'"
        " GROUP BY 1, 2 ORDER BY 1, 2",
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


@dataclass(frozen=True)
class CostSettings:
    """The settings a ledger row must have been made under to enter the cost model: per prompt
    id its current prompt version and job; per job the thinking setting, whose label on the
    row's model (`thinking(job, model)`) the row must carry."""

    prompt_versions: Mapping[str, str]
    prompt_jobs: Mapping[str, str]
    thinking_modes: Mapping[str, str]
    backend: str = "api"
    superseded_before: datetime = ADR087_AT

    def thinking(self, job: str, model: str) -> str | None:
        from pigtail.llm.thinking import THINKING_MODES, label

        if self.backend != "api":
            return "cli-default"
        mode = self.thinking_modes.get(job)
        for m in THINKING_MODES:
            if mode == m:
                try:
                    return label(m, model)
                except ValueError:
                    return None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_versions": dict(self.prompt_versions),
            "thinking_modes": dict(self.thinking_modes),
            "backend": self.backend,
            "legacy_rule": LEGACY_RULE,
            "superseded_before": self.superseded_before.isoformat(),
        }


def current_settings(
    *, backend: str = "api", superseded_before: datetime = ADR087_AT
) -> CostSettings:
    """The coding and adjudication jobs' settings as this code sends them."""
    from pigtail.forensics.prompts import (
        ADJUDICATOR,
        CODER_A,
        CODER_B,
        JOB_ADJUDICATION,
        JOB_CODING,
    )
    from pigtail.llm.thinking import mode_for

    return CostSettings(
        prompt_versions={p.id: p.version for p in (CODER_A, CODER_B, ADJUDICATOR)},
        prompt_jobs={
            CODER_A.id: JOB_CODING,
            CODER_B.id: JOB_CODING,
            ADJUDICATOR.id: JOB_ADJUDICATION,
        },
        thinking_modes={j: mode_for(j) for j in (JOB_CODING, JOB_ADJUDICATION)},
        backend=backend,
        superseded_before=superseded_before,
    )


def row_exclusion(row: Mapping[str, Any], settings: CostSettings) -> str | None:
    """Why a ledger row is left out of the cost model, or None when it is a call of the
    current settings (module docstring)."""
    status = str(row.get("status"))
    if status not in COST_MODEL_STATUSES:
        return f"status:{status}"
    pid = str(row.get("prompt_id"))
    if pid not in settings.prompt_versions:
        return "not_a_coding_prompt"
    if str(row.get("prompt_version")) != settings.prompt_versions[pid]:
        return "prompt_version"
    job = str(row.get("job") or settings.prompt_jobs[pid])
    thinking = row.get("thinking")
    if thinking is None:  # written before migration 0031: the narrow back-compat rule
        created = row.get("created_at")
        if (
            job == "double_coding"
            and int(row.get("output_tokens") or 0) >= LEGACY_SUPERSEDED_OUTPUT_TOKENS
            and isinstance(created, datetime)
            and created < settings.superseded_before
        ):
            return "legacy_superseded_thinking"
        return None
    want = settings.thinking(job, str(row.get("model")))
    if want is None or str(thinking) != want:
        return "thinking"
    return None


def model_rows(
    conn: Any, brief_run_id: str, settings: CostSettings
) -> tuple[dict[str, dict[str, StageTotals]], dict[str, Any]]:
    """Per case and stage the ledger rows that enter the cost model (`row_exclusion`), and a
    count of the rows and requests used and left out, by reason."""
    cur = conn.execute(
        "SELECT COALESCE(case_ref, '-') AS case_ref, prompt_id, prompt_version, job, model,"
        " status, thinking, requests, batch_id, input_tokens, output_tokens,"
        " cache_write_tokens, cache_read_tokens, cost_usd, created_at"
        " FROM llm_cost_ledger WHERE brief_run_id = %s ORDER BY id",
        (brief_run_id,),
    )
    cols = [d.name for d in cur.description or []]
    out: dict[str, dict[str, StageTotals]] = {}
    used: dict[str, Any] = {"rows": 0, "requests": 0, "usd": 0.0}
    excluded: dict[str, dict[str, Any]] = {}
    for raw in cur.fetchall():
        row = dict(zip(cols, raw, strict=True))
        n = max(1, int(row.get("requests") or 1))
        usd = float(row.get("cost_usd") or 0.0)
        why = row_exclusion(row, settings)
        slot = (
            used
            if why is None
            else excluded.setdefault(why, {"rows": 0, "requests": 0, "usd": 0.0})
        )
        slot["rows"] += 1
        slot["requests"] += n
        slot["usd"] = round(float(slot["usd"]) + usd, 6)
        if why is not None:
            continue
        stage = STAGE_OF_PROMPT.get(str(row["prompt_id"]), str(row["prompt_id"]))
        out.setdefault(str(row["case_ref"]), {}).setdefault(stage, StageTotals()).add(
            {
                "calls": n,
                "batched": n if row.get("batch_id") else 0,
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "cache_write_tokens": row.get("cache_write_tokens"),
                "cache_read_tokens": row.get("cache_read_tokens"),
                "cost_usd": usd,
            }
        )
    return out, {
        "used": used,
        "excluded": dict(sorted(excluded.items())),
        "settings": settings.to_dict(),
    }


def measurable(by_case: Mapping[str, Mapping[str, StageTotals]]) -> bool:
    """Whether the ledger measured a coder call that cost something (addendum 1: never a model
    or a projection from zero measured cost)."""
    return any(
        t.calls > 0 and t.usd > 0
        for stages in by_case.values()
        for name, t in stages.items()
        if name in ("coder_a", "coder_b")
    )


def measured(
    by_case: Mapping[str, Mapping[str, StageTotals]],
    github_by_case: Mapping[str, Mapping[str, int]],
    n_cases: int,
    *,
    rows: dict[str, Any] | None = None,
) -> CaseCostModel:
    """The per-case model measured by a pilot of `n_cases` coded cases, per call (a row
    covering several requests counts as that many calls), from the rows `model_rows` let in
    (`rows`: its counts, stored with the model). Only for a `measurable` ledger."""
    if not measurable(by_case):
        raise ValueError("no coder call with a measured cost: no cost model from zero cost")
    coder, adj = StageTotals(), StageTotals()
    usd = {"coder_a": 0.0, "coder_b": 0.0, "adjudication": 0.0}
    adjudicated = 0
    for stages in by_case.values():
        for name, t in stages.items():
            if name in ("coder_a", "coder_b"):
                coder.add(_row(t))
            if name == "adjudication":
                adj.add(_row(t))
                adjudicated += t.calls  # requests per coded case (module docstring)
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
        coder_calls_per_case=coder.calls / n if coder.calls else CODERS_PER_CASE,
        rows=rows,
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
    future = None if coding is None or synth is None else coding + synth
    total = None if future is None else brief_spent_usd + future
    with_c = None if future is None else brief_spent_usd + CONTINGENCY_FACTOR * future
    h6 = with_c is None or with_c > cap_usd + 1e-9
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
        "contingency_factor": CONTINGENCY_FACTOR,
        "projected_total_with_contingency_usd": None if with_c is None else round(with_c, 4),
        "cap_usd": cap_usd,
        "within_cap": not h6,
        "h6": h6,
        "h6_basis": "projected_total_with_contingency_usd",
        "month_room_usd": round(month_room, 4),
        "fits_this_month": None
        if coding is None
        else CONTINGENCY_FACTOR * (coding + (synth or 0)) <= month_room,
        "github_requests_remaining": {
            k: math.ceil(v * remaining) for k, v in model.github_per_case().items()
        },
    }
