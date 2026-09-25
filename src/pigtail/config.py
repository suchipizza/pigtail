"""Runtime settings, read only from environment variables (secrets never live in the repo)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BackendName = Literal["subscription", "api"]
BACKENDS: tuple[BackendName, ...] = ("subscription", "api")

DEFAULT_MODEL = "claude-opus-5"


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
        )
