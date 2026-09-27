"""ADR-085 on Postgres (synthetic data, fakes only, no network): view B's Product Hunt launches
(slug route, topic scan, the product slot, confirmation by URL, domain, keywords or the Haiku
check, fail-closed; only ids, times and counts stored; raw answers dropped; refused without a
token) and declared maintainers' Bluesky posts (accounts from the homepage field, README, profile
social accounts and org page; searches with author and url filters only; only kind, time, role
and match stored, no handle or DID anywhere in the database; evidence URLs with a placeholder;
incomplete repos without a view-B anchor, counted, retried; more than 10 % incomplete refused).
Every name, handle, DID and text is made up (`org-…`, `maintainer-a.example`, `SYNTH-…`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.launch_sources import (
    BSKY_SOURCE,
    PH_SOURCE,
    LaunchSourceIncomplete,
    LaunchSourceUnavailable,
    ph_confirmer,
)
from pigtail.briefs.runner import EXIT_NO_LAUNCH_LOOKUP, RunDeps, _selection_blocked
from pigtail.briefs.selection_store import run_stage, view
from pigtail.briefs.shortlist import Shortlist
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import RetryPolicy, TokenBucket
from pigtail.connectors.bluesky import SEARCH_PARAMS, BlueskySearchConnector
from pigtail.connectors.producthunt import QUERIES, ProductHuntConnector
from pigtail.llm.store import LLMStore
from pigtail.privacy import requests
from pigtail.privacy.deletion import DeletionLog
from tests.discovery_fake import FakeShowHN
from tests.github_fake import FakeGitHub
from tests.integration.test_github_per_repo_m1t24 import connector
from tests.integration.test_launch_lookup_m22 import NOW, hn_connector, prereg
from tests.launch_sources_fake import (
    DID_B,
    HANDLE_A,
    HANDLE_C,
    HANDLE_OTHER,
    PH_TOKEN,
    FakeBluesky,
    FakeProductHunt,
    bsky_post,
    did_of,
    ph_post,
)
from tests.selection_fake import brief as synthetic_brief
from tests.surface_fake import default_coder
from tests.unit.test_m22_views import VerdictBackend, guard, llm

pytestmark = pytest.mark.db
HANDLE_D = "maintainer-d.example"


def t(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def seed(capture_db: Any, repos: dict[str, dict[str, Any]], version: int = 1) -> Any:
    capture_db.conn.autocommit = True
    b = synthetic_brief(minimums={}, primary_threshold="at_least_median")
    if version != 1:
        b = b.model_copy(update={"version": version, "notes": f"v{version}"})
    store = CandidateStore(capture_db.conn, b.brief_id, version)
    for i, (name, meta) in enumerate(repos.items()):
        store.upsert(
            Candidate(
                ref=f"gh:{name}",
                repo_full_name=name,
                repo_host_id=8_970_001 + 100 * version + i,
                metadata={
                    "created_at": "2025-01-01T00:00:00+00:00",
                    "language": "Go",
                    "description": meta.get("description", "a synthetic project"),
                    "topics": ["synthetic"],
                    "homepage_domain": meta.get("homepage_domain", ""),
                    "owner_type": meta.get("owner_type", "Organization"),
                },
            ),
            brief_run_id=None,
            now=NOW,
        )
    sl = Shortlist(capture_db.conn, b)
    sl.ensure(None)
    sl.decide([f"gh:{n}" for n in repos], "accept", "synthetic", reviewer="owner")
    sl.finalize(reviewer="owner")
    return b


def ph_conn(capture_db: Any, fake: FakeProductHunt, tmp: Path, token: str | None = PH_TOKEN):
    return ProductHuntConnector(
        store=LocalSnapshotStore(tmp / "snap"),
        http=fake.client(),
        env={} if token is None else {"PH_API_TOKEN": token},
        evidence_sink=capture_db.upsert_evidence,
        sleep=lambda s: None,
        limiter=TokenBucket(1000, burst=1000),
        retry=RetryPolicy(max_retries=1),
        clock=lambda: NOW,
    )


def bsky_conn(capture_db: Any, fake: FakeBluesky, tmp: Path) -> BlueskySearchConnector:
    return BlueskySearchConnector(
        store=LocalSnapshotStore(tmp / "snap"),
        http=fake.client(),
        env={},
        evidence_sink=capture_db.upsert_evidence,
        sleep=lambda s: None,
        limiter=TokenBucket(1000, burst=1000),
        retry=RetryPolicy(max_retries=1),
        clock=lambda: NOW,
    )


def stage(conn: Any, b: Any, tmp: Path, **kw: Any) -> Any:
    cp: dict[str, Any] = kw.pop("checkpoint", {})
    return run_stage(
        conn,
        b,
        brief_run_id=None,
        github=kw.pop("github", None),
        checkpoint=cp,
        save_checkpoint=lambda _c: None,
        run_date=NOW.date(),
        clock=lambda: NOW,
        hn=kw.pop("hn"),
        coder=default_coder(),
        **kw,
    )


TABLES = (
    "brief_candidate",
    "brief_selection",
    "brief_selection_case",
    "evidence",
    "deletion_log",
    "runs",
)


def dump(conn: Any) -> str:
    return " ".join(
        json.dumps([str(x) for x in r])
        for table in TABLES
        for r in conn.execute(f"SELECT * FROM {table}").fetchall()
    )


# --- Product Hunt ---------------------------------------------------------------------------------
P1, P2, P3, P4, P5 = (
    "org-p/stellarlint", "org-q/quartzmesh", "org-r/nimbuscache", "org-s/orbitdeck", "org-t/lint",
)  # fmt: skip
PH_REPOS = {
    P1: {"description": "Lints YAML pipelines and Terraform modules"},
    P2: {"description": "A sandbox for service meshes", "homepage_domain": "quartzmesh.example"},
    P3: {"description": "A distributed cache for build artifacts"},
    P4: {"description": "Presentation decks from markdown notes"},
    P5: {"description": "Lints things"},
}
PH_POSTS = [
    # P1: found by slug and by the open-source topic; the description links the repo
    ph_post("901", "StellarLint", slug="stellarlint", description="Source: github.com/org-p/"
            "stellarlint SYNTH-DESC-901", votes=321, comments=45, created=t(2025, 9, 9),
            featured=t(2025, 9, 10), topics=("open-source",)),
    # P2: its slug differs (topic scan only); names the homepage domain; never featured
    ph_post("902", "Quartz Mesh", slug="quartz-mesh-2", description="Docs at quartzmesh.example "
            "SYNTH-DESC-902", votes=40, comments=3, created=t(2025, 6, 1),
            topics=("developer-tools",)),
    # the slug `quartzmesh` belongs to another product: not the product slot
    ph_post("903", "QuartzMesh Cloud", slug="quartzmesh", created=t(2025, 6, 2)),
    # P3: two keywords shared with the repo description
    ph_post("904", "NimbusCache", slug="nimbuscache", tagline="Distributed cache for artifacts",
            created=t(2025, 7, 1), featured=t(2025, 7, 2), votes=12, comments=1),
    # P4: nothing deterministic confirms it: the Haiku check decides
    ph_post("905", "OrbitDeck", slug="orbitdeck", tagline="SYNTH TRUE-POST slides",
            created=t(2025, 8, 1), featured=t(2025, 8, 1), votes=77, comments=5),
    # P5: a short name, confirmed only by a URL or domain: not here
    ph_post("906", "Lint", slug="lint", tagline="Lints things for markdown files",
            created=t(2025, 5, 1)),
]  # fmt: skip
PH_TEXTS = ("SYNTH-DESC", "SYNTH-TAGLINE", "TRUE-POST", "Quartz Mesh", "QuartzMesh Cloud",
            "SYNTH-MAKER", "synth-maker-handle", "Distributed cache for artifacts")  # fmt: skip


def run_ph(capture_db: Any, tmp: Path, monkeypatch: Any, *, approved: bool = True) -> Any:
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "true")
    b = seed(capture_db, PH_REPOS)
    prereg(capture_db.conn, b, tmp)
    fake = FakeProductHunt(PH_POSTS)
    store = LLMStore(":memory:")
    be = VerdictBackend()
    conf = None
    if approved:
        g = guard(store)
        conf = ph_confirmer(
            llm(be, store), before_submit=g.before_submit,
            check_backend=lambda x: g.check_backend(x, job="ph_match_check"),
            sleep=lambda s: None, poll_seconds=0,
        )  # fmt: skip
    res = stage(capture_db.conn, b, tmp, hn=hn_connector(capture_db, FakeShowHN([]), tmp),
                ph=ph_conn(capture_db, fake, tmp), ph_confirmer=conf)  # fmt: skip
    return b, fake, res, be


def test_product_hunt_slug_route_topic_scan_confirmation_and_storage(
    capture_db, tmp_path, monkeypatch
):
    b, fake, res, be = run_ph(capture_db, tmp_path, monkeypatch)
    ph = res.fetch["product_hunt"]
    assert ph["repos"] == 5 and ph["looked_up"] == 5
    assert ph["topic_status"] == {"developer-tools": "complete", "open-source": "complete"}
    assert ph["confirmed"] == {"description_keywords": 1, "github_url": 1, "haiku": 1,
                               "homepage_domain": 1}  # fmt: skip
    assert ph["unconfirmed"] == {"short_or_common_name": 1} and ph["not_product_slot"] == 1
    assert ph["by_route"] == {"slug": 3, "slug+topic": 1, "topic": 1} and ph["haiku_checks"] == 1
    assert be.calls == 1  # only the match rules 1-3 left open went to the model
    # every request is one of our queries: project-level fields only
    assert all(r["query"] in QUERIES for r in fake.requests)
    # slug lookups: 2 for the names that differ without hyphens, else 1; 2 topic pages
    assert sum(1 for r in fake.requests if "slug" in r["variables"]) == 5
    assert sum(1 for r in fake.requests if "topic" in r["variables"]) == 2
    cs = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}
    (sig,) = [s for s in cs[f"gh:{P1}"].sources if s["source"] == PH_SOURCE]
    (rec,) = sig["posts"]
    assert set(rec) == {"ph_post_id", "created_at", "featured_at", "votes", "comments", "route",
                        "rule", "confirmed", "confirmation"}  # fmt: skip
    assert (rec["ph_post_id"], rec["votes"], rec["comments"], rec["route"]) == (
        "901", 321, 45, "slug+topic",
    )  # fmt: skip
    (p4,) = next(s for s in cs[f"gh:{P4}"].sources if s["source"] == PH_SOURCE)["posts"]
    assert p4["confirmation"] == "haiku" and p4["confirmation_provenance"]["prompt_id"] == (
        "ph_match_check"
    )
    # view B anchors on the Product Hunt launches: featuredAt, else createdAt
    v = view(capture_db.conn, b.brief_id, 1)
    rows = {r["repo_full_name"]: r["detail"] for r in v["cases_by_view"]["launch"]}
    assert rows[P1]["anchor_rule"] == "product_hunt"
    assert rows[P1]["anchor"]["at"] == t(2025, 9, 10).isoformat()
    assert rows[P1]["anchor"]["ref"] == "901"
    assert rows[P1]["anchor"]["via"] == "product_hunt:slug+topic"
    assert rows[P2]["anchor"]["at"] == t(2025, 6, 1).isoformat()
    assert rows[P5]["anchor_rule"] != "product_hunt"
    # votes and comments: secondary launch-size measures, reported with no percentile
    assert rows[P1]["values"]["att.ph_votes"]["value"] == 321.0
    assert rows[P1]["values"]["att.ph_comments"]["value"] == 45.0
    assert "percentile" not in rows[P1]["values"]["att.ph_votes"]
    assert v["selection"]["summary"]["view_b_anchor_rules"]["product_hunt"] == 4
    assert v["selection"]["params"]["launch_sources"]["product_hunt"]["applies"] is True
    # nothing but ids, times and counts: no name, tagline, description or maker in the database
    text = dump(capture_db.conn)
    for marker in PH_TEXTS:
        assert marker not in text, marker
    # raw answers dropped right after parsing; evidence URLs name the repo or topic only
    ev = capture_db.conn.execute(
        "SELECT url, deletion_state FROM evidence WHERE source = 'producthunt'"
    ).fetchall()
    assert len(ev) == len(fake.requests) and {s for _, s in ev} == {"raw_dropped"}
    assert all("query" not in u and PH_TOKEN not in u for u, _ in ev)
    assert not list((tmp_path / "snap").rglob("*")) or all(
        b"SYNTH-DESC" not in f.read_bytes() for f in (tmp_path / "snap").rglob("*") if f.is_file()
    )


def test_the_haiku_check_fails_closed(capture_db, tmp_path, monkeypatch):
    b, _fake, res, be = run_ph(capture_db, tmp_path, monkeypatch, approved=False)
    assert res.fetch["product_hunt"]["unconfirmed"] == {
        "haiku_unavailable": 1, "short_or_common_name": 1,
    }  # fmt: skip
    assert be.calls == 0
    rows = {r["repo_full_name"]: r["detail"]
            for r in view(capture_db.conn, b.brief_id, 1)["cases_by_view"]["launch"]}  # fmt: skip
    assert rows[P4]["anchor_rule"] != "product_hunt"


def test_the_selection_is_refused_without_the_product_hunt_token(capture_db, tmp_path, monkeypatch):
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "true")
    b = seed(capture_db, PH_REPOS)
    prereg(capture_db.conn, b, tmp_path)
    fake_hn, fake = FakeShowHN([]), FakeProductHunt(PH_POSTS)
    for ph in (None, ph_conn(capture_db, fake, tmp_path, token=None)):
        with pytest.raises(LaunchSourceUnavailable, match="PH_API_TOKEN"):
            stage(capture_db.conn, b, tmp_path, hn=hn_connector(capture_db, fake_hn, tmp_path),
                  ph=ph)  # fmt: skip
    assert fake_hn.requests == [] and fake.requests == []
    assert capture_db.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0
    # `pigtail run` refuses with exit 8 (the HN lookup's refusal family), before any fetch
    deps = RunDeps(conn=capture_db.conn, client=None, hn=object(), ph=None)  # type: ignore[arg-type]
    code, why = _selection_blocked(capture_db.conn, b, deps) or (0, "")
    assert code == EXIT_NO_LAUNCH_LOOKUP == 8 and "PH_API_TOKEN" in why
    # with the flag off (pre-registered so), no token is needed
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "false")
    assert _selection_blocked(capture_db.conn, b, deps) is not None  # params changed: exit 7
    prereg(capture_db.conn, b, tmp_path)
    assert _selection_blocked(capture_db.conn, b, deps) is None


# --- Bluesky --------------------------------------------------------------------------------------
B1, B2, B3, B4, B5 = (
    "org-b1/alpha-tool", "org-b2/beta-kit", "org-b3/gamma-lib", "org-b4/delta-app",
    "org-b5/echo-cli",
)  # fmt: skip
BSKY_REPOS = {
    B1: {"owner_type": "User"},
    B2: {"owner_type": "Organization"},
    B3: {"owner_type": "Organization"},
    B4: {"owner_type": "User"},
    B5: {"owner_type": "User"},
}
READMES = {
    B3: b"# Gamma\nNews: @" + HANDLE_C.encode() + b"\n",
    B5: b"# Echo\nNo social links here. Mail: someone@example.org\n",
}
BSKY_POSTS = [
    bsky_post(1, HANDLE_A, t(2025, 8, 1), ["https://github.com/org-b1/alpha-tool"]),
    bsky_post(2, HANDLE_A, t(2025, 11, 1), ["https://github.com/org-b1/alpha-tool/releases"]),
    # an account nobody declared: never searched, never counted
    bsky_post(3, HANDLE_OTHER, t(2025, 3, 1), ["https://github.com/org-b1/alpha-tool"]),
    bsky_post(4, "beta-kit-team.example", t(2025, 5, 5), ["https://betakit.example/docs"],
              did=DID_B),
    bsky_post(5, HANDLE_D, t(2025, 4, 4), ["https://github.com/org-b4/delta-app"]),
    # the declared account posts about something else: its links don't match
    bsky_post(6, HANDLE_A, t(2025, 2, 2), ["https://github.com/org-b1/alpha-tool-extras"]),
]  # fmt: skip


def gh_fake(capture_db: Any, tmp: Path) -> tuple[FakeGitHub, Any]:
    gh = FakeGitHub(now=lambda: NOW)
    gh.links = {
        B1: (None, "User"),
        B2: ("https://betakit.example", "Organization"),
        B3: (None, "Organization"),
        B4: (f"https://bsky.app/profile/{HANDLE_D}", "User"),
        B5: ("https://echo.example", "User"),
    }
    gh.social = {
        "org-b1": [
            {"provider": "bluesky", "url": f"https://bsky.app/profile/{HANDLE_A}"},
            {"provider": "generic", "url": "https://example.org/synth"},
        ]
    }
    gh.orgs = {"org-b2": {"login": "org-b2", "blog": f"https://bsky.app/profile/{DID_B}",
                          "description": "SYNTH-ORG-DESCRIPTION"}}  # fmt: skip
    return gh, connector(capture_db, gh, tmp)


def readme_of(texts: dict[str, bytes]) -> Any:
    """A README loader over synthetic texts (the selection's loader reads the snapshot store)."""
    return lambda c: (texts.get(str(c.repo_full_name)), None)


def run_bsky(capture_db: Any, tmp: Path, monkeypatch: Any, repos: Any = None, **kw: Any) -> Any:
    monkeypatch.setenv("PIGTAIL_SELECTION_BLUESKY", "true")
    b = seed(capture_db, repos or BSKY_REPOS, kw.pop("version", 1))
    prereg(capture_db.conn, b, tmp)
    fake = kw.pop("fake", None) or FakeBluesky(BSKY_POSTS)
    gh, ghc = gh_fake(capture_db, tmp)
    cp: dict[str, Any] = kw.pop("checkpoint", {})
    res = stage(capture_db.conn, b, tmp, hn=hn_connector(capture_db, FakeShowHN([]), tmp),
                bsky=bsky_conn(capture_db, fake, tmp), github=ghc,
                readme=readme_of(kw.pop("readmes", READMES)), checkpoint=cp)  # fmt: skip
    return b, fake, gh, res, cp


def test_bluesky_declared_accounts_searches_and_view_b_anchor(capture_db, tmp_path, monkeypatch):
    b, fake, gh, res, cp = run_bsky(capture_db, tmp_path, monkeypatch)
    bs = res.fetch["bluesky"]
    assert bs["status"] == {"complete": 4, "no_declared_account": 1} and bs["incomplete"] == 0
    assert bs["declared_in"] == {"github_profile": 1, "homepage": 1, "org_page": 1, "readme": 1}
    # searches: author = a declared account, url = the repo URL (and the homepage for beta-kit)
    # (a declared handle is resolved to its DID first, in memory; the search uses the DID)
    searches = [r for r in fake.requests if r.url.path.endswith("searchPosts")]
    seen = [(r.url.params["author"], r.url.params["url"]) for r in searches]
    assert sorted(seen) == sorted([
        (did_of(fake, HANDLE_A), "https://github.com/org-b1/alpha-tool"),
        (DID_B, "https://github.com/org-b2/beta-kit"),
        (DID_B, "https://betakit.example"),
        (did_of(fake, HANDLE_C), "https://github.com/org-b3/gamma-lib"),
        (did_of(fake, HANDLE_D), "https://github.com/org-b4/delta-app"),
    ])  # fmt: skip
    resolves = [r for r in fake.requests if r.url.path.endswith("resolveHandle")]
    assert {r.url.params["handle"] for r in resolves} == {HANDLE_A, HANDLE_C, HANDLE_D}
    for r in searches:
        assert set(r.url.params) <= SEARCH_PARAMS and r.url.params["sort"] == "latest"
        assert "since" not in r.url.params and "until" not in r.url.params  # window in memory
    assert HANDLE_OTHER not in {a for a, _ in seen} | {r.url.params["handle"] for r in resolves}
    # GitHub: profile social accounts for user owners, the org page for org owners
    paths = [r.url.path for r in gh.requests]
    assert "/users/org-b1/social_accounts" in paths and "/orgs/org-b2" in paths
    assert not any(p.endswith("/followers") or "/following" in p for p in paths)
    # stored per repo: status and kind/time/role/match only
    cs = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}
    (s1,) = [s for s in cs[f"gh:{B1}"].sources if s["source"] == BSKY_SOURCE]
    assert set(s1) == {"source", "rule", "status", "posts"}
    assert s1["posts"] == [
        {"kind": "bluesky_maintainer_post", "time": t(2025, 8, 1).isoformat(),
         "role": "maintainer", "match": "repo_url"},
        {"kind": "bluesky_maintainer_post", "time": t(2025, 11, 1).isoformat(),
         "role": "maintainer", "match": "repo_url"},
    ]  # fmt: skip
    (s2,) = [s for s in cs[f"gh:{B2}"].sources if s["source"] == BSKY_SOURCE]
    assert [p["match"] for p in s2["posts"]] == ["homepage_url"]
    # view B: the earliest maintainer post anchors, later ones are relaunch events
    v = view(capture_db.conn, b.brief_id, 1)
    rows = {r["repo_full_name"]: r["detail"] for r in v["cases_by_view"]["launch"]}
    assert rows[B1]["anchor_rule"] == "bluesky_maintainer_post"
    assert rows[B1]["anchor"]["at"] == t(2025, 8, 1).isoformat()
    assert rows[B1]["anchor"]["via"] == "bluesky:repo_url"
    assert [e["kind"] for e in rows[B1]["relaunch_events"]] == ["bluesky_maintainer_post"]
    assert rows[B2]["anchor"]["via"] == "bluesky:homepage_url"
    assert rows[B4]["anchor"]["at"] == t(2025, 4, 4).isoformat()
    assert v["selection"]["summary"]["view_b_anchor_rules"]["bluesky_maintainer_post"] == 3
    # no handle, DID, post URI, text or count anywhere in the database or the checkpoint
    text = dump(capture_db.conn) + json.dumps(cp)
    for marker in (HANDLE_A, DID_B, HANDLE_C, HANDLE_D, HANDLE_OTHER, "beta-kit-team",
                   "abcdefghijklmnopqrstuvwx", "at://", "SYNTH-POST-TEXT", "SYNTH-DISPLAY-NAME",
                   "SYNTH-ORG-DESCRIPTION", "likeCount", "bafy"):  # fmt: skip
        assert marker not in text, marker
    # evidence: a placeholder for the author, raw answers dropped (Bluesky and GitHub)
    ev = capture_db.conn.execute(
        "SELECT url, deletion_state FROM evidence WHERE source = 'bluesky_search'"
    ).fetchall()
    assert len(ev) == 5 and {s for _, s in ev} == {"raw_dropped"}
    assert all("author=[declared-account]" in u and "launch_source_repo=" in u for u, _ in ev)
    gh_ev = capture_db.conn.execute(
        "SELECT deletion_state FROM evidence WHERE url LIKE %s OR url LIKE %s OR url LIKE %s",
        ("%/social_accounts%", "%/orgs/%", "%/graphql%"),
    ).fetchall()
    assert gh_ev and {s for (s,) in gh_ev} == {"raw_dropped"}
    for f in (tmp_path / "snap").rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            assert HANDLE_A.encode() not in data and DID_B.encode() not in data, f
    # a repo opt-out removes its launch-source evidence rows too
    capture_db.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:8970101', 'github', 8970101, %s, %s)",
        (B1, NOW),
    )
    requests.purge_repo(capture_db, LocalSnapshotStore(tmp_path / "snap"), "github:8970101",
                        DeletionLog(capture_db, "objection"))  # fmt: skip
    left = capture_db.conn.execute(
        "SELECT count(*) FROM evidence WHERE lower(url) LIKE %s", (f"%{B1}%",)
    ).fetchone()[0]
    assert left == 0


