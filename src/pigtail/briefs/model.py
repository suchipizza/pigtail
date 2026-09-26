"""Brief schema v1: the pydantic model behind `schemas/brief/v1.json` (PRD R18.1, ADR-053,
ADR-054, outcome-model v2 §5).

Every field of PRD R18.1 is here, plus the owner decisions that became brief options:

- `project`: name, description, target users, business model (R18.1).
- `field`: core field, include/exclude boundaries, seed projects, named reference cases (R4.11),
  qualitative reference models, and the widening steps to adjacent fields (R4.10, ADR-054.2).
- `window`: discovery and backfill window, 12–18 months (R18.1, ADR-047.1).
- `success`: one primary dimension, its threshold, minimum thresholds on the others, the
  advanced weights option (§5.3), per-dimension metric and `if_not_applicable` (outcome-model
  §5.1, open issue O17), and the fallbacks (ADR-053.2).
- `own_audience`: the user's own audience per channel, as reach bands only (CB §4.5).
- `channels`, `geography`.
- `panel`: winners and losers (15–25 each), exact-match characteristics and balance targets
  (R4.3, R4.8, ADR-054.1).
- `budget`: `money_usd` (non-LLM paid services, default 0), `subscription_share` (default 0.5)
  and the LLM backend, which is never switched automatically (ADR-053.1).
- `optional_sources`: paid or not-yet-cleared sources, all off by default (ADR-053.1).
- `expansion`: the LLM expansion as edited by the user (R18.7).
- Metadata: schema version, brief id, version, supersedes, created and edited times (R18.1).

Cross-field rules that JSON Schema can't express are enforced by the model validators and
listed in the schema file under `x-cross-field-rules`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

SCHEMA_VERSION: Literal["brief/v1"] = "brief/v1"
SCHEMA_ID = "https://github.com/suchipizza/pigtail/schemas/brief/v1.json"

Dimension = Literal["attention", "adoption", "community", "business"]
RankableDimension = Literal["attention", "adoption", "community"]
DIMENSIONS: tuple[Dimension, ...] = ("attention", "adoption", "community", "business")
RANKABLE: tuple[RankableDimension, ...] = ("attention", "adoption", "community")

# Percentile floors within the brief's shortlist (outcome-model §5.3). `top_third` is ADR-053.2's
# first fallback step; the other values sit on the band ladder {25, 50, 75, 90}.
Threshold = Literal[
    "none", "at_least_p25", "at_least_median", "top_third", "top_quartile", "top_decile"
]
THRESHOLD_PERCENTILE: dict[str, float | None] = {
    "none": None,
    "at_least_p25": 25.0,
    "at_least_median": 50.0,
    "top_third": 200.0 / 3.0,
    "top_quartile": 75.0,
    "top_decile": 90.0,
}

# Business thresholds are sets of verified signals, not percentiles (outcome-model §5.2).
BusinessSignal = Literal["pricing_page", "hiring_hn_posts", "careers_roles"]

# Allowed metric per dimension (outcome-model §5.2); the first entry is the default.
METRICS: dict[str, tuple[str, ...]] = {
    "attention": ("att.stars@30", "att.stars@90", "att.hn_points"),
    "adoption": (
        "adopt.downloads@90",
        "adopt.downloads@365",
        "adopt.dependents@90",
        "adopt.dependents@365",
    ),
    "community": (
        "comm.returning_external_contributors@90",
        "comm.returning_external_contributors@365",
        "comm.external_authors@90",
    ),
    "business": ("biz.verified_signal_count@365",),
}

FallbackStep = Literal[
    "relax_primary_to_top_third",
    "drop_attention_minimum",
    "drop_adoption_minimum",
    "drop_community_minimum",
    "drop_business_minimum",
]
Sensitivity = Literal["primary_swap", "band_shift", "weights", "fake_star_filter"]

# Reach bands (codebook §4.5, CB-10): an audience is stored only as a band, never as a count.
AudienceBand = Literal["none", "r1", "r2", "r3", "r4", "unknown"]
ExactMatch = Literal["founder_audience_bucket", "launch_half_year"]
PAID_SOURCES: tuple[str, ...] = ("trendshift", "x", "bigquery")

Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Text = Annotated[str, Field(min_length=1, max_length=500)]
BriefId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")]
Language = Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")]
Topic = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,49}$")]

METADATA_FIELDS = frozenset(
    {"schema_version", "brief_id", "version", "supersedes", "created_at", "edited_at"}
)


def band_for_count(n: int) -> str:
    """CB-10 reach band for a follower count (the count itself is discarded)."""
    if n <= 0:
        return "none"
    if n < 1_000:
        return "r1"
    if n < 10_000:
        return "r2"
    if n < 100_000:
        return "r3"
    return "r4"


def _none_to_list(v: Any) -> Any:
    return [] if v is None else v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TargetUsers(_Strict):
    primary: Text
    secondary: Text | None = None


class Project(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(min_length=10, max_length=2000)]
    target_users: TargetUsers
    business_model: Text
    context: Annotated[str, Field(max_length=2000)] | None = None


class FieldBoundaries(_Strict):
    """R18.1 `field`, R4.10 widening, R4.11 reference cases."""

    core_field: Text
    include: Annotated[list[Text], Field(min_length=1, max_length=30)]
    exclude: Annotated[list[Text], Field(max_length=30)] = []
    seed_projects: Annotated[list[Text], Field(max_length=50)] = []
    reference_cases: Annotated[list[Text], Field(max_length=10)] = []
    reference_models: Annotated[list[Text], Field(max_length=10)] = []
    widening_steps: Annotated[list[Text], Field(max_length=5)] = []

    _lists = field_validator(
        "include",
        "exclude",
        "seed_projects",
        "reference_cases",
        "reference_models",
        "widening_steps",
        mode="before",
    )(_none_to_list)

    @model_validator(mode="after")
    def _no_overlap(self) -> FieldBoundaries:
        both = {s.lower() for s in self.include} & {s.lower() for s in self.exclude}
        if both:
            raise ValueError(f"include and exclude both list {sorted(both)}")
        return self


class Window(_Strict):
    """Discovery and backfill window (R18.1, ADR-047.1: 12–18 months)."""

    months: Annotated[int, Field(ge=12, le=18)] = 18
    end: date | None = None  # default: the run date, recorded in the run's provenance


class TooFewWinners(_Strict):
    """ADR-053.2: if fewer than `min_winners` qualify, apply `steps` in order."""

    min_winners: Annotated[int, Field(ge=1, le=25)] = 10
    steps: list[FallbackStep] = ["relax_primary_to_top_third", "drop_community_minimum"]
    log_each_step: Literal[True] = True
    report_sensitivity_check: Literal[True] = True


class Fallbacks(_Strict):
    # ADR-053.2: a comparable with no registry downloads and no dependents.
    no_measurable_adoption: Literal[
        "use_community_as_primary_and_flag", "fail_threshold", "skip_threshold"
    ] = "use_community_as_primary_and_flag"
    too_few_winners: TooFewWinners = TooFewWinners()


class Success(_Strict):
    """Success definition (R18.8, outcome-model v2 §5–6, ADR-053.2)."""

    primary: Dimension
    primary_threshold: Threshold = "top_quartile"
    minimums: dict[RankableDimension, Threshold] = {}
    business_minimum: list[BusinessSignal] = []
    accept_self_reported: bool = False
    metrics: dict[Dimension, str] = {}
    if_not_applicable: dict[Dimension, Literal["fail", "skip"]] = {}
    weights: dict[RankableDimension, Annotated[float, Field(ge=0, le=1)]] | None = None
    sensitivity: list[Sensitivity] = ["primary_swap", "band_shift", "weights", "fake_star_filter"]
    fallbacks: Fallbacks = Fallbacks()

    _lists = field_validator("business_minimum", "sensitivity", mode="before")(_none_to_list)

    @field_validator("minimums", "metrics", "if_not_applicable", mode="before")
    @classmethod
    def _none_to_dict(cls, v: Any) -> Any:
        return {} if v is None else v

    @model_validator(mode="after")
    def _rules(self) -> Success:
        if self.primary in self.minimums:
            raise ValueError(
                f"minimums.{self.primary}: the primary dimension's floor is primary_threshold, "
                "not a minimum"
            )
        if self.primary == "business" and self.primary_threshold != "none":
            raise ValueError(
                "primary_threshold: business is ranked by its verified-signal count, not a "
                "percentile (outcome-model §5.2); set primary_threshold: none and use "
                "business_minimum"
            )
        for dim, metric in self.metrics.items():
            if metric not in METRICS[dim]:
                raise ValueError(
                    f"metrics.{dim}: {metric!r} is not allowed; use one of {list(METRICS[dim])}"
                )
        if self.weights is not None:
            if not self.weights:
                raise ValueError("weights: give at least one dimension, or leave weights out")
            total = sum(self.weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"weights: must sum to 1 (got {total:g})")
        for step in self.fallbacks.too_few_winners.steps:
            if step == "relax_primary_to_top_third":
                if self.primary_threshold not in ("top_quartile", "top_decile"):
                    raise ValueError(
                        "fallbacks.too_few_winners.steps: relax_primary_to_top_third needs a "
                        f"primary_threshold stricter than top_third (got {self.primary_threshold})"
                    )
                continue
            name = step.removeprefix("drop_").removesuffix("_minimum")
            present = bool(self.business_minimum) if name == "business" else name in self.minimums
            if not present:
                raise ValueError(
                    f"fallbacks.too_few_winners.steps: {step} but the brief sets no {name} minimum"
                )
        if len(set(self.fallbacks.too_few_winners.steps)) != len(
            self.fallbacks.too_few_winners.steps
        ):
            raise ValueError("fallbacks.too_few_winners.steps: a step is listed twice")
        return self

    def metric(self, dim: str) -> str:
        """The metric used for `dim`: the brief's choice or the default (outcome-model §5.2)."""
        for k, v in self.metrics.items():
            if k == dim:
                return v
        return METRICS[dim][0]


