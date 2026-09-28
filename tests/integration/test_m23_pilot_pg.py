"""M23 pilot end to end on Postgres, a local snapshot store and fakes (no network, synthetic).

Covers: case evidence with gaps and snapshot-or-drop; coder A and B batches, validation,
adjudication only on disagreements; alpha stored per run; estimate -> approve -> run with a
budget stop and a resume that never resubmits a batch; the cost report and the H6 projection
stop; private report paths; evidence-decay scheduling and aggregation; the GitHub budget stop and
resume; no handles in outputs; purge and export.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from pigtail.briefs.budget import BudgetStop
from pigtail.capture.snapshots import LocalSnapshotStore
from pigtail.connectors.base import TokenBucket
from pigtail.connectors.github import GitHubConnector, MemoryCache
from pigtail.connectors.github_budget import Budget, JobCaps
from pigtail.connectors.project_page import ProjectPageConnector
from pigtail.forensics.decay import aggregate, run_due
from pigtail.forensics.pilot import EXIT_CODING_FAILED, PilotDeps, PilotOptions, run_pilot
from pigtail.llm.batch import MemoryBatchStore, PgCostLedger
from pigtail.llm.client import LLMClient
from pigtail.llm.redact import alias_redact
from pigtail.llm.store import LLMStore, UsageRow
from tests import selection_fake
from tests.forensics_fake import (
    BLIND_NUMBERS,
    EMAIL,
    GRAMMAR_ERROR,
    HANDLE,
    NOW,
    CodingBatchBackend,
    FakeGitHubCases,
    FakeSite,
    is_coder,
    seed_selection,
)

pytestmark = pytest.mark.db
REPO_ROOT = Path(__file__).resolve().parents[2]
LLM = "LLM-coded, not human-validated"
PILOT_TABLES = (
    "brief_pilot",
    "brief_pilot_case",
    "brief_case_evidence",
    "brief_case_gap",
    "brief_coding",
    "brief_reliability",
    "brief_case_cost_model",
    "brief_evidence_decay",
)


def _fast() -> TokenBucket:
    return TokenBucket(1000, 5, 0.0, sleep=lambda _s: None)


@dataclass
class Env:
    conn: Any
    db: Any
    brief: Any
    snaps: LocalSnapshotStore
    gh_fake: FakeGitHubCases
    site: FakeSite
    backend: CodingBatchBackend
    batches: MemoryBatchStore
    llm: LLMStore
    data_dir: Path

    def github(self, caps: dict[str, int] | None = None) -> GitHubConnector:
        return GitHubConnector(
            store=self.snaps, http=self.gh_fake.client(), token="fake-token-for-tests",
            evidence_sink=self.db.upsert_evidence, cache=MemoryCache(),
            budget=Budget(job=JobCaps(caps or {}), clock=lambda: NOW, sleep=lambda _s: None),
            limiters={r: _fast() for r in ("core", "graphql", "search")},
            limiter=_fast(), sleep=lambda _s: None, clock=lambda: NOW,
        )  # fmt: skip

    def pages(self) -> ProjectPageConnector:
        return ProjectPageConnector(
            store=self.snaps, pseudonymizer=None, http=self.site.client(),
            evidence_sink=self.db.upsert_evidence, limiter=_fast(), sleep=lambda _s: None,
            clock=lambda: NOW, env={},
        )  # fmt: skip

    def client(self) -> LLMClient:
        return LLMClient(
            backends={"api": self.backend}, default_backend="api", store=self.llm,
            models={"relevance": "claude-haiku-4-5-20251001", "extraction": "claude-sonnet-5",
                    "synthesis": "claude-opus-5-5"},
            redactor=alias_redact, batch_store=self.batches, cost_sink=PgCostLedger(self.conn),
        )  # fmt: skip

    def deps(self, *, month_cap: float = 200.0, github: Any = "default") -> PilotDeps:
        return PilotDeps(
            conn=self.conn, client=self.client(), snapshots=self.snaps, data_dir=self.data_dir,
            github=self.github() if github == "default" else github, pages=self.pages(),
            month_cap_usd=month_cap, clock=lambda: NOW, sleep=lambda _s: None,
        )  # fmt: skip


@pytest.fixture
def env(capture_db: Any, tmp_path: Path) -> Env:
    brief = selection_fake.brief()
    seed_selection(capture_db.conn, brief)
    return Env(
        conn=capture_db.conn, db=capture_db, brief=brief,
        snaps=LocalSnapshotStore(tmp_path / "snaps"), gh_fake=FakeGitHubCases(),
        site=FakeSite(), backend=CodingBatchBackend(), batches=MemoryBatchStore(),
        llm=LLMStore(tmp_path / "llm.sqlite3"), data_dir=tmp_path / "private-data",
    )  # fmt: skip


OPTS = PilotOptions(cases=5, approve_paid=True, bootstrap_resamples=200)


def q(env: Env, sql: str, *args: Any) -> list[Any]:
    return env.conn.execute(sql, args).fetchall()


# --- end to end ------------------------------------------------------------------------------
def test_m23_pilot_end_to_end(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0, out.message
    rid = out.brief_run_id
    # a pilot row of kind `pilot`, never picked up by `pigtail run`
    assert q(env, "SELECT kind, status FROM brief_runs WHERE id = %s", rid) == [
        ("pilot", "succeeded")
    ]
    from pigtail.briefs.runner import find_run

    assert find_run(env.conn, env.brief)[0] == "new"
    # the case rule: A1 winner and nearest loser, B1 winner and loser, exemplar
    cases = q(env, "SELECT position, view, candidate_ref, role FROM brief_pilot_case"
                   " WHERE brief_run_id = %s ORDER BY position", rid)  # fmt: skip
    assert cases == [
        (1, "follow_through", "gh:org-p/alpha-cli", "winner"),
        (2, "follow_through", "gh:org-p/beta-tool", "matched_loser"),
        (3, "launch", "gh:org-p/theta-b2", "winner"),
        (4, "launch", "gh:org-p/iota-b2l", "matched_loser"),
        (5, "follow_through", "gh:org-p/zeta-ex", "exemplar"),
    ]
    # evidence: every item has an evidence record with a content hash (snapshot or drop)
    items = q(env, "SELECT e.case_key, e.kind, ev.content_hash, ev.deletion_state"
                   " FROM brief_case_evidence e JOIN evidence ev ON ev.id = e.evidence_id"
                   " WHERE e.brief_run_id = %s", rid)  # fmt: skip
    kinds = {(k.split(":", 1)[1], kind) for k, kind, _h, _s in items}
    assert ("gh:org-p/alpha-cli", "homepage") in kinds
    for full in ("alpha-cli", "beta-tool", "theta-b2", "iota-b2l", "zeta-ex"):
        for kind in ("repo_metadata", "readme_current", "readme_at_anchor", "releases",
                     "launch_events"):  # fmt: skip
            assert (f"gh:org-p/{full}", kind) in kinds
    assert all(len(h) == 64 and st == "present" for _k, _kind, h, st in items)
    for _k, _kind, h, _st in items:
        assert env.snaps.exists(h)
    # person-level raw pages (commits, releases) were dropped right after parsing
    dropped = q(
        env,
        "SELECT count(*) FROM evidence WHERE deletion_state = 'raw_dropped'"
        " AND (url LIKE '%%/commits%%' OR url LIKE '%%/releases?%%')",
    )[0][0]
    assert dropped >= 10
    # gaps: the person-level sources and gap sources for every case; the missing homepage
    gaps = q(env, "SELECT case_key, source, reason FROM brief_case_gap WHERE brief_run_id = %s",
             rid)  # fmt: skip
    by = {(c.split(":", 1)[1], s): r for c, s, r in gaps}
    for full in ("alpha-cli", "beta-tool", "theta-b2", "iota-b2l", "zeta-ex"):
        ref = f"gh:org-p/{full}"
        assert by[(ref, "hn_comments_and_mentions")] == "held_person_level_adr_073_2"
        assert by[(ref, "bluesky_mention_text")] == "held_person_level_adr_073_2"
        assert by[(ref, "repo_event_actors")] == "held_person_level_adr_073_2"
        assert by[(ref, "reddit")].startswith("source_gap")
        assert by[(ref, "x")].startswith("source_gap")
    assert by[("gh:org-p/beta-tool", "homepage")] == "no_homepage"
    # three batches: coder A, coder B, adjudication
    assert len(env.backend.submitted) == 3
    # passes A and B for every case, provenance on every row
    rows = q(
        env,
        "SELECT pass, count(*), count(prompt_fingerprint), count(batch_id),"
        " count(model) FROM brief_coding WHERE brief_run_id = %s GROUP BY pass",
        rid,
    )
    got = {r[0]: r[1:] for r in rows}
    assert got["A"][0] == got["B"][0] == got["A"][1] == got["A"][2] == got["A"][3] > 0
    # adjudication only on disagreements
    dis = q(env, "SELECT count(*) FROM brief_coding a JOIN brief_coding b ON b.brief_run_id ="
                 " a.brief_run_id AND b.case_key = a.case_key AND b.unit = a.unit AND b.pass ="
                 " 'B' WHERE a.pass = 'A' AND a.brief_run_id = %s AND a.excluded IS NULL AND"
                 " b.excluded IS NULL AND a.value <> b.value", rid)[0][0]  # fmt: skip
    assert dis > 0
    assert got["adjudicator"][0] == dis
    adj_units = {
        r[0]
        for r in q(
            env,
            "SELECT DISTINCT field FROM brief_coding"
            " WHERE brief_run_id = %s AND pass = 'adjudicator'",
            rid,
        )
    }
    assert "category_primary" in adj_units and "first_party" not in adj_units
    # the person alias in pass B's MC-01 excerpt was dropped (citation_failed)
    assert q(env, "SELECT value, unknown_reason, status FROM brief_coding WHERE brief_run_id ="
                  " %s AND pass = 'B' AND unit = 'pattern.MC-01' LIMIT 1", rid)[0] == (
        "unknown", "citation_failed", "citation_failed")  # fmt: skip
    # MC-12 derived from the selection's star anomaly flag, excluded from alpha
    assert q(env, "SELECT value, status FROM brief_coding WHERE brief_run_id = %s AND pass ="
                  " 'final' AND unit = 'pattern.MC-12' AND candidate_ref ="
                  " 'gh:org-p/beta-tool'", rid)[0] == ("present", "derived")  # fmt: skip
    # alpha per field and run, labelled
    rel = q(env, "SELECT field, statistic, n_cases, labels, n_pairable, assessed"
                 " FROM brief_reliability WHERE brief_run_id = %s", rid)  # fmt: skip
    fields = {r[0] for r in rel}
    assert {"category_primary", "reliability", "pattern.*", "module_active.cli_devtools"} <= fields
    assert ("reliability", "ordinal") in {(r[0], r[1]) for r in rel}
    assert all(r[2] == 5 and "pilot, n = 5" in r[3] and LLM in r[3] for r in rel)
    # below 30 pairable units a statistic is never assessed (codebook §10.4)
    assert all("reliability not assessed" in r[3] and not r[5] for r in rel if r[4] < 30)
    assert q(env, "SELECT n_pairable FROM brief_reliability WHERE brief_run_id = %s"
                  " AND field = 'category_primary'", rid)[0][0] == 5  # fmt: skip
    # cost per case and stage from the ledger
    ledger = q(env, "SELECT prompt_id, count(*), count(DISTINCT case_ref) FROM llm_cost_ledger"
                    " WHERE brief_run_id = %s GROUP BY prompt_id ORDER BY 1", rid)  # fmt: skip
    assert {r[0]: r[1] for r in ledger} == {
        "case-adjudicator": 5,
        "case-coder-a": 5,
        "case-coder-b": 5,
    }
    model = q(env, "SELECT n_cases, per_case, projection, h6 FROM brief_case_cost_model")[0]
    assert model[0] == 5 and model[1]["source"] == "measured" and model[3] is False
    assert model[2]["full_brief_cases"] == 11 and model[2]["cases_remaining"] == 6
    # private report: outside git, 0600
    js = Path(out.report_paths["json"])
    assert js.is_relative_to(env.data_dir.resolve()) and not js.is_relative_to(REPO_ROOT)
    assert js.name == f"pilot-{NOW.date().isoformat()}.json"
    assert stat.S_IMODE(os.stat(js).st_mode) == 0o600
    report = json.loads(js.read_text())
    assert len(report["provenance"]["batch_ids"]) == 3
    assert report["provenance"]["frame_version"] == "pilot-frame-v1"
    assert set(report["cost"]["per_case"]) == {
        c for (c,) in q(env, "SELECT coding_id FROM brief_pilot_case WHERE brief_run_id = %s", rid)
    }
    for c in report["cost"]["per_case"].values():
        assert {"coder_a", "coder_b"} <= set(c["stages"]) and c["github_requests"]
    # decay scheduled at +1, +7, +30 for every item with a URL
    n_url = q(env, "SELECT count(*) FROM brief_case_evidence WHERE brief_run_id = %s"
                   " AND decay_url IS NOT NULL", rid)[0][0]  # fmt: skip
    assert out.summary["decay_scheduled"] == 3 * n_url > 0
    # running it again pays for nothing: the finished pilot is reported, not redone
    again = run_pilot(env.brief, env.deps(), OPTS)
    assert again.exit_code == 0 and again.status == "complete" and again.brief_run_id == rid
    assert len(env.backend.submitted) == 3 and q(env, "SELECT count(*) FROM brief_pilot")[0][0] == 1


def test_m23_no_handles_or_post_text_in_outputs(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0
    dump = []
    for t in PILOT_TABLES:
        cur = env.conn.execute(f"SELECT * FROM {t}")
        dump += [json.dumps(r, default=str) for r in cur.fetchall()]
    coded = [
        json.dumps(r, default=str)
        for r in q(env, "SELECT value, excerpts, reason, detail FROM brief_coding")
    ]
    reports = [Path(p).read_text() for p in out.report_paths.values()]
    cache = [json.dumps(r) for r in env.llm._db.execute("SELECT * FROM llm_cache").fetchall()]
    derived = [
        env.snaps.get(h).decode()
        for (h,) in q(env, "SELECT content_hash FROM evidence WHERE source = 'pigtail_derived'")
    ]
    for text in [*dump, *coded, *reports, *cache, *derived]:
        assert HANDLE not in text and EMAIL not in text
        assert "SECRET POST TEXT" not in text  # Bluesky post text never copied
    for text in coded:
        assert "@user" not in text and "[profile" not in text
    # pass A quoted the redacted maintainer line: dropped, never stored
    assert {r[0] for r in q(env, "SELECT status FROM brief_coding WHERE pass = 'A'"
                                 " AND unit = 'module_active.corporate_backed'")} == {
        "citation_failed"}  # fmt: skip
    # the model only ever saw redacted input
    for p in env.backend.prompts():
        assert HANDLE not in p and EMAIL not in p
    # coders never see role, pair, view or outcome words
    for p in env.backend.prompts():
        head = p.split("### evidence_id", 1)[0]
        for word in ("winner", "loser", "follow_through", "exemplar", "pair"):
            assert word not in head.lower()
        assert "matched_loser" not in p and "winner" not in p


def test_m23_approve_budget_stop_and_resume_without_resubmitting(env: Env) -> None:
    # 1. paid steps need approval: nothing starts
    out = run_pilot(env.brief, env.deps(), PilotOptions(cases=5))
    assert out.exit_code == 3 and out.estimate is not None
    assert out.estimate["requires_approval"] and out.estimate["total_usd"] > 0
    assert q(env, "SELECT count(*) FROM brief_pilot")[0][0] == 0
    # 2. an estimate above the brief's cap is refused before anything (H6)
    tiny = env.brief.model_copy(
        update={"budget": env.brief.budget.model_copy(update={"money_usd": 0.01})}
    )
    out = run_pilot(tiny, env.deps(), OPTS)
    assert out.exit_code == 4 and out.stop and out.stop["kind"] == "money"
    assert "H6" in out.message
    assert q(env, "SELECT count(*) FROM brief_pilot")[0][0] == 0
    # 3. batches still running: waiting, resumable
    env.backend.polls_until_end = 10
    wait = PilotOptions(cases=5, approve_paid=True, wait_seconds=0.0, bootstrap_resamples=100)
    out = run_pilot(env.brief, env.deps(), wait)
    assert out.exit_code == 5 and out.status == "waiting_batch"
    assert len(env.backend.submitted) == 2  # coder A and B in flight together
    rid = out.brief_run_id
    # 4. this month's spend grew meanwhile: the adjudication batch hits the monthly cap
    env.backend.polls_until_end = 0
    env.llm.record(UsageRow("api", "other", "claude-sonnet-5", "p", "1", "ok", 1, 1,
                            cost_usd=199.99))  # fmt: skip
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 4 and out.status == "paused_budget" and out.brief_run_id == rid
    assert out.stop and out.stop["kind"] == "month"
    assert len(env.backend.submitted) == 2  # collected, not resubmitted
    # 5. with room again, it resumes: only the adjudication batch is new
    out = run_pilot(env.brief, env.deps(month_cap=1000.0), OPTS)
    assert out.exit_code == 0, out.message
    assert out.brief_run_id == rid and len(env.backend.submitted) == 3
    assert q(env, "SELECT count(*) FROM llm_cost_ledger WHERE brief_run_id = %s"
                  " AND prompt_id = 'case-coder-a'", rid)[0][0] == 5  # fmt: skip
    assert q(env, "SELECT resumes FROM brief_runs WHERE id = %s", rid)[0][0] == 2


def test_m23_projection_over_the_cap_stops_with_h6(env: Env) -> None:
    capped = env.brief.model_copy(
        update={"budget": env.brief.budget.model_copy(update={"money_usd": 1.0})}
    )
    out = run_pilot(capped, env.deps(), OPTS)
    assert out.exit_code == 4 and out.status == "succeeded_h6"
    assert "H6" in out.message and out.report_paths
    proj = out.summary["projection"]
    assert proj["h6"] and proj["projected_total_usd"] > proj["cap_usd"] == 1.0
    assert q(env, "SELECT h6 FROM brief_case_cost_model")[0][0] is True
    report = json.loads(Path(out.report_paths["json"]).read_text())
    assert report["projection"]["h6"] is True
    assert "H6" in Path(out.report_paths["md"]).read_text()


def test_m23_github_budget_stop_pauses_and_resumes(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(github=env.github({"core": 7})), OPTS)
    assert out.exit_code == 4 and out.stop and out.stop["kind"] == "github_budget"
    rid = out.brief_run_id
    done = q(env, "SELECT count(*) FROM brief_pilot_case WHERE brief_run_id = %s"
                  " AND evidence_status = 'done'", rid)[0][0]  # fmt: skip
    assert 1 <= done < 5
    before = sum(1 for r in env.gh_fake.requests if "alpha-cli/readme" in r.url.path)
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0 and out.brief_run_id == rid
    after = sum(1 for r in env.gh_fake.requests if "alpha-cli/readme" in r.url.path)
    assert after == before  # the finished case was not fetched again


def test_m23_decay_schedule_and_aggregation(env: Env) -> None:
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0
    env.gh_fake.gone.add("org-p/beta-tool")
    env.gh_fake.changed.add("org-p/alpha-cli")
    gh, pages = env.github(), env.pages()
    # nothing is due before +1 day
    assert run_due(env.conn, now=NOW + timedelta(hours=12), github=gh, pages=pages).checked == 0
    r1 = run_due(env.conn, now=NOW + timedelta(days=2), github=gh, pages=pages)
    agg = aggregate(env.conn, env.brief.brief_id)
    assert r1.checked == agg["by_age"]["+1d"]["scheduled"] > 0
    assert agg["by_age"]["+1d"]["complete"] and agg["by_age"]["+7d"]["checked"] == 0
    assert agg["by_age"]["+1d"]["gone"] == 2  # beta-tool: README now and at its commit
    row = {(r["kind"], r["offset_days"]): r for r in agg["by_kind_and_age"]}
    assert row[("readme_current", 1)]["changed"] == 1  # alpha-cli's README changed
    assert row[("readme_at_anchor", 1)]["changed"] == 0  # pinned to a commit
    # GitHub checks stay due without a token
    r7 = run_due(env.conn, now=NOW + timedelta(days=8), github=None, pages=pages)
    assert r7.skipped_no_token > 0 and r7.checked <= 1
    r7 = run_due(env.conn, now=NOW + timedelta(days=8), github=gh, pages=pages)
    agg = aggregate(env.conn, env.brief.brief_id)
    assert agg["by_age"]["+7d"]["complete"] and not agg["by_age"]["+30d"]["complete"]
    lost = agg["r19_8"]["lost_at_7d"]
    assert lost is not None and agg["r19_8"]["cadence_adr_needed"] == (lost > 0.10)
    # conditional requests where a validator is known: nothing stored by a check
    n_ev = q(env, "SELECT count(*) FROM evidence")[0][0]
    run_due(env.conn, now=NOW + timedelta(days=40), github=gh, pages=pages)
    assert q(env, "SELECT count(*) FROM evidence")[0][0] == n_ev


def test_m23_optout_purges_codings_and_evidence_links(env: Env, pz: Any) -> None:
    from pigtail.privacy import requests

    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0
    key = "github:8100001"  # org-p/alpha-cli
    env.conn.execute(
        "INSERT INTO repos (id, host, host_id, full_name, first_seen_at)"
        " VALUES (%s, 'github', 8100001, 'org-p/alpha-cli', now())",
        (key,),
    )
    evs = [e for (e,) in q(env, "SELECT evidence_id FROM brief_case_evidence"
                                " WHERE repo_full_name = 'org-p/alpha-cli'")]  # fmt: skip
    assert evs
    requests.optout_repo(env.db, env.snaps, platform="github", repo_key=key, pz=pz,
                         llm_store=env.llm)  # fmt: skip
    for t in ("brief_pilot_case", "brief_case_evidence", "brief_case_gap", "brief_coding",
              "brief_evidence_decay"):  # fmt: skip
        assert q(env, f"SELECT count(*) FROM {t} WHERE repo_full_name = 'org-p/alpha-cli'"
                      " OR repo_id = %s", key)[0][0] == 0, t  # fmt: skip
    for e in evs:
        assert q(env, "SELECT count(*) FROM evidence WHERE id = %s", e)[0][0] == 0
    # the other cases stay
    assert q(env, "SELECT count(*) FROM brief_coding WHERE repo_full_name ="
                  " 'org-p/theta-b2'")[0][0] > 0  # fmt: skip


def test_m23_export_and_inventory(env: Env, pg_url: str, tmp_path: Path) -> None:
    from pigtail.capture.inventory import inventory
    from pigtail.export.jsonl import export_jsonl

    assert run_pilot(env.brief, env.deps(), OPTS).exit_code == 0
    res = export_jsonl(pg_url, tmp_path / "export")
    assert {"brief_pilot", "brief_case_evidence", "brief_reliability",
            "brief_evidence_decay"} <= set(res.tables)  # fmt: skip
    assert "brief_coding" not in res.tables  # carries excerpts: person-level export only
    res = export_jsonl(pg_url, tmp_path / "export2", include_person_level=True)
    assert "brief_coding" in res.tables
    inv = inventory(env.conn)
    by = {t.table: t for t in inv.tables}
    assert by["brief_pilot_case"].rows == 5 and by["brief_pilot_case"].distinct_repos == 5
    assert inv.unlisted == ()


def test_m23_budget_guard_rejects_before_submit(env: Env) -> None:
    """The guard's `before_submit` is what every pilot batch passes through (H6 above caps)."""
    from pigtail.briefs.budget import BudgetGuard

    g = BudgetGuard(env.brief.budget, env.llm, month_cap_usd=0.05, approved_paid=True)
    with pytest.raises(BudgetStop):
        g.before_submit("double_coding", 5, 0.2)


