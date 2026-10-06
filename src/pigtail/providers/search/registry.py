"""Search/discovery provider selection (spec §30.2). The default is OPEN / MEASURE FIRST."""

from __future__ import annotations

from pigtail.config import Config
from pigtail.errors import CredentialsError
from pigtail.providers.base import Meter, SearchQuery, SearchResults

SUPPORTED_SEARCH_PROVIDERS = ("anthropic_web_search", "none")


class NoSearch:
    provider_key = "none"
    provider_version = None

    async def search(self, query: SearchQuery) -> SearchResults:
        return SearchResults(query=query)

    async def search_many(self, queries: list[SearchQuery], context: str) -> list[SearchResults]:
        return [SearchResults(query=q) for q in queries]


def make_search_provider(cfg: Config, meter: Meter):
    p = cfg.discovery.provider
    if p == "none" or cfg.discovery.max_queries == 0:
        return NoSearch()
    if p == "anthropic_web_search":
        from pigtail.providers.search.anthropic_web_search import AnthropicWebSearch

        model = cfg.model.model if cfg.model.provider == "anthropic" else "claude-sonnet-5-5"
        return AnthropicWebSearch(model, cfg.secret(cfg.discovery.api_key_env), meter)
    raise CredentialsError(f"Discovery provider {p!r} is not supported ({', '.join(SUPPORTED_SEARCH_PROVIDERS)}).")