TEN = {f"org-x{i}/repo-{i:02d}": {"owner_type": "User"} for i in range(10)}


def ten(fail: int) -> tuple[FakeBluesky, dict[str, bytes]]:
    """Ten repos; the first `fail` + 1 declare an account in their README; the searches of the
    first `fail` fail (an outage), so those repos are incomplete."""
    fake = FakeBluesky([])
    fake.fail_urls = {f"https://github.com/org-x{i}/repo-{i:02d}" for i in range(fail)}
    return fake, {f"org-x{i}/repo-{i:02d}": f"@{HANDLE_C}".encode() for i in range(fail + 1)}


def test_incomplete_bluesky_repos_have_no_anchor_are_counted_and_retried(
    capture_db, tmp_path, monkeypatch
):
    fake, texts = ten(1)
    b, fake, _gh, res, cp = run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake,
                                     readmes=texts)  # fmt: skip
    bs = res.fetch["bluesky"]
    assert bs["incomplete"] == 1 and bs["incomplete_reasons"] == {"search_failed": 1}
    assert res.counts["view_b_incomplete"] == {"bluesky": 1}  # 1 of 10: not over 10 %
    v = view(capture_db.conn, b.brief_id, 1)
    rows = {r["repo_full_name"]: r for r in v["cases_by_view"]["launch"]}
    r0 = rows["org-x0/repo-00"]
    assert r0["role"] == "no_anchor"
    assert r0["detail"]["anchor_reason"] == "launch_source_incomplete:bluesky"
    assert v["selection"]["summary"]["view_b_incomplete"] == {"bluesky": 1}
    assert any("Bluesky data incomplete" in w for w in v["selection"]["summary"]["warnings"])
    # the checkpoint keeps the complete repos; the next run retries only the incomplete one
    assert "gh:org-x0/repo-00" not in cp["fetch"]["bsky_done"]
    assert len(cp["fetch"]["bsky_done"]) == 9
    n = len(fake.requests)
    fake.fail_urls = set()
    res2 = stage(capture_db.conn, b, tmp_path,
                 hn=hn_connector(capture_db, FakeShowHN([]), tmp_path),
                 bsky=bsky_conn(capture_db, fake, tmp_path),
                 github=gh_fake(capture_db, tmp_path)[1], readme=readme_of(texts),
                 checkpoint=cp)  # fmt: skip
    assert len(fake.requests) == n + 2 and len(cp["fetch"]["bsky_done"]) == 10  # resolve+search
    assert res2.fetch["bluesky"]["searched"] == 1 and res2.fetch["bluesky"]["already_done"] == 9
    assert res2.counts["view_b_incomplete"] == {}


