"""GitHub REST + GraphQL connectors (M1-T24; ADR-032; TM-02, TM-33).

Two connectors share one HTTP layer (`GitHubAPI`) and one token:

- **`github`** (`GitHubConnector`): project-level data only. Generic GraphQL queries
  (`graphql()`), `search/repositories` pages for one caller-supplied query
  (`search_repositories()`, paged by `pigtail.capture.github_search.search_repos` for
  brief-scoped discovery; the all-GitHub sweeps were removed in M11, ADR-047.6), and the
  star-history endpoint `GET /repos/{o}/{r}/stargazers/history` (weekly and daily net counts, no
  identities). On by default, but it **refuses every live call without `GITHUB_TOKEN`**
  (`MissingToken`); the scheduler skips its jobs with the logged reason
  `missing_env:GITHUB_TOKEN`.
- **`github_events`** (`GitHubRepoEventsConnector`): per-repo `GET /repos/{o}/{r}/events` for
  tracked cases (TM-33). Person-level (`actor`), so it is **off by default**
  (`PIGTAIL_ENABLE_GITHUB_EVENTS`) and held by ADR-022 (`PIGTAIL_ADR022_PERSON_SOURCES_OK=1`
  needed; ADR-036). At parse only `WatchEvent` and `ForkEvent` are kept (CB-23); the actor is
  coded in memory (automated-account flag by the bot rule, a per-run token for de-duplication
  within the poll) and its login discarded (Directive §8.1, ADR-071.2); snapshots use the
  `person_level_30d` retention class (CB-22) and the poller drops their raw bytes right after
  parsing. Only counts are stored. Nothing in pigtail lists the stargazers of a repo.

What the shared layer does on every request (TM-02 conditions; GitHub REST best practices):

- one operator token from the environment (`GITHUB_TOKEN`, never logged or stored), sent as
  `Authorization: Bearer`; `X-GitHub-Api-Version` (`GITHUB_API_VERSION`, default 2026-03-10,
  the version the star-history docs are published under); the pigtail contact User-Agent;
- serial requests, a token bucket **per resource** (`core`, `graphql`, `search`; search at
  21/min = 70 % of 30/min);
- budget hard stops per resource before each request (`pigtail.connectors.github_budget`);
- **primary limits**: `X-RateLimit-Remaining: 0` on a 403/429 → wait until `X-RateLimit-Reset`
  (if within `max_rate_wait`, else raise `RateLimited`);
- **secondary limits**: 403/429 with `Retry-After` → wait that long; 429, or 403 whose body
  mentions a rate limit, without `Retry-After` → wait 60 s, doubling on each repeat;
- 5xx and transport errors → exponential backoff with jitter;
- **conditional requests** (`fetch_conditional`): `If-None-Match` with the stored ETag; a `304`
  costs no rate limit, stores no snapshot and returns `fetched=None`;
- `X-Poll-Interval` is recorded per URL and returned, so pollers never poll faster.

GraphQL errors of type `RATE_LIMITED` are treated like a primary limit.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, Protocol

import httpx

from pigtail.capture.botfilter import is_bot_login
from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import (
    USER_AGENT,
    Clearance,
    Connector,
    ConnectorDisabled,
    ConnectorError,
    Fetched,
    FetchError,
    NotFound,
    Record,
    TermsMetadata,
    TokenBucket,
    parse_retry_after,
)
from pigtail.connectors.github_budget import Budget, Resource
from pigtail.privacy.deletion import PARSE_ERRORS

API = "https://api.github.com"
GRAPHQL_URL = f"{API}/graphql"
DEFAULT_API_VERSION = "2026-03-10"
TOKEN_ENV = "GITHUB_TOKEN"
EVENTS_ENABLE_ENV = "PIGTAIL_ENABLE_GITHUB_EVENTS"
STAR_HISTORY_MAX_PER_PAGE = 30  # weeks (docs)
STAR_HISTORY_MAX_PAGE = 100  # docs
SEARCH_MAX_RESULTS = 1000  # "up to 1,000 results for each search"
KEPT_EVENT_TYPES = frozenset({"WatchEvent", "ForkEvent"})  # CB-23

TM02 = (
    "TM-02 (docs/compliance/terms-memos.md): GitHub REST/GraphQL API under the GitHub Terms of "
    "Service §H (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service). "
    "Conditions: the operator's own single token, never pooled, no App-plus-PAT doubling "
    "(ADR-032.4); stay within primary and secondary rate limits; serial requests, ETag, fixed "
    "schedule; no user identities stored (roles and buckets, counts)."
)

GITHUB_TERMS = TermsMetadata(
    terms_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
    terms_basis=(
        TM02 + " TM-33: star-history endpoint (no personal data) at most daily per repo. "
        "Search results name owners: personal-account repos are never named in outputs "
        "(ADR-022)."
    ),
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,  # unknown: H2 Q1
    deletion_obligation=None,
    notes="Stargazer lists are closed to non-collaborators since 2026-06-30; not used.",
)

EVENTS_TERMS = TermsMetadata(
    terms_url="https://docs.github.com/en/rest/activity/events",
    terms_basis=(
        TM02 + " TM-33 (per-repo Events API, actor use pending LQ-29): only repos with an open "
        "case, tracked or above the pre-threshold; no faster than X-Poll-Interval (15-60 min); "
        "ETag; actors used in memory only (bot flag, de-duplication within a poll) and never "
        "stored; never rebuild, store or export a stargazer list; only aggregate counts are "
        "kept (CB-22, CB-23; ADR-036; Directive §8.1)."
    ),
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,
    deletion_obligation=None,
    notes="300-event window per repo; overflow between polls is detected and recorded.",
)


class MissingToken(ConnectorError):
    """No `GITHUB_TOKEN`: pigtail makes no live GitHub call without the operator's token."""


