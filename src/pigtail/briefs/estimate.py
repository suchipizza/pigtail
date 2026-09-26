"""Cost estimate before a brief run (PRD R18.5; ADR-053.1; D7 "Run").

Everything here is an **estimate** from a small, versioned planning model (`ESTIMATE_MODEL`):
counts of queries, candidates and cases multiplied by per-unit assumptions. None of the unit
figures is a measurement yet; the install guide will replace them with numbers from a real run
of the example brief (D6). The output always carries `label: "estimate"`.

What it reports:
- GitHub API requests per bucket (`core`, `graphql`, `search`) and the hours they need at the
  default 70 % caps (`pigtail.connectors.github_budget`); other free sources (HN Algolia).
- LLM calls and tokens per stage and in total; on the `subscription` backend, the share of the
  weekly allowance (see `pigtail.briefs.budget.resolve_allowance`) against
  `budget.subscription_share`, and how many weeks the run is spread over if it exceeds the cap
  (heavy stages run in chunks and pause at the cap, ADR-053.1); on the `api` backend, USD at list
  price against `budget.llm_api_usd`.
- Money: 0 unless a paid source is enabled (or the `api` backend is used). Every paid step is
  listed and `requires_approval` is true; the run won't start without explicit approval.
- Reuse: with a `RerunPlan` (a previous run of an earlier version), stages that will be reused
  cost nothing (R18.4).
- Expansion (R18.7): the LLM expansion is proposed on demand, before a run
  (`pigtail brief expand`, "Propose expansion" in `/briefs`), and only an accepted expansion is
  part of the brief version. A run therefore makes no expansion call; the `expansion` block
  reports whether the brief has one and what one proposal call costs (estimate-v1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pigtail.briefs.budget import WEEK, Allowance, resolve_allowance, utcnow
from pigtail.briefs.cache import RerunPlan, plan_rerun
from pigtail.briefs.expansion import EXPANSION_TOKENS
from pigtail.briefs.model import Brief, BriefInvalid
from pigtail.briefs.store import BriefNotFound, BriefStore
from pigtail.connectors.github_budget import DEFAULT_CAP_FRACTION, GITHUB_LIMITS_PER_HOUR

ESTIMATE_MODEL = "estimate-v1"  # v1: expansion is an on-demand call, not a run stage

# --- planning assumptions (estimate-v0; replaced by measured values after the example run) ---
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

# (input tokens, output tokens) per call
TOKENS = {
    "expansion": EXPANSION_TOKENS,
    "relevance": (1_500, 250),
    "extraction": (8_000, 1_500),
    "adjudication": (6_000, 1_000),
    "patterns": (20_000, 3_000),
}
EXTRACTION_CHUNKS_PER_CASE = 12
CODERS = 2  # double coding (R7.2)
ADJUDICATION_SHARE = 0.2
PATTERN_CALLS = 10


@dataclass(frozen=True)
class StageCost:
    stage: str
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reused: bool = False

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
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
    allowance: Allowance
    subscription_used_7d: float
    subscription_share_cap: float
    llm_api_usd: float
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
    def subscription_share(self) -> float:
        """Estimated share of one week's allowance this run needs (subscription backend)."""
        if self.llm_backend != "subscription":
            return 0.0
        return self.tokens / self.allowance.weekly_tokens

    @property
    def weeks(self) -> int:
        """Weeks the LLM work is spread over at the share cap (ADR-053.1: pause and resume)."""
        if self.llm_backend != "subscription" or self.tokens == 0:
            return 0
        cap = self.subscription_share_cap * self.allowance.weekly_tokens
        available_now = max(0.0, cap - self.subscription_used_7d)
        if self.tokens <= available_now:
            return 1
        return 1 + math.ceil((self.tokens - available_now) / cap)

    @property
    def money_usd(self) -> float | None:
        """Known paid cost; None when a paid step's price is unknown."""
        total = 0.0
        for p in self.paid_steps:
            if p.est_usd is None:
                return None
            total += p.est_usd
        return total

    @property
    def requires_approval(self) -> bool:
        return bool(self.paid_steps)

    def to_dict(self) -> dict[str, Any]:
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
                "stages": [s.to_dict() for s in self.stages],
                "subscription": {
                    "share_of_weekly_allowance": round(self.subscription_share, 4),
                    "share_cap": self.subscription_share_cap,
                    "used_last_7_days_tokens": self.subscription_used_7d,
                    "weeks": self.weeks,
                    "allowance": self.allowance.to_dict(),
                },
                "api_usd": round(self.llm_api_usd, 2),
            },
            "money": {
                "usd": self.money_usd,
                "paid_steps": [p.to_dict() for p in self.paid_steps],
                "requires_approval": self.requires_approval,
            },
            "reuse": self.reuse,
            "expansion": self.expansion,
        }


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
    allowance: Allowance,
    subscription_used_7d: float = 0.0,
    plan: RerunPlan | None = None,
    model: str = "claude-opus-5",
) -> Estimate:
    """Estimate one run of `brief` (widening included as an upper bound)."""
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

    core = gh("evidence", shortlisted * (CORE_PER_SHORTLISTED + star_pages)) + gh(
        "extraction", cases * CORE_PER_CASE
    )
    graphql = gh("discovery", math.ceil(candidates / GRAPHQL_BATCH) * GRAPHQL_POINTS_PER_BATCH)
    requests = {"core": core, "graphql": graphql, "search": gh("discovery", search)}
    hours = max(
        requests[r] / (GITHUB_LIMITS_PER_HOUR[r] * DEFAULT_CAP_FRACTION)  # type: ignore[index]
        for r in requests
    )
    other = {"hn_algolia": gh("discovery", terms * slices * HN_QUERIES_PER_TERM_SLICE)}

    def cost(stage: str, calls: int, per: tuple[int, int]) -> StageCost:
        return StageCost(stage, calls, calls * per[0], calls * per[1], reused=stage in reused)

    chunks = cases * EXTRACTION_CHUNKS_PER_CASE
    stages = [
        # R18.7: the run uses the accepted expansion; proposing one is a separate, on-demand call.
        cost("expansion", 0, TOKENS["expansion"]),
        cost("relevance", candidates, TOKENS["relevance"]),
        cost("extraction", chunks * CODERS, TOKENS["extraction"]),
        StageCost(
            "adjudication",
            math.ceil(chunks * ADJUDICATION_SHARE),
            math.ceil(chunks * ADJUDICATION_SHARE) * TOKENS["adjudication"][0],
            math.ceil(chunks * ADJUDICATION_SHARE) * TOKENS["adjudication"][1],
            reused="extraction" in reused,
        ),
        cost("patterns", PATTERN_CALLS, TOKENS["patterns"]),
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
    api_usd = 0.0
    if brief.budget.llm_backend == "api":
        from pigtail.llm.api import estimate_cost

        live = [s for s in stages if not s.reused]
        api_usd = sum(estimate_cost(model, s.input_tokens, s.output_tokens) for s in live)
        if api_usd > 0:
            paid.append(
                PaidStep("LLM calls", "anthropic_api", round(api_usd, 2), "api backend, list price")
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
        allowance=allowance,
        subscription_used_7d=subscription_used_7d,
        subscription_share_cap=brief.budget.subscription_share,
        llm_api_usd=api_usd,
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
    model: str,
    last_run_version: int | None,
) -> tuple[Estimate, RerunPlan | None]:
    """Estimate against this install's usage ledger, with a reuse plan when an earlier version
    of the brief was run (used by the CLI and the D7 API)."""
    from pigtail.llm.store import LLMStore

    ledger = LLMStore(Path(data_dir) / "llm.sqlite3")
    try:
        allowance = resolve_allowance(ledger)
        used = ledger.usage_since("subscription", utcnow() - WEEK)["tokens"]
    finally:
        ledger.close()
    plan = None
    if last_run_version is not None:
        try:
            plan = plan_rerun(store.get(brief.brief_id, last_run_version).brief, brief)
        except (BriefNotFound, BriefInvalid):
            plan = None
    est = estimate(brief, allowance=allowance, subscription_used_7d=used, plan=plan, model=model)
    return est, plan


def render_text(e: Estimate, brief: Brief) -> str:
    d = e.to_dict()
    sub = d["llm"]["subscription"]
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
        *(
            f"  {s.stage:<13} {s.llm_calls:>6,} calls {s.tokens:>12,} tokens"
            + ("  (reused)" if s.reused else "")
            for s in e.stages
        ),
    ]
    if e.llm_backend == "subscription":
        lines += [
            f"  share of your weekly subscription allowance: {sub['share_of_weekly_allowance']:.0%}"
            f" (cap {e.subscription_share_cap:.0%}; allowance {e.allowance.weekly_tokens:,} "
            f"tokens, basis: {e.allowance.basis})",
            f"  spread over about {e.weeks} week(s): heavy stages run in chunks and pause at "
            "the cap, then resume",
        ]
    else:
        lines.append(
            f"  API cost ~${e.llm_api_usd:.2f} "
            f"(cap budget.llm_api_usd ${brief.budget.llm_api_usd:.2f})"
        )
    money = e.money_usd
    lines += ["", "Money (non-LLM paid services):"]
    if not e.paid_steps:
        lines.append("  $0: no paid source is enabled (free tiers only)")
    else:
        shown = "unknown" if money is None else f"${money:.2f}"
        lines.append(f"  {shown} against budget.money_usd ${brief.budget.money_usd:.2f}")
        lines += [
            f"  - {p.step}: {'unknown' if p.est_usd is None else f'${p.est_usd:.2f}'} ({p.note})"
            for p in e.paid_steps
        ]
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
