"""Hacker News discovery via the public HN Search API by Algolia (policy: source-policies/hacker-news.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from pigtail.logging import get_logger
from pigtail.providers.base import Meter

log = get_logger("hn")
ALGOLIA = "https://hn.algolia.com/api/v1/search"


@dataclass
class HNStory:
    id: str
    title: str
    url: str | None
    author: str | None
    created_at: str
    points: int
    num_comments: int
    matched_by: str

    @property
    def item_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"

    @property
    def api_url(self) -> str:
        return f"https://hn.algolia.com/api/v1/items/{self.id}"

    @property
    def is_show_hn(self) -> bool:
        return self.title.lower().startswith("show hn")

    @property
    def is_launch_hn(self) -> bool:
        return self.title.lower().startswith("launch hn")


class HackerNewsClient:
    def __init__(self, meter: Meter, timeout: int = 20):
        self.http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "pigtail/0.1"})
        self.meter = meter
        self.errors: list[str] = []

    async def close(self) -> None:
        await self.http.aclose()

    async def _search(self, params: dict) -> list[dict]:
        self.meter.http_requests += 1
        try:
            r = await self.http.get(ALGOLIA, params=params)
            r.raise_for_status()
            return r.json().get("hits", [])
        except httpx.HTTPError as exc:
            self.errors.append(type(exc).__name__)
            return []

    async def stories_for(
        self, *, name: str, repo_url: str | None, homepage: str | None, limit: int = 20
    ) -> list[HNStory]:
        found: dict[str, HNStory] = {}

        def add(hit: dict, how: str) -> None:
            oid = str(hit.get("objectID"))
            if not oid or oid in found or not hit.get("title") or not hit.get("created_at"):
                return
            found[oid] = HNStory(
                id=oid,
                title=hit["title"],
                url=hit.get("url"),
                author=hit.get("author"),
                created_at=hit["created_at"],
                points=int(hit.get("points") or 0),
                num_comments=int(hit.get("num_comments") or 0),
                matched_by=how,
            )

        url_queries = []
        if repo_url:
            url_queries.append(repo_url.replace("https://", "").rstrip("/"))
        if homepage:
            host = re.sub(r"^https?://(www\.)?", "", homepage).split("/")[0]
            if host and "github.com" not in host:
                url_queries.append(host)
        for q in url_queries:
            for hit in await self._search(
                {"query": q, "tags": "story", "restrictSearchableAttributes": "url", "hitsPerPage": 50}
            ):
                url = (hit.get("url") or "").lower()
                if q.lower() in url:
                    add(hit, f"url:{q}")

        # Title mentions (Show HN / Launch HN or the exact name as a word).
        if len(name) >= 3:
            pat = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.I)
            for tag in ("show_hn", "story"):
                hits = await self._search(
                    {"query": name, "tags": tag, "restrictSearchableAttributes": "title", "hitsPerPage": 50}
                )
                for hit in hits:
                    title = hit.get("title") or ""
                    if not pat.search(title):
                        continue
                    if tag == "story" and int(hit.get("points") or 0) < 20:
                        continue
                    url = (hit.get("url") or "").lower()
                    related_url = any(q.lower() in url for q in url_queries)
                    # Strong only if the post links to the project; title-only matches (even
                    # "Show HN: <name> ...") may be another product and go to model triage.
                    if related_url:
                        add(hit, f"title:{tag}")
                    else:
                        add(hit, "title:mention")
        # URL matches are strong evidence and always kept; title-only mentions are capped separately.
        strong = sorted((s for s in found.values() if s.matched_by != "title:mention"), key=lambda s: -s.points)
        weak = sorted((s for s in found.values() if s.matched_by == "title:mention"), key=lambda s: -s.points)
        return strong[:limit] + weak[:10]
