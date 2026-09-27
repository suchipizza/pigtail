"""In-process fakes of the Product Hunt GraphQL API and the Bluesky AppView search for the ADR-085
tests. No network. Every product name, slug, text, handle and DID is made up
(`maintainer-a.example`, `did:plc:` + synthetic letters, `SYNTH-…`). Both return person-level
fields on purpose (makers, author objects) so the tests can show pigtail never asks for, reads or
keeps them.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

PH_TOKEN = "fake-ph-token-not-secret"


def ph_post(
    pid: str,
    name: str,
    *,
    slug: str | None = None,
    tagline: str = "SYNTH-TAGLINE",
    description: str = "SYNTH-DESCRIPTION",
    votes: int = 10,
    comments: int = 2,
    created: datetime,
    featured: datetime | None = None,
    topics: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A synthetic Product Hunt post node; `_topics` is the fake's own index, and `makers` a
    person-level object the API would have (pigtail never requests it)."""
    return {
        "id": pid,
        "name": name,
        "slug": slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
        "tagline": tagline,
        "description": description,
        "votesCount": votes,
        "commentsCount": comments,
        "createdAt": created.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "featuredAt": None
        if featured is None
        else featured.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "_topics": list(topics),
        "makers": [{"name": "SYNTH-MAKER-NAME", "username": "synth-maker-handle"}],
    }


def _dt(s: str | None) -> datetime | None:
    return None if not s else datetime.fromisoformat(s.replace("Z", "+00:00"))