class RateLimited(FetchError):
    def __init__(self, url: str, status: int | None, kind: str, wait: float | None) -> None:
        super().__init__(url, status, f"{kind} rate limit (wait {wait or 0:.0f}s)")
        self.kind = kind
        self.wait = wait


# --- ETag / poll-interval cache -------------------------------------------------------------------
@dataclass(frozen=True)
class CacheEntry:
    url: str
    etag: str | None = None
    content_hash: str | None = None
    evidence_id: str | None = None
    fetched_at: datetime | None = None
    last_status: int | None = None
    poll_interval_s: int | None = None


class HttpCache(Protocol):
    def get(self, url: str) -> CacheEntry | None: ...

    def put(self, entry: CacheEntry) -> None: ...


@dataclass
class MemoryCache:
    entries: dict[str, CacheEntry] = field(default_factory=dict)

    def get(self, url: str) -> CacheEntry | None:
        return self.entries.get(url)

    def put(self, entry: CacheEntry) -> None:
        self.entries[entry.url] = entry


class PostgresCache:
    """`github_http_cache` (migration 0007)."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def get(self, url: str) -> CacheEntry | None:
        row = self.conn.execute(
            "SELECT etag, content_hash, evidence_id, fetched_at, last_status, poll_interval_s"
            " FROM github_http_cache WHERE url = %s",
            (url,),
        ).fetchone()
        if row is None:
            return None
        return CacheEntry(url, row[0], row[1], row[2], row[3], row[4], row[5])

    def put(self, e: CacheEntry) -> None:
        self.conn.execute(
            """
            INSERT INTO github_http_cache (url, etag, content_hash, evidence_id, fetched_at,
                                           last_status, poll_interval_s)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET etag = EXCLUDED.etag,
                content_hash = EXCLUDED.content_hash, evidence_id = EXCLUDED.evidence_id,
                fetched_at = EXCLUDED.fetched_at, last_status = EXCLUDED.last_status,
                poll_interval_s = EXCLUDED.poll_interval_s
            """,
            (e.url, e.etag, e.content_hash, e.evidence_id, e.fetched_at, e.last_status,
             e.poll_interval_s),
        )  # fmt: skip


@dataclass(frozen=True)
class Conditional:
    """Result of a conditional GET: `fetched` is None on `304 Not Modified`."""

    url: str
    status: int
    fetched: Fetched | None
    poll_interval_s: int | None
    previous: CacheEntry | None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def full_url(url: str, params: Mapping[str, Any] | None) -> str:
    return str(httpx.URL(url, params=dict(params or {})))


def resource_for(url: str) -> Resource:
    path = httpx.URL(url).path
    if path.startswith("/graphql"):
        return "graphql"
    if path.startswith("/search/"):
        return "search"
    return "core"


# --- shared HTTP layer ---------------------------------------------------------------------------
class GitHubAPI(Connector):
    """Token, rate limits, budget, ETag. Not a source by itself (see the two subclasses)."""

    requires_env: ClassVar[tuple[str, ...]] = (TOKEN_ENV,)
    rates: ClassVar[dict[str, tuple[float, float]]] = {
        # resource: (requests/s, safety margin) -> effective rate = r * (1 - margin)
        "core": (2.0, 0.2),
        "graphql": (1.0, 0.2),
        "search": (0.5, 0.3),  # 30/min * 0.7 = 21/min
    }
    max_rate_wait: ClassVar[float] = 900.0  # wait at most 15 min for a limit reset, else raise

    def __init__(
        self,
        *,
        budget: Budget | None = None,
        cache: HttpCache | None = None,
        token: str | None = None,
        limiters: Mapping[str, TokenBucket] | None = None,
        **kw: Any,
    ) -> None:
        kw.setdefault("pseudonymizer", None)  # required by the base only with handle fields
        super().__init__(**kw)
        env = kw.get("env")
        e: Mapping[str, str] = os.environ if env is None else env
        self._token = token if token is not None else ((e.get(TOKEN_ENV) or "").strip() or None)
        self.api_version = (e.get("GITHUB_API_VERSION") or "").strip() or DEFAULT_API_VERSION
        self.budget = budget or Budget(clock=self.clock, sleep=self.sleep)
        self.cache: HttpCache = cache if cache is not None else MemoryCache()
        self.limiters: dict[str, TokenBucket] = dict(limiters or {})
        for r, (rate, margin) in self.rates.items():
            self.limiters.setdefault(r, TokenBucket(rate, 1, margin, sleep=self.sleep))

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def __repr__(self) -> str:  # never show the token
        tok = "set" if getattr(self, "_token", None) else "unset"
        return f"<{type(self).__name__} {self.name} token={tok}>"

    def _require_live(self) -> None:
        if not self.enabled:
            raise ConnectorDisabled(f"connector {self.name!r} is disabled")
        if self._token is None:
            raise MissingToken(
                f"{TOKEN_ENV} is not set: pigtail makes no live GitHub call without the "
                "operator's own token (TM-02; docs/guides/operator.md 'GitHub token and budgets')"
            )

    @staticmethod
    def _rate_limited(resp: httpx.Response, n: int, now: datetime) -> tuple[str, float] | None:
        """(kind, seconds to wait) when a response is a primary or secondary rate limit."""
        if resp.status_code not in (403, 429):
            return None
        remaining = resp.headers.get("X-RateLimit-Remaining")
        ra = parse_retry_after(resp.headers.get("Retry-After"), now)
        if ra is not None:
            return ("primary" if remaining == "0" else "secondary", ra)
        if remaining == "0":
            try:
                reset = datetime.fromtimestamp(int(resp.headers["X-RateLimit-Reset"]), UTC)
                return "primary", max(0.0, (reset - now).total_seconds()) + 1
            except (KeyError, ValueError):
                return "primary", 60.0
        text = resp.text.lower() if resp.content else ""
        if resp.status_code == 429 or "rate limit" in text:
            return "secondary", 60.0 * 2**n
        return None  # an ordinary 403 (e.g. access blocked)

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        est_units: int = 1,
    ) -> httpx.Response:
        self._require_live()
        resource = resource_for(url)
        hdrs = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "Authorization": f"Bearer {self._token}",
            **(headers or {}),
        }
        attempt = limited_n = 0
        while True:
            self.budget.before(resource, est_units)
            self.limiters[resource].acquire()
            try:
                resp = self.http.request(method, url, params=params, json=json_body, headers=hdrs)
            except httpx.TransportError as exc:
                self._account(url, None, 0)
                if attempt >= self.retry.max_retries:
                    raise FetchError(url, None, f"transport error: {type(exc).__name__}") from exc
                self.sleep(self.retry.backoff(attempt, self.rng))
                attempt += 1
                continue
            self._account(url, resp.status_code, len(resp.content))
            nm = resp.status_code == 304
            lim = self._rate_limited(resp, limited_n, self.clock())
            self.budget.after(
                resource,
                resp.headers,
                units=0 if nm else est_units,
                not_modified=nm,
                limited=lim is not None,
            )
            if lim is not None:
                kind, wait = lim
                limited_n += 1
                if self.run is not None:
                    self.run.incr(f"{self.name}.{kind}_rate_limited")
                if attempt >= self.retry.max_retries or wait > self.max_rate_wait:
                    raise RateLimited(url, resp.status_code, kind, wait)
                self.sleep(wait)
                attempt += 1
                continue
            if resp.status_code in (500, 502, 503, 504):
                if attempt >= self.retry.max_retries:
                    raise FetchError(url, resp.status_code, "retries exhausted")
                self.sleep(self.retry.backoff(attempt, self.rng))
                attempt += 1
                continue
            return resp

    def _request(
        self, url: str, params: Mapping[str, Any] | None, headers: Mapping[str, str] | None
    ) -> httpx.Response:
        """Base-class GET path (`fetch`, `check`, `refetch_verified`) through the GitHub layer."""
        return self._send("GET", url, params=params, headers=headers)

    def fetch_conditional(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        repo_id: str | None = None,
        case_id: str | None = None,
        retention_class: Any = None,
    ) -> Conditional:
        """GET with `If-None-Match`; 304 → no snapshot, no rate-limit cost (REST best practices)."""
        key = full_url(url, params)
        prev = self.cache.get(key)
        hdrs = {"If-None-Match": prev.etag} if prev and prev.etag else {}
        resp = self._send("GET", url, params=params, headers=hdrs)
        poll = _int_header(resp.headers.get("X-Poll-Interval"))
        now = self.clock()
        if resp.status_code == 304:
            if self.run is not None:
                self.run.incr(f"{self.name}.not_modified")
            self.cache.put(
                CacheEntry(
                    key,
                    prev.etag if prev else None,
                    prev.content_hash if prev else None,
                    prev.evidence_id if prev else None,
                    now,
                    304,
                    poll or (prev.poll_interval_s if prev else None),
                )
            )
            return Conditional(key, 304, None, poll, prev)
        if resp.status_code == 404:
            raise NotFound(key, 404)
        if not resp.is_success:
            raise FetchError(key, resp.status_code)
        f = self._snapshot_response(
            resp, case_id=case_id, repo_id=repo_id, retention_class=retention_class
        )
        self.cache.put(
            CacheEntry(key, resp.headers.get("ETag"), f.content_hash, f.evidence.id, now, 200, poll)
        )
        return Conditional(key, resp.status_code, f, poll, prev)


def _int_header(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


# --- project-level connector ----------------------------------------------------------------------
@dataclass(frozen=True)
class GraphQLResult:
    fetched: Fetched
    data: dict[str, Any]
    errors: list[dict[str, Any]]
    cost: int | None
    remaining: int | None
    limit: int | None
    reset_at: datetime | None


@dataclass(frozen=True)
class SearchRepo:
    id: int
    node_id: str | None
    full_name: str
    stars: int
    forks: int
    created_at: datetime | None
    pushed_at: datetime | None
    owner_type: str | None  # "User" | "Organization"; never the login itself
    archived: bool
    fork: bool
    # M22 discovery (R4.5, R4.6): public project metadata the relevance filter reads
    description: str | None = None
    topics: tuple[str, ...] = ()
    language: str | None = None
    # the repo's homepage URL (project-level; pigtail stores only its domain, ADR-083 E)
    homepage: str | None = None


@dataclass(frozen=True)
class SearchPage:
    total_count: int
    incomplete_results: bool
    items: list[SearchRepo]


@dataclass(frozen=True)
class StarWeek:
    """One star-history item. `days[i]` belongs to day label `week_start + i` (endpoint days)."""

    week_label: str  # the endpoint's own label, verbatim
    week_start: date
    total: int
    days: tuple[int, ...]


def _dt(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_week_label(label: Any) -> date:
    """The endpoint's week label as a calendar date, **without** converting time zones.

    ISO strings keep their written date (`2026-09-20`, `2026-09-20T00:00:00-07:00` →
    2026-09-20). A Unix timestamp is read in UTC; GitHub's weeks appear to start at Pacific
    midnight (07:00/08:00 UTC, same calendar date), which is what M2 of the validation plan
    checks once a token exists.
    """
    if isinstance(label, bool):
        raise ValueError("bad week label")
    if isinstance(label, int | float):
        return datetime.fromtimestamp(label, UTC).date()
    if isinstance(label, str) and re.match(r"^\d{4}-\d{2}-\d{2}", label):
        return date.fromisoformat(label[:10])
    if isinstance(label, str) and label.isdigit():
        return datetime.fromtimestamp(int(label), UTC).date()
    raise ValueError(f"unrecognised week label {label!r}")


def parse_star_history(data: bytes) -> list[StarWeek]:
    doc = json.loads(data)
    items = doc
    if isinstance(doc, dict):
        items = next((doc[k] for k in ("weeks", "history", "items") if k in doc), [])
    if not isinstance(items, list):
        raise ValueError("star history: expected a list of weeks")
    out: list[StarWeek] = []
    for it in items:
        if not isinstance(it, dict) or "week" not in it:
            raise ValueError("star history: item without `week`")
        days = tuple(int(x) for x in (it.get("days") or []))
        if len(days) != 7:
            raise ValueError("star history: `days` must have 7 entries")
        out.append(
            StarWeek(str(it["week"]), parse_week_label(it["week"]), int(it.get("total", 0)), days)
        )
    return out


def parse_search_page(data: bytes) -> SearchPage:
    """Project-level fields of a search page. From each item's `owner` object only `type`
    (`User` | `Organization`) is read; the login, avatar and profile URLs are never kept."""
    doc = json.loads(data)
    items: list[SearchRepo] = []
    for it in doc.get("items") or []:
        owner = it.get("owner") or {}
        items.append(
            SearchRepo(
                id=int(it["id"]),
                node_id=it.get("node_id"),
                full_name=str(it["full_name"]),
                stars=int(it.get("stargazers_count") or 0),
                forks=int(it.get("forks_count") or 0),
                created_at=_dt(it.get("created_at")),
                pushed_at=_dt(it.get("pushed_at")),
                owner_type=owner.get("type") if isinstance(owner, dict) else None,
                archived=bool(it.get("archived")),
                fork=bool(it.get("fork")),
                description=_opt_str(it.get("description")),
                topics=tuple(t for t in it.get("topics") or [] if isinstance(t, str)),
                language=_opt_str(it.get("language")),
                homepage=_opt_str(it.get("homepage")),
            )
        )
    return SearchPage(
        total_count=int(doc.get("total_count") or 0),
        incomplete_results=bool(doc.get("incomplete_results")),
        items=items,
    )


def _opt_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


@dataclass(frozen=True)
class RepoMeta:
    """Project-level metadata of one repo from GraphQL (M22 discovery; no identities: the owner
    is reduced to its type)."""

    host_id: int
    full_name: str
    stars: int
    forks: int
    created_at: datetime | None
    pushed_at: datetime | None
    owner_type: str | None
    archived: bool
    fork: bool
    description: str | None = None
    topics: tuple[str, ...] = ()
    language: str | None = None
    homepage: str | None = None  # homepageUrl (project-level; only its domain is stored)


REPO_META_FIELDS = (
    "databaseId nameWithOwner stargazerCount forkCount createdAt pushedAt description"
    " isArchived isFork primaryLanguage { name } repositoryTopics(first: 20) { nodes { topic"
    " { name } } } owner { __typename } homepageUrl"
)


def repo_meta_query(names: Sequence[str]) -> tuple[str, dict[str, str]]:
    """One aliased GraphQL query for up to 50 `owner/name` repos (`r0`, `r1`, ...)."""
    parts: list[str] = []
    variables: dict[str, str] = {}
    decl: list[str] = []
    for i, full in enumerate(names):
        owner, _, name = full.partition("/")
        variables[f"o{i}"], variables[f"n{i}"] = owner, name
        decl += [f"$o{i}: String!", f"$n{i}: String!"]
        parts.append(f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ {REPO_META_FIELDS} }}")
    body = " ".join(parts)
    q = f"query({', '.join(decl)}) {{ {body} rateLimit {{ cost remaining limit resetAt }} }}"
    return q, variables


def parse_repo_node(node: Any) -> RepoMeta | None:
    if not isinstance(node, dict) or node.get("databaseId") is None:
        return None
    owner = node.get("owner") or {}
    lang = node.get("primaryLanguage") or {}
    topics = [
        ((n or {}).get("topic") or {}).get("name")
        for n in ((node.get("repositoryTopics") or {}).get("nodes") or [])
    ]
    return RepoMeta(
        host_id=int(node["databaseId"]),
        full_name=str(node["nameWithOwner"]),
        stars=int(node.get("stargazerCount") or 0),
        forks=int(node.get("forkCount") or 0),
        created_at=_dt(node.get("createdAt")),
        pushed_at=_dt(node.get("pushedAt")),
        owner_type=owner.get("__typename") if isinstance(owner, dict) else None,
        archived=bool(node.get("isArchived")),
        fork=bool(node.get("isFork")),
        description=_opt_str(node.get("description")),
        topics=tuple(t for t in topics if isinstance(t, str)),
        language=_opt_str(lang.get("name") if isinstance(lang, dict) else None),
        homepage=_opt_str(node.get("homepageUrl")),
    )


@dataclass(frozen=True)
class Release:
    """One GitHub release as the selection reads it (ADR-084). `name` and `body_head` (the
    body's first 300 characters) are held in memory only, for the launch-wording test; they
    are never stored. The `author` object of the API response is never read."""

    tag: str
    published_at: datetime | None  # None: a draft (never public)
    prerelease: bool
    name: str | None
    body_head: str | None


RELEASE_BODY_CHARS = 300
RELEASES_PER_PAGE = 100


def parse_releases(data: bytes) -> list[Release]:
    """Releases from one `GET /repos/{owner}/{repo}/releases` page. Reads only `tag_name`,
    `published_at`, `prerelease`, `name` and the first 300 characters of `body`."""
    body = json.loads(data)
    out: list[Release] = []
    for it in body if isinstance(body, list) else []:
        if not isinstance(it, dict):
            continue
        tag = it.get("tag_name")
        if not isinstance(tag, str):
            continue
        text = it.get("body")
        out.append(
            Release(
                tag=tag[:200],
                published_at=_dt(it.get("published_at")),
                prerelease=bool(it.get("prerelease")),
                name=_opt_str(it.get("name")),
                body_head=text[:RELEASE_BODY_CHARS] if isinstance(text, str) else None,
            )
        )
    return out


def releases_url(full_name: str) -> str:
    return f"{API}/repos/{full_name}/releases"


# --- declared Bluesky accounts (ADR-085): homepage field, owner profile, org page -------------
LINKS_FIELDS = "homepageUrl owner { __typename }"


def repo_links_query(names: Sequence[str]) -> tuple[str, dict[str, str]]:
    """One aliased GraphQL query for up to 50 repos' homepage URL and owner type."""
    parts: list[str] = []
    variables: dict[str, str] = {}
    decl: list[str] = []
    for i, full in enumerate(names):
        owner, _, name = full.partition("/")
        variables[f"o{i}"], variables[f"n{i}"] = owner, name
        decl += [f"$o{i}: String!", f"$n{i}: String!"]
        parts.append(f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ {LINKS_FIELDS} }}")
    rl = "rateLimit { cost remaining limit resetAt }"
    q = f"query({', '.join(decl)}) {{ {' '.join(parts)} {rl} }}"
    return q, variables


