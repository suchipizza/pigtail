"""Policy-obeying web fetcher. Page text lives only in memory for claim extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from pigtail.logging import get_logger
from pigtail.policies.models import SourcePolicy
from pigtail.providers.base import Meter

log = get_logger("fetch")
USER_AGENT = "Mozilla/5.0 (compatible; pigtail/0.1; +https://github.com/suchipizza/pigtail)"
ROBOTS_AGENT = "pigtail"
PARSER_VERSION = "trafilatura-2"
MAX_BYTES = 3_000_000


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: str  # SourceFetch.status vocabulary
    http_status: int | None = None
    text: str | None = None  # transient: never written to the bundle
    title: str | None = None
    author: str | None = None
    published: str | None = None  # YYYY-MM-DD as declared by the page metadata
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error_code: str | None = None


class WebFetcher:
    def __init__(self, meter: Meter, timeout: int = 20, concurrency: int = 6):
        self.http = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        )
        self.meter = meter
        self.sem = asyncio.Semaphore(concurrency)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def _allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        async with self._robots_lock:
            if base not in self._robots:
                rp: RobotFileParser | None = RobotFileParser()
                try:
                    self.meter.http_requests += 1
                    r = await self.http.get(base + "/robots.txt")
                    if r.status_code == 200:
                        rp.parse(r.text.splitlines())  # type: ignore[union-attr]
                    else:
                        rp = None  # no robots.txt: allowed
                except httpx.HTTPError:
                    rp = None
                self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(ROBOTS_AGENT, url)

    async def fetch(self, url: str, policy: SourcePolicy) -> FetchedPage:
        if not policy.access.automated_retrieval_allowed:
            return FetchedPage(url=url, final_url=url, status="skipped_policy", error_code="retrieval_not_permitted")
        if not await self._allowed(url):
            return FetchedPage(url=url, final_url=url, status="skipped_policy", error_code="robots_txt")
        async with self.sem:
            for attempt in range(2):
                try:
                    self.meter.http_requests += 1
                    r = await self.http.get(url)
                    break
                except httpx.HTTPError as exc:
                    if attempt == 1:
                        return FetchedPage(
                            url=url, final_url=url, status="network_failed", error_code=type(exc).__name__
                        )
                    self.meter.retries += 1
                    await asyncio.sleep(2)
        self.meter.pages_fetched += 1
        page = FetchedPage(
            url=url,
            final_url=str(r.url),
            status="success",
            http_status=r.status_code,
            etag=r.headers.get("etag"),
            last_modified=r.headers.get("last-modified"),
        )
        if r.status_code in (401, 403, 451):
            page.status, page.error_code = "blocked", f"http_{r.status_code}"
            return page
        if r.status_code == 404 or r.status_code == 410:
            page.status, page.error_code = "not_found", f"http_{r.status_code}"
            return page
        if r.status_code == 429:
            page.status, page.error_code = "rate_limited", "http_429"
            return page
        if r.status_code >= 400:
            page.status, page.error_code = "network_failed", f"http_{r.status_code}"
            return page
        body = r.content[:MAX_BYTES]
        page.content_hash = "sha256:" + hashlib.sha256(body).hexdigest()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            page.status, page.error_code = "parse_failed", "unsupported_content_type"
            return page
        html = body.decode(r.encoding or "utf-8", errors="replace")
        try:
            extracted = trafilatura.extract(
                html,
                url=str(r.url),
                output_format="json",
                with_metadata=True,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
        except Exception as exc:
            page.status, page.error_code = "parse_failed", type(exc).__name__
            return page
        if not extracted:
            page.status, page.error_code = "parse_failed", "no_main_text"
            return page
        data = json.loads(extracted)
        page.text = data.get("text") or data.get("raw_text")
        page.title = data.get("title")
        page.author = data.get("author")
        page.published = data.get("date")
        if not page.text or len(page.text) < 200:
            page.status, page.error_code = "parse_failed", "too_little_text"
        return page
