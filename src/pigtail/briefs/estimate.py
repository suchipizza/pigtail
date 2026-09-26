"""Cost estimate before a brief run (PRD R15.8-R15.11, R18.5; Directive §6.4, ADR-064.4,
ADR-072.4; D7 "Run").

Everything here is an **estimate** from a small, versioned planning model (`ESTIMATE_MODEL`):
counts of queries, candidates and cases multiplied by per-unit assumptions. None of the unit
figures is a measurement yet: the pilot (M23) replaces them with the actual cost per case from
the cost ledger. The output always carries `label: "estimate"`.

What it reports:
- GitHub API requests per bucket (`core`, `graphql`, `search`) and the hours they need at the
  default 70 % caps (`pigtail.connectors.github_budget`); other free sources (HN Algolia).
- LLM calls, tokens and **USD per stage**: each run stage maps to an LLM stage and its model
  (R15.8: relevance filter, extraction and coding, synthesis/report/plan), is sent through the
  Message Batches API when it isn't time-sensitive (R15.9, 50 % price), and its stable prefix
  (system prompt and codebook) is read from the prompt cache (R15.9, `CACHED_PREFIX`,
  `CACHE_HIT_SHARE`; a prefix below the model's minimum cacheable length is priced uncached).
  Prices: `pigtail.llm.pricing` (dated table).
- The **caps** (R15.11): the brief's total money cap `budget.money_usd` (API included, minus
  what the brief has already spent) and the instance's monthly cap `BUDGET_USD_MONTH` (minus
  this month's API spend), and whether the estimate fits both. A run that doesn't fit stops at
  the cap (H6).
- Money: every paid step (API calls, enabled paid sources) is listed and `requires_approval`
  is true; the run won't start without explicit approval.
- On the `subscription` backend: the expected number of model calls and tokens, no money.
  `budget.subscription_share` applies only to the agents building pigtail (ADR-064.4).
- Reuse: with a `RerunPlan` (a previous run of an earlier version), stages that will be reused
  cost nothing (R18.4).
- Expansion (R18.7): proposed on demand, before a run (`pigtail brief expand`); a run makes no
  expansion call. The `expansion` block reports its status and what one proposal costs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pigtail.briefs.budget import month_start, utcnow
from pigtail.briefs.cache import RerunPlan, plan_rerun
from pigtail.briefs.expansion import EXPANSION_TOKENS
from pigtail.briefs.model import Brief, BriefInvalid
from pigtail.briefs.store import BriefNotFound, BriefStore
from pigtail.config import DEFAULT_BUDGET_USD_MONTH, DEFAULT_STAGE_MODELS
from pigtail.connectors.github_budget import DEFAULT_CAP_FRACTION, GITHUB_LIMITS_PER_HOUR
from pigtail.llm.pricing import TokenUsage, canonical_model, cost_usd, pricing_table
from pigtail.llm.stages import stage_for, time_sensitive

# v3 (M22): the relevance filter sends ~20 candidates per request; discovery fetches one README
# per candidate (core bucket). v2 (M21b): per-stage models, Batch API discount, prompt caching,
# the brief's total money cap and the monthly cap. v1 (ADR-058.4): expansion is an
# on-demand call, not a run stage.
ESTIMATE_MODEL = "estimate-v3"

# --- planning assumptions (placeholders until the pilot measures them, M23) ---------------
SEARCH_PAGES_PER_QUERY = 2  # 100 results per page
CANDIDATES_PER_QUERY_SLICE = 40  # new unique candidates per (term, quarter) after de-duplication
MAX_CANDIDATES = 1500
TERMS_PER_WIDENING_STEP = 3
SHORTLIST_FRACTION = 0.25  # share of candidates the relevance filter keeps
CORE_PER_SHORTLISTED = 7  # repo metadata, contributors, issues/PRs, releases (outcome metrics)
STAR_HISTORY_WEEKS_PER_PAGE = 30
CORE_PER_CASE = 40  # deep forensics per winner, loser and reference case (events, releases, ...)
GRAPHQL_BATCH = 50  # candidates per GraphQL metadata query
GRAPHQL_POINTS_PER_BATCH = 2
HN_QUERIES_PER_TERM_SLICE = 1

# Relevance filter (M22, R4.6): about 20 candidates per request, each ~350 input tokens
# (name, description, topics, README excerpt of <= 1,200 chars) and ~70 output tokens.
RELEVANCE_PER_REQUEST = 20
# (input tokens, output tokens) per call, after evidence trimming (R15.10)
TOKENS = {
    "expansion": EXPANSION_TOKENS,
    "relevance": (1_000 + RELEVANCE_PER_REQUEST * 350, RELEVANCE_PER_REQUEST * 70),
    "extraction": (8_000, 1_500),
    "adjudication": (6_000, 1_000),
    "patterns": (20_000, 3_000),
    "report": (30_000, 6_000),
    "plan": (20_000, 5_000),
}
# Stable, cached prefix of each call's input (system prompt, rubric or codebook), in tokens.
CACHED_PREFIX = {
    "expansion": 0,
    "relevance": 1_000,
    "extraction": 6_000,
    "adjudication": 5_000,
    "patterns": 3_000,
    "report": 3_000,
    "plan": 3_000,
}
# Share of calls that read the prefix from the cache (the rest write it). Batches are
# processed in any order, so cache hits in them are best effort: assumed lower.
CACHE_HIT_SHARE = {"batch": 0.5, "standard": 0.8}
# Minimum cacheable prefix per model (claude-api skill, prompt caching). Opus 5.5 isn't listed;
# assumed 1,024 (Opus 5 is 512), so a prefix between the two is priced uncached.
MIN_CACHEABLE = {"claude-haiku-4-5": 4_096, "claude-sonnet-5": 1_024, "claude-opus-5-5": 1_024}
EXTRACTION_CHUNKS_PER_CASE = 12
CODERS = 2  # double coding (R7.2)
ADJUDICATION_SHARE = 0.2
PATTERN_CALLS = 10
REPORT_CALLS = 4
PLAN_CALLS = 3


@dataclass(frozen=True)
class StageCost:
    stage: str
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reused: bool = False
    llm_stage: str | None = None
    model: str | None = None
    mode: str = "standard"  # batch | standard (R15.9)
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float | None = 0.0  # api backend, list price; None: unknown price

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "llm_stage": self.llm_stage,
            "model": self.model,
            "mode": self.mode,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "usd": None if self.usd is None else round(self.usd, 4),
            "reused": self.reused,
        }


@dataclass(frozen=True)
class PaidStep:
    step: str
    source: str
    est_usd: float | None  # None: unknown until the source's pricing is set (M13 terms audit)
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "source": self.source,
            "est_usd": self.est_usd,
            "note": self.note,
        }


@dataclass
class Estimate:
    brief_id: str
    brief_version: int | None
    model: str
    github_requests: dict[str, int]
    github_hours: float
    other_requests: dict[str, int]
    candidates: int
    shortlisted: int
    cases: int
    stages: list[StageCost]
    llm_backend: str
    brief_cap_usd: float
    llm_api_usd: float | None = 0.0  # None: a stage's model has no known price
    month_cap_usd: float = DEFAULT_BUDGET_USD_MONTH
    brief_spent_usd: float = 0.0
    month_spent_usd: float = 0.0
    batch: bool = True
    paid_steps: list[PaidStep] = field(default_factory=list)
    reuse: dict[str, Any] | None = None
    expansion: dict[str, Any] = field(default_factory=dict)
    exemplar_cases: int = 0  # distribution exemplars + their matched losers (in `cases`)

    @property
    def llm_calls(self) -> int:
        return sum(s.llm_calls for s in self.stages if not s.reused)

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.stages if not s.reused)

    @property
    def money_usd(self) -> float | None:
        """Known paid cost (API included); None when a paid step's price is unknown."""
        total = 0.0
        for p in self.paid_steps:
            if p.est_usd is None:
                return None
            total += p.est_usd
        return total

    @property
    def requires_approval(self) -> bool:
        return bool(self.paid_steps)

    def caps(self) -> dict[str, Any]:
        """R15.11: the estimate against the brief's total cap and the monthly cap."""
        money = self.money_usd

        def cap(limit: float, spent: float) -> dict[str, Any]:
            left = max(0.0, limit - spent)
            return {
                "cap_usd": limit,
                "spent_usd": round(spent, 2),
                "remaining_usd": round(left, 2),
                "estimate_usd": None if money is None else round(money, 2),
                "within": None if money is None else money <= left + 1e-9,
            }

        brief = cap(self.brief_cap_usd, self.brief_spent_usd)
        month = cap(self.month_cap_usd, self.month_spent_usd)
        within = None if money is None else bool(brief["within"] and month["within"])
        return {
            "brief": {**brief, "field": "budget.money_usd (total, API included)"},
            "month": {**month, "field": "BUDGET_USD_MONTH"},
            "within_caps": within,
            "on_exceed": "the run hard-stops at the cap with a resumable checkpoint (H6)",
        }

    def to_dict(self) -> dict[str, Any]:
        live = [s for s in self.stages if not s.reused]
        return {
            "label": "estimate",
            "model": self.model,
            "brief_id": self.brief_id,
            "brief_version": self.brief_version,
            "github": {
                "requests": self.github_requests,
                "hours_at_default_caps": round(self.github_hours, 2),
            },
            "other_requests": self.other_requests,
            "counts": {
                "candidates": self.candidates,
                "shortlisted": self.shortlisted,
                "cases": self.cases,
                "exemplar_cases": self.exemplar_cases,
            },
            "llm": {
                "backend": self.llm_backend,
                "calls": self.llm_calls,
                "tokens": self.tokens,
                "batch": self.batch and self.llm_backend == "api",
                "stages": [s.to_dict() for s in self.stages],
                "api_usd": None if self.llm_api_usd is None else round(self.llm_api_usd, 2),
                "per_llm_stage": _per_llm_stage(live),
                "pricing": pricing_table() if self.llm_backend == "api" else None,
                "subscription_note": (
                    "no money cost; budget.subscription_share applies only to the agents "
                    "building pigtail (ADR-064.4)"
                    if self.llm_backend == "subscription"
                    else None
                ),
            },
            "caps": self.caps(),
            "money": {
                "usd": self.money_usd,
                "paid_steps": [p.to_dict() for p in self.paid_steps],
                "requires_approval": self.requires_approval,
            },
            "reuse": self.reuse,
            "expansion": self.expansion,
        }


