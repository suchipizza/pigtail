"""Hacker News connectors: official Firebase API and Algolia search API (M1-T4, R1.2, R2.1).

Terms: TM-03 (Algolia) and TM-04 (Firebase) in docs/compliance/terms-memos.md, both
CLEARED-WITH-CONDITIONS; use by commercial operators is pending LQ-6. Conditions implemented here:

- API only: nothing in pigtail requests news.ycombinator.com HTML.
- Algolia at or below 10,000 requests/hour: the limiter runs at 5,000/hour (50 % margin).
- Firebase polling kept modest (TM-04): a self-imposed 2 requests/s minus a 20 % margin; the rank
  poller (`pigtail.connectors.hn_ranks`) polls `topstories` at most once a minute.
- Raw JSON is snapshotted (private storage) before parsing; comment text is person-level content
  (retention class `person_level_24m`) and is never republished.
- `by` / `author` handles are pseudonymized at ingest in namespace "hn".
- The `deleted` / `dead` flags are propagated by deletion sync (`pigtail.privacy.deletion_sync`).

Both connectors are **disabled by default** (`PIGTAIL_ENABLE_HN=0`) and held by ADR-022: enabling
them raises `PersonSourceHold` unless `PIGTAIL_ADR022_PERSON_SOURCES_OK=1`
(`pigtail.connectors.base`).

HN text fields are HTML (`&#x2F;` for `/`); records carry the unescaped text, in memory only, for
mention matching: it is never written to Postgres. Parsed records carry `evidence_type`
(codebook §2.1: `community_post` for stories and comments, `platform_metric` for id lists) and
`capture_mode = "api_json"`.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import (
    Clearance,
    Connector,
    Fetched,
    Record,
    TermsMetadata,
)

FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
YC_TERMS = "https://www.ycombinator.com/legal"
HN_ENABLE_ENV = "PIGTAIL_ENABLE_HN"

StoryList = Literal["topstories", "newstories", "beststories", "showstories"]
STORY_LISTS: tuple[StoryList, ...] = ("topstories", "newstories", "beststories", "showstories")

HN_CONDITIONS = (
    "Conditions: API only, never scrape news.ycombinator.com; snapshots private, no "
    "republication of comment text; usernames pseudonymized; commercial use pending LQ-6."
)

FIREBASE_TERMS = TermsMetadata(
    terms_url="https://github.com/HackerNews/API",
    terms_basis=(
        "TM-04 (docs/compliance/terms-memos.md): official HN Firebase API "
        "(https://github.com/HackerNews/API, 'There is currently no rate limit'); YC Terms of Use "
        f"apply ({YC_TERMS}). {HN_CONDITIONS} Modest polling (topstories at most once a minute); "
        "the deleted flag is propagated."
    ),
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,  # unknown: LQ-6
    deletion_obligation=None,  # no duty found (TM-04); courtesy deletion sync, R1.5
    notes="API v0; 'changes won't always be backward compatible'.",
)

ALGOLIA_TERMS = TermsMetadata(
    terms_url="https://hn.algolia.com/api",
    terms_basis=(
        "TM-03 (docs/compliance/terms-memos.md): HN Algolia search API "
        "(https://hn.algolia.com/api, 10,000 requests/hour per IP); YC Terms of Use apply "
        f"({YC_TERMS}). {HN_CONDITIONS} At most 10,000 requests/hour."
    ),
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,  # unknown: LQ-6
    deletion_obligation=None,  # no duty found (TM-03); courtesy deletion sync, R1.5
    notes="~1,000 hits per query (observed, undocumented; source-matrix §2.3): windowed queries.",
)


# --- GitHub repo URLs ---------------------------------------------------------------------------
_GH_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_GH_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
# First path segments that are GitHub site pages, not owners.
_GH_NOT_OWNER = frozenset(
    {
        "about", "apps", "blog", "collections", "contact", "customer-stories", "enterprise",
        "events", "explore", "features", "login", "marketplace", "new", "notifications", "orgs",
        "organizations", "pricing", "pulls", "issues", "search", "security", "settings",
        "signup", "site", "sponsors", "topics", "trending", "users", "readme", "resources",
        "solutions", "team", "join", "codespaces", "copilot", "models", "dashboard", "stars",
    }
)  # fmt: skip
_GH_URL_IN_TEXT = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+", re.IGNORECASE
)


def normalize_github_repo(url: str | None) -> str | None:
    """`owner/name` (lowercase) for a github.com repo URL, else None.

    Accepts `http(s)://`, `www.` and scheme-less forms, deeper paths (`/issues/1`, `/tree/x`),
    a `.git` suffix, query strings and fragments. Profile URLs, site pages and other hosts
    (gist.github.com, *.github.io) return None.
    """
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        return None
    owner, name = segs[0], segs[1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    if owner.lower() in _GH_NOT_OWNER or not _GH_OWNER.match(owner) or not _GH_REPO.match(name):
        return None
    if name in (".", ".."):
        return None
    return f"{owner}/{name}".lower()


def github_repos_in_text(text: str | None) -> list[str]:
    """Distinct `owner/name` repos linked in free text (order of first appearance)."""
    out: list[str] = []
    for m in _GH_URL_IN_TEXT.finditer(text or ""):
        r = normalize_github_repo(m.group(0).rstrip(".,;:!?)"))
        if r and r not in out:
            out.append(r)
    return out


def _ts(value: Any) -> str | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC).isoformat()
    return None


def _item_type(value: Any) -> str:
    return value if value in ("story", "comment", "poll", "job") else "other"


# --- Firebase -----------------------------------------------------------------------------------
def item_url(item_id: int | str) -> str:
    return f"{FIREBASE_BASE}/item/{int(item_id)}.json"


def list_url(kind: StoryList) -> str:
    if kind not in STORY_LISTS:
        raise ValueError(f"unknown story list {kind!r}")
    return f"{FIREBASE_BASE}/{kind}.json"


def parse_id_list(data: bytes) -> list[int]:
    """A Firebase story list (`[id, id, ...]`), in rank order; non-ints are skipped."""
    raw = json.loads(data)
    if not isinstance(raw, list):
        raise ValueError("story list is not a JSON array")
    return [int(v) for v in raw if isinstance(v, int) and not isinstance(v, bool)]


def is_list_url(url: str) -> bool:
    return urlsplit(url).path.rsplit("/", 1)[-1].removesuffix(".json") in STORY_LISTS


class HNFirebaseConnector(Connector):
    """Official HN API: items by id and the story lists (M1-T4a, TM-04).

    Records: an item document yields one record (`by` pseudonymized, `text` kept in memory only
    for mention matching; callers must not persist it); a story list yields one record per id
    (`{"list", "rank", "item_id"}`). A missing item (`null`) yields nothing.
    """

    name: ClassVar[str] = "hn_firebase"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = FIREBASE_TERMS
    enabled_by_default: ClassVar[bool] = False
    enable_env: ClassVar[str | None] = HN_ENABLE_ENV
    person_level_hold: ClassVar[bool] = True
    rate_per_second: ClassVar[float] = 2.0
    safety_margin: ClassVar[float] = 0.2
    retention_class = "person_level_24m"
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ("by",)
    handle_namespace: ClassVar[str] = "hn"
    timeout_seconds: ClassVar[float] = 30.0

    def fetch_item(
        self, item_id: int, *, case_id: str | None = None, repo_id: str | None = None
    ) -> tuple[Fetched, Record | None]:
        f = self.fetch(item_url(item_id), case_id=case_id, repo_id=repo_id)
        recs = list(self.records(f.data, f.meta))
        return f, (recs[0] if recs else None)

    def fetch_list(self, kind: StoryList) -> tuple[Fetched, list[int]]:
        """A story list; its snapshot holds ids only (project-level)."""
        f = self.fetch(list_url(kind), retention_class="project_level")
        return f, parse_id_list(f.data)

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        if is_list_url(meta.url):
            kind = urlsplit(meta.url).path.rsplit("/", 1)[-1].removesuffix(".json")
            for rank, iid in enumerate(parse_id_list(data), start=1):
                yield {
                    "list": kind,
                    "rank": rank,
                    "item_id": iid,
                    "evidence_type": "platform_metric",
                    "capture_mode": "api_json",
                }
            return
        item = json.loads(data)
        if not isinstance(item, dict) or "id" not in item:
            return  # `null`: no such item (or removed)
        yield parse_firebase_item(item)


def parse_firebase_item(item: dict[str, Any]) -> Record:
    """Minimal record from a Firebase item. `by` is still raw here (pseudonymized by records())."""
    typ = _item_type(item.get("type"))
    url = item.get("url") if isinstance(item.get("url"), str) else None
    text = html.unescape(item["text"]) if isinstance(item.get("text"), str) else None
    repos = [r for r in [normalize_github_repo(url)] if r]
    repos += [r for r in github_repos_in_text(text) if r not in repos]
    return {
        "item_id": int(item["id"]),
        "type": typ,
        "by": item.get("by") if isinstance(item.get("by"), str) else None,
        "time": item.get("time"),
        "created_at": _ts(item.get("time")),
        "title": item.get("title") if isinstance(item.get("title"), str) else None,
        "url": url,
        "text": text,
        "score": item.get("score"),
        "descendants": item.get("descendants"),
        "parent": item.get("parent"),
        "n_kids": len(item.get("kids") or []),
        "deleted": bool(item.get("deleted", False)),
        "dead": bool(item.get("dead", False)),
        "repo_full_names": repos,
        "evidence_type": "community_post",
        "capture_mode": "api_json",
    }


# --- Algolia ------------------------------------------------------------------------------------
HITS_CAP = 1000  # observed, undocumented (source-matrix §2.3): results stop after ~1,000 hits
HITS_PER_PAGE = 100
MIN_WINDOW_SECONDS = 3600
HN_EPOCH = 1160418111  # item 1, 2006-10-09T18:21:51Z (source-matrix §2.3)


@dataclass(frozen=True)
class AlgoliaQuery:
    """One search: `label` names the mention rule it serves (url, full_name, name_and_owner…)."""

    label: str
    query: str
    tags: str = "(story,comment)"
    restrict_to_url: bool = False


@dataclass
class SearchPage:
    fetched: Fetched
    records: list[Record]
    nb_hits: int
    nb_pages: int
    page: int


@dataclass
class SearchResult:
    pages: list[SearchPage] = field(default_factory=list)
    truncated_windows: int = 0  # windows that still exceeded the hit cap at minimum width

    @property
    def records(self) -> Iterator[tuple[SearchPage, Record]]:
        for p in self.pages:
            for r in p.records:
                yield p, r


class HNAlgoliaConnector(Connector):
    """HN Algolia search (`search_by_date`) for repo mentions, with date windows (M1-T4b, TM-03).

    Paging stays within the observed ~1,000-hit cap: `HITS_PER_PAGE` hits per page, at most
    `HITS_CAP / HITS_PER_PAGE` pages per window; a window with more hits than the cap is split in
    two by `created_at_i` (down to one hour) instead of paging past the cap.
    """

    name: ClassVar[str] = "hn_algolia"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = ALGOLIA_TERMS
    enabled_by_default: ClassVar[bool] = False
    enable_env: ClassVar[str | None] = HN_ENABLE_ENV
    person_level_hold: ClassVar[bool] = True
    rate_per_second: ClassVar[float] = 10_000 / 3600  # documented limit, per IP
    safety_margin: ClassVar[float] = 0.5  # -> 5,000 requests/hour
    retention_class = "person_level_24m"
    reliability = "high"
    handle_fields: ClassVar[tuple[str, ...]] = ("author",)
    handle_namespace: ClassVar[str] = "hn"
    timeout_seconds: ClassVar[float] = 30.0

    def search_page(
        self,
        q: AlgoliaQuery,
        *,
        since_i: int,
        until_i: int,
        page: int = 0,
        case_id: str | None = None,
        repo_id: str | None = None,
    ) -> SearchPage:
        params: dict[str, Any] = {
            "query": q.query,
            "tags": q.tags,
            "numericFilters": f"created_at_i>={since_i},created_at_i<{until_i}",
            "hitsPerPage": HITS_PER_PAGE,
            "page": page,
        }
        if q.restrict_to_url:
            params["restrictSearchableAttributes"] = "url"
        f = self.fetch(
            f"{ALGOLIA_BASE}/search_by_date", params=params, case_id=case_id, repo_id=repo_id
        )
        body = json.loads(f.data)
        return SearchPage(
            fetched=f,
            records=list(self.records(f.data, f.meta)),
            nb_hits=int(body.get("nbHits") or 0),
            nb_pages=int(body.get("nbPages") or 0),
            page=int(body.get("page") or page),
        )

    def search(
        self,
        q: AlgoliaQuery,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        case_id: str | None = None,
        repo_id: str | None = None,
    ) -> SearchResult:
        lo = int(since.timestamp()) if since else HN_EPOCH
        hi = int((until or self.clock()).timestamp()) + 1
        res = SearchResult()
        self._window(q, lo, hi, res, case_id=case_id, repo_id=repo_id)
        return res

    def _window(
        self,
        q: AlgoliaQuery,
        lo: int,
        hi: int,
        res: SearchResult,
        *,
        case_id: str | None,
        repo_id: str | None,
    ) -> None:
        if hi <= lo:
            return
        first = self.search_page(q, since_i=lo, until_i=hi, case_id=case_id, repo_id=repo_id)
        # Every fetched page is returned, even one that is then split: its snapshot holds
        # person-level content that deletion sync must track. Callers dedupe by item id.
        res.pages.append(first)
        if first.nb_hits > HITS_CAP and hi - lo > MIN_WINDOW_SECONDS:
            mid = lo + (hi - lo) // 2  # too many hits: split instead of paging past the cap
            if self.run is not None:
                self.run.incr(f"{self.name}.window_splits")
            self._window(q, lo, mid, res, case_id=case_id, repo_id=repo_id)
            self._window(q, mid, hi, res, case_id=case_id, repo_id=repo_id)
            return
        if first.nb_hits > HITS_CAP:
            res.truncated_windows += 1
        max_pages = min(first.nb_pages, HITS_CAP // HITS_PER_PAGE)
        for page in range(1, max_pages):
            p = self.search_page(
                q, since_i=lo, until_i=hi, page=page, case_id=case_id, repo_id=repo_id
            )
            res.pages.append(p)
            if not p.records:
                break

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        body = json.loads(data)
        for hit in body.get("hits") or []:
            rec = parse_algolia_hit(hit)
            if rec is not None:
                yield rec


def parse_algolia_hit(hit: dict[str, Any]) -> Record | None:
    """Minimal record from a search hit. `author` is raw here; `_tags` / `_highlightResult`
    (which repeat the username) are never copied."""
    try:
        item_id = int(hit["objectID"])
    except (KeyError, TypeError, ValueError):
        return None
    tags = [t for t in hit.get("_tags") or [] if isinstance(t, str)]
    typ = next((t for t in ("story", "comment", "poll", "job") if t in tags), "other")
    raw_text = hit.get("comment_text") or hit.get("story_text")
    text = html.unescape(raw_text) if isinstance(raw_text, str) else None
    url = hit.get("url") if isinstance(hit.get("url"), str) else None
    repos = [r for r in [normalize_github_repo(url)] if r]
    repos += [r for r in github_repos_in_text(text) if r not in repos]
    return {
        "item_id": item_id,
        "type": typ,
        "author": hit.get("author") if isinstance(hit.get("author"), str) else None,
        "created_at": hit.get("created_at"),
        "created_at_i": hit.get("created_at_i"),
        "title": hit.get("title") if typ == "story" else None,
        "url": url if typ == "story" else None,
        "text": text,
        "story_id": hit.get("story_id"),
        "parent_id": hit.get("parent_id"),
        "points": hit.get("points"),
        "num_comments": hit.get("num_comments"),
        "front_page_tag": "front_page" in tags,
        "show_hn": "show_hn" in tags,
        "ask_hn": "ask_hn" in tags,
        "repo_full_names": repos,
        "evidence_type": "community_post",
        "capture_mode": "api_json",
    }
