"""Registry download connectors for the exploratory adoption outcome (M23b; ADR-088.3, ADR-090).

Two free, official, unauthenticated sources, read per package, never in bulk:

- **npm** (`NpmDownloadsConnector`, TM-08, CLEARED): the public downloads API
  `GET https://api.npmjs.org/downloads/range/<start>:<end>/<package>` (daily counts, UTC days,
  history from 2015-01-10, at most ~18 months per request: longer ranges are clipped by the API,
  so `chunk_ranges` splits them at `NPM_MAX_RANGE_DAYS`), and the registry's latest manifest
  `GET https://registry.npmjs.org/<package>/latest`, read only for its `repository` field (the
  mapping check). The downloads answer is aggregate counts (snapshot kept, `project_level`); the
  manifest names maintainers and authors, so it is classed `person_level_24m` and the caller
  drops its raw bytes right after parsing (CB-24).
  npm reports a registry-wide outage day as 0 for every package (seen live: 2026-09-03 is 0 for
  `react` and for `left-pad`), so a zero can be a source gap: the caller reads a reference
  package over the same days and treats days on which it is 0 as gaps (`NPM_REFERENCE_PACKAGE`).
- **PyPI** (`PypiStatsConnector`, TM-35, CLEARED-WITH-CONDITIONS): pypistats.org, operated by the
  PSF, `GET https://pypistats.org/api/packages/<package>/overall?mirrors=false`. It keeps only
  ~180 days of daily history and ignores `start_date`/`end_date` (checked live 2026-09-29: the
  whole retained series comes back), so one request per package gives everything it has. Its
  etiquette asks for no bulk history downloads and at most one request per endpoint per day
  (the caller reuses a same-day snapshot, `pigtail.briefs.downloads.same_day_reuse`); the
  client limiter runs at one request every 20 s (`PYPISTATS_SECONDS_PER_REQUEST`; a burst of a few
  requests within a minute was refused live on 2026-09-29), and a 429 (which carries no
  `Retry-After`) backs off in half-minutes (`PYPISTATS_RETRY`). Days without downloads have no
  row. Data derive from the PyPI BigQuery tables (CC BY 4.0), so reports attribute
  "PyPI / Linehaul (PSF), CC BY 4.0, via pypistats.org".

BigQuery (TM-07) stays off: it is a paid service under the owner's USD 0 cap (H6).

Every request goes through the base class (token bucket with margin, retries with backoff on
429/5xx honouring `Retry-After`, contact User-Agent, cost events at USD 0, snapshot before parse).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, ClassVar
from urllib.parse import quote

from pigtail.capture.snapshots import SnapshotMeta
from pigtail.connectors.base import (
    Clearance,
    Connector,
    Fetched,
    FetchError,
    NotFound,
    Record,
    RetryPolicy,
    TermsMetadata,
)

NPM_DOWNLOADS_API = "https://api.npmjs.org/downloads/range"
NPM_REGISTRY = "https://registry.npmjs.org"
NPM_HISTORY_START = date(2015, 1, 10)  # the downloads API's first day
NPM_MAX_RANGE_DAYS = 540  # below the API's ~18-month clip (549 days seen live)
NPM_REFERENCE_PACKAGE = "react"  # a package downloaded every day: a 0 marks a source gap

PYPISTATS_API = "https://pypistats.org/api/packages"
PYPISTATS_REFERENCE_PACKAGE = "pip"  # a package downloaded every day: its range is coverage
PYPISTATS_SECONDS_PER_REQUEST = 20.0
PYPISTATS_RETRY = RetryPolicy(max_retries=4, base_delay=30.0, max_delay=600.0)

NPM_TERMS = TermsMetadata(
    terms_url="https://docs.npmjs.com/policies/open-source-terms",
    terms_basis="TM-08",
    clearance=Clearance.CLEARED,
    commercial_use=True,
    deletion_obligation=None,
    notes="Public downloads API and registry manifests; far below 5M requests a month; "
    "chunked ranges; descriptive User-Agent.",
)
PYPISTATS_TERMS = TermsMetadata(
    terms_url="https://pypistats.org/api/",
    terms_basis="TM-35",
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,
    deletion_obligation=None,
    notes="Shortlisted packages only, one request per package per day (same-day reuse), no "
    "bulk history; ~180 days retained; CC BY 4.0 attribution of the PyPI data.",
)


class DownloadsParseError(ValueError):
    pass


# --- npm -----------------------------------------------------------------------------------------
_NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")


def valid_npm_name(name: str) -> bool:
    """A name the registry could hold (lowercase, optional scope, ≤ 214 characters)."""
    return len(name) <= 214 and bool(_NPM_NAME.match(name))


def npm_path(name: str) -> str:
    """URL path segment of a package name: a scope's `/` stays literal (the APIs expect it)."""
    return quote(name, safe="@/")


def chunk_ranges(
    start: date, end: date, max_days: int = NPM_MAX_RANGE_DAYS
) -> list[tuple[date, date]]:
    """Inclusive `[start, end]` split into inclusive chunks of at most `max_days` days."""
    if end < start:
        return []
    out: list[tuple[date, date]] = []
    lo = start
    while lo <= end:
        hi = min(end, lo + timedelta(days=max_days - 1))
        out.append((lo, hi))
        lo = hi + timedelta(days=1)
    return out


@dataclass(frozen=True)
class DailySeries:
    """Daily downloads per UTC day as the source reported them."""

    package: str
    days: dict[date, int]


