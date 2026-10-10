"""Reddit posts via Reddit's official Data API, with the user's own app keys (policy: source-policies/reddit.yaml).

Optional: used only when REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set. Pigtail signs in with
application-only OAuth (client credentials), searches for posts that link or mention the project (plus
the same authors' other posts that name it), and keeps only metadata: title, link, subreddit, date,
upvotes and comment count. Post text and authors are read only to check that the post is about the
project; they are never stored. Removed and deleted posts are skipped.
"""

from __future__ import annotations

import re
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
    matched_by: str  # link | title | text | author (names the project; same author as a linked post)

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

    async def _listing(self, path: str, params: dict) -> list[dict]:
        self.meter.http_requests += 1
        try:
            r = await self.http.get(
                f"{API}{path}",
                params=params | {"limit": 100, "raw_json": 1},
                headers={"Authorization": f"bearer {self._token}"},
            )
            r.raise_for_status()
            return [c.get("data", {}) for c in r.json().get("data", {}).get("children", [])]
        except (httpx.HTTPError, ValueError) as exc:
            self.errors.append(type(exc).__name__)
            return []

    async def _search(self, q: str) -> list[dict]:
        return await self._listing("/search", {"q": q, "sort": "relevance", "t": "all", "type": "link"})

    async def _submitted(self, author: str) -> list[dict]:
        return await self._listing(f"/user/{author}/submitted", {"sort": "new", "t": "all"})

    async def posts_for(
        self,
        terms: list[str],
        *,
        names: list[str] | None = None,
        min_score: int = 1,
        limit: int = 30,
        max_authors: int = 5,
    ) -> list[RedditPost]:
        """Posts about the project: a post is kept if one of `terms` (a repository path or a domain)
        literally appears in its link, title or text, or if it names the project (one of `names`, as a
        word) and was posted by someone who also posted a post kept on `terms`.

        Reddit's search is fuzzy, so every result is checked locally. The second rule finds the same
        maker's other posts (a milestone post that says "/brag" but not the repository path); authors are
        used only during the search and never stored. Removed, deleted and NSFW posts are skipped.
        """
        if self._token is None:
            await self.sign_in()
        lowered = [t.lower() for t in terms]
        name_res = [
            re.compile(rf"(?<![\w.-]){re.escape(n.lower())}(?![\w-])")
            for n in dict.fromkeys(names or [])
            if len(n) >= 3
        ]
        found: dict[str, RedditPost] = {}
        authors: dict[str, int] = {}  # author -> number of posts kept on `terms`
        maybe: list[dict] = []  # posts that name the project; kept only if their author is known

        def consider(d: dict) -> None:
            pid = d.get("id")
            if not pid or pid in found or not d.get("title") or not d.get("created_utc"):
                return
            author = d.get("author") or ""
            if d.get("removed_by_category") or author in ("", "[deleted]", "AutoModerator") or d.get("over_18"):
                return
            text = d.get("selftext") or ""
            if text in ("[removed]", "[deleted]") or int(d.get("score") or 0) < min_score:
                return
            link, title, body = (d.get("url") or "").lower(), d["title"].lower(), text.lower()
            if any(t in link for t in lowered):
                how = "link"
            elif any(t in title for t in lowered):
                how = "title"
            elif any(t in body for t in lowered):
                how = "text"
            elif any(r.search(title) or r.search(body) for r in name_res):
                maybe.append(d)
                return
            else:
                return  # Reddit matched something else; not about this project
            authors[author] = authors.get(author, 0) + 1
            found[pid] = _post(d, how)

        for term in terms:
            for q in (f'"{term}"', f"url:{term}"):
                for d in await self._search(q):
                    consider(d)
        for name in names or []:
            for d in await self._search(f'"{name}"'):
                consider(d)
        for author in sorted(authors, key=lambda a: -authors[a])[:max_authors] if name_res else []:
            for d in await self._submitted(author):
                consider(d)
        for d in maybe:
            if d["id"] not in found and d.get("author") in authors:
                found[d["id"]] = _post(d, "author")
        return sorted(found.values(), key=lambda p: -p.score)[:limit]


def _post(d: dict, how: str) -> RedditPost:
    return RedditPost(
        id=d["id"],
        title=d["title"],
        subreddit=d.get("subreddit") or "",
        permalink=d.get("permalink") or f"/comments/{d['id']}/",
        created_utc=float(d["created_utc"]),
        score=int(d.get("score") or 0),
        num_comments=int(d.get("num_comments") or 0),
        matched_by=how,
    )
