"""Pydantic models for Pigtail Research Bundle v0.1.0 (spec §5–§20).

These models are the runtime form of the normative JSON Schema checked in at
`schemas/research-bundle/0.1.0.schema.json`. The schema is generated from these
models (`python -m pigtail.bundle.schema --write`) and CI fails if they diverge.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, WithJsonSchema

from pigtail.domain.ids import is_uuid7
from pigtail.domain.time import parse_dt

SCHEMA_VERSION = "0.1.0"
SCHEMA_ID = "https://pigtail.dev/schemas/research-bundle/0.1.0"

UUID7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _check_uuid7(v: str) -> str:
    if not is_uuid7(v):
        raise ValueError(f"not a lowercase UUIDv7: {v!r}")
    return v


def _check_rfc3339(v: str) -> str:
    if parse_dt(v) is None or "T" not in v:
        raise ValueError(f"not an RFC3339 timestamp: {v!r}")
    return v


Id = Annotated[
    str,
    AfterValidator(_check_uuid7),
    WithJsonSchema({"type": "string", "pattern": UUID7_PATTERN}),
]
Timestamp = Annotated[
    str,
    AfterValidator(_check_rfc3339),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]

ObjectRefType = Literal[
    "target",
    "person",
    "repository",
    "source",
    "claim",
    "event",
    "launch_episode",
    "growth_episode",
    "metric_snapshot",
    "company_stage",
    "strategy_phase",
    "surface",
    "surface_presence",
    "tactic",
    "tactic_occurrence",
    "growth_engine",
    "growth_engine_occurrence",
    "outcome",
    "prerequisite",
    "constraint",
    "conflict",
    "gap",
]
Precision = Literal["second", "minute", "hour", "day", "month", "quarter", "year", "range", "unknown"]
EvidenceClass = Literal[
    "documented", "company_measured", "third_party_measured", "third_party_reported", "inferred", "unknown"
]
ReviewState = Literal["machine_extracted", "machine_inferred", "human_verified", "community_verified"]
SourceDirectness = Literal[
    "primary_direct",
    "primary_indirect",
    "independent_direct",
    "secondary_synthesis",
    "community_report",
    "unknown",
]
Corroboration = Literal["single_source", "multi_source_consistent", "multi_source_mixed", "contradicted", "unknown"]
InferenceStrength = Literal["explicit", "strong_inference", "moderate_inference", "weak_inference", "not_applicable"]
CausalAttribution = Literal[
    "directly_measured", "company_attributed", "strongly_associated", "weakly_associated", "unknown"
]
RetentionMode = Literal["metadata_only", "metadata_and_excerpt", "full_permitted"]
PublicDisplayMode = Literal["link_only", "paraphrase_and_link", "paraphrase_link_excerpt"]
CoverageTier = Literal["A", "B", "C"]
FetchStatus = Literal[
    "success", "blocked", "not_found", "rate_limited", "parse_failed", "network_failed", "skipped_policy"
]
LocatorKind = Literal[
    "text_fragment",
    "heading",
    "paragraph",
    "line_range",
    "json_path",
    "html_anchor",
    "timestamp",
    "page",
    "other",
]
StarHistoryQuality = Literal["exact", "sampled", "reconstructed", "unavailable"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ObjectRef(_Strict):
    type: ObjectRefType
    id: Id


class TimeRange(_Strict):
    start: Timestamp | None
    end: Timestamp | None
    precision: Precision
    label: str | None


class ModelInfo(_Strict):
    provider: str
    model: str


class DiscoveryInfo(_Strict):
    provider: str
    provider_version: str | None


class Cost(_Strict):
    currency: Literal["USD"] = "USD"
    model_cost: float = Field(ge=0)
    search_cost: float = Field(ge=0)
    other_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)


class Usage(_Strict):
    model_input_tokens: int = Field(ge=0)
    model_output_tokens: int = Field(ge=0)
    search_requests: int = Field(ge=0)
    http_requests: int = Field(ge=0)
    github_api_requests: int = Field(ge=0)
    retries: int = Field(ge=0)


class Run(_Strict):
    run_id: Id
    status: Literal["completed", "completed_with_gaps", "failed"]
    started_at: Timestamp
    completed_at: Timestamp
    source_cutoff_at: Timestamp
    engine_version: str
    research_policy_version: str
    source_policy_version: str
    ontology_version: str
    model: ModelInfo
    discovery: DiscoveryInfo
    cost: Cost
    usage: Usage


class ExternalId(_Strict):
    namespace: str
    value: str
    url: str | None


class Target(_Strict):
    id: Id
    kind: Literal["product", "company", "repository"]
    name: str
    canonical_url: str
    domain: str | None
    description: str | None
    primary_repository_id: Id | None
    external_ids: list[ExternalId]
    aliases: list[str]


class Person(_Strict):
    id: Id
    name: str
    role: str | None
    external_ids: list[ExternalId]
    claim_ids: list[Id]


class RepositoryCurrent(_Strict):
    stars: int | None
    forks: int | None
    watchers: int | None
    open_issues: int | None
    contributors: int | None


class Repository(_Strict):
    id: Id
    provider: Literal["github"]
    owner: str
    name: str
    url: str
    external_id: str | None
    created_at: Timestamp | None
    default_branch: str | None
    language: str | None
    license: str | None
    is_archived: bool
    observed_at: Timestamp
    current: RepositoryCurrent
    star_history_quality: StarHistoryQuality
    claim_ids: list[Id]


class SourcePolicyRef(_Strict):
    policy_key: str
    policy_version: str
    coverage_tier: CoverageTier
    retention_mode: RetentionMode
    public_display_mode: PublicDisplayMode


class Source(_Strict):
    id: Id
    url: str
    canonical_url: str
    surface_key: str
    source_type: str
    title: str | None
    author: str | None
    published_at: Timestamp | None
    discovered_at: Timestamp
    policy: SourcePolicyRef


class SourceFetch(_Strict):
    id: Id
    source_id: Id
    retrieved_at: Timestamp
    status: FetchStatus
    http_status: int | None
    content_hash: str | None
    etag: str | None
    last_modified: str | None
    parser_version: str
    retention_mode: RetentionMode
    retained_artifact_path: str | None
    error_code: str | None


class Claim(_Strict):
    id: Id
    statement: str
    claim_kind: str
    subject_ref: ObjectRef
    time: TimeRange
    review_state: ReviewState
    extraction_certainty: float = Field(ge=0, le=1)
    is_negative_sensitive: bool
    status: Literal["active", "contradicted", "superseded", "withdrawn"]


class Locator(_Strict):
    kind: LocatorKind
    value: str


class EvidenceLink(_Strict):
    id: Id
    target_ref: ObjectRef
    claim_id: Id
    source_id: Id
    source_fetch_id: Id
    evidence_class: EvidenceClass
    source_directness: SourceDirectness
    corroboration: Corroboration
    inference_strength: InferenceStrength
    causal_attribution: CausalAttribution
    locator: Locator
    excerpt: str | None
    notes: str | None


class Event(_Strict):
    id: Id
    event_type: str
    title: str
    summary: str
    time: TimeRange
    surface_ids: list[Id]
    claim_ids: list[Id]
    metric_snapshot_ids: list[Id]
    outcome_ids: list[Id]
    tactic_occurrence_ids: list[Id]
    launch_episode_id: Id | None
    review_state: ReviewState
    is_negative_sensitive: bool


class LaunchEpisode(_Strict):
    id: Id
    title: str
    time: TimeRange
    event_ids: list[Id]
    claim_ids: list[Id]
    summary: str
    review_state: ReviewState


class GrowthEpisode(_Strict):
    id: Id
    repository_id: Id
    title: str
    time: TimeRange
    start_metric_id: Id | None
    end_metric_id: Id | None
    delta_numeric: float | None
    related_event_ids: list[Id]
    summary: str
    causal_attribution: CausalAttribution
    claim_ids: list[Id]


class MetricSnapshot(_Strict):
    id: Id
    metric_key: str
    label: str
    value_numeric: float | None
    value_text: str | None
    unit: str
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None
    time: TimeRange
    repository_id: Id | None
    claim_ids: list[Id]
    review_state: ReviewState


class CompanyStage(_Strict):
    id: Id
    label: str
    summary: str
    time: TimeRange
    claim_ids: list[Id]
    review_state: ReviewState


class StrategyPhase(_Strict):
    id: Id
    label: str
    summary: str
    time: TimeRange
    claim_ids: list[Id]
    review_state: ReviewState


class Surface(_Strict):
    id: Id
    key: str
    name: str
    category: str | None
    url: str | None


class SurfacePresence(_Strict):
    id: Id
    surface_id: Id
    profile_url: str | None
    time: TimeRange
    claim_ids: list[Id]


class Tactic(_Strict):
    id: Id
    name: str
    description: str
    mechanism: str
    status: Literal["candidate", "reference"]
    claim_ids: list[Id]


class TacticOccurrence(_Strict):
    id: Id
    tactic_id: Id
    event_id: Id | None
    strategy_phase_id: Id | None
    time: TimeRange
    implementation: str
    outcome_ids: list[Id]
    claim_ids: list[Id]
    review_state: ReviewState


class GrowthEngine(_Strict):
    id: Id
    name: str
    description: str
    mechanism: str
    status: Literal["candidate", "reference"]
    claim_ids: list[Id]


class GrowthEngineOccurrence(_Strict):
    id: Id
    growth_engine_id: Id
    strategy_phase_ids: list[Id]
    time: TimeRange
    state: Literal["emerging", "active", "weakening", "ended", "unknown"]
    strength: str
    tactic_occurrence_ids: list[Id]
    outcome_ids: list[Id]
    claim_ids: list[Id]
    review_state: ReviewState


class Outcome(_Strict):
    id: Id
    summary: str
    event_id: Id | None
    tactic_occurrence_id: Id | None
    metric_snapshot_ids: list[Id]
    causal_attribution: CausalAttribution
    claim_ids: list[Id]
    review_state: ReviewState


class Prerequisite(_Strict):
    id: Id
    name: str
    description: str
    applies_to_refs: list[ObjectRef]
    claim_ids: list[Id]
    review_state: ReviewState


class Constraint(_Strict):
    id: Id
    name: str
    description: str
    applies_to_refs: list[ObjectRef]
    claim_ids: list[Id]
    review_state: ReviewState


class Conflict(_Strict):
    id: Id
    summary: str
    claim_ids: list[Id]
    related_refs: list[ObjectRef]
    status: Literal["unresolved", "resolved"]


class Gap(_Strict):
    id: Id
    gap_type: str
    summary: str
    severity: Literal["material", "minor"]
    related_refs: list[ObjectRef]
    surface_key: str | None


class NarrativeBlock(_Strict):
    text: str
    claim_ids: list[Id]
    inference_strength: InferenceStrength


class Narrative(_Strict):
    # A block is null when the evidence does not support writing it (PRD P5).
    thirty_second: NarrativeBlock | None
    origin: NarrativeBlock | None
    first_users: NarrativeBlock | None
    flywheel: NarrativeBlock | None
    did_differently: NarrativeBlock | None
    key_takeaways: list[NarrativeBlock]


class ResearchBundle(_Strict):
    schema_version: Literal["0.1.0"]
    bundle_id: Id
    generated_at: Timestamp

    run: Run
    target: Target
    people: list[Person]
    repositories: list[Repository]
    sources: list[Source]
    source_fetches: list[SourceFetch]
    claims: list[Claim]
    evidence_links: list[EvidenceLink]

    events: list[Event]
    launch_episodes: list[LaunchEpisode]
    growth_episodes: list[GrowthEpisode]
    metric_snapshots: list[MetricSnapshot]
    company_stages: list[CompanyStage]
    strategy_phases: list[StrategyPhase]
    surfaces: list[Surface]
    surface_presences: list[SurfacePresence]

    tactics: list[Tactic]
    tactic_occurrences: list[TacticOccurrence]
    growth_engines: list[GrowthEngine]
    growth_engine_occurrences: list[GrowthEngineOccurrence]
    outcomes: list[Outcome]
    prerequisites: list[Prerequisite]
    constraints: list[Constraint]

    conflicts: list[Conflict]
    gaps: list[Gap]
    narrative: Narrative

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"$id": SCHEMA_ID, "title": "Pigtail Research Bundle 0.1.0"},
    )


COLLECTIONS: tuple[str, ...] = (
    "people",
    "repositories",
    "sources",
    "source_fetches",
    "claims",
    "evidence_links",
    "events",
    "launch_episodes",
    "growth_episodes",
    "metric_snapshots",
    "company_stages",
    "strategy_phases",
    "surfaces",
    "surface_presences",
    "tactics",
    "tactic_occurrences",
    "growth_engines",
    "growth_engine_occurrences",
    "outcomes",
    "prerequisites",
    "constraints",
    "conflicts",
    "gaps",
)