class OwnAudience(_Strict):
    """The user's own audience per channel, as CB-10 reach bands (a count becomes a band)."""

    channels: dict[Slug, AudienceBand] = {}
    note: Annotated[str, Field(max_length=500)] | None = None

    @field_validator("channels", mode="before")
    @classmethod
    def _bands(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {
                k: band_for_count(x) if isinstance(x, int) and not isinstance(x, bool) else x
                for k, x in v.items()
            }
        return v


class Channels(_Strict):
    planned: list[Slug] = []
    avoid: list[Slug] = []
    details: dict[Slug, list[Text]] = {}

    _lists = field_validator("planned", "avoid", mode="before")(_none_to_list)

    @model_validator(mode="after")
    def _disjoint(self) -> Channels:
        both = set(self.planned) & set(self.avoid)
        if both:
            raise ValueError(f"planned and avoid both list {sorted(both)}")
        return self


class Geography(_Strict):
    scope: Text = "global"
    languages: Annotated[list[Language], Field(min_length=1)] = ["en"]
    later: list[Text] = []

    _lists = field_validator("later", mode="before")(_none_to_list)


class Panel(_Strict):
    """R4.3, R4.8 (15–25 each, default 20/20), ADR-054.1 (exact match and SMD rules)."""

    winners: Annotated[int, Field(ge=15, le=25)] = 20
    losers: Annotated[int, Field(ge=15, le=25)] = 20
    exact_match: list[ExactMatch] = ["founder_audience_bucket", "launch_half_year"]
    smd_target: Annotated[float, Field(gt=0, le=1)] = 0.25
    headline_exclusion_smd: Annotated[float, Field(gt=0, le=2)] = 0.5

    @model_validator(mode="after")
    def _rules(self) -> Panel:
        if len(set(self.exact_match)) != len(self.exact_match):
            raise ValueError("exact_match: a characteristic is listed twice")
        if self.headline_exclusion_smd <= self.smd_target:
            raise ValueError(
                "headline_exclusion_smd must be larger than smd_target "
                f"({self.headline_exclusion_smd:g} <= {self.smd_target:g})"
            )
        return self


class Budget(_Strict):
    """ADR-053.1: two separate caps; the LLM backend is never switched automatically."""

    money_usd: Annotated[float, Field(ge=0, le=100_000)] = 0.0
    subscription_share: Annotated[float, Field(gt=0, le=1)] = 0.5
    llm_backend: Literal["subscription", "api"] = "subscription"
    # Spend cap for LLM calls on the `api` backend (R15.5 overrides included). Default 0.
    llm_api_usd: Annotated[float, Field(ge=0, le=100_000)] = 0.0
    auto_switch_backend: Literal[False] = False


class OptionalSources(_Strict):
    """Optional, possibly paid sources; all off unless the user enables them (ADR-053.1)."""

    trendshift: bool = False
    x: bool = False
    bigquery: bool = False

    def enabled_paid(self) -> list[str]:
        return [s for s in PAID_SOURCES if getattr(self, s)]


class Expansion(_Strict):
    """R18.7: the LLM-proposed expansion, as edited by the user (part of the brief version)."""

    problem_statement: Annotated[str, Field(max_length=2000)] | None = None
    users: list[Text] = []
    keywords: Annotated[list[Text], Field(max_length=40)] = []
    topics: Annotated[list[Topic], Field(max_length=40)] = []
    competitors: Annotated[list[Text], Field(max_length=40)] = []
    generated_by: Literal["user", "llm"] = "user"
    prompt_version: str | None = None


class Brief(_Strict):
    schema_version: Literal["brief/v1"] = SCHEMA_VERSION
    brief_id: BriefId
    version: Annotated[int, Field(ge=1)] | None = None  # set by the store
    supersedes: Annotated[int, Field(ge=1)] | None = None  # set by the store
    created_at: datetime | None = None  # set by the store
    edited_at: datetime | None = None  # set by the store
    status: Literal["draft", "ready_for_discovery"] = "draft"
    project: Project
    field: FieldBoundaries
    window: Window = Window()
    success: Success
    own_audience: OwnAudience = OwnAudience()
    channels: Channels = Channels()
    geography: Geography = Geography()
    panel: Panel = Panel()
    budget: Budget = Budget()
    optional_sources: OptionalSources = OptionalSources()
    expansion: Expansion | None = None
    notes: Annotated[str, Field(max_length=4000)] | None = None

    @model_validator(mode="after")
    def _cross(self) -> Brief:
        mw = self.success.fallbacks.too_few_winners.min_winners
        if mw > self.panel.winners:
            raise ValueError(
                f"success.fallbacks.too_few_winners.min_winners: {mw} is larger than "
                f"panel.winners ({self.panel.winners})"
            )
        return self

    # --- helpers --------------------------------------------------------------------------------
    def content(self) -> dict[str, Any]:
        """The brief without store-managed metadata (what an edit can change)."""
        d = self.model_dump(mode="json")
        return {k: v for k, v in d.items() if k not in METADATA_FIELDS}

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON content; equal content → equal hash (R18.4)."""
        return sha256_json(self.content())

    def warnings(self) -> list[str]:
        """Valid but worth a second look (shown by `validate`, the form and `estimate`)."""
        out: list[str] = []
        paid = self.optional_sources.enabled_paid()
        if paid and self.budget.money_usd == 0:
            out.append(
                f"optional_sources: {', '.join(paid)} enabled but budget.money_usd is 0, so any "
                "paid step stops the run (ADR-053.1)"
            )
        if self.budget.llm_backend == "api" and self.budget.llm_api_usd == 0:
            out.append(
                "budget.llm_api_usd is 0 with llm_backend: api, so the first LLM call stops the run"
            )
        if self.success.weights is not None:
            out.append(
                "success.weights is the advanced option: it only ranks qualifiers; results are "
                "still shown per dimension (outcome-model §6)"
            )
        if not self.field.widening_steps:
            out.append(
                "field.widening_steps is empty: if the core field yields too few winners or "
                "losers the panel can't widen (R4.10)"
            )
        return out


def sha256_json(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


# --- loading and error messages -------------------------------------------------------------


@dataclass(frozen=True)
class BriefProblem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class BriefInvalid(ValueError):
    """The brief failed validation; `problems` name each field (D7 acceptance)."""

    def __init__(self, problems: list[BriefProblem]) -> None:
        self.problems = problems
        super().__init__("invalid brief:\n" + "\n".join(f"  - {p}" for p in problems))


_FIELD_PREFIX = re.compile(r"^([a-z_][a-z0-9_.]*): (.+)$", re.DOTALL)
_MESSAGES = {
    "missing": "is required",
    "extra_forbidden": "unknown field (check the spelling against schemas/brief/v1.json)",
}


def _problems(err: ValidationError) -> list[BriefProblem]:
    out: list[BriefProblem] = []
    for e in err.errors(include_url=False):
        loc = [str(p) for p in e["loc"]]
        path = ".".join(loc)
        msg = _MESSAGES.get(e["type"], e["msg"])
        msg = msg.removeprefix("Value error, ")
        # A model-level rule names its own field ("minimums.adoption: ..."): join the paths.
        if e["type"] == "value_error" and (m := _FIELD_PREFIX.match(msg)):
            path = ".".join(p for p in (path, m.group(1)) if p)
            msg = m.group(2)
        if e["type"] not in ("missing", "extra_forbidden", "value_error") and "input" in e:
            shown = e["input"]
            if isinstance(shown, str | int | float | bool) and len(repr(shown)) <= 60:
                msg = f"{msg} (got {shown!r})"
        out.append(BriefProblem(path or "(brief)", msg))
    return out


def validate_brief(data: Any) -> Brief:
    """Validate a parsed brief; raise `BriefInvalid` with one message per field."""
    if not isinstance(data, dict):
        raise BriefInvalid([BriefProblem("(brief)", "must be a mapping of fields")])
    try:
        return Brief.model_validate(data)
    except ValidationError as e:
        raise BriefInvalid(_problems(e)) from None


def parse_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        problem = getattr(e, "problem", None) or str(e)
        raise BriefInvalid([BriefProblem("(yaml)", f"{where}{problem}")]) from None


def load_brief_text(text: str) -> Brief:
    return validate_brief(parse_yaml(text))


def dump_yaml(brief: Brief) -> str:
    """Canonical YAML of a brief (field order as in the schema; None values left out)."""
    d = brief.model_dump(mode="json", exclude_none=True)
    header = f"# Research brief ({SCHEMA_VERSION}). Private: keep it out of git (PRD R18.9).\n"
    return header + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=100)


# --- JSON Schema ------------------------------------------------------------------------------

CROSS_FIELD_RULES = [
    "field: include and exclude don't overlap",
    "success: the primary dimension has no entry in minimums (its floor is primary_threshold)",
    "success: primary business requires primary_threshold none (ranked by signal count)",
    "success.metrics: each metric is one of the allowed metrics for its dimension",
    "success.weights: sums to 1",
    "success.fallbacks.too_few_winners.steps: relax_primary_to_top_third needs a primary "
    "threshold stricter than top_third; drop_<dim>_minimum needs that minimum; no duplicates",
    "success.fallbacks.too_few_winners.min_winners <= panel.winners",
    "channels: planned and avoid don't overlap",
    "panel: headline_exclusion_smd > smd_target; exact_match has no duplicates",
]


def json_schema() -> dict[str, Any]:
    """The public JSON Schema for brief v1 (generated from the model; drift is tested)."""
    body = Brief.model_json_schema(mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "brief",
        "description": (
            "A pigtail research brief, schema v1 (PRD R18.1, ADR-053, ADR-054). Briefs are "
            "private and live only in the instance's data directory (R18.9)."
        ),
        **{k: v for k, v in body.items() if k not in ("title", "description")},
        "x-cross-field-rules": CROSS_FIELD_RULES,
    }


# --- upgrade from the provisional v0 layout ---------------------------------------------------

_V0_THRESHOLD = {
    "at_least_median_in_neighbourhood": "at_least_median",
    "median": "at_least_median",
    "top_quarter": "top_quartile",
}


def upgrade_v0(d: dict[str, Any]) -> dict[str, Any]:
    """Map a provisional "brief v0" document (ADR-047, before this schema) onto v1.

    v0 kept `core_field`, `widening_steps` and `time_window_months` at the top level, spelled
    thresholds as prose (`at_least_median_in_neighbourhood`), used a flat `own_audience`
    mapping and free-text geography. Unknown v0 keys are moved into `notes` rather than dropped.
    The result still goes through `validate_brief`.
    """
    d = dict(d)
    notes: list[str] = []
    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "brief_id": d.pop("brief_id", None)}
    for k in ("version", "supersedes", "created", "created_at", "edited_at"):
        d.pop(k, None)
    if "status" in d:
        out["status"] = d.pop("status")
    out["project"] = d.pop("project", None)
    fld = dict(d.pop("field", None) or {})
    for k in ("core_field", "widening_steps"):
        if k in d:
            fld[k] = d.pop(k)
    out["field"] = fld
    if "time_window_months" in d:
        out["window"] = {"months": d.pop("time_window_months")}
    elif "window" in d:
        out["window"] = d.pop("window")
    succ = dict(d.pop("success", None) or {})
    if "revenue" in succ:
        notes.append(f"success.revenue (v0): {succ.pop('revenue')}")
    if "primary_threshold" in succ:
        t = succ["primary_threshold"]
        succ["primary_threshold"] = _V0_THRESHOLD.get(t, t)
    if isinstance(succ.get("minimums"), dict):
        succ["minimums"] = {k: _V0_THRESHOLD.get(v, v) for k, v in succ["minimums"].items()}
    out["success"] = succ
    aud = d.pop("own_audience", None)
    if isinstance(aud, dict) and "channels" not in aud:
        note = aud.pop("note", None)
        out["own_audience"] = {"channels": aud, **({"note": note} if note else {})}
    elif aud is not None:
        out["own_audience"] = aud
    ch = d.pop("channels", None)
    if isinstance(ch, dict):
        known = {k: ch.pop(k) for k in ("planned", "avoid", "details") if k in ch}
        details = dict(known.pop("details", None) or {})
        details.update({k: v for k, v in ch.items() if isinstance(v, list)})
        out["channels"] = {**known, **({"details": details} if details else {})}
    geo = d.pop("geography", None)
    if isinstance(geo, dict) and "focus" in geo:
        later = geo.get("later")
        out["geography"] = {
            "scope": geo["focus"],
            **({"later": later if isinstance(later, list) else [later]} if later else {}),
        }
    elif geo is not None:
        out["geography"] = geo
    for k in ("panel", "budget", "expansion", "notes"):
        if k in d:
            out[k] = d.pop(k)
    if "optional_sources" in d:
        out["optional_sources"] = d.pop("optional_sources")
    for k, v in d.items():
        notes.append(f"{k} (v0): {json.dumps(v, default=str)}")
    if notes:
        prior = out.get("notes")
        out["notes"] = "\n".join(([prior] if prior else []) + notes)
    return {k: v for k, v in out.items() if v is not None}
