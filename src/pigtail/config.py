"""Runtime settings, read only from environment variables (secrets never live in the repo)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BackendName = Literal["subscription", "api"]
BACKENDS: tuple[BackendName, ...] = ("subscription", "api")

DEFAULT_MODEL = "claude-opus-5"

# Retention ceilings from docs/compliance/retention-policy.md (DPIA CB-01, CB-05, CB-18).
# Operators may configure shorter periods, never longer ones.
PERSON_LEVEL_MAX_DAYS = 730  # 24 months
LLM_CACHE_MAX_DAYS = 730  # 24 months, and never longer than the evidence it came from
LOG_MAX_DAYS = 365  # 12 months
GITHUB_EVENTS_MAX_DAYS = 30  # TM-33 / CB-22: per-repo event actors, person_level_30d
# ADR-038: default covers the 14-day case cooldown + 48 h detection window (de-duplicating stars
# within a case window); the lockstep rule is not applied to per-repo events (ADR-037.2).
GITHUB_EVENTS_DEFAULT_DAYS = 16


def _backend(value: str, source: str) -> BackendName:
    v = value.strip().lower()
    if v == "subscription":
        return "subscription"
    if v == "api":
        return "api"
    raise ValueError(f"{source} must be 'subscription' or 'api', got {value!r}")


def parse_overrides(raw: str) -> dict[str, BackendName]:
    """Parse `LLM_BACKEND_OVERRIDES`, e.g. `tier2_extraction:api,pilot:subscription` (R15.5)."""
    out: dict[str, BackendName] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        job, sep, backend = part.partition(":")
        if not sep or not job.strip():
            raise ValueError(f"bad LLM_BACKEND_OVERRIDES entry {part!r}; expected job:backend")
        out[job.strip()] = _backend(backend, f"LLM_BACKEND_OVERRIDES[{job}]")
    return out


@dataclass(frozen=True)
class Settings:
    llm_backend: BackendName = "subscription"
    llm_backend_overrides: dict[str, BackendName] = field(default_factory=dict)
    llm_model: str = DEFAULT_MODEL
    llm_limit_pause_seconds: int = 1800
    data_dir: Path = Path("data")
    pseudonym_key: str | None = None
    budget_usd_month: float = 300.0
    database_url: str | None = None
    snapshot_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str | None = None
    s3_bucket: str = "pigtail-snapshots"
    s3_access_key: str | None = None
    s3_secret_key: str | None = field(default=None, repr=False)
    s3_region: str = "us-east-1"
    gharchive_raw_retention_days: int = 30
    person_level_retention_days: int = PERSON_LEVEL_MAX_DAYS
    llm_cache_retention_days: int = LLM_CACHE_MAX_DAYS
    log_retention_days: int = LOG_MAX_DAYS
    github_events_retention_days: int = GITHUB_EVENTS_DEFAULT_DAYS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ) if env is None else env
        return cls(
            llm_backend=_backend(e.get("LLM_BACKEND", "subscription"), "LLM_BACKEND"),
            llm_backend_overrides=parse_overrides(e.get("LLM_BACKEND_OVERRIDES", "")),
            llm_model=e.get("LLM_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            llm_limit_pause_seconds=int(e.get("LLM_LIMIT_PAUSE_SECONDS", "1800")),
            data_dir=Path(e.get("PIGTAIL_DATA_DIR", "data")),
            pseudonym_key=e.get("PSEUDONYM_KEY") or None,
            budget_usd_month=float(e.get("BUDGET_USD_MONTH", "300") or 300),
            database_url=e.get("DATABASE_URL") or None,
            snapshot_backend=_snapshot_backend(e.get("SNAPSHOT_BACKEND", "local")),
            s3_endpoint=e.get("S3_ENDPOINT") or None,
            s3_bucket=e.get("S3_BUCKET") or "pigtail-snapshots",
            s3_access_key=e.get("S3_ACCESS_KEY") or None,
            s3_secret_key=e.get("S3_SECRET_KEY") or None,
            s3_region=e.get("S3_REGION") or "us-east-1",
            gharchive_raw_retention_days=int(e.get("GHARCHIVE_RAW_RETENTION_DAYS") or 30),
            person_level_retention_days=_days(
                e, "PERSON_LEVEL_RETENTION_DAYS", PERSON_LEVEL_MAX_DAYS
            ),
            llm_cache_retention_days=_days(e, "LLM_CACHE_RETENTION_DAYS", LLM_CACHE_MAX_DAYS),
            log_retention_days=_days(e, "LOG_RETENTION_DAYS", LOG_MAX_DAYS),
            github_events_retention_days=_days(
                e, "GITHUB_EVENTS_RETENTION_DAYS", GITHUB_EVENTS_DEFAULT_DAYS
            ),
        )


def _days(e: dict[str, str], name: str, maximum: int) -> int:
    """A retention period in days: default and ceiling `maximum` (the policy limit)."""
    raw = (e.get(name) or "").strip()
    days = int(raw) if raw else maximum
    if not 0 <= days <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum} (retention policy), got {days}")
    return days


def _snapshot_backend(value: str) -> Literal["local", "s3"]:
    v = (value or "local").strip().lower()
    if v == "local":
        return "local"
    if v == "s3":
        return "s3"
    raise ValueError(f"SNAPSHOT_BACKEND must be 'local' or 's3', got {value!r}")