def test_more_than_ten_percent_incomplete_refuses_the_selection(capture_db, tmp_path, monkeypatch):
    fake, texts = ten(2)
    with pytest.raises(LaunchSourceIncomplete, match="more than 10%") as ei:
        run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake, readmes=texts)
    assert HANDLE_C not in str(ei.value)
    assert capture_db.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0


def test_the_selection_is_refused_with_the_bluesky_connector_off(capture_db, tmp_path, monkeypatch):
    monkeypatch.setenv("PIGTAIL_SELECTION_BLUESKY", "true")
    b = seed(capture_db, BSKY_REPOS)
    prereg(capture_db.conn, b, tmp_path)
    off = BlueskySearchConnector(
        store=LocalSnapshotStore(tmp_path / "snap"),
        env={"PIGTAIL_CONNECTOR_BLUESKY_SEARCH_ENABLED": "false"},
    )
    with pytest.raises(LaunchSourceUnavailable, match="bluesky_search"):
        stage(capture_db.conn, b, tmp_path, hn=hn_connector(capture_db, FakeShowHN([]), tmp_path),
              bsky=off)  # fmt: skip
    deps = RunDeps(conn=capture_db.conn, client=None, hn=object(), bsky=off)  # type: ignore[arg-type]
    blockedby = _selection_blocked(capture_db.conn, b, deps)
    assert blockedby is not None and blockedby[0] == 8 and "bluesky_search" in blockedby[1]


