"""M22 verifier round 4 (ADR-082), pure parts: the conservative title rule of the launch lookup
(every false match the verifier found is rejected; product-slot titles still match), the
creation-date rule, the `launch_hn` tag, the refusal message without the Show HN connector, the
anchor-rule versions and the guard that pins the anchor rule's code, and the estimate's launch
lookup while the selection is pending. Owners and titles are synthetic (`org-…`); only the
repo-name part of the verifier's cases is kept.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigtail.briefs import selection as selmod
from pigtail.briefs.cache import plan_rerun
from pigtail.briefs.candidates import Candidate
from pigtail.briefs.estimate import (
    HN_LAUNCH_LOOKUP_PER_SHORTLISTED,
    LAUNCH_LOOKUP_OFF_WARNING,
    SelectionState,
    estimate,
    launch_lookup_enabled,
    render_text,
    run_scope,
)
from pigtail.briefs.outcomes import (
    LAUNCH_LOOKUP_SOURCE,
    TITLE_STOPLIST,
    Launch,
    LaunchLookupUnavailable,
    _launches,
    ambiguous_title_matches,
    anchor_rule_source_sha256,
    classify_launch_post,
    launch_lookup_blocked,
    lookup_queries,
    match_launch_post,
    title_names_repo,
)
from pigtail.briefs.selection import (
    ANCHOR_RULE_SOURCE_SHA256,
    ANCHOR_RULE_VERSION,
    SELECTION_VERSION,
    Context,
)
from pigtail.connectors.hn import HNShowDiscoveryConnector, ShowHNStory, normalize_github_repo
from tests.selection_fake import brief

CREATED = datetime(2025, 3, 1, tzinfo=UTC)  # every repo here was created before its posts
POSTED = datetime(2025, 10, 1, 15, tzinfo=UTC)


def story(
    title: str | None, url: str | None = None, *, at: datetime = POSTED, item: int = 1
) -> ShowHNStory:
    return ShowHNStory(
        item_id=item,
        title=title,
        url=url,
        points=10,
        created_at=at,
        repo_full_name=normalize_github_repo(url),
    )


def classify(title: str | None, repo: str, url: str | None = None, **kw: datetime | None):
    created = kw.get("created", CREATED)
    return classify_launch_post(story(title, url, at=kw.get("at") or POSTED), repo, created=created)


# --- 1. every false match of the live acceptance run is rejected ------------------------------
@pytest.mark.parametrize(
    ("repo", "title", "why"),
    [
        # a common word inside another product's name
        ("org-s/studio", "Show HN: Visual Studio Code extension for X", "not_product_slot"),
        ("org-s/studio", "Show HN: Screen Studio – record your screen", "not_product_slot"),
        ("org-s/studio", "Launch HN: Screen Studio (YC W23)", "not_product_slot"),
        # a product slot, but the name is a common or generic word
        ("org-s/studio", "Show HN: Studio – a design tool", "common_word"),
        ("org-p/poly", "Launch HN: Poly (YC S22) – 3D textures", "short_name"),
        ("org-t/toml", "Show HN: A deploy tool with just TOML and webhooks", "not_product_slot"),
        ("org-t/toml", "Show HN: TOML – yet another parser", "short_name"),
        ("org-p/pick", "Show HN: Tile the windows you pick", "not_product_slot"),
        ("org-m/microwave", "Show HN: Microwave – an iOS app for timers", "common_word"),
        ("org-a/augur", "Show HN: Augur – a text RPG in the terminal", "common_word"),
        ("org-c/confetti", "Show HN: Confetti – a configuration language", "common_word"),
        ("org-j/json", "Show HN: A faster JSON parser", "not_product_slot"),
        ("org-j/json", "Show HN: JSON – a faster parser", "short_name"),
        # not the name itself in the slot
        ("org-k/kubeforge", "Show HN: I built KubeForge", "not_product_slot"),
        ("org-k/kubeforge", "Show HN: KubeForge plugin for Helm", "not_product_slot"),
        ("org-k/kubeforge", "Show HN: KubeForge 2 – clusters", "not_product_slot"),
        ("org-k/kubeforge", "Ask HN: KubeForge – is it good?", "not_product_slot"),
        ("org-k/kubeforge", "Why we Show HN: KubeForge", "not_product_slot"),
    ],
)
def test_verifier_false_matches_are_rejected(repo, title, why):
    assert classify(title, repo) == (None, why)
    assert match_launch_post(story(title), repo, created=CREATED) is None
    assert title_names_repo(title, repo) is False


def test_names_that_are_not_mentioned_are_not_counted_as_candidates():
    # "kubeforge-ui" and "Kubeforger" are other projects' names: not a title-only candidate
    for t in (
        "Show HN: kubeforge-ui, a dashboard",
        "Show HN: Kubeforger",
        "Show HN: MyKubeforge",
        "Show HN: KubeForge.io is live",  # a domain, not the name
    ):
        assert classify(t, "org-k/kubeforge") == (None, None)
    assert classify(None, "org-k/kubeforge") == (None, None)


def test_verifier_words_are_on_the_stop_list():
    for w in ("studio", "poly", "toml", "pick", "microwave", "augur", "confetti", "json"):
        assert w in TITLE_STOPLIST
    assert all(w == w.lower() and " " not in w for w in TITLE_STOPLIST)


# --- 2. product-slot titles still match -------------------------------------------------------
@pytest.mark.parametrize(
    ("repo", "title"),
    [
        ("org-k/kubeforge", "Show HN: KubeForge – clusters in one command"),
        ("org-k/kubeforge", "Show HN: KubeForge — clusters in one command"),
        ("org-k/kubeforge", "Show HN: KubeForge - clusters in one command"),
        ("org-k/kubeforge", "Show HN: kubeforge: clusters in one command"),
        ("org-k/kubeforge", "Show HN: KubeForge, clusters in one command"),
        ("org-k/kubeforge", "Show HN: KubeForge | clusters in one command"),
        ("org-k/kubeforge", "Show HN: KubeForge (open source)"),
        ("org-k/kubeforge", "Show HN: KubeForge"),
        ("org-k/kubeforge", "show hn:KubeForge"),
        ("org-a/acme-cli", "Launch HN: Acme-CLI (YC W24) – a CLI for invoices"),
        ("org-a/acme-cli", "Launch HN: Acme CLI (YC W24) – a CLI for invoices"),  # '-' = ' '
        ("org-a/acme-cli", "Show HN: Acme_CLI – a CLI for invoices"),
        ("org-k/kube_forge", "Show HN: Kube Forge – clusters"),  # '_' = ' '
        ("org-k/kube-forge", "Show HN: kube_forge: clusters"),
    ],
)
def test_product_slot_titles_match(repo, title):
    assert title_names_repo(title, repo)
    assert classify(title, repo) == ("title", None)
    assert classify(title, repo, "https://acme.example/launch") == ("title", None)  # not GitHub


def test_a_hyphen_attached_to_the_name_is_not_a_separator():
    assert classify("Show HN: Acme-CLI-Pro – a CLI", "org-a/acme-cli") == (None, None)
    assert classify("Show HN: Acme-CLI-Pro – a CLI", "org-a/acme-cli-pro")[0] == "title"


# --- 3. links, creation date, URL rule --------------------------------------------------------
def test_a_post_linking_another_repo_is_rejected_and_this_repo_is_a_url_match():
    t = "Show HN: KubeForge – clusters"
    assert classify(t, "org-k/kubeforge", "https://github.com/org-x/other") == (
        None,
        "links_other_repo",
    )
    assert classify(t, "org-k/kubeforge", "https://github.com/ORG-K/KubeForge") == ("url", None)
    # a URL match needs no title and no creation date (the A2 rule, unchanged)
    s = story("Show HN: something else", "https://github.com/org-k/kubeforge/tree/main")
    assert classify_launch_post(s, "org-k/kubeforge", created=None) == ("url", None)


def test_title_matches_need_a_post_no_earlier_than_a_day_before_creation():
    t, repo = "Show HN: KubeForge – clusters", "org-k/kubeforge"
    created = datetime(2025, 10, 5, 12, tzinfo=UTC)
    ok = created - timedelta(hours=23)  # within the 1-day tolerance
    assert classify(t, repo, created=created, at=ok) == ("title", None)
    assert classify(t, repo, created=created, at=created - timedelta(days=1)) == ("title", None)
    early = created - timedelta(days=1, seconds=1)
    assert classify(t, repo, created=created, at=early) == (None, "before_creation")
    assert classify(t, repo, created=None) == (None, "no_creation_date")  # unknown: rejected


# --- 4. rule (e) and records of an earlier rule -----------------------------------------------
def test_shared_title_matches_are_still_dropped_and_old_rule_records_ignored():
    def lk(item: int, match: str, rule: str | None = ANCHOR_RULE_VERSION) -> dict:
        rec = {"source": LAUNCH_LOOKUP_SOURCE, "hn_item_id": item, "match": match,
               "time": POSTED.isoformat(), "kind": "show_hn", "points": 3}  # fmt: skip
        if rule is not None:
            rec["rule"] = rule
        return rec

    a = Candidate(ref="gh:org-a/kubeforge", repo_full_name="org-a/kubeforge",
                  sources=[lk(1, "title"), lk(2, "title", rule=None)])  # fmt: skip
    b = Candidate(ref="gh:org-b/kubeforge", repo_full_name="org-b/kubeforge",
                  sources=[lk(1, "title")])  # fmt: skip
    assert ambiguous_title_matches([a, b]) == {(a.ref, 1), (b.ref, 1)}
    start, end = POSTED - timedelta(days=90), POSTED + timedelta(days=90)
    # item 1 is shared (dropped), item 2 was stored under anchor-v2 (ignored)
    assert _launches(a, start, end, ambiguous_title_matches([a, b])) == []
    assert _launches(a, start, end) == [Launch(POSTED, 1, 3, "show_hn", "lookup:title")]


# --- 5. the launch_hn tag, the refusal without the connector ----------------------------------
def test_lookup_uses_the_launch_hn_tag():
    assert lookup_queries("org-a/acme-cli") == [
        ("url", "github.com/org-a/acme-cli", "show_hn"),
        ("name", "acme-cli", "show_hn"),
        ("launch_hn", "acme-cli", "launch_hn"),
    ]


def test_connector_accepts_launch_hn_and_refuses_all_story_searches(tmp_path: Path):
    from pigtail.capture.snapshots import LocalSnapshotStore

    hn = HNShowDiscoveryConnector(store=LocalSnapshotStore(tmp_path), pseudonymizer=None, env={})
    with pytest.raises(ValueError, match="unsupported tags"):
        hn.search_show_hn("x", since=None, until=POSTED, tags="story")


def test_refusal_names_the_show_hn_connector_flag():
    msg = launch_lookup_blocked(None)
    assert msg is not None and "PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED" in msg
    assert "PIGTAIL_ENABLE_HN" not in msg
    assert issubclass(LaunchLookupUnavailable, selmod.SelectionError)

    class Off:
        enabled = False

    assert launch_lookup_blocked(Off()) == msg
    assert launch_lookup_enabled({}) is True  # on by default
    assert launch_lookup_enabled({"PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED": "false"}) is False
    assert launch_lookup_enabled({"PIGTAIL_ENABLE_HN": "0"}) is True  # another connector's flag
    assert "PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED" in LAUNCH_LOOKUP_OFF_WARNING


# --- 6. versions, the rule text in the hash, and the guard ------------------------------------
def test_versions_and_title_rule_are_in_the_selection_params():
    assert SELECTION_VERSION == "selection-v4" and ANCHOR_RULE_VERSION == "anchor-v3"
    p = Context.from_brief(brief()).params()
    assert p["selection_version"] == "selection-v4" and p["anchor_rule_version"] == "anchor-v3"
    rule = p["launch_lookup"]
    for part in ("tags=launch_hn", "(Show|Launch) HN:", ">= 5 characters", "1 day before",
                 "links no GitHub repo", "claimed by two", "refused"):  # fmt: skip
        assert part in rule, part


def test_anchor_rule_source_is_pinned():
    got = anchor_rule_source_sha256()
    assert got == ANCHOR_RULE_SOURCE_SHA256, (
        "the anchor rule's code changed (choose_anchor, match_launch_post, title_names_repo or a "
        "helper/constant in outcomes.ANCHOR_RULE_FUNCTIONS / ANCHOR_RULE_CONSTANTS). A changed "
        "anchor rule changes the pre-registered selection rule: bump ANCHOR_RULE_VERSION (and "
        "SELECTION_VERSION) in pigtail/briefs/selection.py, record why in an ADR, and set "
        f"ANCHOR_RULE_SOURCE_SHA256 = {got!r}"
    )


def test_guard_ignores_docstrings_but_not_code(monkeypatch):
    import pigtail.briefs.outcomes as out

    base = anchor_rule_source_sha256()
    monkeypatch.setattr(out, "TITLE_MIN_CHARS", 4)
    assert anchor_rule_source_sha256() != base
    monkeypatch.undo()
    monkeypatch.setattr(out, "TITLE_STOPLIST", out.TITLE_STOPLIST - {"augur"})
    assert anchor_rule_source_sha256() != base


# --- 7. the estimate counts the lookup while the selection is pending --------------------------
def test_estimate_counts_the_lookup_whatever_the_reuse_plan_says():
    v1 = v2 = brief()  # e.g. a carried-forward version: every stage "reuse"
    plan = plan_rerun(v1, v2)
    assert "evidence" in plan.fully_reused()  # the reason round 3's estimate said 0
    e = estimate(v2, plan=plan, selection=SelectionState(pending=True, shortlisted=114))
    assert e.other_requests["hn_launch_lookup"] == 114 * HN_LAUNCH_LOOKUP_PER_SHORTLISTED
    sort = {d["stage"]: d for d in (e.reuse or {})["stages"]}["outcome_sort"]
    assert sort["action"] == "recompute" and "not yet run" in sort["because"][0]
    scope = run_scope(e, ("selection",))
    assert scope["selection"]["hn_algolia_requests"] == 342
    # repos already looked up (run checkpoint) are not counted again
    part = estimate(v2, plan=plan, selection=SelectionState(True, 114, lookup_done=100))
    assert part.other_requests["hn_launch_lookup"] == 14 * HN_LAUNCH_LOOKUP_PER_SHORTLISTED
    # a done selection costs nothing; without a state the planning estimate is used
    done = estimate(v2, plan=plan, selection=SelectionState(pending=False))
    assert done.other_requests["hn_launch_lookup"] == 0
    assert estimate(v2).other_requests["hn_launch_lookup"] == 3 * estimate(v2).shortlisted
    assert estimate(v2).selection["hn_launch_lookup_requests"] > 0


def test_estimate_warns_when_the_show_hn_connector_is_off():
    b = brief()
    e = estimate(b, hn_enabled=False)
    assert e.warnings == [LAUNCH_LOOKUP_OFF_WARNING]
    assert e.to_dict()["warnings"] == [LAUNCH_LOOKUP_OFF_WARNING]
    assert f"warning: {LAUNCH_LOOKUP_OFF_WARNING}" in render_text(e, b)
    assert run_scope(e, ("selection",))["selection"]["warnings"] == [LAUNCH_LOOKUP_OFF_WARNING]
    assert estimate(b).warnings == []
    assert estimate(b, hn_enabled=False, selection=SelectionState(pending=False)).warnings == []