def parse_repo_links(node: Any) -> tuple[str | None, str | None] | None:
    """(homepage URL, owner type) of one repository node, or None when it didn't resolve."""
    if not isinstance(node, dict):
        return None
    owner = node.get("owner") if isinstance(node.get("owner"), dict) else {}
    return _opt_str(node.get("homepageUrl")), _opt_str((owner or {}).get("__typename"))


def parse_social_accounts(data: bytes) -> list[str]:
    """The URLs of a user's social accounts (`GET /users/{u}/social_accounts`) whose provider is
    `bluesky` or whose URL is on bsky.app; in memory only (ADR-085)."""
    body = json.loads(data)
    out: list[str] = []
    for it in body if isinstance(body, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("url"), str):
            continue
        url = it["url"]
        if it.get("provider") == "bluesky" or "bsky.app/" in url.lower():
            out.append(url)
    return out


def parse_org_profile(data: bytes) -> list[str]:
    """The org page's text fields that may carry a bsky.app profile link (`blog`,
    `description`), from `GET /orgs/{org}`; in memory only (ADR-085)."""
    body = json.loads(data)
    if not isinstance(body, dict):
        return []
    return [v for k in ("blog", "description") if isinstance(v := body.get(k), str) and v]


def readme_url(full_name: str) -> str:
    return f"{API}/repos/{full_name}/readme"


