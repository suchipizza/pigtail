"""M24 report facts: deterministic rules on synthetic text and series (ADR-089; M24-T2)."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

from pigtail.forensics.facts import (
    Event,
    Project,
    assets_at_launch,
    detect_assets,
    events_from_docs,
    explain,
    readme_structure,
    title_words,
    trajectory,
)
from pigtail.forensics.pilot import select_all_cases

README = """# widget

![demo](docs/demo.gif)
![build](https://img.shields.io/badge/build-passing-green)

## Features
- fast

## Quick start

```sh
pipx install widget
```

## Benchmarks

| tool | time |
|---|---|
| widget | 1.2 s |

## Comparison

| | widget | other |
|---|---|---|
| offline | ✅ | ❌ |

Docs: https://widget.readthedocs.io/en/latest/

Featured in a weekly newsletter.
"""


def test_m24_t2_assets_detected_with_verbatim_lines() -> None:
    found = detect_assets(README, Project("widget", "[owner]", None))
    assert "demo.gif" in found["demo_media"]
    assert found["install_one_liner"].strip() == "pipx install widget"
    assert "Benchmarks" in found["benchmarks"]
    assert "readthedocs" in found["docs_site"]
    assert "widget" in found["comparison_table"] and "other" in found["comparison_table"]
    assert "Featured in" in found["featured_in_claim"]
    assert found["screenshots"] == ""  # the only other image is a badge


def test_m24_t2_assets_absent_only_when_readme_captured() -> None:
    plain = ("ev_readme", "# tool\n\nA small library.\n")
    out = assets_at_launch(plain, None)["assets"]
    assert out["demo_media"] == {
        "value": "absent",
        "evidence_id": "ev_readme",
        "excerpt": None,
        "source": "readme_at_anchor",
    }
    none = assets_at_launch(None, None)["assets"]
    assert all(a["value"] == "unknown" and a["evidence_id"] is None for a in none.values())
    # release notes can only turn an asset present
    notes = ("ev_rel", "v1.0: new benchmark, 3x faster than before")
    out2 = assets_at_launch(plain, notes)["assets"]
    assert (
        out2["benchmarks"]["value"] == "present" and out2["benchmarks"]["evidence_id"] == "ev_rel"
    )


def test_m24_t2_readme_structure() -> None:
    st = readme_structure(README)
    assert st["headings_total"] == 5 and st["quick_start_section"] and st["features_section"]
    assert st["code_blocks"] == 1 and st["tables"] == 2 and st["badges"] == 1


def test_m24_t2_title_words_cut_at_25() -> None:
    t, cut = title_words(" ".join(str(i) for i in range(30)))
    assert t is not None and len(t.split()) == 25 and cut
    assert title_words("Show HN: a tool") == ("Show HN: a tool", False)
    assert title_words(None) == (None, False)


def test_m24_t2_events_from_docs() -> None:
    launch = {
        "signals": [
            {
                "source": "show_hn",
                "hn_item_id": 7,
                "title": "Show HN: x",
                "time": "2026-03-10T15:00:00+00:00",
            },
            {
                "source": "hn_launch_lookup",
                "hn_item_id": 7,
                "kind": "show_hn",
                "time": "2026-03-10T15:00:00+00:00",
            },
            {
                "source": "ph_launch",
                "posts": [
                    {"id": "p1", "featuredAt": "2026-03-11T08:00:00Z", "confirmed": True},
                    {"id": "p2", "createdAt": "2026-03-11T08:00:00Z", "confirmed": False},
                ],
            },
            {
                "source": "bsky_maintainer_posts",
                "posts": [
                    {
                        "kind": "bluesky_maintainer_post",
                        "time": "2026-03-10T16:00:00Z",
                        "role": "maintainer",
                        "match": "repo_url",
                    }
                ],
            },
            {
                "source": "gh_releases",
                "launch_worded": [{"tag": "v1.0.0", "published_at": "2026-03-09T12:00:00Z"}],
            },
        ],
        "relaunch_events": [],
    }
    rels = {
        "releases": [
            {"tag": "v1.0.0", "name": "Introducing v1.0", "published_at": "2026-03-09T12:00:00Z"},
            {"tag": "v0.9.0", "name": "", "published_at": "2026-02-01T12:00:00Z"},
        ]
    }
    ev = events_from_docs(launch, "ev_l", rels, "ev_r")
    kinds = [(e.kind, e.ref) for e in ev]
    assert kinds.count(("show_hn", "7")) == 1  # deduplicated by (kind, ref)
    assert ("product_hunt", "p2") not in kinds  # unconfirmed PH posts are not events
    rl = next(e for e in ev if e.kind == "release_launch")
    assert rl.title == "Introducing v1.0" and rl.title_evidence_id == "ev_r"
    assert set(rl.evidence_ids) == {"ev_l", "ev_r"}
    rel = next(e for e in ev if e.kind == "release")
    assert rel.launch is False and rel.title is None
    bs = next(e for e in ev if e.kind == "bluesky_maintainer_post")
    assert bs.title is None and "never stored" in (bs.title_missing or "")
    assert [e.at for e in ev] == sorted(e.at for e in ev if e.at is not None)


def _series() -> dict[date, int]:
    s = {}
    d = date(2026, 1, 1)
    while d <= date(2026, 4, 30):
        s[d] = 2
        d += timedelta(days=1)
    return s


def test_m24_t2_trajectory_pending_and_incomplete() -> None:
    s = _series()
    at = datetime(2026, 4, 20, 18, tzinfo=UTC)  # endpoint day 2026-04-20 (Los Angeles)
    tr = trajectory(s, at, created=date(2026, 1, 1), as_of=date(2026, 4, 30))
    assert tr["gained"]["+1d"]["value"] == 2 and tr["gained"]["+7d"]["value"] == 14
    assert tr["gained"]["+30d"] == {"value": None, "reason": "pending"}
    assert tr["stars_before"]["value"] == 2 * (date(2026, 4, 20) - date(2026, 1, 1)).days
    s.pop(date(2026, 4, 22))
    tr2 = trajectory(s, at, created=date(2025, 12, 1), as_of=date(2026, 4, 30))
    assert tr2["gained"]["+3d"]["reason"] == "incomplete_series"
    assert tr2["stars_before"]["reason"] == "series_does_not_reach_creation"


def test_m24_t2_explain_rule() -> None:
    """[M24-T2] explain-v2: unconfirmed HN title matches never explain a burst; a launch event
    within 24 h of a release is preferred and the release named with it."""

    def ev(kind: str, day: int, ref: str = "r", confirmed: bool | None = True) -> Event:
        at = datetime(2026, 3, day, 20, tzinfo=UTC)
        launch = kind not in ("release", "first_mention")
        return Event(kind, ref, at, [], launch, confirmed=confirmed)

    onset = date(2026, 3, 10)
    got = explain(onset, [ev("release", 10, "v1"), ev("show_hn", 10, "7"), ev("show_hn", 2)])
    assert got == {
        "event": "show_hn:7",
        "kind": "show_hn",
        "days_from_onset": 0,
        "also": ["release:v1"],
    }
    # the release is closer, but a launch event is within 24 h of it: the launch is preferred
    got = explain(onset, [ev("release", 10, "v2"), ev("product_hunt", 9, "p")])
    assert got is not None and got["event"] == "product_hunt:p" and got["also"] == ["release:v2"]
    # an unconfirmed title match is ignored; the release explains it
    got = explain(onset, [ev("release", 10, "v1"), ev("show_hn", 10, "9", confirmed=False)])
    assert got is not None and got["event"] == "release:v1"
    assert explain(onset, [ev("show_hn", 10, "9", confirmed=None)]) is None
    assert explain(onset, [ev("show_hn", 2)]) is None  # more than 3 days before: unexplained
    assert explain(onset, [ev("release", 12)]) is None  # more than 1 day after


def test_m24_t2_burst_size_is_the_whole_burst() -> None:
    """[M24-T2] size-v1: a ramp-up burst's total stars cover every day until the rate is back
    at baseline, not only the first 48 h."""
    from pigtail.forensics.facts import bursts

    s = {}
    d = date(2026, 1, 1)
    while d <= date(2026, 4, 30):
        s[d] = 1
        d += timedelta(days=1)
    ramp = [60, 60, 500, 2000, 3000, 1500, 400]
    for i, n in enumerate(ramp):
        s[date(2026, 3, 10) + timedelta(days=i)] = n
    b = bursts(s, [], created=date(2026, 1, 1))[0]
    assert b["stars_48h"] == 120
    assert b["stars_total"] == sum(ramp) and b["stars_total_complete"]
    assert b["peak_stars"] == 3000 and b["explained_by"] == "unexplained"


def test_m24_t1_full_case_rule_deterministic_under_shuffle() -> None:
    def row(
        view: str,
        ref: str,
        role: str,
        pid: int | None,
        panel: str | None,
        rank: int | None,
        dist: float | None = None,
    ) -> dict[str, object]:
        return {
            "view": view,
            "candidate_ref": ref,
            "repo_full_name": ref.lower(),
            "role": role,
            "pair_id": pid,
            "pair_panel": panel,
            "rank": rank,
            "headline": True,
            "detail": {"pair": {"distance": dist}, "anchor": {"at": "2026-03-10T15:00:00+00:00"}},
        }

    rows = [
        row("launch", "b1", "winner", 1, "field", 1),
        row("launch", "b1l", "matched_loser", 1, "field", None, 0.2),
        row("follow_through", "a2", "winner", 2, "field", 2),
        row("follow_through", "a2l", "matched_loser", 2, "field", None, 0.3),
        row("follow_through", "a1", "winner", 1, "field", 1),
        row("follow_through", "a1l2", "matched_loser", 1, "field", None, 0.9),
        row("follow_through", "a1l1", "matched_loser", 1, "field", None, 0.1),
        row("follow_through", "ex", "exemplar", 3, "exemplar", None),
        row("follow_through", "exl", "exemplar_matched_loser", 3, "exemplar", None, 0.5),
        row("follow_through", "q", "qualified_not_selected", None, None, None),
        row("launch_undeclared", "u1", "winner", 1, "field", 1),
    ]
    want = [c.candidate_ref for c in select_all_cases(rows)]
    assert want == ["a1", "a1l1", "a1l2", "a2", "a2l", "ex", "exl", "b1", "b1l", "u1"]
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert [c.candidate_ref for c in select_all_cases(shuffled)] == want


def test_m24_r1_asset_rules_on_the_labelled_set() -> None:
    """[M24-T2] verifier round 1 fix 4: assets-v2 measured on the labelled set; the v1 error
    types (logos, third-party docs, "mentioned in chat", "VS Code", dev installs) are negatives
    and every rule is at least 0.70 precise on it."""
    from pigtail.forensics.assets_labelled import EXAMPLES, measure

    m = measure()
    for a, r in m["per_asset"].items():
        assert r["examples"] >= 5, a
        assert r["precision"] is not None and r["precision"] >= 0.70, (a, r)
    negatives = [e for e in EXAMPLES if not e.label]
    assert len(negatives) >= 20
    p = Project("tinyqueue", "[owner]", "tinyqueue.dev")
    assert detect_assets("![logo](assets/logo.png)", p)["screenshots"] == ""
    assert detect_assets("https://docs.python.org/3/library/queue.html", p)["docs_site"] == ""
    assert detect_assets("People mentioned in chat that it works.", p)["featured_in_claim"] == ""
    assert detect_assets("```sh\nnpm install\n```", p)["install_one_liner"] == ""
    vs = "### VS Code (GitHub Copilot)\n\n| key | value |\n|---|---|\n| a | b |"
    assert detect_assets(vs, p)["comparison_table"] == ""


def test_m24_r1_release_notes_after_t_are_not_at_launch() -> None:
    """[M24-T2] verifier round 1 fix 4: release notes later than T + 1 day are listed as after
    launch, never as at launch."""
    readme = ("ev_r", "# tinyqueue\n\nA queue.\n")
    late = ("ev_n", "| | tinyqueue | x |\n|---|---|---|\n| a | ✅ | ❌ |")
    out = assets_at_launch(readme, None, Project("tinyqueue"), late)
    assert out["assets"]["comparison_table"]["value"] == "absent"
    assert out["after_launch"]["comparison_table"]["evidence_id"] == "ev_n"


def test_m24_r2_hn_confirmation_is_repo_or_homepage_only() -> None:
    """[M24-T2] verifier round 2 fix 1 (events-v3): a story is the case's only when its URL is
    the repo, a former URL GitHub resolves to it, or under the recorded homepage (exact host and
    path prefix). A host that merely contains the repo's name stays unconfirmed."""
    from dataclasses import dataclass as dc

    from pigtail.forensics.facts import FactsStage, links_homepage
    from pigtail.forensics.store import PilotCase

    assert links_homepage("https://tinyqueue.dev/blog/launch", "https://tinyqueue.dev/")
    assert links_homepage("https://www.tinyqueue.dev/", "tinyqueue.dev")
    assert links_homepage("https://acme.dev/tq/docs", "https://acme.dev/tq")
    assert not links_homepage("https://acme.dev/other", "https://acme.dev/tq")
    assert not links_homepage("https://tinyqueue-cloud.io/", "https://tinyqueue.dev/")
    assert not links_homepage("https://tinyqueue.io/", "https://tinyqueue.dev/")

    @dc
    class Meta:
        host_id: int

    class FakeGitHub:
        def repos_metadata(self, names: list[str]) -> dict[str, Meta]:
            return {"old-owner/tq-old": Meta(42)} if "old-owner/tq-old" in names else {}

    stage = object.__new__(FactsStage)
    stage.github = FakeGitHub()
    case = PilotCase(
        "follow_through:gh:org/tinyqueue",
        "follow_through",
        "gh:org/tinyqueue",
        "org/tinyqueue",
        None,
        42,
        1,
        "winner",
        1,
        {},
    )
    hp = "https://tinyqueue.dev/"

    def basis(url: str, links: bool = False) -> str | None:
        return stage._confirmation({"url": url, "links_repo": links}, case, hp, 42)

    assert basis("https://github.com/org/tinyqueue", links=True) == "links_repo"
    assert basis("https://github.com/old-owner/tq-old") == "links_repo_renamed"
    assert basis("https://github.com/someone/else") is None
    assert basis("https://tinyqueue.dev/launch") == "links_homepage"
    # the verifier's cases: a host containing the name, not the homepage, is not confirmation
    assert basis("https://tinyqueue.ai/") is None
    assert basis("https://get-tinyqueue.com/pm") is None
    stage.github = None
    assert basis("https://github.com/old-owner/tq-old") is None  # no way to resolve it


def test_m24_r2_open_burst_first_7_days() -> None:
    """[M24-T2] verifier round 2 fix 4 (size-v2): a burst that never returns to baseline by
    the series' end is `open`; its first 7 days are measured separately from its total so far."""
    from pigtail.forensics.facts import bursts

    s = {}
    d = date(2026, 1, 1)
    while d < date(2026, 3, 10):
        s[d] = 1
        d += timedelta(days=1)
    for i in range(60):  # a long growth phase that never calms down
        s[date(2026, 3, 10) + timedelta(days=i)] = 300 + i
    b = bursts(s, [], created=date(2026, 1, 1))[0]
    assert b["open"] is True and b["end_day"] is None
    assert b["stars_first_7d"] == sum(300 + i for i in range(7)) and b["first_7d_complete"]
    assert b["stars_total"] == sum(300 + i for i in range(60))
