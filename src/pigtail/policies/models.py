"""Source-policy file format (spec §24). One YAML file per surface under source-policies/."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Access(_Strict):
    discovery_allowed: bool
    automated_retrieval_allowed: bool
    method: str
    authentication: str
    rate_limit_notes: str | None


class Retention(_Strict):
    mode: Literal["metadata_only", "metadata_and_excerpt", "full_permitted"]
    allow_full_artifact: bool
    max_excerpt_chars: int = Field(ge=0)


class PublicDisplay(_Strict):
    mode: Literal["link_only", "paraphrase_and_link", "paraphrase_link_excerpt"]
    attribution_required: bool
    notes: str | None


class Obligations(_Strict):
    deletion_refresh: str | None
    terms_url: str | None
    reviewed_at: str


class Implementation(_Strict):
    adapter: str
    enabled_by_default: bool
    notes: str | None


class SourcePolicy(_Strict):
    key: str
    version: str
    display_name: str
    coverage_tier: Literal["A", "B", "C"]
    access: Access
    retention: Retention
    public_display: PublicDisplay
    obligations: Obligations
    implementation: Implementation

    @property
    def excerpt_allowed(self) -> bool:
        return (
            self.retention.mode != "metadata_only"
            and self.public_display.mode != "link_only"
            and self.retention.max_excerpt_chars > 0
        )

    def clip_excerpt(self, text: str | None) -> str | None:
        """Return an excerpt the policy permits, or None."""
        if not text or not self.excerpt_allowed:
            return None
        text = " ".join(text.split())
        limit = self.retention.max_excerpt_chars
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
