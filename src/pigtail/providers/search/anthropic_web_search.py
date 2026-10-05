"""Discovery through Anthropic's server-side web search tool.

Only URLs that appear in `web_search_tool_result` blocks are returned. Text the model writes
is ignored, so the model cannot introduce a URL that the search engine did not return.
"""

from __future__ import annotations

import asyncio

import anthropic

from pigtail.errors import CredentialsError
from pigtail.logging import get_logger
from pigtail.providers.base import Meter, SearchQuery, SearchResult, SearchResults
from pigtail.providers.models.anthropic import price_for

log = get_logger("search.anthropic")

SEARCH_PRICE_USD = 0.01  # $10 per 1,000 searches
TOOL_TYPE = "web_search_20260209"
BASIC_TOOL_TYPE = "web_search_20250305"
DYNAMIC_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
}


class AnthropicWebSearch:
    provider_key = "anthropic_web_search"
    provider_version = TOOL_TYPE

    def __init__(self, model_id: str, api_key: str | None, meter: Meter):
        if not api_key:
            raise CredentialsError("Web search needs ANTHROPIC_API_KEY (discovery provider anthropic_web_search).")
        self.model_id = model_id
        self.tool_type = TOOL_TYPE if model_id in DYNAMIC_MODELS else BASIC_TOOL_TYPE
        self.provider_version = self.tool_type
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=600)
        self.meter = meter

    async def search(self, query: SearchQuery) -> SearchResults:
        return (await self.search_many([query], context=""))[0]

    async def _batch(self, queries: list[SearchQuery], context: str) -> list[SearchResults]:
        listing = "\n".join(f"{i + 1}. {q.text}" for i, q in enumerate(queries))
        messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    "Run each of these web searches exactly once, using the query text as written. "
                    "Do not run other searches. After the searches, reply with the single word: done.\n\n"
                    f"Research context (for your information only): {context}\n\nQueries:\n{listing}"
                ),
            }
        ]
        tool = {"type": self.tool_type, "name": "web_search", "max_uses": len(queries)}
        found: list[SearchResult] = []
        errors: list[str] = []
        for _ in range(4):
            try:
                resp = await self.client.messages.create(  # type: ignore[call-overload]
                    model=self.model_id,
                    max_tokens=4000,
                    messages=messages,
                    tools=[tool],
                    **({"output_config": {"effort": "low"}} if self.model_id in DYNAMIC_MODELS else {}),
                )
            except anthropic.APIError as exc:
                errors.append(type(exc).__name__)
                self.meter.retries += 1
                break
            pin, pout = price_for(self.model_id)
            u = resp.usage
            inp = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
            self.meter.model_input_tokens += inp
            self.meter.model_output_tokens += u.output_tokens or 0
            self.meter.model_cost += (inp * pin + (u.output_tokens or 0) * pout) / 1e6
            stu = getattr(u, "server_tool_use", None)
            n = getattr(stu, "web_search_requests", 0) or 0
            self.meter.search_requests += n
            self.meter.search_cost += n * SEARCH_PRICE_USD
            last_query = None
            for block in resp.content:
                btype = getattr(block, "type", "")
                if btype == "server_tool_use":
                    last_query = (getattr(block, "input", {}) or {}).get("query")
                if btype == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for r in content:
                            url = getattr(r, "url", None)
                            if url:
                                found.append(
                                    SearchResult(
                                        url=url,
                                        title=getattr(r, "title", None),
                                        page_age=getattr(r, "page_age", None),
                                        query=last_query,
                                    )
                                )
                    else:
                        errors.append(str(getattr(content, "error_code", "search_error")))
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        # Attribute results back to queries by their query text when available.
        out = []
        for q in queries:
            rs = [r for r in found if r.query == q.text]
            out.append(SearchResults(query=q, results=rs))
        unmatched = [r for r in found if r.query not in {q.text for q in queries}]
        if unmatched and out:
            out[0].results.extend(unmatched)
        if errors and out:
            out[0].error = ",".join(errors)
        return out

    async def search_many(self, queries: list[SearchQuery], context: str) -> list[SearchResults]:
        if not queries:
            return []
        chunks = [queries[i : i + 4] for i in range(0, len(queries), 4)]
        results = await asyncio.gather(*(self._batch(c, context) for c in chunks))
        return [r for chunk in results for r in chunk]
