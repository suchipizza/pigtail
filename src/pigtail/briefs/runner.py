"""`pigtail run --brief <id>`: the brief's stage runner (PRD R19.1, R18.5, R18.6, R15.11; D7).

M22 stages, in order: **discovery** (R4.5), **relevance** (R4.6), **shortlist** (R4.7, ends
with the shortlist awaiting the user's review). Later milestones append outcome sort and deep
forensics.

**Checkpoints and resume.** A run is one `brief_runs` row (brief version and content hash,
data version, code commit, prompt, rubric and model versions, estimate, approval, spend, and the
per-stage status in `stages`). Every stage writes its progress to the row's `checkpoint` as it
goes (discovery: the queries done; relevance: the chunk plan and the chunks done). Running the
same command again after a crash, a budget stop, a failure or while a batch is still running
**resumes that run in place** (same id, `resumes` + 1): completed stages are skipped, discovery
skips its done queries, and relevance rebuilds exactly the same requests, so answered ones come
from the LLM cache and in-flight batches are collected by their stored batch ids instead of
being submitted and paid for again (the batch store is keyed on the run id, which is why a
resume keeps it). A Postgres advisory lock per brief keeps two runs of one brief apart.

**Idempotent.** Candidates are upserted, verdicts are only computed for candidates without one
under the current rubric, and a brief version whose run is complete is not run again unless
`--incremental` asks for a refresh: a new run (`resumed_from` the last one) whose discovery only
adds repos created since the last discovery, whose relevance judges only new candidates, and
whose shortlist keeps every decision already made.

**Money.** The cost estimate is shown before the run (CLI) and stored on the run. Paid steps need
explicit approval (`--approve-paid`, or the approval recorded by `pigtail brief estimate
--approve-paid`). `BudgetGuard` checks every batch before it is submitted, with the stage's
per-request estimate, against `budget.money_usd` (API spend of all the brief's runs, from the
cost ledger) and `BUDGET_USD_MONTH`; a refused check ends the run as `paused_budget` with its
checkpoint (R18.5). The GitHub request budget ends discovery the same way (resumable).
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from pigtail.briefs.budget import BudgetGuard, BudgetStop
from pigtail.briefs.cache import BriefRuns, data_version
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.discovery import DISCOVERY_VERSION, Discovery, DiscoveryConfig, DiscoveryPaused
from pigtail.briefs.estimate import RUN_STAGES
from pigtail.briefs.model import Brief
from pigtail.briefs.relevance import JOB as RELEVANCE_JOB
from pigtail.briefs.relevance import PROMPT_ID, PROMPT_VERSION, Relevance, prompt_for
from pigtail.briefs.shortlist import Shortlist
from pigtail.capture.db import CaptureDB
from pigtail.capture.runs import git_commit
from pigtail.connectors.github_budget import BudgetExhausted
from pigtail.llm import BatchPending, LLMClient, LLMError
from pigtail.logsafe import scrub

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NEEDS_APPROVAL = 3
EXIT_BUDGET = 4
EXIT_WAITING = 5
EXIT_BUSY = 6

RESUMABLE = ("planned", "running", "waiting_batch", "paused_budget", "failed")
COMPLETE = ("awaiting_review", "succeeded")


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RunOptions:
    stages: tuple[str, ...] = RUN_STAGES
    incremental: bool = False
    approve_paid: bool = False
    wait_seconds: float | None = None  # how long to poll a batch before leaving it running
    poll_seconds: float = 60.0
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    estimate: dict[str, Any] | None = None  # shown before the run; stored on it


@dataclass
class RunDeps:
    conn: psycopg.Connection[Any]  # autocommit
    client: LLMClient
    github: Any = None  # GitHubConnector (discovery, READMEs)
    hn: Any = None  # HNShowDiscoveryConnector
    gharchive: Any = None
    month_cap_usd: float = 200.0
    clock: Callable[[], datetime] = utcnow
    sleep: Callable[[float], None] | None = None
    run_record_id: str | None = None  # the generic `runs` record of this invocation
    recorder: Any = None  # its RunRecorder (connector counts)


@dataclass
class RunOutcome:
    brief_run_id: str | None
    status: str
    exit_code: int
    message: str
    resumed: bool = False
    stages: dict[str, Any] = field(default_factory=dict)
    stop: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class _Run:
    """Row helpers for one `brief_runs` row."""

    def __init__(self, conn: psycopg.Connection[Any], row: dict[str, Any]) -> None:
        self.conn = conn
        self.id: str = row["id"]
        self.stages: dict[str, Any] = dict(row.get("stages") or {})
        self.checkpoint: dict[str, Any] = dict(row.get("checkpoint") or {})
        self.approved_paid = bool(row.get("approved_paid"))
        self.incremental = bool(row.get("incremental"))

    def update(self, **cols: Any) -> None:
        sets = ", ".join(f"{k} = %s" for k in cols)
        vals = [Jsonb(v) if isinstance(v, dict | list) else v for v in cols.values()]
        self.conn.execute(f"UPDATE brief_runs SET {sets} WHERE id = %s", (*vals, self.id))

    def save_checkpoint(self) -> None:
        self.update(checkpoint=self.checkpoint)

    def stage(self, name: str, status: str, **extra: Any) -> None:
        s = dict(self.stages.get(name) or {})
        s["status"] = status
        s.update(extra)
        self.stages[name] = s
        self.update(stages=self.stages, checkpoint=self.checkpoint)

    def done(self, name: str) -> bool:
        return (self.stages.get(name) or {}).get("status") == "done"


def _lock(conn: psycopg.Connection[Any], brief_id: str) -> bool:
    row = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s))", (f"pigtail-brief-run:{brief_id}",)
    ).fetchone()
    return bool(row and row[0])


def _unlock(conn: psycopg.Connection[Any], brief_id: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"pigtail-brief-run:{brief_id}",))


def _latest(
    conn: psycopg.Connection[Any], brief: Brief, statuses: tuple[str, ...]
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id FROM brief_runs WHERE brief_id = %s AND brief_version = %s"
        " AND status = ANY(%s) ORDER BY created_at DESC, id DESC LIMIT 1",
        (brief.brief_id, brief.version, list(statuses)),
    ).fetchone()
    return BriefRuns(conn).get(row[0]) if row else None


def find_run(conn: psycopg.Connection[Any], brief: Brief) -> tuple[str, dict[str, Any] | None]:
    """('resume', row) for an unfinished run of this version, ('complete', row) when the last
    one is done, ('new', None) otherwise. Used by `--dry-run` to say what would happen."""
    last = _latest(conn, brief, RESUMABLE + COMPLETE)
    if last is None:
        return "new", None
    if last["status"] in RESUMABLE:
        return "resume", last
    return "complete", last


def _spend(conn: psycopg.Connection[Any], run_id: str, brief_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COALESCE(sum(cost_usd), 0), count(*) FROM llm_cost_ledger"
        " WHERE brief_run_id = %s AND backend = 'api'",
        (run_id,),
    ).fetchone()
    from pigtail.llm.batch import PgCostLedger

    return {
        "api_usd_this_run": round(float(row[0]) if row else 0.0, 6),
        "api_calls_this_run": int(row[1]) if row else 0,
        "api_usd_brief_total": round(PgCostLedger(conn).brief_total(brief_id), 6),
        "label": "list price, from the cost ledger",
    }


def run_brief(brief: Brief, deps: RunDeps, opts: RunOptions) -> RunOutcome:
    """Run (or resume) the M22 stages of one stored brief version. See the module docstring."""
    if brief.version is None:
        raise ValueError("run a stored brief version")
    conn = deps.conn
    if not _lock(conn, brief.brief_id):
        return RunOutcome(
            None, "busy", EXIT_BUSY, f"another run of {brief.brief_id} is in progress"
        )
    try:
        return _run(brief, deps, opts)
    finally:
        _unlock(conn, brief.brief_id)


def _run(brief: Brief, deps: RunDeps, opts: RunOptions) -> RunOutcome:
    conn = deps.conn
    assert brief.version is not None
    kind, row = find_run(conn, brief)
    resumed = False
    since: datetime | None = None
    if kind == "complete" and not opts.incremental:
        assert row is not None
        msg = (
            f"{brief.brief_id} v{brief.version} was already run ({row['id']}, {row['status']}); "
            "nothing to do. Review the shortlist, or refresh with --incremental."
        )
        return RunOutcome(row["id"], row["status"], EXIT_OK, msg, stages=row.get("stages") or {})
    _prompt, rubric_v = prompt_for(brief)
    versions = {
        "prompt_versions": {
            PROMPT_ID: PROMPT_VERSION,
            "rubric": rubric_v,
            "discovery": DISCOVERY_VERSION,
        },
        "model_versions": {"relevance": deps.client.model_for(RELEVANCE_JOB)},
    }
    if kind == "resume" and row is not None and row["status"] != "planned":
        run = _Run(conn, row)
        run.update(
            status="running", resumes=int(row.get("resumes") or 0) + 1, stop=None, finished_at=None
        )
        resumed = True
    else:
        prev = row if kind == "complete" else None
        if prev is not None:  # --incremental: only repos created since the last discovery
            ts = (prev.get("stages") or {}).get("discovery", {}).get("finished_at")
            since = datetime.fromisoformat(ts) if ts else None
        if kind == "resume" and row is not None:  # adopt a run planned by `brief estimate`
            run = _Run(conn, row)
            run.update(
                status="running",
                started_at=deps.clock(),
                data_version=data_version(conn),
                code_commit=git_commit(),
                **versions,
            )
        else:
            created = BriefRuns(conn).create(
                brief,
                data_version=data_version(conn),
                estimate=opts.estimate,
                approved_paid=opts.approve_paid,
                code_commit=git_commit(),
                resumed_from=prev["id"] if prev else None,
                run_id=deps.run_record_id,
                prompt_versions=versions["prompt_versions"],
                model_versions=versions["model_versions"],
            )
            got = BriefRuns(conn).get(created.id)
            assert got is not None
            run = _Run(conn, got)
            run.update(status="running", started_at=deps.clock(), incremental=prev is not None)
            run.incremental = prev is not None
        if since is not None:
            run.checkpoint["since"] = since.isoformat()
    if deps.run_record_id is not None:
        run.update(run_id=deps.run_record_id)
    if "run_date" not in run.checkpoint:  # the window's end is fixed for the whole run
        run.checkpoint["run_date"] = deps.clock().date().isoformat()
        run.save_checkpoint()
    if run.checkpoint.get("since"):
        since = datetime.fromisoformat(run.checkpoint["since"])

    from pigtail.llm.batch import PgCostLedger

    guard = BudgetGuard(
        budget=brief.budget,
        usage=deps.client.store,
        month_cap_usd=deps.month_cap_usd,
        approved_paid=opts.approve_paid or run.approved_paid,
        spent_usd=PgCostLedger(conn).brief_total(brief.brief_id),
        overrides=dict(deps.client.overrides),
    )
    if opts.approve_paid and not run.approved_paid:
        run.update(approved_paid=True)
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)

    def stop(status: str, code: int, msg: str, stop_rec: dict[str, Any] | None) -> RunOutcome:
        run.update(
            status=status,
            stop=stop_rec,
            checkpoint=run.checkpoint,
            spend=_spend(conn, run.id, brief.brief_id),
            finished_at=deps.clock() if status != "waiting_batch" else None,
        )
        return RunOutcome(run.id, status, code, msg, resumed, run.stages, stop_rec)

    current = ""
    try:
        for name in RUN_STAGES:
            if name not in opts.stages:
                continue
            if run.done(name):
                continue
            current = name
            run.stage(name, "running", started_at=deps.clock().isoformat())
            if name == "discovery":
                if deps.github is None:
                    return stop(
                        "failed",
                        EXIT_USAGE,
                        "discovery needs GITHUB_TOKEN (the operator's own token)",
                        {"kind": "config", "step": "discovery"},
                    )
                cp = run.checkpoint.setdefault("discovery", {})
                disc = Discovery(
                    brief=brief,
                    db=db,
                    store=store,
                    github=deps.github,
                    hn=deps.hn,
                    brief_run_id=run.id,
                    run_date=date.fromisoformat(run.checkpoint["run_date"]),
                    checkpoint=cp,
                    save=lambda _cp: run.save_checkpoint(),
                    cfg=opts.discovery,
                    run=deps.recorder,
                    gharchive=deps.gharchive,
                    clock=deps.clock,
                    since=since,
                )
                res = disc.run_stage()
                run.stage(name, "done", finished_at=deps.clock().isoformat(), result=res.to_dict())
            elif name == "relevance":
                if store.count() == 0:
                    return stop(
                        "failed",
                        EXIT_USAGE,
                        "no candidates for this brief version: run discovery first",
                        {"kind": "prerequisite", "step": "relevance"},
                    )
                guard.check_backend(deps.client.backend_for(RELEVANCE_JOB).name, job=RELEVANCE_JOB)
                rel = Relevance(
                    brief=brief,
                    client=deps.client,
                    store=store,
                    brief_run_id=run.id,
                    checkpoint=run.checkpoint.setdefault("relevance", {}),
                    save=lambda _cp: run.save_checkpoint(),
                    readme=_readme_loader(deps, store),
                    before_submit=guard.before_submit,
                    on_group=lambda: setattr(
                        guard, "spent_usd", PgCostLedger(conn).brief_total(brief.brief_id)
                    ),
                    poll_seconds=opts.poll_seconds,
                    timeout_seconds=opts.wait_seconds,
                    sleep=deps.sleep,
                    clock=deps.clock,
                )
                try:
                    rres = rel.run_stage()
                finally:
                    _link(db, run.id, rel.result.readme_evidence)
                run.stage(name, "done", finished_at=deps.clock().isoformat(), result=rres.to_dict())
            elif name == "shortlist":
                sl = Shortlist(conn, brief)
                st = sl.ensure(run.id)
                sl.sync_scope()
                view = sl.view()
                run.stage(
                    name,
                    "done",
                    finished_at=deps.clock().isoformat(),
                    result={"status": st["status"], **view["counts"]},
                )
        if "shortlist" in opts.stages or run.done("shortlist"):
            status = "awaiting_review"
            st2 = Shortlist(conn, brief).status()
            if st2 is not None and st2["status"] == "final":
                status = "succeeded"
            msg = (
                f"shortlist of {brief.brief_id} v{brief.version} is awaiting your review: "
                f"pigtail brief shortlist show {brief.brief_id} "
                f"(or /briefs/{brief.brief_id}/shortlist)"
                if status == "awaiting_review"
                else f"{brief.brief_id} v{brief.version}: shortlist final"
            )
        else:
            status = "running"
            msg = f"stages done: {', '.join(s for s in RUN_STAGES if run.done(s))}"
        run.update(
            status=status,
            spend=_spend(conn, run.id, brief.brief_id),
            finished_at=deps.clock() if status != "running" else None,
        )
        return RunOutcome(run.id, status, EXIT_OK, msg, resumed, run.stages)
    except BudgetStop as e:
        run.stage(current, "paused_budget")
        code = EXIT_NEEDS_APPROVAL if e.kind == "approval" else EXIT_BUDGET
        return stop(
            "paused_budget", code, f"stopped before any spend over the cap: {e}", e.to_dict()
        )
    except (DiscoveryPaused, BudgetExhausted) as e:
        run.stage(current, "paused_budget")
        reason = getattr(e, "reason", str(e))
        return stop(
            "paused_budget",
            EXIT_BUDGET,
            f"GitHub request budget exhausted ({reason}); run again later to resume",
            {"kind": "github_requests", "step": current, "detail": str(reason)},
        )
    except BatchPending as e:
        run.stage(current, "waiting_batch", batch_ids=e.batch_ids)
        return stop(
            "waiting_batch",
            EXIT_WAITING,
            f"{len(e.batch_ids)} batch(es) still running; run the same command again "
            "to collect them (nothing is resubmitted)",
            {"kind": "batch_pending", "step": current, "batch_ids": e.batch_ids},
        )
    except LLMError as e:
        run.stage(current, "failed")
        return stop(
            "failed",
            EXIT_FAILED,
            f"LLM error in {current}: {type(e).__name__}",
            {"kind": "llm", "step": current, "error": type(e).__name__},
        )
    except Exception as e:
        run.stage(current or "run", "failed")
        detail = scrub(f"{type(e).__name__}: {e}")[:500]
        stop(
            "failed",
            EXIT_FAILED,
            detail,
            {
                "kind": "error",
                "step": current,
                "error": type(e).__name__,
                "trace": scrub("".join(traceback.format_exception_only(e)))[:500],
            },
        )
        raise


def _link(db: CaptureDB, run_id: str, evidence_ids: list[str]) -> None:
    """R19.9: the README snapshots this run sent (as excerpts) are the run's evidence."""
    if not evidence_ids:
        return
    from pigtail.privacy.snapshot_retention import link

    link(db, run_id, evidence_ids)


def _readme_loader(
    deps: RunDeps, store: CandidateStore
) -> Callable[[Candidate], tuple[bytes | None, str | None]]:
    def load(c: Candidate) -> tuple[bytes | None, str | None]:
        rec = c.metadata.get("readme") or {}
        if rec.get("none"):
            return None, None
        gh = deps.github
        h = rec.get("content_hash")
        if h and gh is not None and gh.store.exists(h):
            return gh.store.get(h), rec.get("evidence_id")
        if gh is None or not gh.enabled or c.repo_full_name is None:
            return None, None
        f = gh.readme(c.repo_full_name, repo_id=c.repo_id)
        if f is None:
            store.set_metadata(c.ref, {"readme": {"none": True}})
            return None, None
        store.set_metadata(
            c.ref, {"readme": {"evidence_id": f.evidence.id, "content_hash": f.content_hash}}
        )
        return f.data, f.evidence.id

    return load
