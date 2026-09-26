"""Brief-run provenance and cache reuse on re-runs (PRD R18.4, R18.6; D7).

**Cache key design.** A brief run is a chain of stages. Each stage depends on a declared subset
of the brief (`STAGE_INPUTS`, dotted paths) and, for whole-stage outputs, on the stages before
it. Two kinds of stage:

- *Whole-stage* (`expansion`, `discovery`, `outcome_sort`, `matching`, `patterns`): one result per
  brief version. Its fingerprint is
  `sha256(stage, stage_version, brief-subset hash, upstream fingerprints, data_version)`.
- *Per-item* (`relevance` per candidate, `evidence` per repo, `extraction` per case): one result
  per item. The key is
  `sha256(stage, stage_version, brief-subset hash, item_ref, item input hash)`
  and deliberately leaves out upstream fingerprints: an item whose own inputs are unchanged is
  reused even when the set of items changed (for example, a stricter success threshold changes
  the winners, but each remaining case's extraction is reused). `extraction` depends on no brief
  field at all, so its items are reused across briefs too; LLM calls are additionally cached by
  `LLMClient` (prompt, input hash, schema, model, backend; ADR-006).

`plan_rerun(previous, new)` turns the field diff between two brief versions into a per-stage
decision (reuse / recompute) with the fields that caused it, so the estimate and the run report
can say what is recomputed (R18.4). `StageCache` stores results in Postgres
(`brief_stage_cache`) and counts reuse per stage; `BriefRuns` writes the provenance record
(`brief_runs`: brief version and hash, data version, code/codebook/prompt/model versions,
estimate, spend, budget stop and checkpoint, R18.5–R18.6). The shortlist decisions table
(`shortlist_decision`) is created now and written in M13 (R4.7).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from pigtail.briefs.budget import BudgetStop
from pigtail.briefs.diff import field_changes
from pigtail.briefs.model import Brief, sha256_json

Stage = Literal[
    "expansion",
    "discovery",
    "relevance",
    "evidence",
    "outcome_sort",
    "matching",
    "extraction",
    "patterns",
]
STAGES: tuple[Stage, ...] = (
    "expansion",
    "discovery",
    "relevance",
    "evidence",
    "outcome_sort",
    "matching",
    "extraction",
    "patterns",
)
PER_ITEM: frozenset[Stage] = frozenset({"relevance", "evidence", "extraction"})

# The brief fields each stage reads (dotted prefixes of `Brief.content()`).
STAGE_INPUTS: dict[Stage, tuple[str, ...]] = {
    # Exactly what `pigtail.briefs.expansion.expansion_input` sends to the model (R18.7).
    "expansion": (
        "project.description",
        "project.target_users",
        "project.business_model",
        "field.include",
        "field.exclude",
    ),
    "discovery": (
        "field",
        "window",
        "expansion",
        "optional_sources",
        "geography.languages",
        "distribution_exemplars",
    ),
    "relevance": ("project", "field", "expansion"),
    "evidence": ("window", "optional_sources"),
    "outcome_sort": ("success", "panel.winners", "window", "field.reference_cases"),
    "matching": ("panel", "distribution_exemplars"),
    "extraction": (),
    "patterns": ("success", "panel", "distribution_exemplars", "report"),
}
# Whole-stage upstream dependencies (per-item stages depend on their items instead).
UPSTREAM: dict[Stage, tuple[Stage, ...]] = {
    "expansion": (),
    "discovery": ("expansion",),
    "relevance": ("discovery",),
    "evidence": ("relevance",),
    "outcome_sort": ("relevance", "evidence"),
    "matching": ("outcome_sort",),
    "extraction": ("matching",),
    "patterns": ("matching", "extraction"),
}
STAGE_VERSIONS: dict[Stage, str] = dict.fromkeys(STAGES, "v0")  # bumped when a stage changes


def utcnow() -> datetime:
    return datetime.now(UTC)


def _select(content: dict[str, Any], path: str) -> Any:
    node: Any = content
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def brief_subset_hash(brief: Brief, stage: Stage) -> str:
    content = brief.content()
    return sha256_json({p: _select(content, p) for p in STAGE_INPUTS[stage]})


def stage_keys(brief: Brief, data_version: str | None) -> dict[str, str]:
    """Whole-stage fingerprints for one brief version and data version."""
    keys: dict[str, str] = {}
    for stage in STAGES:
        keys[stage] = sha256_json(
            {
                "stage": stage,
                "stage_version": STAGE_VERSIONS[stage],
                "brief": brief_subset_hash(brief, stage),
                "upstream": [keys[u] for u in UPSTREAM[stage]],
                "data_version": data_version,
            }
        )
    return keys


def item_key(brief: Brief, stage: Stage, item_ref: str, input_hash: str) -> str:
    """Cache key of one item of a per-item stage (see the module docstring)."""
    if stage not in PER_ITEM:
        raise ValueError(f"{stage} is a whole-stage step; use stage_keys()")
    return sha256_json(
        {
            "stage": stage,
            "stage_version": STAGE_VERSIONS[stage],
            "brief": brief_subset_hash(brief, stage),
            "item": item_ref,
            "input": input_hash,
        }
    )


def _affected(path: str, inputs: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + ".") or p.startswith(path + ".") for p in inputs)


@dataclass(frozen=True)
class StageDecision:
    stage: Stage
    action: Literal["reuse", "recompute", "reuse_unchanged_items", "recompute_items"]
    because: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "action": self.action, "because": self.because}


@dataclass(frozen=True)
class RerunPlan:
    from_version: int | None
    to_version: int | None
    changed_fields: list[str]
    stages: list[StageDecision]

    def recomputed(self) -> list[str]:
        return [s.stage for s in self.stages if s.action in ("recompute", "recompute_items")]

    def reused(self) -> list[str]:
        return [s.stage for s in self.stages if s.action in ("reuse", "reuse_unchanged_items")]

    def fully_reused(self) -> list[str]:
        """Stages expected to make no new calls: reused whole, or per-item with an unchanged
        item set (the estimate counts these as free)."""
        return [
            s.stage
            for s in self.stages
            if s.action == "reuse"
            or (s.action == "reuse_unchanged_items" and s.because == ["unchanged"])
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changed_fields": self.changed_fields,
            "stages": [s.to_dict() for s in self.stages],
        }


def plan_rerun(previous: Brief | None, new: Brief) -> RerunPlan:
    """Which stages an edit affects (R18.4). `previous=None` is a first run (all computed)."""
    if previous is None:
        return RerunPlan(
            None,
            new.version,
            [],
            [StageDecision(s, "recompute", ["first run"]) for s in STAGES],
        )
    changed = [c.path for c in field_changes(previous.content(), new.content())]
    decisions: list[StageDecision] = []
    recomputed: set[Stage] = set()
    for stage in STAGES:
        own = [p for p in changed if _affected(p, STAGE_INPUTS[stage])]
        if stage in PER_ITEM:
            if own:
                decisions.append(StageDecision(stage, "recompute_items", own))
                recomputed.add(stage)
            else:
                # Items are keyed by their own inputs: unchanged items are reused even when the
                # set of items changes upstream.
                ups = [u for u in UPSTREAM[stage] if u in recomputed]
                why = [f"item set may change ({', '.join(ups)})"] if ups else ["unchanged"]
                decisions.append(StageDecision(stage, "reuse_unchanged_items", why))
            continue
        up = [f"upstream {u}" for u in UPSTREAM[stage] if u in recomputed]
        if own or up:
            decisions.append(StageDecision(stage, "recompute", own + up))
            recomputed.add(stage)
        else:
            decisions.append(StageDecision(stage, "reuse", ["unchanged"]))
    return RerunPlan(previous.version, new.version, changed, decisions)


# --- Postgres: stage cache and brief-run provenance ------------------------------------------


@dataclass
class ReuseCounter:
    """Per-stage reuse counts for the run report (R18.4: "the report shows what was recomputed")."""

    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, stage: str, reused: bool) -> None:
        c = self.counts.setdefault(stage, {"reused": 0, "recomputed": 0})
        c["reused" if reused else "recomputed"] += 1

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {k: dict(v) for k, v in sorted(self.counts.items())}


class StageCache:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "UPDATE brief_stage_cache SET hits = hits + 1, last_hit_at = now()"
            " WHERE key = %s RETURNING result",
            (key,),
        ).fetchone()
        return dict(row[0]) if row else None

    def put(
        self,
        key: str,
        *,
        stage: Stage,
        item_ref: str,
        input_hash: str,
        result: dict[str, Any],
        brief: Brief,
        brief_run_id: str | None,
        repo_id: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO brief_stage_cache (key, stage, stage_version, item_ref, repo_id,"
            " input_hash, result, result_hash, first_brief_id, first_brief_version,"
            " first_brief_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (key) DO NOTHING",
            (
                key,
                stage,
                STAGE_VERSIONS[stage],
                item_ref,
                repo_id,
                input_hash,
                Jsonb(result),
                sha256_json(result),
                brief.brief_id,
                brief.version,
                brief_run_id,
            ),
        )

    def get_or_compute(
        self,
        key: str,
        compute: Callable[[], dict[str, Any]],
        *,
        stage: Stage,
        item_ref: str,
        input_hash: str,
        brief: Brief,
        brief_run_id: str | None,
        counter: ReuseCounter,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        hit = self.get(key)
        if hit is not None:
            counter.add(stage, reused=True)
            return hit
        result = compute()
        self.put(
            key,
            stage=stage,
            item_ref=item_ref,
            input_hash=input_hash,
            result=result,
            brief=brief,
            brief_run_id=brief_run_id,
            repo_id=repo_id,
        )
        counter.add(stage, reused=False)
        return result


def data_version(conn: psycopg.Connection[Any]) -> str:
    """A fingerprint of the captured data a run reads (R18.6): counts and latest fetch times of
    the evidence and star-history caches. Unchanged data → the same data version."""
    row = conn.execute(
        "SELECT (SELECT count(*) FROM evidence), (SELECT max(fetched_at) FROM evidence),"
        " (SELECT count(*) FROM star_history_fetch), (SELECT max(fetched_at) FROM"
        " star_history_fetch), (SELECT max(version) FROM schema_migrations)"
    ).fetchone()
    assert row is not None
    return "dv1-" + sha256_json([str(v) for v in row])[:16]


def new_brief_run_id() -> str:
    return "brun_" + secrets.token_hex(10)


@dataclass
class BriefRun:
    id: str
    brief: Brief
    conn: psycopg.Connection[Any]
    counter: ReuseCounter = field(default_factory=ReuseCounter)

    def _update(self, **cols: Any) -> None:
        sets = ", ".join(f"{k} = %s" for k in cols)
        vals = [Jsonb(v) if isinstance(v, dict | list) else v for v in cols.values()]
        self.conn.execute(f"UPDATE brief_runs SET {sets} WHERE id = %s", (*vals, self.id))

    def start(self) -> None:
        self._update(status="running", started_at=utcnow())

    def pause_for_budget(
        self, stop: BudgetStop, checkpoint: dict[str, Any], spend: dict[str, Any]
    ) -> None:
        """R18.5: the run hard-stopped at the budget, leaving a resumable checkpoint."""
        self._update(
            status="paused_budget",
            finished_at=utcnow(),
            stop=stop.to_dict(),
            checkpoint=checkpoint,
            spend=spend,
            reuse=self.counter.to_dict(),
        )

    def finish(self, status: Literal["succeeded", "failed"], spend: dict[str, Any]) -> None:
        self._update(status=status, finished_at=utcnow(), spend=spend, reuse=self.counter.to_dict())


class BriefRuns:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def create(
        self,
        brief: Brief,
        *,
        data_version: str | None,
        estimate: dict[str, Any] | None = None,
        approved_paid: bool = False,
        plan: RerunPlan | None = None,
        code_commit: str | None = None,
        codebook_version: str | None = None,
        outcome_model_version: str | None = None,
        prompt_versions: dict[str, str] | None = None,
        model_versions: dict[str, str] | None = None,
        resumed_from: str | None = None,
        run_id: str | None = None,
    ) -> BriefRun:
        if brief.version is None:
            raise ValueError("only a stored brief version can be run")
        rid = new_brief_run_id()
        self.conn.execute(
            "INSERT INTO brief_runs (id, brief_id, brief_version, brief_hash, run_id, status,"
            " data_version, code_commit, codebook_version, outcome_model_version,"
            " prompt_versions, model_versions, estimate, approved_paid, stage_keys, rerun_plan,"
            " resumed_from) VALUES (%s, %s, %s, %s, %s, 'planned', %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s)",
            (
                rid,
                brief.brief_id,
                brief.version,
                brief.content_hash(),
                run_id,
                data_version,
                code_commit,
                codebook_version,
                outcome_model_version,
                Jsonb(prompt_versions or {}),
                Jsonb(model_versions or {}),
                Jsonb(estimate) if estimate is not None else None,
                approved_paid,
                Jsonb(stage_keys(brief, data_version)),
                Jsonb(plan.to_dict()) if plan is not None else None,
                resumed_from,
            ),
        )
        return BriefRun(rid, brief, self.conn)

    def get(self, run_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM brief_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d.name for d in cur.description or []]
        return dict(zip(cols, row, strict=True))

    def latest(
        self, brief_id: str, *, statuses: tuple[str, ...] | None = None
    ) -> dict[str, Any] | None:
        q = "SELECT id FROM brief_runs WHERE brief_id = %s"
        params: list[Any] = [brief_id]
        if statuses:
            q += " AND status = ANY(%s)"
            params.append(list(statuses))
        row = self.conn.execute(q + " ORDER BY created_at DESC, id LIMIT 1", params).fetchone()
        return self.get(row[0]) if row else None

    def last_run_version(self, brief_id: str) -> int | None:
        """Brief version of the latest run that finished or paused at the budget."""
        last = self.latest(brief_id, statuses=("succeeded", "paused_budget"))
        return int(last["brief_version"]) if last else None

    def summary_by_brief(self) -> dict[str, dict[str, Any]]:
        """Last run per brief for the D7 list (status, version, times, spend)."""
        rows = self.conn.execute(
            "SELECT DISTINCT ON (brief_id) brief_id, id, brief_version, status, created_at,"
            " finished_at, spend, stop FROM brief_runs ORDER BY brief_id, created_at DESC"
        ).fetchall()
        return {
            r[0]: {
                "id": r[1],
                "brief_version": r[2],
                "status": r[3],
                "created_at": r[4],
                "finished_at": r[5],
                "spend": r[6],
                "stop": r[7],
            }
            for r in rows
        }
