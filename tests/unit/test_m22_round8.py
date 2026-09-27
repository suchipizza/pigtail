"""ADR-085 addendum 3 (the owner's decisions after verifier M22 round 7): a declared maintainer's
Bluesky post is a view-B launch event only with launch wording (the release rule's pattern, in
memory); the README declares an account only when it names exactly one; a declared launch
before the window leaves no view-B anchor (`launched_before_window`, counted per kind); Product
Hunt votes and comments come only from a post in the anchor's days 0..2. Unit tests with fakes
only (no network, no database); every name, handle and DID is made up.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pigtail.briefs import launch_sources as ls
from pigtail.briefs import outcomes as out
from pigtail.briefs import selection as selmod
from pigtail.briefs.launch_sources import bsky_post_is_launch, declared_account_sources
from pigtail.briefs.model import sha256_json
from pigtail.briefs.outcomes import launch_events, launch_events_before, ph_values, view_b_anchor
from pigtail.briefs.selection import (
    ANCHOR_COUNT_LABELS,
    ANCHOR_RULE_SOURCE_SHA256,
    ANCHOR_RULE_VERSION,
    PRE_WINDOW_LABELS,
    PRE_WINDOW_REASON,
    RELEASE_LAUNCH_PATTERN,
    SELECTION_VERSION,
    Context,
    Definition,
    anchor_rule_counts,
    select_views,
)
from pigtail.connectors.bluesky import parse_search_page
from tests.launch_sources_fake import DID_B, HANDLE_A, HANDLE_C, bsky_post
from tests.unit.test_m22_round6 import W0, W1, cand, mixed_field, releases, show_hn
from tests.unit.test_m22_round7 import bsky_sig, lookup, ph_sig
from tests.unit.test_m22_views import B, ctx


def d(month: int, day: int = 1, year: int = 2025, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


BEFORE = d(11, 5, 2024)  # before the window (W0 = 2025-01-01)


# --- 1. Bluesky: launch wording ---------------------------------------------------------------
def test_bluesky_launch_wording_reuses_the_release_pattern():
    assert out.RELEASE_LAUNCH_RE.pattern == RELEASE_LAUNCH_PATTERN
    for text in ("Launching SYNTH-TOOL today", "Introducing our linter", "announcing v1",
                 "our first public release is out", "pre-launch notes", "LAUNCH day"):  # fmt: skip
        assert bsky_post_is_launch(text), text
    for text in ("new docs page", "we relaunched", "launched last year", "the launcher",
                 "", None):  # fmt: skip
        assert not bsky_post_is_launch(text), text


def test_parse_search_page_exposes_the_text_in_memory_only():
    raw = bsky_post(1, HANDLE_A, d(5), ["https://github.com/o/r"], text="SYNTH-ZEPHYR launching")
    import json

    posts, _ = parse_search_page(json.dumps({"posts": [raw]}).encode())
    (p,) = posts
    assert p.text == "SYNTH-ZEPHYR launching" and bsky_post_is_launch(p.text)
    assert "SYNTH-ZEPHYR" not in repr(p)  # not in reprs or logs of the post


def test_only_launch_worded_bluesky_posts_are_events_and_relaunches():
    # the stored signal holds launch-worded posts only; unworded ones are a number
    sig = bsky_sig((d(3), "repo_url"), (d(6), "repo_url"))
    sig["posts_without_launch_wording"] = 4
    c = cand("org-w/worded", [sig])
    a, why, rel = view_b_anchor(c, W0, W1)
    assert why is None and a is not None and a.rule == "bluesky_maintainer_post" and a.at == d(3)
    assert [r["kind"] for r in rel] == ["bluesky_maintainer_post"]  # the other worded post only


# --- 2. declared accounts: the README names exactly one ----------------------------------------
def test_readme_counts_only_with_exactly_one_account():
    one = f"# Tool\nFollow @{HANDLE_C} and https://bsky.app/profile/{HANDLE_C.upper()}\n"
    accounts, kinds, amb = declared_account_sources([("readme", [one])])
    assert accounts == [HANDLE_C] and kinds == ["readme"] and amb is False  # distinct: one
    two = f"@{HANDLE_C} and https://bsky.app/profile/{HANDLE_A}"
    accounts, kinds, amb = declared_account_sources([("readme", [two])])
    assert accounts == [] and kinds == [] and amb is True
    # the profile, org page and homepage always count, whatever their number of accounts
    both = f"https://bsky.app/profile/{HANDLE_A} https://bsky.app/profile/{DID_B}"
    for kind in ("github_profile", "org_page", "homepage"):
        accounts, kinds, amb = declared_account_sources([(kind, [both]), ("readme", [two])])
        assert accounts == [HANDLE_A, DID_B] and kinds == [kind] and amb is True
    # a README account already declared elsewhere adds nothing new (the kind isn't counted)
    accounts, kinds, amb = declared_account_sources(
        [("github_profile", [f"https://bsky.app/profile/{HANDLE_C}"]), ("readme", [one])]
    )
    assert accounts == [HANDLE_C] and kinds == ["github_profile"] and amb is False
    assert ls.README_MAX_ACCOUNTS == 1


# --- 3. a declared launch before the window ---------------------------------------------------
def test_pre_window_show_hn_and_in_window_release_leave_no_anchor():
    c = cand("org-p/early", [show_hn(9101, BEFORE), releases(("v1.0", d(3), True))])
    assert [e.kind for e in launch_events(c, W0, W1)] == ["release_launch"]
    assert [(e.kind, e.ref) for e in launch_events_before(c, W0)] == [("show_hn", "9101")]
    a, why, rel = view_b_anchor(c, W0, W1)
    assert a is None and why == PRE_WINDOW_REASON == "launched_before_window" and rel == []


def test_pre_window_launch_worded_bluesky_post_leaves_no_anchor():
    c = cand("org-p/bsky", [bsky_sig((BEFORE, "homepage_url"), (d(4), "repo_url")),
                            lookup(9102, d(5))])  # fmt: skip
    a, why, rel = view_b_anchor(c, W0, W1)
    assert a is None and why == PRE_WINDOW_REASON and rel == []
    (e,) = launch_events_before(c, W0)
    assert e.kind == "bluesky_maintainer_post" and e.via == "bluesky:homepage_url"


def test_other_pre_window_events_and_non_launches():
    # a pre-window Product Hunt post counts when confirmed; an unconfirmed one never does
    ok = cand("org-p/ph", [ph_sig(("77", BEFORE, BEFORE, True)), show_hn(9101, d(4))])
    assert view_b_anchor(ok, W0, W1)[1] == PRE_WINDOW_REASON
    no = cand("org-p/ph2", [ph_sig(("78", BEFORE, BEFORE, False)), show_hn(9101, d(4))])
    assert view_b_anchor(no, W0, W1)[0] is not None
    # a release before the window without launch wording is no launch: the in-window one anchors
    rel = cand("org-p/rel", [releases(("v0.1", BEFORE, False), ("v1.0", d(3), True))])
    a, why, _ = view_b_anchor(rel, W0, W1)
    assert why is None and a is not None and a.rule == "release_launch"
    # a lookup Launch HN before the window counts (the lookup now searches from HN's epoch)
    lk = cand("org-p/lk", [lookup(9103, BEFORE), releases(("v1.0", d(3), True))])
    assert view_b_anchor(lk, W0, W1)[1] == PRE_WINDOW_REASON
    # the pre-window rule comes before the incomplete-source rule (an unread source could only
    # hold an even earlier event)
    inc = cand("org-p/inc", [show_hn(9101, BEFORE), bsky_sig(status="incomplete")])
    assert view_b_anchor(inc, W0, W1, required=("bluesky",))[1] == PRE_WINDOW_REASON
    # the undeclared rule is unchanged: its before-window case keeps its own reason
    und = cand("org-p/und", [releases(("v0.1", BEFORE, False)),
                             {"source": "hn_first_mention", "rule": ANCHOR_RULE_VERSION,
                              "status": "none", "requests": 1}])  # fmt: skip
    assert view_b_anchor(und, W0, W1)[1] == "undeclared:before_window"


def pre_window_cases() -> tuple[list[Any], dict[str, int]]:
    cases = mixed_field()
    want = dict.fromkeys(PRE_WINDOW_LABELS, 0)
    kinds = ["show_hn", "bluesky_maintainer_post", "release_launch"]
    k = 0
    for i, c in enumerate(cases):
        lc = c.launch_case
        if lc is not None and lc.anchor is not None and i % 7 == 1:
            kind = kinds[k % len(kinds)]
            pre = {"at": BEFORE.isoformat(), "kind": kind, "ref": f"r{i}", "via": "x"}
            nb = replace(lc, anchor=None, anchor_reason=PRE_WINDOW_REASON, pre_window_launch=pre)
            cases[i] = replace(c, launch_case=nb)
            want[f"{PRE_WINDOW_REASON}:{kind}"] += 1
            k += 1
    assert k >= 3
    return cases, want


def test_launched_before_window_is_counted_per_rule():
    assert list(ANCHOR_COUNT_LABELS[-len(PRE_WINDOW_LABELS) :]) == list(PRE_WINDOW_LABELS)
    cases, want = pre_window_cases()
    sels = select_views(cases, ctx(), Definition.from_brief(B))
    counts = sels.summary["view_b_anchor_rules"]
    assert {k: counts[k] for k in PRE_WINDOW_LABELS} == want
    assert counts["none"] >= sum(want.values())  # a part of `none`
    assert anchor_rule_counts([c.for_view_b() for c in cases]) == counts
    rows = {r["candidate_ref"]: r for r in sels.views["launch"].cases}
    pre = [r for r in rows.values() if r.get("anchor_reason") == PRE_WINDOW_REASON]
    assert len(pre) == sum(want.values())
    assert all(r["anchor_rule"] == "none" and r["pre_window_launch"]["kind"] for r in pre)


def test_recompute_is_deterministic_with_pre_window_cases():
    cases, _ = pre_window_cases()
    s1 = select_views(cases, ctx(), Definition.from_brief(B))
    shuffled = list(cases)
    random.Random(8).shuffle(shuffled)
    s2 = select_views(shuffled, ctx(), Definition.from_brief(B))
    assert s1.result_hash == s2.result_hash and s1.inputs_hash == s2.inputs_hash
    # the pre-window event is part of the inputs: another kind, another inputs hash
    i = next(i for i, c in enumerate(cases) if c.launch_case.pre_window_launch is not None)
    lc = cases[i].launch_case
    other = {**lc.pre_window_launch, "kind": "product_hunt"}
    cases[i] = replace(cases[i], launch_case=replace(lc, pre_window_launch=other))
    assert select_views(cases, ctx(), Definition.from_brief(B)).inputs_hash != s1.inputs_hash


# --- 4. Product Hunt votes and comments: days 0..2 of the anchor only ---------------------------
def test_ph_values_come_from_the_launch_window_only():
    posts = [
        (d(5, 1), "2", "topic", 80, None),  # endpoint day 2025-05-01 (05:00 PDT)
        (d(3), "1", "slug", 50, 7),
        (d(1), "3", "slug", 900, 1),
        # 06:00 UTC on 4 May is 23:00 PDT on 3 May: endpoint day 3 May (the launch-size mapping)
        (datetime(2025, 5, 4, 6, tzinfo=UTC), "4", "slug", 60, 9),
    ]
    v, n = ph_values(posts, date(2025, 5, 1))  # days 1, 2, 3 May
    assert v.status == "observed" and v.value == 80.0 and n.status == "unknown"
    v, n = ph_values(posts, date(2025, 5, 2))  # days 2, 3, 4 May: only post 4
    assert (v.value, n.value) == (60.0, 9.0) and v.reason == "as of fetch"
    v, n = ph_values(posts, date(2025, 4, 25))  # days 25..27 April: none
    assert v.status == n.status == "unknown" and v.reason == "no_ph_post_in_launch_window"
    v, _ = ph_values(posts, date(2025, 4, 29))  # 29 April .. 1 May: post 2
    assert v.value == 80.0
    v, _ = ph_values(posts, date(2025, 5, 4))  # 4..6 May: post 4 (3 May Pacific) is outside
    assert v.reason == "no_ph_post_in_launch_window"
    assert ph_values(None, date(2025, 5, 1))[0].reason == "product_hunt_not_collected"
    assert ph_values([], date(2025, 5, 1))[0].reason == "no_ph_post_in_launch_window"
    v, _ = ph_values(posts, date(2025, 1, 1))  # a tie of days: the most votes
    assert v.value == 900.0


# --- 5. versions, parameters and the guard ------------------------------------------------------
def test_versions_and_the_new_rules_in_the_params(launch_sources_on):
    assert SELECTION_VERSION == "selection-v10" and ANCHOR_RULE_VERSION == "anchor-v9"
    assert out.anchor_rule_source_sha256() == ANCHOR_RULE_SOURCE_SHA256
    p = Context.from_brief(B).params()
    assert p["selection_version"] == "selection-v10" and p["anchor_rule_version"] == "anchor-v9"
    vb = p["view_b_anchor"]
    assert "launched_before_window" in vb["pre_window_rule"]
    assert "topic scan" in vb["pre_window_rule"] and "HN's epoch" in vb["pre_window_rule"]
    assert vb["count_labels"] == list(ANCHOR_COUNT_LABELS)
    assert "launch-wording" in vb["rule"] and "exactly one account" in vb["rule"]
    bs = p["launch_sources"]["bluesky"]
    assert "release rule's pattern" in bs["launch_wording"]
    assert "exactly 1 account" in bs["readme_rule"] and "readme_ambiguous" in bs["readme_rule"]
    assert "no since/until" in bs["search"]
    ph = p["launch_sources"]["product_hunt"]
    assert "endpoint days 0..2" in ph["secondary_measures"]
    assert "no_ph_post_in_launch_window" in ph["secondary_measures"]
    assert "topic scan is bounded to the window" in ph["event_time"]
    assert "from HN's epoch to the window's start" in p["launch_lookup"]


def test_the_guard_covers_the_new_rules(monkeypatch):
    names = set(out.ANCHOR_RULE_FUNCTIONS)
    for f in ("launch_events_before", "ph_values", "_lookup_repo",
              "pigtail.briefs.launch_sources:bsky_post_is_launch",
              "pigtail.briefs.launch_sources:declared_account_sources"):  # fmt: skip
        assert f in names, f
    consts = set(out.ANCHOR_RULE_CONSTANTS)
    assert {"pigtail.briefs.launch_sources:README_MAX_ACCOUNTS",
            "pigtail.briefs.selection:PRE_WINDOW_REASON", "EARLIEST"} <= consts  # fmt: skip
    h0 = sha256_json(Context.from_brief(B).params())
    for mod, name, value in (
        (ls, "bsky_post_is_launch", lambda text: True),
        (ls, "README_MAX_ACCOUNTS", 2),
        (out, "launch_events_before", lambda *a, **k: []),
        (selmod, "PRE_WINDOW_REASON", "x"),
    ):
        monkeypatch.setattr(mod, name, value)
        assert sha256_json(Context.from_brief(B).params()) != h0, name
        monkeypatch.undo()
    assert sha256_json(Context.from_brief(B).params()) == h0


def test_old_rule_bluesky_signals_are_ignored():
    old = {**bsky_sig((d(3), "repo_url")), "rule": "anchor-v7"}
    c = cand("org-o/old", [old, show_hn(9101, d(5))])
    assert [e.kind for e in launch_events(c, W0, W1)] == ["show_hn"]
    assert launch_events_before(c, W0) == []
    assert timedelta(0) < W1 - W0