def test_m23_coder_and_adjudicator_inputs_are_blind_to_outcome_numbers(env: Env) -> None:
    """blind-v1 (ADR-086): no HN points, PH votes or comments, star or fork counts (structured
    or in text), outcome, percentile, rank, role, pair or view in any coder or adjudicator input;
    event kinds and times stay."""
    import re

    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0
    prompts = env.backend.prompts()
    adj = [p for b in env.backend.submitted for _, par in b
           for p in [par["messages"][0]["content"]] if "## Disagreements" in p]  # fmt: skip
    assert adj and len(prompts) == 15
    # the fixture's numbers are really there (in the stored evidence), and never in an input
    stored = " ".join(
        env.snaps.get(h).decode(errors="replace")
        for (h,) in q(env, "SELECT content_hash FROM evidence WHERE deletion_state = 'present'")
    )
    assert all(n in stored for n in ("98765", "4242", "7777", "8888"))  # README: base64
    for p in prompts:
        for n in BLIND_NUMBERS:
            assert n not in p, n
        for key in ('"points"', '"votesCount"', '"commentsCount"', "stargazerCount",
                    "forkCount", '"percentile', '"values"', '"rank"', '"role"', '"pair',
                    '"view"', "follow_through", "matched_loser"):  # fmt: skip
            assert key not in p, key
        assert re.search(r"\d\s*(stars|forks|points|votes)\b", p, re.I) is None
    # event kinds and times are kept
    launch = next(p for p in prompts if "kind: launch_events" in p)
    assert '"source": "ph_launch"' in launch and '"featuredAt": "2026-03-10T08:00:00Z"' in launch
    assert '"source": "show_hn"' in launch and '"time": "2026-03-10T15:00:00+00:00"' in launch
    assert "[count withheld]" in launch or "[count withheld]" in " ".join(prompts)


