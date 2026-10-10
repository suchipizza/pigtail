"""Optional Product Hunt launches with the user's own token (owner decision 2026-10-10).

Product Hunt is never called here: a fake transport stands in for api.producthunt.com and the product's
homepage. The posts mirror what the owner's real probe returned on 2026-10-10.
"""

from __future__ import annotations

import asyncio
import copy
import json
import types
from datetime import date

import httpx
import pytest

from pigtail.config import Config
from pigtail.policies.loader import default_registry
from pigtail.providers.base import Meter
from pigtail.providers.community.product_hunt import (
    ProductHuntAuthError,
    ProductHuntClient,
    guess_slugs,
    refs_in,
)
from pigtail.publication.gate import PublicationGate
from pigtail.research.builder import BundleBuilder
from pigtail.research.orchestrator import _product_hunt, product_hunt_status
from pigtail.research.repository_analysis import PRODUCT_HUNT_API_PARSER, product_hunt_api_event


def post(pid, name, slug, when, votes=100, comments=5, featured=True):
    return {
        "id": pid,
        "name": name,
        "slug": slug,
        "createdAt": when,
        "featuredAt": when if featured else None,
        "votesCount": votes,
        "commentsCount": comments,
    }


POSTS = {
    "analytics": post("1", "Analytics", "analytics", "2020-05-11T07:00:00Z", 106, 1),
    "plausible-analytics": post("2", "Plausible Analytics", "plausible-analytics", "2020-08-27T07:01:00Z", 502, 107),
    "tally": post("3", "Tally", "tally", "2014-09-18T07:00:00Z", 312, 44),
    "tally-2-0": post("4", "Tally 2.0", "tally-2-0", "2023-09-19T07:01:00Z", 1467, 345),
    "id:415284": post("4", "Tally 2.0", "tally-2-0", "2023-09-19T07:01:00Z", 1467, 345),
    "hatchet": post("5", "Hatchet", "hatchet", "2015-11-11T08:00:00Z", 6, 0, featured=False),
}
HOMEPAGE = (
    '<a href="https://www.producthunt.com/posts/tally-2-0?utm_source=badge">'
    '<img src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=415284&theme=light"></a>'
)


def fake_ph(status=200):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host != "api.producthunt.com":
            return httpx.Response(200, text=HOMEPAGE)
        if status != 200:
            return httpx.Response(status, json={"error": "invalid_token"})
        body = json.loads(req.content)
        if "posts(first: 1)" in body["query"]:
            return httpx.Response(200, json={"data": {"posts": {"edges": []}}})
        v = body["variables"]["v"]
        ref = f"id:{v}" if "post(id:" in body["query"] else v
        return httpx.Response(200, json={"data": {"post": POSTS.get(ref)}})

    return httpx.MockTransport(handler), seen


def builder():
    return BundleBuilder(
        target={
            "id": "t",
            "kind": "product",
            "name": "Tally",
            "canonical_url": "https://tally.so",
            "domain": "tally.so",
            "description": None,
            "primary_repository_id": None,
            "external_ids": [],
            "aliases": [],
        }
    )


def test_refs_from_badges_and_links():
    assert refs_in(HOMEPAGE) == ["tally-2-0", "id:415284"]
    assert refs_in("see https://www.producthunt.com/products/pocketbase") == ["pocketbase"]
    assert guess_slugs(["Tally", "tally"]) == ["tally", "tally-2", "tally-2-0"]


def test_guesses_keep_only_launches_named_like_the_product_and_not_older_than_it():
    transport, _ = fake_ph()
    client = ProductHuntClient("tok", Meter(), transport=transport)
    got = asyncio.run(
        client.launches_for(
            "Plausible",
            own_refs=[],
            search_refs=[],
            guess_words=["Plausible", "plausible", "analytics", "plausible/analytics"],
            not_before=date(2018, 12, 4),
        )
    )
    assert [(x.slug, x.found_by, x.votes) for x in got] == [("plausible-analytics", "guess", 502)]  # not "Analytics"
    old = asyncio.run(
        ProductHuntClient("tok", Meter(), transport=fake_ph()[0]).launches_for(
            "Hatchet", own_refs=[], search_refs=[], guess_words=["Hatchet"], not_before=date(2023, 12, 15)
        )
    )
    assert old == []  # a 2015 "Hatchet" is another product


