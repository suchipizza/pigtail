"""Candidate discovery for a brief version (PRD R4.5, R4.11; ADR-047.1, ADR-054.3, ADR-057).

Sources, all restricted to the brief's accepted expansion, field boundaries and time window:

- **GitHub search** (`search_repos`, paged, under the GitHub budget of
  `pigtail.connectors.github_budget`): one query per keyword, search query and the core field
  (`<term> in:name,description,topics`), and one per GitHub topic (`topic:<slug>`), each limited
  to repos created in the window with at least `min_stars` stars, not archived. Search pages keep
  only project-level fields and are dropped right after parsing (CB-24).
- **Show HN** (`HNShowDiscoveryConnector`): stories matching each keyword in the window that
  link a GitHub repo; project-level story metadata only (title, url, points, time), raw page
  dropped after parsing (see the connector's docstring for why this runs before CB-12).
- **Awesome lists** named in the brief (a GitHub URL whose repo name starts with `awesome`, in
  the expansion's competitors or the named projects) or found through the brief's GitHub topics
  (`topic:awesome-list <topic>`): the list's README is fetched through the GitHub API and its
  repo links extracted; linked repos created in the window with enough stars become candidates.
- **GH Archive** (off by default; `gharchive_hours` > 0): sampled hourly dumps add an activity
  signal to candidates already found. It never adds a candidate (discovery signal only).
- **Named projects**: every reference case and distribution exemplar of the brief is always
  added (R4.11, ADR-057). The repo follows the launch-link rule (ADR-054.3): the brief's `repo`,
  else a GitHub repo URL in its `urls`, else the repo its Show HN launch posts linked to; when
  that gives several repos or none, the case is stored **unresolved** with candidate matches
  (URL, one-line description, stars) for the user to confirm in the shortlist review. An
  unresolved case never blocks the run (ADR-062).

Every candidate records which sources found it (`sources`: source, term or list, rank or
points) and when it was first seen; candidates are de-duplicated by `owner/name`. Progress is
checkpointed per query, so a resumed run skips the queries it already did (idempotent anyway:
rows are upserted).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pigtail.briefs.candidates import (
    Candidate,
    CandidateStore,
    clean_description,
    gh_ref,
    named_ref,
)
from pigtail.briefs.model import Brief, NamedProject
from pigtail.capture.db import CaptureDB
from pigtail.capture.github_search import search_repos
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import ConnectorError, FetchError
from pigtail.connectors.github import GitHubConnector, RepoMeta, SearchRepo
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.connectors.hn import (
    HNShowDiscoveryConnector,
    github_repos_in_text,
    normalize_github_repo,
    parse_show_hn_page,
)
from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog, drop_after_parse

log = logging.getLogger("pigtail.briefs.discovery")

DISCOVERY_VERSION = "discovery-v1"
EARLIEST = "2008-01-01T00:00:00Z"  # GitHub's start: "no lower bound" for awesome lists
# evidence URLs for named-project searches: the query is replaced by the `named:<panel>:<i>` key
HN_SEARCH_EVIDENCE = "https://hn.algolia.com/api/v1/search"
GITHUB_SEARCH_EVIDENCE = "https://api.github.com/search/repositories"


@dataclass(frozen=True)
class DiscoveryConfig:
    min_stars: int = 10
    pages_per_query: int = 1  # 100 results per page
    max_queries: int = 60
    max_candidates: int = 1500
    show_hn_hits: int = 50
    awesome_lists_per_topic: int = 2
    awesome_topics: int = 5
    awesome_max_links: int = 300
    reference_matches: int = 5
    gharchive_hours: int = 0  # 0: GH Archive off (default)


class DiscoveryPaused(Exception):
    """The GitHub request budget ran out: the stage stops with a resumable checkpoint."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"GitHub request budget exhausted ({reason})")
        self.reason = reason


@dataclass
class DiscoveryResult:
    queries_run: int = 0
    queries_skipped: int = 0
    new_candidates: int = 0
    seen: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    named_resolved: int = 0
    named_unresolved: int = 0
    capped: bool = False
    suppressed: int = 0  # repos on the refusal list, skipped (CB-13)
    skipped_sources: dict[str, str] = field(default_factory=dict)
    window: dict[str, str] = field(default_factory=dict)

    def add(self, source: str, new: bool) -> None:
        self.by_source[source] = self.by_source.get(source, 0) + 1
        self.seen += 1
        self.new_candidates += int(new)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _months_back(end: date, months: int) -> date:
    y, m = end.year, end.month - months
    while m <= 0:
        m += 12
        y -= 1
    day = min(end.day, 28)
    return date(y, m, day)


