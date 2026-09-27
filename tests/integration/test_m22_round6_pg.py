"""Owner decisions of 2026-09-27 after M22 verifier round 6 (ADR-084) on Postgres (synthetic
data, fakes only, no network): the distribution-surface coding runs first, before any star,
anchor or outcome data, is stored per repo with its provenance, is cached, and fails closed
(no client, not approved, over a cap: the selection is refused, nothing fetched); view B's
anchor comes from launch events only (Show HN, launch-worded GitHub releases), with relaunch
events, and repos without one are anchored on their first external mention or first public
release and reported as their own sub-population; release names and bodies, comment texts and
authors are never stored; purge, export and inventory cover the new data. Every name, title,
tag and text here is made up (`org-…`, `SYNTH-…`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.budget import BudgetStop
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.selection_store import run_stage, view
from pigtail.briefs.shortlist import Shortlist
from pigtail.briefs.surface import SurfaceCoder, SurfaceCodingUnavailable
from pigtail.capture.inventory import inventory
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.export.jsonl import export_jsonl
from pigtail.llm.store import LLMStore
from pigtail.privacy import requests
from pigtail.privacy.deletion import DeletionLog
from tests.discovery_fake import FakeShowHN
from tests.github_fake import FakeGitHub
from tests.integration.test_github_per_repo_m1t24 import connector
from tests.integration.test_launch_lookup_m22 import NOW, hit, hn_connector, prereg
from tests.selection_fake import brief as synthetic_brief
from tests.surface_fake import SurfaceBackend, coder
from tests.unit.test_m22_views import guard, llm

pytestmark = pytest.mark.db

A, B_, C, D, E = (
    "org-a/alpha-cli",
    "org-b/bravo-lib",
    "org-c/charlie-mcp",
    "org-d/delta-app",
    "org-e/echo-plugin",
)
NAMES = (A, B_, C, D, E)


def t(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 12, tzinfo=UTC)


def rel(tag: str, when: datetime, name: str, body: str, pre: bool = False) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "published_at": when.isoformat().replace("+00:00", "Z"),
        "prerelease": pre,
        "name": name,
        "body": body,
    }


RELEASES = {
    # alpha: a Show HN (discovery) first, then a launch-worded release: a relaunch event
    A: [rel("v0.1", t(2025, 3, 1), "v0.1", "SYNTH-BODY-A0 first cut"),
        rel("v1.0", t(2025, 11, 1), "Launching alpha 1.0", "SYNTH-BODY-A1")],
    # bravo: a launch-worded release first, then a Show HN (lookup, URL-matched)
    B_: [rel("v0.1", t(2025, 2, 1), "v0.1", "SYNTH-BODY-B0"),
         rel("v1.0", t(2025, 9, 1), "SYNTH-NAME-B1", "Introducing bravo, SYNTH-BODY-B1")],
    # charlie: no launch wording; its first mention (a comment) is earlier than its first release
    C: [rel("v0.1", t(2025, 8, 1), "v0.1", "SYNTH-BODY-C0")],
    # delta: its first release is earlier than its first mention
    D: [rel("v0.1-rc", t(2025, 4, 1), "v0.1-rc", "SYNTH-BODY-D0", pre=True)],
    # echo: no release, no mention: no view-B anchor
}  # fmt: skip


def mention(item: int, url: str | None, when: datetime, text: str | None, kind: str) -> dict:
    h = {
        "objectID": str(item),
        "url": url,
        "created_at_i": int(when.timestamp()),
        "author": f"hnuser{item % 1000}",
        "_tags": [kind, f"author_hnuser{item % 1000}"],
    }
    if kind == "comment":
        h |= {"comment_text": text, "parent_id": item - 1}
    else:
        h |= {"title": "SYNTH-TITLE", "story_text": text}
    return h


HITS = [
    hit(9201, "Show HN: Bravo", "https://github.com/org-b/bravo-lib", t(2025, 12, 1), 12,
        "show_hn"),  # bravo's later Show HN: a relaunch event
    # charlie: a fuzzy near-miss first (another repo whose name extends charlie's), then the
    # real first mention, a comment linking the repo (HTML-escaped, as Algolia returns it)
    mention(9301, "https://github.com/org-c/charlie-mcp-extra", t(2025, 3, 1), None, "story"),
    mention(9302, None, t(2025, 6, 1),
            'SYNTH-COMMENT see <a href="https:&#x2F;&#x2F;github.com&#x2F;org-c&#x2F;charlie-mcp">'
            "it</a>", "comment"),
    mention(9303, None, t(2025, 10, 1), "SYNTH-COMMENT again github.com/org-c/charlie-mcp",
            "comment"),
    # delta: mentioned after its first release
    mention(9401, "https://github.com/org-d/delta-app", t(2025, 7, 1), None, "story"),
]  # fmt: skip


def seed(capture_db: Any) -> Any:
    capture_db.conn.autocommit = True
    b = synthetic_brief(minimums={}, primary_threshold="at_least_median")
    store = CandidateStore(capture_db.conn, b.brief_id, b.version)
    langs = {A: "Go", B_: "Python", C: "TypeScript", D: "Rust", E: "Shell"}
    for i, name in enumerate(NAMES):
        sources = []
        if name == A:  # discovery found alpha's Show HN
            sources = [{"source": "show_hn", "term": "t", "hn_item_id": 9101, "points": 30,
                        "title": "Show HN", "time": t(2025, 10, 5).isoformat()}]  # fmt: skip
        store.upsert(
            Candidate(
                ref=f"gh:{name}",
                repo_full_name=name,
                repo_host_id=8_960_001 + i,
                sources=sources,
                metadata={
                    "created_at": "2025-01-01T00:00:00+00:00",
                    "language": langs[name],
                    "description": f"a synthetic {name.split('-')[-1]} project",
                    "topics": ["synthetic"],
                    "homepage_domain": "",
                },
            ),
            brief_run_id=None,
            now=NOW,
        )
    sl = Shortlist(capture_db.conn, b)
    sl.ensure(None)
    sl.decide([f"gh:{n}" for n in NAMES], "accept", "synthetic", reviewer="owner")
    sl.finalize(reviewer="owner")
    return b


def github(capture_db: Any, tmp: Path) -> tuple[FakeGitHub, Any]:
    gh = FakeGitHub(now=lambda: NOW)
    gh.releases = {k: v for k, v in RELEASES.items()}
    return gh, connector(capture_db, gh, tmp)


def stage(conn: Any, b: Any, hn: Any, gh: Any, surface: Any) -> Any:
    return run_stage(
        conn,
        b,
        brief_run_id=None,
        github=gh,
        checkpoint={},
        save_checkpoint=lambda _c: None,
        run_date=NOW.date(),
        clock=lambda: NOW,
        hn=hn,
        coder=surface,
    )


class Watching(SurfaceCoder):
    """A coder that records how many HN and GitHub requests were made when it ran."""

    seen: tuple[int, int] | None = None
    fakes: tuple[FakeShowHN, FakeGitHub] | None = None

    def run(self, *a: Any, **kw: Any) -> Any:
        assert self.fakes is not None
        hn, gh = self.fakes
        Watching.seen = (len(hn.requests), len(gh.requests))
        return super().run(*a, **kw)


def watching(client: Any, g: Any, fakes: tuple[FakeShowHN, FakeGitHub]) -> Watching:
    w = Watching(
        client,
        before_submit=g.before_submit,
        check_backend=lambda b: g.check_backend(b, job="distribution_surface"),
        sleep=lambda s: None,
        poll_seconds=0,
    )
    w.fakes = fakes
    return w


def dump(conn: Any) -> str:
    return " ".join(
        str(r)
        for table in ("brief_candidate", "brief_selection", "brief_selection_case")
        for r in conn.execute(f"SELECT * FROM {table}").fetchall()
    )


# --- surface coding -------------------------------------------------------------------------------
def test_surface_coding_runs_first_is_stored_cached_and_shown_in_balance(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake_hn = FakeShowHN(HITS)
    gh, ghc = github(capture_db, tmp_path)
    store = LLMStore(":memory:")
    be = SurfaceBackend()
    g = guard(store)
    res = stage(capture_db.conn, b, hn_connector(capture_db, fake_hn, tmp_path), ghc,
                watching(llm(be, store), g, (fake_hn, gh)))  # fmt: skip
    # the coding ran before any HN or GitHub request of the selection (no star history, launch
    # lookup, release, mention or anchor existed yet), in one request of 20 repos at most
    assert Watching.seen == (0, 0) and len(be.calls) == 1
    assert res.counts["surface"]["coded"] == 5 and res.counts["surface"]["requests"] == 1
    sent = be.calls[0]
    for name in NAMES:  # the owner login never reaches the model
        assert name.split("/")[0] not in sent
    cs = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}
    rec = cs[f"gh:{C}"].metadata["distribution_surface"]
    assert rec["surface"] == "mcp_server" and rec["install_paths"] == ["npx"]
    assert set(rec) == {"surface", "install_paths", "coded_by", "coded_at", "rule"}
    by = rec["coded_by"]
    assert by["model"] == "claude-haiku-4-5-20251001" and by["prompt_id"] == "distribution_surface"
    assert by["prompt_version"] == "1" and by["cached"] is False
    assert datetime.fromisoformat(rec["coded_at"]).tzinfo is not None
    assert cs[f"gh:{A}"].metadata["distribution_surface"]["surface"] == "cli"
    # surface and install path are balance rows (SMD per level) in every view; never exclude
    sel = view(capture_db.conn, b.brief_id, 1)["selection"]
    for key in ("follow_through", "launch", "launch_undeclared"):
        covs = sel["balance"][key]["covariates"]
        assert "surface" in covs and "install_path" in covs and "language_group" in covs
    for rows in view(capture_db.conn, b.brief_id, 1)["cases_by_view"].values():
        for r in rows:
            assert "surface" not in ((r["detail"].get("pair") or {}).get("excluded_on") or [])
    # coded before any outcome, so a later run reuses the stored coding (no model call)
    stage(capture_db.conn, b, hn_connector(capture_db, FakeShowHN(HITS), tmp_path), ghc,
          coder(llm(be, store), guard(store, approved=False)))  # fmt: skip
    assert len(be.calls) == 1
    # with the stored coding gone, the LLM cache answers without approval or a model call
    for ref in cs:
        capture_db.conn.execute(
            "UPDATE brief_candidate SET metadata = metadata - 'distribution_surface'"
            " WHERE candidate_ref = %s",
            (ref,),
        )
    stage(capture_db.conn, b, hn_connector(capture_db, FakeShowHN(HITS), tmp_path), ghc,
          coder(llm(be, store), guard(store, approved=False)))  # fmt: skip
    assert len(be.calls) == 1
    cs2 = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}
    assert cs2[f"gh:{C}"].metadata["distribution_surface"]["coded_by"]["cached"] is True


@pytest.mark.parametrize("case", ["no_coder", "no_client", "not_approved", "over_cap"])
def test_the_selection_is_refused_without_the_surface_coding(capture_db, tmp_path, case):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake_hn = FakeShowHN(HITS)
    gh, ghc = github(capture_db, tmp_path)
    store = LLMStore(":memory:")
    be = SurfaceBackend()
    surface = {
        "no_coder": None,
        "no_client": SurfaceCoder(None),
        "not_approved": coder(llm(be, store), guard(store, approved=False)),
        "over_cap": coder(llm(be, store), guard(store, cap=1e-9)),
    }[case]
    err: type[Exception] = (
        BudgetStop if case in ("not_approved", "over_cap") else (SurfaceCodingUnavailable)
    )
    with pytest.raises(err) as ei:
        stage(capture_db.conn, b, hn_connector(capture_db, fake_hn, tmp_path), ghc, surface)
    if isinstance(ei.value, BudgetStop):
        assert ei.value.kind == ("approval" if case == "not_approved" else "money")
    # fail closed: nothing fetched, nothing computed or stored, no model call
    assert fake_hn.requests == [] and gh.requests == [] and be.calls == []
    assert capture_db.conn.execute("SELECT count(*) FROM brief_selection").fetchone()[0] == 0
    cs = CandidateStore(capture_db.conn, b.brief_id, 1).all()
    assert not any("distribution_surface" in c.metadata for c in cs)


def test_a_repo_the_model_leaves_out_is_retried_then_unknown(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    store = LLMStore(":memory:")
    once = SurfaceBackend(forget={"delta-app"})
    stage(capture_db.conn, b, hn_connector(capture_db, FakeShowHN([]), tmp_path), None,
          coder(llm(once, store), guard(store)))  # fmt: skip
    assert len(once.calls) == 2  # the plan, then one retry request for the missing repo
    cs = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}
    assert cs[f"gh:{D}"].metadata["distribution_surface"]["surface"] == "hosted_app"
    b2 = seed_again(capture_db, b, tmp_path)
    never = SurfaceBackend(forget={"delta-app"}, always_forget=True)
    res = stage(capture_db.conn, b2, hn_connector(capture_db, FakeShowHN([]), tmp_path), None,
                coder(llm(never, LLMStore(":memory:")), guard(store)))  # fmt: skip
    assert res.counts["surface"]["missing"] == 1
    got = {c.ref: c for c in CandidateStore(capture_db.conn, b2.brief_id, 2).all()}
    rec = got[f"gh:{D}"].metadata["distribution_surface"]
    assert rec["surface"] == "unknown" and rec["install_paths"] == ["unknown"] and rec["missing"]


def seed_again(capture_db: Any, b: Any, tmp: Path) -> Any:
    """The same shortlist as brief version 2 (a fresh coding)."""
    b2 = b.model_copy(update={"version": 2, "notes": "v2"})
    store = CandidateStore(capture_db.conn, b2.brief_id, 2)
    for c in CandidateStore(capture_db.conn, b.brief_id, 1).all():
        meta = {k: v for k, v in c.metadata.items() if k != "distribution_surface"}
        store.upsert(
            Candidate(c.ref, c.repo_full_name, repo_host_id=c.repo_host_id, metadata=meta),
            brief_run_id=None,
            now=NOW,
        )
    sl = Shortlist(capture_db.conn, b2)
    sl.ensure(None)
    sl.decide([f"gh:{n}" for n in NAMES], "accept", "synthetic", reviewer="owner")
    sl.finalize(reviewer="owner")
    prereg(capture_db.conn, b2, tmp)
    return b2


# --- view B's anchor ------------------------------------------------------------------------------
def test_view_b_anchors_on_launch_events_relaunches_and_undeclared_launches(capture_db, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    fake_hn = FakeShowHN(HITS)
    _gh, ghc = github(capture_db, tmp_path)
    store = LLMStore(":memory:")
    res = stage(capture_db.conn, b, hn_connector(capture_db, fake_hn, tmp_path), ghc,
                coder(llm(SurfaceBackend(), store), guard(store)))  # fmt: skip
    f = res.fetch
    assert f["releases"]["status"] == {"complete": 5} and f["releases"]["pages"] == 5
    # the first mention is searched only for repos without a launch event (charlie, delta, echo)
    assert f["mentions"]["searched"] == 3 and f["mentions"]["not_needed"] == 2
    assert f["mentions"]["status"] == {"found": 2, "none": 1}
    v = view(capture_db.conn, b.brief_id, 1)
    rows = {r["repo_full_name"]: r["detail"] for r in v["cases_by_view"]["launch"]}
    want = {
        A: ("show_hn", "9101", False),
        B_: ("release_launch", "v1.0", False),
        C: ("undeclared:first_mention", "9302", True),
        D: ("undeclared:first_release", "v0.1-rc", True),
        E: ("none", None, False),
    }
    for name, (rule, ref, und) in want.items():
        d = rows[name]
        assert d["anchor_rule"] == rule and d["undeclared_launch"] is und, name
        assert (d["anchor"] or {}).get("ref") == ref, name
    assert rows[C]["anchor"]["via"] == "hn_comment"  # the near-miss story was not taken
    assert rows[C]["anchor"]["at"] == t(2025, 6, 1).isoformat()
    # relaunch events: alpha's launch-worded release, bravo's later Show HN (lookup, URL)
    assert [(e["kind"], e["ref"]) for e in rows[A]["relaunch_events"]] == [
        ("release_launch", "v1.0")
    ]
    assert [(e["kind"], e["ref"], e["via"]) for e in rows[B_]["relaunch_events"]] == [
        ("show_hn", "9201", "lookup:url")
    ]
    # declared and undeclared sub-populations, counts per anchor rule
    roles = {r["repo_full_name"]: r["role"] for r in v["cases_by_view"]["launch"]}
    uroles = {r["repo_full_name"]: r["role"] for r in v["cases_by_view"]["launch_undeclared"]}
    assert roles[C] == roles[D] == "not_in_view" and uroles[A] == uroles[B_] == "not_in_view"
    assert roles[E] == uroles[E] == "no_anchor"
    counts = {"show_hn": 1, "launch_hn": 0, "release_launch": 1,
              "undeclared:first_mention": 1, "undeclared:first_release": 1, "none": 1}  # fmt: skip
    sel = v["selection"]
    assert sel["summary"]["view_b_anchor_rules"] == counts
    assert sel["views"]["launch"]["summary"]["anchor_rules"] == counts
    assert res.counts["view_b_anchor_rules"] == counts
    # view A keeps its §2.2 anchor: only alpha (discovery) and bravo (lookup) have a launch
    a_rows = {r["repo_full_name"]: r["detail"] for r in v["cases_by_view"]["follow_through"]}
    assert a_rows[B_]["anchor"]["at"] == t(2025, 12, 1).isoformat()  # its Show HN, not v1.0
    assert a_rows[C]["anchor"] is None and "anchor_rule" not in a_rows[C]
    # stored: tag, time, prerelease and the launch test only; no name, body, comment or author
    cs = {c.ref: c for c in CandidateStore(capture_db.conn, b.brief_id, 1).all()}
    (rs,) = [s for s in cs[f"gh:{B_}"].sources if s["source"] == "gh_releases"]
    assert rs["releases"] == [
        {"tag": "v0.1", "published_at": "2025-02-01T12:00:00+00:00", "prerelease": False,
         "launch": False},
        {"tag": "v1.0", "published_at": "2025-09-01T12:00:00+00:00", "prerelease": False,
         "launch": True},
    ]  # fmt: skip
    (ms,) = [s for s in cs[f"gh:{C}"].sources if s["source"] == "hn_first_mention"]
    assert set(ms) == {"source", "rule", "requests", "status", "hn_item_id", "time", "kind"}
    text = dump(capture_db.conn)
    for marker in ("SYNTH-BODY", "SYNTH-NAME", "SYNTH-COMMENT", "SYNTH-TITLE", "hnuser",
                   "someone-synthetic", "Launching alpha", "Introducing bravo"):  # fmt: skip
        assert marker not in text, marker
    # the raw release and mention pages were dropped after parsing (CB-24)
    ev = capture_db.conn.execute(
        "SELECT url, deletion_state FROM evidence WHERE url LIKE %s OR url LIKE %s",
        ("%/releases%", "%search=first_mention%"),
    ).fetchall()
    assert len(ev) == 8 and {s for _, s in ev} == {"raw_dropped"}


def test_new_data_is_covered_by_purge_export_and_inventory(capture_db, pg_url, tmp_path):
    b = seed(capture_db)
    prereg(capture_db.conn, b, tmp_path)
    _gh, ghc = github(capture_db, tmp_path)
    store = LLMStore(":memory:")
    stage(capture_db.conn, b, hn_connector(capture_db, FakeShowHN(HITS), tmp_path), ghc,
          coder(llm(SurfaceBackend(), store), guard(store)))  # fmt: skip
    conn = capture_db.conn
    # export (project level): the candidate rows carry the surface, releases and mention
    res = export_jsonl(
        pg_url, tmp_path / "export", tables=["brief_candidate", "brief_selection_case"]
    )
    assert res.tables["brief_candidate"]["rows"] == 5
    text = (tmp_path / "export" / "brief_candidate.jsonl").read_text()
    import json

    rows = [json.loads(line) for line in text.splitlines() if line]
    assert all(r["metadata"]["distribution_surface"]["surface"] for r in rows)
    assert "gh_releases" in text
    assert "hn_first_mention" in text
    sc = (tmp_path / "export" / "brief_selection_case.jsonl").read_text()
    assert sc.count("launch_undeclared") >= 5
    inv = {x.table: x for x in inventory(conn).tables}
    assert inv["brief_candidate"].rows == 5 and inv["brief_selection_case"].rows == 15
    # a repo opt-out removes its coding, launch events, view rows and evidence
    conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES ('github:8960003', 'github', 8960003, %s, %s)",
        (C, NOW),
    )
    requests.purge_repo(
        capture_db, LocalSnapshotStore(Path(tmp_path) / "snap"), "github:8960003",
        DeletionLog(capture_db, "objection"),
    )  # fmt: skip
    for sql in (
        "SELECT count(*) FROM brief_candidate WHERE repo_full_name = %s",
        "SELECT count(*) FROM brief_selection_case WHERE repo_full_name = %s",
    ):
        assert conn.execute(sql, (C,)).fetchone()[0] == 0, sql
    left = conn.execute(
        "SELECT count(*) FROM evidence WHERE lower(url) LIKE %s", (f"%{C}%",)
    ).fetchone()[0]
    assert left == 0
    assert (
        conn.execute(
            "SELECT count(*) FROM brief_selection_case WHERE view = 'launch_undeclared'"
        ).fetchone()[0]
        == 4
    )
