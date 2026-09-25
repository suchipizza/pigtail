"""In-process fake of the GitHub REST + GraphQL APIs for M1-T24 tests. No network.

Serves the synthetic repos in tests/fixtures/github/repos.json (fake orgs `org-x`, `org-y`, fake
ids 7000001+), generated search pools (`org-s/search-NNNN`), star-history series and per-repo
events with fake logins (`ghuserNNN`, `helper-app[bot]`). Emulates rate-limit headers per
resource, ETag/304, `X-Poll-Interval`, the 1,000-result search cap, and GraphQL aliases with a
`rateLimit` block. `script` holds canned responses served before normal routing (for rate-limit
tests).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

FIX = Path(__file__).parent / "fixtures" / "github"
NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)  # 11:00 in US Pacific: endpoint day 2026-09-25
TODAY = date(2026, 9, 25)
TOKEN = "fake-token-for-tests"  # not a real token format


def load_repos() -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = json.loads((FIX / "repos.json").read_text())["repos"]
    return repos


def series(kind: str, days: int = 70) -> list[int]:
    """Daily net stars, oldest first, ending on TODAY (TODAY is the last element)."""
    base = [3, 5, 7, 4, 6, 5, 5]
    if kind == "burst":  # ~5/day, then a breakout over the last three endpoint days
        return [base[i % 7] for i in range(days - 3)] + [150, 160, 40]
    if kind == "flat_busy":  # ~50/day, no change
        return [50 + (i % 3) - 1 for i in range(days)]
    if kind == "new_burst":  # created 5 days ago
        return [10, 20, 30, 100, 100]
    if kind == "quiet":
        return [1] * days
    if kind == "fake_jump":  # counts jump but star-history shows nothing (e.g. a glitch)
        return [2] * days
    raise ValueError(kind)


def week_start(d: date) -> date:
    """Sunday on or before `d` (the fake's week convention)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def make_events(
    n: int,
    *,
    repo_id: int,
    name: str,
    newest: datetime,
    first_id: int = 1,
    step_s: int = 60,
    types: tuple[str, ...] = ("WatchEvent",),
    login: Callable[[int], str] | None = None,
) -> list[dict[str, Any]]:
    """`n` synthetic events, newest first."""
    out = []
    for k in range(n):
        eid = first_id + n - 1 - k
        out.append(
            {
                "id": str(eid),
                "type": types[eid % len(types)],
                "actor": {
                    "id": 60_000_000 + eid,
                    "login": (login or (lambda i: f"ghuser{i:03d}"))(eid),
                },
                "repo": {"id": repo_id, "name": name},
                "payload": {"action": "started"},
                "public": True,
                "created_at": (newest - timedelta(seconds=step_s * k)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
        )
    return out


class FakeGitHub:
    def __init__(self, now: Callable[[], datetime] = lambda: NOW) -> None:
        self.now = now
        self.repos: dict[int, dict[str, Any]] = {r["id"]: dict(r) for r in load_repos()}
        self.daily: dict[int, list[int]] = {
            r["id"]: series(r["series"]) for r in self.repos.values()
        }
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.search_pool: list[dict[str, Any]] = []
        self.script: list[Any] = []
        self.requests: list[httpx.Request] = []
        self.limit = {"core": 5000, "graphql": 5000, "search": 30}
        self.remaining = dict(self.limit)
        self.graphql_cost = 1
        self.poll_interval = 60
        self.label = "iso"  # star-history week label format: iso | iso_offset | unix
        self.search_queries: list[tuple[str, int, int]] = []  # (q, page, total)
        self.fail_graphql_over: int | None = None  # 502 for batches larger than this

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def by_name(self, full_name: str) -> dict[str, Any] | None:
        for r in [*self.repos.values(), *self.search_pool]:
            if r["full_name"].lower() == full_name.lower():
                return r
        return None

    # --- plumbing -------------------------------------------------------------------------------
    def _headers(self, resource: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        reset = int((self.now() + timedelta(hours=1)).timestamp())
        return {
            "X-RateLimit-Limit": str(self.limit[resource]),
            "X-RateLimit-Remaining": str(self.remaining[resource]),
            "X-RateLimit-Used": str(self.limit[resource] - self.remaining[resource]),
            "X-RateLimit-Reset": str(reset),
            "X-RateLimit-Resource": resource,
            "Content-Type": "application/json; charset=utf-8",
            **(extra or {}),
        }

    def _json(self, req: httpx.Request, resource: str, body: Any, cost: int = 1) -> httpx.Response:
        data = json.dumps(body, sort_keys=True).encode()
        etag = '"' + hashlib.sha1(data).hexdigest() + '"'
        extra = {"ETag": etag}
        if resource == "core" and "/events" in req.url.path:
            extra["X-Poll-Interval"] = str(self.poll_interval)
        if req.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers=self._headers(resource, extra))
        if resource != "search":  # search is a per-minute bucket; the fake never exhausts it
            self.remaining[resource] = max(0, self.remaining[resource] - cost)
        return httpx.Response(200, content=data, headers=self._headers(resource, extra))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.url.host == "api.github.com", req.url
        if self.script:
            item = self.script.pop(0)
            resp = item(req) if callable(item) else item
            if resp is not None:
                return resp
        path = req.url.path
        if path == "/graphql":
            return self._graphql(req)
        if path == "/search/repositories":
            return self._search(req)
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/stargazers/history", path)
        if m:
            return self._history(req, m.group(1))
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/events", path)
        if m:
            return self._events(req, m.group(1))
        return httpx.Response(404, json={"message": "Not Found"})

    # --- GraphQL --------------------------------------------------------------------------------
    def _node(self, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r.get("node_id"),
            "databaseId": r["id"],
            "nameWithOwner": r["full_name"],
            "stargazerCount": r["stars"],
            "forkCount": r.get("forks", 0),
            "pushedAt": "2026-09-25T10:00:00Z",
            "createdAt": r.get("created_at"),
            "isPrivate": bool(r.get("private")),
            "owner": {"__typename": r.get("owner_type", "Organization")},
        }

    def _graphql(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        q, v = body["query"], body.get("variables") or {}
        aliases = re.findall(r"(r(\d+)): (node|repository)\(", q)
        if self.fail_graphql_over is not None and len(aliases) > self.fail_graphql_over:
            return httpx.Response(502, headers=self._headers("graphql"))
        data: dict[str, Any] = {}
        errors = []
        for alias, i, kind in aliases:
            r = None
            if kind == "node":
                nid = v[f"i{i}"]
                r = next(
                    (
                        x
                        for x in [*self.repos.values(), *self.search_pool]
                        if x.get("node_id") == nid
                    ),
                    None,
                )
            else:
                r = self.by_name(f"{v[f'o{i}']}/{v[f'n{i}']}")
            if r is None:
                data[alias] = None
                errors.append({"type": "NOT_FOUND", "path": [alias], "message": "not found"})
            else:
                data[alias] = self._node(r)
        cost = self.graphql_cost
        self.remaining["graphql"] = max(0, self.remaining["graphql"] - cost)
        data["rateLimit"] = {
            "cost": cost,
            "limit": self.limit["graphql"],
            "remaining": self.remaining["graphql"],
            "resetAt": (self.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "used": self.limit["graphql"] - self.remaining["graphql"],
        }
        out: dict[str, Any] = {"data": data}
        if errors:
            out["errors"] = errors
        return httpx.Response(200, json=out, headers=self._headers("graphql"))

    # --- search ---------------------------------------------------------------------------------
    def _search(self, req: httpx.Request) -> httpx.Response:
        p = req.url.params
        q = p["q"]
        page, per_page = int(p.get("page", "1")), int(p.get("per_page", "30"))
        m = re.search(r"(created|pushed):(\S+)\.\.(\S+)", q)
        s = re.search(r"stars:(\d+)\.\.(\S+)", q)
        assert m and s, q
        field, lo_t, hi_t = m.group(1), _parse(m.group(2)), _parse(m.group(3))
        lo_s = int(s.group(1))
        hi_s = None if s.group(2) == "*" else int(s.group(2))
        hits = [
            r
            for r in self.search_pool
            if lo_t <= _parse(r[f"{field}_at"]) <= hi_t
            and r["stars"] >= lo_s
            and (hi_s is None or r["stars"] <= hi_s)
        ]
        hits.sort(key=lambda r: (-r["stars"], r["id"]))
        self.search_queries.append((q, page, len(hits)))
        if page * per_page > 1000:
            return httpx.Response(
                422,
                json={"message": "Only the first 1000 search results"},
                headers=self._headers("search"),
            )
        items = [
            {
                "id": r["id"],
                "node_id": r["node_id"],
                "full_name": r["full_name"],
                "stargazers_count": r["stars"],
                "forks_count": r.get("forks", 0),
                "created_at": r["created_at"],
                "pushed_at": r["pushed_at"],
                "owner": {"login": r["full_name"].split("/")[0], "type": "Organization"},
                "archived": False,
                "fork": False,
            }
            for r in hits[(page - 1) * per_page : page * per_page]
        ]
        body = {"total_count": len(hits), "incomplete_results": False, "items": items}
        return self._json(req, "search", body)

    # --- star history ---------------------------------------------------------------------------
    def history_weeks(self, repo_id: int) -> list[dict[str, Any]]:
        vals = self.daily[repo_id]
        start = TODAY - timedelta(days=len(vals) - 1)
        by_day = {start + timedelta(days=i): n for i, n in enumerate(vals)}
        weeks = []
        w = week_start(TODAY)
        while w + timedelta(days=6) >= start:
            days = [by_day.get(w + timedelta(days=i), 0) for i in range(7)]
            weeks.append({"week": self._label(w), "total": sum(days), "days": days})
            w -= timedelta(days=7)
        return weeks  # most recent first

    def _label(self, d: date) -> Any:
        if self.label == "unix":  # Pacific midnight (PDT) as a Unix timestamp
            return int(datetime(d.year, d.month, d.day, 7, tzinfo=UTC).timestamp())
        if self.label == "iso_offset":
            return f"{d.isoformat()}T00:00:00-07:00"
        return d.isoformat()

    def _history(self, req: httpx.Request, full_name: str) -> httpx.Response:
        r = self.by_name(full_name)
        if r is None or r["id"] not in self.daily:
            return httpx.Response(404, json={"message": "Not Found"}, headers=self._headers("core"))
        per_page = int(req.url.params.get("per_page", "30"))
        page = int(req.url.params.get("page", "1"))
        assert 1 <= per_page <= 30 and 1 <= page <= 100
        weeks = self.history_weeks(r["id"])
        return self._json(req, "core", weeks[(page - 1) * per_page : page * per_page])

    # --- events ---------------------------------------------------------------------------------
    def _events(self, req: httpx.Request, full_name: str) -> httpx.Response:
        evs = self.events.get(full_name.lower())
        if evs is None:
            return httpx.Response(404, json={"message": "Not Found"}, headers=self._headers("core"))
        per_page = int(req.url.params.get("per_page", "30"))
        page = int(req.url.params.get("page", "1"))
        if page > 3:
            return httpx.Response(422, headers=self._headers("core"))
        window = evs[:300]
        return self._json(req, "core", window[(page - 1) * per_page : page * per_page])


def _parse(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def search_pool(
    n: int, *, created_from: datetime, span: timedelta, seed: int = 7
) -> list[dict[str, Any]]:
    """`n` synthetic repos `org-s/search-NNNN` with skewed star counts and spread creation times."""
    out = []
    for i in range(n):
        stars = 20 + (i * 7919 + seed) % 400 + (5000 if i % 97 == 0 else 0)
        t = created_from + span * ((i * 37 % n) / n)
        ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(
            {
                "id": 8_000_000 + i,
                "node_id": f"R_fake_s{i}",
                "full_name": f"org-s/search-{i:04d}",
                "stars": stars,
                "forks": i % 11,
                "created_at": ts,
                "pushed_at": ts,
            }
        )
    return out
