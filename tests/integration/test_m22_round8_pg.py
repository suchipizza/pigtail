"""ADR-085 addendum 3 on Postgres (synthetic data, fakes only, no network): a declared
maintainer's Bluesky post counts as a view-B launch event only with launch wording, and its text
never reaches the database; posts without it are counted; a README naming two accounts declares
none; a declared launch before the window (a Bluesky post, a Product Hunt post found by slug)
leaves no view-B anchor; Product Hunt votes come only from a post in the anchor's days 0..2; the
selection recomputed from stored data gives the same hashes. Every name, handle, DID and text is
made up (`org-w…`, `maintainer-…example`, `SYNTH-…`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.candidates import CandidateStore
from pigtail.briefs.discovery import window_bounds
from pigtail.briefs.launch_sources import BSKY_SOURCE, PH_SOURCE
from pigtail.briefs.outcomes import load_inputs, shortlisted
from pigtail.briefs.selection import Context, Definition, select_views
from pigtail.briefs.selection_store import latest, view
from tests.discovery_fake import FakeShowHN
from tests.github_fake import FakeGitHub
from tests.integration.test_github_per_repo_m1t24 import connector
from tests.integration.test_launch_lookup_m22 import NOW, hit, hn_connector, prereg
from tests.integration.test_m22_round7_pg import bsky_conn, dump, ph_conn, readme_of, seed, stage
from tests.launch_sources_fake import (
    HANDLE_A,
    HANDLE_C,
    FakeBluesky,
    FakeProductHunt,
    bsky_post,
    ph_post,
    synthetic_did,
)

pytestmark = pytest.mark.db

HANDLE_D = "maintainer-d.example"
HANDLE_E = "maintainer-e.example"
W1, W2, W3, W4, W5, W6 = (
    "org-w1/wordy-tool", "org-w2/twin-kit", "org-w3/early-app", "org-w4/pulsegrid",
    "org-w5/tidepool", "org-w6/emberkit",
)  # fmt: skip
REPOS: dict[str, dict[str, Any]] = {n: {"owner_type": "User"} for n in (W1, W3)}
REPOS |= {n: {"owner_type": "Organization"} for n in (W2, W4, W5, W6)}
QUIET, LOUD = "SYNTH-QUIETWORD", "SYNTH-ZEPHYRMARK"


def t(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def gh_links() -> Any:
    gh = FakeGitHub(now=lambda: NOW)
    gh.links = {n: (None, meta["owner_type"]) for n, meta in REPOS.items()}
    gh.social = {
        "org-w1": [{"provider": "bluesky", "url": f"https://bsky.app/profile/{HANDLE_A}"}],
        "org-w3": [{"provider": "bluesky", "url": f"https://bsky.app/profile/{HANDLE_E}"}],
    }
    return gh


POSTS = [
    # wordy-tool: two posts without launch wording, one with it (the anchor)
    bsky_post(1, HANDLE_A, t(2025, 6, 1), [f"https://github.com/{W1}"],
              text=f"{QUIET} new docs page"),
    bsky_post(2, HANDLE_A, t(2025, 7, 1), [f"https://github.com/{W1}"],
              text=f"{LOUD} Launching wordy-tool today"),
    bsky_post(3, HANDLE_A, t(2025, 9, 1), [f"https://github.com/{W1}/releases"],
              text=f"{QUIET} v2 notes"),
    # early-app: announced before the window (2025-03-26), and again inside it
    bsky_post(4, HANDLE_E, t(2024, 6, 1), [f"https://github.com/{W3}"],
              text=f"{LOUD} Introducing early-app"),
    bsky_post(5, HANDLE_E, t(2025, 8, 1), [f"https://github.com/{W3}"],
              text=f"{LOUD} launching early-app 2.0"),
    # the two README accounts of twin-kit post about it: never searched
    bsky_post(6, HANDLE_C, t(2025, 5, 1), [f"https://github.com/{W2}"], text=f"{LOUD} launch"),
]  # fmt: skip
READMES = {W2: f"# Twin\nBy @{HANDLE_C} and https://bsky.app/profile/{HANDLE_D}\n".encode()}
HN_HITS = [
    hit(8401, "Show HN: PulseGrid", f"https://github.com/{W4}", t(2025, 5, 1), 30, "show_hn"),
    hit(8402, "Show HN: Tidepool", f"https://github.com/{W5}", t(2025, 6, 1), 20, "show_hn"),
    hit(8403, "Show HN: Emberkit", f"https://github.com/{W6}", t(2025, 7, 1), 10, "show_hn"),
]
PH_POSTS = [
    # pulsegrid: its Product Hunt launch comes 19 days after the Show HN anchor: a relaunch,
    # outside days 0..2, so no votes
    ph_post("951", "PulseGrid", slug="pulsegrid", description=f"github.com/{W4}", votes=500,
            comments=50, created=t(2025, 5, 19), featured=t(2025, 5, 20)),
    # tidepool: featured the day after the Show HN anchor: votes observed
    ph_post("952", "Tidepool", slug="tidepool", description=f"github.com/{W5}", votes=222,
            comments=22, created=t(2025, 6, 1), featured=t(2025, 6, 2)),
    # emberkit: launched on Product Hunt before the window (found by slug: no date bound)
    ph_post("953", "Emberkit", slug="emberkit", description=f"github.com/{W6}", votes=90,
            comments=9, created=t(2024, 10, 1), featured=t(2024, 10, 1)),
]  # fmt: skip


def run(capture_db: Any, tmp: Path, monkeypatch: Any) -> tuple[Any, Any, Any, dict[str, Any]]:
    monkeypatch.setenv("PIGTAIL_SELECTION_BLUESKY", "true")
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "true")
    b = seed(capture_db, REPOS)
    prereg(capture_db.conn, b, tmp)
    fake = FakeBluesky(POSTS)
    cp: dict[str, Any] = {}
    res = stage(capture_db.conn, b, tmp, hn=hn_connector(capture_db, FakeShowHN(HN_HITS), tmp),
                bsky=bsky_conn(capture_db, fake, tmp),
                github=connector(capture_db, gh_links(), tmp),
                ph=ph_conn(capture_db, FakeProductHunt(PH_POSTS), tmp),
                readme=readme_of(READMES), checkpoint=cp)  # fmt: skip
    return b, fake, res, cp


def test_launch_wording_readme_rule_pre_window_and_ph_launch_window(
    capture_db, tmp_path, monkeypatch
):
    b, fake, res, cp = run(capture_db, tmp_path, monkeypatch)
    bs = res.fetch["bluesky"]
    assert bs["status"] == {"complete": 2, "no_declared_account": 4} and bs["incomplete"] == 0
    assert bs["readme_ambiguous"] == 1 and bs["declared_in"] == {"github_profile": 2}
    assert bs["posts_without_launch_wording"] == 2 and bs["launch_posts_before_window"] == 1
    # the README's two accounts were never resolved or searched
    handles = {r.url.params.get("handle") for r in fake.requests}
    authors = {r.url.params.get("author") for r in fake.requests}
    assert not {HANDLE_C, HANDLE_D} & handles
    assert not {synthetic_did(HANDLE_C), synthetic_did(HANDLE_D)} & authors
    # the searches carry no date filter; before-window posts were read
    assert all("since" not in r.url.params for r in fake.requests)

    cs = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}

    def sig(name: str) -> dict[str, Any]:
        (s,) = [s for s in cs[f"gh:{name}"].sources if s["source"] == BSKY_SOURCE]
        return s

    s1 = sig(W1)
    assert [p["time"] for p in s1["posts"]] == [t(2025, 7, 1).isoformat()]
    assert s1["posts_without_launch_wording"] == 2 and s1["readme_ambiguous"] is False
    s2 = sig(W2)
    assert s2["status"] == "no_declared_account" and s2["readme_ambiguous"] is True
    s3 = sig(W3)
    assert [p["time"] for p in s3["posts"]] == [
        t(2024, 6, 1).isoformat(),
        t(2025, 8, 1).isoformat(),
    ]
    # Product Hunt: the pre-window post found by slug is stored
    (p6,) = next(s for s in cs[f"gh:{W6}"].sources if s["source"] == PH_SOURCE)["posts"]
    assert p6["ph_post_id"] == "953" and p6["confirmed"] is True
    assert res.fetch["product_hunt"]["before_window"] == 1

    v = view(capture_db.conn, b.brief_id, 1)
    rows = {r["repo_full_name"]: r["detail"] for r in v["cases_by_view"]["launch"]}
    # 1. launch wording: the worded post anchors; the unworded ones are neither anchor nor relaunch
    assert rows[W1]["anchor_rule"] == "bluesky_maintainer_post"
    assert rows[W1]["anchor"]["at"] == t(2025, 7, 1).isoformat()
    assert rows[W1]["relaunch_events"] == []
    # 3. launched before the window: no anchor, counted per kind
    for name, kind in ((W3, "bluesky_maintainer_post"), (W6, "product_hunt")):
        assert rows[name]["anchor"] is None, name
        assert rows[name]["anchor_reason"] == "launched_before_window", name
        assert rows[name]["pre_window_launch"]["kind"] == kind, name
        assert rows[name]["relaunch_events"] == [], name
    counts = v["selection"]["summary"]["view_b_anchor_rules"]
    assert counts["launched_before_window:bluesky_maintainer_post"] == 1
    assert counts["launched_before_window:product_hunt"] == 1
    assert counts["none"] >= 2 and res.counts["view_b_anchor_rules"] == counts
    # view A is unchanged: emberkit's in-window Show HN still anchors it
    fa = {r["repo_full_name"]: r["detail"] for r in v["cases_by_view"]["follow_through"]}
    assert fa[W6]["anchor"]["via"] == "lookup:url"
    # 4. Product Hunt votes: only from a post in the anchor's days 0..2
    assert rows[W4]["anchor_rule"] == "show_hn"
    assert [e["kind"] for e in rows[W4]["relaunch_events"]] == ["product_hunt"]
    assert rows[W4]["values"]["att.ph_votes"]["status"] == "unknown"
    assert rows[W4]["values"]["att.ph_votes"]["reason"] == "no_ph_post_in_launch_window"
    assert rows[W4]["values"]["att.ph_comments"]["reason"] == "no_ph_post_in_launch_window"
    assert rows[W5]["values"]["att.ph_votes"]["value"] == 222.0
    assert rows[W5]["values"]["att.ph_comments"]["value"] == 22.0
    # the count of unworded posts reaches the stage result and the selection's notes
    assert res.counts["bluesky"] == {
        "posts_without_launch_wording": 2,
        "repos_with_unworded_posts": 1,
        "readme_ambiguous": 1,
    }
    w = v["selection"]["summary"]["warnings"]
    assert any(x.startswith("view B: 2 maintainer posts linked the repo without launch wording")
               for x in w)  # fmt: skip
    assert any("1 READMEs named more than one Bluesky account" in x for x in w)

    # the post text is never stored: no marker word, handle, DID or URI anywhere
    text = dump(capture_db.conn) + json.dumps(cp)
    for marker in (QUIET, LOUD, "Launching wordy-tool", "Introducing early-app", HANDLE_A,
                   HANDLE_C, HANDLE_D, HANDLE_E, synthetic_did(HANDLE_A),
                   synthetic_did(HANDLE_E), "at://"):  # fmt: skip
        assert marker not in text, marker
    for f in (tmp_path / "snap").rglob("*"):
        if f.is_file():
            assert QUIET.encode() not in f.read_bytes() and LOUD.encode() not in f.read_bytes()

    # recompute from stored data: the same inputs and result hashes
    stored = latest(capture_db.conn, b.brief_id, 1)
    assert stored is not None
    ctx = Context.from_brief(b)
    again = load_inputs(
        capture_db.conn, b, shortlisted(capture_db.conn, b),
        window=window_bounds(b, NOW.date()), as_of=stored["as_of"],
        launch_sources=ctx.required_launch_sources,
    )  # fmt: skip
    sel = select_views(again, ctx, Definition.from_brief(b))
    assert sel.inputs_hash == stored["inputs_hash"] and sel.result_hash == stored["result_hash"]
