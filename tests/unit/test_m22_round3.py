"""M22 verifier round 3 (ADR-081), pure parts: the launch lookup's matching rule, the day-
precision anchor comparison, the anchor rule in the pre-registered parameters, the selection's
"why so few winners" warnings and the anchor type stored per pair side. Synthetic names only.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from pigtail.analysis.bursts import Onset, day_start
from pigtail.briefs import selection as selmod
from pigtail.briefs.candidates import Candidate
from pigtail.briefs.estimate import HN_LAUNCH_LOOKUP_PER_SHORTLISTED, estimate
from pigtail.briefs.model import sha256_json
from pigtail.briefs.outcomes import (
    LAUNCH_LOOKUP_REQUESTS,
    LAUNCH_LOOKUP_SOURCE,
    Launch,
    _launches,
    ambiguous_title_matches,
    choose_anchor,
    endpoint_day,
    lookup_queries,
    match_launch_post,
)
from pigtail.briefs.selection import ANCHOR_RULE_VERSION, Anchor, Context, Definition, select
from pigtail.connectors.hn import ShowHNStory, normalize_github_repo
from tests.selection_fake import brief, population

REPO = "org-k/kubeforge"


def story(title: str | None, url: str | None = None, item: int = 1) -> ShowHNStory:
    return ShowHNStory(
        item_id=item,
        title=title,
        url=url,
        points=10,
        created_at=datetime(2025, 10, 1, tzinfo=UTC),
        repo_full_name=normalize_github_repo(url),
    )


# --- 1. matching ------------------------------------------------------------------------------
def test_url_match_is_case_insensitive_and_ignores_deeper_paths():
    # the verifier's case: the post links the repo with another capitalisation
    s = story("Show HN: A cluster tool", "https://github.com/KubeForge/KubeForge")
    assert match_launch_post(s, "kubeforge/kubeforge", created=None) == "url"
    s2 = story("Show HN: x", "https://www.GitHub.com/ORG-K/KubeForge/tree/main/docs")
    assert match_launch_post(s2, REPO, created=None) == "url"
    assert match_launch_post(s2, "Org-K/KubeForge", created=None) == "url"


# the title rule of anchor-v2 (a whole word anywhere) was replaced by ADR-082's conservative
# rule: see tests/unit/test_m22_round4.py


def test_lookup_queries_hold_only_the_repo_name():
    qs = lookup_queries("org-k/kubeforge")
    assert qs == [
        ("url", "github.com/org-k/kubeforge", "show_hn"),
        ("name", "kubeforge", "show_hn"),
        ("launch_hn", "kubeforge", "launch_hn"),
    ]
    assert len(qs) == LAUNCH_LOOKUP_REQUESTS == HN_LAUNCH_LOOKUP_PER_SHORTLISTED


def test_launches_merge_discovery_and_lookup_by_item_id():
    t1 = datetime(2025, 10, 1, 15, tzinfo=UTC)
    t2 = datetime(2025, 11, 1, 15, tzinfo=UTC)
    c = Candidate(
        ref=f"gh:{REPO}",
        repo_full_name=REPO,
        sources=[
            {"source": "show_hn", "term": "t", "hn_item_id": 11, "points": 5, "title": "x",
             "time": t1.isoformat()},
            {"source": LAUNCH_LOOKUP_SOURCE, "hn_item_id": 11, "points": 9, "kind": "show_hn",
             "match": "url", "time": t1.isoformat(), "rule": ANCHOR_RULE_VERSION},
            {"source": LAUNCH_LOOKUP_SOURCE, "hn_item_id": 12, "points": 3, "kind": "launch_hn",
             "match": "title", "time": t2.isoformat(), "rule": ANCHOR_RULE_VERSION},
            {"source": "github_keyword", "term": "t"},
        ],
    )  # fmt: skip
    start, end = datetime(2025, 3, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
    got = _launches(c, start, end)
    assert got == [
        Launch(t1, 11, 9, "show_hn", "lookup:url"),
        Launch(t2, 12, 3, "launch_hn", "lookup:title"),
    ]
    assert _launches(c, start, end, {(c.ref, 12)}) == got[:1]
    assert _launches(c, t2, end) == got[1:]  # outside the window: not a launch


def test_ambiguous_title_matches_are_dropped():
    def cand(name: str, *srcs: dict) -> Candidate:
        return Candidate(ref=f"gh:{name}", repo_full_name=name, sources=list(srcs))

    def lk(item: int, match: str) -> dict:
        return {"source": LAUNCH_LOOKUP_SOURCE, "hn_item_id": item, "match": match,
                "rule": ANCHOR_RULE_VERSION}  # fmt: skip

    a = cand("org-a/kubeforge", lk(1, "title"), lk(2, "title"), lk(3, "title"))
    b = cand("org-b/kubeforge", lk(1, "title"))  # same item named by title for two repos
    c = cand("org-c/other", lk(2, "url"))  # item 2 links org-c/other
    d = cand("org-d/x", {"source": "show_hn", "hn_item_id": 3, "term": "t"})
    assert ambiguous_title_matches([a, b, c, d]) == {
        ("gh:org-a/kubeforge", 1),
        ("gh:org-b/kubeforge", 1),
        ("gh:org-a/kubeforge", 2),
        ("gh:org-a/kubeforge", 3),
    }


# --- 2. same-day launch precedes a day-precision burst ---------------------------------------
def test_same_day_launch_anchors_on_the_launch():
    # 02:30 UTC on 1 Aug is 19:30 on 31 Jul in US Pacific; the burst's onset day is 31 Jul
    launch_at = datetime(2025, 8, 1, 2, 30, tzinfo=UTC)
    assert endpoint_day(launch_at) == date(2025, 7, 31)
    onset = Onset(date(2025, 7, 31), day_start(date(2025, 7, 31)), "day")
    assert launch_at > onset.at  # an instant comparison would call it later than the onset
    launch = Launch(launch_at, 7001, 40, "show_hn", "lookup:url")
    a, reason = choose_anchor([launch], [onset], True)
    assert a == Anchor("launch", launch_at, "hour", "show_hn", "lookup:url") and reason is None
    # a launch on the next endpoint day follows the burst: the burst anchors
    nxt = Launch(datetime(2025, 8, 1, 16, tzinfo=UTC), 7002, 40, "show_hn", "discovery")
    a2, _ = choose_anchor([nxt], [onset], True)
    assert a2 is not None and a2.type == "burst" and a2.precision == "day"
    # 30 endpoint days before the onset day still counts (rule 1), 31 does not
    early = Launch(datetime(2025, 7, 1, 20, tzinfo=UTC), 7003, 1, "launch_hn", "lookup:title")
    assert endpoint_day(early.at) == date(2025, 7, 1)
    a3, _ = choose_anchor([early], [onset], True)
    assert a3 is not None and a3.type == "launch" and a3.source == "launch_hn"
    too_early = replace(early, at=datetime(2025, 6, 30, 20, tzinfo=UTC))
    a4, _ = choose_anchor([too_early], [onset], True)
    assert a4 is not None and a4.type == "burst"  # rule 3 fails: the burst follows within 90 d


def test_hour_precision_onsets_still_compare_instants():
    at = datetime(2025, 7, 31, 18, tzinfo=UTC)
    onset = Onset(endpoint_day(at), at, "hour")
    before = Launch(at - timedelta(hours=1), 1, 1, "show_hn", "discovery")
    after = Launch(at + timedelta(hours=1), 2, 1, "show_hn", "discovery")
    assert choose_anchor([before], [onset], True)[0].type == "launch"  # type: ignore[union-attr]
    assert choose_anchor([after], [onset], True)[0].type == "burst"  # type: ignore[union-attr]
    tie = Launch(at, 3, 1, "show_hn", "discovery")  # rule 5: a tie is a launch
    assert choose_anchor([tie], [onset], True)[0].type == "launch"  # type: ignore[union-attr]


# --- 3. the anchor rule is in the pre-registered parameters ----------------------------------
def test_anchor_rule_is_part_of_the_selection_params_hash(monkeypatch):
    ctx = Context.from_brief(brief())
    p = ctx.params()
    assert p["selection_version"] == "selection-v4"
    assert p["anchor_rule_version"] == "anchor-v3"
    assert "day precision" in p["anchor_rule"] or "endpoint day" in p["anchor_rule"]
    assert "Launch" in p["launch_lookup"] and "tags=launch_hn" in p["launch_lookup"]
    h = sha256_json(p)
    monkeypatch.setattr(selmod, "ANCHOR_RULE_VERSION", "anchor-v4")
    assert sha256_json(ctx.params()) != h


# --- 4a. warnings say why there are few winners -----------------------------------------------
def test_warnings_name_the_small_population_and_missing_anchors():
    cases = population(24)
    small = [replace(c, anchor=None, anchor_reason="no_anchor") if i < 5 else c
             for i, c in enumerate(cases)]  # fmt: skip
    b = brief(minimums={}, primary_threshold="at_least_median")
    sel = select(small, Context.from_brief(b), Definition.from_brief(b), notes=["note from input"])
    w = sel.summary["warnings"]
    assert w[0] == "note from input"
    assert "no anchor: 5 of 24 shortlisted" in w
    assert "attention population 19 < 20 (minimum): no percentiles" in w
    assert sel.summary["counts"]["winners"] == 0
    ok = select(cases, Context.from_brief(b), Definition.from_brief(b))
    assert not any("population" in x or "no anchor" in x for x in ok.summary["warnings"])


# --- H1 note: each pair side's anchor type is stored ------------------------------------------
def test_pairs_store_each_sides_anchor_type():
    cases = population(60)
    burst = datetime(2025, 7, 20, tzinfo=UTC)
    mixed = [
        replace(c, anchor=Anchor("burst", burst + timedelta(days=i % 30), "day", "velocity-v0"))
        if i % 3 == 0
        else c
        for i, c in enumerate(cases)
    ]
    b = brief(minimums={}, primary_threshold="at_least_median")
    sel = select(mixed, Context.from_brief(b), Definition.from_brief(b))
    losers = [c for c in sel.cases if c["role"] == "matched_loser"]
    assert losers
    by = {c["candidate_ref"]: c for c in sel.cases}
    n_launch = 0
    for lo in losers:
        pr = lo["pair"]
        assert pr["anchor_type"] == lo["anchor"]["type"]
        (wn,) = [c for c in sel.cases if c["role"] == "winner" and c["pair"]
                 and c["pair"]["pair_id"] == pr["pair_id"]]  # fmt: skip
        assert pr["anchor_types"] == {"winner": wn["anchor"]["type"], "loser": pr["anchor_type"]}
        assert wn["pair"]["anchor_type"] == by[wn["candidate_ref"]]["anchor"]["type"]
        assert pr["launch_anchored"] is (set(pr["anchor_types"].values()) == {"launch"})
        n_launch += pr["launch_anchored"] and pr["headline"]
    pba = sel.summary["pairs_by_anchor"]
    assert sum(pba.values()) == len(losers) and set(pba) <= {
        "launch/launch", "launch/burst", "burst/launch", "burst/burst"
    }  # fmt: skip
    assert sel.summary["headline_pairs_launch_anchored"] == n_launch
    assert sel.summary["anchors"]["launch:show_hn"] + sel.summary["anchors"]["burst:velocity-v0"]


# --- the estimate counts the lookup's HN requests ----------------------------------------------
def test_estimate_counts_the_launch_lookup():
    e = estimate(brief())
    assert e.other_requests["hn_launch_lookup"] == e.shortlisted * 3