# --- ADR-086 addendum 1: coding failures are honest and redoable -------------------------------
def test_m23_all_coding_failed_ends_failed_without_projection_and_reruns(env: Env) -> None:
    """The live failure: every coder request answered `invalid_request_error`. The pilot ends
    `failed` (exit 7) with the error per case in the run and the private report, writes no cost
    model, projection or alpha, and the same command redoes the coding once the cause is fixed."""
    env.backend.fail_when = is_coder
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.status == "failed" and out.exit_code == EXIT_CODING_FAILED == 7, out.message
    rid = out.brief_run_id
    assert "invalid_request_error" in out.message and "compiled grammar" in out.message
    status, stop, spend = q(env, "SELECT status, stop, spend FROM brief_runs WHERE id = %s",
                            rid)[0]  # fmt: skip
    assert status == "failed" and stop["kind"] == "coding_failed" and stop["cases"] == 5
    assert stop["errors"] == [{"error": f"invalid_request_error: {GRAMMAR_ERROR}",
                               "requests": 10}]  # fmt: skip
    assert spend["api_usd"] == 0.0
    # nothing measured: no cost model, no projection, no alpha, no adjudication batch
    assert q(env, "SELECT count(*) FROM brief_case_cost_model")[0][0] == 0
    assert q(env, "SELECT count(*) FROM brief_reliability")[0][0] == 0
    assert len(env.backend.submitted) == 2
    summary = q(env, "SELECT summary FROM brief_pilot WHERE brief_run_id = %s", rid)[0][0]
    assert summary["coding_failed_cases"] == 5 and not summary.get("report_written")
    assert summary["projection"]["skipped"] and "projected_total_usd" not in summary["projection"]
    errs = summary["coding_errors"]
    assert len(errs) == 5 and all(
        e[p] == {"failure": "invalid_request_error", "type": "invalid_request_error",
                 "message": GRAMMAR_ERROR} for e in errs.values() for p in ("A", "B")
    )  # fmt: skip
    # the private failure report names the error per case
    report = json.loads(Path(out.report_paths["json"]).read_text())
    assert report["outcome"]["status"] == "failed" and report["projection"]["skipped"]
    assert all(c["coding_errors"]["A"]["message"] == GRAMMAR_ERROR for c in report["cases"])
    assert "FAILED" in Path(out.report_paths["md"]).read_text()
    # the failed requests cost nothing
    assert q(env, "SELECT count(*), COALESCE(sum(cost_usd), 0) FROM llm_cost_ledger"
                  " WHERE brief_run_id = %s AND status = 'error'", rid)[0] == (10, 0)  # fmt: skip
    # fixed: the same command resumes the same run and redoes the coding
    env.backend.fail_when = None
    again = run_pilot(env.brief, env.deps(), OPTS)
    assert again.exit_code == 0 and again.status == "succeeded", again.message
    assert again.brief_run_id == rid and len(env.backend.submitted) == 5  # 2 coders + adj
    assert q(env, "SELECT count(*) FROM brief_coding WHERE brief_run_id = %s"
                  " AND excluded = 'coding_failed'", rid)[0][0] == 0  # fmt: skip
    assert q(env, "SELECT status, resumes FROM brief_runs WHERE id = %s", rid)[0] == (
        "succeeded", 1)  # fmt: skip
    ((n, per_case),) = q(env, "SELECT n_cases, per_case FROM brief_case_cost_model")
    assert n == 5 and per_case["model_version"] == "case-cost-v2"
    assert per_case["measured_usd_per_case"]["total"] > 0
    assert per_case["coder_call"]["output"] == 3000  # the error rows are not averaged in
    assert again.summary["projection"]["projected_total_usd"] > 0


