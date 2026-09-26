"""Capture records v0 (PRD §7, R1.4), mirroring `schemas/v0/*.schema.json`.

The JSON Schemas are the contract; these models must stay field-for-field identical
(`tests/unit/test_capture_schemas.py` checks both directions on the examples).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal["v0"] = "v0"

Trigger = Literal["velocity", "announced", "manual", "analyze"]
CaseStatus = Literal["live", "pre_launch", "closed"]
Reliability = Literal["high", "medium", "low", "unknown"]
RetentionClass = Literal[
    "person_level_24m", "person_level_30d", "project_level", "derived_aggregate"
]
DeletionState = Literal["present", "deleted_upstream", "raw_dropped"]
RunStatus = Literal["running", "succeeded", "failed"]
BaselineQuality = Literal["full", "partial", "none"]

_HASH = r"^[0-9a-f]{64}$"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v0"] = SCHEMA_VERSION

    def to_json_dict(self) -> dict[str, Any]:
        """JSON-compatible dict (what goes into JSONL exports and JSONB columns)."""
        return self.model_dump(mode="json")


def repo_id(host: str, host_id: int) -> str:
    return f"{host}:{host_id}"


class Repo(_Record):
    id: str = Field(pattern=r"^[a-z]+:[0-9]+$")
    host: Literal["github"]
    host_id: int = Field(ge=0)
    full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    first_seen_at: datetime
    created_at: datetime | None = None


class Coverage(BaseModel):
    """Completeness of the star series behind a detection (GH Archive may under-capture stars).

    `reference_*` are filled when an independent source (since ADR-032 the GitHub star-history
    endpoint, `reference_source="github_star_history"`) was checked for the same window;
    otherwise they are null ("unknown", never guessed).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    window_start: datetime
    window_end: datetime
    observed_stars: int = Field(ge=0)
    reference_stars: int | None = Field(default=None, ge=0)
    reference_source: str | None = None
    ratio: float | None = Field(default=None, ge=0)


class VelocityDetection(BaseModel):
    """Metrics that made a velocity case fire (R1.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_version: str
    detected_hour: datetime
    stars_48h: int = Field(ge=0)
    stars_48h_raw: int = Field(ge=0)
    forks_48h: int = Field(ge=0)
    baseline_mean_48h: float = Field(ge=0)
    baseline_std_48h: float = Field(ge=0)
    sigma_used: float = Field(gt=0)
    z_score: float
    baseline_hours_covered: int = Field(ge=0, le=720)
    baseline_quality: BaselineQuality
    threshold_min_stars: int = Field(ge=0)
    threshold_sigma: float = Field(ge=0)
    bot_filter_version: str
    coverage: Coverage


BotFilterState = Literal["pending", "applied", "unavailable"]
BotFilterBasis = Literal["repo_events", "gharchive", "none"]


class BotFilterStatus(BaseModel):
    """Bot/lockstep confirmation of a detection-v1 case (ADR-032.2; replan §6.3).

    `status`: `pending` (per-repo events polling is on and will fill this in), `applied` (the
    heuristics of `pigtail.capture.botfilter` ran on identity-level events for the window),
    `unavailable` (no identity-level source: events polling off or held by ADR-022).
    `basis`: the data the filter used (outcome-model §2.1 `bot_filter_basis`).
    `confirmed` (outcome-model §2.1 `bot_filter_confirmed`): null until applied; then whether the
    window still meets R1.1's absolute threshold after removing the stars the filter flagged
    (star-history net stars minus flagged stars). Counts are null until applied.
    `coverage_ratio` = distinct non-bot stargazers seen in events / star-history net stars for the
    window (reference = star-history).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: BotFilterState
    basis: BotFilterBasis
    confirmed: bool | None = None
    version: str
    layers: list[str] = Field(default_factory=list)  # heuristics applied, e.g. ["login_rules"]
    updated_at: datetime | None = None
    stars_seen: int | None = Field(default=None, ge=0)
    stars_bot: int | None = Field(default=None, ge=0)
    stars_lockstep: int | None = Field(default=None, ge=0)
    stars_filtered: int | None = Field(default=None, ge=0)
    lockstep_hours: int | None = Field(default=None, ge=0)
    events_from: datetime | None = None
    window_overflow: bool | None = None
    coverage_ratio: float | None = Field(default=None, ge=0)