def window_bounds(brief: Brief, run_date: date) -> tuple[datetime, datetime]:
    """[start, end] of the brief's window (R18.1): `window.end` or the run date, back `months`."""
    end = brief.window.end or run_date
    start = _months_back(end, brief.window.months)
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC),
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        s = " ".join(it.split())
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


_UNSAFE = re.compile(r"[\"\\:]")


def _term(t: str) -> str:
    """A free-text search term without qualifier syntax (quotes, colons)."""
    return " ".join(_UNSAFE.sub(" ", t).split())


def search_terms(brief: Brief) -> list[str]:
    """Keywords, search queries and the core field (free-text terms, de-duplicated)."""
    e = brief.expansion
    terms = [*(e.keywords if e else []), *(e.search_queries if e else []), brief.field.core_field]
    return _dedupe(_term(t) for t in terms)


def github_queries(
    brief: Brief, start: datetime, end: datetime, cfg: DiscoveryConfig
) -> list[tuple[str, str, str]]:
    """(key, kind, query) per GitHub search, in a stable order (the checkpoint keys)."""
    tail = f"created:{_iso(start)}..{_iso(end)} stars:{cfg.min_stars}..* archived:false"
    out: list[tuple[str, str, str]] = []
    for t in search_terms(brief):
        out.append((f"kw:{t.lower()}", "keyword", f"{t} in:name,description,topics {tail}"))
    for topic in _dedupe(brief.expansion.github_topics if brief.expansion else []):
        out.append((f"topic:{topic}", "topic", f"topic:{topic} {tail}"))
    return out[: cfg.max_queries]


def _meta_from_search(r: SearchRepo) -> dict[str, Any]:
    return {
        "description": clean_description(r.description, r.full_name.split("/")[0]),
        "topics": list(r.topics)[:20],
        "language": r.language,
        "stars": r.stars,
        "forks": r.forks,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None,
        "owner_type": r.owner_type,
        "archived": r.archived,
        "fork": r.fork,
    }


def _meta_from_graphql(m: RepoMeta) -> dict[str, Any]:
    return {
        "description": clean_description(m.description, m.full_name.split("/")[0]),
        "topics": list(m.topics)[:20],
        "language": m.language,
        "stars": m.stars,
        "forks": m.forks,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "pushed_at": m.pushed_at.isoformat() if m.pushed_at else None,
        "owner_type": m.owner_type,
        "archived": m.archived,
        "fork": m.fork,
    }


def _match(m: RepoMeta) -> dict[str, Any]:
    return {
        "full_name": m.full_name.lower(),
        "url": f"https://github.com/{m.full_name.lower()}",
        "description": clean_description(m.description, m.full_name.split("/")[0]),
        "stars": m.stars,
    }