def test_own_site_badge_is_trusted_and_guesses_need_a_project_date():
    transport, _ = fake_ph()
    client = ProductHuntClient("tok", Meter(), transport=transport)
    got = asyncio.run(
        client.launches_for(
            "Tally", own_refs=["tally-2-0", "id:415284"], search_refs=[], guess_words=["Tally"], not_before=None
        )
    )
    assert [(x.slug, x.found_by) for x in got] == [("tally-2-0", "own_site")]  # one launch, no 2014 "Tally"


def test_token_goes_to_the_api_only():
    transport, seen = fake_ph()
    client = ProductHuntClient("secret-token", Meter(), transport=transport)
    asyncio.run(client.page_refs("https://tally.so/"))
    asyncio.run(client.check())
    home = [r for r in seen if r.url.host == "tally.so"]
    api = [r for r in seen if r.url.host == "api.producthunt.com"]
    assert home and "authorization" not in home[0].headers
    assert api and api[0].headers["authorization"] == "Bearer secret-token"


def test_wrong_token_raises_a_clear_error():
    client = ProductHuntClient("bad", Meter(), transport=fake_ph(status=401)[0])
    with pytest.raises(ProductHuntAuthError):
        asyncio.run(client.check())


def test_launch_event_keeps_metadata_only():
    transport, _ = fake_ph()
    launch = asyncio.run(
        ProductHuntClient("tok", Meter(), transport=transport).launches_for(
            "Tally", own_refs=["tally-2-0"], search_refs=[], guess_words=[], not_before=None
        )
    )[0]
    b = builder()
    ev = product_hunt_api_event(b, launch, default_registry().policy_for(launch.url, "product_hunt"))
    assert ev["event_type"] == "product_hunt_launch" and ev["time"]["start"].startswith("2023-09-19")
    assert b.c["source_fetches"][-1]["parser_version"] == PRODUCT_HUNT_API_PARSER
    assert "1467 upvotes and 345 comments" in b.c["claims"][-1]["statement"]
    assert b.c["sources"][-1]["url"] == "https://www.producthunt.com/posts/tally-2-0"


def test_without_a_token_nothing_is_called(monkeypatch):
    monkeypatch.delenv("PRODUCTHUNT_TOKEN", raising=False)
    cfg = Config()
    assert product_hunt_status(cfg) == "no_keys"
    b = builder()
    target = types.SimpleNamespace(kind="product", name="Tally", domain="tally.so", repo=None, product_hunt_refs=[])
    prog = types.SimpleNamespace(info=lambda *_: None)
    asyncio.run(_product_hunt(b, target, None, [], cfg, Meter(), default_registry(), prog))  # type: ignore[arg-type]
    assert b.c["events"] == [] and b.c["gaps"] == []  # links from web search stay the only Product Hunt data
    monkeypatch.setenv("PRODUCTHUNT_TOKEN", "tok")
    assert product_hunt_status(cfg) == "on"
    cfg.product_hunt.enabled = False  # what --no-producthunt does
    assert product_hunt_status(cfg) == "off"


def test_publication_gate_blocks_product_hunt_api_data(bundle_dict):
    b = copy.deepcopy(bundle_dict)
    b["source_fetches"][0]["parser_version"] = PRODUCT_HUNT_API_PARSER
    res = PublicationGate(b, input_hash="sha256:test").run(render=False)
    assert res.status == "BLOCKED"
    assert any(f.rule_id == "PUB-020" for f in res.audit.findings)