def _fail_theta_a(params: dict[str, Any]) -> bool:
    return "Work field by field" in params["system"][0]["text"] and (
        "# theta-b2" in params["messages"][0]["content"]
    )


def test_m23_partial_failure_is_redone_without_paying_twice(env: Env) -> None:
    """Coder A fails for one case: the pilot finishes on the other four (the failure named), and
    the same command redoes that case only: its pass B comes from the result cache, the other
    cases are not called again."""
    env.backend.fail_when = _fail_theta_a
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0 and out.status == "succeeded", out.message
    rid = out.brief_run_id
    assert "coding failed for 1 case" in out.message
    assert out.summary["cases_coded"] == 4 and out.summary["coding_failed_cases"] == 1
    assert len(env.backend.submitted) == 3
    (n,) = q(env, "SELECT n_cases FROM brief_case_cost_model")[0]
    assert n == 4  # measured on the coded cases only

    def calls() -> dict[str, int]:
        rows = q(env, "SELECT prompt_id, count(*) FROM llm_cost_ledger WHERE brief_run_id = %s"
                      " AND status <> 'error' GROUP BY 1", rid)  # fmt: skip
        return dict(rows)

    before = calls()
    assert before == {"case-coder-a": 4, "case-coder-b": 5, "case-adjudicator": 4}
    env.backend.fail_when = None
    again = run_pilot(env.brief, env.deps(), OPTS)
    assert again.exit_code == 0 and again.brief_run_id == rid, again.message
    assert again.summary["coding_failed_cases"] == 0 and again.summary["cases_coded"] == 5
    # one new coder-A request and one adjudication, nothing else
    assert calls() == {"case-coder-a": 5, "case-coder-b": 5, "case-adjudicator": 5}
    new = env.backend.submitted[3:]
    assert [len(b) for b in new] == [1, 1]
    assert "# theta-b2" in new[0][0][1]["messages"][0]["content"]
    # every case has final values, none failed
    assert q(env, "SELECT count(DISTINCT case_key) FROM brief_coding WHERE brief_run_id = %s"
                  " AND pass = 'final'", rid)[0][0] == 5  # fmt: skip
    assert q(env, "SELECT count(*) FROM brief_coding WHERE brief_run_id = %s"
                  " AND excluded = 'coding_failed'", rid)[0][0] == 0  # fmt: skip
    # a third run has nothing to do
    third = run_pilot(env.brief, env.deps(), OPTS)
    assert third.status == "complete" and len(env.backend.submitted) == 5


