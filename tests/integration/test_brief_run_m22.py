"""M22 on Postgres: `pigtail run --brief` stages on the synthetic example brief with fakes only
(GitHub, Show HN, Message Batches; no network, no key): discovery (R4.5, R4.11) -> relevance
batch (R4.6, R15.8-R15.10) -> shortlist (R4.7) -> decisions -> finalize; resumability after a
batch left running and after a crash mid-batch without resubmission (R19.1, R15.9); budget and
approval stops with a resumable checkpoint (R18.5, R15.11); no handles reach the model or the
database (Directive §8.1, ADR-066.1); idempotent re-runs and `--incremental`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pigtail.briefs.candidates import CandidateStore
from pigtail.briefs.discovery import GITHUB_SEARCH_EVIDENCE
from pigtail.briefs.model import Brief, Budget, load_brief_text
from pigtail.briefs.runner import RunDeps, RunOptions, RunOutcome, run_brief
from pigtail.briefs.shortlist import Shortlist, ShortlistError, ShortlistFinal
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import TokenBucket
from pigtail.connectors.github_budget import JobCaps
from pigtail.connectors.hn import HNShowDiscoveryConnector
from pigtail.llm import LLMClient
from pigtail.llm.batch import PgBatchStore, PgCostLedger
from pigtail.llm.redact import alias_redact
from pigtail.llm.store import LLMStore
from pigtail.privacy.suppression import Suppressions
from tests.discovery_fake import RUN_NOW, DiscoveryFakeGitHub, FakeShowHN
from tests.integration.test_github_per_repo_m1t24 import connector, dump_all_tables
from tests.relevance_fake import RelevanceBatchBackend

pytestmark = pytest.mark.db

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"
MODELS = {
    "relevance": "claude-haiku-4-5-20251001",
    "extraction": "claude-sonnet-5",
    "synthesis": "claude-opus-5-5",
}
HAIKU = "claude-haiku-4-5-20251001"


def example(money: float = 150.0) -> Brief:
    b = load_brief_text(EXAMPLE.read_text())
    return b.model_copy(update={"version": 1, "budget": Budget(money_usd=money, llm_backend="api")})


@dataclass
class World:
    db: Any
    conn: psycopg.Connection[Any]
    tmp: Path
    gh: DiscoveryFakeGitHub
    hn: FakeShowHN
    github: Any
    hnconn: HNShowDiscoveryConnector


@pytest.fixture
def w(capture_db: Any, tmp_path: Path) -> World:
    return make_world(capture_db, tmp_path)


def make_world(capture_db: Any, tmp_path: Path) -> World:
    capture_db.conn.autocommit = True
    gh, hn = DiscoveryFakeGitHub(), FakeShowHN()
    github = connector(capture_db, gh, tmp_path)
    hnconn = HNShowDiscoveryConnector(
        store=LocalSnapshotStore(tmp_path / "snap"),
        pseudonymizer=None,
        http=hn.client(),
        env={},
        evidence_sink=capture_db.upsert_evidence,
        sleep=lambda s: None,
        limiter=TokenBucket(1000, burst=1000),
        clock=lambda: RUN_NOW,
    )
    return World(capture_db, capture_db.conn, tmp_path, gh, hn, github, hnconn)


def client(w: World, backend: RelevanceBatchBackend) -> LLMClient:
    return LLMClient(
        backends={"api": backend},
        default_backend="api",
        store=LLMStore(w.tmp / "llm.sqlite3"),  # a file: survives a "new process"
        models=MODELS,
        redactor=alias_redact,
        batch_store=PgBatchStore(w.conn),
        cost_sink=PgCostLedger(w.conn),
    )


def run(
    w: World,
    backend: RelevanceBatchBackend,
    brief: Brief | None = None,
    *,
    sleep: Any = None,
    github: Any = None,
    **opts: Any,
) -> RunOutcome:
    deps = RunDeps(
        conn=w.conn,
        client=client(w, backend),
        github=github or w.github,
        hn=w.hnconn,
        clock=lambda: RUN_NOW,
        sleep=sleep or (lambda s: None),
    )
    opts.setdefault("approve_paid", True)
    opts.setdefault("poll_seconds", 0)
    return run_brief(brief or example(), deps, RunOptions(**opts))


def cands(w: World) -> dict[str, Any]:
    return {c.ref: c for c in CandidateStore(w.conn, "example-config-linter", 1).all()}


def runs(w: World) -> list[dict[str, Any]]:
    cur = w.conn.execute("SELECT * FROM brief_runs ORDER BY created_at")
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# --- end to end ------------------------------------------------------------------------------
def test_m22_e2e_discovery_relevance_shortlist_decisions_finalize(w):
    """R4.5, R4.6, R4.7, R4.11, R19.1, R18.6 on the synthetic example brief."""
    fb = RelevanceBatchBackend()
    out = run(w, fb)
    assert out.exit_code == 0 and out.status == "awaiting_review", out.message

    # R4.5 discovery: in-window, enough stars, from every source; de-duplicated
    c = cands(w)
    repos = {r.removeprefix("gh:") for r in c if r.startswith("gh:")}
    assert repos == {
        "org-s/yaml-guard",
        "ghuser042/tomlcheck",
        "org-s/maybe-config",
        "org-s/json-schema-cli",
        "org-s/web-framework-x",
        "org-z/linkedtool",
        "org-z/confcheck",
        "org-z/schema-checker",
    }
    assert "gh:org-y/old-linter" not in c and "gh:org-y/tiny-lint" not in c  # window, stars
    assert "gh:org-z/ancient" not in c  # linked by the awesome list, but outside the window
    srcs = {r: {s["source"] for s in x.sources} for r, x in c.items()}
    assert {"github_keyword", "github_topic", "awesome_list"} <= srcs["gh:org-s/yaml-guard"]
    assert srcs["gh:org-z/linkedtool"] == {"awesome_list", "github_topic"}
    hn = next(s for s in c["gh:org-z/confcheck"].sources if s["source"] == "show_hn")
    assert hn["points"] == 120 and hn["hn_item_id"] == 7001 and "author" not in hn
    assert all(x.sources and x.first_seen_at for x in c.values())
    # R4.11 / ADR-054.3: the reference case resolved by its launch post; the exemplar's launch
    # posts link several repos, so it stays unresolved with candidate matches (not blocking)
    ref = c["gh:org-z/schema-checker"]
    assert (ref.panel, ref.named_index, ref.resolution_rule) == ("reference", 0, "launch_link")
    ex = c["named:exemplar:0"]
    assert ex.resolution == "unresolved" and ex.repo_full_name is None
    assert {m["full_name"] for m in ex.matches} >= {"org-z/showcase-site", "org-z/showcase-engine"}
    assert all(
        m["stars"] is not None and m["url"].startswith("https://github.com/") for m in ex.matches
    )

    # R4.6 relevance: every repo candidate judged, with reason, distance and provenance
    for ref_, x in c.items():
        if x.repo_full_name is None:
            continue
        assert x.verdict in ("relevant", "not_relevant", "uncertain"), ref_
        assert x.reason and len(x.reason.replace(" …", "").split()) <= 30
        assert x.distance in (0, 1, 2) and x.rubric_version.startswith("rubric-v1-")
        p = x.relevance
        assert p["model"] == HAIKU and p["stage"] == "relevance" and p["batch_id"]
        assert (p["prompt_id"], p["prompt_version"]) == ("relevance_filter", "1")
        assert p["redaction_version"] == "alias-v1" and p["rubric_version"] == x.rubric_version
    assert c["gh:org-s/web-framework-x"].verdict == "not_relevant"
    assert c["gh:org-s/maybe-config"].verdict == "uncertain"
    assert c["gh:org-s/yaml-guard"].relevance["reason_cut"] is True  # 45 words -> 30
    assert c["gh:org-z/schema-checker"].panel == "reference"  # the brief's panel wins
    # R15.9: one batch, <= 20 candidates per request, cached prefix, Haiku
    assert len(fb.submitted) == 1
    for _cid, params in fb.submitted[0]:
        assert params["model"] == HAIKU and params["system"][-1]["cache_control"]
        assert "RUBRIC rubric-v1-" in params["system"][-1]["text"]
        payload = params["messages"][0]["content"]
        assert len(json.loads(payload[payload.index("[") : payload.rindex("]") + 1])) <= 20
    # the rubric carries field boundaries, target users and problem; not the project's name
    rubric = fb.submitted[0][0][1]["system"][-1]["text"]
    assert "configuration file linters" in rubric and "platform developers" in rubric
    assert "Example Config Linter" not in rubric and "Example Schema Checker" not in rubric

    # R18.6 provenance and spend on the run; README evidence linked for retention (R19.9)
    (r,) = runs(w)
    assert r["status"] == "awaiting_review" and r["brief_hash"] == example().content_hash()
    assert r["data_version"].startswith("dv1-")
    assert r["code_commit"] is None or re.fullmatch(r"[0-9a-f]{7,40}", r["code_commit"])
    assert r["estimate"] is None and r["approved_paid"] is True  # (the CLI stores its estimate)
    assert r["prompt_versions"]["relevance_filter"] == "1"
    assert r["prompt_versions"]["rubric"] == ref.rubric_version
    assert r["model_versions"] == {"relevance": HAIKU}
    assert {k: v["status"] for k, v in r["stages"].items()} == {
        "discovery": "done",
        "relevance": "done",
        "shortlist": "done",
    }
    assert r["spend"]["api_usd_this_run"] > 0
    linked = w.conn.execute(
        "SELECT count(*) FROM brief_evidence WHERE brief_run_id = %s", (r["id"],)
    ).fetchone()[0]
    assert linked >= 5

    # R4.7 review: in scope while in review; decisions need reasons; finalize gated
    sl = Shortlist(w.conn, example())
    v = sl.view()
    assert v["status"] == "in_review" and v["counts"]["undecided"] == 6
    assert v["precision"]["value"] is None and v["precision"]["label"] == "not reviewed"
    assert [x["candidate_ref"] for x in v["reference_cases_to_confirm"]] == ["named:exemplar:0"]
    assert v["reference_cases_to_confirm"][0]["named_as"] == "Example Launch Showcase"
    scope = dict(w.conn.execute("SELECT repo_full_name, status FROM brief_shortlist_entry"))
    assert scope["org-s/yaml-guard"] == "in_review" and "org-s/web-framework-x" not in scope
    with pytest.raises(ShortlistError, match="reason"):
        sl.decide(["org-s/yaml-guard"], "accept", "  ")
    with pytest.raises(ShortlistError, match="undecided"):
        sl.finalize()
    sl.decide(
        ["https://github.com/org-s/yaml-guard", "org-s/json-schema-cli"],
        "accept",
        "core config linter",
        reviewer="owner",
    )
    sl.decide(["gh:org-z/confcheck"], "reject", "a checker, not a linter", reviewer="owner")
    assert sl.decide_where("accept", "bulk: relevant", verdict="relevant", reviewer="owner") == 3
    assert sl.decide_where("reject", "bulk: uncertain", verdict="uncertain", reviewer="owner") == 1
    sl.add("https://github.com/org-z/extra-tool", "missed by search", reviewer="owner")
    sl.add(
        "https://github.com/org-z/showcase-engine",
        "the engine was promoted (ADR-054.3)",
        resolves="named:exemplar:0",
        reviewer="owner",
    )
    p = sl.precision()
    # named projects are left out: 4 accepted of 5 decided model-relevant field candidates
    assert (p["kept"], p["decided"], p["model_relevant"]) == (4, 5, 5)
    assert p["value"] == pytest.approx(0.8) and p["meets_target"] is True
    # 2 of the 5 came from the bulk action on the filter's own verdict (BACKLOG M22-P)
    assert p["label"] == "owner-checked; 2 of 5 by bulk action, not item-reviewed"
    assert (p["bulk_on_filter_verdict"], p["item_reviewed"]) == (2, 3)
    res = sl.finalize(reviewer="owner")
    assert res["status"] == "final" and res["unresolved_named"] == []
    final = {
        n
        for (n,) in w.conn.execute(
            "SELECT repo_full_name FROM brief_shortlist_entry WHERE status = 'final'"
        )
    }
    assert final == {
        "org-s/yaml-guard",
        "org-s/json-schema-cli",
        "ghuser042/tomlcheck",
        "org-z/linkedtool",
        "org-z/schema-checker",
        "org-z/extra-tool",
        "org-z/showcase-engine",
    }
    removed = {
        n
        for (n,) in w.conn.execute(
            "SELECT repo_full_name FROM brief_shortlist_entry WHERE status = 'removed'"
        )
    }
    assert "org-z/confcheck" in removed
    rows = w.conn.execute(
        "SELECT decision, reason, reviewer_role, via, decided_at, brief_version, brief_run_id"
        " FROM shortlist_decision"
    ).fetchall()
    assert len(rows) == 9 and all(
        x[1] and x[2] == "owner" and x[3] == "cli" and x[4] and x[5] == 1 and x[6] == r["id"]
        for x in rows
    )
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        w.conn.execute("UPDATE shortlist_decision SET reason = 'x'")
    with pytest.raises(ShortlistFinal):
        sl.decide(["org-s/yaml-guard"], "reject", "too late")
    assert cands(w)["named:exemplar:0"].resolution == "confirmed"
    (r,) = runs(w)
    assert r["status"] == "succeeded"
    st = sl.status()
    assert st["finalized_role"] == "owner" and st["precision"]["value"] == pytest.approx(0.8)


# --- privacy ---------------------------------------------------------------------------------
def test_m22_no_handles_reach_the_model_or_the_database(w):
    """R4.6 inputs are public repo metadata, owner login removed and alias-v1 redacted; stored
    candidates keep repo names only (Directive §8.1, ADR-066.1, CB-24)."""
    fb = RelevanceBatchBackend()
    assert run(w, fb).exit_code == 0
    prompts = "\n".join(fb.prompts())
    for handle in ("ghuser042", "ghuser001", "ghuser009", "hnuser70"):
        assert handle not in prompts, handle
    assert "@" + "example.com" not in prompts and "[owner]" in prompts
    assert '"owner_type": "User"' in prompts
    dump = dump_all_tables(w.db)
    for handle in ("ghuser001", "ghuser009", "hnuser70", "ghuser042@", "author_hnuser"):
        assert handle not in dump, handle
    # the only trace of a user-owned repo's owner is the repo's own name
    assert all(
        m.group(0) == "ghuser042/tomlcheck" for m in re.finditer(r"ghuser042[^\s\"]{0,10}", dump)
    )
    # Show HN pages were dropped right after parsing (author in the raw response)
    ev = w.conn.execute("SELECT deletion_state FROM evidence WHERE source = 'hn_showhn'").fetchall()
    assert ev and {e[0] for e in ev} == {"raw_dropped"}


def test_m22_refused_repo_never_becomes_a_candidate(w, tmp_path):
    """CB-13: a repo on the refusal list is skipped by discovery."""
    sup = Suppressions(repos=frozenset({"github:9100000"}))  # org-s/yaml-guard
    gh = connector(w.db, w.gh, tmp_path, suppression=sup)
    assert run(w, RelevanceBatchBackend(), github=gh).exit_code == 0
    assert "gh:org-s/yaml-guard" not in cands(w)
    assert runs(w)[0]["stages"]["discovery"]["result"]["suppressed"] >= 1


NAMED_TRACES = (
    "example schema checker",
    "example+schema+checker",
    "example%20schema%20checker",
    "example launch showcase",
    "example+launch+showcase",
    "example%20launch%20showcase",
    "example.com/schema-checker",
    "example.org/launch-showcase",
    "synthetic placeholder",
)


@pytest.mark.parametrize("launch_posts", [True, False])
def test_m22_named_projects_names_and_urls_never_reach_the_database(w, launch_posts):
    """ADR-076.6 (M22 verifier): the named projects' names, URLs and notes are not stored; the
    name searches (Show HN, then GitHub `in:name`) record `named:<panel>:<i>` as their query."""
    if not launch_posts:
        w.hn.hits = []  # no launch post: resolution falls back to the GitHub name search
    assert run(w, RelevanceBatchBackend()).exit_code == 0
    dump = dump_all_tables(w.db).lower()
    for t in NAMED_TRACES:
        assert t not in dump, t
    urls = {r[0] for r in w.conn.execute("SELECT url FROM evidence").fetchall()}
    assert any("[named:reference:0]" in u for u in urls)
    if not launch_posts:
        assert any(u.startswith(GITHUB_SEARCH_EVIDENCE) and "[named:" in u for u in urls)


def test_m22_refused_repo_cannot_be_added_back_or_shown_as_a_match(w):
    """CB-13 (M22 verifier): a repo refused after discovery is off the shortlist, can't be added
    by the reviewer, and is not offered as a match for an unresolved named project."""
    assert run(w, RelevanceBatchBackend()).exit_code == 0
    sup = Suppressions(repos=frozenset({"github:9100000"}))  # org-s/yaml-guard
    sl = Shortlist(w.conn, example(), suppressions=sup)
    on, proposed = sl.members()
    assert "org-s/yaml-guard" not in on + proposed
    with pytest.raises(ShortlistError, match="refusal list"):
        sl.add("https://github.com/org-s/yaml-guard", "the filter missed it")
    by_name = Suppressions(
        repo_names=frozenset({"rk_x"}),
        name_key=lambda n, h: "rk_x" if "showcase-site" in n else "no",
    )
    view = Shortlist(w.conn, example(), suppressions=by_name).view()
    confirm = view["reference_cases_to_confirm"]
    (ex,) = [r for r in confirm if r["candidate_ref"] == "named:exemplar:0"]
    shown = {m["full_name"] for m in ex["matches"]}
    assert "org-z/showcase-site" not in shown and "org-z/showcase-engine" in shown
    rows = Shortlist(w.conn, example(), suppressions=sup).view()["candidates"]
    assert rows and all(r["repo_full_name"] != "org-s/yaml-guard" for r in rows)
    assert all(c.repo_full_name != "org-s/yaml-guard" for c in sl.undecided())


def test_m22_view_lists_brief_fields_still_at_their_default(w):
    """R4.7 / D7 (M22 verifier): the review lists defaulted brief fields, apart from warnings."""
    assert run(w, RelevanceBatchBackend()).exit_code == 0
    view = Shortlist(w.conn, example(), suppressions=Suppressions()).view()
    fields = {d["field"]: d["value"] for d in view["defaulted_fields"]}
    assert fields["panel.winners"] == 20 and fields["window.months"] == 18
    assert "project.name" not in fields and "budget.money_usd" not in fields


# --- resumability (R19.1, R15.9) ---------------------------------------------------------------
def test_m22_batch_still_running_resumes_and_collects_without_resubmitting(w):
    fb = RelevanceBatchBackend(polls_until_end=3)
    first = run(w, fb, wait_seconds=0)
    assert first.exit_code == 5 and first.status == "waiting_batch"
    assert first.stop["batch_ids"] == ["msgbatch_fake1"] and len(fb.submitted) == 1
    assert runs(w)[0]["stages"]["relevance"]["status"] == "waiting_batch"
    # a new process: new client, same database and LLM cache file
    second = run(w, fb)
    assert second.exit_code == 0 and second.status == "awaiting_review" and second.resumed
    assert second.brief_run_id == first.brief_run_id and len(fb.submitted) == 1
    (r,) = runs(w)
    assert r["resumes"] == 1 and r["stages"]["discovery"]["status"] == "done"
    assert all(x.verdict for x in cands(w).values() if x.repo_full_name)


class Crash(BaseException):
    """The process dies (not an Exception: nothing in the run catches it)."""


def test_m22_crash_mid_batch_then_resume_collects_the_same_batch(w):
    fb = RelevanceBatchBackend(polls_until_end=2)

    def die(_s: float) -> None:
        raise Crash

    with pytest.raises(Crash):
        run(w, fb, sleep=die)
    (r,) = runs(w)
    assert r["status"] == "running" and len(fb.submitted) == 1  # died while polling
    assert r["checkpoint"]["relevance"]["plan"]["chunks"]
    out = run(w, fb)
    assert out.exit_code == 0 and out.brief_run_id == r["id"] and len(fb.submitted) == 1
    assert fb.standard_calls == []  # collected from the batch, no fallback call either


def test_m22_missing_verdicts_get_one_retry_pass(w):
    fb = RelevanceBatchBackend(drop_ids={"c02"})
    assert run(w, fb).exit_code == 0
    assert len(fb.submitted) == 2 and len(fb.submitted[1]) == 1
    assert all(x.verdict for x in cands(w).values() if x.repo_full_name)


# --- money (R18.5, R15.11) ---------------------------------------------------------------------
def test_m22_budget_below_estimate_stops_with_resumable_checkpoint(w):
    fb = RelevanceBatchBackend()
    out = run(w, fb, brief=example(money=0.0001))
    assert out.exit_code == 4 and out.status == "paused_budget"
    assert out.stop["kind"] == "money" and "H6" in out.stop["detail"]
    assert fb.submitted == []  # nothing was sent: the check runs before the batch
    (r,) = runs(w)
    assert r["status"] == "paused_budget" and r["stages"]["discovery"]["status"] == "done"
    assert r["stages"]["relevance"]["status"] == "paused_budget" and r["checkpoint"]["relevance"]
    # the cap is raised (a new decision by the owner): the same run resumes where it stopped
    again = run(w, fb, brief=example(money=150))
    assert again.exit_code == 0 and again.brief_run_id == r["id"] and len(fb.submitted) == 1
    assert runs(w)[0]["stages"]["discovery"]["result"]["seen"] > 0  # not re-run


def test_m22_paid_steps_need_approval(w):
    fb = RelevanceBatchBackend()
    out = run(w, fb, approve_paid=False)
    assert out.exit_code == 3 and out.stop["kind"] == "approval" and fb.submitted == []


def test_m22_github_request_budget_pauses_discovery_and_resume_skips_done_queries(w, tmp_path):
    small = connector(w.db, w.gh, tmp_path, job=JobCaps({"search": 3}))
    out = run(w, RelevanceBatchBackend(), github=small)
    assert out.exit_code == 4 and out.stop["kind"] == "github_requests"
    done = list(runs(w)[0]["checkpoint"]["discovery"]["done"])
    assert done
    n = len(w.gh.search_queries)
    assert run(w, RelevanceBatchBackend()).exit_code == 0
    redone = [q for q, _, _ in w.gh.search_queries[n:]]
    assert not any("created:2025" in q and q.startswith("config linter") for q in redone)


# --- idempotence ----------------------------------------------------------------------------
def test_m22_rerun_is_a_no_op_and_incremental_reuses_everything(w):
    fb = RelevanceBatchBackend()
    first = run(w, fb)
    again = run(w, fb)
    assert again.exit_code == 0 and "nothing to do" in again.message
    assert len(runs(w)) == 1 and len(fb.submitted) == 1
    inc = run(w, fb, incremental=True)
    assert inc.exit_code == 0 and inc.brief_run_id != first.brief_run_id
    rows = runs(w)
    assert rows[1]["resumed_from"] == first.brief_run_id and rows[1]["incremental"] is True
    assert len(fb.submitted) == 1  # every candidate already judged under this rubric
    assert rows[1]["stages"]["relevance"]["result"]["already_judged"] == 8
