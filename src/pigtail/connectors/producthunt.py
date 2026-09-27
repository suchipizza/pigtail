"""Product Hunt API v2 (GraphQL) for view B's launch events (ADR-085; TM-16 as amended).

Official API only: `POST https://api.producthunt.com/v2/api/graphql` with
`Authorization: Bearer $PH_API_TOKEN`, a developer token of the operator's own (read-only public
scope). The schema is public (github.com/producthunt/producthunt-api, `schema.graphql`).

What pigtail asks for, and nothing else (`POST_FIELDS`): a post's `id`, `name`, `slug`,
`tagline`, `description`, `votesCount`, `commentsCount`, `createdAt` and `featuredAt`. The
**person-level objects of the schema (`makers`, `user`, `comments`, `votes`, hunters) are never
requested**; `url`, `website` (a Product Hunt redirect link, not the product's domain) and
`topics` are not needed and not requested either. Two queries use them:

- `post(slug: $slug)` (and `post(id: $id)`, to re-read a post the topic scan found, after a
  resume) for one post;
- `posts(topic: $topic, postedAfter, postedBefore, order: NEWEST, first: 20, after: $cursor)`
  for one page of a topic's posts inside the brief's window.

There is no text or URL search in the API, which is why the matching (slug candidates and a
topic scan) lives in `pigtail.briefs.launch_sources`.

**Rate limits.** The API meters GraphQL by complexity points per 15-minute window (source
matrix: 6,250) and reports the budget in response headers (api.producthunt.com/v2/docs/
rate_limits/headers: `X-Rate-Limit-Limit`, `X-Rate-Limit-Remaining`, `X-Rate-Limit-Reset`, the
reset read as seconds until the window resets, or as a Unix time when it is that large; not
re-checked live: no network in this session). The connector reads them after every response and,
once the remaining budget falls to `RESERVE_FRACTION` of the limit, sleeps until the reset before
the next request (or raises `ProductHuntRateLimited` when that is longer than `max_rate_wait`);
a 429 waits for `Retry-After` or the reset. On top of that a conservative client-side token bucket
(`rate_per_second` 0.5 with a 50 % margin: one request every 4 s, 900 an hour).

**Off without a token.** `PH_API_TOKEN` unset means the connector is off (`enabled` false), and
the selection refuses to run when its pre-registered parameters say Product Hunt applies (ADR-085,
`pigtail.briefs.launch_sources.ph_blocked`). The token is never logged, stored or shown.

**Storage.** Raw answers are snapshotted before parsing (snapshot or drop) and dropped right after
parsing by the caller (CB-24); the description may name people, so the snapshot is classed
`person_level_24m` until then. Evidence URLs are overridden (`evidence_url`) so they name only
the repo (or the topic) and the route, never the query text.

Terms (TM-16): the API "must not be used for commercial purposes"; businesses contact
hello@producthunt.com; attribution to Product Hunt is requested. The owner's use is personal and
non-commercial (Directive §1); she writes to Product Hunt herself (H5).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import (
    USER_AGENT,
    Clearance,
    Connector,
    ConnectorDisabled,
    Fetched,
    FetchError,
    Record,
    TermsMetadata,
    parse_retry_after,
)

PH_API = "https://api.producthunt.com/v2/api/graphql"
PH_TOKEN_ENV = "PH_API_TOKEN"
PH_PAGE = 20  # posts per topic page (the API's page size)
# project-level fields only; person-level objects (makers, user, comments, votes) never
POST_FIELDS = (
    "id",
    "name",
    "slug",
    "tagline",
    "description",
    "votesCount",
    "commentsCount",
    "createdAt",
    "featuredAt",
)
_FIELDS = " ".join(POST_FIELDS)
SLUG_QUERY = f"query($slug: String!) {{ post(slug: $slug) {{ {_FIELDS} }} }}"
ID_QUERY = f"query($id: ID!) {{ post(id: $id) {{ {_FIELDS} }} }}"
TOPIC_QUERY = (
    "query($topic: String!, $postedAfter: DateTime, $postedBefore: DateTime, $first: Int!, "
    "$after: String) { posts(topic: $topic, postedAfter: $postedAfter, postedBefore: "
    "$postedBefore, order: NEWEST, first: $first, after: $after) { pageInfo { hasNextPage "
    f"endCursor }} edges {{ node {{ {_FIELDS} }} }} }} }}"
)
QUERIES = (SLUG_QUERY, ID_QUERY, TOPIC_QUERY)
# Evidence URLs name the repo (or topic) and the route only; a repo opt-out finds them by the
# `launch_source_repo=` marker (`pigtail.privacy.requests`, CB-13c).
EVIDENCE_REPO_MARK = "launch_source_repo="
RESERVE_FRACTION = 0.1  # stop and wait for the reset once 10 % of the window's budget is left
DESCRIPTION_CHARS = 2000  # the description is read (in memory) up to this length

PH_TERMS = TermsMetadata(
    terms_url="https://api.producthunt.com/v2/docs",
    terms_basis=(
        "TM-16 (docs/compliance/terms-memos.md, amended 2026-09-27 by ADR-085): official Product "
        "Hunt API v2 (GraphQL), the operator's own developer token (read-only public scope). "
        "The API 'must not be used for commercial purposes'; businesses contact "
        "hello@producthunt.com; attribution to Product Hunt requested. Personal, non-commercial "
        "use only (the owner writes to Product Hunt herself, H5). Project-level post fields "
        "only (no makers, users, comments or votes objects); raw answers dropped after parsing; "
        "rate-limit headers respected with a client-side limiter."
    ),
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=False,
    deletion_obligation=None,
    notes="Complexity-metered: 6,250 points per 15 minutes (source matrix row 16).",
)


class ProductHuntRateLimited(FetchError):
    """The window's budget is spent and its reset is further away than `max_rate_wait`."""


