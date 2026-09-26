"""Synthetic neighbourhood for M22 discovery tests (no network): a GitHub fake with keyword and
topic search, READMEs and GraphQL metadata, and a Show HN (Algolia) fake. Every org, repo and
user name is made up (`org-s`, `org-x`, `org-y`, `org-z`, users `ghuser0NN`, HN `hnuser7NN`),
built around the synthetic example brief (docs/examples/brief-example.yaml).

The fakes deliberately return person-level fields the product must drop: search items carry
owner logins, the Algolia fake ignores `attributesToRetrieve` and returns `author`, `_tags` and
story text, and READMEs quote handles, profile URLs and e-mail addresses.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx

from tests.github_fake import FakeGitHub, _parse


def ts(s: str) -> str:
    return f"{s}T12:00:00Z"


# name, description, topics, stars, created, owner type, README
REPOS: list[tuple[str, str, list[str], int, str, str, str | None]] = [
    (
        "org-s/yaml-guard",
        "Fast YAML linter CLI for CI pipelines",
        ["yaml", "linter", "cli"],
        800,
        "2025-06-01",
        "Organization",
        "# yaml-guard\n[![ci](https://img.shields.io/x.svg)](https://ci.example)\nA YAML linter "
        "for config files. Maintained by @ghuser001, see https://github.com/ghuser001 .",
    ),
    (
        "ghuser042/tomlcheck",
        "TOML config linter by ghuser042",
        ["toml", "linter"],
        150,
        "2025-09-10",
        "User",
        "tomlcheck validates TOML config files. Built by ghuser042 (ghuser042@example.com).",
    ),
    (
        "org-s/json-schema-cli",
        "Validate JSON and YAML config files against a JSON schema",
        ["json-schema", "cli"],
        400,
        "2025-11-20",
        "Organization",
        None,
    ),
    (
        "org-s/web-framework-x",
        "A web framework with a CLI",
        ["cli", "web"],
        2000,
        "2025-04-02",
        "Organization",
        "A web framework.",
    ),
    (
        "org-s/maybe-config",
        "Config tooling experiments, maybe a linter",
        ["yaml"],
        40,
        "2026-01-05",
        "Organization",
        "Experiments.",
    ),
    (
        "org-y/old-linter",
        "YAML linter CLI",
        ["yaml", "linter"],
        5000,
        "2023-02-01",
        "Organization",
        None,
    ),
    (
        "org-y/tiny-lint",
        "YAML linter CLI",
        ["yaml", "linter"],
        3,
        "2025-08-01",
        "Organization",
        None,
    ),
    (
        "org-z/confcheck",
        "Check config files before CI",
        ["config"],
        300,
        "2025-12-01",
        "Organization",
        "confcheck: config validation.",
    ),
    (
        "org-z/linkedtool",
        "TOML/YAML config formatter and checker",
        ["toml"],
        60,
        "2026-02-01",
        "Organization",
        "Formats and checks config.",
    ),
    ("org-z/ancient", "An old config tool", ["yaml"], 900, "2020-01-01", "Organization", None),
    (
        "org-z/schema-checker",
        "Schema checker for configs",
        ["json-schema"],
        250,
        "2025-07-07",
        "Organization",
        "Checks schemas.",
    ),
    (
        "org-z/showcase-site",
        "Website of the launch showcase",
        [],
        30,
        "2025-05-05",
        "Organization",
        None,
    ),
    (
        "org-z/showcase-engine",
        "Engine of the launch showcase",
        [],
        700,
        "2025-05-06",
        "Organization",
        None,
    ),
    (
        "org-x/awesome-config",
        "A curated list of config tools",
        ["awesome-list", "yaml"],
        3000,
        "2019-03-03",
        "Organization",
        "# Awesome config\n- [yaml-guard](https://github.com/org-s/yaml-guard)\n"
        "- [linkedtool](https://github.com/org-z/linkedtool) by @ghuser009\n"
        "- [ancient](https://github.com/org-z/ancient)\n",
    ),
]

HN_HITS: list[dict[str, Any]] = [
    {
        "objectID": "7001",
        "title": "Show HN: Confcheck, validate config files",
        "points": 120,
        "url": "https://github.com/org-z/confcheck",
        "created_at_i": 1765000000,
        "author": "hnuser701",
        "_tags": ["story", "author_hnuser701", "show_hn"],
        "story_text": "I (hnuser701) built this.",
    },
    {
        "objectID": "7002",
        "title": "Show HN: Schema Checker, an example tool",
        "points": 80,
        "url": "https://github.com/org-z/schema-checker",
        "created_at_i": 1752000000,
        "author": "hnuser702",
        "_tags": ["story", "author_hnuser702", "show_hn"],
    },
    {
        "objectID": "7003",
        "title": "Show HN: Launch Showcase (site)",
        "points": 300,
        "url": "https://github.com/org-z/showcase-site",
        "created_at_i": 1746500000,
        "author": "hnuser703",
        "_tags": ["story", "author_hnuser703", "show_hn"],
    },
    {
        "objectID": "7004",
        "title": "Show HN: Launch Showcase engine",
        "points": 500,
        "url": "https://github.com/org-z/showcase-engine",
        "created_at_i": 1746600000,
        "author": "hnuser704",
        "_tags": ["story", "author_hnuser704", "show_hn"],
    },
    {
        "objectID": "7005",
        "title": "Show HN: My blog about config",
        "points": 10,
        "url": "https://example.org/blog",
        "created_at_i": 1760000000,
        "author": "hnuser705",
        "_tags": ["story", "author_hnuser705", "show_hn"],
    },
]


def pool() -> list[dict[str, Any]]:
    out = []
    for i, (name, desc, topics, stars, created, otype, readme) in enumerate(REPOS):
        out.append(
            {
                "id": 9_100_000 + i,
                "node_id": f"R_fake_d{i}",
                "full_name": name,
                "description": desc,
                "topics": topics,
                "stars": stars,
                "forks": 1,
                "created_at": ts(created),
                "pushed_at": ts("2026-09-01"),
                "owner_type": otype,
                "readme": readme,
                "language": "Go",
            }
        )
    return out


_QUAL = re.compile(r"^[a-z]+:")


class DiscoveryFakeGitHub(FakeGitHub):
    """Search matches every free word (AND) in name, description and topics; `topic:` filters
    by topic; created and star ranges as in the base fake."""

    def __init__(self) -> None:
        super().__init__()
        self.search_pool = pool()
        self.readme_requests: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/readme", req.url.path)
        if m is not None and not self.script:
            self.requests.append(req)
            return self._readme(req, m.group(1))
        return super().__call__(req)

    def _readme(self, req: httpx.Request, full: str) -> httpx.Response:
        assert "raw" in req.headers.get("Accept", "")
        self.readme_requests.append(full)
        r = self.by_name(full)
        if r is None or not r.get("readme"):
            return httpx.Response(404, json={"message": "Not Found"}, headers=self._headers("core"))
        self.remaining["core"] -= 1
        return httpx.Response(
            200,
            content=r["readme"].encode(),
            headers={**self._headers("core"), "Content-Type": "text/plain"},
        )

    def _node(self, r: dict[str, Any]) -> dict[str, Any]:
        n = super()._node(r)
        n.update(
            {
                "createdAt": r.get("created_at"),
                "description": r.get("description"),
                "isArchived": False,
                "isFork": False,
                "primaryLanguage": {"name": r.get("language") or "Go"},
                "repositoryTopics": {
                    "nodes": [{"topic": {"name": t}} for t in r.get("topics", [])]
                },
                "owner": {"__typename": r.get("owner_type", "Organization")},
            }
        )
        return n

    def _search(self, req: httpx.Request) -> httpx.Response:
        p = req.url.params
        q = p["q"]
        page, per_page = int(p.get("page", "1")), int(p.get("per_page", "30"))
        m = re.search(r"created:(\S+)\.\.(\S+)", q)
        s = re.search(r"stars:(\d+)\.\.(\S+)", q)
        assert m and s, q
        lo_t, hi_t = _parse(m.group(1)), _parse(m.group(2))
        lo_s = int(s.group(1))
        topics = re.findall(r"topic:(\S+)", q)
        words = [w.lower() for w in q.split() if not _QUAL.match(w)]
        hits = []
        for r in self.search_pool:
            text = " ".join([r["full_name"], r.get("description") or "", *r.get("topics", [])])
            if not lo_t <= _parse(r["created_at"]) <= hi_t or r["stars"] < lo_s:
                continue
            if any(t not in r.get("topics", []) for t in topics):
                continue
            if any(w not in text.lower() for w in words):
                continue
            hits.append(r)
        hits.sort(key=lambda r: (-r["stars"], r["id"]))
        self.search_queries.append((q, page, len(hits)))
        items = [
            {
                "id": r["id"],
                "node_id": r["node_id"],
                "full_name": r["full_name"],
                "description": r.get("description"),
                "topics": r.get("topics", []),
                "language": r.get("language"),
                "stargazers_count": r["stars"],
                "forks_count": r.get("forks", 0),
                "created_at": r["created_at"],
                "pushed_at": r["pushed_at"],
                "owner": {"login": r["full_name"].split("/")[0], "type": r.get("owner_type")},
                "archived": False,
                "fork": False,
            }
            for r in hits[(page - 1) * per_page : page * per_page]
        ]
        return self._json(
            req, "search", {"total_count": len(hits), "incomplete_results": False, "items": items}
        )


class FakeShowHN:
    """HN Algolia `/search` with `tags=show_hn` or `tags=launch_hn` (the hit's `_tags` must hold the
    tag): a hit matches when any query word (4+ letters) is in its title, or, for a query that
    names `github.com`, when the query's path is in its URL (case-insensitive), within the
    numeric time filter. It returns person-level fields on purpose."""

    def __init__(self, hits: list[dict[str, Any]] | None = None) -> None:
        self.hits = list(HN_HITS if hits is None else hits)
        self.requests: list[httpx.Request] = []
        self.fail_after: int | None = None  # answer 500 once this many requests were served

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def _matches(self, h: dict[str, Any], query: str) -> bool:
        if "github.com" in query.lower():
            path = query.lower().split("github.com", 1)[1]
            return path in str(h.get("url") or "").lower()
        words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", query)]
        return any(w in h["title"].lower() for w in words)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.fail_after is not None and len(self.requests) >= self.fail_after:
            return httpx.Response(500, json={"message": "unavailable"})
        self.requests.append(req)
        assert req.url.host == "hn.algolia.com" and req.url.path == "/api/v1/search", req.url
        p = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        assert p["tags"] in ("show_hn", "launch_hn")
        assert "author" not in p.get("attributesToRetrieve", "")
        lo, hi = (int(x) for x in re.findall(r"created_at_i[<>]=?(\d+)", p["numericFilters"]))
        hits = [
            h
            for h in self.hits
            if lo <= h["created_at_i"] < hi
            and p["tags"] in h.get("_tags", ["story", "show_hn"])
            and self._matches(h, p.get("query", ""))
        ][: int(p.get("hitsPerPage", "20"))]
        body = {"hits": hits, "nbHits": len(hits), "nbPages": 1, "page": 0}
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )


RUN_NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