# --- `pigtail run`: exit 8 without a source, exit 9 on an outage (resumable) --------------------
def test_run_refuses_with_exit_8_and_fails_resumably_with_exit_9(capture_db, tmp_path, monkeypatch):
    from pigtail.briefs import outcomes
    from pigtail.briefs.runner import EXIT_SOURCE_INCOMPLETE, RunOptions, run_brief
    from tests.integration.test_brief_run_m22 import RUN_NOW, client, example, make_world
    from tests.integration.test_brief_run_m22 import run as run_world
    from tests.integration.test_selection_m22 import finalize
    from tests.integration.test_selection_m22 import prereg as prereg_world
    from tests.relevance_fake import RelevanceBatchBackend

    w = make_world(capture_db, tmp_path)
    fb = RelevanceBatchBackend()
    assert run_world(w, fb).status == "awaiting_review"
    finalize(w)
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "true")
    monkeypatch.setenv("PIGTAIL_SELECTION_BLUESKY", "true")
    prereg_world(w, example(), tmp_path)

    def go(ph: Any, bsky: Any) -> Any:
        deps = RunDeps(conn=w.conn, client=client(w, fb), github=w.github, hn=w.hnconn,
                       ph=ph, bsky=bsky, clock=lambda: RUN_NOW, sleep=lambda s: None)  # fmt: skip
        return run_brief(example(), deps, RunOptions(approve_paid=True, poll_seconds=0))

    bsky = bsky_conn(capture_db, FakeBluesky([]), tmp_path)
    refused = go(None, bsky)  # no Product Hunt token: refused before anything runs
    assert refused.exit_code == EXIT_NO_LAUNCH_LOOKUP and "PH_API_TOKEN" in refused.message
    assert w.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0

    def outage(*a: Any, **kw: Any) -> Any:
        raise LaunchSourceIncomplete("Bluesky: 3 of 8 shortlisted repos have incomplete data")

    monkeypatch.setattr(outcomes, "run_bluesky", outage)
    ph = ph_conn(capture_db, FakeProductHunt([]), tmp_path)
    failed = go(ph, bsky)
    assert failed.exit_code == EXIT_SOURCE_INCOMPLETE == 9 and failed.status == "failed"
    assert failed.stop and failed.stop["kind"] == "launch_source_incomplete"
    assert w.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0
    monkeypatch.undo()
    monkeypatch.setenv("PIGTAIL_SELECTION_PRODUCT_HUNT", "true")
    monkeypatch.setenv("PIGTAIL_SELECTION_BLUESKY", "true")
    again = go(ph, bsky)  # the same run resumes and completes once the outage is over
    assert again.exit_code == 0 and again.status == "succeeded", again.message
    assert again.brief_run_id == failed.brief_run_id and again.resumed