class FakeProductHunt:
    """`POST https://api.producthunt.com/v2/api/graphql`: `post(slug|id)` and `posts(topic, …)`.
    Answers only the fields the query names (like GraphQL). Rate-limit headers from
    `limit` / `remaining` / `reset`; `script` holds canned responses served first."""

    FIELDS = re.compile(r"\{\s*([a-zA-Z ]+)\s*\}")

    def __init__(self, posts: list[dict[str, Any]] | None = None) -> None:
        self.posts = list(posts or [])
        self.requests: list[dict[str, Any]] = []
        self.limit = 6250
        self.remaining = 6000
        self.reset = 900
        self.script: list[Any] = []
        self.page_size = 20

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def _headers(self) -> dict[str, str]:
        return {
            "X-Rate-Limit-Limit": str(self.limit),
            "X-Rate-Limit-Remaining": str(self.remaining),
            "X-Rate-Limit-Reset": str(self.reset),
            "Content-Type": "application/json",
        }

    def _node(self, p: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        return {f: p.get(f) for f in fields}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.host == "api.producthunt.com", req.url
        assert req.url.path == "/v2/api/graphql"
        assert req.headers.get("Authorization") == f"Bearer {PH_TOKEN}"
        body = json.loads(req.content)
        self.requests.append(body)
        if self.script:
            item = self.script.pop(0)
            resp = item(req) if callable(item) else item
            if resp is not None:
                return resp
        q, v = body["query"], body.get("variables") or {}
        fields = self.FIELDS.findall(q)[-1].split()
        if "posts(" in q:
            lo, hi = _dt(v.get("postedAfter")), _dt(v.get("postedBefore"))
            hits = [
                p
                for p in self.posts
                if v["topic"] in p["_topics"]
                and (lo is None or _dt(p["createdAt"]) >= lo)  # type: ignore[operator]
                and (hi is None or _dt(p["createdAt"]) <= hi)  # type: ignore[operator]
            ]
            hits.sort(key=lambda p: p["createdAt"], reverse=True)
            start = int(v.get("after") or 0)
            n = int(v.get("first") or self.page_size)
            page = hits[start : start + n]
            end = start + len(page)
            data: dict[str, Any] = {
                "posts": {
                    "pageInfo": {"hasNextPage": end < len(hits), "endCursor": str(end)},
                    "edges": [{"node": self._node(p, fields)} for p in page],
                }
            }
        else:
            key, val = ("slug", v["slug"]) if "slug" in v else ("id", v["id"])
            p = next((x for x in self.posts if x[key] == val), None)
            data = {"post": None if p is None else self._node(p, fields)}
        self.remaining = max(0, self.remaining - 10)
        return httpx.Response(200, json={"data": data}, headers=self._headers())


# --- Bluesky ------------------------------------------------------------------------------------
HANDLE_A = "maintainer-a.example"
DID_B = "did:plc:abcdefghijklmnopqrstuvwx"  # synthetic (24 base32 letters)
HANDLE_C = "maintainer-c.bsky.social"
HANDLE_OTHER = "someone-else.example"


def bsky_post(
    n: int,
    author: str,
    when: datetime,
    links: list[str],
    *,
    did: str | None = None,
    text: str = "SYNTH-POST-TEXT",
    indexed: datetime | None = None,
) -> dict[str, Any]:
    """A synthetic post view as searchPosts returns it, author object included."""
    facets = [
        {"index": {"byteStart": 0, "byteEnd": 1},
         "features": [{"$type": "app.bsky.richtext.facet#link", "uri": u}]}
        for u in links
    ]  # fmt: skip
    return {
        "uri": f"at://{did or 'did:plc:zzzzzzzzzzzzzzzzzzzzzzzz'}/app.bsky.feed.post/{n:08d}",
        "cid": f"bafy{n:08d}",
        "author": {
            "did": did or "did:plc:zzzzzzzzzzzzzzzzzzzzzzzz",
            "handle": author,
            "displayName": "SYNTH-DISPLAY-NAME",
        },
        "record": {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "facets": facets,
        },
        "indexedAt": (indexed or when).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "likeCount": 3,
        "repostCount": 1,
        "replyCount": 0,
    }


class FakeBluesky:
    """`GET https://api.bsky.app/xrpc/app.bsky.feed.searchPosts` with the author, url, since,
    until, sort, limit and cursor filters. Any other path is an assertion error (pigtail never
    reads a feed or a profile), and `com.atproto.identity.resolveHandle` (400 for a handle that
    names no account). `fail` answers 503 to every request; `fail_urls` to searches of those
    URLs."""

    def __init__(self, posts: list[dict[str, Any]] | None = None) -> None:
        self.posts = list(posts or [])
        self.requests: list[httpx.Request] = []
        self.fail = False
        self.fail_urls: set[str] = set()
        self.page_size: int | None = None
        # handles without posts in this fake that still resolve to an account
        self.known: dict[str, str] = {
            HANDLE_C: "did:plc:cccccccccccccccccccccccc",
            "maintainer-d.example": "did:plc:dddddddddddddddddddddddd",
        }

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.method == "GET" and req.url.host == "api.bsky.app", req.url
        self.requests.append(req)
        p = dict(req.url.params)
        if req.url.path == "/xrpc/com.atproto.identity.resolveHandle":
            if self.fail:
                return httpx.Response(503, json={"error": "unavailable"})
            for x in self.posts:
                if x["author"]["handle"] == p["handle"]:
                    return httpx.Response(200, json={"did": x["author"]["did"]})
            if p["handle"] in self.known:
                return httpx.Response(200, json={"did": self.known[p["handle"]]})
            return httpx.Response(400, json={"error": "InvalidRequest"})
        assert req.url.path == "/xrpc/app.bsky.feed.searchPosts", req.url.path
        if self.fail or p.get("url") in self.fail_urls:
            return httpx.Response(503, json={"error": "unavailable"})
        author, url = p["author"], p["url"]
        lo, hi = _dt(p.get("since")), _dt(p.get("until"))
        hits = []
        for x in self.posts:
            if author not in (x["author"]["handle"], x["author"]["did"]):
                continue
            t = _dt(x["record"]["createdAt"])
            assert t is not None
            if (lo and t < lo) or (hi and t > hi):
                continue
            links = [f["uri"] for fc in x["record"]["facets"] for f in fc["features"]]
            if not any(u.lower().startswith(url.lower()) for u in links):
                continue
            hits.append(x)
        hits.sort(key=lambda x: x["record"]["createdAt"], reverse=True)
        start = int(p.get("cursor") or 0)
        n = self.page_size or int(p.get("limit") or 25)
        page = hits[start : start + n]
        body: dict[str, Any] = {"posts": page, "hitsTotal": len(hits)}
        if start + n < len(hits):
            body["cursor"] = str(start + n)
        return httpx.Response(200, json=body)


def did_of(fake: FakeBluesky, account: str) -> str:
    """The DID the fake resolves `account` to (a DID is returned unchanged)."""
    if account.startswith("did:"):
        return account
    for x in fake.posts:
        if x["author"]["handle"] == account:
            return str(x["author"]["did"])
    return fake.known[account]