def test_m23_adjudicator_value_outside_the_enum_is_schema_invalid(env: Env) -> None:
    from tests import forensics_fake

    orig = forensics_fake.adjudicator_answer

    def bad(prompt: str) -> dict[str, Any]:
        out = orig(prompt)
        for d in out["decisions"]:
            if d["unit"] == "category_primary":
                d["value"] = "not-a-category"
        return out

    forensics_fake.adjudicator_answer = bad
    try:
        out = run_pilot(env.brief, env.deps(), OPTS)
    finally:
        forensics_fake.adjudicator_answer = orig
    assert out.exit_code == 0
    rows = q(env, "SELECT value, status, unknown_reason FROM brief_coding WHERE pass ="
                  " 'adjudicator' AND unit = 'category_primary'")  # fmt: skip
    assert rows and set(rows) == {("unknown", "schema_invalid", "schema_invalid")}


def test_m23_zero_cost_models_are_never_used_for_estimates(env: Env) -> None:
    """A model stored from zero measured cost (as the failed live pilot stored one) or of the
    old cost-model version is ignored: the estimate falls back to planning."""
    from pigtail.forensics.pilot import plan
    from pigtail.forensics.store import latest_cost_model, save_cost_model

    zero = {"source": "measured", "model_version": "case-cost-v1",
            "coder_call": {"input": 0, "output": 0}, "adjudication_call": {"input": 0,
            "output": 0}, "adjudication_share": 0.0, "n_cases": 5,
            "measured_usd_per_case": {"total": 0.0}}  # fmt: skip
    for version in ("case-cost-v1", "case-cost-v2"):
        save_cost_model(env.conn, brief_id=env.brief.brief_id, brief_version=1,
                        brief_run_id=None, model_version=version, n_cases=5, per_case=zero,
                        projection={}, h6=False, prices_as_of=None, code_commit=None)  # fmt: skip
    assert latest_cost_model(env.conn, env.brief.brief_id) is None
    _sel, _cases, est, err = plan(env.conn, env.brief, OPTS, env.deps())
    assert err is None and est is not None and est["cost_model"]["source"] == "planning"
    assert est["per_case_usd"]["total"] and est["per_case_usd"]["total"] > 0


