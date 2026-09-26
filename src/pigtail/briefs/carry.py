"""Carry a final shortlist forward to a later version of the same brief (PRD R4.7, R4.8;
ADR-079; `pigtail brief shortlist carry-forward`).

A brief edit that changes only the success definition (primary dimension, thresholds, minimums,
fallback steps), the panel sizes and balance rules, the report options or the notes leaves
discovery, the relevance filter and the shortlist review exactly as they were. Running them
again would cost money and a second review for the same result, so the reviewed shortlist can be
copied instead.

**When it is allowed.**
- The source version's shortlist is final.
- The target version is later and has no candidates, no decisions and no shortlist yet.
- The two versions differ only in `ALLOWED` paths: `success.*`, `panel.*`, `report.*`, `notes`
  and store metadata (`METADATA_FIELDS`). The difference is computed generically from the two
  model dumps (`diff.field_changes`), so a new brief field is refused until it is added here on
  purpose. Anything else (`field.*`, `window.*`, `expansion`, `distribution_exemplars`, …) is
  refused with the list of differing fields.

**What is copied** (one transaction, under the brief's run lock):
- every candidate row, with its verdict, reason, distance, rubric version, relevance provenance,
  discovery signals and resolution, plus `carried_from_version`;
- each candidate's **latest** decision, as a new immutable `shortlist_decision` row of the target
  version that keeps the original decision, reason, reviewer role, channel, bulk marker and time,
  and adds the carry-forward's own role, reason and time and the source version;
- the named-project resolutions and confirmations (they live on the candidate rows);
- then the target shortlist is finalized with the same members (`Shortlist.finalize`: final
  mention scope for the target version). Its precision is recomputed from the copied decisions,
  so it carries the source's label (including "not item-reviewed" and "not reviewed") followed
  by "carried from vN".

**Refusal list (CB-13).** Checked again for every candidate: a repo refused since the source
was finalized is not copied (nor its decision) and is counted in the result; a named project's
confirmation that pointed at it goes back to unresolved.

**Run linkage.** A new `brief_runs` row for the target version with status `carried_forward`,
`carried_from` = the source shortlist's run, the source's prompt and model versions and window
end (`checkpoint.run_date`), and discovery, relevance and shortlist marked done (carried). The
runner treats it as complete, so `pigtail run --brief <id>` then runs only the selection on it,
behind the pre-registration gate of the target version (R8.2). README evidence linked to the
source version's runs is linked to it too (retention, R19.9).

No brief text is stored: versions are referenced by number; the rows copied hold the same
project-level data as the source rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from pigtail.briefs.cache import BriefRuns, data_version
from pigtail.briefs.candidates import CandidateStore, parse_named
from pigtail.briefs.diff import field_changes
from pigtail.briefs.model import METADATA_FIELDS, Brief
from pigtail.briefs.shortlist import Reviewer, Shortlist, ShortlistError
from pigtail.privacy.suppression import Suppressions

# Top-level brief fields whose change leaves discovery, relevance and the shortlist untouched.
ALLOWED_SECTIONS = frozenset({"success", "panel", "report"})
ALLOWED_LEAVES = frozenset({"notes"}) | METADATA_FIELDS
CARRIED_STAGES = ("discovery", "relevance", "shortlist")


class CarryError(ShortlistError):
    pass


class CarryRefused(CarryError):
    """The versions differ in fields that affect discovery, relevance or the shortlist."""

    def __init__(self, paths: list[str], source: int, target: int) -> None:
        self.paths = paths
        super().__init__(
            f"v{target} differs from v{source} in fields that affect discovery, relevance or the "
            f"shortlist: {', '.join(paths)}. Only success.*, panel.*, report.*, notes and store "
            "metadata may differ; run the brief instead (pigtail run --brief ...)"
        )


def utcnow() -> datetime:
    return datetime.now(UTC)


def allowed(path: str) -> bool:
    top = path.split(".", 1)[0]
    return top in ALLOWED_SECTIONS or path in ALLOWED_LEAVES or top in ALLOWED_LEAVES


def blocking_changes(source: Brief, target: Brief) -> list[str]:
    """Dotted paths that differ between the two versions and are not on the allow-list."""
    a = source.model_dump(mode="json")
    b = target.model_dump(mode="json")
    return sorted({c.path for c in field_changes(a, b) if not allowed(c.path)})


@dataclass
class CarryResult:
    brief_id: str
    from_version: int
    to_version: int
    brief_run_id: str
    candidates: int
    decisions: int
    shortlisted: int
    dropped_refused: int
    unconfirmed_named: int
    precision: dict[str, Any]
    changed_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _count(conn: psycopg.Connection[Any], sql: str, *args: Any) -> int:
    row = conn.execute(sql, args).fetchone()
    return int(row[0]) if row else 0


def _source_run(conn: psycopg.Connection[Any], source: Brief, run_id: str | None) -> dict[str, Any]:
    runs = BriefRuns(conn)
    if run_id is not None and (got := runs.get(run_id)) is not None:
        return got
    row = conn.execute(
        "SELECT id FROM brief_runs WHERE brief_id = %s AND brief_version = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (source.brief_id, source.version),
    ).fetchone()
    return (runs.get(row[0]) if row else None) or {}


def carry_forward(
    conn: psycopg.Connection[Any],
    source: Brief,
    target: Brief,
    *,
    reason: str,
    reviewer: Reviewer = "user",
    suppressions: Suppressions | None = None,
    now: datetime | None = None,
) -> CarryResult:
    """Copy the final shortlist of `source` to `target` (module docstring). Raises
    `CarryRefused` for a disallowed difference, `CarryError` for any other refusal; nothing is
    written then."""
    from pigtail.briefs.runner import _lock, _unlock

    if source.version is None or target.version is None:
        raise CarryError("carry forward between stored brief versions")
    if source.brief_id != target.brief_id:
        raise CarryError("a shortlist is carried forward within one brief only")
    if target.version <= source.version:
        raise CarryError(
            f"carry forward to a later version (got v{source.version} -> v{target.version})"
        )
    try:
        why = Shortlist._reason(reason)
    except ShortlistError as e:
        raise CarryError("a carry-forward needs a reason (R4.7)") from e
    blocked = blocking_changes(source, target)
    if blocked:
        raise CarryRefused(blocked, source.version, target.version)
    changed = sorted(
        {
            c.path
            for c in field_changes(source.model_dump(mode="json"), target.model_dump(mode="json"))
            if c.path.split(".", 1)[0] not in METADATA_FIELDS
        }
    )
    if not _lock(conn, source.brief_id):
        raise CarryError(f"a run of {source.brief_id} is in progress; try again when it ends")
    try:
        return _carry(conn, source, target, why, reviewer, suppressions, now or utcnow(), changed)
    finally:
        _unlock(conn, source.brief_id)


def _carry(
    conn: psycopg.Connection[Any],
    source: Brief,
    target: Brief,
    why: str,
    reviewer: Reviewer,
    suppressions: Suppressions | None,
    at: datetime,
    changed: list[str],
) -> CarryResult:
    from pigtail.capture.runs import git_commit

    assert source.version is not None and target.version is not None
    bid, sv, tv = source.brief_id, source.version, target.version
    src = Shortlist(conn, source, suppressions=suppressions)
    st = src.status()
    if st is None or st["status"] != "final":
        raise CarryError(
            f"the shortlist of {bid} v{sv} is not final; only a final shortlist is carried forward"
        )
    tgt = Shortlist(conn, target, suppressions=suppressions or src.suppressions)
    n_cand = _count(
        conn, "SELECT count(*) FROM brief_candidate WHERE brief_id = %s AND brief_version = %s",
        bid, tv,
    )  # fmt: skip
    n_dec = _count(
        conn, "SELECT count(*) FROM shortlist_decision WHERE brief_id = %s AND brief_version = %s",
        bid, tv,
    )  # fmt: skip
    if n_cand or n_dec or tgt.status() is not None:
        raise CarryError(
            f"{bid} v{tv} already has a shortlist ({n_cand} candidate(s), {n_dec} decision(s)); "
            "a shortlist is carried forward only to a version with none"
        )

    # CB-13 again: the refusal list may have grown since the source was finalized
    keep: list[str] = []
    dropped: set[str] = set()
    named_fix: dict[str, list[dict[str, Any]]] = {}
    for c in src.candidates.all():
        if c.repo_full_name is not None and src.refused(c.repo_full_name, c.repo_host_id):
            dropped.add(c.repo_full_name)
            continue
        keep.append(c.ref)
        if c.matches:
            ok = [m for m in c.matches if not src.refused(m.get("full_name"))]
            if len(ok) != len(c.matches):
                named_fix[c.ref] = ok
    latest = src.latest()
    src_run = _source_run(conn, source, st["brief_run_id"])

    with conn.transaction():
        # the target version's run: no work of its own; linked to the source's run
        run = BriefRuns(conn).create(
            target,
            data_version=data_version(conn),
            code_commit=git_commit(),
            prompt_versions=dict(src_run.get("prompt_versions") or {}),
            model_versions=dict(src_run.get("model_versions") or {}),
        )
        src_stages = dict(src_run.get("stages") or {})
        stages = {
            name: {
                **{k: v for k, v in dict(src_stages.get(name) or {}).items() if k != "status"},
                "status": "done",
                "carried_from_version": sv,
                "carried_from_run": src_run.get("id"),
            }
            for name in CARRIED_STAGES
        }
        checkpoint: dict[str, Any] = {"carried_from": {"version": sv, "run": src_run.get("id")}}
        run_date = (src_run.get("checkpoint") or {}).get("run_date")
        if run_date:  # same window end as the source: the same discovery window (R4.5)
            checkpoint["run_date"] = run_date
        conn.execute(
            "UPDATE brief_runs SET status = 'carried_forward', carried_from = %s, stages = %s,"
            " checkpoint = %s, started_at = %s, finished_at = %s WHERE id = %s",
            (src_run.get("id"), Jsonb(stages), Jsonb(checkpoint), at, at, run.id),
        )
        # candidates, with verdicts, reasons, distances, provenance and resolutions
        conn.execute(
            "INSERT INTO brief_candidate (brief_id, brief_version, candidate_ref, repo_full_name,"
            " repo_host_id, repo_id, panel, named_index, resolution, resolution_rule, matches,"
            " sources, metadata, first_seen_at, last_seen_at, first_brief_run_id, verdict,"
            " reason, distance, model_panel, rubric_version, relevance, judged_at,"
            " judged_brief_run_id, carried_from_version)"
            " SELECT brief_id, %s, candidate_ref, repo_full_name, repo_host_id, repo_id, panel,"
            " named_index, resolution, resolution_rule, matches, sources, metadata,"
            " first_seen_at, last_seen_at, first_brief_run_id, verdict, reason, distance,"
            " model_panel, rubric_version, relevance, judged_at, judged_brief_run_id, %s"
            " FROM brief_candidate WHERE brief_id = %s AND brief_version = %s"
            " AND candidate_ref = ANY(%s)",
            (tv, sv, bid, sv, keep),
        )
        store = CandidateStore(conn, bid, tv)
        for ref, ok in named_fix.items():
            conn.execute(
                "UPDATE brief_candidate SET matches = %s"
                " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
                (Jsonb(ok), bid, tv, ref),
            )
        # a named project confirmed as a repo that is now refused goes back to unresolved
        unconfirmed = 0
        for c in store.all():
            confirmed = c.metadata.get("confirmed_repo")
            if parse_named(c.ref) and confirmed in dropped:
                store.set_resolution(c.ref, "unresolved", "confirmation_refused", {})
                conn.execute(
                    "UPDATE brief_candidate SET metadata = metadata - 'confirmed_repo'"
                    " WHERE brief_id = %s AND brief_version = %s AND candidate_ref = %s",
                    (bid, tv, c.ref),
                )
                unconfirmed += 1
        # each candidate's latest decision, as a new immutable row of the target version
        n_decisions = 0
        with conn.cursor() as cur:
            for ref in keep:
                d = latest.get(ref)
                if d is None:
                    continue
                cand = src.candidates.get(ref)
                cur.execute(
                    "INSERT INTO shortlist_decision (brief_id, brief_version, brief_run_id,"
                    " candidate_ref, candidate_repo_id, decision, reason, reviewer_role, via,"
                    " decided_at, bulk_id, bulk_verdict, carried_from_version, carried_role,"
                    " carried_reason, carried_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s, %s, %s, %s, %s)",
                    (
                        bid,
                        tv,
                        run.id,
                        ref,
                        cand.repo_id if cand else None,
                        d.decision,
                        d.reason,
                        d.reviewer_role,
                        d.via,
                        d.decided_at,
                        d.bulk_id,
                        d.bulk_verdict,
                        sv,
                        reviewer,
                        why,
                        at,
                    ),
                )
                n_decisions += 1
        conn.execute(
            "INSERT INTO brief_shortlist (brief_id, brief_version, status, brief_run_id,"
            " carried_from_version, carried_reason, created_at)"
            " VALUES (%s, %s, 'in_review', %s, %s, %s, %s)",
            (bid, tv, run.id, sv, why, at),
        )
        # README evidence the source version's runs used backs this version too (R19.9)
        conn.execute(
            "INSERT INTO brief_evidence (brief_run_id, evidence_id)"
            " SELECT DISTINCT %s, be.evidence_id FROM brief_evidence be"
            " JOIN brief_runs r ON r.id = be.brief_run_id"
            " WHERE r.brief_id = %s AND r.brief_version = %s ON CONFLICT DO NOTHING",
            (run.id, bid, sv),
        )
        tgt.sync_scope()
        res = tgt.finalize(reviewer=reviewer, via="cli", now=at)
    return CarryResult(
        brief_id=bid,
        from_version=sv,
        to_version=tv,
        brief_run_id=run.id,
        candidates=len(keep),
        decisions=n_decisions,
        shortlisted=int(res["shortlisted"]),
        dropped_refused=len(dropped),
        unconfirmed_named=unconfirmed,
        precision=res["precision"],
        changed_fields=changed,
    )
