"""In-process fake of the HN Firebase and Algolia APIs over the synthetic fixtures in
tests/fixtures/hn/ (M1-T4, M1-T14, CB-02 tests). No network. All usernames are fake (hnuserNNN,
hnfillNNN)."""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx

FIX = Path(__file__).parent / "fixtures" / "hn"
FILLER_BASE = 8_000_000  # synthetic filler stories: ids 8000000 + n, by "hnfill<n>"


def load_items() -> dict[int, dict[str, Any] | None]:
    raw = json.loads((FIX / "firebase_items.json").read_text())["items"]
    return {int(k): v for k, v in raw.items()}


def load_hits() -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = json.loads((FIX / "algolia_hits.json").read_text())["hits"]
    return hits


def filler(n: int) -> dict[str, Any]:
    return {
        "id": FILLER_BASE + n,
        "type": "story",
        "by": f"hnfill{n:03d}",
        "time": 1790000000 + n,
        "title": f"Synthetic filler story {n}",
        "url": f"https://example.org/filler/{n}",
        "score": 1000 - n,
        "descendants": n % 7,
        "text": f"synthetic body by hnfill{n:03d}",
        "kids": [FILLER_BASE + 10_000 + n],
    }


class FakeHN:
    """httpx transport handler; mutate `items` / `hits` to simulate upstream changes."""

    def __init__(self, top: list[int] | None = None, hits: list[dict[str, Any]] | None = None):
        self.items = load_items()
        self.hits = load_hits() if hits is None else hits
        fixed = [9000001, 9000004, 9000002, 9000003]
        self.top = top if top is not None else fixed + [FILLER_BASE + n for n in range(496)]
        self.requests: list[httpx.Request] = []
        self.fail_items: set[int] = set()

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.host == "hacker-news.firebaseio.com":
            return self._firebase(req)
        if req.url.host == "hn.algolia.com":
            return self._algolia(req)
        return httpx.Response(404)

    def _firebase(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/v0/topstories.json":
            return httpx.Response(200, json=self.top)
        m = re.fullmatch(r"/v0/item/(\d+)\.json", path)
        if not m:
            return httpx.Response(404)
        iid = int(m.group(1))
        if iid in self.fail_items:
            return httpx.Response(503)
        item = self.items.get(iid)
        if item is None and FILLER_BASE <= iid < FILLER_BASE + 1000:
            item = filler(iid - FILLER_BASE)
        return httpx.Response(
            200, content=json.dumps(item).encode(), headers={"Content-Type": "application/json"}
        )

    def _algolia(self, req: httpx.Request) -> httpx.Response:
        if req.url.path != "/api/v1/search_by_date":
            return httpx.Response(404)
        p = req.url.params
        words = [w for w in p.get("query", "").lower().split() if w]
        tags = p.get("tags", "")
        allowed = set(tags.strip("()").split(",")) if tags else set()
        lo, hi = 0, 2**62
        for cond in p.get("numericFilters", "").split(","):
            if cond.startswith("created_at_i>="):
                lo = int(cond.split(">=")[1])
            elif cond.startswith("created_at_i<"):
                hi = int(cond.split("<")[1])
        url_only = p.get("restrictSearchableAttributes") == "url"
        out = []
        for h in sorted(self.hits, key=lambda h: -int(h["created_at_i"])):
            if allowed and not allowed & set(h.get("_tags", [])):
                continue
            if not lo <= int(h["created_at_i"]) < hi:
                continue
            fields = ["url"] if url_only else ["title", "url", "story_text", "comment_text"]
            hay = " ".join(str(h.get(f) or "") for f in fields).lower().replace("&#x2f;", "/")
            if all(w in hay for w in words):
                out.append(h)
        hpp = int(p.get("hitsPerPage", "20"))
        page = int(p.get("page", "0"))
        visible = out[:1000]  # the observed ~1,000-hit cap
        body = {
            "hits": copy.deepcopy(visible[page * hpp : (page + 1) * hpp]),
            "nbHits": len(out),
            "page": page,
            "nbPages": math.ceil(len(visible) / hpp) if hpp else 0,
            "hitsPerPage": hpp,
        }
        return httpx.Response(200, json=body)
