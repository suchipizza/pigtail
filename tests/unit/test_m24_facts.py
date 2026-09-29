"""M24 report facts: deterministic rules on synthetic text and series (ADR-089; M24-T2)."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

from pigtail.forensics.facts import (
    Event,
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
    found = detect_assets(README)
    assert "demo.gif" in found["demo_media"]
    assert found["install_one_liner"].strip() == "pipx install widget"
    assert "Benchmarks" in found["benchmarks"]
    assert "readthedocs" in found["docs_site"]
    assert "✅" in found["comparison_table"]
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
    def ev(kind: str, day: int, ref: str = "r") -> Event:
        return Event(kind, ref, datetime(2026, 3, day, 20, tzinfo=UTC), [], kind != "release")

    onset = date(2026, 3, 10)
    got = explain(onset, [ev("release", 10, "v1"), ev("show_hn", 10, "7"), ev("show_hn", 2)])
    assert got == {"event": "show_hn:7", "kind": "show_hn", "days_from_onset": 0}
    assert explain(onset, [ev("show_hn", 2)]) is None  # more than 3 days before: unexplained
    assert explain(onset, [ev("release", 12)]) is None  # more than 1 day after


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
