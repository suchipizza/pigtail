"""Product Hunt launches via the official API v2, with the user's own developer token (policy:
source-policies/product-hunt.yaml).

Optional: used only when PRODUCTHUNT_TOKEN is set. The API cannot search by name or website, so the
launch address (slug) comes from Product Hunt links on the product's own homepage or README (a
"Featured on Product Hunt" badge), from Product Hunt links found by web search, or is guessed from the
product's name. A guessed or searched launch is kept only if its name contains the product's name and
it is not older than the project, since many products share a name. Pigtail keeps the launch name,
date, upvotes, comment count and link; never makers, comments or users. Product Hunt pages are never
fetched; the token is sent only to the API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from pigtail import ENGINE_VERSION, REPO_URL
from pigtail.logging import get_logger
from pigtail.providers.base import Meter

log = get_logger("product_hunt")
API = "https://api.producthunt.com/v2/api/graphql"
USER_AGENT = f"pigtail/{ENGINE_VERSION} (+{REPO_URL})"
FIELDS = "id name slug createdAt featuredAt votesCount commentsCount"
# A launch may come a little before the repository was created (a renamed or moved repository).
NOT_BEFORE_SLACK = timedelta(days=90)

_SLUG = re.compile(r"producthunt\.com/(?:posts|products)/([a-z0-9][a-z0-9-]*)", re.I)
_BADGE_ID = re.compile(r"producthunt\.com/widgets/embed-image/[^\s\"'<>)]*?post_id=(\d+)", re.I)


class ProductHuntAuthError(Exception):
    """The Product Hunt API rejected the token."""


@dataclass
class ProductHuntLaunch:
    id: str
    name: str
    slug: str
    launched_at: datetime
    featured: bool
    votes: int
    comments: int
    found_by: str  # own_site | web_search | guess

    @property
    def url(self) -> str:
        return f"https://www.producthunt.com/posts/{self.slug}"


def refs_in(text: str) -> list[str]:
    """Product Hunt launch references in a page or README: slugs, and "id:<n>" for badge images."""
    refs = [m.lower() for m in _SLUG.findall(text or "")] + [f"id:{n}" for n in _BADGE_ID.findall(text or "")]
    return list(dict.fromkeys(refs))


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def guess_slugs(words: list[str]) -> list[str]:
    """Slugs Product Hunt gives to a product's launches: "tally", "tally-2", "tally-2-0" …"""
    out: list[str] = []
    for w in dict.fromkeys(_slugify(w) for w in words if w):
        if w:
            out += [w, f"{w}-2", f"{w}-2-0"]
    return list(dict.fromkeys(out))


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class ProductHuntClient:
    def __init__(
        self,
        token: str,
        meter: Meter,
        timeout: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.token = token
        # No default Authorization header: the token goes to the API only, never to a homepage.
        self.http = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport, follow_redirects=True
        )
        self.meter = meter
        self.errors: list[str] = []

    async def close(self) -> None:
        await self.http.aclose()

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        self.meter.http_requests += 1
        r = await self.http.post(
            API,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        if r.status_code in (401, 403):
            raise ProductHuntAuthError(f"Product Hunt rejected the token (HTTP {r.status_code}).")
        r.raise_for_status()
        return r.json().get("data") or {}

    async def check(self) -> None:
        """One small read to confirm the token works. Raises ProductHuntAuthError if it does not."""
        await self._query("query { posts(first: 1) { edges { node { id } } } }")

    async def page_refs(self, url: str) -> list[str]:
        """Product Hunt references on the product's own homepage (one GET, nothing kept)."""
        self.meter.http_requests += 1
        try:
            r = await self.http.get(url)
            return refs_in(r.text[:400_000]) if r.status_code < 400 else []
        except httpx.HTTPError:
            return []

    async def _post(self, ref: str) -> dict | None:
        key, value = ("id", ref[3:]) if ref.startswith("id:") else ("slug", ref)
        q = f"query($v: {'ID' if key == 'id' else 'String'}!) {{ post({key}: $v) {{ {FIELDS} }} }}"
        try:
            return (await self._query(q, {"v": value})).get("post")
        except ProductHuntAuthError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            self.errors.append(type(exc).__name__)
            return None

    async def launches_for(
        self,
        name: str,
        *,
        own_refs: list[str],
        search_refs: list[str],
        guess_words: list[str],
        not_before: date | None,
        max_lookups: int = 15,
    ) -> list[ProductHuntLaunch]:
        """The product's launches. Own-site references are trusted; searched and guessed ones must name
        the product and not be older than the project. Without a project date, guesses are not tried."""
        tries = [(r, "own_site") for r in own_refs] + [(r, "web_search") for r in search_refs if r not in own_refs]
        if not_before:
            tries += [(s, "guess") for s in guess_slugs(guess_words) if s not in own_refs + search_refs]
        found: dict[str, ProductHuntLaunch] = {}
        for ref, how in tries[:max_lookups]:
            p = await self._post(ref)
            if not p or p.get("id") in found or not p.get("slug"):
                continue
            when = p.get("featuredAt") or p.get("createdAt")
            if not when:
                continue
            launched = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(UTC)
            if how != "own_site":
                if _key(name) not in _key(p.get("name") or ""):
                    continue  # another product with a similar slug
                if not_before and launched.date() < not_before - NOT_BEFORE_SLACK:
                    continue  # older than the project: another product with the same name
            found[p["id"]] = ProductHuntLaunch(
                id=str(p["id"]),
                name=p.get("name") or p["slug"],
                slug=p["slug"],
                launched_at=launched,
                featured=bool(p.get("featuredAt")),
                votes=int(p.get("votesCount") or 0),
                comments=int(p.get("commentsCount") or 0),
                found_by=how,
            )
        return sorted(found.values(), key=lambda x: x.launched_at)
