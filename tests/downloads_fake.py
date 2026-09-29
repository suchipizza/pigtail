"""In-process fakes of the GitHub contents API, the npm downloads API and registry, and
pypistats.org for the M23b download tests. No network; synthetic repos (`org-x/…`), packages
(`fake-…`) and counts only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import TokenBucket
from pigtail.connectors.downloads import (
    NPM_REFERENCE_PACKAGE,
    PYPISTATS_REFERENCE_PACKAGE,
    NpmDownloadsConnector,
    PypiStatsConnector,
)
from pigtail.connectors.github import GitHubConnector, MemoryCache
from pigtail.connectors.github_budget import Budget, JobCaps

NPM_GAP_DAY = date(2026, 8, 20)  # a registry-wide outage day: 0 for every package
PYPI_FIRST = date(2026, 4, 1)  # the fake pypistats retention start


def fast() -> TokenBucket:
    return TokenBucket(1000, 5, 0.0, sleep=lambda _s: None)


@dataclass
class FakeRegistries:
    # GitHub: repo -> path -> file text (a path ending in "/" lists its subdirs)
    files: dict[str, dict[str, str]] = field(default_factory=dict)
    dirs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # npm: package -> latest manifest; package -> per-day count function
    npm_latest: dict[str, dict[str, Any]] = field(default_factory=dict)
    npm_daily: dict[str, Callable[[date], int]] = field(default_factory=dict)
    # pypistats: package -> {day: count} (days without downloads left out)
    pypi: dict[str, dict[date, int]] = field(default_factory=dict)
    pypi_status: int = 200
    log: list[str] = field(default_factory=list)
    script: list[httpx.Response] = field(default_factory=list)  # served first, in order

    def __post_init__(self) -> None:
        self.npm_daily.setdefault(
            NPM_REFERENCE_PACKAGE, lambda d: 0 if d == NPM_GAP_DAY else 1_000_000
        )
        ref: dict[date, int] = {}
        d = PYPI_FIRST
        while d <= date(2026, 9, 28):
            ref[d] = 5_000_000
            d += timedelta(days=1)
        self.pypi.setdefault(PYPISTATS_REFERENCE_PACKAGE, ref)

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = req.url
        self.log.append(f"{url.host}{url.raw_path.decode()}")
        if self.script:
            return self.script.pop(0)
        path = unquote(url.path)
        if url.host == "api.github.com":
            return self._github(req, path)
        if url.host == "registry.npmjs.org":
            name = path.removeprefix("/").removesuffix("/latest")
            doc = self.npm_latest.get(name)
            if doc is None:
                return httpx.Response(404, json={"error": "Not found"})
            return httpx.Response(200, json=doc)
        if url.host == "api.npmjs.org":
            rest = path.removeprefix("/downloads/range/")
            span, _, name = rest.partition("/")
            a, _, b = span.partition(":")
            fn = self.npm_daily.get(name)
            if fn is None:
                return httpx.Response(404, json={"error": f"package {name} not found"})
            lo, hi = date.fromisoformat(a), date.fromisoformat(b)
            rows = []
            d = lo
            while d <= hi:
                rows.append({"downloads": fn(d), "day": d.isoformat()})
                d += timedelta(days=1)
            return httpx.Response(
                200, json={"start": a, "end": b, "package": name, "downloads": rows}
            )
        if url.host == "pypistats.org":
            if self.pypi_status != 200:
                return httpx.Response(self.pypi_status, text="429 RATE LIMIT EXCEEDED")
            name = path.removeprefix("/api/packages/").removesuffix("/overall")
            series = self.pypi.get(name)
            if series is None:
                return httpx.Response(404, text="Not Found")
            data = []
            for d, n in sorted(series.items()):
                data.append({"category": "with_mirrors", "date": d.isoformat(), "downloads": n + 3})
                data.append({"category": "without_mirrors", "date": d.isoformat(), "downloads": n})
            return httpx.Response(
                200, json={"data": data, "package": name, "type": "overall_downloads"}
            )
        return httpx.Response(404)

    def _github(self, req: httpx.Request, path: str) -> httpx.Response:
        parts = path.split("/")  # /repos/o/n/contents/<path>
        repo = f"{parts[2]}/{parts[3]}"
        fpath = "/".join(parts[5:])
        if fpath in self.dirs.get(repo, {}):
            items = [
                {"name": s, "path": f"{fpath}/{s}", "type": "dir"} for s in self.dirs[repo][fpath]
            ] + [{"name": "README.md", "path": f"{fpath}/README.md", "type": "file"}]
            return httpx.Response(200, json=items, headers={"X-RateLimit-Remaining": "4999"})
        text = self.files.get(repo, {}).get(fpath)
        if text is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, text=text, headers={"X-RateLimit-Remaining": "4999"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def github(self, store: LocalSnapshotStore, sink: Any = None) -> GitHubConnector:
        return GitHubConnector(
            store=store,
            http=self.client(),
            token="fake-token-for-tests",
            evidence_sink=sink,
            cache=MemoryCache(),
            budget=Budget(job=JobCaps({}), sleep=lambda _s: None),
            limiters={r: fast() for r in ("core", "graphql", "search")},
            limiter=fast(),
            sleep=lambda _s: None,
            env={},
        )

    def npm(self, store: LocalSnapshotStore, sink: Any = None) -> NpmDownloadsConnector:
        return NpmDownloadsConnector(
            store=store, http=self.client(), evidence_sink=sink, limiter=fast(),
            sleep=lambda _s: None, env={},
        )  # fmt: skip

    def pypistats(self, store: LocalSnapshotStore, sink: Any = None) -> PypiStatsConnector:
        return PypiStatsConnector(
            store=store, http=self.client(), evidence_sink=sink, limiter=fast(),
            sleep=lambda _s: None, env={},
        )  # fmt: skip


def package_json(name: str | None, *, private: bool = False, workspaces: Any = None) -> str:
    doc: dict[str, Any] = {"version": "1.0.0", "author": "npmuser002 <fake@example.org>"}
    if name is not None:
        doc["name"] = name
    if private:
        doc["private"] = True
    if workspaces is not None:
        doc["workspaces"] = workspaces
    return json.dumps(doc)


def npm_latest(name: str, repo_url: str | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "maintainers": [{"name": "npmuser001", "email": "fake@example.org"}],
    }
    if repo_url is not None:
        doc["repository"] = {"type": "git", "url": repo_url}
    return doc
