"""Reddit posts via Reddit's official Data API, with the user's own app keys (policy: source-policies/reddit.yaml).

Optional: used only when REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set. Pigtail signs in with
application-only OAuth (client credentials), searches for posts that link or mention the project, and
keeps only metadata: title, link, subreddit, date, upvotes and comment count. Post text is read only to
check that the post really mentions the project; it is never stored. Removed and deleted posts are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from pigtail import ENGINE_VERSION, REPO_URL
from pigtail.logging import get_logger
from pigtail.providers.base import Meter

log = get_logger("reddit")
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
# Reddit's API rules ask for a unique, descriptive User-Agent.
USER_AGENT = f"python:pigtail:{ENGINE_VERSION} (+{REPO_URL})"


class RedditAuthError(Exception):
    """The Reddit API rejected the app keys."""


@dataclass
class RedditPost:
    id: str
    title: str
    subreddit: str
    permalink: str
    created_utc: float
    score: int
    num_comments: int
    matched_by: str  # link | title | text

    @property
    def url(self) -> str:
        return f"https://www.reddit.com{self.permalink}"

    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.created_utc, UTC)


class RedditClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        meter: Meter,
        timeout: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.auth = (client_id, client_secret)
        self.http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport)
        self.meter = meter
        self.errors: list[str] = []
        self._token: str | None = None

    async def close(self) -> None:
        await self.http.aclose()

    async def sign_in(self) -> None:
        """Get an application-only token. Raises RedditAuthError if the keys are wrong."""
        self.meter.http_requests += 1
        r = await self.http.post(TOKEN_URL, auth=self.auth, data={"grant_type": "client_credentials"})
        if r.status_code in (401, 403) or (r.status_code == 200 and "access_token" not in r.json()):
            raise RedditAuthError(f"Reddit rejected the app keys (HTTP {r.status_code}).")
        r.raise_for_status()
        self._token = r.json()["access_token"]

    async def _search(self, q: str) -> list[dict]:
        self.meter.http_requests += 1
        try:
            r = await self.http.get(
                f"{API}/search",
                params={"q": q, "sort": "relevance", "t": "all", "type": "link", "limit": 100, "raw_json": 1},
                headers={"Authorization": f"bearer {self._token}"},
            )
            r.raise_for_status()
            return [c.get("data", {}) for c in r.json().get("data", {}).get("children", [])]
        except (httpx.HTTPError, ValueError) as exc:
            self.errors.append(type(exc).__name__)
            return []

    async def posts_for(self, terms: list[str], *, min_score: int = 3, limit: int = 15) -> list[RedditPost]:
        """Posts whose link, title or text contains one of `terms` (a repository path or a domain).

        Reddit's search is fuzzy, so every result is checked locally: a post is kept only if a term
        literally appears in it. Removed, deleted and NSFW posts are skipped.
        """
        if self._token is None:
            await self.sign_in()
        found: dict[str, RedditPost] = {}
        for term in terms:
            t = term.lower()
            for q in (f'"{term}"', f"url:{term}"):
                for d in await self._search(q):
                    pid = d.get("id")
                    if not pid or pid in found or not d.get("title") or not d.get("created_utc"):
                        continue
                    if d.get("removed_by_category") or d.get("author") == "[deleted]" or d.get("over_18"):
                        continue
                    text = d.get("selftext") or ""
                    if text in ("[removed]", "[deleted]"):
                        continue
                    if t in (d.get("url") or "").lower():
                        how = "link"
                    elif t in d["title"].lower():
                        how = "title"
                    elif t in text.lower():
                        how = "text"
                    else:
                        continue  # Reddit matched something else; not about this project
                    if int(d.get("score") or 0) < min_score:
                        continue
                    found[pid] = RedditPost(
                        id=pid,
                        title=d["title"],
                        subreddit=d.get("subreddit") or "",
                        permalink=d.get("permalink") or f"/comments/{pid}/",
                        created_utc=float(d["created_utc"]),
                        score=int(d.get("score") or 0),
                        num_comments=int(d.get("num_comments") or 0),
                        matched_by=how,
                    )
        return sorted(found.values(), key=lambda p: -p.score)[:limit]
