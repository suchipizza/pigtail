"""Outcome sort, winners and matched losers, balance diagnostics and the sensitivity check of a
brief version's final shortlist (PRD R4.3, R4.8, R4.9, R4.10, R4.11, R18.8, §8.2, §9.2;
outcome-model v2.1 §3, §5, §6, §8; ADR-053.2, ADR-054, ADR-055.5, ADR-057, ADR-077).

Pure functions over `CaseInput`s (one per shortlisted repo): no database, no network, no clock.
`pigtail.briefs.outcomes` builds the inputs from stored data and `pigtail.briefs.selection_store`
persists the result. Everything here is deterministic for a given brief version and input set
(R4.8): cases are processed in `candidate_ref` order whatever order they arrive in, and every tie
is broken by `sha256(brief_id:brief_version:candidate_ref)` (outcome-model §5.4; the "seed" is
the brief id and version). `Selection.result_hash` is the SHA-256 of the canonical result.

**Outcome sort** (§3, §5.3–5.4, R18.8). The reference population is the final shortlist's field
and reference-case repos with an anchor (exemplars are outside the field, ADR-057.1). Each
metric's mid-rank percentile is computed over the population's `observed` values (per `group`,
e.g. the downloads ecosystem), and is `unknown` below `MIN_POPULATION` (20). A candidate
qualifies when it has an anchor and meets every threshold: a percentile floor is met only by an
observed value > 0 (zero floor) at or above the floor; `unknown` / `pending` never meet one and
make the candidate *undetermined*, not a loser; `not_applicable` fails unless the brief skips it
(`if_not_applicable`, `fallbacks.no_measurable_adoption`). Qualifiers are ranked by the primary
dimension's percentile (or the advanced weighted composite, §6); a qualifier without a rankable
value is *unrankable*.

**Selection** (§5.5–5.6, R4.3, R4.10, ADR-053.2, ADR-054, ADR-077). The top `panel.winners`
ranked qualifiers of the eligible panel are the winners. The loser pool is the eligible
candidates that fail a threshold on an observed value. Winners in rank order take an unused
loser (without replacement; more rounds while fewer than `panel.losers` are matched) among those
with the same `panel.exact_match` keys (founder audience bucket, launch half-year; `unknown` is
its own level) inside the calipers (`|ΔLSM| <= 0.5 SD`, `|Δquarter| <= 1`): one whose pair
passes the headline rule first, then the nearest (ADR-078; `match_losers`). The
panel starts at the core field (distance 0). If it has fewer than 15 winners or matched losers,
it widens one declared widening step at a time (R4.10); then, if fewer than
`fallbacks.too_few_winners.min_winners` rankable qualifiers remain, the brief's fallback steps
are applied in order (ADR-053.2, ADR-055.5). Every step is logged with its counts. Named
reference cases are always studied (`is_reference`, R4.11) whatever their role. Distribution
exemplars get `losers_per_exemplar` losers each, exactly matched on `match_on` and never on
field (ADR-057.1).

**Balance** (§5.6, §9.2, ADR-054.1): SMD per covariate (pooled-SD form; per level for language)
and the variance ratio, before and after matching, the exact-match check, and the pairs whose
standardized difference on any covariate exceeds `headline_exclusion_smd` (excluded from
headline patterns). After matching, each matched winner is counted once, even when it has two
losers (unweighted; ADR-078). No p-values; no re-matching.

**Sensitivity** (§8, R4.9): the winner set is recomputed under each listed alternative (primary
swap, band shift, weights, and D, exclude anomaly-flagged candidates, which the brief schema
still calls `fake_star_filter`), with the same N and eligible panel and without re-matching;
the Jaccard overlap and each baseline winner's and matched loser's status are reported, with the
flags `definition_sensitive`, `sensitive_to_star_anomaly` and `excluded_anomaly_flagged`.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from pigtail.analysis.params import PARAMS_VERSION
from pigtail.briefs.model import DIMENSIONS, RANKABLE, THRESHOLD_PERCENTILE, Brief, sha256_json

# v2: headline-first matching, missing language (ADR-078); v3: anchor rule anchor-v2 (ADR-081)
SELECTION_VERSION = "selection-v3"
OUTCOME_MODEL_VERSION = "2.1"
MIN_POPULATION = 20  # outcome-model §3 (v0 design choice; the brief schema has no field yet, O17)
WINNERS_MIN = 15  # R4.8 range floor: below it the report says "fewer winners than the minimum"
LOSERS_MIN = 15
MAX_WIDENING = 2  # distances 0-2 (the relevance filter labels at most two widening steps)
LADDER: tuple[float | None, ...] = (None, 25.0, 50.0, 75.0, 90.0, 95.0)  # §8.1 band ladder
LSM_CALIPER_SD = 0.5
QUARTER_CALIPER = 1
BUSINESS_SIGNALS = ("pricing_page", "hiring_hn_posts", "careers_roles")
BUSINESS_COUNT = "biz.verified_signal_count@365"
BAND_ORDINAL: dict[str, int] = {"none": 0, "r1": 1, "r2": 2, "r3": 3, "r4": 4}
TIE_BREAK = "sha256(brief_id:brief_version:candidate_ref) ascending"
MATCHING_RULE = (
    "round 1: each winner in rank order takes an eligible loser (exact match, calipers), "
    "headline-passing first, then nearest; later rounds: headline-passing losers first, then any "
    "eligible loser (ADR-078)"
)
# The anchor rule (outcome-model §2.2; `pigtail.briefs.outcomes.choose_anchor` and the launch
# lookup) is part of the pre-registered selection parameters (ADR-081): bump the version whenever
# either changes, so an existing pre-registration no longer passes the gate.
ANCHOR_RULE_VERSION = "anchor-v2"
ANCHOR_RULE = (
    "outcome-model §2.2 rules 1-6 as read by ADR-077.3; declared launches = Show HN or Launch HN "
    "posts from discovery and the per-repo launch lookup, merged by item id; a launch is compared "
    "with a day-precision burst onset on the onset's endpoint day (US Pacific, §1.2), so a launch "
    "on the onset day precedes the burst (ADR-081)"
)
LAUNCH_LOOKUP = (
    "HN Algolia per shortlisted repo, inside the brief's window: show_hn search for "
    "'github.com/<owner>/<name>' and for '<name>', story search for 'Launch HN <name>' (titles "
    "starting 'Launch HN' only); a hit is accepted when its URL is the repo's "
    "github.com/owner/name (case-insensitive) or, when it links no other GitHub repo, its title "
    "names the repo name (>= 4 characters) as a whole word; a title match claimed by two "
    "shortlisted repos, or linked by URL to another, is dropped; stored: item id, time, points, "
    "kind, match (ADR-081)"
)
ROUND = 6

Status = Literal["observed", "pending", "unknown", "not_applicable"]
CasePanel = Literal["field", "reference", "exemplar"]
Outcome = Literal["qualifies", "fails", "undetermined"]
AnomalyFlag = Literal["true", "false", "unknown"]
Role = Literal[
    "winner",
    "matched_loser",
    "qualified_not_selected",
    "unrankable",
    "loser_pool_unmatched",
    "undetermined",
    "no_anchor",
    "outside_widening",
    "exemplar",
    "exemplar_matched_loser",
]
ROLES: tuple[str, ...] = Role.__args__  # type: ignore[attr-defined]


class SelectionError(ValueError):
    pass


# --- inputs -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Value:
    """One metric value of one candidate (outcome-model §1.1): status, tag and, when `observed`,
    the value. `group` partitions the percentile population (the downloads ecosystem, §3)."""

    status: Status
    value: float | None = None
    tag: str = "unknown"  # verified | estimated | self_reported | unknown (ADR-014)
    reason: str | None = None
    group: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": _r(self.value),
            "status": self.status,
            "tag": self.tag,
            "reason": self.reason,
            "group": self.group,
        }


NO_SOURCE = Value("unknown", reason="no_source")


@dataclass(frozen=True)
class Anchor:
    """The case anchor T (outcome-model §2.2)."""

    type: Literal["launch", "burst"]
    at: datetime
    precision: Literal["hour", "day"]
    source: str  # show_hn | launch_hn | velocity-v0
    via: str | None = None  # launches: discovery | lookup:url | lookup:title (ADR-081)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "at": self.at.isoformat(),
            "precision": self.precision,
            "source": self.source,
            "via": self.via,
        }


@dataclass(frozen=True)
class Covariates:
    """Matching covariates (outcome-model §5.6), measured at or before T except LSM."""

    lsm: float | None = None  # log10(1 + raw stars in the first 2 endpoint days of the window)
    launch_quarter: int | None = None  # year * 4 + quarter index (UTC quarter of T)
    launch_half_year: str | None = None  # e.g. "2025H2"
    age_log10: float | None = None  # log10(days from repo creation to T)
    audience_band: str = "unknown"  # CB-10 reach band; "unknown" is its own level (O14)
    language: str | None = None
    launch_type: str | None = None  # show_hn | launch_hn | burst (ADR-057.1 exemplar matching)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lsm": _r(self.lsm),
            "launch_quarter": self.launch_quarter,
            "launch_half_year": self.launch_half_year,
            "age_log10": _r(self.age_log10),
            "audience_band": self.audience_band,
            "language": self.language,
            "launch_type": self.launch_type,
        }


@dataclass(frozen=True)
class CaseInput:
    ref: str  # candidate_ref (`gh:owner/name`)
    panel: CasePanel = "field"
    distance: int = 0  # 0 = core field (R4.10)
    named_index: int | None = None
    anchor: Anchor | None = None
    anchor_reason: str | None = None
    values: Mapping[str, Value] = field(default_factory=dict)
    business: Mapping[str, Value] = field(default_factory=dict)
    covariates: Covariates = field(default_factory=Covariates)
    star_anomaly_flag: AnomalyFlag = "unknown"
    anomaly: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_reference(self) -> bool:
        return self.panel == "reference"


# --- the success definition ---------------------------------------------------------------------
@dataclass(frozen=True)
class Definition:
    """The effective success definition (R18.1 `success`, after any fallback step)."""

    primary: str
    floors: Mapping[str, float | None]  # rankable dimension -> percentile floor (primary incl.)
    metrics: Mapping[str, str]  # dimension -> metric id
    business_minimum: tuple[str, ...] = ()
    if_not_applicable: Mapping[str, str] = field(default_factory=dict)
    weights: Mapping[str, float] | None = None
    no_measurable_adoption: str = "use_community_as_primary_and_flag"
    accept_self_reported: bool = False
    rank_by: str | None = None  # sensitivity A: rank by this dimension instead

    @classmethod
    def from_brief(cls, brief: Brief) -> Definition:
        s = brief.success
        floors: dict[str, float | None] = {}
        if s.primary != "business":
            floors[s.primary] = THRESHOLD_PERCENTILE[s.primary_threshold]
        for dim, t in s.minimums.items():
            floors[dim] = THRESHOLD_PERCENTILE[t]
        return cls(
            primary=s.primary,
            floors=floors,
            metrics={d: s.metric(d) for d in DIMENSIONS},
            business_minimum=tuple(s.business_minimum),
            if_not_applicable={str(k): v for k, v in s.if_not_applicable.items()},
            weights={str(k): w for k, w in s.weights.items()} if s.weights else None,
            no_measurable_adoption=s.fallbacks.no_measurable_adoption,
            accept_self_reported=s.accept_self_reported,
        )

    def star_metric_used(self) -> bool:
        """Sensitivity D runs only when a star metric is in the definition (§8.1)."""
        dims = {self.primary, *self.floors, *(self.weights or {})}
        return any(self.metrics.get(d, "").startswith("att.stars") for d in dims)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "floors": {d: _r(v) for d, v in sorted(self.floors.items())},
            "metrics": dict(sorted(self.metrics.items())),
            "business_minimum": list(self.business_minimum),
            "if_not_applicable": dict(sorted(self.if_not_applicable.items())),
            "weights": None if self.weights is None else dict(sorted(self.weights.items())),
            "no_measurable_adoption": self.no_measurable_adoption,
            "accept_self_reported": self.accept_self_reported,
            "rank_by": self.rank_by,
        }


@dataclass(frozen=True)
class Context:
    """Brief settings the selection reads (R4.3, R4.8, R4.10, ADR-053.2, ADR-054, ADR-057)."""

    brief_id: str
    brief_version: int
    winners: int = 20
    losers: int = 20
    exact_match: tuple[str, ...] = ("founder_audience_bucket", "launch_half_year")
    smd_target: float = 0.25
    headline_exclusion_smd: float = 0.5
    max_distance: int = 0
    min_winners: int = 10
    fallback_steps: tuple[str, ...] = ()
    losers_per_exemplar: int = 2
    exemplar_match_on: tuple[str, ...] = ("launch_type", "launch_period", "audience_bucket")
    sensitivity: tuple[str, ...] = ("primary_swap", "band_shift", "weights", "fake_star_filter")

    @classmethod
    def from_brief(cls, brief: Brief) -> Context:
        if brief.version is None:
            raise SelectionError("select on a stored brief version")
        tfw = brief.success.fallbacks.too_few_winners
        return cls(
            brief_id=brief.brief_id,
            brief_version=brief.version,
            winners=brief.panel.winners,
            losers=brief.panel.losers,
            exact_match=tuple(brief.panel.exact_match),
            smd_target=brief.panel.smd_target,
            headline_exclusion_smd=brief.panel.headline_exclusion_smd,
            max_distance=min(MAX_WIDENING, len(brief.field.widening_steps)),
            min_winners=tfw.min_winners,
            fallback_steps=tuple(tfw.steps),
            losers_per_exemplar=brief.distribution_exemplars.losers_per_exemplar,
            exemplar_match_on=tuple(brief.distribution_exemplars.match_on),
            sensitivity=tuple(dict.fromkeys(brief.success.sensitivity)),
        )

    def tie(self, ref: str) -> str:
        """Deterministic tie-break key (outcome-model §5.4)."""
        return hashlib.sha256(f"{self.brief_id}:{self.brief_version}:{ref}".encode()).hexdigest()

    def params(self) -> dict[str, Any]:
        return {
            "selection_version": SELECTION_VERSION,
            "outcome_model_version": OUTCOME_MODEL_VERSION,
            "analysis_params_version": PARAMS_VERSION,
            "min_population": MIN_POPULATION,
            "winners": self.winners,
            "losers": self.losers,
            "winners_min": WINNERS_MIN,
            "losers_min": LOSERS_MIN,
            "exact_match": list(self.exact_match),
            "smd_target": self.smd_target,
            "headline_exclusion_smd": self.headline_exclusion_smd,
            "calipers": {"lsm_sd": LSM_CALIPER_SD, "quarter": QUARTER_CALIPER},
            "matching": MATCHING_RULE,
            "anchor_rule_version": ANCHOR_RULE_VERSION,
            "anchor_rule": ANCHOR_RULE,
            "launch_lookup": LAUNCH_LOOKUP,
            "max_distance": self.max_distance,
            "too_few_winners": {
                "min_winners": self.min_winners,
                "steps": list(self.fallback_steps),
            },
            "exemplars": {
                "losers_per_exemplar": self.losers_per_exemplar,
                "match_on": list(self.exemplar_match_on),
            },
            "sensitivity": list(self.sensitivity),
            "tie_break": TIE_BREAK,
        }


# --- percentiles and qualification (§3, §5.3, §5.4, §6) -----------------------------------------
@dataclass
class Qual:
    outcome: Outcome
    checks: list[dict[str, Any]]
    score: float | None
    rank_basis: str
    flags: list[str]


@dataclass
class Evaluation:
    definition: Definition
    pct: dict[str, dict[str, float | None]]  # metric -> ref -> percentile
    populations: dict[str, dict[str, Any]]  # metric -> counts (n per group, statuses)
    qual: dict[str, Qual]  # ref -> qualification (population cases only)


def percentiles(
    cases: Sequence[CaseInput], metric: str
) -> tuple[dict[str, float | None], dict[str, Any]]:
    """Mid-rank percentiles of `metric` over the `observed` values of `cases`, per group
    (outcome-model §3): `p = 100 (n_below + 0.5 n_equal) / n`; `None` below MIN_POPULATION."""
    groups: dict[str, list[tuple[str, float]]] = {}
    statuses: dict[str, int] = {}
    for c in cases:
        v = c.values.get(metric, NO_SOURCE)
        statuses[v.status] = statuses.get(v.status, 0) + 1
        if v.status == "observed" and v.value is not None:
            groups.setdefault(v.group or "", []).append((c.ref, float(v.value)))
    out: dict[str, float | None] = {}
    ns: dict[str, int] = {}
    for g in sorted(groups):
        items = groups[g]
        n = len(items)
        ns[g or "all"] = n
        vals = sorted(v for _, v in items)
        for ref, x in items:
            if n < MIN_POPULATION:
                out[ref] = None
                continue
            lo = bisect.bisect_left(vals, x)
            eq = bisect.bisect_right(vals, x) - lo
            out[ref] = 100.0 * (lo + 0.5 * eq) / n
    reference = len(cases)
    unknown = statuses.get("unknown", 0)
    info = {
        "reference_n": reference,
        "n_by_group": ns,
        "statuses": dict(sorted(statuses.items())),
        "small_population": [g for g, n in ns.items() if n < MIN_POPULATION],
        "over_half_unknown": reference > 0 and unknown / reference > 0.5,
    }
    return out, info


def _substitute_community(c: CaseInput, d: Definition, dim: str) -> bool:
    """ADR-053.2 `no_measurable_adoption: use_community_as_primary_and_flag`."""
    return (
        dim == "adoption"
        and d.primary == "adoption"
        and d.no_measurable_adoption == "use_community_as_primary_and_flag"
        and c.values.get(d.metrics["adoption"], NO_SOURCE).status == "not_applicable"
    )


def _check(
    c: CaseInput, dim: str, floor: float, d: Definition, pct: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    metric = d.metrics[dim]
    if _substitute_community(c, d, dim):
        metric = d.metrics["community"]
    v = c.values.get(metric, NO_SOURCE)
    rec: dict[str, Any] = {"dimension": dim, "metric": metric, "floor": _r(floor)}
    rec["status"] = v.status
    if metric != d.metrics[dim]:
        rec["substituted_for"] = d.metrics[dim]
    if v.status == "not_applicable":
        skip = d.if_not_applicable.get(dim) == "skip" or (
            dim == "adoption" and d.no_measurable_adoption == "skip_threshold"
        )
        rec["result"] = "waived" if skip else "fail"
        rec["reason"] = "not_applicable"
        return rec
    if v.status != "observed" or v.value is None:
        rec["result"] = "undetermined"
        rec["reason"] = v.status if v.reason is None else f"{v.status}:{v.reason}"
        return rec
    p = pct.get(metric, {}).get(c.ref)
    rec["percentile"] = _r(p)
    if p is None:
        rec["result"] = "undetermined"
        rec["reason"] = "small_population"
    elif v.value <= 0:
        rec["result"] = "fail"
        rec["reason"] = "zero_floor"
    else:
        rec["result"] = "pass" if p >= floor - 1e-9 else "fail"
    return rec


def _business_check(c: CaseInput, signal: str, d: Definition) -> dict[str, Any]:
    v = c.business.get(signal, NO_SOURCE)
    rec: dict[str, Any] = {"dimension": "business", "metric": f"biz.{signal}", "status": v.status}
    if v.status != "observed" or v.value is None:
        rec["result"] = "undetermined"
        rec["reason"] = v.status if v.reason is None else f"{v.status}:{v.reason}"
    elif v.tag == "self_reported" and not d.accept_self_reported:
        rec["result"] = "undetermined"
        rec["reason"] = "self_reported_not_accepted"
    else:
        rec["result"] = "pass" if v.value > 0 else "fail"
    return rec


def _score(
    c: CaseInput, d: Definition, pct: Mapping[str, Mapping[str, float | None]]
) -> tuple[float | None, str]:
    def dim_pct(dim: str) -> float | None:
        metric = d.metrics["community"] if _substitute_community(c, d, dim) else d.metrics[dim]
        v = c.values.get(metric, NO_SOURCE)
        if v.status != "observed":
            return None
        return pct.get(metric, {}).get(c.ref)

    if d.rank_by is None and d.weights:
        total = 0.0
        for dim, w in sorted(d.weights.items()):
            p = dim_pct(dim)
            if p is None:
                return None, "composite"
            total += w * p
        return total, "composite"
    dim = d.rank_by or d.primary
    if dim == "business":
        v = c.values.get(BUSINESS_COUNT, NO_SOURCE)
        ok = v.status == "observed" and v.value is not None
        return (float(v.value) if ok and v.value is not None else None), BUSINESS_COUNT
    basis = d.metrics["community"] if _substitute_community(c, d, dim) else d.metrics[dim]
    return dim_pct(dim), basis


def evaluate(d: Definition, population: Sequence[CaseInput]) -> Evaluation:
    """Percentiles over `population` and each population case's qualification and score."""
    pct: dict[str, dict[str, float | None]] = {}
    pops: dict[str, dict[str, Any]] = {}
    for dim in sorted(d.metrics):
        metric = d.metrics[dim]
        if dim == "business":
            continue
        pct[metric], pops[metric] = percentiles(population, metric)
    qual: dict[str, Qual] = {}
    for c in population:
        checks = [
            _check(c, dim, fl, d, pct) for dim, fl in sorted(d.floors.items()) if fl is not None
        ]
        checks += [_business_check(c, s, d) for s in d.business_minimum]
        results = {x["result"] for x in checks}
        outcome: Outcome
        if "fail" in results:
            outcome = "fails"
        elif "undetermined" in results:
            outcome = "undetermined"
        else:
            outcome = "qualifies"
        flags: list[str] = []
        if _substitute_community(c, d, d.primary):
            flags.append("adoption_not_measurable_community_as_primary")
        if any(x["result"] == "waived" for x in checks):
            flags.append("threshold_waived_not_applicable")
        score, basis = _score(c, d, pct)
        qual[c.ref] = Qual(outcome, checks, score, basis, flags)
    return Evaluation(d, pct, pops, qual)