class DetectionV1(BaseModel):
    """Metrics of a detection-v1 case (ADR-032; detection removed in M11, ADR-047.6: the
    model stays because cases it opened keep this block).

    Same core fields as `VelocityDetection` (so readers of `stars_48h`, `z_score`,
    `detected_hour`, `baseline_quality` and `coverage.ratio` keep working) plus the data sources,
    the star-history baseline and the bot-filter confirmation state. `stars_48h` is the net
    change of the public stargazer count over the window (GraphQL hourly snapshots);
    `baseline_hours_covered` = baseline days observed x 24.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_version: Literal["detection-v1"]
    detected_hour: datetime
    stars_48h: int
    stars_48h_raw: int
    forks_48h: int
    baseline_mean_48h: float = Field(ge=0)
    baseline_std_48h: float = Field(ge=0)
    sigma_used: float = Field(gt=0)
    z_score: float
    baseline_hours_covered: int = Field(ge=0, le=720)
    baseline_quality: BaselineQuality
    threshold_min_stars: int = Field(ge=0)
    threshold_sigma: float = Field(ge=0)
    bot_filter_version: str
    coverage: Coverage
    data_sources: list[str]
    window_hours_observed: float = Field(ge=0)
    baseline_source: str
    baseline_days_covered: int = Field(ge=0)
    day_boundary_tz: str
    bot_filter: BotFilterStatus


class Case(_Record):
    id: str = Field(pattern=r"^case_[0-9a-f]{20}$")
    repo_id: str = Field(pattern=r"^[a-z]+:[0-9]+$")
    opened_at: datetime
    closed_at: datetime | None = None
    trigger: Trigger
    status: CaseStatus
    run_id: str | None = None
    detection: DetectionV1 | VelocityDetection | None


def case_id(repo: str, trigger: str, at: datetime) -> str:
    """Deterministic case id, so re-running a detection never duplicates a case."""
    key = f"{repo}|{trigger}|{at.isoformat()}".encode()
    return "case_" + hashlib.sha256(key).hexdigest()[:20]


class Evidence(_Record):
    id: str = Field(pattern=r"^ev_[0-9a-f]{24}$")
    source: str = Field(pattern=r"^[a-z0-9_]+$")
    url: str = Field(min_length=1)
    fetched_at: datetime
    content_hash: str = Field(pattern=_HASH)
    snapshot_ref: str = Field(
        pattern=r"^(s3://[^/]+/|local:)sha256/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$"
    )
    content_type: str | None = None
    http_status: int | None = None
    reliability: Reliability
    terms_basis: str = Field(min_length=1)
    retention_class: RetentionClass
    deletion_state: DeletionState = "present"
    collector_version: str = Field(min_length=1)
    case_id: str | None
    repo_id: str | None
    run_id: str | None = None


def evidence_id(source: str, url: str, content_hash: str) -> str:
    """Deterministic evidence id: the same bytes fetched from the same URL are one record."""
    key = f"{source}\n{url}\n{content_hash}".encode()
    return "ev_" + hashlib.sha256(key).hexdigest()[:24]


class Run(_Record):
    id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    job: str = Field(pattern=r"^[a-z0-9_.]+$")
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus
    code_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    config: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int | float] = Field(default_factory=dict)
    prompt_versions: dict[str, str] | None = None
    model_versions: dict[str, str] | None = None
    error: str | None = None


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex


RECORD_MODELS: dict[str, type[_Record]] = {
    "repo": Repo,
    "case": Case,
    "evidence": Evidence,
    "run": Run,
}
