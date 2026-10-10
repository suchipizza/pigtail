"""Optional Reddit search with the user's own API keys (owner decision 2026-10-09).

Reddit is never called here: a fake transport stands in for www.reddit.com and oauth.reddit.com.
"""

from __future__ import annotations

import asyncio
import copy
import types

import httpx
import pytest
from typer.testing import CliRunner

from pigtail.cli import app
from pigtail.config import Config
from pigtail.policies.loader import default_registry
from pigtail.providers.base import Meter
from pigtail.providers.community.reddit import USER_AGENT, RedditAuthError, RedditClient
from pigtail.publication.gate import PublicationGate
from pigtail.research.builder import BundleBuilder
from pigtail.research.orchestrator import _reddit, reddit_status
from pigtail.research.repository_analysis import REDDIT_API_PARSER, reddit_api_event


def post(pid, title, *, score=50, url="https://www.reddit.com/r/x", selftext="", **kw):
    d = {
        "id": pid,
        "title": title,
        "subreddit": "SideProject",
        "permalink": f"/r/SideProject/comments/{pid}/x/",
        "created_utc": 1789000000,  # 2026-09-10
        "score": score,
        "num_comments": 12,
        "url": url,
        "selftext": selftext,
        "author": "maker",
    }
    return {"kind": "t3", "data": d | kw}


SEARCH = [
    post(
        "a1",
        "My side project crossed 7,000 GitHub stars",
        score=420,
        selftext="Repo: https://github.com/latent-spaces/brag — feedback welcome",
    ),
    post("b2", "brag: launch videos from the terminal", score=90, url="https://github.com/latent-spaces/brag"),
    post("c3", "Brag about your wins here", score=900, selftext="weekly thread", author="mod"),  # not the repo
    post("d4", "Removed", selftext="see latent-spaces/brag", removed_by_category="moderator"),
    post("e5", "Low score latent-spaces/brag", score=0),
    post("f6", "nsfw latent-spaces/brag", over_18=True),
]


# The maker's own post list: a milestone post that names the project but not the repository path.
SUBMITTED = [
    post("g7", "My side project crossed 7,000 GitHub stars", score=310, selftext="Thanks to all who tried /brag!"),
    post("h8", "What is the best mechanical keyboard?", score=40, selftext="asking for a friend"),
    post("i9", "Bragging rights: my first marathon", score=25),  # "brag" only inside a longer word
]


def fake_reddit(token_status=200):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "www.reddit.com":
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer", "expires_in": 86400})
        children = SUBMITTED if req.url.path.startswith("/user/") else SEARCH
        return httpx.Response(200, json={"kind": "Listing", "data": {"children": children}})

    return httpx.MockTransport(handler), seen


def test_keeps_only_posts_that_really_mention_the_project():
    transport, seen = fake_reddit()
    client = RedditClient("id", "secret", Meter(), transport=transport)
    posts = asyncio.run(client.posts_for(["latent-spaces/brag"]))
    assert [(p.id, p.matched_by) for p in posts] == [("a1", "text"), ("b2", "link")]
    assert posts[0].url == "https://www.reddit.com/r/SideProject/comments/a1/x/"
    search = [r for r in seen if r.url.host == "oauth.reddit.com"]
    assert search and all(r.headers["Authorization"] == "bearer tok" for r in search)
    assert all(r.headers["User-Agent"] == USER_AGENT for r in seen)


def test_finds_the_same_makers_other_posts_that_name_the_project():
    transport, seen = fake_reddit()
    client = RedditClient("id", "secret", Meter(), transport=transport)
    posts = asyncio.run(client.posts_for(["latent-spaces/brag"], names=["brag"]))
    assert [(p.id, p.matched_by) for p in posts] == [("a1", "text"), ("g7", "author"), ("b2", "link")]
    paths = [r.url.path for r in seen if r.url.host == "oauth.reddit.com"]
    assert paths.count("/user/maker/submitted") == 1 and not any("/user/mod/" in p for p in paths)
    assert any(r.url.params.get("q") == '"brag"' for r in seen)
    assert "c3" not in {p.id for p in posts}  # names the project but posted by someone else


