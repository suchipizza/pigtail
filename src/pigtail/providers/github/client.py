"""GitHub adapter: repository metadata, releases, contributors and star history.

Partial or rate-limited data is reported as such; star history always declares its quality.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from pigtail.domain.time import parse_dt
from pigtail.logging import get_logger
from pigtail.providers.base import Meter

log = get_logger("github")

API = "https://api.github.com"
STAR_HISTORY_PER_PAGE = 30  # weeks per page (endpoint maximum)
STAR_HISTORY_MAX_PAGE = 100
USER_AGENT = "pigtail/0.1 (+https://github.com/suchipizza/pigtail)"


@dataclass
class RepositorySnapshot:
    owner: str
    name: str
    full_name: str
    url: str
    api_url: str
    external_id: str
    description: str | None
    homepage: str | None
    created_at: str | None
    pushed_at: str | None
    default_branch: str | None
    language: str | None
    license: str | None
    topics: list[str]
    is_archived: bool
    is_fork: bool
    stars: int
    forks: int
    watchers: int
    open_issues: int
    owner_type: str | None
    contributors: int | None = None
    raw_etag: str | None = None


@dataclass
class Release:
    tag: str
    name: str | None
    published_at: str | None
    url: str
    prerelease: bool
    body_excerpt: str | None
    api_path: str


@dataclass
class StarHistory:
    quality: str  # exact | sampled | reconstructed | unavailable
    # (day, cumulative stars) points. For exact history: every day with new stars.
    points: list[tuple[date, int]] = field(default_factory=list)
    total_now: int = 0
    covered_stars: int = 0
    notes: list[str] = field(default_factory=list)
    method: str = ""
    requests: int = 0


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(self, token: str | None, meter: Meter, timeout: int = 30):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.token = token
        self.http = httpx.AsyncClient(base_url=API, headers=headers, timeout=timeout, follow_redirects=True)
        self.meter = meter
        self.sem = asyncio.Semaphore(8)
        self.rate_limited = False

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        for attempt in range(3):
            async with self.sem:
                self.meter.github_api_requests += 1
                self.meter.http_requests += 1
                try:
                    r = await self.http.get(path, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    self.meter.retries += 1
                    if attempt == 2:
                        raise GitHubError(0, f"network error: {type(exc).__name__}") from exc
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
            if r.status_code in (403, 429) and (
                r.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in r.text.lower()
            ):
                reset = int(r.headers.get("x-ratelimit-reset", "0") or 0)
                wait = reset - int(datetime.now(UTC).timestamp())
                retry_after = int(r.headers.get("retry-after", "0") or 0)
                wait = retry_after or wait
                if 0 < wait <= 60 and attempt < 2:
                    self.meter.retries += 1
                    await asyncio.sleep(wait + 1)
                    continue
                self.rate_limited = True
                raise GitHubError(r.status_code, "GitHub API rate limit reached")
            if r.status_code >= 500 and attempt < 2:
                self.meter.retries += 1
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise GitHubError(r.status_code, f"GitHub API {r.status_code} for {path}")
            return r
        raise GitHubError(0, f"GitHub API failed for {path}")

    async def repository(self, owner: str, repo: str) -> RepositorySnapshot:
        r = await self._get(f"/repos/{owner}/{repo}")
        d = r.json()
        lic = (d.get("license") or {}).get("spdx_id")
        snap = RepositorySnapshot(
            owner=d["owner"]["login"],
            name=d["name"],
            full_name=d["full_name"],
            url=d["html_url"],
            api_url=f"{API}/repos/{d['full_name']}",
            external_id=str(d["id"]),
            description=d.get("description"),
            homepage=(d.get("homepage") or None),
            created_at=d.get("created_at"),
            pushed_at=d.get("pushed_at"),
            default_branch=d.get("default_branch"),
            language=d.get("language"),
            license=None if lic in (None, "NOASSERTION") else lic,
            topics=d.get("topics") or [],
            is_archived=bool(d.get("archived")),
            is_fork=bool(d.get("fork")),
            stars=int(d.get("stargazers_count") or 0),
            forks=int(d.get("forks_count") or 0),
            watchers=int(d.get("subscribers_count") or 0),
            open_issues=int(d.get("open_issues_count") or 0),
            owner_type=(d.get("owner") or {}).get("type"),
            raw_etag=r.headers.get("etag"),
        )
        try:
            snap.contributors = await self.contributors_count(snap.owner, snap.name)
        except GitHubError:
            snap.contributors = None
        return snap

    async def contributors_count(self, owner: str, repo: str) -> int | None:
        r = await self._get(f"/repos/{owner}/{repo}/contributors", params={"per_page": 1, "anon": "true"})
        link = r.headers.get("link", "")
        m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
        if m:
            return int(m.group(1))
        return len(r.json()) if r.status_code == 200 else None

    async def readme(self, owner: str, repo: str) -> tuple[str, str] | None:
        try:
            r = await self._get(f"/repos/{owner}/{repo}/readme", headers={"Accept": "application/vnd.github.raw+json"})
        except GitHubError:
            return None
        meta_url = f"https://github.com/{owner}/{repo}#readme"
        return r.text, meta_url

    async def releases(self, owner: str, repo: str, max_pages: int = 5) -> list[Release]:
        out: list[Release] = []
        for page in range(1, max_pages + 1):
            try:
                r = await self._get(f"/repos/{owner}/{repo}/releases", params={"per_page": 100, "page": page})
            except GitHubError as exc:
                if exc.status == 404:
                    break
                raise
            items = r.json()
            for i, d in enumerate(items):
                if d.get("draft"):
                    continue
                out.append(
                    Release(
                        tag=d.get("tag_name") or "",
                        name=d.get("name") or None,
                        published_at=d.get("published_at") or d.get("created_at"),
                        url=d.get("html_url") or "",
                        prerelease=bool(d.get("prerelease")),
                        body_excerpt=(d.get("body") or "")[:1500] or None,
                        api_path=f"$[{(page - 1) * 100 + i}]",
                    )
                )
            if len(items) < 100:
                break
        return out

    async def tags_count(self, owner: str, repo: str) -> int | None:
        try:
            r = await self._get(f"/repos/{owner}/{repo}/tags", params={"per_page": 1})
        except GitHubError:
            return None
        m = re.search(r'[?&]page=(\d+)>; rel="last"', r.headers.get("link", ""))
        return int(m.group(1)) if m else len(r.json())

    # ------------------------------------------------------------------ stars
    async def star_history(self, owner: str, repo: str, total: int, created_at: str | None) -> StarHistory:
        """Daily star counts from `GET /repos/{o}/{r}/stargazers/history`.

        GitHub closed stargazer *lists* to non-collaborators on 2026-06-30. The history endpoint returns
        weekly buckets (most recent first) with per-day net counts of current stargazers and no identities.
        """
        created = parse_dt(created_at)
        if total == 0:
            return StarHistory(
                quality="exact", points=[(created.date(), 0)] if created else [], total_now=0, method="no stars"
            )
        before = self.meter.github_api_requests
        counts: dict[date, int] = {}
        try:
            for page in range(1, STAR_HISTORY_MAX_PAGE + 1):
                r = await self._get(
                    f"/repos/{owner}/{repo}/stargazers/history",
                    params={"per_page": STAR_HISTORY_PER_PAGE, "page": page},
                )
                weeks = r.json()
                if not isinstance(weeks, list) or not weeks:
                    break
                oldest = None
                for w in weeks:
                    start = datetime.fromtimestamp(int(w["week"]), tz=UTC).date()
                    oldest = start
                    for i, n in enumerate(w.get("days") or []):
                        if n:
                            d = start + timedelta(days=i)
                            counts[d] = counts.get(d, 0) + int(n)
                if len(weeks) < STAR_HISTORY_PER_PAGE or (
                    created and oldest and oldest < created.date() - timedelta(days=7)
                ):
                    break
        except GitHubError as exc:
            return StarHistory(
                quality="unavailable",
                total_now=total,
                notes=[f"Star history could not be retrieved: {exc}"],
                requests=self.meter.github_api_requests - before,
            )
        cum, pts = 0, []
        for d in sorted(counts):
            cum += counts[d]
            pts.append((d, cum))
        notes = [
            "Daily counts come from GitHub's star-history endpoint, which counts current stargazers by the day "
            "they starred. People who later removed their star are not counted, and GitHub's day boundaries "
            "may not align exactly with UTC."
        ]
        quality = "exact"
        if pts and abs(pts[-1][1] - total) > max(5, total * 0.01):
            notes.append(f"GitHub reports {total:,} stars now; the daily history sums to {pts[-1][1]:,}.")
        if not pts:
            return StarHistory(
                quality="unavailable",
                total_now=total,
                notes=["GitHub returned no star history."],
                requests=self.meter.github_api_requests - before,
            )
        return StarHistory(
            quality=quality,
            points=pts,
            total_now=total,
            covered_stars=pts[-1][1],
            notes=notes,
            method="GitHub stargazers/history endpoint (daily net counts)",
            requests=self.meter.github_api_requests - before,
        )