# --- case evidence (M23, ADR-086): README at a commit, release notes, probes for decay ----------
@dataclass(frozen=True)
class ReadmeDoc:
    """`GET /repos/{o}/{r}/readme` as JSON: the file's path and decoded text (project page)."""

    path: str
    text: str


def parse_readme_json(data: bytes) -> ReadmeDoc:
    """Path and text of a README answer (`content` is base64). Reads no other field."""
    import base64

    body = json.loads(data)
    if not isinstance(body, dict) or not isinstance(body.get("path"), str):
        raise ValueError("not a README object")
    raw = body.get("content") or ""
    if not isinstance(raw, str):
        raise ValueError("README content is not a string")
    text = base64.b64decode(raw.encode()).decode("utf-8", errors="replace")
    return ReadmeDoc(body["path"], text)


def parse_commit_refs(data: bytes) -> list[tuple[str, datetime | None]]:
    """(sha, committer date) of a `GET /repos/{o}/{r}/commits` page. Reads only `sha` and
    `commit.committer.date`: the author and committer names, e-mails and accounts are never
    read, and the caller drops the raw page right after parsing (M23, ADR-086)."""
    body = json.loads(data)
    out: list[tuple[str, datetime | None]] = []
    for it in body if isinstance(body, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("sha"), str):
            continue
        commit = it.get("commit") if isinstance(it.get("commit"), dict) else {}
        committer = (commit or {}).get("committer")
        when = _dt(committer.get("date")) if isinstance(committer, dict) else None
        out.append((it["sha"], when))
    return out