# --- selection at one widening level ------------------------------------------------------------
@dataclass(frozen=True)
class Pair:
    pair_id: int
    panel: Literal["field", "exemplar"]
    winner: str  # the winner, or the exemplar
    loser: str
    round: int
    distance: float | None
    diffs: Mapping[str, float | None]  # standardized difference per balance covariate
    excluded_on: tuple[str, ...]

    @property
    def headline(self) -> bool:
        return self.panel == "field" and not self.excluded_on


@dataclass
class Level:
    distance: int
    definition: Definition
    evaluation: Evaluation
    eligible: list[CaseInput]
    ranked: list[CaseInput]  # rankable qualifiers, in rank order
    unrankable: list[CaseInput]
    loser_pool: list[CaseInput]
    winners: list[CaseInput]
    pairs: list[Pair]
    sds: dict[str, float | None]

    def counts(self) -> dict[str, int]:
        q = self.evaluation.qual
        return {
            "eligible": len(self.eligible),
            "qualifiers": sum(1 for c in self.eligible if q[c.ref].outcome == "qualifies"),
            "rankable_qualifiers": len(self.ranked),
            "winners": len(self.winners),
            "loser_pool": len(self.loser_pool),
            "matched_losers": len(self.pairs),
            "undetermined": sum(1 for c in self.eligible if q[c.ref].outcome == "undetermined"),
        }