def test_wrong_keys_raise_a_clear_error():
    transport, _ = fake_reddit(token_status=401)
    client = RedditClient("id", "bad", Meter(), transport=transport)
    with pytest.raises(RedditAuthError):
        asyncio.run(client.sign_in())


def test_reddit_event_keeps_metadata_only():
    transport, _ = fake_reddit()
    p = asyncio.run(RedditClient("id", "s", Meter(), transport=transport).posts_for(["latent-spaces/brag"]))[0]
    b = BundleBuilder(
        target={
            "id": "t",
            "kind": "repository",
            "name": "brag",
            "canonical_url": "x",
            "domain": None,
            "description": None,
            "primary_repository_id": None,
            "external_ids": [],
            "aliases": [],
        }
    )
    ev = reddit_api_event(b, p, default_registry().policy_for(p.url, "reddit"))
    assert ev["event_type"] == "reddit_post" and ev["time"]["precision"] == "minute"
    assert b.c["source_fetches"][-1]["parser_version"] == REDDIT_API_PARSER
    claim = b.c["claims"][-1]["statement"]
    assert "420 upvotes" in claim and "maker" not in claim  # no username
    assert "feedback welcome" not in str(b.c)  # post text is never stored
    assert all(el["excerpt"] is None for el in b.c["evidence_links"])


def test_without_keys_the_report_says_reddit_is_missing(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    cfg = Config()
    assert reddit_status(cfg) == "no_keys"
    b = BundleBuilder(
        target={
            "id": "t",
            "kind": "repository",
            "name": "brag",
            "canonical_url": "x",
            "domain": None,
            "description": None,
            "primary_repository_id": None,
            "external_ids": [],
            "aliases": [],
        }
    )
    target = types.SimpleNamespace(repo=None, domain="brag.dev")
    prog = types.SimpleNamespace(info=lambda *_: None)
    asyncio.run(_reddit(b, target, cfg, Meter(), default_registry(), prog))  # type: ignore[arg-type]
    gap = b.c["gaps"][-1]
    assert gap["severity"] == "material" and "Reddit's API was not used" in gap["summary"]
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")  # gitleaks:allow
    assert reddit_status(cfg) == "on"
    cfg.reddit.enabled = False  # what --no-reddit does
    assert reddit_status(cfg) == "off"


def test_doctor_explains_reddit_is_optional(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    r = CliRunner().invoke(app, ["doctor", "--offline"])
    assert "Reddit API" in r.output and "optional" in r.output


def test_publication_gate_blocks_reddit_api_data(bundle_dict):
    b = copy.deepcopy(bundle_dict)
    b["source_fetches"][0]["parser_version"] = REDDIT_API_PARSER
    res = PublicationGate(b, input_hash="sha256:test").run(render=False)
    assert res.status == "BLOCKED"
    assert any(f.rule_id == "PUB-019" for f in res.audit.findings)
    clean = PublicationGate(copy.deepcopy(bundle_dict), input_hash="sha256:test").run(render=False)
    assert not any(f.rule_id == "PUB-019" for f in clean.audit.findings)


def test_report_scope_discloses_missing_reddit(bundle_dict):
    from pigtail.renderer.view_model import build_view_model

    b = copy.deepcopy(bundle_dict)
    assert not build_view_model(b)["reddit_missing"]
    b["gaps"].append(
        {
            "id": "g-reddit",
            "gap_type": "surface_not_covered",
            "severity": "material",
            "related_refs": [],
            "surface_key": "reddit",
            "summary": "Reddit was not searched (no Reddit API keys were set), so ...",
        }
    )
    assert build_view_model(b)["reddit_missing"]
