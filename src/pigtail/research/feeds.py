"""First-party feed discovery: a product's own blog feed lists its milestone posts with dates.

Generic for any target: try common RSS/Atom locations on the product's own hosts. Only titles,
links and dates are read; the posts themselves go through the normal triage → fetch → extract path.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from pigtail.domain.time import iso, parse_dt
from pigtail.providers.base import Meter
from pigtail.providers.fetchers.web import USER_AGENT, WebFetcher

FEED_PATHS = (
    "/rss/",
    "/feed/",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/index.xml",
    "/blog/rss.xml",
    "/blog/feed.xml",
    "/blog/rss/",
    "/blog/feed/",
    "/blog/index.xml",
    "/blog/atom.xml",
)


@dataclass
class FeedItem:
    title: str
    url: str
    published: str | None


def _text(el: ET.Element | None) -> str | None:
    return el.text.strip() if el is not None and el.text else None


def parse_feed(xml: str) -> list[FeedItem]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    items: list[FeedItem] = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date = None
        for ch in it:
            ctag = ch.tag.split("}")[-1]
            if ctag == "title":
                title = _text(ch)
            elif ctag == "link":
                link = ch.get("href") or _text(ch)
            elif ctag in ("pubDate", "published", "updated", "date") and not date:
                raw = _text(ch)
                if raw:
                    dt = parse_dt(raw)
                    if dt is None:
                        try:
                            dt = parsedate_to_datetime(raw)
                        except (TypeError, ValueError):
                            dt = None
                    date = iso(dt) if dt else None
        if title and link and link.startswith("http"):
            items.append(FeedItem(title=title, url=link, published=date))
    return items


async def discover_feeds(hosts: list[str], fetcher: WebFetcher, meter: Meter, limit: int = 120) -> list[FeedItem]:
    found: dict[str, FeedItem] = {}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:

        async def probe(host: str, path: str) -> list[FeedItem]:
            url = f"https://{host}{path}"
            if not await fetcher._allowed(url):
                return []
            meter.http_requests += 1
            try:
                r = await http.get(url)
            except httpx.HTTPError:
                return []
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200 or ("xml" not in ctype and "rss" not in ctype and "atom" not in ctype):
                return []
            return parse_feed(r.text[:3_000_000])

        for host in hosts:
            results = await asyncio.gather(*(probe(host, p) for p in FEED_PATHS))
            for items in results:
                for it in items:
                    found.setdefault(it.url, it)
            if len(found) >= limit:
                break
    return sorted(found.values(), key=lambda i: i.published or "", reverse=True)[:limit]


def _locs(xml: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Return (urls with lastmod, child sitemaps) from a sitemap or sitemap index."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], []
    urls: list[tuple[str, str | None]] = []
    children: list[str] = []
    for el in root:
        tag = el.tag.split("}")[-1]
        loc = lastmod = None
        for ch in el:
            ctag = ch.tag.split("}")[-1]
            if ctag == "loc":
                loc = _text(ch)
            elif ctag == "lastmod":
                lastmod = _text(ch)
        if not loc:
            continue
        if tag == "sitemap":
            children.append(loc)
        else:
            urls.append((loc, lastmod))
    return urls, children


def _slug_title(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip() or url


async def discover_sitemap_posts(
    hosts: list[str], fetcher: WebFetcher, meter: Meter, limit: int = 150
) -> list[FeedItem]:
    """List article-like URLs from the product's own sitemaps (titles derived from URL slugs)."""
    out: dict[str, FeedItem] = {}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:

        async def get(url: str) -> str | None:
            if not await fetcher._allowed(url):
                return None
            meter.http_requests += 1
            try:
                r = await http.get(url)
            except httpx.HTTPError:
                return None
            return r.text[:5_000_000] if r.status_code == 200 and "xml" in r.headers.get("content-type", "") else None

        for host in hosts:
            root = await get(f"https://{host}/sitemap.xml")
            if not root:
                continue
            urls, children = _locs(root)
            wanted = [c for c in children if any(k in c.lower() for k in ("post", "blog", "article", "news"))]
            for child in (wanted or children)[:4]:
                xml = await get(child)
                if xml:
                    urls += _locs(xml)[0]
            for loc, _lastmod in urls:
                path = loc.split("//", 1)[-1].split("/", 1)[-1] if "//" in loc else loc
                is_article = (
                    ("blog" in loc.lower() or host.startswith("blog.") or "/posts/" in loc or "/news/" in loc)
                    and path.count("/") <= 3
                    and len(path.strip("/")) > 8
                )
                if is_article and loc not in out and not loc.rstrip("/").endswith(("/blog", "/tag", "/author")):
                    if "/tag/" in loc or "/author/" in loc or "/page/" in loc:
                        continue
                    out[loc] = FeedItem(
                        title=_slug_title(loc), url=loc, published=None
                    )  # lastmod is not a publication date
            if len(out) >= limit:
                break
    return list(out.values())[:limit]