def test_a_declared_handle_that_names_no_account_is_skipped_not_incomplete(
    capture_db, tmp_path, monkeypatch
):
    """A dead or mistyped declared link (resolveHandle: 400) is skipped and counted; it never
    makes the repo incomplete (live acceptance run: 8 such repos were wrongly incomplete)."""
    fake = FakeBluesky([])
    texts = {f"org-x{i}/repo-{i:02d}": b"@nobody-zz9.bsky.social" for i in range(3)}
    _b, fake, _gh, res, _cp = run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake,
                                       readmes=texts)  # fmt: skip
    bs = res.fetch["bluesky"]
    assert bs["incomplete"] == 0 and bs["accounts_unresolvable"] == 3
    assert not [r for r in fake.requests if r.url.path.endswith("searchPosts")]


def test_a_rejected_search_is_incomplete_never_no_posts(capture_db, tmp_path, monkeypatch):
    """Verifier M22 round 7: a 400 from searchPosts after the handle resolved is an incomplete
    source (counted, retried), never a silent "no posts"."""
    fake, texts = ten(0)
    fake.bad_request = True
    _b, fake, _gh, res, _cp = run_bsky(capture_db, tmp_path, monkeypatch, TEN, fake=fake,
                                       readmes=texts)  # fmt: skip
    bs = res.fetch["bluesky"]
    assert bs["incomplete"] == 1 and bs["incomplete_reasons"] == {"search_failed": 1}
    assert bs["accounts_unresolvable"] == 0
