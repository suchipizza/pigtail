"""Owner decisions of 2026-09-27 after M22 verifier round 6 (ADR-084), pure parts: E rule 2 never
confirms a login that overlaps the repo name, the resolved Haiku model in the parameters, the
numeric-only headline exclusion, the language groups and the same-language-group sensitivity,
the distribution-surface balance rows, view B's launch-event anchor (the release-launch
wording, relaunch events, undeclared first mention vs first release, flags, per-rule counts, no
star data), the calipers' SD over the full shortlisted pool, view C over every non-winner, the
version bump and the guard, recompute determinism and the estimate. Every repo, title and tag
is synthetic (`org-…`).
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pigtail.briefs import confirm as conf
from pigtail.briefs import outcomes as out
from pigtail.briefs import selection as selmod
from pigtail.briefs.candidates import Candidate
from pigtail.briefs.confirm import (
    CONFIRMATION_VERSION,
    confirm_by_rules,
    confirmation_params,
    owner_named,
    owner_overlaps_name,
)
from pigtail.briefs.estimate import SelectionState, estimate, run_scope
from pigtail.briefs.model import sha256_json
from pigtail.briefs.outcomes import (
    LaunchEvent,
    choose_launch_anchor,
    first_release_at,
    launch_events,
    release_is_launch,
    view_b_anchor,
)
from pigtail.briefs.selection import (
    ANCHOR_RULE_LABELS,
    ANCHOR_RULE_VERSION,
    LANGUAGE_GROUPS,
    SELECTION_VERSION,
    VIEW_FOLLOW_THROUGH,
    VIEW_LAUNCH,
    VIEW_LAUNCH_UNDECLARED,
    VIEW_PLAIN,
    Anchor,
    CaseInput,
    Context,
    Covariates,
    Definition,
    _pair_diffs,
    anchor_rule_counts,
    language_group,
    pool_sds,
    select,
    select_views,
)
from pigtail.briefs.surface import (
    INSTALL_PATHS,
    PROMPT,
    SURFACES,
    SurfaceOutput,
    surface_input,
    surface_of,
    surface_params,
)
from pigtail.llm.types import schema_hash
from tests.unit.test_m22_views import B, ctx, split_field, vcase

W0 = datetime(2025, 1, 1, tzinfo=UTC)
W1 = datetime(2026, 6, 30, tzinfo=UTC)


# --- 1a. E rule 2 ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("full", "title", "url"),
    [
        ("preflight/preflight", "Show HN: Preflight – checks before takeoff", None),
        ("preflight/preflight", "Show HN: Preflight", "https://preflight.example/launch"),
        ("kubeforge/kubeforge", "Show HN: KubeForge – clusters by kubeforge", None),
        ("acme/acme-cli", "Show HN: Acme CLI – from acme", "https://acme.example"),
        ("acmelabs/acme", "Show HN: Acme – by AcmeLabs", None),  # the login contains the name
    ],
)
def test_rule_2_never_confirms_a_login_that_overlaps_the_repo_name(full: str, title: str, url: Any):
    """Verifier round 6: a namesake post names the login too, so rule 2 says nothing; such
    matches fall through to rules 3 and 4."""
    assert owner_overlaps_name(full)
    assert not owner_named(title, url, full)
    how = confirm_by_rules(
        full_name=full, description=None, homepage_domain=None, title=title, url=url
    )
    assert how is None  # rules 3 (no description) and then 4 (Haiku) decide


def test_rule_2_still_confirms_a_distinct_login_and_rule_3_still_applies_after_overlap():
    assert not owner_overlaps_name("orgtally/tallyho")
    assert owner_named("Show HN: Tallyho – made at OrgTally", None, "orgtally/tallyho")
    # an overlapping login falls through to rule 3 when the description shares two keywords
    how = confirm_by_rules(
        full_name="preflight/preflight",
        description="Checklists for aircraft maintenance crews",
        homepage_domain=None,
        title="Show HN: Preflight – aircraft maintenance checklists",
        url=None,
    )
    assert how == "description_keywords"


def test_confirmation_version_and_the_resolved_model_in_the_params(monkeypatch):
    assert CONFIRMATION_VERSION == "confirm-v2"
    p = confirmation_params()
    assert p["version"] == "confirm-v2" and "contained in the repo name" in p["rule"]
    assert p["haiku"]["model"] == "claude-haiku-4-5-20251001"
    h0 = sha256_json(Context.from_brief(B).params())
    assert Context.from_brief(B).params()["distribution_surface"]["model"] == p["haiku"]["model"]
    monkeypatch.setenv("LLM_MODEL_RELEVANCE", "claude-haiku-synthetic-9")
    p2 = Context.from_brief(B).params()
    assert p2["title_confirmation"]["haiku"]["model"] == "claude-haiku-synthetic-9"
    assert p2["distribution_surface"]["model"] == "claude-haiku-synthetic-9"
    assert sha256_json(p2) != h0  # a changed model changes the pre-registered hash


# --- 2. headline exclusion, language groups ------------------------------------------------------
def test_language_groups_are_fixed_and_exact():
    assert LANGUAGE_GROUPS == {
        "JavaScript": "js_ts", "TypeScript": "js_ts", "Python": "python", "Go": "go",
        "Rust": "rust",
    }  # fmt: skip
    assert [language_group(x) for x in ("JavaScript", "TypeScript", "Python", "Go", "Rust")] == [
        "js_ts", "js_ts", "python", "go", "rust",
    ]  # fmt: skip
    for other in (None, "", "javascript", "go", "Jupyter Notebook", "Vue", "C++", "Shell"):
        assert language_group(other) == "other", other
    assert Covariates(language="TypeScript").to_dict()["language_group"] == "js_ts"


def _pair(lang_w: str | None, lang_l: str | None, age_w: float, age_l: float, dist: int = 0):
    w = vcase(1, launch=100, follow=50, lang=lang_w or "", age=age_w)
    lo = vcase(2, launch=100, follow=5, lang=lang_l or "", age=age_l, distance=dist)
    if lang_w is None:
        w = replace(w, covariates=replace(w.covariates, language=None))
    if lang_l is None:
        lo = replace(lo, covariates=replace(lo.covariates, language=None))
    return w, lo


def test_only_numeric_differences_exclude_a_pair():
    sds = {"lsm": 1.0, "age_log10": 1.0, "prelaunch_log": 1.0}
    for view in (VIEW_FOLLOW_THROUGH, VIEW_LAUNCH, VIEW_LAUNCH_UNDECLARED):
        c = ctx(view)
        # language, language group, category (view B) and surface all differ: still headline
        w, lo = _pair("Python", "Rust", 2.0, 2.2, dist=1)
        lo = replace(lo, covariates=replace(lo.covariates, surface="cli"))
        diffs, excl = _pair_diffs(w, lo, c, sds)
        assert excl == (), view.key
        assert diffs["language"] == diffs["language_group"] == diffs["surface"] == 1.0
        if view.matching == "pre_launch":
            assert diffs["category"] == 1.0
        # a missing language never excludes either
        w, lo = _pair(None, "Go", 2.0, 2.0)
        assert _pair_diffs(w, lo, c, sds)[1] == ()
        # a numeric difference > 0.5 SD does
        w, lo = _pair("Go", "Go", 2.0, 2.8)
        assert _pair_diffs(w, lo, c, sds)[1] == ("age_log10",)
    # the plain comparison view keeps the selection-v5 rule (language mismatch = 1, excludes)
    w, lo = _pair("Python", "Rust", 2.0, 2.0)
    assert _pair_diffs(w, lo, ctx(VIEW_PLAIN), sds)[1] == ("language",)


def lang_field() -> list[CaseInput]:
    """The split field with languages from the five groups and some surfaces."""
    langs = ["Go", "Python", "TypeScript", "Rust", "C++", "JavaScript"]
    surf = ["cli", "library", "mcp_server", "hosted_app"]
    outc = []
    for i, c in enumerate(split_field(0)):
        cov = replace(
            c.covariates,
            language=langs[i % len(langs)],
            surface=surf[i % len(surf)],
            install_paths=(("npx",) if i % 3 == 0 else ("brew", "binary")),
        )
        outc.append(replace(c, covariates=cov))
    return outc


def test_language_and_surface_stay_in_the_balance_table_and_the_same_group_subset():
    cases = lang_field()
    a = select(cases, ctx(VIEW_FOLLOW_THROUGH), Definition.from_brief(B))
    bal = a.balance
    assert bal["pairs"] > 0
    # every pair's headline status depends on numeric covariates only
    for p in a.level.pairs:
        assert set(p.excluded_on) <= {"lsm", "age_log10", "audience_band", "launch_quarter"}
    for cov in ("language", "language_group", "surface", "install_path"):
        rec = bal["after_matching"][cov]
        assert rec["kind"] == "categorical" and rec["excludes_pairs"] is False
        assert rec["levels"], cov  # SMD per level
        if rec["meets_target"] is False:
            assert rec["label"] == "balance_limited"
    assert set(bal["after_matching"]["install_path"]["levels"]) == {"npx", "brew", "binary"}
    # the same-language-group sensitivity alternative: headline pairs restricted to pairs
    # whose two sides share a group, with counts and balance on that subset
    sub = bal["same_language_group"]
    by = {c.ref: c for c in cases}
    head = [p for p in a.level.pairs if p.headline]
    same = [
        p
        for p in head
        if by[p.winner].covariates.language_group == by[p.loser].covariates.language_group
    ]
    assert sub["headline_pairs"] == len(head) and sub["same_group_headline_pairs"] == len(same)
    assert all(p.same_language_group == (p in same) for p in head)
    assert (sub["balance"] != {}) == bool(same)
    alts = {x["key"]: x for x in a.sensitivity["alternatives"]}
    assert alts["pairs:same_language_group"]["same_group_headline_pairs"] == len(same)
    rows = {r["candidate_ref"]: r for r in a.cases}
    for p in a.level.pairs:
        assert rows[p.loser]["pair"]["same_language_group"] == p.same_language_group
    # the later pattern step's rule is recorded in the parameters
    lg = a.params["language_groups"]
    assert "reverses" in lg["pattern_rule"] and "language-dependent" in lg["pattern_rule"]


# --- 3. distribution surface (pure parts) --------------------------------------------------------
def test_surface_labels_prompt_schema_and_input_are_project_level():
    assert SURFACES == (
        "mcp_server", "cli", "library", "editor_or_agent_plugin", "hosted_app", "other", "unknown",
    )  # fmt: skip
    assert INSTALL_PATHS == ("npx", "pip", "brew", "binary", "marketplace", "other", "unknown")
    p = surface_params()
    assert p["prompt_id"] == PROMPT.id == "distribution_surface" and PROMPT.version == "1"
    assert p["prompt_fingerprint"] == PROMPT.fingerprint
    assert p["schema_sha"] == schema_hash(SurfaceOutput) and p["repos_per_request"] == 20
    assert p == Context.from_brief(B).params()["distribution_surface"]
    schema = SurfaceOutput.model_json_schema()
    item = schema["$defs"]["SurfaceItem"]["properties"]
    assert set(item) == {"id", "surface", "install_paths"}  # enums only: no free text
    c = Candidate(
        "gh:org-z/zapkit",
        "org-z/zapkit",
        metadata={
            "description": "zapkit by org-z: a CLI",
            "topics": ["cli", 3],
            "language": "Go",
            "owner_type": "User",
            "homepage_domain": "zap.example",
            "created_at": "2024-01-01T00:00:00+00:00",
        },
    )
    got = surface_input(c, "c01", "Install with org-z tools")
    assert got == {
        "id": "c01",
        "name": "zapkit",
        "description": "zapkit by [owner]: a CLI",
        "topics": ["cli"],
        "language": "Go",
        "readme_excerpt": "Install with [owner] tools",
    }
    assert surface_of(c) == ("unknown", ("unknown",))  # not coded yet
    c.metadata["distribution_surface"] = {"surface": "cli", "install_paths": ["brew", "npx"]}
    assert surface_of(c) == ("cli", ("brew", "npx"))


# --- 4. view B's anchor ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "body", "hit"),
    [
        ("v1.0: Launch", None, True),
        ("Launching today", None, True),
        ("v0.3", "Introducing the new widget mode", True),
        ("ANNOUNCING v2", None, True),
        ("v1.0.0", "Our first public release!", True),
        ("v1.0.0", "Our first  public\nrelease", True),
        ("v1.0.0", "pre-launch fixes", True),  # a hyphen is a word boundary
        ("v1.1", "Launched last week; bug fixes", False),
        ("v1.1", "relaunch of the docs", False),
        ("launcher v2", "Launches the launcher", False),
        ("v1.1", "x" * 300 + " launch", False),  # past the first 300 characters
        ("v1.1", "x" * 290 + " launch", True),
        (None, None, False),
    ],
)
def test_release_launch_wording(name: Any, body: Any, hit: bool):
    assert release_is_launch(name, body) is hit


def cand(ref: str, sources: list[dict[str, Any]], **meta: Any) -> Candidate:
    return Candidate(f"gh:{ref}", ref, sources=sources, metadata=meta)


def show_hn(item: int, t: datetime) -> dict[str, Any]:
    return {
        "source": "show_hn",
        "term": "x",
        "hn_item_id": item,
        "points": 5,
        "time": t.isoformat(),
    }


def releases(*rels: tuple[str, datetime, bool], complete: bool = True) -> dict[str, Any]:
    return {
        "source": "gh_releases",
        "rule": ANCHOR_RULE_VERSION,
        "status": "complete" if complete else "truncated",
        "complete": complete,
        "pages": 1,
        "releases": [
            {"tag": tag, "published_at": t.isoformat(), "prerelease": False, "launch": launch}
            for tag, t, launch in rels
        ],
    }


def mention(status: str, t: datetime | None = None, kind: str = "comment") -> dict[str, Any]:
    d: dict[str, Any] = {"source": "hn_first_mention", "rule": ANCHOR_RULE_VERSION}
    d["status"] = status
    if t is not None:
        d |= {"hn_item_id": 7001, "time": t.isoformat(), "kind": kind}
    return d


def d(month: int, day: int = 1, year: int = 2025) -> datetime:
    return datetime(year, month, day, 12, tzinfo=UTC)


def test_first_launch_event_anchors_later_ones_are_relaunch_events():
    c = cand(
        "org-r/relaunchy",
        [
            show_hn(9001, d(9)),
            releases(("v0.9", d(3), False), ("v1.0", d(5), True), ("v2.0", d(11), True)),
        ],
    )
    a, why, rel = view_b_anchor(c, W0, W1)
    assert why is None and a is not None
    assert (a.rule, a.source, a.via, a.ref, a.at) == (
        "release_launch", "release_launch", "github_release", "v1.0", d(5),
    )  # fmt: skip
    assert a.type == "launch" and a.precision == "hour" and not a.undeclared
    assert [(e["kind"], e["ref"]) for e in rel] == [("show_hn", "9001"), ("release_launch", "v2.0")]
    # a Show HN first: rule show_hn; the launch-worded release is the relaunch
    c2 = cand("org-r/showy", [show_hn(9002, d(4)), releases(("v1.0", d(5), True))])
    a2, _, rel2 = view_b_anchor(c2, W0, W1)
    assert a2 is not None and a2.rule == "show_hn" and a2.via == "discovery"
    assert rel2 == [{"at": d(5).isoformat(), "kind": "release_launch", "ref": "v1.0",
                     "via": "github_release"}]  # fmt: skip
    # a tie at the same instant: show_hn before launch_hn before release_launch
    ev = launch_events(cand("org-r/tie", [show_hn(9003, d(6)), releases(("v1", d(6), True))]),
                       W0, W1)  # fmt: skip
    assert [e.kind for e in ev] == ["show_hn", "release_launch"]
    # events outside the window never anchor
    late = cand("org-r/late", [releases(("v1", d(8, 1, 2026), True))])
    assert launch_events(late, W0, W1) == []


def test_undeclared_launch_first_mention_vs_first_release():
    # no launch event: the earlier of the first mention and the first release
    m_first = cand("org-u/mention", [releases(("v0.1", d(6), False)), mention("found", d(4))])
    a, why, rel = view_b_anchor(m_first, W0, W1)
    assert a is not None and why is None and rel == []
    assert (a.rule, a.source, a.via, a.ref) == (
        "undeclared:first_mention", "first_mention", "hn_comment", "7001",
    )  # fmt: skip
    assert a.undeclared and a.to_dict()["undeclared_launch"] is True
    r_first = cand("org-u/release", [releases(("v0.1", d(3), False)), mention("found", d(4))])
    a, _, _ = view_b_anchor(r_first, W0, W1)
    assert a is not None and (a.rule, a.ref) == ("undeclared:first_release", "v0.1")
    # the first release counts even when it is a prerelease or not launch-worded
    assert first_release_at(r_first) == (d(3), "v0.1", True)
    # a tie goes to the mention; no mention (searched, none): the release
    tie = cand("org-u/tie", [releases(("v0.1", d(4), False)), mention("found", d(4), "story")])
    a, _, _ = view_b_anchor(tie, W0, W1)
    assert a is not None and a.rule == "undeclared:first_mention" and a.via == "hn_story"
    only_rel = cand("org-u/rel", [releases(("v0.1", d(4), False)), mention("none")])
    a, _, _ = view_b_anchor(only_rel, W0, W1)
    assert a is not None and a.rule == "undeclared:first_release"
    only_m = cand("org-u/m", [releases(), mention("found", d(4))])
    a, _, _ = view_b_anchor(only_m, W0, W1)
    assert a is not None and a.rule == "undeclared:first_mention"
    # no anchor: nothing at all, earlier one outside the window, or an unknown source
    for c, reason in (
        (cand("org-u/none", [releases(), mention("none")]), "no_launch_event"),
        (cand("org-u/old", [releases(("v0.1", d(6, 1, 2024), False)), mention("found", d(4))]),
         "undeclared:before_window"),
        (cand("org-u/capped", [releases(), mention("capped")]), "undeclared:mention_capped"),
        (cand("org-u/nosearch", [releases()]), "undeclared:mention_not_searched"),
        (cand("org-u/trunc", [releases(("v1", d(3), False), complete=False),
                               mention("found", d(4))]), "undeclared:releases_incomplete"),
        (cand("org-u/norel", [mention("found", d(4))]), "undeclared:releases_incomplete"),
    ):  # fmt: skip
        a, why, _ = view_b_anchor(c, W0, W1)
        assert a is None and why == reason, c.ref


def test_view_b_anchor_reads_no_star_data():
    """The anchor is a function of the launch signals alone (ADR-084): no star series, burst or
    outcome value is an argument, and records of an earlier rule are ignored."""
    import inspect

    for f in (choose_launch_anchor, view_b_anchor, launch_events, first_release_at):
        params = set(inspect.signature(f).parameters)
        assert not params & {"series", "bursts", "has_series", "values", "as_of"}, f.__name__
    old = releases(("v1.0", d(5), True))
    old["rule"] = "anchor-v4"
    c = cand("org-o/old", [old, mention("none")])
    assert launch_events(c, W0, W1) == [] and first_release_at(c) == (None, None, False)
    events = [LaunchEvent(d(5), "show_hn", "1", "discovery")]
    a, _, _ = choose_launch_anchor(events, None, (None, None, False), W0, W1)
    assert a is not None and a.rule == "show_hn"  # a launch event needs no other source


def b_case(c: CaseInput, rule: str, days: int = 0) -> CaseInput:
    """`c` with a view-B anchor of `rule` (shifted by `days`)."""
    a = c.anchor
    assert a is not None
    src = rule.removeprefix("undeclared:")
    ba = Anchor("launch", a.at + timedelta(days=days), "hour", src, "x", rule, "ref")
    return replace(c, launch_case=replace(c, anchor=ba))


def mixed_field() -> list[CaseInput]:
    """40 launch-anchored cases (split field): 20 with a declared and 20 with an undeclared
    view-B anchor, every rule used; plus 4 burst-anchored ones without a view-B anchor."""
    rules = ["show_hn", "undeclared:first_mention", "launch_hn", "undeclared:first_release",
             "release_launch", "undeclared:first_mention"]  # fmt: skip
    outc = []
    for i, c in enumerate(split_field(4)):
        if c.anchor is not None and c.anchor.type == "burst":
            nb = replace(c, anchor=None, anchor_reason="no_launch_event")
            outc.append(replace(c, launch_case=nb))
        else:
            outc.append(b_case(c, rules[i % len(rules)]))
    return outc


def test_declared_and_undeclared_sub_populations_flags_and_rule_counts():
    cases = mixed_field()
    sels = select_views(cases, ctx(), Definition.from_brief(B))
    counts = {"show_hn": 7, "launch_hn": 7, "product_hunt": 0, "release_launch": 6,
              "bluesky_maintainer_post": 0, "undeclared:first_mention": 13,
              "undeclared:first_release": 7, "none": 4}  # fmt: skip
    assert sels.summary["view_b_anchor_rules"] == counts
    assert list(counts) == list(ANCHOR_RULE_LABELS)
    assert sels.summary["undeclared_launch"] == 20
    b, u = sels.views["launch"], sels.views["launch_undeclared"]
    assert b.summary["anchor_rules"] == u.summary["anchor_rules"] == counts
    assert b.summary["reference_population_n"] == 20 and u.summary["reference_population_n"] == 20
    rb = {r["candidate_ref"]: r for r in b.cases}
    ru = {r["candidate_ref"]: r for r in u.cases}
    for c in cases:
        rule = selmod.anchor_rule(c.for_view_b())
        assert rb[c.ref]["anchor_rule"] == ru[c.ref]["anchor_rule"] == rule
        und = rule.startswith("undeclared:")
        assert rb[c.ref]["undeclared_launch"] is und
        if rule == "none":
            assert rb[c.ref]["role"] == ru[c.ref]["role"] == "no_anchor"
        elif und:  # not mixed into the declared-launch headline
            assert rb[c.ref]["role"] == "not_in_view" and ru[c.ref]["role"] != "not_in_view"
        else:
            assert ru[c.ref]["role"] == "not_in_view" and rb[c.ref]["role"] != "not_in_view"
    # each sub-population has its own winners, losers and balance
    wb, wu = {c.ref for c in b.level.winners}, {c.ref for c in u.level.winners}
    assert wb and wu and not wb & wu
    assert b.balance["view"] == "launch" and u.balance["view"] == "launch_undeclared"
    assert sels.summary["views"]["launch"]["headline"] is True
    assert sels.summary["views"]["launch_undeclared"]["headline"] is False
    # view A keeps its own (§2.2) anchor: burst-anchored cases are in its population
    assert sels.views["follow_through"].summary["reference_population_n"] == 44
    # relaunch events are carried into view B's case rows
    ev = ({"at": "2025-12-01T00:00:00+00:00", "kind": "show_hn", "ref": "9", "via": "discovery"},)
    c0 = cases[0]
    assert c0.launch_case is not None
    c0 = replace(c0, launch_case=replace(c0.launch_case, relaunch_events=ev))
    s2 = select([c0, *cases[1:]], ctx(VIEW_LAUNCH), Definition.from_brief(B))
    row = next(r for r in s2.cases if r["candidate_ref"] == c0.ref)
    assert row["relaunch_events"] == [dict(ev[0])]
    assert s2.summary["relaunch_events"] == 1 and s2.summary["cases_with_relaunch"] == 1
    assert "X: paid API" in s2.summary["limitations"][0]


def test_anchor_rule_counts_show_every_label():
    assert anchor_rule_counts([]) == dict.fromkeys(ANCHOR_RULE_LABELS, 0)


# --- 5. SD over the full shortlisted pool ---------------------------------------------------------
def test_calipers_use_the_sd_of_the_full_shortlisted_pool():
    cases = split_field(0)
    # repos outside the population (no anchor in view A) still count for the SD when they
    # have an observed value: here, far-out LSM values widen it
    extra = []
    for i in range(6):
        c = vcase(60 + i, launch=10.0**6, follow=10.0)
        extra.append(replace(c, anchor=None, anchor_reason="no_anchor"))
    allc = [*cases, *extra]
    a = select(allc, ctx(VIEW_FOLLOW_THROUGH), Definition.from_brief(B))
    full = pool_sds(allc)
    assert a.level.sds == full
    narrow = pool_sds([*a.level.winners, *a.level.loser_pool])
    assert full["lsm"] is not None and narrow["lsm"] is not None and full["lsm"] > narrow["lsm"]
    assert a.balance["sd_basis"] == "full_pool" and a.summary["sd_n"]["lsm"] == len(allc)
    # the wider SD admits more losers inside the LSM caliper than the winners+pool SD did
    plain_sd = select(allc, replace(ctx(VIEW_FOLLOW_THROUGH), view=replace(
        VIEW_FOLLOW_THROUGH, sd_basis="winners_and_pool")), Definition.from_brief(B))  # fmt: skip
    assert len(a.level.pairs) >= len(plain_sd.level.pairs)
    assert "full shortlisted pool" in a.params["sd_rule"] and "log10" in a.params["sd_rule"]
    # view B's SD is over every field and reference repo's view-B values (declared or not)
    sels = select_views(mixed_field(), ctx(), Definition.from_brief(B))
    bcases = [c.for_view_b() for c in mixed_field()]
    assert sels.views["launch"].level.sds == pool_sds(bcases)
    assert sels.views["launch_undeclared"].level.sds == pool_sds(bcases)


# --- 6. view C over every non-winner -------------------------------------------------------------
def test_view_c_covers_every_shortlisted_non_winner_with_counts_of_values():
    cases = split_field(4)
    na = [replace(vcase(70 + i, launch=5, follow=1), anchor=None, anchor_reason="no_anchor",
                  values={}) for i in range(3)]  # fmt: skip
    und = vcase(80, launch=5, follow=1)
    und = replace(und, values={**und.values, selmod.FOLLOW_STARS: selmod.Value("pending")})
    ex = replace(vcase(90, launch=5, follow=1), panel="exemplar")
    sels = select_views([*cases, *na, und, ex], ctx(), Definition.from_brief(B))
    cv = sels.context["views"]["follow_through"]
    s = sels.views["follow_through"]
    n_field = len(cases) + len(na) + 1  # the exemplar is outside the field
    assert cv["non_winners"] == n_field - len(s.level.winners)
    assert cv["non_winners_by_role"]["no_anchor"] == 3
    assert cv["non_winners_by_role"]["undetermined"] == 1
    rec = cv["outcomes"][selmod.LAUNCH_SIZE]
    assert rec["non_winners_with_value"] + rec["non_winners_without_value"] == cv["non_winners"]
    assert rec["non_winners_without_value"] == 3  # the no-anchor repos
    assert rec["non_winners"]["n"] == rec["non_winners_with_value"]
    assert "every shortlisted non-winner" in cv["comparison"]


# --- 7. versions, guard, determinism, estimate -------------------------------------------
def test_versions_are_bumped():
    assert SELECTION_VERSION == "selection-v8" and ANCHOR_RULE_VERSION == "anchor-v7"
    p = Context.from_brief(B).params()
    assert p["selection_version"] == "selection-v8" and p["anchor_rule_version"] == "anchor-v7"
    assert set(p["views"]) == {"follow_through", "launch", "launch_undeclared"}
    vb = p["view_b_anchor"]
    assert vb["rules"] == list(ANCHOR_RULE_LABELS)
    assert vb["release_launch_pattern"] == selmod.RELEASE_LAUNCH_PATTERN
    # selection-v8 (ADR-085): Bluesky and Product Hunt are sources now; the ADR-075 limitation
    # names only the accounts the maintainer did not declare
    assert any("ADR-075" in x for x in vb["limitations"])
    assert "unobserved channel" in vb["known_bias"]
    assert "numeric standardized differences only" in p["headline_exclusion_rule"]


def test_the_guard_covers_the_new_anchor_code(monkeypatch):
    names = set(out.ANCHOR_RULE_FUNCTIONS)
    for f in ("choose_launch_anchor", "release_is_launch", "launch_events", "first_mention",
              "fetch_releases", "view_b_anchor", "pigtail.connectors.hn:parse_mention_page",
              "pigtail.briefs.confirm:owner_overlaps_name"):  # fmt: skip
        assert f in names, f
    h0 = sha256_json(Context.from_brief(B).params())

    def h() -> str:
        return sha256_json(Context.from_brief(B).params())

    for mod, name, value in (
        (out, "choose_launch_anchor", lambda *a: (None, None, [])),
        (out, "release_is_launch", lambda *a: False),
        (out, "MENTION_MAX_REQUESTS", 9),
        (selmod, "RELEASE_LAUNCH_PATTERN", r"\blaunch\b"),
        (conf, "owner_overlaps_name", lambda full: False),
    ):
        monkeypatch.setattr(mod, name, value)
        assert h() != h0, name
        monkeypatch.undo()
    assert h() == h0


def test_recompute_is_deterministic_with_view_b_cases():
    cases = mixed_field()
    s1 = select_views(cases, ctx(), Definition.from_brief(B))
    shuffled = list(cases)
    random.Random(11).shuffle(shuffled)
    s2 = select_views(shuffled, ctx(), Definition.from_brief(B))
    assert s1.result_hash == s2.result_hash and s1.inputs_hash == s2.inputs_hash
    assert {k: v.result_hash for k, v in s1.views.items()} == {
        k: v.result_hash for k, v in s2.views.items()
    }
    # the view-B inputs are part of the inputs hash
    c0 = cases[0]
    assert c0.launch_case is not None and c0.launch_case.anchor is not None
    moved = replace(c0.launch_case, anchor=replace(c0.launch_case.anchor, ref="other"))
    s3 = select_views([replace(c0, launch_case=moved), *cases[1:]], ctx(),
                      Definition.from_brief(B))  # fmt: skip
    assert s3.inputs_hash != s1.inputs_hash


def test_estimate_counts_surface_coding_releases_and_mentions():
    e = estimate(B, selection=SelectionState(pending=True, shortlisted=114))
    s = e.selection
    assert s["surface_coding_requests"] == math.ceil(114 / 20) == 6
    assert s["release_core_requests"] == 114
    assert s["hn_first_mention_requests"] == 2 * math.ceil(114 * 0.75)
    assert e.other_requests["hn_first_mention"] == s["hn_first_mention_requests"]
    sc = {x.stage: x for x in e.stages}["distribution_surface"]
    assert sc.model == "claude-haiku-4-5-20251001" and sc.mode == "batch" and sc.usd
    scope = run_scope(e, ("selection",))
    assert scope["selection"]["surface_calls"] == 6 and "releases" in scope["selection"]["view_b"]
    done = estimate(
        B,
        selection=SelectionState(
            pending=True, shortlisted=114, surface_done=True, releases_done=114, mentions_done=114
        ),
    )
    assert done.selection["surface_coding_requests"] == 0
    assert done.selection["release_core_requests"] == 0
    assert done.selection["hn_first_mention_requests"] == 0
