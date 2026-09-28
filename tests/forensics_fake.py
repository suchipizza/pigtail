"""Synthetic fixtures for the M23 pilot tests (no network, no real data).

- `FakeGitHubCases`: an httpx MockTransport handler for the GitHub routes the case-evidence stage
  and the decay checks use (GraphQL repository metadata, README JSON now and at a commit, the
  commits list, releases, the REST repo record), with ETag/304 and rate-limit headers.
- `FakeSite`: robots.txt and homepages of made-up project sites.
- `CodingBatchBackend`: a batch-capable fake `api` backend answering the coder A, coder B and
  adjudicator prompts from the evidence it is given (valid, verbatim citations), with scripted
  disagreements between A and B.
- `seed_selection`: a stored selection (views A and B, headline pairs, an exemplar pair) and
  the candidates' launch signals.

Every repo, owner, handle and text is invented (`org-p/…`, `person-owner`, `synthetic-person`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from pigtail.llm.api import BatchItemResult, BatchStatus, build_params
from pigtail.llm.pricing import TokenUsage, cost_usd
from pigtail.llm.types import BackendResponse

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
ANCHOR = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
# outcome-proximal numbers the coders must never see (blind-v1, ADR-086): distinctive
STARS, FORKS, POINTS, PH_VOTES, PH_COMMENTS = 98765, 5432, 4242, 7777, 8888
README_STARS, README_FORKS = "31,337", "2,718"
BLIND_NUMBERS = (
    str(STARS),
    str(FORKS),
    str(POINTS),
    str(PH_VOTES),
    str(PH_COMMENTS),
    README_STARS,
    README_FORKS,
    "31337",
    "2718",
)
HANDLE = "synthetic-person"  # a made-up handle that must never reach coded outputs
EMAIL = "synthetic.person@example.org"

REPOS: dict[str, dict[str, Any]] = {
    "org-p/alpha-cli": {"id": 8100001, "owner_type": "Organization", "homepage": "https://alpha.example.org/"},
    "org-p/beta-tool": {"id": 8100002, "owner_type": "Organization", "homepage": None},
    "org-p/gamma-lib": {"id": 8100003, "owner_type": "Organization", "homepage": "https://twitter.com/x"},
    "org-p/delta-app": {"id": 8100004, "owner_type": "Organization", "homepage": "https://blocked.example.org/"},
    "person-owner/epsilon": {"id": 8100005, "owner_type": "User", "homepage": None},
    "org-p/zeta-ex": {"id": 8100006, "owner_type": "Organization", "homepage": None},
    "org-p/eta-loser": {"id": 8100007, "owner_type": "Organization", "homepage": None},
    "org-p/theta-b2": {"id": 8100008, "owner_type": "Organization", "homepage": None},
    "org-p/iota-b2l": {"id": 8100009, "owner_type": "Organization", "homepage": None},
}  # fmt: skip


def readme_text(full: str, at: str) -> str:
    name = full.split("/")[1]
    return (
        f"# {name}\n\n{name} is the first command-line tool that checks config files ({at}).\n\n"
        f"Install: `brew install {name}`\n\nLoved by {README_STARS} stars and "
        f"{README_FORKS} forks.\n\nMaintained by @{HANDLE} "
        f"(https://github.com/{HANDLE}, {EMAIL}).\n\nSee the docs for more.\n"
    )


@dataclass
class FakeGitHubCases:
    requests: list[httpx.Request] = field(default_factory=list)
    gone: set[str] = field(default_factory=set)  # full names whose README is now 404
    changed: set[str] = field(default_factory=set)  # full names whose README changed
    no_commit: set[str] = field(default_factory=set)  # no README commit before T

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def _h(self, resource: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4000",
            "X-RateLimit-Resource": resource,
            "X-RateLimit-Reset": str(int((NOW + timedelta(hours=1)).timestamp())),
            "Content-Type": "application/json",
            **(extra or {}),
        }

    def _json(self, req: httpx.Request, body: Any, resource: str = "core") -> httpx.Response:
        data = json.dumps(body, sort_keys=True).encode()
        etag = '"' + hashlib.sha1(data).hexdigest() + '"'
        if req.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers=self._h(resource, {"ETag": etag}))
        return httpx.Response(200, content=data, headers=self._h(resource, {"ETag": etag}))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path == "/graphql":
            body = json.loads(req.content)
            v = body.get("variables") or {}
            full = f"{v['o0']}/{v['n0']}".lower()
            r = REPOS.get(full)
            node = None
            if r is not None:
                node = {
                    "databaseId": r["id"], "nameWithOwner": full, "stargazerCount": STARS,
                    "forkCount": FORKS, "createdAt": "2025-11-01T10:00:00Z",
                    "pushedAt": "2026-09-01T10:00:00Z",
                    "description": f"A tool by {full.split('/')[0]} for config checks",
                    "isArchived": False, "isFork": False,
                    "primaryLanguage": {"name": "Go"},
                    "repositoryTopics": {"nodes": [{"topic": {"name": "cli"}}]},
                    "owner": {"__typename": r["owner_type"]}, "homepageUrl": r["homepage"],
                }  # fmt: skip
            out = {
                "data": {
                    "r0": node,
                    "rateLimit": {
                        "cost": 1,
                        "remaining": 4999,
                        "limit": 5000,
                        "resetAt": "2026-09-28T13:00:00Z",
                    },
                }
            }
            return httpx.Response(200, json=out, headers=self._h("graphql"))
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/readme", path)
        if m:
            full = m.group(1).lower()
            if full in self.gone:
                return httpx.Response(404, json={"message": "Not Found"})
            ref = req.url.params.get("ref")
            at = "at-anchor" if ref else ("changed" if full in self.changed else "now")
            text = readme_text(full, at)
            return self._json(req, {"name": "README.md", "path": "README.md", "sha": "abc",
                                    "content": base64.b64encode(text.encode()).decode(),
                                    "encoding": "base64"})  # fmt: skip
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/commits", path)
        if m:
            full = m.group(1).lower()
            if full in self.no_commit:
                return self._json(req, [])
            return self._json(req, [{
                "sha": "c0ffee" + str(REPOS.get(full, {"id": 0})["id"]),
                "commit": {"committer": {"name": "Synthetic Person", "email": EMAIL,
                                         "date": "2026-03-01T09:00:00Z"},
                           "author": {"name": "Synthetic Person", "email": EMAIL}},
                "author": {"login": HANDLE}, "committer": {"login": HANDLE},
            }])  # fmt: skip
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/releases", path)
        if m:
            rels = [
                {"tag_name": "v1.0.0", "name": "Introducing v1.0", "published_at":
                 "2026-03-09T12:00:00Z", "prerelease": False,
                 "body": f"First public release. Thanks @{HANDLE}!", "author": {"login": HANDLE}},
                {"tag_name": "v0.1.0", "name": "early", "published_at": "2025-01-01T12:00:00Z",
                 "prerelease": True, "body": "old", "author": {"login": HANDLE}},
            ]  # fmt: skip
            return self._json(req, rels)
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)", path)
        if m:
            return self._json(req, {"full_name": m.group(1), "description": "x"})
        return httpx.Response(404, json={"message": "Not Found"})


@dataclass
class FakeSite:
    requests: list[httpx.Request] = field(default_factory=list)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=True)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        host, path = req.url.host, req.url.path
        if path == "/robots.txt":
            if host == "blocked.example.org":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(404)
        html = (
            "<html><head><title>Alpha</title><script>var x=1;</script></head><body>"
            "<h1>Alpha CLI</h1><p>Try it now: npx alpha-cli. Contact "
            f"{EMAIL}.</p></body></html>"
        )
        return httpx.Response(200, text=html, headers={"Content-Type": "text/html",
                                                       "ETag": '"site1"'})  # fmt: skip


# --- the fake coding model ------------------------------------------------------------------
_BLOCK = re.compile(r"^### evidence_id: (\S+)\n(.*?)\n---\n(.*?)(?=\n\n### evidence_id: |\Z)",
                    re.MULTILINE | re.DOTALL)  # fmt: skip


def _items(text: str) -> list[tuple[str, str, str]]:
    return [(m.group(1), m.group(2), m.group(3)) for m in _BLOCK.finditer(text)]


def _quote(body: str) -> str:
    line = next((ln.strip() for ln in body.splitlines() if len(ln.strip()) > 8), body.strip())
    return line[:60]


def _coded(value: Any, ev: str | None, body: str | None, **kw: Any) -> dict[str, Any]:
    if ev is None or value == "unknown":
        return {"value": value, "evidence_ids": [], "excerpts": [],
                "unknown_reason": kw.get("reason", "insufficient_evidence"),
                "confidence": "low"}  # fmt: skip
    return {"value": value, "evidence_ids": [ev],
            "excerpts": [{"evidence_id": ev, "quote": kw.get("quote") or _quote(body or "")}],
            "unknown_reason": None, "confidence": "medium"}  # fmt: skip


def coder_answer(prompt: str, system: str) -> dict[str, Any]:
    is_b = "Read every evidence item first" in system
    items = _items(prompt)
    by_kind = {}
    for ev, header, body in items:
        kind = header.split("|")[0].replace("kind:", "").strip()
        by_kind[kind] = (ev, body)
    meta = by_kind.get("repo_metadata")
    readme = by_kind.get("readme_at_anchor") or by_kind.get("readme_current")
    launch = by_kind.get("launch_events")
    cat = "ai-apps-agents" if is_b else "devtools"
    mods = {
        m: _coded("no", *(meta or (None, None)))
        for m in (
            "ai_hype",
            "b2b_oss_saas",
            "chinese_ecosystem",
            "corporate_backed",
            "relaunch_pivot",
        )
    }
    mods["cli_devtools"] = _coded("yes", *(readme or (None, None)))
    if readme and not is_b:  # quotes the (redacted) maintainer line: must be dropped
        line = next((ln for ln in readme[1].splitlines() if "Maintained by" in ln), "")
        mods["corporate_backed"] = _coded("yes", readme[0], readme[1], quote=line[:120])
    novelty = _coded("present", *(readme or (None, None)),
                     quote="is the first command-line tool") if readme else _coded(
        "unknown", None, None)  # fmt: skip
    patterns = {f"mc_{i:02d}": _coded("unknown", None, None, reason="unobservable_channel")
                for i in (1, 2, 3, 4, 5, 6, 7, 8, 11, 13)}  # fmt: skip
    patterns["mc_09"] = dict(novelty)
    patterns["mc_10"] = _coded("absent" if is_b else "unknown", *(launch or (None, None)))
    if is_b and launch:  # a quote with a person alias: must be dropped (citation_failed)
        patterns["mc_01"] = _coded("present", launch[0], launch[1], quote="@user1 launched it")
    elif launch:
        patterns["mc_01"] = _coded("present", *launch)
    out_items = []
    for ev, _h, body in items:
        out_items.append({
            "evidence_id": ev,
            "reliability": _coded("high", ev, body),
            "first_party": _coded("yes", ev, body),
            "event_type_supported": _coded("none", ev, body),
        })  # fmt: skip
    return {
        "category_primary": _coded(cat, *(meta or (None, None))),
        "modules": mods,
        "novelty_claim": novelty,
        "novelty_kind": {**novelty, "value": ["new_in_kind"] if readme else []},
        "patterns": patterns,
        "items": out_items,
    }


def adjudicator_answer(prompt: str) -> dict[str, Any]:
    items = {ev: body for ev, _h, body in _items(prompt.split("## Disagreements")[0])}
    units = json.loads(prompt.split("## Disagreements\n", 1)[1].rsplit("\n\nReturn one", 1)[0])
    decisions = []
    for u in units:
        opt = next((o for o in u["options"] if o["value"] != "unknown"), u["options"][0])
        ev = (opt["evidence_ids"] or [None])[0]
        if ev is None or ev not in items:
            decisions.append(
                {
                    "unit": u["unit"],
                    "value": "unknown",
                    "reason": "no support",
                    "evidence_ids": [],
                    "excerpts": [],
                    "unknown_reason": "conflicting_evidence",
                    "confidence": "low",
                }
            )
            continue
        decisions.append({"unit": u["unit"], "value": opt["value"],
                          "reason": "The cited item supports it.", "evidence_ids": [ev],
                          "excerpts": [{"evidence_id": ev, "quote": _quote(items[ev])}],
                          "unknown_reason": None, "confidence": "medium"})  # fmt: skip
    return {"decisions": decisions}


def answer(params: dict[str, Any]) -> dict[str, Any]:
    system = params["system"][0]["text"]
    prompt = params["messages"][0]["content"]
    if "adjudicate" in system:
        return adjudicator_answer(prompt)
    return coder_answer(prompt, system)


@dataclass
class CodingBatchBackend:
    name: str = "api"
    supports_batch: bool = True
    polls_until_end: int = 0
    submitted: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)
    prefix: str = "msgbatch_m23_"

    def params(self, **kw: Any) -> dict[str, Any]:
        return build_params(max_tokens=16000, **kw)

    def prompts(self) -> list[str]:
        return [p["messages"][0]["content"] for b in self.submitted for _, p in b]

    def complete(self, **kw: Any) -> BackendResponse:
        p = self.params(**kw)
        return self._resp(p, None)

    def _resp(self, params: dict[str, Any], batch_id: str | None) -> BackendResponse:
        usage = TokenUsage(input=6000, output=3000, cache_write=1400, cache_read=1400)
        model = params["model"]
        return BackendResponse(
            data=answer(params), model=model, input_tokens=usage.input,
            output_tokens=usage.output, cache_write_tokens=usage.cache_write,
            cache_read_tokens=usage.cache_read,
            cost_usd=cost_usd(model, usage, batch=batch_id is not None) or 0.0,
            batch_id=batch_id,
        )  # fmt: skip

    def submit_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        self.submitted.append(list(requests))
        return f"{self.prefix}{len(self.submitted)}"

    def batch_status(self, batch_id: str) -> BatchStatus:
        self.polls[batch_id] = self.polls.get(batch_id, 0) + 1
        ended = self.polls[batch_id] > self.polls_until_end
        return BatchStatus(batch_id, "ended" if ended else "in_progress", {})

    def batch_results(self, batch_id: str) -> Iterator[BatchItemResult]:
        n = int(batch_id.removeprefix(self.prefix))
        for cid, params in self.submitted[n - 1]:
            yield BatchItemResult(cid, "succeeded", response=self._resp(params, batch_id))


# --- a stored selection ------------------------------------------------------------------------
def _detail(role: str, pair: dict[str, Any] | None, flag: str = "false") -> dict[str, Any]:
    return {
        "anchor": {"type": "launch", "at": ANCHOR.isoformat(), "precision": "hour",
                   "source": "show_hn", "via": "discovery"},
        "pair": pair, "role": role, "star_anomaly": {"flag": flag},
    }  # fmt: skip


def seed_selection(conn: Any, brief: Any) -> str:
    """One stored selection of `brief` with views A and B (two headline pairs each, an exemplar
    pair in view A, a non-headline pair) and the candidates' launch signals."""
    sid = "sel_" + "a" * 20
    conn.execute(
        "INSERT INTO brief_selection (id, brief_id, brief_version, brief_hash, data_version,"
        " as_of, selection_version, outcome_model_version, params_version, inputs_hash,"
        " result_hash, params, summary, balance, sensitivity) VALUES (%s, %s, %s, %s, 'dv1-x',"
        " '2026-09-28', 'selection-v12', '2.1', '1.1.0', %s, %s, '{}', '{}', '{}', '{}')",
        (sid, brief.brief_id, brief.version, brief.content_hash(), "0" * 64, "1" * 64),
    )
    rows = [
        # view, repo, role, rank, pair, pair_panel, headline, loser distance
        ("follow_through", "org-p/alpha-cli", "winner", 1, 1, "field", None, None),
        ("follow_through", "org-p/beta-tool", "matched_loser", None, 1, "field", True, 0.4),
        ("follow_through", "person-owner/epsilon", "matched_loser", None, 1, "field", True, 0.9),
        ("follow_through", "org-p/gamma-lib", "winner", 2, 2, "field", None, None),
        ("follow_through", "org-p/delta-app", "matched_loser", None, 2, "field", True, 0.2),
        ("follow_through", "org-p/zeta-ex", "exemplar", None, 3, "exemplar", None, None),
        ("follow_through", "org-p/eta-loser", "exemplar_matched_loser", None, 3, "exemplar",
         None, 0.3),
        ("launch", "org-p/theta-b2", "winner", 1, 1, "field", None, None),
        ("launch", "org-p/iota-b2l", "matched_loser", None, 1, "field", True, 0.5),
        ("launch", "org-p/gamma-lib", "winner", 3, 2, "field", None, None),
        ("launch", "org-p/alpha-cli", "matched_loser", None, 2, "field", True, 0.1),
        ("launch", "org-p/beta-tool", "qualified_not_selected", None, None, None, None, None),
    ]  # fmt: skip
    for view, full, role, rank, pid, ppanel, headline, dist in rows:
        pair = None
        if pid is not None:
            pair = {
                "pair_id": pid,
                "panel": ppanel,
                "side": "loser" if "loser" in role else "winner",
                "distance": dist,
            }
        detail = _detail(role, pair, flag="true" if full == "org-p/beta-tool" else "false")
        conn.execute(
            "INSERT INTO brief_selection_case (selection_id, view, candidate_ref, repo_full_name,"
            " repo_host_id, repo_id, panel, role, rank, pair_id, pair_panel, headline, detail)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, view, f"gh:{full}", full, REPOS[full]["id"], f"github:{REPOS[full]['id']}",
             "exemplar" if ppanel == "exemplar" else "field", role, rank, pid, ppanel, headline,
             Jsonb(detail)),
        )  # fmt: skip
    for full in REPOS:
        sources = [
            {"source": "show_hn", "term": "t", "hn_item_id": 900 + REPOS[full]["id"] % 100,
             "points": POINTS, "title": f"Show HN: {full.split('/')[1]} – checks configs",
             "time": "2026-03-10T15:00:00+00:00"},
            {"source": "ph_launch", "rule": "anchor-v11", "status": "complete",
             "posts": [{"id": "ph1", "createdAt": "2026-03-10T08:00:00Z",
                        "featuredAt": "2026-03-10T08:00:00Z", "votesCount": PH_VOTES,
                        "commentsCount": PH_COMMENTS, "confirmed": True}]},
            {"source": "bsky_maintainer_posts", "rule": "anchor-v11", "status": "complete",
             "posts": [{"kind": "bluesky_maintainer_post", "time": "2026-03-10T16:00:00+00:00",
                        "role": "maintainer", "match": "repo_url",
                        "text": "SECRET POST TEXT must not be copied"}],
             "posts_without_launch_wording": 0},
        ]  # fmt: skip
        conn.execute(
            "INSERT INTO brief_candidate (brief_id, brief_version, candidate_ref, repo_full_name,"
            " repo_host_id, repo_id, panel, sources, metadata) VALUES (%s, %s, %s, %s, %s, %s,"
            " 'field', %s, %s)",
            (brief.brief_id, brief.version, f"gh:{full}", full, REPOS[full]["id"],
             f"github:{REPOS[full]['id']}", Jsonb(sources),
             Jsonb({"homepage": REPOS[full]["homepage"]})),
        )  # fmt: skip
    return sid
