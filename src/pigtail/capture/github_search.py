"""Paged GitHub repository search for given queries (M11; kept from the removed search sweeps
for brief-scoped discovery, M13; CB-24, CB-23b).

`search_repos(conn, db, query)` pages one query the caller supplies (for example a brief's
keywords and topics) up to GitHub's 1,000-result cap, stopping at a short page. It does not
choose queries, split star or date ranges, or keep a watch list: the all-GitHub sweeps built on
it were removed in M11 (ADR-047.6; git tag `archive/global-collection`).

Search pages are person-level snapshots (items embed owner objects, ADR-037.8). Only the
project-level fields of `parse_search_page` survive (repo id, node id, name, counts, dates, the
owner *type*), and each page's raw bytes are dropped right after parsing (CB-24, ADR-038: hash and
URL kept, evidence `raw_dropped`, tombstone in `deletion_log`). A page that fails to parse is
dropped the same way at once and counted (`parse_failed`, CB-23b). A budget stop ends the query
cleanly with `budget_stop` set (the pages read so far are returned).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from pigtail.capture.db import CaptureDB
from pigtail.capture.runs import RunRecorder
from pigtail.connectors.base import FetchError
from pigtail.connectors.github import (
    SEARCH_MAX_RESULTS,
    GitHubConnector,
    SearchRepo,
    parse_search_page,
)
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.privacy.deletion import (
    PARSE_ERRORS,
    DeletionLog,
    drop_after_parse,
    drop_unparseable,
)

log = logging.getLogger("pigtail.capture.github")

PER_PAGE = 100


@dataclass
class SearchResult:
    total_count: int = 0
    pages: int = 0
    incomplete_pages: int = 0
    truncated: bool = False  # more than 1,000 results: only the first 1,000 are reachable
    failed_pages: int = 0
    parse_failed: int = 0
    raw_dropped: int = 0
    budget_stop: str | None = None
    items: list[SearchRepo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["items"] = len(self.items)
        return d


def search_repos(
    conn: GitHubConnector,
    db: CaptureDB,
    query: str,
    *,
    max_pages: int = SEARCH_MAX_RESULTS // PER_PAGE,
    sort: str = "stars",
    run: RunRecorder | None = None,
    evidence_url: str | None = None,
) -> SearchResult:
    """All reachable results of `query` (at most `max_pages` pages of 100), project-level.
    `evidence_url` stands in for the request URL in evidence (a query that must not be stored,
    ADR-076.6); the page number is appended."""
    if conn.evidence_sink is None:
        conn.evidence_sink = db.upsert_evidence
    dlog = DeletionLog(db, "retention", run_id=run.id if run else None)
    res = SearchResult()
    last = min(max_pages, SEARCH_MAX_RESULTS // PER_PAGE)
    page = 1
    try:
        while page <= last:
            try:
                f = conn.fetch_search_page(
                    query,
                    page=page,
                    per_page=PER_PAGE,
                    sort=sort,
                    evidence_url=f"{evidence_url}&page={page}" if evidence_url else None,
                )
            except FetchError as e:
                if not (e.status == 422 and page > 1):  # 422 past the end: not a failure
                    res.failed_pages += 1
                    log.warning("search page %d failed: %s", page, e.status)
                break
            ev = f.evidence
            try:
                sp = parse_search_page(f.data)
            except PARSE_ERRORS as e:  # CB-23b: useless and person-level, drop it now
                res.parse_failed += 1
                if drop_unparseable(db, conn.store, ev.id, f.content_hash, dlog,
                                    source=conn.name, error=e, run=run):  # fmt: skip
                    res.raw_dropped += 1
                break
            # CB-24: only project-level fields (and the owner type) survive parsing
            if drop_after_parse(db, conn.store, ev.id, f.content_hash, dlog):
                res.raw_dropped += 1
            res.pages += 1
            res.incomplete_pages += int(sp.incomplete_results)
            res.items.extend(sp.items)
            if page == 1:
                res.total_count = sp.total_count
                res.truncated = sp.total_count > SEARCH_MAX_RESULTS
                last = min(last, math.ceil(min(sp.total_count, SEARCH_MAX_RESULTS) / PER_PAGE))
            if len(sp.items) < PER_PAGE:
                break
            page += 1
    except BudgetExhausted as e:
        res.budget_stop = e.reason
        log.warning("search stopped by budget: %s", e.reason)
    if run is not None:
        for k, v in res.to_dict().items():
            if isinstance(v, int) and not isinstance(v, bool):
                run.incr(f"search.{k}", v)
        if res.budget_stop:
            run.incr(f"budget_stop.{res.budget_stop}")
    return res
