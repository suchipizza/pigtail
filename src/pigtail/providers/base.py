"""Provider interfaces (spec §29–§30) and run metering.

Provider-specific code lives under providers/*; the research core depends only on these protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredModelRequest(Generic[T]):
    system: str
    prompt: str
    output_type: type[T]
    purpose: str = "general"
    max_tokens: int = 16000
    effort: str = "medium"


class ModelProvider(Protocol):
    provider_key: str
    model_id: str

    async def structured(self, request: StructuredModelRequest[T]) -> T: ...


@dataclass
class SearchQuery:
    text: str
    purpose: str = "general"
    max_results: int = 10


@dataclass
class SearchResult:
    url: str
    title: str | None
    page_age: str | None = None
    query: str | None = None


@dataclass
class SearchResults:
    query: SearchQuery
    results: list[SearchResult] = field(default_factory=list)
    error: str | None = None


class SearchProvider(Protocol):
    provider_key: str
    provider_version: str | None

    async def search(self, query: SearchQuery) -> SearchResults: ...

    async def search_many(self, queries: list[SearchQuery], context: str) -> list[SearchResults]: ...


@dataclass
class Meter:
    """Counts requests, tokens and cost for a run (PRD §14). Shared by all providers."""

    model_input_tokens: int = 0
    model_output_tokens: int = 0
    model_cost: float = 0.0
    search_requests: int = 0
    search_cost: float = 0.0
    http_requests: int = 0
    github_api_requests: int = 0
    retries: int = 0
    model_calls: int = 0
    pages_fetched: int = 0
    budget_usd: float | None = None

    @property
    def total_cost(self) -> float:
        return round(self.model_cost + self.search_cost, 4)

    def over_budget(self) -> bool:
        return self.budget_usd is not None and self.total_cost >= self.budget_usd

    def usage(self) -> dict:
        return {
            "model_input_tokens": self.model_input_tokens,
            "model_output_tokens": self.model_output_tokens,
            "search_requests": self.search_requests,
            "http_requests": self.http_requests,
            "github_api_requests": self.github_api_requests,
            "retries": self.retries,
        }

    def cost(self) -> dict:
        return {
            "currency": "USD",
            "model_cost": round(self.model_cost, 4),
            "search_cost": round(self.search_cost, 4),
            "other_cost": 0.0,
            "total_cost": self.total_cost,
        }
