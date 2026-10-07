"""Shapes of the publication artifacts (PRD §5.2, §11, §12)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pigtail.bundle.models import (
    Claim,
    CompanyStage,
    Conflict,
    Constraint,
    Event,
    EvidenceLink,
    FetchStatus,
    Gap,
    GrowthEngine,
    GrowthEngineOccurrence,
    GrowthEpisode,
    Id,
    LaunchEpisode,
    MetricSnapshot,
    Narrative,
    Outcome,
    Prerequisite,
    Repository,
    Source,
    StrategyPhase,
    Surface,
    SurfacePresence,
    Tactic,
    TacticOccurrence,
    Target,
    Timestamp,
)

Action = Literal["KEEP", "DROP", "REDACT", "RELABEL", "TRUNCATE", "REQUIRE_REVIEW", "BLOCK"]
Severity = Literal["info", "warning", "blocking"]
Decision = Literal["approve_as_is", "approve_public_text", "exclude", "mark_manually_verified"]
Status = Literal["PASS", "NEEDS_REVIEW", "BLOCKED"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- public projection ----------------------------------------------------------------------


class PublicClaim(Claim):
    public_attribution: str | None = None
    manually_verified: bool = False


class PublicSource(Source):
    public_note: str | None = None


class PublicMetricSnapshot(MetricSnapshot):
    public_attribution: str | None = None


class PublicSourceFetch(_Strict):
    id: Id
    source_id: Id
    retrieved_at: Timestamp
    status: FetchStatus
    content_hash: str | None


class PublicReportInfo(_Strict):
    status: Literal["completed", "completed_with_gaps", "failed"]
    source_cutoff_at: Timestamp
    engine_version: str


class PublicNotices(_Strict):
    independence: str
    third_party_content: str
    machine_generated: str


class PublicReportBundle(_Strict):
    """Derived, minimized projection of a Research Bundle. Not authoritative research data."""

    projection: Literal["pigtail-public-report"]
    projection_version: str
    schema_version: Literal["0.1.0"]
    publication_policy_version: str
    report: PublicReportInfo
    notices: PublicNotices

    target: Target
    target_description_source: Literal["self_description", "reviewer"] | None = None
    repositories: list[Repository]
    sources: list[PublicSource]
    source_fetches: list[PublicSourceFetch]
    claims: list[PublicClaim]
    evidence_links: list[EvidenceLink]

    events: list[Event]
    launch_episodes: list[LaunchEpisode]
    growth_episodes: list[GrowthEpisode]
    metric_snapshots: list[PublicMetricSnapshot]
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


# ---- audit, review, manifest ---------------------------------------------------------------------


class AuditRef(_Strict):
    type: str
    id: str


class Finding(_Strict):
    finding_id: str
    rule_id: str
    severity: Severity
    action: Action
    object_ref: AuditRef
    field: str | None = None
    reason: str
    before: str | None = None
    after: str | None = None
    review_required: bool = False
    allowed_decisions: list[Decision] = Field(default_factory=list)
    resolution: str | None = None  # "automatic", a review decision, or None while unresolved


class AuditSummary(_Strict):
    kept: int = 0
    dropped: int = 0
    redacted: int = 0
    relabeled: int = 0
    truncated: int = 0
    requires_review: int = 0
    blocked: int = 0


class StepResult(_Strict):
    name: str
    result: str


class PublicationAudit(_Strict):
    policy_version: str
    sanitizer_version: str
    input_bundle_hash: str
    review_hash: str | None
    status: Status
    steps: list[StepResult]
    reviewers: list[str]
    findings: list[Finding]
    summary: AuditSummary


class ReviewEntry(_Strict):
    finding_id: str
    rule_id: str
    object_ref: AuditRef
    field: str | None = None
    reason: str = ""
    context: str | None = None
    allowed_decisions: list[Decision] = Field(default_factory=list)
    decision: Literal["pending", "approve_as_is", "approve_public_text", "exclude", "mark_manually_verified"] = (
        "pending"
    )
    public_text: str | None = None
    rationale: str | None = None
    reviewer: str | None = None


class KeepIdentity(_Strict):
    name: str
    rationale: str
    reviewer: str | None = None


class ReviewFile(_Strict):
    policy_version: str
    keep_identities: list[KeepIdentity] = Field(default_factory=list)
    items: list[ReviewEntry] = Field(default_factory=list)


class PublicationManifest(_Strict):
    publication_policy_version: str
    sanitizer_version: str
    engine_version: str
    target_name: str
    input_bundle_hash: str
    review_hash: str | None
    public_bundle_hash: str
    report_hash: str
    status: Status
    unresolved_findings: int
    generated_at: Timestamp