def _per_llm_stage(stages: list[StageCost]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in stages:
        if not s.llm_calls or s.llm_stage is None:
            continue
        d = out.setdefault(s.llm_stage, {"model": s.model, "calls": 0, "tokens": 0, "usd": 0.0})
        d["calls"] += s.llm_calls
        d["tokens"] += s.tokens
        d["usd"] = None if d["usd"] is None or s.usd is None else round(d["usd"] + s.usd, 4)
    return out


def price_stage(
    stage: str,
    calls: int,
    per: tuple[int, int],
    *,
    model: str,
    batch: bool,
    api: bool,
) -> tuple[float | None, int, int]:
    """USD (None when the price is unknown), cache-read and cache-write tokens of `calls`."""
    if calls == 0:
        return 0.0, 0, 0
    tin, tout = per
    prefix = min(CACHED_PREFIX.get(stage, 0), tin)
    if prefix < MIN_CACHEABLE.get(canonical_model(model), 1_024):
        prefix = 0  # below the minimum cacheable length: sent uncached
    hit = CACHE_HIT_SHARE["batch" if batch else "standard"]
    reads = round(calls * hit) * prefix
    writes = (calls - round(calls * hit)) * prefix
    usage = TokenUsage(
        input=calls * (tin - prefix), output=calls * tout, cache_write=writes, cache_read=reads
    )
    usd = cost_usd(model, usage, batch=batch) if api else 0.0
    return usd, reads, writes


def _terms(brief: Brief) -> int:
    n = len(brief.field.include) + 1  # + the core field itself
    if brief.expansion is not None:
        e = brief.expansion
        n += len(e.keywords) + len(e.topics) + len(e.github_topics) + len(e.search_queries)
    return n


def expansion_status(brief: Brief) -> dict[str, Any]:
    """R18.7: whether the brief has an accepted expansion, and the cost of one proposal."""
    e = brief.expansion
    if e is None:
        status = "none"
    elif e.generated_by == "llm":
        status = "accepted_llm_proposal"
    else:
        status = "written_by_user"
    tin, tout = TOKENS["expansion"]
    return {
        "status": status,
        "edited_by_user": bool(e and e.provenance and e.provenance.edited_by_user),
        "run_llm_calls": 0,
        "proposal": {
            "llm_calls": 1,
            "input_tokens": tin,
            "output_tokens": tout,
            "command": f"pigtail brief expand {brief.brief_id}",
            "note": "on demand, before a run; saved only when you accept it",
        },
    }


def estimate(
    brief: Brief,
    *,
    plan: RerunPlan | None = None,
    models: Mapping[str, str] | None = None,
    batch: bool = True,
    month_cap_usd: float = DEFAULT_BUDGET_USD_MONTH,
    month_spent_usd: float = 0.0,
    brief_spent_usd: float = 0.0,
) -> Estimate:
    """Estimate one run of `brief` (widening included as an upper bound).

    `models` maps LLM stages to models (R15.8; default: the code defaults); `batch` is whether
    non-time-sensitive stages use the Message Batches API (`LLM_BATCH`)."""
    stage_models: dict[str, str] = {str(k): v for k, v in DEFAULT_STAGE_MODELS.items()}
    stage_models.update(models or {})
    slices = math.ceil(brief.window.months / 3)
    terms = _terms(brief) + TERMS_PER_WIDENING_STEP * len(brief.field.widening_steps)
    search = terms * slices * SEARCH_PAGES_PER_QUERY
    candidates = min(terms * slices * CANDIDATES_PER_QUERY_SLICE, MAX_CANDIDATES)
    shortlisted = max(
        math.ceil(candidates * SHORTLIST_FRACTION), brief.panel.winners + brief.panel.losers
    )
    ex = brief.distribution_exemplars
    # Deep forensics: field panel, reference cases, and each distribution exemplar with its
    # matched losers (ADR-057.1).
    exemplar_cases = len(ex.projects) * (1 + ex.losers_per_exemplar)
    cases = (
        brief.panel.winners + brief.panel.losers + len(brief.field.reference_cases) + exemplar_cases
    )
    star_pages = math.ceil(brief.window.months * 4.35 / STAR_HISTORY_WEEKS_PER_PAGE) + 1
    reused = set(plan.fully_reused()) if plan is not None else set()

    def gh(stage: str, n: int) -> int:
        return 0 if stage in reused else n

    core = (
        gh("evidence", shortlisted * (CORE_PER_SHORTLISTED + star_pages))
        + gh("extraction", cases * CORE_PER_CASE)
        + gh("relevance", candidates)  # one README per candidate (M22)
    )
    graphql = gh("discovery", math.ceil(candidates / GRAPHQL_BATCH) * GRAPHQL_POINTS_PER_BATCH)
    requests = {"core": core, "graphql": graphql, "search": gh("discovery", search)}
    hours = max(
        requests[r] / (GITHUB_LIMITS_PER_HOUR[r] * DEFAULT_CAP_FRACTION)  # type: ignore[index]
        for r in requests
    )
    other = {"hn_algolia": gh("discovery", terms * slices * HN_QUERIES_PER_TERM_SLICE)}
    api = brief.budget.llm_backend == "api"

    def cost(stage: str, calls: int, *, reused_by: str | None = None) -> StageCost:
        job = "brief_expansion" if stage == "expansion" else stage
        llm_stage = stage_for(job)
        model = stage_models[llm_stage]
        use_batch = api and batch and not time_sensitive(job)
        is_reused = (reused_by or stage) in reused
        usd, reads, writes = price_stage(
            stage, calls, TOKENS[stage], model=model, batch=use_batch, api=api
        )
        return StageCost(
            stage,
            calls,
            calls * TOKENS[stage][0],
            calls * TOKENS[stage][1],
            reused=is_reused,
            llm_stage=llm_stage,
            model=model,
            mode="batch" if use_batch else "standard",
            cache_read_tokens=reads,
            cache_write_tokens=writes,
            usd=usd,
        )

    chunks = cases * EXTRACTION_CHUNKS_PER_CASE
    stages = [
        # R18.7: the run uses the accepted expansion; proposing one is a separate, on-demand call.
        cost("expansion", 0),
        cost("relevance", math.ceil(candidates / RELEVANCE_PER_REQUEST)),
        cost("extraction", chunks * CODERS),
        cost("adjudication", math.ceil(chunks * ADJUDICATION_SHARE), reused_by="extraction"),
        cost("patterns", PATTERN_CALLS),
        cost("report", REPORT_CALLS, reused_by="patterns"),
        cost("plan", PLAN_CALLS, reused_by="patterns"),
    ]

    paid: list[PaidStep] = []
    for src in brief.optional_sources.enabled_paid():
        paid.append(
            PaidStep(
                f"{src} collection",
                src,
                None,
                "price unknown until the source's terms audit sets it (M13); treated as paid",
            )
        )
    api_usd: float | None = 0.0
    if api:
        live = [s for s in stages if not s.reused]
        api_usd = None if any(s.usd is None for s in live) else sum(s.usd or 0.0 for s in live)
        if api_usd is None or api_usd > 0:
            paid.append(
                PaidStep(
                    "LLM calls",
                    "anthropic_api",
                    None if api_usd is None else round(api_usd, 2),
                    "api backend, list price"
                    + (", Batch API for non-time-sensitive stages" if batch else "")
                    + ", prompt caching",
                )
            )

    return Estimate(
        brief_id=brief.brief_id,
        brief_version=brief.version,
        model=ESTIMATE_MODEL,
        github_requests=requests,
        github_hours=hours,
        other_requests=other,
        candidates=candidates,
        shortlisted=shortlisted,
        cases=cases,
        stages=stages,
        llm_backend=brief.budget.llm_backend,
        brief_cap_usd=brief.budget.money_usd,
        llm_api_usd=api_usd,
        month_cap_usd=month_cap_usd,
        brief_spent_usd=brief_spent_usd,
        month_spent_usd=month_spent_usd,
        batch=batch,
        paid_steps=paid,
        reuse=plan.to_dict() if plan is not None else None,
        expansion=expansion_status(brief),
        exemplar_cases=exemplar_cases,
    )


def estimate_for(
    brief: Brief,
    *,
    store: BriefStore,
    data_dir: Path,
    models: Mapping[str, str] | None = None,
    batch: bool = True,
    month_cap_usd: float = DEFAULT_BUDGET_USD_MONTH,
    last_run_version: int | None = None,
    brief_spent_usd: float = 0.0,
) -> tuple[Estimate, RerunPlan | None]:
    """Estimate against this install's usage ledger (this month's API spend), with a reuse
    plan when an earlier version of the brief was run (used by the CLI and the D7 API).
    `brief_spent_usd` is what the brief has already spent (`PgCostLedger.brief_total`)."""
    from pigtail.llm.store import LLMStore

    ledger = LLMStore(Path(data_dir) / "llm.sqlite3")
    try:
        month = ledger.usage_since("api", month_start(utcnow()))["cost_usd"]
    finally:
        ledger.close()
    plan = None
    if last_run_version is not None:
        try:
            plan = plan_rerun(store.get(brief.brief_id, last_run_version).brief, brief)
        except (BriefNotFound, BriefInvalid):
            plan = None
    est = estimate(
        brief,
        plan=plan,
        models=models,
        batch=batch,
        month_cap_usd=month_cap_usd,
        month_spent_usd=month,
        brief_spent_usd=brief_spent_usd,
    )
    return est, plan


def _usd(v: float | None) -> str:
    return "unknown" if v is None else f"${v:,.2f}"


def render_text(e: Estimate, brief: Brief) -> str:
    d = e.to_dict()
    lines = [
        f"Cost ESTIMATE for {e.brief_id} v{e.brief_version} ({e.model}; not a measurement)",
        "",
        "GitHub API requests (your token):",
        *(f"  {k:<8} {v:>8,}" for k, v in e.github_requests.items()),
        f"  about {e.github_hours:.1f} h at the default 70 % caps",
        "Other free sources:",
        *(f"  {k:<11} {v:>5,} requests" for k, v in e.other_requests.items()),
        f"Candidates ~{e.candidates:,}, shortlisted ~{e.shortlisted:,}, cases {e.cases}"
        + (f" (incl. {e.exemplar_cases} distribution-exemplar cases)" if e.exemplar_cases else ""),
        "",
        f"LLM ({e.llm_backend} backend): {e.llm_calls:,} calls, {e.tokens:,} tokens",
    ]
    for s in e.stages:
        row = f"  {s.stage:<13} {s.llm_calls:>6,} calls {s.tokens:>12,} tokens"
        if e.llm_backend == "api" and s.llm_calls:
            row += f"  {s.model} {s.mode:<8} {_usd(s.usd):>10}"
        lines.append(row + ("  (reused)" if s.reused else ""))
    if e.llm_backend == "subscription":
        lines.append(f"  {d['llm']['subscription_note']}")
    else:
        p = d["llm"]["pricing"]
        lines.append(
            f"  API cost ~{_usd(e.llm_api_usd)} at list price (prices as of {p['as_of']}; "
            f"batch {int(p['batch_discount'] * 100)} %, prompt caching)"
        )
    money = e.money_usd
    lines += ["", "Money (API and other paid services):"]
    if not e.paid_steps:
        lines.append("  $0: no paid step (free tiers only)")
    else:
        lines += [f"  - {p.step}: {_usd(p.est_usd)} ({p.note})" for p in e.paid_steps]
        lines.append(f"  total {_usd(money)}")
    c = d["caps"]
    for key, label in (("brief", "brief cap budget.money_usd"), ("month", "monthly cap")):
        x = c[key]
        fits = {True: "fits", False: "EXCEEDS", None: "unknown"}[x["within"]]
        lines.append(
            f"  {label}: ${x['cap_usd']:,.2f}, spent ${x['spent_usd']:,.2f}, "
            f"remaining ${x['remaining_usd']:,.2f}: {fits}"
        )
    if c["within_caps"] is False:
        lines.append("  The run would stop at the cap (H6: spend above a cap needs the owner).")
    if e.paid_steps:
        lines.append("  Paid steps need explicit approval: re-run with --approve-paid.")
    x = e.expansion
    if x:
        state = {
            "none": "none yet",
            "accepted_llm_proposal": "accepted model proposal"
            + (" (edited)" if x["edited_by_user"] else ""),
            "written_by_user": "written by you",
        }[x["status"]]
        p = x["proposal"]
        lines += [
            "",
            f"Expansion (R18.7): {state}. The run makes no expansion call; a proposal "
            f"(`{p['command']}`) is 1 call, ~{p['input_tokens'] + p['output_tokens']:,} tokens, "
            "on demand.",
        ]
    if e.reuse is not None:
        lines += ["", f"Re-run from v{e.reuse['from_version']}:"]
        lines += [f"  {s['stage']:<13} {s['action']}" for s in e.reuse["stages"]]
    return "\n".join(lines)


# --- the stages of one `pigtail run` (M22: discovery, relevance, shortlist, selection; R19.1) ----
RUN_STAGES = ("discovery", "relevance", "shortlist", "selection")


def run_scope(e: Estimate, stages: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """The part of the estimate that `pigtail run` will spend with these stages (R18.5): GitHub
    and HN requests of discovery (plus one README per candidate for relevance), and the relevance
    filter's LLM calls and USD, against the brief's remaining cap and the monthly cap. The
    shortlist stage makes no paid call; the selection stage (only on a final shortlist) makes no
    model call and only GitHub requests for star history (ADR-077). Later milestones add their
    stages here."""
    st = set(stages)
    by = {s.stage: s for s in e.stages}
    rel = by["relevance"]
    github = {
        "search": e.github_requests["search"] if "discovery" in st else 0,
        "graphql": e.github_requests["graphql"] if "discovery" in st else 0,
        "core": e.candidates if "relevance" in st and not rel.reused else 0,
    }
    llm = rel.to_dict() if "relevance" in st and not rel.reused else None
    usd: float | None = 0.0
    if llm is not None and e.llm_backend == "api":
        usd = rel.usd
    brief_left = max(0.0, e.brief_cap_usd - e.brief_spent_usd)
    month_left = max(0.0, e.month_cap_usd - e.month_spent_usd)
    within = None if usd is None else (usd <= brief_left + 1e-9 and usd <= month_left + 1e-9)
    return {
        "label": "estimate",
        "model": e.model,
        "stages": [s for s in RUN_STAGES if s in st],
        "candidates": e.candidates,
        "github_requests": github,
        "hn_algolia_requests": e.other_requests.get("hn_algolia", 0) if "discovery" in st else 0,
        "llm": llm,
        "selection": (
            {
                "github": "star history: 1-3 core requests per shortlisted repo (ETag, 30 weeks "
                "per page), plus 1 GraphQL query per 50 repos without metadata",
                "llm_calls": 0,
                "api_usd": 0.0,
                "runs_only_when": "the shortlist is final",
            }
            if "selection" in st
            else None
        ),
        "api_usd": None if usd is None else round(usd, 4),
        "requires_approval": e.llm_backend == "api" and (usd is None or usd > 0),
        "brief_remaining_usd": round(brief_left, 2),
        "month_remaining_usd": round(month_left, 2),
        "within_caps": within,
        "on_exceed": "the run hard-stops at the cap with a resumable checkpoint (H6)",
    }


def render_scope_text(scope: dict[str, Any]) -> str:
    g = scope["github_requests"]
    lines = [
        f"This run ({', '.join(scope['stages']) or 'no stage'}; {scope['model']}, ESTIMATE):",
        f"  ~{scope['candidates']:,} candidates; GitHub requests: search {g['search']:,}, "
        f"graphql {g['graphql']:,}, core {g['core']:,} (READMEs); HN Algolia "
        f"{scope['hn_algolia_requests']:,}",
    ]
    llm = scope["llm"]
    if llm:
        lines.append(
            f"  relevance filter: {llm['llm_calls']:,} requests of ~20 candidates on "
            f"{llm['model']} ({llm['mode']}), {llm['input_tokens'] + llm['output_tokens']:,} "
            f"tokens, {_usd(scope['api_usd'])}"
        )
    if scope.get("selection"):
        lines.append(
            "  selection (once the shortlist is final): no model call; "
            + scope["selection"]["github"]
        )
    fits = {True: "fits", False: "EXCEEDS a cap: the run will stop there", None: "unknown"}
    lines.append(
        f"  remaining: brief ${scope['brief_remaining_usd']:,.2f}, month "
        f"${scope['month_remaining_usd']:,.2f}: {fits[scope['within_caps']]}"
    )
    return "\n".join(lines)
