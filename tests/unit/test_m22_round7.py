"""ADR-085 (owner decisions of 2026-09-27): Product Hunt launches and declared maintainers'
Bluesky posts as view B launch events. Unit tests with fakes only (no network, no database):
the Product Hunt queries ask for project-level fields only, slug candidates, the product slot and
the confirmation rules, rate-limit headers, the connector off without its token; Bluesky's
declared accounts (homepage, README, social accounts, org page), the search's parameters, the
evidence placeholder and error messages without the handle; view B's anchor with all five
launch-event kinds, the tie-break, relaunches, the incomplete-source rule, the secondary
measures never ranked on; versions, guard, parameters, determinism and the estimate. Every name,
handle and DID here is made up.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from pigtail.briefs import confirm as conf
from pigtail.briefs import estimate as est
from pigtail.briefs import launch_sources as ls
from pigtail.briefs import outcomes as out
from pigtail.briefs import selection as selmod
from pigtail.briefs.candidates import Candidate
from pigtail.briefs.confirm import domain_in_text, ph_confirm_by_rules, ph_haiku_input
from pigtail.briefs.estimate import SelectionState, estimate, launch_source_warnings, run_scope
from pigtail.briefs.launch_sources import (
    blocked,
    homepage_search_url,
    links_to,
    ph_blocked,
    ph_name_key,
    ph_repo_key,
    ph_slug_candidates,
    ph_urls_only,
)
from pigtail.briefs.model import sha256_json
from pigtail.briefs.outcomes import launch_events, view_b_anchor
from pigtail.briefs.selection import (
    ANCHOR_RULE_LABELS,
    ANCHOR_RULE_VERSION,
    DECLARED_RULES,
    PH_COMMENTS,
    PH_VOTES,
    SELECTION_VERSION,
    VIEW_LAUNCH,
    Anchor,
    Context,
    Definition,
    Value,
    select,
    select_views,
)
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import FetchError, RetryPolicy, TokenBucket
from pigtail.connectors.bluesky import (
    ACCOUNT_PLACEHOLDER,
    BlueskySearchConnector,
    account_id,
    bsky_evidence_url,
    declared_accounts,
    parse_search_page,
)
from pigtail.connectors.github import parse_org_profile, parse_social_accounts
from pigtail.connectors.producthunt import (
    POST_FIELDS,
    QUERIES,
    TOPIC_FIELDS,
    TOPIC_QUERY,
    ProductHuntConnector,
    ProductHuntRateLimited,
    parse_post,
    reset_seconds,
)
from tests.launch_sources_fake import (
    DID_B,
    HANDLE_A,
    HANDLE_C,
    PH_TOKEN,
    FakeBluesky,
    FakeProductHunt,
    bsky_post,
    ph_post,
)
from tests.unit.test_m22_round6 import W0, W1, cand, mixed_field, releases, show_hn
from tests.unit.test_m22_views import B, ctx

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def d(month: int, day: int = 1, year: int = 2025, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# --- Product Hunt: the queries ask for project-level fields only ------------------------------
PERSON_FIELDS = {"makers", "maker", "user", "users", "comments", "votes", "hunter", "hunters",
                 "followers", "collections", "reviews", "username", "twitterUsername"}  # fmt: skip


def test_ph_queries_request_no_person_fields():
    for q in QUERIES:
        tokens = set(re.findall(r"[A-Za-z_]+", q))
        assert not tokens & PERSON_FIELDS, (q, tokens & PERSON_FIELDS)
        # every field requested on a post is one of the project-level fields; the topic
        # listing asks only for what the shared topic cache stores (ADR-085 addendum 4)
        inner = re.findall(r"\{\s*([a-zA-Z ]+)\s*\}", q)[-1].split()
        want = TOPIC_FIELDS if q == TOPIC_QUERY else POST_FIELDS
        assert set(inner) == set(want) and set(want) <= set(POST_FIELDS)
    assert "url" not in POST_FIELDS and "website" not in POST_FIELDS and "topics" not in POST_FIELDS


def ph_conn(tmp: Path, fake: FakeProductHunt, sleeps: list[float] | None = None, **kw: Any):
    return ProductHuntConnector(
        store=LocalSnapshotStore(tmp / "snap"),
        http=fake.client(),
        env={"PH_API_TOKEN": PH_TOKEN},
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        limiter=TokenBucket(1000, burst=1000),
        retry=RetryPolicy(max_retries=2),
        clock=lambda: NOW,
        **kw,
    )


def test_ph_request_carries_the_token_and_a_query_of_ours_only(tmp_path):
    fake = FakeProductHunt([ph_post("11", "StellarLint", created=d(3))])
    c = ph_conn(tmp_path, fake)
    f = c.post_by_slug(
        "stellarlint", evidence_url="https://api.producthunt.com/x?launch_source_repo=o/r&"
    )
    p = parse_post(f.data)
    assert p is not None and p.id == "11" and p.votes == 10 and p.comments == 2
    assert fake.requests[0]["query"] in QUERIES
    assert "makers" not in f.data.decode() and "SYNTH-MAKER" not in f.data.decode()
    assert f.evidence.url.startswith("https://api.producthunt.com/x?launch_source_repo=")
    assert "fake-ph-token" not in repr(c) and "fake-ph-token" not in f.evidence.url
    # the client-side limiter is conservative: one request every 4 s by default
    default = ProductHuntConnector(store=LocalSnapshotStore(tmp_path / "s2"), env={})
    assert default.limiter.rate == pytest.approx(0.25)


def test_ph_connector_is_off_without_a_token_and_the_refusal_names_it(tmp_path):
    c = ProductHuntConnector(store=LocalSnapshotStore(tmp_path / "snap"), env={})
    assert c.enabled is False and c.has_token is False
    why = ph_blocked(c)
    assert why is not None and "PH_API_TOKEN" in why and "refused" in why
    assert ph_blocked(None) == why
    assert blocked(None, None, product_hunt=False, bluesky=False) is None
    assert "PH_API_TOKEN" in (blocked(None, object(), product_hunt=True, bluesky=True) or "")
    on = ProductHuntConnector(store=LocalSnapshotStore(tmp_path / "s"), env={"PH_API_TOKEN": "x"})
    assert on.enabled and ph_blocked(on) is None
    off = ProductHuntConnector(
        store=LocalSnapshotStore(tmp_path / "s3"),
        env={"PH_API_TOKEN": "x", "PIGTAIL_CONNECTOR_PRODUCTHUNT_ENABLED": "false"},
    )
    assert ph_blocked(off) is not None


def test_ph_rate_limit_headers_make_the_connector_wait_or_stop(tmp_path):
    fake = FakeProductHunt([ph_post("11", "StellarLint", created=d(3))])
    fake.limit, fake.remaining, fake.reset = 6250, 400, 120  # at the 10 % reserve
    sleeps: list[float] = []
    c = ph_conn(tmp_path, fake, sleeps)
    ev = "https://api.producthunt.com/x"
    c.post_by_slug("stellarlint", evidence_url=ev)  # the answer says: 390 left, reset in 120 s
    assert sleeps == []
    c.post_by_slug("stellarlint", evidence_url=ev)  # below the reserve: wait for the reset
    assert sleeps == [pytest.approx(121.0)]
    # a 429 waits for Retry-After, then retries
    fake.remaining = 5000
    c.rate_remaining = None
    fake.script = [httpx.Response(429, headers={"Retry-After": "7"})]
    sleeps.clear()
    c.post_by_slug("stellarlint", evidence_url=ev)
    assert sleeps == [pytest.approx(8.0)] and len(fake.requests) == 4
    # a reset further away than one window: stop instead of sleeping for hours
    fake.remaining, fake.reset = 1, 7200
    c.post_by_slug("stellarlint", evidence_url=ev)
    with pytest.raises(ProductHuntRateLimited):
        c.post_by_slug("stellarlint", evidence_url=ev)
    # the reset header read as seconds, or as a Unix time when it is that large
    assert reset_seconds("120", NOW) == 120.0
    assert reset_seconds(str(int(NOW.timestamp()) + 60), NOW) == pytest.approx(60.0)
    assert reset_seconds(None, NOW) is None and reset_seconds("soon", NOW) is None


# --- Product Hunt: matching and confirmation --------------------------------------------------
def test_slug_candidates_name_key_and_the_short_or_common_name_rule():
    assert ph_slug_candidates("org/Kube_Forge.io") == ["kube-forge-io", "kubeforgeio"]
    assert ph_slug_candidates("org/stellarlint") == ["stellarlint"]
    assert ph_slug_candidates("org/acme-cli") == ["acme-cli", "acmecli"]
    assert len(ph_slug_candidates("org/a-b-c")) <= 2
    # the product slot: the repo name and its name parts joined give the same key
    assert ph_name_key("Acme CLI") == ph_name_key("acme-cli") == ph_repo_key("o/acme_cli")
    assert ph_name_key("Acme CLI Pro") != ph_repo_key("o/acme-cli")
    assert ph_name_key("KubeForge 2.0") != ph_repo_key("o/kubeforge")
    assert ph_urls_only("o/json") and ph_urls_only("o/studio") and ph_urls_only("o/cli-tool")
    assert not ph_urls_only("o/stellarlint")


def test_ph_confirmation_rules_url_domain_keywords_and_shared_hosts():
    kw: dict[str, Any] = dict(full_name="org-p/stellarlint", description=None,
                              homepage_domain=None, tagline=None)  # fmt: skip
    # 1. the repo's GitHub URL in the description or tagline
    assert ph_confirm_by_rules(**kw, post_description="Code: github.com/ORG-P/StellarLint") == (
        "github_url"
    )
    assert ph_confirm_by_rules(**kw, post_description="github.com/org-p/stellarlint-pro") is None
    # 2. the homepage domain as a whole domain, never a shared host
    kw2 = {**kw, "homepage_domain": "stellarlint.example"}
    assert ph_confirm_by_rules(**kw2, post_description="See www.stellarlint.example/docs") == (
        "homepage_domain"
    )
    assert ph_confirm_by_rules(**kw2, post_description="docs.stellarlint.example.org") is None
    kw3 = {**kw, "homepage_domain": "vercel.app"}
    assert ph_confirm_by_rules(**kw3, post_description="Hosted on vercel.app") is None
    assert domain_in_text("try stellarlint.example.", "stellarlint.example")
    assert not domain_in_text("mystellarlint.example", "stellarlint.example")
    # 3. >= 2 distinctive keywords shared with the repo description (the rule-3 tokenizer)
    kw4 = {**kw, "description": "Lints YAML pipelines and Terraform modules"}
    assert ph_confirm_by_rules(**kw4, post_description="for terraform modules") == (
        "description_keywords"
    )
    assert ph_confirm_by_rules(**kw4, post_description="A fast open source tool") is None
    # a short or common name: rules 1-2 only
    assert (
        ph_confirm_by_rules(**{**kw4, "full_name": "o/lint"}, post_description="terraform modules",
                            urls_only=True) is None
    )  # fmt: skip
    text = ph_haiku_input("orgx/stellarlint", "orgx builds it", "stellarlint.example",
                          "StellarLint", "Lint by orgx", "x" * 900)  # fmt: skip
    assert "orgx" not in text and "[owner]" in text and len(text) < 700
    assert conf.PH_PROMPT.id == "ph_match_check" and conf.PH_PROMPT.fingerprint


# --- Bluesky: declared accounts, in memory -------------------------------------------------------
def test_declared_accounts_from_homepage_readme_social_accounts_and_org_page():
    homepage = f"https://bsky.app/profile/{HANDLE_A}"
    readme = (
        "# Gamma\nFollow updates: @" + HANDLE_C + " or bsky.app/profile/" + DID_B + "\n"
        "Not accounts: example.org/@maintainer-x.bsky.social, @notahandle,"
        " bsky.app/profile/bsky.app, https://example.org/@someone, bsky.app/profile/not_valid!"
    )
    assert declared_accounts([homepage]) == [HANDLE_A]
    assert declared_accounts([readme]) == [HANDLE_C, DID_B]
    social = parse_social_accounts(
        b'[{"provider":"bluesky","url":"https://bsky.app/profile/' + HANDLE_A.encode() + b'"},'
        b'{"provider":"mastodon","url":"https://example.social/@x"},'
        b'{"provider":"generic","url":"https://bsky.app/profile/' + HANDLE_C.encode() + b'"}]'
    )
    assert declared_accounts(social) == [HANDLE_A, HANDLE_C]
    org = parse_org_profile(
        b'{"login":"org-x","blog":"https://bsky.app/profile/' + DID_B.encode() + b'",'
        b'"description":"We build things","email":"x@example.org"}'
    )
    assert declared_accounts(org) == [DID_B]
    assert account_id("@" + HANDLE_A.upper()) == HANDLE_A
    assert account_id("did:plc:tooshort") is None and account_id("bsky.social") is None
    assert account_id("did%3Aplc%3Aabcdefghijklmnopqrstuvwx") == DID_B


def test_search_posts_carries_author_and_url_filters_only_and_no_handle_leaves_memory(tmp_path):
    fake = FakeBluesky(
        [bsky_post(1, HANDLE_A, d(5), ["https://github.com/org-b1/alpha-tool"], did=DID_B)]
    )
    c = BlueskySearchConnector(
        store=LocalSnapshotStore(tmp_path / "snap"),
        http=fake.client(),
        env={},
        sleep=lambda s: None,
        limiter=TokenBucket(1000, burst=1000),
        clock=lambda: NOW,
    )
    ev = bsky_evidence_url(c.base, "org-b1/alpha-tool", "repo_url", 0)
    f = c.search_posts(author=HANDLE_A, url="https://github.com/org-b1/alpha-tool", since=W0,
                       until=W1, cursor=None, evidence_url=ev)  # fmt: skip
    (req,) = fake.requests
    assert set(req.url.params) == {"q", "author", "url", "sort", "limit"}  # no dates (400 live)
    assert req.url.params["sort"] == "latest" and req.url.params["q"] == "*"
    assert HANDLE_A not in f.evidence.url and DID_B not in f.evidence.url
    assert ACCOUNT_PLACEHOLDER in f.evidence.url and "launch_source_repo=org-b1/alpha-tool&" in ev
    posts, cursor = parse_search_page(f.data)
    assert cursor is None and len(posts) == 1 and posts[0].at == d(5)
    assert not any(HANDLE_A in x or "SYNTH" in x for x in posts[0].links)
    assert links_to(posts[0].links, "repo_url", "org-b1/alpha-tool", "")
    # errors name the endpoint, never the query (which holds the account)
    fake.fail = True
    with pytest.raises(FetchError) as ei:
        c.search_posts(author=HANDLE_A, url="https://x.example", since=W0, until=W1, cursor=None,
                       evidence_url=ev)  # fmt: skip
    assert HANDLE_A not in str(ei.value)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {req.url}")

    c.http = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(FetchError) as ei:
        c.search_posts(author=HANDLE_A, url="https://x.example", since=W0, until=W1, cursor=None,
                       evidence_url=ev)  # fmt: skip
    assert HANDLE_A not in str(ei.value) and "ConnectError" in str(ei.value)
    # no feed, profile or follower method exists on the connector; handle resolution only
    methods = {m for m in dir(c) if callable(getattr(c, m, None))}
    assert not {m for m in methods if re.search(r"feed|profile|follow", m, re.I)}
    assert {m for m in methods if re.search(r"resolve", m, re.I)} == {"resolve_handle"}


def test_sort_at_is_the_earlier_of_created_and_indexed_and_links_are_checked():
    data = (
        b'{"posts":[{"uri":"at://x/1","record":{"createdAt":"2025-05-02T00:00:00Z","text":'
        b'"see stellar.example/docs","facets":[]},"indexedAt":"2025-05-01T00:00:00Z",'
        b'"author":{"handle":"' + HANDLE_A.encode() + b'"}}],"cursor":"25"}'
    )
    posts, cursor = parse_search_page(data)
    assert cursor == "25" and posts[0].at == datetime(2025, 5, 1, tzinfo=UTC)
    assert links_to(posts[0].links, "homepage_url", "o/r", "https://stellar.example")
    assert not links_to(posts[0].links, "homepage_url", "o/r", "https://stellar.example/blog")
    assert not links_to(["https://github.com/o/r-other"], "repo_url", "o/r", "")
    assert homepage_search_url("https://bsky.app/profile/x.example", "o/r") is None
    assert homepage_search_url("https://github.com/o/r", "o/r") is None
    assert homepage_search_url("stellar.example", "o/r") == "https://stellar.example"


# --- view B: five launch-event kinds ----------------------------------------------------------
def ph_sig(
    *posts: tuple[str, datetime | None, datetime, bool], votes: int = 5, status: str = "complete"
) -> dict[str, Any]:
    return {
        "source": "ph_launch",
        "rule": ANCHOR_RULE_VERSION,
        "status": status,
        "posts": [
            {"ph_post_id": pid, "created_at": created.isoformat(),
             "featured_at": None if feat is None else feat.isoformat(), "votes": votes,
             "comments": 1, "route": "slug", "confirmed": ok,
             "confirmation": "github_url" if ok else "unconfirmed:haiku_false",
             "rule": ANCHOR_RULE_VERSION}
            for pid, feat, created, ok in posts
        ],
    }  # fmt: skip


def bsky_sig(*times: tuple[datetime, str], status: str = "complete") -> dict[str, Any]:
    return {
        "source": "bsky_maintainer_posts",
        "rule": ANCHOR_RULE_VERSION,
        "status": status,
        "posts": [{"kind": "bluesky_maintainer_post", "time": t.isoformat(), "role": "maintainer",
                   "match": m} for t, m in times],
    }  # fmt: skip


def lookup(item: int, t: datetime, kind: str = "launch_hn") -> dict[str, Any]:
    return {"source": "hn_launch_lookup", "hn_item_id": item, "time": t.isoformat(),
            "points": 3, "kind": kind, "match": "url", "rule": ANCHOR_RULE_VERSION}  # fmt: skip


def test_all_five_kinds_are_launch_events_with_the_tie_break_order():
    t = d(6)
    ph = ph_sig(("77", t, t - timedelta(days=2), True))
    c = cand("org-f/five", [releases(("v1.0", t, True)), bsky_sig((t, "repo_url")), ph,
                            lookup(9102, t), show_hn(9101, t)])  # fmt: skip
    ev = launch_events(c, W0, W1)
    assert [e.kind for e in ev] == list(DECLARED_RULES) == [
        "show_hn", "launch_hn", "product_hunt", "release_launch", "bluesky_maintainer_post",
    ]  # fmt: skip
    assert ev[2].ref == "77" and ev[2].via == "product_hunt:slug"
    assert ev[4].ref == "repo_url#0" and ev[4].via == "bluesky:repo_url"
    a, why, rel = view_b_anchor(c, W0, W1)
    assert why is None and a is not None and a.rule == "show_hn"
    assert [r["kind"] for r in rel] == list(DECLARED_RULES[1:])


def test_earliest_event_anchors_later_ones_are_relaunches_and_ph_uses_featured_at():
    # Product Hunt: featuredAt when set (d(4)), not createdAt (d(2)); Bluesky earliest (d(3))
    c = cand("org-f/order", [
        show_hn(9101, d(9)), ph_sig(("77", d(4), d(2), True)),
        bsky_sig((d(3), "homepage_url"), (d(10), "repo_url")), releases(("v1", d(5), True)),
    ])  # fmt: skip
    a, _, rel = view_b_anchor(c, W0, W1)
    assert a is not None and (a.rule, a.at, a.ref, a.via) == (
        "bluesky_maintainer_post", d(3), "homepage_url#0", "bluesky:homepage_url",
    )  # fmt: skip
    assert [(r["kind"], r["at"]) for r in rel] == [
        ("product_hunt", d(4).isoformat()), ("release_launch", d(5).isoformat()),
        ("show_hn", d(9).isoformat()), ("bluesky_maintainer_post", d(10).isoformat()),
    ]  # fmt: skip
    # a PH post without featuredAt is dated by createdAt; unconfirmed posts never count
    c2 = cand("org-f/ph", [ph_sig(("78", None, d(2), True), ("79", None, d(1), False))])
    a2, _, rel2 = view_b_anchor(c2, W0, W1)
    assert a2 is not None and (a2.rule, a2.at, a2.ref) == ("product_hunt", d(2), "78")
    assert rel2 == []
    # Bluesky posts outside the window, or a status other than complete, are no events
    c3 = cand("org-f/late", [bsky_sig((d(8, 1, 2026), "repo_url"))])
    assert launch_events(c3, W0, W1) == []


def test_incomplete_bluesky_or_missing_ph_data_leave_no_anchor_and_are_counted():
    ok = [show_hn(9101, d(4))]
    inc = cand("org-i/inc", [*ok, bsky_sig(status="incomplete")])
    # without `required` (older callers) the anchor stands; with it, it can't be known
    a, _, _ = view_b_anchor(inc, W0, W1)
    assert a is not None
    a, why, rel = view_b_anchor(inc, W0, W1, required=("bluesky",))
    assert a is None and why == "launch_source_incomplete:bluesky" and rel == []
    missing = cand("org-i/miss", ok)
    assert view_b_anchor(missing, W0, W1, required=("product_hunt",))[1] == (
        "launch_source_incomplete:product_hunt"
    )
    assert view_b_anchor(missing, W0, W1, required=("bluesky",))[1] == (
        "launch_source_incomplete:bluesky"
    )
    none_declared = cand("org-i/none", [*ok, bsky_sig(status="no_declared_account"),
                                        ph_sig()])  # fmt: skip
    a, why, _ = view_b_anchor(none_declared, W0, W1, required=("bluesky", "product_hunt"))
    assert a is not None and why is None
    # the counts reach the view-B summaries
    cases = mixed_field()
    k = 0
    for i, c in enumerate(cases):
        if c.launch_case is not None and c.launch_case.anchor is not None and i % 9 == 0:
            why = "launch_source_incomplete:bluesky"
            nb = replace(c.launch_case, anchor=None, anchor_reason=why)
            cases[i] = replace(c, launch_case=nb)
            k += 1
    assert k >= 3
    sels = select_views(cases, ctx(), Definition.from_brief(B))
    assert sels.summary["view_b_incomplete"] == {"bluesky": k}
    assert sels.views["launch"].summary["incomplete_sources"] == {"bluesky": k}
    assert sels.summary["view_b_anchor_rules"]["none"] >= k


def test_ambiguous_ph_post_is_dropped_for_every_repo():
    a = cand("org-a/stellarlint", [ph_sig(("77", d(4), d(4), True))])
    b = cand("org-b/stellarlint", [ph_sig(("77", d(4), d(4), True))])
    drop = out.ambiguous_ph_posts([a, b])
    assert drop == {("gh:org-a/stellarlint", "ph:77"), ("gh:org-b/stellarlint", "ph:77")}
    assert launch_events(a, W0, W1, drop) == []


# --- PH votes and comments: secondary, never ranked on -----------------------------------------
# (the launch-window rule of ph_values replaced the "anchor - 7 days" rule in anchor-v8: see
# tests/unit/test_m22_round8.py)


def test_ph_votes_and_comments_are_reported_never_ranked():
    base = mixed_field()
    rnd = random.Random(5)

    def with_ph(c: Any) -> Any:
        lc = c.launch_case
        vals = {**lc.values, PH_VOTES: Value("observed", float(rnd.randint(0, 5000)), "verified"),
                PH_COMMENTS: Value("observed", float(rnd.randint(0, 50)), "verified")}  # fmt: skip
        return replace(c, launch_case=replace(lc, values=vals))

    varied = [with_ph(c) for c in base]
    s0 = select(base, ctx(VIEW_LAUNCH), Definition.from_brief(B))
    s1 = select(varied, ctx(VIEW_LAUNCH), Definition.from_brief(B))
    assert [c.ref for c in s0.level.winners] == [c.ref for c in s1.level.winners]
    assert [(p.winner, p.loser) for p in s0.level.pairs] == [
        (p.winner, p.loser) for p in s1.level.pairs
    ]
    row = next(r for r in s1.cases if r["values"].get(PH_VOTES))
    assert "percentile" not in row["values"][PH_VOTES]
    assert s1.params["views"]["launch"]["secondary"] == [
        "att.hn_points",
        PH_VOTES,
        PH_COMMENTS,
        "att.reddit_reach",
    ]
    from pigtail.briefs.model import METRICS

    assert PH_VOTES not in {m for dim in METRICS.values() for m in dim}


# --- versions, parameters, guard, determinism, estimate ----------------------------------------
def test_versions_and_the_launch_source_flags_in_the_params(launch_sources_on, monkeypatch):
    assert SELECTION_VERSION == "selection-v11" and ANCHOR_RULE_VERSION == "anchor-v10"
    c = Context.from_brief(B)
    assert c.product_hunt and c.bluesky and c.ph_topics == ("open-source", "developer-tools")
    assert c.required_launch_sources == ("product_hunt", "bluesky")
    p = c.params()
    assert p["selection_version"] == "selection-v11" and p["anchor_rule_version"] == "anchor-v10"
    lsp = p["launch_sources"]
    assert lsp["product_hunt"]["applies"] is True and lsp["bluesky"]["applies"] is True
    assert lsp["product_hunt"]["topics"] == ["open-source", "developer-tools"]
    assert "without hyphens" in lsp["product_hunt"]["slug_rule"]
    assert lsp["product_hunt"]["confirmation"]["haiku"]["prompt_id"] == "ph_match_check"
    assert lsp["product_hunt"]["confirmation"]["haiku"]["model"] == "claude-haiku-4-5-20251001"
    assert lsp["bluesky"]["incomplete_max_share"] == 0.10
    assert list(p["view_b_anchor"]["rules"]) == list(ANCHOR_RULE_LABELS)
    assert "product_hunt < release_launch < bluesky_maintainer_post" in p["view_b_anchor"][
        "tie_break"]  # fmt: skip
    assert not any("not collected" in x for x in p["view_b_anchor"]["limitations"])
    h_on = sha256_json(p)
    # turning a source off, or changing the topics, changes the pre-registered hash
    monkeypatch.setenv("PIGTAIL_SELECTION_BLUESKY", "false")
    off = Context.from_brief(B)
    assert off.required_launch_sources == ("product_hunt",)
    assert any("Bluesky maintainer posts: not collected" in x for x in off.view_b_limitations())
    assert sha256_json(off.params()) != h_on
    monkeypatch.delenv("PIGTAIL_SELECTION_BLUESKY")
    monkeypatch.setenv("PIGTAIL_PH_TOPICS", "developer-tools")
    assert Context.from_brief(B).ph_topics == ("developer-tools",)
    assert sha256_json(Context.from_brief(B).params()) != h_on
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "maybe")
    with pytest.raises(selmod.SelectionError):
        Context.from_brief(B)


def test_the_guard_covers_the_launch_source_code(monkeypatch):
    names = set(out.ANCHOR_RULE_FUNCTIONS)
    for f in ("ph_launches", "incomplete_source", "pigtail.briefs.launch_sources:run_bluesky",
              "pigtail.briefs.launch_sources:run_product_hunt",
              "pigtail.briefs.confirm:ph_confirm_by_rules",
              "pigtail.connectors.bluesky:declared_accounts",
              "pigtail.connectors.producthunt:ProductHuntConnector"):  # fmt: skip
        assert f in names, f
    h0 = sha256_json(Context.from_brief(B).params())
    for mod, name, value in (
        (conf, "ph_confirm_by_rules", lambda **k: None),
        (ls, "ph_slug_candidates", lambda full: []),
        (ls, "BSKY_INCOMPLETE_MAX_SHARE", 0.2),
        (selmod, "DECLARED_RULES", ("show_hn",)),
        (out, "ph_values", lambda *a: None),
    ):
        monkeypatch.setattr(mod, name, value)
        assert sha256_json(Context.from_brief(B).params()) != h0, name
        monkeypatch.undo()
    assert sha256_json(Context.from_brief(B).params()) == h0


def test_recompute_is_deterministic_with_the_new_kinds():
    cases = mixed_field()
    for i, c in enumerate(cases):
        lc = c.launch_case
        if lc is not None and lc.anchor is not None and i % 5 == 0:
            rule = "product_hunt" if i % 10 == 0 else "bluesky_maintainer_post"
            a = Anchor("launch", lc.anchor.at, "hour", rule, "x", rule, f"r{i}")
            cases[i] = replace(c, launch_case=replace(lc, anchor=a))
    s1 = select_views(cases, ctx(), Definition.from_brief(B))
    shuffled = list(cases)
    random.Random(3).shuffle(shuffled)
    s2 = select_views(shuffled, ctx(), Definition.from_brief(B))
    assert s1.result_hash == s2.result_hash and s1.inputs_hash == s2.inputs_hash
    counts = s1.summary["view_b_anchor_rules"]
    assert counts["product_hunt"] > 0 and counts["bluesky_maintainer_post"] > 0
    assert s1.views["launch"].summary["reference_population_n"] == sum(
        counts[k] for k in DECLARED_RULES
    )


def test_estimate_counts_product_hunt_and_bluesky(launch_sources_on):
    e = estimate(B, selection=SelectionState(pending=True, shortlisted=114))
    s = e.selection
    # no cache known (no database): the whole window, at the planning density, capped per topic
    # (ADR-085 addendum 4)
    per_day = est.PH_TOPIC_POSTS_PER_MONTH / 20 / est.DAYS_PER_MONTH
    pages = (
        2 * B.window.months * min(ls.PH_TOPIC_MAX_PAGES, math.ceil(est.DAYS_PER_MONTH * per_day))
    )
    assert s["producthunt_topic_pages"] == pages
    assert s["producthunt_requests"] == 2 * 114 + math.ceil(114 * 0.05) + pages
    assert e.other_requests["producthunt"] == s["producthunt_requests"]
    assert s["ph_match_checks"] == math.ceil(114 * 0.05) == 6
    assert s["bluesky_requests"] == 2 * math.ceil(114 * 0.3) == e.other_requests["bluesky"]
    assert s["bluesky_github_core_requests"] == 114 + math.ceil(114 * 0.1)
    assert s["bluesky_github_graphql_requests"] == 3
    pc = {x.stage: x for x in e.stages}["ph_match_check"]
    assert pc.llm_calls == 6 and pc.model == "claude-haiku-4-5-20251001" and pc.usd
    scope = run_scope(e, ("selection",))
    assert "Product Hunt" in scope["selection"]["launch_sources"]
    assert scope["selection"]["ph_calls"] == 6
    off = estimate(B, selection=SelectionState(pending=True, shortlisted=114),
                   launch_sources=(False, False))  # fmt: skip
    assert off.other_requests["producthunt"] == 0 and off.other_requests["bluesky"] == 0
    done = estimate(B, selection=SelectionState(pending=True, shortlisted=114, ph_done=114,
                                                ph_topics_done=True, bsky_done=114))  # fmt: skip
    assert done.other_requests["producthunt"] == 0 and done.other_requests["bluesky"] == 0


def test_estimate_warns_when_a_source_applies_but_cannot_run():
    ws = launch_source_warnings({})
    assert any("PH_API_TOKEN" in w for w in ws) and len(ws) == 1
    assert launch_source_warnings({"PH_API_TOKEN": "x"}) == []
    ws = launch_source_warnings(
        {"PH_API_TOKEN": "x", "PIGTAIL_CONNECTOR_BLUESKY_SEARCH_ENABLED": "false"}
    )
    assert len(ws) == 1 and "bluesky_search" in ws[0]
    assert (
        launch_source_warnings(
            {"PIGTAIL_SELECTION_PRODUCT_HUNT": "false", "PIGTAIL_SELECTION_BLUESKY": "0"}
        )
        == []
    )


def test_candidate_with_old_rule_signals_is_ignored():
    old = ph_sig(("77", d(4), d(4), True))
    old["rule"] = "anchor-v5"
    c = Candidate("gh:o/r", "o/r", sources=[old, {**bsky_sig((d(3), "repo_url")), "rule": "x"}])
    assert launch_events(c, W0, W1) == []
