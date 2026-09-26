"""Runtime settings, read only from environment variables (secrets never live in the repo)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

from pigtail.pseudonymize import optout_key_from_env

BackendName = Literal["subscription", "api"]
BACKENDS: tuple[BackendName, ...] = ("subscription", "api")

# Model per LLM stage (Directive §6.3, ADR-064.3, PRD R15.8). Each is overridden by
# `LLM_MODEL_<STAGE>`; `LLM_MODEL`, when set, is the fallback for every stage without its own
# variable (ADR-072.5). Which job runs in which stage: `pigtail.llm.stages`.
LLMStageName = Literal["relevance", "extraction", "synthesis"]
LLM_STAGES: tuple[LLMStageName, ...] = ("relevance", "extraction", "synthesis")
DEFAULT_STAGE_MODELS: dict[LLMStageName, str] = {
    "relevance": "claude-haiku-4-5-20251001",
    "extraction": "claude-sonnet-5",
    "synthesis": "claude-opus-5-5",
}
DEFAULT_BUDGET_USD_MONTH = 200.0  # instance-wide monthly API cap (ADR-064.4, ADR-072.4)
DEFAULT_BRIEFS_DIR = "~/.pigtail/briefs"  # outside any git tree (ADR-071.3, PRD R18.9)
BRIEFS_DIR_ENV = "PIGTAIL_BRIEFS_DIR"
# Explicit opt-in to the pre-M21 location PIGTAIL_DATA_DIR/briefs (ADR-055.1); never a default.
BRIEFS_IN_DATA_DIR_ENV = "PIGTAIL_BRIEFS_IN_DATA_DIR"

# Retention ceilings from docs/compliance/retention-policy.md (DPIA CB-01, CB-05, CB-18).
# Operators may configure shorter periods, never longer ones.
# R19.9 (Directive §8.2, ADR-066.2): raw person-level snapshots referenced by a brief are kept
# until that brief's report is final plus 12 months. PERSON_LEVEL_MAX_DAYS (24 months from
# fetch) is the ceiling for snapshots no final report anchors (unreferenced, or every
# referencing report still pending); see pigtail.privacy.snapshot_retention.
PERSON_LEVEL_MAX_DAYS = 730  # 24 months
SNAPSHOT_AFTER_REPORT_MAX_DAYS = 365  # report final + 12 months
LLM_CACHE_MAX_DAYS = 730  # 24 months, and never longer than the evidence it came from
LOG_MAX_DAYS = 365  # 12 months
GHARCHIVE_RAW_MAX_DAYS = 30  # CB-04 / CB-32: raw GH Archive dumps (retention-policy §2)
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


# CB-29 (ADR-043): fields never shown by `repr(Settings)`. `database_url` can carry a password.
SECRET_FIELDS = frozenset({"pseudonym_key", "database_url", "s3_access_key", "s3_secret_key"})


@dataclass(frozen=True, repr=False)
class Settings:
    llm_backend: BackendName = "subscription"
    llm_backend_overrides: dict[str, BackendName] = field(default_factory=dict)
    # `LLM_MODEL`: fallback model for stages without their own variable (None when unset).
    llm_model: str | None = None
    llm_models: dict[LLMStageName, str] = field(default_factory=lambda: dict(DEFAULT_STAGE_MODELS))
    # R15.9: non-time-sensitive stages go through the Message Batches API on `api` (LLM_BATCH=0
    # sends standard calls instead, e.g. for a quick test).
    llm_batch: bool = True
    llm_limit_pause_seconds: int = 1800
    data_dir: Path = Path("data")
    pseudonym_key: str | None = None
    budget_usd_month: float = DEFAULT_BUDGET_USD_MONTH
    briefs_dir: Path = field(default_factory=lambda: Path(DEFAULT_BRIEFS_DIR).expanduser())
    # "env" (PIGTAIL_BRIEFS_DIR), "data_dir" (explicit PIGTAIL_BRIEFS_IN_DATA_DIR=1) or "default"
    briefs_dir_source: Literal["env", "data_dir", "default"] = "default"
    database_url: str | None = None
    snapshot_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str | None = None
    s3_bucket: str = "pigtail-snapshots"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    gharchive_raw_retention_days: int = GHARCHIVE_RAW_MAX_DAYS
    person_level_retention_days: int = PERSON_LEVEL_MAX_DAYS
    llm_cache_retention_days: int = LLM_CACHE_MAX_DAYS
    log_retention_days: int = LOG_MAX_DAYS
    github_events_retention_days: int = GITHUB_EVENTS_DEFAULT_DAYS
    snapshot_after_report_days: int = SNAPSHOT_AFTER_REPORT_MAX_DAYS  # R19.9

    @property
    def optout_key(self) -> str | None:
        """The opt-out key (`OPTOUT_KEY`, alias `PSEUDONYM_KEY`; ADR-071.1). `pseudonym_key` is
        the earlier attribute name of the same value."""
        return self.pseudonym_key

    def __repr__(self) -> str:
        """Secrets are masked (CB-29): a logged or printed Settings never shows them."""
        parts = []
        for f in fields(self):
            v = getattr(self, f.name)
            shown = ("'***'" if v else repr(v)) if f.name in SECRET_FIELDS else repr(v)
            parts.append(f"{f.name}={shown}")
        return f"Settings({', '.join(parts)})"

    def model_for(self, stage: LLMStageName) -> str:
        """The model a stage uses (R15.8): `llm_models` already folds in the env fallbacks."""
        return self.llm_models[stage]

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ) if env is None else env
        data_dir = Path(e.get("PIGTAIL_DATA_DIR", "data"))
        briefs_dir, briefs_src = resolve_briefs_dir(e, data_dir)
        fallback = (e.get("LLM_MODEL") or "").strip() or None
        return cls(
            llm_backend=_backend(e.get("LLM_BACKEND", "subscription"), "LLM_BACKEND"),
            llm_backend_overrides=parse_overrides(e.get("LLM_BACKEND_OVERRIDES", "")),
            llm_model=fallback,
            llm_models=stage_models(e),
            llm_batch=_flag(e.get("LLM_BATCH", "1"), "LLM_BATCH"),
            llm_limit_pause_seconds=int(e.get("LLM_LIMIT_PAUSE_SECONDS", "1800")),
            data_dir=data_dir,
            pseudonym_key=optout_key_from_env(e),
            budget_usd_month=_money(e.get("BUDGET_USD_MONTH"), "BUDGET_USD_MONTH"),
            briefs_dir=briefs_dir,
            briefs_dir_source=briefs_src,
            database_url=e.get("DATABASE_URL") or None,
            snapshot_backend=_snapshot_backend(e.get("SNAPSHOT_BACKEND", "local")),
            s3_endpoint=e.get("S3_ENDPOINT") or None,
            s3_bucket=e.get("S3_BUCKET") or "pigtail-snapshots",
            s3_access_key=e.get("S3_ACCESS_KEY") or None,
            s3_secret_key=e.get("S3_SECRET_KEY") or None,
            s3_region=e.get("S3_REGION") or "us-east-1",
            gharchive_raw_retention_days=_days(
                e, "GHARCHIVE_RAW_RETENTION_DAYS", GHARCHIVE_RAW_MAX_DAYS
            ),
            person_level_retention_days=_days(
                e, "PERSON_LEVEL_RETENTION_DAYS", PERSON_LEVEL_MAX_DAYS
            ),
            llm_cache_retention_days=_days(e, "LLM_CACHE_RETENTION_DAYS", LLM_CACHE_MAX_DAYS),
            log_retention_days=_days(e, "LOG_RETENTION_DAYS", LOG_MAX_DAYS),
            github_events_retention_days=_days(
                e,
                "GITHUB_EVENTS_RETENTION_DAYS",
                GITHUB_EVENTS_MAX_DAYS,
                default=GITHUB_EVENTS_DEFAULT_DAYS,
            ),
            snapshot_after_report_days=_days(
                e, "SNAPSHOT_AFTER_REPORT_DAYS", SNAPSHOT_AFTER_REPORT_MAX_DAYS
            ),
        )


def stage_models(e: dict[str, str]) -> dict[LLMStageName, str]:
    """R15.8: `LLM_MODEL_<STAGE>` > `LLM_MODEL` (fallback) > the code default per stage."""
    fallback = (e.get("LLM_MODEL") or "").strip()
    out: dict[LLMStageName, str] = {}
    for stage in LLM_STAGES:
        own = (e.get(f"LLM_MODEL_{stage.upper()}") or "").strip()
        out[stage] = own or fallback or DEFAULT_STAGE_MODELS[stage]
    return out


def resolve_briefs_dir(
    e: dict[str, str], data_dir: Path
) -> tuple[Path, Literal["env", "data_dir", "default"]]:
    """ADR-071.3 / R18.9: `PIGTAIL_BRIEFS_DIR` (with `~` expanded) > PIGTAIL_DATA_DIR/briefs,
    only when `PIGTAIL_BRIEFS_IN_DATA_DIR=1` says so explicitly > `~/.pigtail/briefs`."""
    raw = (e.get(BRIEFS_DIR_ENV) or "").strip()
    if raw:
        return Path(os.path.expandvars(raw)).expanduser(), "env"
    if _flag(e.get(BRIEFS_IN_DATA_DIR_ENV, "0"), BRIEFS_IN_DATA_DIR_ENV):
        return data_dir / "briefs", "data_dir"
    return Path(DEFAULT_BRIEFS_DIR).expanduser(), "default"


def _flag(value: str | None, name: str) -> bool:
    v = (value or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("", "0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be 1 or 0, got {value!r}")


def _money(value: str | None, name: str) -> float:
    raw = (value or "").strip()
    usd = float(raw) if raw else DEFAULT_BUDGET_USD_MONTH
    if not 0 <= usd <= 1_000_000:
        raise ValueError(f"{name} must be between 0 and 1000000 USD, got {raw}")
    return usd


def _days(e: dict[str, str], name: str, maximum: int, default: int | None = None) -> int:
    """A retention period in days: ceiling `maximum` (the policy limit); default `default`,
    or the ceiling when no separate default is given."""
    raw = (e.get(name) or "").strip()
    days = int(raw) if raw else (maximum if default is None else default)
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