def parse_npm_range(data: bytes) -> DailySeries:
    try:
        doc = json.loads(data)
        if not isinstance(doc, dict) or "downloads" not in doc:
            raise DownloadsParseError("no downloads array")
        days: dict[date, int] = {}
        for row in doc["downloads"]:
            n = int(row["downloads"])
            if n < 0:
                raise DownloadsParseError("negative count")
            days[date.fromisoformat(str(row["day"]))] = n
        return DailySeries(str(doc.get("package", "")), days)
    except (ValueError, KeyError, TypeError) as e:
        raise DownloadsParseError(str(e)) from e


def parse_npm_latest(data: bytes) -> dict[str, Any]:
    """Only the fields the mapping check reads: name, repository (url and directory), and
    whether the version is deprecated. Everything else (maintainers, author, …) is ignored."""
    try:
        doc = json.loads(data)
    except ValueError as e:
        raise DownloadsParseError(str(e)) from e
    if not isinstance(doc, dict):
        raise DownloadsParseError("not an object")
    repo = doc.get("repository")
    url: str | None = None
    directory: str | None = None
    if isinstance(repo, str):
        url = repo
    elif isinstance(repo, dict):
        url = repo.get("url") if isinstance(repo.get("url"), str) else None
        directory = repo.get("directory") if isinstance(repo.get("directory"), str) else None
    return {"name": doc.get("name"), "repository_url": url, "repository_directory": directory}


class NpmDownloadsConnector(Connector):
    """npm downloads API and registry manifests (module docstring)."""

    name: ClassVar[str] = "npm_downloads"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = NPM_TERMS
    enabled_by_default: ClassVar[bool] = True
    rate_per_second: ClassVar[float] = 2.0
    safety_margin: ClassVar[float] = 0.5  # one request a second
    reliability = "high"
    retention_class = "project_level"  # daily counts; manifests override it per request
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(self, **kw: Any) -> None:
        kw.setdefault("pseudonymizer", None)
        super().__init__(**kw)

    def range_url(self, package: str, start: date, end: date) -> str:
        return f"{NPM_DOWNLOADS_API}/{start.isoformat()}:{end.isoformat()}/{npm_path(package)}"

    def downloads_range(
        self, package: str, start: date, end: date, *, repo_id: str | None = None
    ) -> Fetched | None:
        """One range request (the caller chunks); None when npm doesn't know the package."""
        try:
            return self.fetch(self.range_url(package, start, end), repo_id=repo_id)
        except NotFound:
            return None
        except FetchError as e:
            if e.status == 400:  # malformed name or range: nothing to read
                return None
            raise

    def latest_url(self, package: str) -> str:
        return f"{NPM_REGISTRY}/{npm_path(package)}/latest"

    def latest(self, package: str, *, repo_id: str | None = None) -> Fetched | None:
        """The latest version's manifest (person-level fields inside: the caller drops the raw
        bytes after `parse_npm_latest`); None when the package is not published."""
        try:
            return self.fetch(
                self.latest_url(package), repo_id=repo_id, retention_class="person_level_24m"
            )
        except NotFound:
            return None

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterable[Record]:
        s = parse_npm_range(data)
        return [
            {"package": s.package, "day": d.isoformat(), "downloads": n} for d, n in s.days.items()
        ]


# --- PyPI via pypistats.org ----------------------------------------------------------------------
def pep503(name: str) -> str:
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


_PYPI_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def valid_pypi_name(name: str) -> bool:
    return bool(_PYPI_NAME.match(pep503(name)))


def parse_pypistats_overall(data: bytes) -> DailySeries:
    """The `without_mirrors` series; a day without downloads has no row in the source."""
    try:
        doc = json.loads(data)
        if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
            raise DownloadsParseError("no data array")
        days: dict[date, int] = {}
        for row in doc["data"]:
            if row.get("category") != "without_mirrors":
                continue
            n = int(row["downloads"])
            if n < 0:
                raise DownloadsParseError("negative count")
            d = date.fromisoformat(str(row["date"]))
            days[d] = days.get(d, 0) + n
        return DailySeries(str(doc.get("package", "")), days)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise DownloadsParseError(str(e)) from e


class PypiStatsConnector(Connector):
    """pypistats.org JSON API, one package at a time (module docstring; TM-35)."""

    name: ClassVar[str] = "pypistats"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = PYPISTATS_TERMS
    enabled_by_default: ClassVar[bool] = True
    rate_per_second: ClassVar[float] = 0.1
    safety_margin: ClassVar[float] = 0.5  # one request every 20 s: the site runs on few resources
    reliability = "high"
    retention_class = "project_level"  # aggregate daily counts
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(self, **kw: Any) -> None:
        kw.setdefault("pseudonymizer", None)
        # the site's IP-based limit is undocumented and its 429 carries no Retry-After (seen
        # live 2026-09-29): back off in half-minutes, not seconds
        kw.setdefault("retry", PYPISTATS_RETRY)
        super().__init__(**kw)

    def overall_url(self, package: str) -> str:
        return f"{PYPISTATS_API}/{quote(pep503(package), safe='')}/overall?mirrors=false"

    def overall(self, package: str, *, repo_id: str | None = None) -> Fetched | None:
        """The retained daily series of one package; None when the source doesn't know it."""
        try:
            return self.fetch(self.overall_url(package), repo_id=repo_id)
        except NotFound:
            return None

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterable[Record]:
        s = parse_pypistats_overall(data)
        return [
            {"package": s.package, "day": d.isoformat(), "downloads": n} for d, n in s.days.items()
        ]