@dataclass(frozen=True)
class ReleaseNote:
    """One release with its notes (M23 case evidence). `name` and `body` are project text; the
    caller scrubs identifiers before storing them. The `author` object is never read."""

    tag: str
    name: str | None
    published_at: datetime | None
    prerelease: bool
    body: str | None


def parse_release_notes(data: bytes, *, body_chars: int = 1500) -> list[ReleaseNote]:
    """Releases of one page with the first `body_chars` characters of their notes."""
    body = json.loads(data)
    out: list[ReleaseNote] = []
    for it in body if isinstance(body, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("tag_name"), str):
            continue
        text = it.get("body")
        out.append(
            ReleaseNote(
                tag=it["tag_name"][:200],
                name=_opt_str(it.get("name")),
                published_at=_dt(it.get("published_at")),
                prerelease=bool(it.get("prerelease")),
                body=text[:body_chars] if isinstance(text, str) else None,
            )
        )
    return out


@dataclass(frozen=True)
class Probe:
    """A conditional GET that stores nothing (evidence decay, R19.8): status, the body's
    SHA-256 (None for 304 or an error), and the validators the server sent."""

    status: int | None
    body_hash: str | None
    etag: str | None
    last_modified: str | None
    body: bytes = b""
    note: str | None = None  # e.g. robots_disallowed (project pages)