def _numeric_covariates(ctx: Context) -> list[str]:
    covs = ["lsm", "age_log10"]
    if "founder_audience_bucket" not in ctx.exact_match:
        covs.append("audience_band")
    if "launch_half_year" not in ctx.exact_match:
        covs.append("launch_quarter")
    return covs


CATEGORICAL = ("language",)
NUMERIC_ALL = ("lsm", "age_log10", "audience_band", "launch_quarter")


def _num(c: CaseInput, cov: str) -> float | None:
    x = c.covariates
    if cov == "audience_band":
        b = BAND_ORDINAL.get(x.audience_band)
        return None if b is None else float(b)
    v = getattr(x, cov)
    return None if v is None else float(v)


def _exact_key(c: CaseInput, key: str) -> Any:
    x = c.covariates
    return {
        "founder_audience_bucket": x.audience_band,
        "audience_bucket": x.audience_band,
        "launch_half_year": x.launch_half_year,
        "launch_period": x.launch_half_year,
        "launch_type": x.launch_type,
    }[key]


def _sd(values: Iterable[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return statistics.stdev(vs) if len(vs) >= 2 else None


def _std_diff(a: float | None, b: float | None, sd: float | None) -> float | None:
    if a is None or b is None:
        return None
    delta = abs(a - b)
    if not sd:
        return 0.0 if delta == 0 else math.inf
    return delta / sd


def _pair_diffs(
    w: CaseInput, lo: CaseInput, ctx: Context, sds: Mapping[str, float | None]
) -> tuple[dict[str, float | None], tuple[str, ...]]:
    """Standardized difference of one pair per balance covariate, and the covariates on which it
    exceeds `headline_exclusion_smd` (ADR-054.1; a language mismatch, or a language missing on
    either side, counts as 1, ADR-078)."""
    diffs: dict[str, float | None] = {}
    for cov in _numeric_covariates(ctx):
        diffs[cov] = _std_diff(_num(w, cov), _num(lo, cov), sds.get(cov))
    for cov in CATEGORICAL:  # a missing value on either side is a mismatch (ADR-078)
        a, b = getattr(w.covariates, cov), getattr(lo.covariates, cov)
        diffs[cov] = 0.0 if a is not None and a == b else 1.0
    excluded = tuple(
        k for k, v in sorted(diffs.items()) if v is not None and v > ctx.headline_exclusion_smd
    )
    return diffs, excluded


def _caliper_ok(w: CaseInput, lo: CaseInput, sds: Mapping[str, float | None]) -> bool:
    a, b = w.covariates.lsm, lo.covariates.lsm
    if a is None or b is None:
        return False
    sd = sds.get("lsm")
    if abs(a - b) > (LSM_CALIPER_SD * sd if sd else 0.0) + 1e-12:
        return False
    qa, qb = w.covariates.launch_quarter, lo.covariates.launch_quarter
    return qa is not None and qb is not None and abs(qa - qb) <= QUARTER_CALIPER


def _distance(w: CaseInput, lo: CaseInput, ctx: Context, sds: Mapping[str, float | None]) -> float:
    """outcome-model §5.6: standardized |Δ| per numeric covariate (a missing value costs one
    SD), + |Δquarter| + 1 if the languages differ or one is missing. Repo age at T
    (`age_log10`) is one of the numeric terms."""
    d = 0.0
    covs = ["lsm", "age_log10"]
    if "founder_audience_bucket" not in ctx.exact_match:
        covs.append("audience_band")
    for cov in covs:
        s = _std_diff(_num(w, cov), _num(lo, cov), sds.get(cov))
        d += 1.0 if s is None or math.isinf(s) else s
    qa, qb = w.covariates.launch_quarter, lo.covariates.launch_quarter
    d += abs(qa - qb) if qa is not None and qb is not None else 1.0
    la, lb = w.covariates.language, lo.covariates.language
    d += 0.0 if la is not None and la == lb else 1.0  # missing counts as different (ADR-078)
    return d


def match_losers(
    winners: Sequence[CaseInput],
    pool: Sequence[CaseInput],
    ctx: Context,
    *,
    prefer_headline: bool = True,
) -> tuple[list[Pair], dict[str, float | None]]:
    """R4.3 / ADR-054.1 nearest-neighbour matching without replacement, refined by ADR-078
    (before any outcome sort; outcome-model §5.6 allows it).

    Eligible losers are those with the same `exact_match` keys inside the calipers. Round 1:
    each winner in rank order takes one eligible loser, preferring one whose pair passes the
    headline rule (ADR-054.1), then the nearest by distance, then the hash. Further rounds (while
    fewer than `panel.losers` are matched) first hand out only headline-passing losers, then any
    eligible loser, so extra losers don't crowd out pairs that can be reported in the headline
    and every matchable winner still gets one loser first. `prefer_headline=False` is the
    selection-v1 rule (nearest only, every round), kept for the ADR-078 comparison."""
    everyone = [*winners, *pool]
    sds = {cov: _sd(_num(c, cov) for c in everyone) for cov in NUMERIC_ALL}
    available = sorted(pool, key=lambda c: c.ref)
    used: set[str] = set()
    pairs: list[Pair] = []
    groups: dict[str, int] = {}
    rnd = 1

    def one_round(headline_only: bool) -> bool:
        progress = False
        for w in winners:
            if len(pairs) >= ctx.losers:
                break
            best: tuple[tuple[int, float, str], CaseInput] | None = None
            for cand in available:
                if cand.ref in used:
                    continue
                if any(_exact_key(w, k) != _exact_key(cand, k) for k in ctx.exact_match):
                    continue
                if not _caliper_ok(w, cand, sds):
                    continue
                _, excl = _pair_diffs(w, cand, ctx, sds)
                if headline_only and excl:
                    continue
                miss = 1 if prefer_headline and excl else 0
                key = (miss, round(_distance(w, cand, ctx, sds), 12), ctx.tie(cand.ref))
                if best is None or key < best[0]:
                    best = (key, cand)
            if best is None:
                continue
            (_, dist, _), loser = best
            used.add(loser.ref)
            diffs, excluded = _pair_diffs(w, loser, ctx, sds)
            gid = groups.setdefault(w.ref, len(groups) + 1)  # a winner and its losers
            pairs.append(Pair(gid, "field", w.ref, loser.ref, rnd, dist, diffs, excluded))
            progress = True
        return progress

    one_round(headline_only=False)  # round 1: every matchable winner gets a loser first
    for headline_only in (True, False) if prefer_headline else (False,):
        while len(pairs) < ctx.losers:
            rnd += 1
            if not one_round(headline_only):
                rnd -= 1  # nothing matched in this round: its number is reused
                break
    return pairs, sds


def select_level(cases: Sequence[CaseInput], ev: Evaluation, distance: int, ctx: Context) -> Level:
    q = ev.qual
    eligible = [
        c
        for c in cases
        if c.ref in q and c.panel in ("field", "reference") and c.distance <= distance
    ]
    quals = [c for c in eligible if q[c.ref].outcome == "qualifies"]
    ranked = sorted(
        (c for c in quals if q[c.ref].score is not None),
        key=lambda c: (-(q[c.ref].score or 0.0), ctx.tie(c.ref)),
    )
    unrankable = [c for c in quals if q[c.ref].score is None]
    winners = ranked[: ctx.winners]
    pool = [c for c in eligible if q[c.ref].outcome == "fails"]
    pairs, sds = match_losers(winners, pool, ctx)
    return Level(
        distance, ev.definition, ev, eligible, ranked, unrankable, pool, winners, pairs, sds
    )


def apply_step(d: Definition, step: str) -> Definition:
    """ADR-053.2 fallback steps (validated against the brief by `Success`)."""
    floors = dict(d.floors)
    if step == "relax_primary_to_top_third":
        floors[d.primary] = THRESHOLD_PERCENTILE["top_third"]
        return replace(d, floors=floors)
    dim = step.removeprefix("drop_").removesuffix("_minimum")
    if dim == "business":
        return replace(d, business_minimum=())
    floors.pop(dim, None)
    return replace(d, floors=floors)


# --- exemplar panel (ADR-057.1) -----------------------------------------------------------------
def match_exemplars(
    exemplars: Sequence[CaseInput], pool: Sequence[CaseInput], ctx: Context, first_id: int
) -> tuple[list[Pair], list[dict[str, Any]]]:
    """1-2 losers per exemplar, exactly matched on `match_on` (never on field), nearest LSM
    first, ties by the hash; losers are not reused."""
    used: set[str] = set()
    pairs: list[Pair] = []
    log: list[dict[str, Any]] = []
    for ex in sorted(
        exemplars, key=lambda c: (c.named_index if c.named_index is not None else 99, c.ref)
    ):
        entry: dict[str, Any] = {"named_index": ex.named_index, "matched": 0}
        if ex.anchor is None:
            entry["reason"] = "no_anchor"
            log.append(entry)
            continue
        options = [
            c
            for c in sorted(pool, key=lambda c: c.ref)
            if c.ref not in used
            and all(_exact_key(ex, k) == _exact_key(c, k) for k in ctx.exemplar_match_on)
        ]

        def gap(c: CaseInput, ex: CaseInput = ex) -> float:
            a, b = ex.covariates.lsm, c.covariates.lsm
            return math.inf if a is None or b is None else abs(a - b)

        options.sort(key=lambda c: (gap(c), ctx.tie(c.ref)))
        gid = first_id + len({p.winner for p in pairs})  # an exemplar and its losers
        for c in options[: ctx.losers_per_exemplar]:
            used.add(c.ref)
            g = gap(c)
            pairs.append(
                Pair(
                    gid,
                    "exemplar",
                    ex.ref,
                    c.ref,
                    1,
                    None if math.isinf(g) else g,
                    {"lsm_abs": None if math.isinf(g) else g},
                    (),
                )
            )
            entry["matched"] += 1
        if entry["matched"] < ctx.losers_per_exemplar:
            entry["reason"] = "too_few_exact_matches"
        log.append(entry)
    return pairs, log


# --- balance diagnostics (§5.6, §9.2) -----------------------------------------------------------
def _var(xs: Sequence[float]) -> float:
    return statistics.variance(xs) if len(xs) >= 2 else 0.0


def smd_numeric(w: Sequence[float], lo: Sequence[float]) -> dict[str, Any]:
    if not w or not lo:
        return {"smd": None, "variance_ratio": None, "n_winners": len(w), "n_losers": len(lo)}
    mw, ml = statistics.fmean(w), statistics.fmean(lo)
    vw, vl = _var(w), _var(lo)
    pooled = math.sqrt((vw + vl) / 2)
    smd = (mw - ml) / pooled if pooled > 0 else (0.0 if mw == ml else None)
    return {
        "smd": _r(smd),
        "variance_ratio": _r(vw / vl) if vl > 0 else None,
        "mean_winners": _r(mw),
        "mean_losers": _r(ml),
        "n_winners": len(w),
        "n_losers": len(lo),
    }


def smd_categorical(w: Sequence[str], lo: Sequence[str]) -> dict[str, Any]:
    if not w or not lo:
        return {"smd": None, "levels": {}, "n_winners": len(w), "n_losers": len(lo)}
    levels: dict[str, Any] = {}
    worst = 0.0
    for lv in sorted(set(w) | set(lo)):
        pw, pl = w.count(lv) / len(w), lo.count(lv) / len(lo)
        den = math.sqrt((pw * (1 - pw) + pl * (1 - pl)) / 2)
        s = (pw - pl) / den if den > 0 else (0.0 if pw == pl else None)
        levels[lv] = {"p_winners": _r(pw), "p_losers": _r(pl), "smd": _r(s)}
        worst = math.inf if s is None else max(worst, abs(s))
    return {
        "smd": None if math.isinf(worst) else _r(worst),
        "levels": levels,
        "n_winners": len(w),
        "n_losers": len(lo),
        "form": "max |SMD| over levels",
    }


def _cov_balance(ws: Sequence[CaseInput], ls: Sequence[CaseInput], ctx: Context) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cov in _numeric_covariates(ctx):
        wv = [v for c in ws if (v := _num(c, cov)) is not None]
        lv = [v for c in ls if (v := _num(c, cov)) is not None]
        out[cov] = smd_numeric(wv, lv)
    for cov in CATEGORICAL:
        wc = [str(getattr(c.covariates, cov)) for c in ws if getattr(c.covariates, cov)]
        lc = [str(getattr(c.covariates, cov)) for c in ls if getattr(c.covariates, cov)]
        out[cov] = smd_categorical(wc, lc)
    for rec in out.values():
        s = rec["smd"]
        rec["meets_target"] = None if s is None else abs(s) < ctx.smd_target
        rec["label"] = "balance_limited" if rec["meets_target"] is False else None
    return out


def balance(lvl: Level, by_ref: Mapping[str, CaseInput], ctx: Context) -> dict[str, Any]:
    pairs = [p for p in lvl.pairs if p.panel == "field"]
    matched_w = [by_ref[r] for r in sorted({p.winner for p in pairs})]
    matched_l = [by_ref[p.loser] for p in pairs]
    exact: dict[str, int] = {}
    for k in ctx.exact_match:
        exact[k] = sum(
            1 for p in pairs if _exact_key(by_ref[p.winner], k) != _exact_key(by_ref[p.loser], k)
        )
    excluded_by: dict[str, int] = {}
    for p in pairs:
        for k in p.excluded_on:
            excluded_by[k] = excluded_by.get(k, 0) + 1
    after = _cov_balance(matched_w, matched_l, ctx)
    return {
        "form": "standardized mean difference, pooled SD; variance ratio; no p-values",
        "winner_counting": "after matching, each matched winner counts once, even when it has "
        "two losers (unweighted, ADR-078)",
        "target": ctx.smd_target,
        "after_matching": after,
        "before_matching": _cov_balance(lvl.winners, lvl.loser_pool, ctx),
        "covariates_missing_target": sorted(
            k for k, v in after.items() if v["meets_target"] is False
        ),
        "exact_match": {
            "keys": list(ctx.exact_match),
            "violations": exact,
            "ok": all(v == 0 for v in exact.values()),
        },
        "pairs": len(pairs),
        "headline_pairs": sum(1 for p in pairs if p.headline),
        "headline_excluded": {
            "rule": f"a pair differing by > {ctx.headline_exclusion_smd:g} SD on any covariate "
            "(a language mismatch or a missing language counts as 1) is shown only in the "
            "case-level view (ADR-054.1, ADR-078)",
            "pairs": sum(1 for p in pairs if not p.headline),
            "by_covariate": dict(sorted(excluded_by.items())),
        },
        "unmatched_winners": sum(1 for c in lvl.winners if c.ref not in {p.winner for p in pairs}),
        "sd": {k: _r(v) for k, v in sorted(lvl.sds.items())},
    }


# --- sensitivity (§8, R4.9) ---------------------------------------------------------------------
def _ladder_step(floor: float, direction: int) -> tuple[bool, float | None]:
    """One step looser (-1) or tighter (+1) on {none, 25, 50, 75, 90, 95}; `top_third` sits
    between 50 and 75. Returns (possible, new floor)."""
    numeric = [x for x in LADDER if x is not None]
    if direction < 0:
        below = [x for x in numeric if x < floor - 1e-9]
        return True, (below[-1] if below else None)
    above = [x for x in numeric if x > floor + 1e-9]
    return (True, above[0]) if above else (False, None)


def alternatives(
    d: Definition, ctx: Context, base: Definition | None = None
) -> tuple[list[tuple[str, Definition, bool]], list[dict[str, Any]]]:
    """(key, definition, exclude_flagged) per alternative that runs, plus notes for the ones that
    don't apply (§8.1), each with its reason. `base` is the brief's own definition: a minimum a
    fallback step dropped (ADR-053.2) gets a note saying so instead of silently disappearing
    (M22 verifier round 2)."""
    runs: list[tuple[str, Definition, bool]] = []
    notes: list[dict[str, Any]] = []
    if "band_shift" in ctx.sensitivity and base is not None:
        for dim, fl in sorted(base.floors.items()):
            if fl is not None and d.floors.get(dim) is None:
                notes.append(
                    {
                        "key": f"band_shift:{dim}",
                        "ran": False,
                        "reason": f"not applicable: the {dim} minimum was dropped by the fallback "
                        f"step drop_{dim}_minimum (ADR-053.2)",
                    }
                )
    if "primary_swap" in ctx.sensitivity:
        ranked_on = d.rank_by or d.primary
        for other in RANKABLE:
            if other != ranked_on:
                runs.append((f"primary_swap:{other}", replace(d, rank_by=other), False))
    if "band_shift" in ctx.sensitivity:
        numeric = {k: v for k, v in sorted(d.floors.items()) if v is not None}
        loose_all: dict[str, float | None] = dict(d.floors)
        tight_all: dict[str, float | None] = dict(d.floors)
        for dim, fl in numeric.items():
            for name, direction in (("looser", -1), ("tighter", 1)):
                ok, new = _ladder_step(fl, direction)
                if not ok:
                    notes.append(
                        {
                            "key": f"band_shift:{dim}:{name}",
                            "ran": False,
                            "reason": "not applicable: already at the top of the ladder",
                        }
                    )
                    continue
                runs.append(
                    (f"band_shift:{dim}:{name}", replace(d, floors={**d.floors, dim: new}), False)
                )
                (loose_all if direction < 0 else tight_all)[dim] = new
        if numeric:
            runs.append(("band_shift:all:looser", replace(d, floors=loose_all), False))
            if tight_all != dict(d.floors):
                runs.append(("band_shift:all:tighter", replace(d, floors=tight_all), False))
        else:
            notes.append(
                {
                    "key": "band_shift",
                    "ran": False,
                    "reason": "not applicable: no percentile threshold in the final definition",
                }
            )
    if "weights" in ctx.sensitivity:
        if d.weights:
            runs.append(("weights:primary_only", replace(d, weights=None), False))
            eq = {k: 1.0 / len(d.weights) for k in sorted(d.weights)}
            runs.append(("weights:equal", replace(d, weights=eq), False))
        else:
            notes.append(
                {
                    "key": "weights",
                    "ran": False,
                    "reason": "not applicable: the brief uses no weights",
                }
            )
    if "fake_star_filter" in ctx.sensitivity:
        if d.star_metric_used():
            runs.append(("exclude_anomaly_flagged", d, True))
        else:
            notes.append(
                {
                    "key": "exclude_anomaly_flagged",
                    "ran": False,
                    "reason": "D not applicable: no star metric in the success definition",
                }
            )
    return runs, notes


def _status(c: CaseInput, ev: Evaluation, winners: set[str], excluded: set[str]) -> str:
    if c.ref in excluded:
        return "excluded_anomaly_flagged"
    if c.ref in winners:
        return "winner"
    q = ev.qual.get(c.ref)
    if q is None:
        return "not_in_population"
    if q.outcome == "qualifies":
        return "qualifier" if q.score is not None else "unrankable"
    return "non_qualifier" if q.outcome == "fails" else "undetermined"


def jaccard(a: set[str], b: set[str]) -> float | None:
    u = a | b
    return None if not u else len(a & b) / len(u)


def sensitivity(
    cases: Sequence[CaseInput],
    population: Sequence[CaseInput],
    lvl: Level,
    ctx: Context,
    base: Definition | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Summary (counts only) and per-case statuses and flags (§8.2, §8.3)."""
    base_w = {c.ref for c in lvl.winners}
    base_l = {p.loser for p in lvl.pairs if p.panel == "field"}
    per_case: dict[str, dict[str, Any]] = {
        r: {"status": {}, "flags": [], "changed_by": []} for r in sorted(base_w | base_l)
    }
    by_ref = {c.ref: c for c in cases}
    runs, notes = alternatives(lvl.definition, ctx, base)
    results: list[dict[str, Any]] = list(notes)
    overlaps: list[float] = []
    stable = set(base_w)
    for key, d, exclude in runs:
        if d.rank_by is not None and d.rank_by != "business":
            metric = d.metrics[d.rank_by]
            if not any(c.values.get(metric, NO_SOURCE).status == "observed" for c in population):
                results.append(
                    {
                        "key": key,
                        "ran": False,
                        "reason": f"not applicable: no observed {metric} value in the "
                        "population (no connector or no data)",
                    }
                )
                continue
        excluded = {c.ref for c in population if exclude and c.star_anomaly_flag == "true"}
        if exclude and not excluded:
            results.append(
                {"key": key, "ran": False, "reason": "not applicable: no flagged candidates"}
            )
            continue
        pop = [c for c in population if c.ref not in excluded]
        ev = evaluate(d, pop)
        alt = select_level(pop, ev, lvl.distance, replace(ctx, losers=0))
        wins = {c.ref for c in alt.winners}
        j = jaccard(base_w, wins)
        if j is not None:
            overlaps.append(j)
        stable &= wins
        results.append(
            {
                "key": key,
                "ran": True,
                "qualifiers": alt.counts()["qualifiers"],
                "rankable_qualifiers": len(alt.ranked),
                "winners": len(wins),
                "jaccard": _r(j),
                "left_baseline": len(base_w - wins),
                "joined": len(wins - base_w),
                "excluded_anomaly_flagged": len(excluded),
            }
        )
        for r, rec in per_case.items():
            st = _status(by_ref[r], ev, wins, excluded)
            rec["status"][key] = st
            changed = (r in base_w and st != "winner") or (
                r in base_l and st in ("winner", "qualifier", "unrankable")
            )
            if st == "excluded_anomaly_flagged":
                if "excluded_anomaly_flagged" not in rec["flags"]:
                    rec["flags"].append("excluded_anomaly_flagged")
            elif changed:
                rec["changed_by"].append(key)
                flag = "sensitive_to_star_anomaly" if exclude else "definition_sensitive"
                if flag not in rec["flags"]:
                    rec["flags"].append(flag)
    ran = [r for r in results if r.get("ran")]
    summary = {
        "alternatives": results,
        "ran": len(ran),
        "min_jaccard": _r(min(overlaps)) if overlaps else None,
        "mean_jaccard": _r(statistics.fmean(overlaps)) if overlaps else None,
        "share_winners_stable": (_r(len(stable) / len(base_w)) if base_w and ran else None),
        "definition_sensitive": sum(
            1 for v in per_case.values() if "definition_sensitive" in v["flags"]
        ),
        "sensitive_to_star_anomaly": sum(
            1 for v in per_case.values() if "sensitive_to_star_anomaly" in v["flags"]
        ),
        "excluded_anomaly_flagged": sum(
            1 for v in per_case.values() if "excluded_anomaly_flagged" in v["flags"]
        ),
        "note": "descriptive; losers are not re-matched; the baseline winner set never changes",
    }
    for v in per_case.values():
        v["flags"].sort()
    return summary, per_case


# --- the whole selection ------------------------------------------------------------------------
@dataclass
class Selection:
    params: dict[str, Any]
    base_definition: Definition
    level: Level
    steps: list[dict[str, Any]]
    exemplar_pairs: list[Pair]
    exemplar_log: list[dict[str, Any]]
    cases: list[dict[str, Any]]
    summary: dict[str, Any]
    balance: dict[str, Any]
    sensitivity: dict[str, Any]
    inputs_hash: str
    result_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "summary": self.summary,
            "balance": self.balance,
            "sensitivity": self.sensitivity,
            "cases": self.cases,
            "inputs_hash": self.inputs_hash,
            "result_hash": self.result_hash,
        }


def _r(x: float | None) -> float | None:
    if x is None:
        return None
    if math.isinf(x) or math.isnan(x):
        return None
    return round(float(x), ROUND)


def inputs_hash(cases: Sequence[CaseInput]) -> str:
    """SHA-256 of the canonical inputs (order-independent)."""
    rows = []
    for c in sorted(cases, key=lambda c: c.ref):
        rows.append(
            {
                "ref": c.ref,
                "panel": c.panel,
                "distance": c.distance,
                "named_index": c.named_index,
                "anchor": None if c.anchor is None else c.anchor.to_dict(),
                "anchor_reason": c.anchor_reason,
                "values": {k: v.to_dict() for k, v in sorted(c.values.items())},
                "business": {k: v.to_dict() for k, v in sorted(c.business.items())},
                "covariates": c.covariates.to_dict(),
                "star_anomaly_flag": c.star_anomaly_flag,
            }
        )
    return sha256_json(rows)


def _step_entry(step: str, lvl: Level, **extra: Any) -> dict[str, Any]:
    return {
        "step": step,
        "distance": lvl.distance,
        "floors": {d: _r(v) for d, v in sorted(lvl.definition.floors.items())},
        "business_minimum": list(lvl.definition.business_minimum),
        **lvl.counts(),
        **extra,
    }


def _anchor_type(c: CaseInput) -> str | None:
    return None if c.anchor is None else c.anchor.type


def select(
    cases_in: Iterable[CaseInput],
    ctx: Context,
    base: Definition,
    *,
    notes: Sequence[str] = (),
) -> Selection:
    """Outcome sort, winners, matched losers, exemplar losers, balance and sensitivity.
    `notes` are warnings from the input stage (for example a launch lookup that could not run),
    reported with the selection's own."""
    cases = sorted(cases_in, key=lambda c: c.ref)
    refs = [c.ref for c in cases]
    if len(set(refs)) != len(refs):
        raise SelectionError("a candidate appears twice in the selection input")
    by_ref = {c.ref: c for c in cases}
    population = [c for c in cases if c.panel in ("field", "reference") and c.anchor is not None]

    # R4.10 then ADR-053.2: every step is logged with its counts (R4.10, ADR-055.5)
    d = base
    lvl = select_level(population, evaluate(d, population), 0, ctx)
    steps = [_step_entry("baseline", lvl, rule="brief success definition, core field (R4.8)")]

    def short(level: Level) -> bool:
        return len(level.winners) < WINNERS_MIN or len(level.pairs) < LOSERS_MIN

    while short(lvl):
        if lvl.distance >= ctx.max_distance:
            if ctx.max_distance == 0:
                steps.append(
                    {
                        "step": "widen",
                        "applied": False,
                        "rule": "R4.10",
                        "reason": "the brief declares no widening steps",
                    }
                )
            break
        reason = (
            f"fewer than {WINNERS_MIN} winners or matched losers "
            f"({len(lvl.winners)}, {len(lvl.pairs)})"
        )
        before = len(lvl.eligible)
        lvl = select_level(population, lvl.evaluation, lvl.distance + 1, ctx)
        added = len(lvl.eligible) - before
        steps.append(
            _step_entry(
                f"widen_to_distance_{lvl.distance}",
                lvl,
                rule="R4.10",
                reason=reason,
                added=added,
                applied=added > 0,
            )
        )
    pending = list(ctx.fallback_steps)
    while len(lvl.ranked) < ctx.min_winners and pending:
        step = pending.pop(0)
        reason = f"fewer than {ctx.min_winners} rankable qualifiers ({len(lvl.ranked)})"
        d = apply_step(lvl.definition, step)
        lvl = select_level(population, evaluate(d, population), lvl.distance, ctx)
        steps.append(_step_entry(step, lvl, rule="ADR-053.2", reason=reason))

    winners = {c.ref for c in lvl.winners}
    field_losers = {p.loser for p in lvl.pairs}
    q = lvl.evaluation.qual
    exemplars = [c for c in cases if c.panel == "exemplar"]
    ex_pool = [
        c
        for c in population
        if q[c.ref].outcome == "fails" and c.ref not in winners and c.ref not in field_losers
    ]
    first_ex = max((p.pair_id for p in lvl.pairs), default=0) + 1
    ex_pairs, ex_log = match_exemplars(exemplars, ex_pool, ctx, first_ex)
    sens_summary, sens_cases = sensitivity(cases, population, lvl, ctx, base)

    rank = {c.ref: i + 1 for i, c in enumerate(lvl.ranked)}
    eligible = {c.ref for c in lvl.eligible}
    unrankable = {c.ref for c in lvl.unrankable}
    # one matched set per winner (or exemplar): the winner row names the set and its size, each
    # loser row carries its own distance and standardized differences to that winner
    # Each side's anchor type is stored so that the report can restrict H1-type contrasts
    # (MC-01) to launch-anchored pairs (ADR-081): losers need a declared launch, winners don't.
    pair_of: dict[str, dict[str, Any]] = {}
    for p in [*lvl.pairs, *ex_pairs]:
        wt, lt = _anchor_type(by_ref[p.winner]), _anchor_type(by_ref[p.loser])
        head = pair_of.setdefault(
            p.winner,
            {
                "pair_id": p.pair_id,
                "panel": p.panel,
                "side": "winner" if p.panel == "field" else "exemplar",
                "losers": 0,
                "anchor_type": wt,
            },
        )
        head["losers"] += 1
        pair_of[p.loser] = {
            "pair_id": p.pair_id,
            "panel": p.panel,
            "side": "loser",
            "anchor_type": lt,
            "anchor_types": {"winner": wt, "loser": lt},
            "launch_anchored": wt == "launch" and lt == "launch",
            "round": p.round,
            "distance": _r(p.distance),
            "diffs": {k: _r(v) for k, v in sorted(p.diffs.items())},
            "headline": p.headline if p.panel == "field" else None,
            "excluded_on": list(p.excluded_on),
        }
    ex_losers = {p.loser for p in ex_pairs}
    out_cases: list[dict[str, Any]] = []
    roles: dict[str, int] = dict.fromkeys(ROLES, 0)
    for c in cases:
        role: Role
        if c.panel == "exemplar":
            role = "exemplar"
        elif c.anchor is None:
            role = "no_anchor"
        elif c.ref in winners:
            role = "winner"
        elif c.ref in field_losers:
            role = "matched_loser"
        elif c.ref in ex_losers:
            role = "exemplar_matched_loser"
        elif c.ref not in eligible:
            role = "outside_widening"
        elif c.ref in unrankable:
            role = "unrankable"
        elif q[c.ref].outcome == "qualifies":
            role = "qualified_not_selected"
        elif q[c.ref].outcome == "fails":
            role = "loser_pool_unmatched"
        else:
            role = "undetermined"
        roles[role] += 1
        qc = q.get(c.ref)
        out_cases.append(
            {
                "candidate_ref": c.ref,
                "panel": c.panel,
                "distance": c.distance,
                "named_index": c.named_index,
                "is_reference": c.is_reference,
                "role": role,
                "rank": rank.get(c.ref) if c.ref in eligible else None,
                "score": _r(qc.score) if qc else None,
                "rank_basis": qc.rank_basis if qc else None,
                "anchor": None if c.anchor is None else c.anchor.to_dict(),
                "anchor_reason": c.anchor_reason,
                "values": _values_out(c, lvl.evaluation),
                "qualification": None
                if qc is None
                else {"outcome": qc.outcome, "checks": qc.checks, "flags": qc.flags},
                "covariates": c.covariates.to_dict(),
                "star_anomaly": {"flag": c.star_anomaly_flag, **dict(c.anomaly)},
                "pair": pair_of.get(c.ref),
                "sensitivity": sens_cases.get(c.ref),
            }
        )

    warnings: list[str] = list(notes)
    field_cases = [c for c in cases if c.panel != "exemplar"]
    no_anchor = sum(1 for c in field_cases if c.anchor is None)
    if no_anchor:
        warnings.append(f"no anchor: {no_anchor} of {len(field_cases)} shortlisted")
    ev = lvl.evaluation
    dims_used = [
        d for d in RANKABLE if d in lvl.definition.floors or d in (lvl.definition.weights or {})
    ]
    if lvl.definition.primary in RANKABLE and lvl.definition.primary not in dims_used:
        dims_used.append(lvl.definition.primary)
    for dim in dims_used:
        info = ev.populations.get(lvl.definition.metrics[dim])
        if info is None:
            continue
        ns = dict(info["n_by_group"]) or {"all": 0}
        for g, n in sorted(ns.items()):
            if n < MIN_POPULATION:
                label = dim if g == "all" else f"{dim} ({g})"
                warnings.append(
                    f"{label} population {n} < {MIN_POPULATION} (minimum): no percentiles"
                )
    if len(lvl.winners) < WINNERS_MIN:
        warnings.append(f"fewer winners than the minimum ({WINNERS_MIN}): {len(lvl.winners)}")
    if len(lvl.ranked) < ctx.winners:
        warnings.append(
            f"fewer rankable qualifiers ({len(lvl.ranked)}) than panel.winners ({ctx.winners}); "
            "all of them are winners"
        )
    if len(lvl.pairs) < min(ctx.losers, len(lvl.winners)):
        warnings.append(f"only {len(lvl.pairs)} matched losers for {len(lvl.winners)} winners")
    if len(steps) > 1 and any(s.get("applied", True) for s in steps[1:]):
        warnings.append(
            "the panel was widened or the thresholds relaxed (see steps); core-field findings "
            "are reported separately (R4.10)"
        )
    affected = {
        dim: sum(
            1
            for c in population
            if any(
                x["dimension"] == dim and x["result"] == "undetermined"
                for x in ev.qual[c.ref].checks
            )
        )
        for dim in DIMENSIONS
    }
    summary = {
        "brief_id": ctx.brief_id,
        "brief_version": ctx.brief_version,
        "shortlist_n": len(cases),
        "reference_population_n": len(population),
        "no_anchor": no_anchor,
        "anchors": _count(
            "none" if c.anchor is None else f"{c.anchor.type}:{c.anchor.via or c.anchor.source}"
            for c in field_cases
        ),
        "pairs_by_anchor": _count(
            f"{_anchor_type(by_ref[p.winner])}/{_anchor_type(by_ref[p.loser])}" for p in lvl.pairs
        ),
        "headline_pairs_launch_anchored": sum(
            1
            for p in lvl.pairs
            if p.headline
            and _anchor_type(by_ref[p.winner]) == "launch"
            and _anchor_type(by_ref[p.loser]) == "launch"
        ),
        "roles": {k: v for k, v in roles.items() if v},
        "final_distance": lvl.distance,
        "core_field_only": all(by_ref[r].distance == 0 for r in winners | field_losers),
        "base_definition": base.to_dict(),
        "final_definition": lvl.definition.to_dict(),
        "steps": steps,
        "counts": lvl.counts(),
        "undetermined_by_dimension": affected,
        "populations": ev.populations,
        "reference_cases": {
            "n": sum(1 for c in cases if c.is_reference),
            "roles": _count(r["role"] for r in out_cases if r["is_reference"]),
        },
        "exemplars": {
            "n": len(exemplars),
            "pairs": len(ex_pairs),
            "log": ex_log,
        },
        "star_anomaly": {
            "label": "unfiltered, anomaly-checked",
            "winners": _count(by_ref[r].star_anomaly_flag for r in sorted(winners)),
            "matched_losers": _count(by_ref[r].star_anomaly_flag for r in sorted(field_losers)),
        },
        "warnings": warnings,
    }
    sel = Selection(
        params=ctx.params(),
        base_definition=base,
        level=lvl,
        steps=steps,
        exemplar_pairs=ex_pairs,
        exemplar_log=ex_log,
        cases=out_cases,
        summary=summary,
        balance=balance(lvl, by_ref, ctx),
        sensitivity=sens_summary,
        inputs_hash=inputs_hash(cases),
    )
    sel.result_hash = sha256_json({k: v for k, v in sel.to_dict().items() if k != "result_hash"})
    return sel


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _values_out(c: CaseInput, ev: Evaluation) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric, v in sorted(c.values.items()):
        rec = v.to_dict()
        if metric in ev.pct:
            rec["percentile"] = _r(ev.pct[metric].get(c.ref))
        out[metric] = rec
    for s, v in sorted(c.business.items()):
        out[f"biz.{s}"] = v.to_dict()
    return out