def test_m23_max_tokens_is_billed_counted_and_fails_only_that_case(env: Env) -> None:
    """ADR-087 (the second live pilot failure): coder A of one case ends `max_tokens` in its
    batch and again in its standard fallback. Both responses were billed: the ledger holds their
    tokens and cost (`error_billed`, batch and standard price), the budget guard's spend and the
    monthly usage count them, and the pilot finishes on the other cases with that case failed
    (the error named), never aborting the run."""
    from pigtail.briefs.budget import BudgetGuard, month_start
    from pigtail.llm.pricing import TokenUsage, cost_usd
    from tests.forensics_fake import MAX_TOKENS_IN, MAX_TOKENS_OUT

    env.backend.max_tokens_when = _fail_theta_a
    out = run_pilot(env.brief, env.deps(), OPTS)
    assert out.exit_code == 0 and out.status == "succeeded", out.message
    rid = out.brief_run_id
    assert out.summary["coding_failed_cases"] == 1 and out.summary["cases_coded"] == 4
    assert "coding failed for 1 case" in out.message
    # the batch result and the standard fallback, both billed and recorded
    assert len(env.backend.standard_calls) == 1
    rows = q(env, "SELECT batch_id IS NOT NULL, input_tokens, output_tokens, cost_usd"
                  " FROM llm_cost_ledger WHERE brief_run_id = %s AND status = 'error_billed'"
                  " ORDER BY 1", rid)  # fmt: skip
    usage = TokenUsage(input=MAX_TOKENS_IN, output=MAX_TOKENS_OUT)
    std = cost_usd("claude-sonnet-5", usage) or 0.0
    batch = cost_usd("claude-sonnet-5", usage, batch=True) or 0.0
    assert [(b, i, o) for b, i, o, _c in rows] == [
        (False, MAX_TOKENS_IN, MAX_TOKENS_OUT),
        (True, MAX_TOKENS_IN, MAX_TOKENS_OUT),
    ]
    assert [float(c) for *_x, c in rows] == pytest.approx([std, batch], abs=1e-6)
    assert std > 0 and batch == pytest.approx(std / 2)
    billed = std + batch
    # no zero-cost error rows for them
    assert q(env, "SELECT count(*) FROM llm_cost_ledger WHERE brief_run_id = %s"
                  " AND status = 'error'", rid)[0][0] == 0  # fmt: skip
    # the budget counts them: the brief's ledger total, the run's spend, the monthly usage
    total = float(q(env, "SELECT sum(cost_usd) FROM llm_cost_ledger WHERE brief_run_id = %s",
                    rid)[0][0])  # fmt: skip
    guard = BudgetGuard(env.brief.budget, env.llm, clock=lambda: NOW,
                        brief_ledger=lambda: PgCostLedger(env.conn).brief_total(
                            env.brief.brief_id))  # fmt: skip
    assert guard.refresh() == pytest.approx(total) and total > billed
    month = env.llm.usage_since("api", month_start(NOW))["cost_usd"]
    assert month == pytest.approx(total, abs=1e-5)
    assert q(env, "SELECT spend FROM brief_runs WHERE id = %s", rid)[0][0]["api_usd"] == (
        pytest.approx(total, abs=1e-5))  # fmt: skip
    # the case's failure is recorded with its type
    detail = q(env, "SELECT DISTINCT detail FROM brief_coding WHERE brief_run_id = %s"
                    " AND excluded = 'coding_failed'", rid)  # fmt: skip
    assert len(detail) == 1 and detail[0][0]["failure"] == "max_tokens"
    assert detail[0][0]["error"]["type"] == "max_tokens"
    assert "max_tokens" in (detail[0][0]["error"]["message"] or "")