def star_history_url(full_name: str) -> str:
    return f"{API}/repos/{full_name}/stargazers/history"


def events_url(full_name: str) -> str:
    return f"{API}/repos/{full_name}/events"


class GitHubConnector(GitHubAPI):
    name: ClassVar[str] = "github"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = GITHUB_TERMS
    enabled_by_default: ClassVar[bool] = True  # but no live call without GITHUB_TOKEN
    person_level_hold: ClassVar[bool] = False
    retention_class = "project_level"
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ()
    handle_namespace: ClassVar[str] = "github"
    timeout_seconds: ClassVar[float] = 60.0

    def graphql(
        self, query: str, variables: Mapping[str, Any] | None = None, *, est_cost: int = 1
    ) -> GraphQLResult:
        """POST a GraphQL query; snapshot the raw answer, then record its `rateLimit.cost`."""
        body = {"query": query, "variables": dict(variables or {})}
        n = 0
        while True:
            resp = self._send("POST", GRAPHQL_URL, json_body=body, est_units=est_cost)
            if not resp.is_success:
                raise FetchError(GRAPHQL_URL, resp.status_code)
            f = self._snapshot_response(resp, retention_class="project_level")
            doc = json.loads(f.data)
            data = doc.get("data") or {}
            errors = list(doc.get("errors") or [])
            rl = data.get("rateLimit") if isinstance(data, dict) else None
            cost = remaining = limit = None
            reset_at = None
            if isinstance(rl, dict):
                cost = _opt_int(rl.get("cost"))
                remaining = _opt_int(rl.get("remaining"))
                limit = _opt_int(rl.get("limit"))
                reset_at = _dt(rl.get("resetAt"))
                if cost is not None:
                    self.budget.adjust("graphql", cost - est_cost)
                if remaining is not None and limit is not None and reset_at is not None:
                    self.budget.set_graphql_state(limit, remaining, reset_at)
            if any(e.get("type") == "RATE_LIMITED" for e in errors):
                wait = max(0.0, (reset_at - self.clock()).total_seconds()) + 1 if reset_at else 60.0
                if self.run is not None:
                    self.run.incr(f"{self.name}.primary_rate_limited")
                if n >= self.retry.max_retries or wait > self.max_rate_wait:
                    raise RateLimited(GRAPHQL_URL, 200, "graphql", wait)
                self.sleep(wait)
                n += 1
                continue
            return GraphQLResult(f, data, errors, cost, remaining, limit, reset_at)

    def fetch_search_page(
        self,
        q: str,
        *,
        page: int = 1,
        per_page: int = 100,
        sort: str = "stars",
        evidence_url: str | None = None,
    ) -> Fetched:
        """One page of `GET /search/repositories` (search bucket), snapshotted, not parsed.

        Snapshots are classed `person_level_24m` because result items embed owner objects
        (ADR-037.8). Callers parse with `parse_search_page()` (which keeps only the owner *type*)
        and then drop the raw bytes (`drop_after_parse`; CB-24, ADR-038)."""
        if not 1 <= per_page <= 100 or page < 1 or page * per_page > SEARCH_MAX_RESULTS:
            raise ValueError("search paging is capped at 1,000 results (100 per page)")
        return self.fetch(
            f"{API}/search/repositories",
            params={"q": q, "sort": sort, "order": "desc", "per_page": per_page, "page": page},
            retention_class="person_level_24m",
            evidence_url=evidence_url,
        )

    def search_repositories(
        self, q: str, *, page: int = 1, per_page: int = 100, sort: str = "stars"
    ) -> tuple[Fetched, SearchPage]:
        """`fetch_search_page()` + `parse_search_page()`. A parse failure goes to
        `parse_failed()` (CB-23b) and raises `ParseFailed`. The caller still owns dropping the
        raw bytes of a parsed page (`pigtail.capture.github_search.search_repos` does, CB-24)."""
        f = self.fetch_search_page(q, page=page, per_page=per_page, sort=sort)
        try:
            return f, parse_search_page(f.data)
        except PARSE_ERRORS as e:
            raise self.parse_failed(f, e) from e

    def readme(self, full_name: str, *, repo_id: str | None = None) -> Fetched | None:
        """A repo's README as raw text (`GET /repos/{owner}/{repo}/readme`, core bucket), or None
        when the repo has none. Project page, snapshotted as project-level (ADR-072.7: project
        pages only); the relevance filter sends a trimmed, redacted excerpt (M22, R4.6)."""
        try:
            return self.fetch(
                readme_url(full_name),
                headers={"Accept": "application/vnd.github.raw+json"},
                repo_id=repo_id,
                retention_class="project_level",
            )
        except NotFound:
            return None

    def readme_json(
        self, full_name: str, *, ref: str | None = None, repo_id: str | None = None
    ) -> Fetched | None:
        """A repo's README as the JSON object (path and base64 content), at `ref` (a commit sha)
        or the default branch; None when there is none. Project page, `project_level` (M23)."""
        try:
            return self.fetch(
                readme_url(full_name),
                params={"ref": ref} if ref else None,
                repo_id=repo_id,
                retention_class="project_level",
            )
        except NotFound:
            return None

    def commits_page(
        self, full_name: str, *, path: str, until: datetime, per_page: int = 1,
        repo_id: str | None = None,
    ) -> Fetched:  # fmt: skip
        """`GET /repos/{o}/{r}/commits?path=…&until=…` (core bucket), not parsed: the newest
        commits touching `path` before `until`. Items embed author and committer accounts, so
        the snapshot is `person_level_24m` and the caller drops it right after
        `parse_commit_refs` (CB-24; M23)."""
        return self.fetch(
            f"{API}/repos/{full_name}/commits",
            params={
                "path": path,
                "until": until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "per_page": per_page,
            },
            repo_id=repo_id,
            retention_class="person_level_24m",
        )

    def probe(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Probe:
        """A conditional GET for evidence decay (R19.8): budgeted and rate-limited like every
        request, a `304` costs no rate limit; nothing is snapshotted or parsed."""
        from pigtail.capture.snapshots import sha256_hex

        hdrs: dict[str, str] = {}
        if etag:
            hdrs["If-None-Match"] = etag
        if last_modified:
            hdrs["If-Modified-Since"] = last_modified
        try:
            resp = self._send("GET", url, headers=hdrs)
        except FetchError as e:
            return Probe(e.status, None, None, None)
        ok = resp.is_success
        return Probe(
            resp.status_code,
            sha256_hex(resp.content) if ok else None,
            resp.headers.get("ETag"),
            resp.headers.get("Last-Modified"),
            resp.content if ok else b"",
        )

    def releases_page(
        self, full_name: str, *, page: int = 1, repo_id: str | None = None
    ) -> Fetched:
        """One page of `GET /repos/{owner}/{repo}/releases` (core bucket, newest first,
        100 per page), not parsed (ADR-084, the view-B anchor). The items embed the author's
        account object, so the snapshot is classed `person_level_24m` and the caller drops the
        raw bytes right after `parse_releases` (CB-24), which never reads the author."""
        if page < 1:
            raise ValueError("releases: page >= 1")
        return self.fetch(
            releases_url(full_name),
            params={"per_page": RELEASES_PER_PAGE, "page": page},
            repo_id=repo_id,
            retention_class="person_level_24m",
        )

    def repo_links(
        self, names: Sequence[str]
    ) -> tuple[dict[str, tuple[str | None, str | None]], list[Fetched]]:
        """(lowercase `owner/name` -> (homepage URL, owner type), the fetched answers) for up to
        50 repos per GraphQL query (ADR-085). The caller drops the raw answers after parsing:
        a homepage field may itself be a Bluesky profile link."""
        out: dict[str, tuple[str | None, str | None]] = {}
        fetched: list[Fetched] = []
        uniq = list(dict.fromkeys(n.strip() for n in names if n.strip()))
        for start in range(0, len(uniq), 50):
            chunk = uniq[start : start + 50]
            q, v = repo_links_query(chunk)
            res = self.graphql(q, v, est_cost=1)
            fetched.append(res.fetched)
            for i, full in enumerate(chunk):
                got = parse_repo_links(res.data.get(f"r{i}"))
                if got is not None:
                    out[full.lower()] = got
        return out, fetched

    def social_accounts(self, login: str, *, evidence_url: str) -> Fetched | None:
        """`GET /users/{login}/social_accounts` (core bucket), not parsed, or None for an
        unknown user. Person-level (a user's own links): classed `person_level_24m` and dropped
        by the caller right after `parse_social_accounts` (ADR-085)."""
        try:
            return self.fetch(
                f"{API}/users/{login}/social_accounts",
                params={"per_page": 100},
                retention_class="person_level_24m",
                evidence_url=evidence_url,
            )
        except NotFound:
            return None

    def org_profile(self, org: str, *, evidence_url: str) -> Fetched | None:
        """`GET /orgs/{org}` (core bucket), not parsed, or None for an unknown org; dropped by
        the caller right after `parse_org_profile` (ADR-085)."""
        try:
            return self.fetch(
                f"{API}/orgs/{org}", retention_class="person_level_24m", evidence_url=evidence_url
            )
        except NotFound:
            return None

    def repos_metadata(self, names: Sequence[str]) -> dict[str, RepoMeta]:
        """Project-level metadata for `owner/name` repos, 50 per GraphQL query (M22 discovery).
        Keys are lowercase `owner/name` as asked; repos that don't resolve are left out."""
        out: dict[str, RepoMeta] = {}
        uniq = list(dict.fromkeys(n.strip() for n in names if n.strip()))
        for start in range(0, len(uniq), 50):
            chunk = uniq[start : start + 50]
            q, v = repo_meta_query(chunk)
            res = self.graphql(q, v, est_cost=1)
            for i, full in enumerate(chunk):
                meta = parse_repo_node(res.data.get(f"r{i}"))
                if meta is not None:
                    out[full.lower()] = meta
        return out

    def star_history(
        self,
        full_name: str,
        *,
        per_page: int = 6,
        page: int = 1,
        repo_id: str | None = None,
        conditional: bool = True,
    ) -> tuple[Conditional, list[StarWeek]]:
        """One page of the star-history endpoint (core bucket, project-level, no identities)."""
        if not 1 <= per_page <= STAR_HISTORY_MAX_PER_PAGE or not 1 <= page <= STAR_HISTORY_MAX_PAGE:
            raise ValueError("star history: per_page 1..30 weeks, page 1..100 (GitHub docs)")
        params = {"per_page": per_page, "page": page}
        if conditional:
            c = self.fetch_conditional(
                star_history_url(full_name),
                params=params,
                repo_id=repo_id,
                retention_class="project_level",
            )
        else:
            f = self.fetch(
                star_history_url(full_name),
                params=params,
                repo_id=repo_id,
                retention_class="project_level",
            )
            c = Conditional(f.meta.url, 200, f, None, None)
        if c.fetched is None:
            h = c.previous.content_hash if c.previous else None
            if h and self.store.exists(h):
                return c, parse_star_history(self.store.get(h))
            return c, []
        return c, parse_star_history(c.fetched.data)

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        """Replay path: records from a snapshot (no handles are ever produced)."""
        path = httpx.URL(meta.url).path
        if path.endswith("/stargazers/history"):
            for w in parse_star_history(data):
                for i, n in enumerate(w.days):
                    yield {
                        "week_label": w.week_label,
                        "day": (w.week_start + timedelta(days=i)).isoformat(),
                        "stars_net": n,
                    }
        elif path.startswith("/search/"):
            for r in parse_search_page(data).items:
                yield {"repo_id": r.id, "full_name": r.full_name, "stars": r.stars}
        elif path.startswith("/graphql"):
            doc = json.loads(data).get("data") or {}
            for alias, node in doc.items():
                if alias != "rateLimit" and isinstance(node, dict):
                    yield {"alias": alias, **{k: node.get(k) for k in COUNT_FIELDS}}


COUNT_FIELDS = ("databaseId", "nameWithOwner", "stargazerCount", "forkCount", "pushedAt")


def _opt_int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# --- person-level per-repo events -----------------------------------------------------------------
class GitHubRepoEventsConnector(GitHubAPI):
    name: ClassVar[str] = "github_events"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = EVENTS_TERMS
    enabled_by_default: ClassVar[bool] = False
    enable_env: ClassVar[str | None] = EVENTS_ENABLE_ENV
    person_level_hold: ClassVar[bool] = True  # ADR-022 / ADR-036
    retention_class = "person_level_30d"  # CB-22
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ("actor",)
    handle_namespace: ClassVar[str] = "github"
    transient_actor_tokens: ClassVar[bool] = True  # de-duplication within one poll only
    repo_fields: ClassVar[tuple[str, ...]] = ("repo_id",)
    repo_host: ClassVar[str] = "github"
    timeout_seconds: ClassVar[float] = 30.0

    def repo_events(
        self, full_name: str, *, page: int = 1, repo_id: str | None = None
    ) -> Conditional:
        """`GET /repos/{o}/{r}/events?per_page=100&page=N` with ETag (1 ≤ N ≤ 3: 300 events)."""
        if not 1 <= page <= 3:
            raise ValueError("the events window is 300 events: page 1..3 at per_page=100")
        return self.fetch_conditional(
            events_url(full_name),
            params={"per_page": 100, "page": page},
            repo_id=repo_id,
            retention_class="person_level_30d",
        )

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        """Only `WatchEvent` and `ForkEvent` survive (CB-23); payloads are never kept."""
        doc = json.loads(data)
        if not isinstance(doc, list):
            return
        for ev in doc:
            try:
                if ev["type"] not in KEPT_EVENT_TYPES:
                    continue
                yield {
                    "event_id": str(ev["id"]),
                    "type": ev["type"],
                    "repo_id": int(ev["repo"]["id"]),
                    "actor": (ev.get("actor") or {}).get("login"),
                    "created_at": ev.get("created_at"),
                }
            except (KeyError, TypeError, ValueError):
                continue

    def _pre_code(self, record: Record) -> Record | None:
        login = record.get("actor")
        bot = login is None or is_bot_login(login)
        record["automated_account"] = bot  # a missing login counts as automated (bot-filter-v0)
        if bot:
            record["actor"] = None  # bots are not persons: neither kept nor hashed
        return record