@dataclass(frozen=True)
class PHPost:
    """One Product Hunt post as the selection reads it. `name`, `tagline` and `description`
    are held in memory only (matching and confirmation), never stored."""

    id: str
    name: str | None
    slug: str | None
    tagline: str | None
    description: str | None
    votes: int | None
    comments: int | None
    created_at: datetime | None
    featured_at: datetime | None

    @property
    def event_at(self) -> datetime | None:
        """The launch time of the post (ADR-085): `featuredAt` when the post was featured (the
        day it was on the home page, which is what a Product Hunt launch means), else
        `createdAt` (when it was posted)."""
        return self.featured_at or self.created_at


def _dt(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _str(v: Any, n: int = 300) -> str | None:
    return v[:n] if isinstance(v, str) else None


def parse_post_node(node: Any) -> PHPost | None:
    """A `Post` node, reading only `POST_FIELDS`."""
    if not isinstance(node, dict) or node.get("id") is None:
        return None
    return PHPost(
        id=str(node["id"]),
        name=_str(node.get("name")),
        slug=_str(node.get("slug")),
        tagline=_str(node.get("tagline")),
        description=_str(node.get("description"), DESCRIPTION_CHARS),
        votes=_int(node.get("votesCount")),
        comments=_int(node.get("commentsCount")),
        created_at=_dt(node.get("createdAt")),
        featured_at=_dt(node.get("featuredAt")),
    )


def _data(body: bytes) -> dict[str, Any]:
    doc = json.loads(body)
    if not isinstance(doc, dict):
        raise ValueError("GraphQL answer is not an object")
    data = doc.get("data")
    if not isinstance(data, dict):
        if doc.get("errors"):
            raise ValueError("GraphQL errors without data")
        raise ValueError("GraphQL answer without data")
    return data


def parse_post(body: bytes) -> PHPost | None:
    """The `post(...)` answer (None: no such post)."""
    return parse_post_node(_data(body).get("post"))


def parse_posts_page(body: bytes) -> tuple[list[PHPost], bool, str | None]:
    """(posts, has next page, end cursor) of one `posts(...)` page."""
    conn = _data(body).get("posts") or {}
    info = conn.get("pageInfo") or {}
    posts = [
        p
        for e in conn.get("edges") or []
        if (p := parse_post_node((e or {}).get("node"))) is not None
    ]
    cursor = info.get("endCursor")
    return posts, bool(info.get("hasNextPage")), cursor if isinstance(cursor, str) else None


def reset_seconds(value: str | None, now: datetime) -> float | None:
    """Seconds until the window resets from `X-Rate-Limit-Reset`: seconds as documented, or a
    Unix time when the number is that large."""
    if value is None:
        return None
    try:
        v = float(value.strip())
    except ValueError:
        return None
    if v > 1e9:
        return max(0.0, v - now.timestamp())
    return max(0.0, v)


def _hdr_int(headers: Mapping[str, str], name: str) -> int | None:
    v = headers.get(name)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def ph_evidence_url(what: str, **params: Any) -> str:
    """Evidence URL of one request: `what` is `repo:<owner/name>` or `topic:<slug>`."""
    kind, _, value = what.partition(":")
    head = f"{EVIDENCE_REPO_MARK}{value.lower()}" if kind == "repo" else f"ph_topic={value}"
    tail = "".join(f"&{k}={v}" for k, v in params.items())
    return f"{PH_API}?{head}{tail}"


class ProductHuntConnector(Connector):
    """Product Hunt API v2, project-level post fields only (module docstring)."""

    name: ClassVar[str] = "producthunt"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = PH_TERMS
    enabled_by_default: ClassVar[bool] = True  # but off without PH_API_TOKEN
    person_level_hold: ClassVar[bool] = False  # nothing person-level is requested or stored
    requires_env: ClassVar[tuple[str, ...]] = (PH_TOKEN_ENV,)
    rate_per_second: ClassVar[float] = 0.5
    safety_margin: ClassVar[float] = 0.5  # -> one request every 4 s
    retention_class = "person_level_24m"  # until dropped: a description may name people
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ()
    timeout_seconds: ClassVar[float] = 30.0
    max_rate_wait: ClassVar[float] = 960.0  # one 15-minute window plus a minute

    def __init__(self, *, token: str | None = None, **kw: Any) -> None:
        kw.setdefault("pseudonymizer", None)
        super().__init__(**kw)
        env = kw.get("env")
        e: Mapping[str, str] = os.environ if env is None else env
        self._token = token if token is not None else ((e.get(PH_TOKEN_ENV) or "").strip() or None)
        if self._token is None:
            self.enabled = False  # ADR-085: off without the operator's token
        self.rate_limit: int | None = None
        self.rate_remaining: int | None = None
        self.rate_reset_at: float | None = None  # clock().timestamp() of the reset

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def __repr__(self) -> str:  # never show the token
        return f"<ProductHuntConnector token={'set' if self._token else 'unset'}>"

    # --- rate limits ------------------------------------------------------------------------
    def _note_rate(self, headers: Mapping[str, str]) -> None:
        remaining = _hdr_int(headers, "X-Rate-Limit-Remaining")
        if remaining is None:
            return
        self.rate_remaining = remaining
        self.rate_limit = _hdr_int(headers, "X-Rate-Limit-Limit") or self.rate_limit
        now = self.clock()
        wait = reset_seconds(headers.get("X-Rate-Limit-Reset"), now)
        self.rate_reset_at = None if wait is None else now.timestamp() + wait

    def _reserve(self) -> int:
        return max(1, int((self.rate_limit or 0) * RESERVE_FRACTION))

    def _wait_for_budget(self) -> None:
        """Before a request: when the last answer left no more than the reserve, sleep until
        the window resets (or raise when that is too long)."""
        if self.rate_remaining is None or self.rate_remaining > self._reserve():
            return
        wait = 60.0 if self.rate_reset_at is None else self.rate_reset_at - self.clock().timestamp()
        wait = max(0.0, wait) + 1
        if wait > self.max_rate_wait:
            raise ProductHuntRateLimited(PH_API, None, f"budget spent; reset in {wait:.0f}s")
        if self.run is not None:
            self.run.incr(f"{self.name}.rate_waits")
        self.sleep(wait)
        self.rate_remaining = None

    # --- requests ---------------------------------------------------------------------------
    def _post(self, query: str, variables: Mapping[str, Any], *, evidence_url: str) -> Fetched:
        if not self.enabled or self._token is None:
            raise ConnectorDisabled(
                f"connector {self.name!r} is off: set {PH_TOKEN_ENV} (a Product Hunt developer "
                "token, read-only public scope)"
            )
        hdrs = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        body = {"query": query, "variables": dict(variables)}
        attempt = 0
        while True:
            self._wait_for_budget()
            self.limiter.acquire()
            try:
                resp = self.http.post(PH_API, json=body, headers=hdrs)
            except httpx.TransportError as exc:
                self._account(PH_API, None, 0)
                if attempt >= self.retry.max_retries:
                    raise FetchError(
                        PH_API, None, f"transport error: {type(exc).__name__}"
                    ) from exc
                self.sleep(self.retry.backoff(attempt, self.rng))
                attempt += 1
                continue
            self._account(PH_API, resp.status_code, len(resp.content))
            self._note_rate(resp.headers)
            if resp.status_code == 429:
                now = self.clock()
                wait = parse_retry_after(resp.headers.get("Retry-After"), now)
                if wait is None:
                    wait = reset_seconds(resp.headers.get("X-Rate-Limit-Reset"), now)
                wait = 60.0 if wait is None else wait + 1
                if self.run is not None:
                    self.run.incr(f"{self.name}.rate_limited")
                if attempt >= self.retry.max_retries or wait > self.max_rate_wait:
                    raise ProductHuntRateLimited(PH_API, 429, f"reset in {wait:.0f}s")
                self.sleep(wait)
                self.rate_remaining = None
                attempt += 1
                continue
            if resp.status_code in (500, 502, 503, 504):
                if attempt >= self.retry.max_retries:
                    raise FetchError(PH_API, resp.status_code, "retries exhausted")
                self.sleep(self.retry.backoff(attempt, self.rng))
                attempt += 1
                continue
            if not resp.is_success:
                raise FetchError(PH_API, resp.status_code)
            return self._snapshot_response(resp, url=evidence_url)

    def post_by_slug(self, slug: str, *, evidence_url: str) -> Fetched:
        """`post(slug: …)`, not parsed (`parse_post`); the caller drops the raw answer."""
        return self._post(SLUG_QUERY, {"slug": slug}, evidence_url=evidence_url)

    def post_by_id(self, post_id: str, *, evidence_url: str) -> Fetched:
        """`post(id: …)`, not parsed (`parse_post`); the caller drops the raw answer."""
        return self._post(ID_QUERY, {"id": post_id}, evidence_url=evidence_url)

    def topic_page(
        self,
        topic: str,
        *,
        posted_after: datetime,
        posted_before: datetime,
        after: str | None,
        evidence_url: str,
    ) -> Fetched:
        """One page (`PH_PAGE` posts, newest first) of a topic's posts inside the window, not
        parsed (`parse_posts_page`); the caller drops the raw answer."""
        return self._post(
            TOPIC_QUERY,
            {
                "topic": topic,
                "postedAfter": posted_after.astimezone(UTC).isoformat(),
                "postedBefore": posted_before.astimezone(UTC).isoformat(),
                "first": PH_PAGE,
                "after": after,
            },
            evidence_url=evidence_url,
        )

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        """Replay path: project-level numbers only."""
        d = _data(data)
        nodes: list[Any] = []
        if "post" in d:
            nodes = [d.get("post")]
        elif isinstance(d.get("posts"), dict):
            nodes = [(e or {}).get("node") for e in d["posts"].get("edges") or []]
        for n in nodes:
            p = parse_post_node(n)
            if p is not None:
                yield {
                    "ph_post_id": p.id,
                    "votes": p.votes,
                    "comments": p.comments,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "featured_at": p.featured_at.isoformat() if p.featured_at else None,
                }
