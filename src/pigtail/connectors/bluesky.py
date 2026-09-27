"""Bluesky public AppView search, limited to declared maintainer accounts and to posts that link
a shortlisted repo (view B's launch events; ADR-085, TM-34; Directive §8.1, §8.3, §8.4).

The owner's rule (2026-09-27, binding): an account counts as the maintainer's only when the
maintainer declared it, through a link from the repo (its homepage field), the README, the org page
or the GitHub profile; the match happens in memory at coding time; only the role is stored,
never the handle. This module holds:

- **Declared-account extraction** (`declared_accounts`, in memory only): `bsky.app/profile/<handle
  or DID>` links and `@<name>.bsky.social` mentions in the texts those sources give. Handles are
  validated against the AT Protocol handle syntax, DIDs against `did:plc` / `did:web`. Nothing
  else is ever used to find an account: Bluesky is never searched for people by name (ADR-075.3).
- **The one API call** (`BlueskySearchConnector.search_posts`):
  `GET <base>/xrpc/app.bsky.feed.searchPosts` with `author=<declared handle or DID>`,
  `url=<the repo's GitHub URL | its homepage URL>`, `since`/`until` = the brief's window,
  `sort=latest`, `limit=100`, `cursor`, and the lexicon's required `q` set to `*` (the filters do
  the selecting). Unauthenticated on `api.bsky.app` (the owner's check of 2026-09-27;
  `public.api.bsky.app` answers 403 for search), configurable with `PIGTAIL_BLUESKY_API_BASE`.
  No feed (`getAuthorFeed`), profile or follower endpoint exists here; handles are not resolved
  (the search takes a handle or a DID).
- **Parsing** (`parse_search_page`): per post, only the links it carries (link facets, external
  embeds, URLs in the text, all read in memory to check that it really links the URL searched)
  and its time (`sortAt` as the API defines it: the earlier of `record.createdAt` and
  `indexedAt`). The author object, text, counts and URI are never kept.

**Storage.** Raw pages are snapshotted before parsing and dropped right after (CB-24), classed
`person_level_24m` until then. The evidence URL never holds the handle or the DID: it names the
repo, the match and the page, with `[declared-account]` in place of the author
(`bsky_evidence_url`). The request's own URL (with the handle) reaches only the HTTP client; a
transport error is reported by its type name only.

**Rate limits.** The Bluesky app endpoints' limits are "generous" with no numbers published
(source matrix §2.6; the PDS limit is 3,000 requests per 5 minutes per IP): a client-side token
bucket at 1 request/s with a 50 % margin (1,800 an hour); a 429 waits for `Retry-After` or the
`ratelimit-reset` header (a Unix time), bounded. Retries are few (`RetryPolicy(max_retries=2)`),
so an outage makes a repo `incomplete` quickly instead of stalling the run.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import (
    USER_AGENT,
    Clearance,
    Connector,
    Fetched,
    FetchError,
    NotFound,
    Record,
    RetryPolicy,
    TermsMetadata,
    parse_retry_after,
)

BSKY_BASE_ENV = "PIGTAIL_BLUESKY_API_BASE"
DEFAULT_BSKY_BASE = "https://api.bsky.app"
SEARCH_PATH = "/xrpc/app.bsky.feed.searchPosts"
RESOLVE_PATH = "/xrpc/com.atproto.identity.resolveHandle"
BSKY_QUERY = "*"  # the lexicon requires `q`; the author and url filters select the posts
BSKY_PAGE = 100
SEARCH_PARAMS = frozenset({"q", "author", "url", "since", "until", "sort", "limit", "cursor"})
ACCOUNT_PLACEHOLDER = "[declared-account]"
EVIDENCE_REPO_MARK = "launch_source_repo="

BSKY_TERMS = TermsMetadata(
    terms_url="https://docs.bsky.app/docs/support/developer-guidelines",
    terms_basis=(
        "TM-34 (docs/compliance/terms-memos.md; ADR-085): Bluesky public AppView search "
        "(app.bsky.feed.searchPosts, unauthenticated), only for accounts the maintainer declared "
        "on the repo, README, org page or GitHub profile, and only for posts linking the "
        "shortlisted repo's GitHub URL or homepage (Directive §8.3); handles in memory only, "
        "never stored or logged; only kind, time, role 'maintainer' and match stored; raw pages "
        "dropped after parsing; conservative client-side rate limit. Developer Guidelines and "
        "Terms of Service as in TM-06."
    ),
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,  # no commercial restriction found (TM-06); unknown until H2
    deletion_obligation=True,  # TM-06: honour deletions (nothing post-level is kept)
    notes="Limits of the Bluesky app endpoints not published; PDS 3,000 per 5 min per IP.",
)

# --- declared accounts (in memory only) ------------------------------------------------------
_HANDLE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_DID = re.compile(r"^did:(?:plc:[a-z2-7]{24}|web:[a-z0-9.-]+(?::[0-9]+)?)$")
_PROFILE = re.compile(
    r"(?:https?://)?(?:www\.)?bsky\.app/profile/([A-Za-z0-9][A-Za-z0-9._:%-]*[A-Za-z0-9])",
    re.IGNORECASE,
)
_AT_HANDLE = re.compile(
    r"(?<![A-Za-z0-9._%+/@-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.bsky\.social)(?![A-Za-z0-9-]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)
# handles that are Bluesky itself, not an account anybody declared as their own
_RESERVED = frozenset({"bsky.app", "bsky.social", "bsky.team", "atproto.com"})


def account_id(raw: str) -> str | None:
    """A valid AT identifier (a handle, lowercased, or a `did:plc` / `did:web` DID), else None."""
    s = raw.strip().strip("/").removeprefix("@")
    s = s.replace("%3A", ":").replace("%3a", ":")
    if s.lower().startswith("did:"):
        d = s.lower() if s.lower().startswith("did:web:") else s
        return d if _DID.match(d) else None
    h = s.lower()
    if h in _RESERVED or not _HANDLE.match(h) or len(h) > 253:
        return None
    return h


def declared_accounts(texts: Iterable[str | None]) -> list[str]:
    """Bluesky accounts declared in `texts` (in order of first appearance, distinct):
    `bsky.app/profile/<handle or DID>` links and `@<name>.bsky.social` mentions. In memory
    only: the result must never be stored or logged."""
    out: list[str] = []
    for text in texts:
        if not text:
            continue
        found = sorted(
            (m.start(), m.group(1)) for rx in (_PROFILE, _AT_HANDLE) for m in rx.finditer(text)
        )
        for _pos, raw in found:
            a = account_id(raw)
            if a is not None and a not in out:
                out.append(a)
    return out


# --- search results ---------------------------------------------------------------------------
_URL_IN_TEXT = re.compile(
    r"(?:https?://)?(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s)\]>\"']*)?"
)


@dataclass(frozen=True)
class BskyPost:
    """One search hit as the selection reads it: a key for de-duplication within the run (the
    post's URI, in memory only), its time, and the links it carries (in memory only)."""

    key: str
    at: datetime | None
    links: tuple[str, ...]


def _dt(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo is not None else t.replace(tzinfo=UTC)).astimezone(UTC)


def _links(record: Mapping[str, Any], view_embed: Any) -> list[str]:
    out: list[str] = []
    for facet in record.get("facets") or []:
        for feat in (facet or {}).get("features") or []:
            uri = (feat or {}).get("uri")
            if isinstance(uri, str):
                out.append(uri)
    for emb in (record.get("embed"), view_embed):
        if not isinstance(emb, dict):
            continue
        for e in (emb, emb.get("media")):
            ext = (e or {}).get("external") if isinstance(e, dict) else None
            uri = (ext or {}).get("uri") if isinstance(ext, dict) else None
            if isinstance(uri, str):
                out.append(uri)
    text = record.get("text")
    if isinstance(text, str):
        out += _URL_IN_TEXT.findall(text)
    return out


def parse_search_page(data: bytes) -> tuple[list[BskyPost], str | None]:
    """(posts, next cursor) of one `searchPosts` page. Reads `uri`, `indexedAt`, the record's
    `createdAt`, link facets, external embeds and URLs in the text; never the author, counts or
    labels (and nothing it reads is kept beyond this run)."""
    body = json.loads(data)
    out: list[BskyPost] = []
    for p in body.get("posts") or []:
        if not isinstance(p, dict):
            continue
        raw = p.get("record")
        rec: dict[str, Any] = raw if isinstance(raw, dict) else {}
        times = [t for t in (_dt(rec.get("createdAt")), _dt(p.get("indexedAt"))) if t is not None]
        out.append(
            BskyPost(
                key=str(p.get("uri") or ""),
                at=min(times) if times else None,  # sortAt: the earlier of the two
                links=tuple(_links(rec, p.get("embed"))),
            )
        )
    cursor = body.get("cursor")
    return out, cursor if isinstance(cursor, str) and cursor else None


def bsky_evidence_url(base: str, full_name: str, match: str, page: int) -> str:
    """The evidence URL of one search page: the repo, the match and the page; the author is
    `[declared-account]`, never the handle or DID."""
    return (
        f"{base}{SEARCH_PATH}?{EVIDENCE_REPO_MARK}{full_name.lower()}"
        f"&author={ACCOUNT_PLACEHOLDER}&match={match}&page={page}"
    )


class BlueskySearchConnector(Connector):
    """`app.bsky.feed.searchPosts` with author and url filters only (module docstring)."""

    name: ClassVar[str] = "bluesky_search"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = BSKY_TERMS
    enabled_by_default: ClassVar[bool] = True
    # Nothing person-level is stored: posts are read in memory for their links and time, and
    # only kind, time, role and match are kept (ADR-085; like the HN mention search, ADR-084)
    person_level_hold: ClassVar[bool] = False
    rate_per_second: ClassVar[float] = 1.0
    safety_margin: ClassVar[float] = 0.5  # 1,800 requests an hour
    retention_class = "person_level_24m"  # until dropped right after parsing
    reliability = "medium"
    handle_fields: ClassVar[tuple[str, ...]] = ()
    timeout_seconds: ClassVar[float] = 30.0
    max_rate_wait: ClassVar[float] = 300.0

    def __init__(self, *, base_url: str | None = None, **kw: Any) -> None:
        kw.setdefault("pseudonymizer", None)
        kw.setdefault("retry", RetryPolicy(max_retries=2, max_delay=30.0, max_retry_after=300.0))
        super().__init__(**kw)
        env = kw.get("env")
        e: Mapping[str, str] = os.environ if env is None else env
        base = base_url or (e.get(BSKY_BASE_ENV) or "").strip() or DEFAULT_BSKY_BASE
        self.base = base.rstrip("/")

    def _request(
        self, url: str, params: Mapping[str, Any] | None, headers: Mapping[str, str] | None
    ) -> httpx.Response:
        """GET with the limiter, a few retries on 429/5xx and transport errors. Errors carry the
        endpoint and status only, never the query (it holds the declared account)."""
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        attempt = 0
        while True:
            self.limiter.acquire()
            try:
                resp = self.http.get(url, params=params, headers=hdrs)
            except httpx.TransportError as exc:
                self._account(url, None, 0)
                if attempt >= self.retry.max_retries:
                    raise FetchError(url, None, f"transport error: {type(exc).__name__}") from exc
                self.sleep(self.retry.backoff(attempt, self.rng))
                attempt += 1
                continue
            self._account(url, resp.status_code, len(resp.content))
            if resp.status_code not in self.retry.retry_statuses:
                return resp
            if attempt >= self.retry.max_retries:
                raise FetchError(url, resp.status_code, "retries exhausted")
            now = self.clock()
            wait = parse_retry_after(resp.headers.get("Retry-After"), now)
            if wait is None and resp.status_code == 429:
                reset = resp.headers.get("ratelimit-reset")
                try:
                    wait = max(0.0, float(reset) - now.timestamp()) if reset else None
                except ValueError:
                    wait = None
            if wait is not None and wait > self.max_rate_wait:
                raise FetchError(url, resp.status_code, f"rate limited for {wait:.0f}s")
            self.sleep(wait if wait is not None else self.retry.backoff(attempt, self.rng))
            attempt += 1

    def resolve_handle(self, handle: str) -> str | None:
        """The DID of a declared handle (`com.atproto.identity.resolveHandle`), or None when the
        handle doesn't resolve (a dead or mistyped link: HTTP 400). Not snapshotted: the answer
        is an identifier, used for the search in memory only and never stored or logged. Other
        failures raise `FetchError` (an outage, not a dead link)."""
        resp = self._request(f"{self.base}{RESOLVE_PATH}", {"handle": handle}, None)
        if resp.status_code == 400:
            return None
        if not resp.is_success:
            raise FetchError(f"{self.base}{RESOLVE_PATH}", resp.status_code)
        try:
            did = resp.json().get("did")
        except ValueError as e:
            raise FetchError(f"{self.base}{RESOLVE_PATH}", resp.status_code, "bad json") from e
        return did if isinstance(did, str) and _DID.match(did) else None

    def search_posts(
        self,
        *,
        author: str,
        url: str,
        since: datetime,
        until: datetime,
        cursor: str | None,
        evidence_url: str,
    ) -> Fetched:
        """One page of the declared account's posts that link `url`, inside the window, newest
        first (not parsed here: `parse_search_page`; the caller drops the raw page). `author`
        is used for this request only and never stored."""
        params: dict[str, Any] = {
            "q": BSKY_QUERY,
            "author": author,
            "url": url,
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sort": "latest",
            "limit": BSKY_PAGE,
        }
        if cursor:
            params["cursor"] = cursor
        assert set(params) <= SEARCH_PARAMS
        try:
            return self.fetch(f"{self.base}{SEARCH_PATH}", params=params, evidence_url=evidence_url)
        except NotFound as e:  # an unknown author: reported like any failed request
            raise FetchError(f"{self.base}{SEARCH_PATH}", 404, "not found") from e

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        """Replay path: the time of each post only."""
        posts, _ = parse_search_page(data)
        for p in posts:
            yield {"time": p.at.isoformat() if p.at else None}