@dataclass
class Discovery:
    brief: Brief
    db: CaptureDB
    store: CandidateStore
    github: GitHubConnector
    hn: HNShowDiscoveryConnector | None
    brief_run_id: str | None
    run_date: date
    checkpoint: dict[str, Any]
    save: Callable[[dict[str, Any]], None]
    cfg: DiscoveryConfig = DiscoveryConfig()
    run: RunRecorder | None = None
    gharchive: Any = None  # GHArchiveConnector when GH Archive is configured
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    since: datetime | None = None  # --incremental: only repos created after this
    result: DiscoveryResult = field(default_factory=DiscoveryResult)

    # --- helpers -------------------------------------------------------------------------------
    @property
    def done(self) -> set[str]:
        return set(self.checkpoint.setdefault("done", []))

    def _mark(self, key: str) -> None:
        d = self.checkpoint.setdefault("done", [])
        if key not in d:
            d.append(key)
        self.save(self.checkpoint)

    def _full(self) -> bool:
        if self.store.count() >= self.cfg.max_candidates:
            self.result.capped = True
            return True
        return False

    def _suppressed(self, c: Candidate) -> bool:
        """CB-13: a repo on the refusal list (by id or by name) never becomes a candidate."""
        sup = getattr(self.github, "suppression", None)
        if not sup:
            return False
        if c.repo_host_id is not None and f"github:{c.repo_host_id}" in sup.repos:
            return True
        return bool(sup.name_suppressed(c.repo_full_name))

    def _upsert(self, c: Candidate) -> None:
        if self._suppressed(c):
            self.result.suppressed += 1
            return
        new = self.store.upsert(c, brief_run_id=self.brief_run_id, now=self.clock())
        self.result.add(str(c.sources[0]["source"]) if c.sources else "unknown", new)

    def _dlog(self) -> DeletionLog:
        return DeletionLog(self.db, "retention", run_id=self.run.id if self.run else None)

    def _in_window(self, created: datetime | None, start: datetime, end: datetime) -> bool:
        return created is not None and start <= created <= end

    def _metadata(self, names: list[str]) -> dict[str, RepoMeta]:
        if not names:
            return {}
        return self.github.repos_metadata(names)

    # --- sources -------------------------------------------------------------------------------
    def github_search(self, start: datetime, end: datetime) -> None:
        for key, kind, q in github_queries(self.brief, start, end, self.cfg):
            if key in self.done:
                self.result.queries_skipped += 1
                continue
            if self._full():
                return
            res = search_repos(
                self.github, self.db, q, max_pages=self.cfg.pages_per_query, run=self.run
            )
            if res.budget_stop:
                raise DiscoveryPaused(res.budget_stop)
            self.result.queries_run += 1
            term = key.split(":", 1)[1]
            for rank, r in enumerate(res.items, 1):
                if r.fork or r.archived:
                    continue
                if self.since is not None and (r.created_at is None or r.created_at < self.since):
                    continue
                if self._full():
                    break
                self._upsert(
                    Candidate(
                        ref=gh_ref(r.full_name),
                        repo_full_name=r.full_name.lower(),
                        repo_host_id=r.id,
                        sources=[
                            {
                                "source": f"github_{kind}",
                                "term": term,
                                "rank": rank,
                                "stars": r.stars,
                            }
                        ],
                        metadata=_meta_from_search(r),
                    )
                )
            self._mark(key)

    def _show_hn(
        self,
        query: str,
        since: datetime | None,
        until: datetime | None,
        evidence_url: str | None = None,
    ) -> list[Any]:
        assert self.hn is not None
        f = self.hn.search_show_hn(
            query, since=since, until=until, hits=self.cfg.show_hn_hits, evidence_url=evidence_url
        )
        try:
            stories, _ = parse_show_hn_page(f.data)
        except PARSE_ERRORS:
            stories = []
        # minimal collection: nothing but the parsed project-level fields survives (CB-24)
        drop_after_parse(self.db, self.hn.store, f.evidence.id, f.content_hash, self._dlog())
        return stories

    def show_hn(self, start: datetime, end: datetime) -> None:
        if self.hn is None or not self.hn.enabled:
            self.result.skipped_sources["show_hn"] = "connector disabled or not configured"
            return
        for t in search_terms(self.brief):
            key = f"showhn:{t.lower()}"
            if key in self.done:
                self.result.queries_skipped += 1
                continue
            if self._full():
                return
            stories = self._show_hn(t, self.since or start, end)
            self.result.queries_run += 1
            linked = [s for s in stories if s.repo_full_name]
            meta = self._metadata([s.repo_full_name for s in linked])
            for s in linked:
                m = meta.get(s.repo_full_name)
                if m is None or m.fork or m.archived:
                    continue
                self._upsert(
                    Candidate(
                        ref=gh_ref(m.full_name),
                        repo_full_name=m.full_name.lower(),
                        repo_host_id=m.host_id,
                        sources=[
                            {
                                "source": "show_hn",
                                "term": t,
                                "hn_item_id": s.item_id,
                                "points": s.points,
                                "title": s.title,
                                "time": s.created_at.isoformat() if s.created_at else None,
                            }
                        ],
                        metadata=_meta_from_graphql(m),
                    )
                )
            self._mark(key)

    def _named_lists(self) -> list[str]:
        urls: list[str] = []
        e = self.brief.expansion
        for c in e.competitors if e else []:
            if c.url:
                urls.append(c.url)
        for p in [*self.brief.field.reference_cases, *self.brief.distribution_exemplars.projects]:
            urls += list(p.urls)
            if p.repo:
                urls.append(f"https://github.com/{p.repo}")
        repos = [normalize_github_repo(u) for u in urls]
        return _dedupe(r for r in repos if r and r.split("/", 1)[1].startswith("awesome"))

    def awesome_lists(self, start: datetime, end: datetime) -> None:
        lists = [(r, "named") for r in self._named_lists()]
        topics = _dedupe(self.brief.expansion.github_topics if self.brief.expansion else [])
        for topic in topics[: self.cfg.awesome_topics]:
            key = f"awesome-search:{topic}"
            found: list[str] = self.checkpoint.setdefault("awesome_found", {}).get(topic, [])
            if key not in self.done:
                q = f"topic:awesome-list {topic} created:{EARLIEST}..{_iso(end)} stars:50..*"
                res = search_repos(self.github, self.db, q, max_pages=1, run=self.run)
                if res.budget_stop:
                    raise DiscoveryPaused(res.budget_stop)
                self.result.queries_run += 1
                found = [r.full_name.lower() for r in res.items[: self.cfg.awesome_lists_per_topic]]
                self.checkpoint["awesome_found"][topic] = found
                self._mark(key)
            lists += [(r, f"topic:{topic}") for r in found]
        seen: set[str] = set()
        for full, via in lists:
            if full in seen:
                continue
            seen.add(full)
            key = f"awesome:{full}"
            if key in self.done:
                self.result.queries_skipped += 1
                continue
            if self._full():
                return
            f = self.github.readme(full)
            links: list[str] = []
            if f is not None:
                text = f.data.decode("utf-8", errors="replace")
                links = [r for r in github_repos_in_text(text) if r != full]
            links = links[: self.cfg.awesome_max_links]
            meta = self._metadata(links)
            lo = self.since or start
            for name in links:
                m = meta.get(name)
                if m is None or m.fork or m.archived or m.stars < self.cfg.min_stars:
                    continue
                if not self._in_window(m.created_at, lo, end):
                    continue
                if self._full():
                    break
                self._upsert(
                    Candidate(
                        ref=gh_ref(m.full_name),
                        repo_full_name=m.full_name.lower(),
                        repo_host_id=m.host_id,
                        sources=[{"source": "awesome_list", "list": full, "via": via}],
                        metadata=_meta_from_graphql(m),
                    )
                )
            self.result.queries_run += 1
            self._mark(key)

    # --- named projects (R4.11, ADR-054.3) -----------------------------------------------------
    def _resolve(
        self, p: NamedProject, key: str
    ) -> tuple[str | None, str, list[str], dict[str, Any]]:
        """(repo, rule, candidate repos, evidence) by the launch-link rule. The name searches
        store `key` (`named:<panel>:<i>`) in place of the query, so the project's name never
        reaches the database (ADR-076.6)."""
        if p.repo:
            return p.repo.lower(), "brief_repo", [], {}
        from_urls = _dedupe(r for r in (normalize_github_repo(u) for u in p.urls) if r)
        if len(from_urls) == 1:
            return from_urls[0], "brief_url", [], {}
        if len(from_urls) > 1:
            return None, "several_repos_in_brief_urls", from_urls, {}
        launch: list[str] = []
        items: list[int] = []
        if self.hn is not None and self.hn.enabled:
            ev_url = f"{HN_SEARCH_EVIDENCE}?query=[{key}]&tags=show_hn"
            for s in self._show_hn(_term(p.name), None, None, evidence_url=ev_url):
                if s.repo_full_name:
                    launch.append(s.repo_full_name)
                    items.append(s.item_id)
        launch = _dedupe(launch)
        if len(launch) == 1:
            return launch[0], "launch_link", [], {"hn_item_ids": items}
        if launch:
            return None, "several_repos_in_launch_posts", launch, {"hn_item_ids": items}
        q = f"{_term(p.name)} in:name created:{EARLIEST}..{_iso(self.clock())} stars:0..*"
        res = search_repos(
            self.github,
            self.db,
            q,
            max_pages=1,
            run=self.run,
            evidence_url=f"{GITHUB_SEARCH_EVIDENCE}?q=[{key}]",
        )
        if res.budget_stop:
            raise DiscoveryPaused(res.budget_stop)
        top = [r.full_name.lower() for r in res.items[: self.cfg.reference_matches]]
        return None, "no_launch_post_found", top, {}

    def named(self) -> None:
        groups: list[tuple[str, list[Any]]] = [
            ("reference", list(self.brief.field.reference_cases)),
            ("exemplar", list(self.brief.distribution_exemplars.projects)),
        ]
        for panel, projects in groups:
            for i, p in enumerate(projects):
                key = f"named:{panel}:{i}"
                if key in self.done:
                    continue
                repo, rule, options, ev = self._resolve(p, key)
                if repo is not None:
                    meta: RepoMeta | None = self._metadata([repo]).get(repo)
                    self._upsert(
                        Candidate(
                            ref=gh_ref(repo),
                            repo_full_name=repo,
                            repo_host_id=meta.host_id if meta else None,
                            panel="reference" if panel == "reference" else "exemplar",
                            named_index=i,
                            resolution="resolved",
                            resolution_rule=rule,
                            sources=[{"source": f"brief_{panel}", "rule": rule, **ev}],
                            metadata=_meta_from_graphql(meta) if meta else {},
                        )
                    )
                    self.result.named_resolved += 1
                else:
                    options = [
                        o
                        for o in options
                        if not self._suppressed(Candidate(ref=gh_ref(o), repo_full_name=o))
                    ]
                    found = self._metadata(options)
                    matches = [_match(found[o]) for o in options if o in found]
                    matches += [
                        {
                            "full_name": o,
                            "url": f"https://github.com/{o}",
                            "description": None,
                            "stars": None,
                        }
                        for o in options
                        if o not in found
                    ]
                    self._upsert(
                        Candidate(
                            ref=named_ref("reference" if panel == "reference" else "exemplar", i),
                            repo_full_name=None,
                            panel="reference" if panel == "reference" else "exemplar",
                            named_index=i,
                            resolution="unresolved",
                            resolution_rule=rule,
                            matches=matches,
                            sources=[{"source": f"brief_{panel}", "rule": rule, **ev}],
                        )
                    )
                    self.result.named_unresolved += 1
                self._mark(key)

    # --- GH Archive (discovery signals only; off by default) -----------------------------------
    def gharchive_signals(self, start: datetime, end: datetime) -> None:
        n = self.cfg.gharchive_hours
        if n <= 0 or self.gharchive is None:
            if n > 0:
                self.result.skipped_sources["gharchive"] = "not configured"
            return
        from pigtail.connectors.gharchive import hour_url

        names = {c.repo_full_name: c for c in self.store.all() if c.repo_full_name}
        span = (end - start) / n
        counts: dict[str, int] = {}
        sampled = 0
        for k in range(n):
            hour = (start + span * k + span / 2).replace(minute=0, second=0, microsecond=0)
            key = f"gharchive:{hour.isoformat()}"
            if key in self.done:
                continue
            try:
                f = self.gharchive.fetch(hour_url(hour))
            except (FetchError, ConnectorError):
                continue
            for rec in self.gharchive.records(f.data, f.meta):
                name = str(rec.get("repo_name") or "").lower()
                if name in names:
                    counts[name] = counts.get(name, 0) + 1
            drop_after_parse(
                self.db, self.gharchive.store, f.evidence.id, f.content_hash, self._dlog()
            )
            sampled += 1
            self._mark(key)
        for name, k in counts.items():
            c = names[name]
            c.sources = [
                {"source": "gharchive", "term": f"{n}h", "sampled_hours": sampled, "events": k}
            ]
            self.store.upsert(c, brief_run_id=self.brief_run_id, now=self.clock())

    # --- stage ---------------------------------------------------------------------------------
    def run_stage(self) -> DiscoveryResult:
        start, end = window_bounds(self.brief, self.run_date)
        self.result.window = {"start": _iso(start), "end": _iso(end)}
        try:
            self.named()
            self.github_search(start, end)
            self.show_hn(start, end)
            self.awesome_lists(start, end)
            self.gharchive_signals(start, end)
        except BudgetExhausted as e:
            raise DiscoveryPaused(e.reason) from None
        return self.result
