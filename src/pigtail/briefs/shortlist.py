"""Shortlist review of a brief version (PRD R4.7, R4.11; D7 `/briefs/:id/shortlist`; ADR-062,
ADR-072.8).

After discovery and the relevance filter, the shortlist is **in review**: the user accepts or
rejects candidates (one at a time or in bulk, always with a reason), adds candidates the filter
missed (by GitHub URL), and confirms which repo an unresolved reference case or distribution
exemplar stands for. Every decision is a new, immutable `shortlist_decision` row with the brief
version, the brief run, the reviewer's role (`user`, `owner`, `verifier`), where it was made
(`cli` or `ui`) and the time; the latest decision per candidate counts.

What is on the shortlist:
- a candidate whose latest decision is `accept` or `add`;
- a named reference case or distribution exemplar resolved to a repo (always studied, R4.11),
  unless the user rejected it;
- nothing else. Undecided model-`relevant` candidates are *proposed* (shown as such), and
  finalizing is refused while any `relevant` or `uncertain` candidate is undecided (bulk actions
  settle them), so the precision below measures an actual review.

**Precision** (R4.7): among field-panel candidates the filter judged `relevant` and the reviewer
decided on, the share accepted; target ≥ 80 %. Named projects are left out (they are on the
shortlist by rule, not by the filter). The label says who checked: `owner-checked`,
`verifier-checked, not owner-checked` (ADR-072.8) or `user-checked`; when every such decision
came from bulk actions on the filter's own `relevant` verdict (`accept --verdict relevant`,
logged with a `bulk_id`, migration 0023), nobody looked at the items and the label is
`not item-reviewed` (BACKLOG M22-P). A precision below target is shown, never hidden.

**Refusal list** (CB-13): a refused repo is never a candidate, on the shortlist or in the mention
scope. A repo refused by GitHub id only is recognised by name through any id pigtail already
holds for it (`repos`, other candidate rows); one added by URL whose id is still unknown is
checked again when its metadata arrives (`outcomes.fetch_outcome_data`), before any fetch, and
`forget` removes it from the brief version.

**Carry-forward** (ADR-079, `pigtail.briefs.carry`): a final shortlist can be copied to a later
version of the same brief whose edit changes only `success.*`, `panel.*`, `report.*`, `notes` or
store metadata. The copied decisions keep their original role, channel, reason, bulk marker and
time, so the precision label is the source's, followed by "carried from vN".

**Mention scope** (Directive §8.3): while in review, the proposed and accepted repos are
`in_review` entries of `brief_shortlist_entry`; finalizing writes the final set as `final` and
marks everything else of this version `removed` (`pigtail.capture.scope.set_entries`). Evidence
used by the brief's runs is linked for snapshot retention by the stages
(`pigtail.privacy.snapshot_retention.link`).
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from pigtail.briefs.candidates import (
    Candidate,
    CandidateStore,
    Panel,
    gh_ref,
    normalize_ref,
    parse_named,
)
from pigtail.briefs.model import Brief
from pigtail.privacy.suppression import Suppressions

Decision = Literal["accept", "reject", "add"]
Reviewer = Literal["user", "owner", "verifier"]
Via = Literal["cli", "ui"]
PRECISION_TARGET = 0.8
REASON_MAX = 1000


class ShortlistError(ValueError):
    pass


class ShortlistFinal(ShortlistError):
    """The shortlist is final: decisions are refused (a new brief version starts a new one)."""


class NoShortlist(ShortlistError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class LatestDecision:
    decision: Decision
    reason: str
    reviewer_role: Reviewer
    via: str | None
    decided_at: datetime
    bulk_id: str | None = None
    bulk_verdict: str | None = None


class Shortlist:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        brief: Brief,
        *,
        suppressions: Suppressions | None = None,
    ) -> None:
        if brief.version is None:
            raise ValueError("a shortlist belongs to a stored brief version")
        self.conn = conn
        self.brief = brief
        self.brief_id = brief.brief_id
        self.version: int = brief.version
        self.candidates = CandidateStore(conn, brief.brief_id, brief.version)
        self._sup = suppressions

    # --- refusal list (CB-13) ------------------------------------------------------------------
    @property
    def suppressions(self) -> Suppressions:
        """The refusal list, loaded on first use (fails closed without the opt-out key when
        repo-name entries exist)."""
        if self._sup is None:
            from pigtail.capture.db import CaptureDB
            from pigtail.privacy import suppression

            self._sup = suppression.load(CaptureDB(self.conn))
        return self._sup

    def refused(self, full_name: str | None, host_id: int | None = None) -> bool:
        """CB-13: a repo on the refusal list (by id or by name) is never on a shortlist. Without
        a GitHub id, any id pigtail already holds for the name is checked (M22 verifier round
        2: a repo refused by id only, added by URL before its metadata is known)."""
        sup = self.suppressions
        if not sup:
            return False
        ids = {host_id} if host_id is not None else self.known_host_ids(full_name)
        if any(f"github:{i}" in sup.repos for i in ids):
            return True
        return bool(sup.name_suppressed(full_name))

    def known_host_ids(self, full_name: str | None) -> set[int]:
        """GitHub ids stored for `owner/name` (the `repos` table, any brief's candidates)."""
        if not full_name or not self.suppressions.repos:
            return set()
        rows = self.conn.execute(
            "SELECT host_id FROM repos WHERE host = 'github' AND lower(full_name) = %s"
            " UNION SELECT repo_host_id FROM brief_candidate WHERE repo_full_name = %s"
            " AND repo_host_id IS NOT NULL",
            (full_name.lower(), full_name.lower()),
        ).fetchall()
        return {int(r[0]) for r in rows}

    def forget(self, ref: str) -> None:
        """Remove a refused repo from this brief version (CB-13): its candidate row and its
        mention-scope entries. Its logged decisions stay (they carry no metadata)."""
        name = ref[3:] if ref.startswith("gh:") else None
        with self.conn.transaction():
            self.conn.execute(
                "DELETE FROM brief_candidate WHERE brief_id = %s AND brief_version = %s"
                " AND candidate_ref = %s",
                (self.brief_id, self.version, ref),
            )
            if name is not None:
                self.conn.execute(
                    "DELETE FROM brief_shortlist_entry WHERE brief_id = %s AND brief_version = %s"
                    " AND repo_full_name = %s",
                    (self.brief_id, self.version, name),
                )

    # --- state ---------------------------------------------------------------------------------
    def status(self) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT status, created_at, brief_run_id, finalized_at, finalized_role, finalized_via,"
            " precision, carried_from_version, carried_reason FROM brief_shortlist"
            " WHERE brief_id = %s AND brief_version = %s",
            (self.brief_id, self.version),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d.name for d in cur.description or []]
        return dict(zip(cols, row, strict=True))

    def ensure(self, brief_run_id: str | None) -> dict[str, Any]:
        """Open the review (idempotent). A final shortlist that gets new candidates to review
        (an incremental run) goes back to `in_review`; its decisions are kept."""
        self.conn.execute(
            "INSERT INTO brief_shortlist (brief_id, brief_version, status, brief_run_id)"
            " VALUES (%s, %s, 'in_review', %s) ON CONFLICT (brief_id, brief_version) DO NOTHING",
            (self.brief_id, self.version, brief_run_id),
        )
        st = self.status()
        assert st is not None
        if st["status"] == "final" and self.undecided():
            self.conn.execute(
                "UPDATE brief_shortlist SET status = 'in_review', finalized_at = NULL,"
                " finalized_role = NULL, finalized_via = NULL, brief_run_id = %s"
                " WHERE brief_id = %s AND brief_version = %s",
                (brief_run_id, self.brief_id, self.version),
            )
            st = self.status()
            assert st is not None
        return st

    def latest(self) -> dict[str, LatestDecision]:
        rows = self.conn.execute(
            "SELECT DISTINCT ON (candidate_ref) candidate_ref, decision, reason, reviewer_role,"
            " via, decided_at, bulk_id, bulk_verdict FROM shortlist_decision"
            " WHERE brief_id = %s AND brief_version = %s"
            " ORDER BY candidate_ref, decided_at DESC, id DESC",
            (self.brief_id, self.version),
        ).fetchall()
        return {r[0]: LatestDecision(r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows}

    @staticmethod
    def included(c: Candidate, d: LatestDecision | None) -> bool | None:
        """True on the shortlist, False off it, None undecided (see the module docstring)."""
        if d is not None:
            return d.decision in ("accept", "add")
        if c.is_named and c.repo_full_name is not None:
            return True  # R4.11: named projects stay, whatever the filter says
        return None

    def undecided(self) -> list[Candidate]:
        dec = self.latest()
        return [
            c
            for c in self.candidates.all()
            if c.verdict in ("relevant", "uncertain")
            and not c.is_named
            and not self.refused(c.repo_full_name, c.repo_host_id)
            and self.included(c, dec.get(c.ref)) is None
        ]

    # --- precision (R4.7) ----------------------------------------------------------------------
    def precision(self) -> dict[str, Any]:
        dec = self.latest()
        relevant = [c for c in self.candidates.all() if c.verdict == "relevant" and not c.is_named]
        decided = [c for c in relevant if c.ref in dec]
        kept = [c for c in decided if dec[c.ref].decision in ("accept", "add")]
        roles = {dec[c.ref].reviewer_role for c in decided}
        # decisions made by a bulk action on the filter's own verdict: nobody looked at the item
        on_verdict = [c for c in decided if dec[c.ref].bulk_verdict == "relevant"]
        if not roles:
            label = "not reviewed"
        elif len(on_verdict) == len(decided):
            label = "not item-reviewed (bulk action on the filter's verdict)"
        elif roles == {"owner"}:
            label = "owner-checked"
        elif "verifier" in roles and "owner" not in roles:
            label = "verifier-checked, not owner-checked"
        elif roles == {"user"}:
            label = "user-checked"
        else:
            label = "mixed reviewers: " + ", ".join(sorted(roles))
        if on_verdict and len(on_verdict) < len(decided):
            label += f"; {len(on_verdict)} of {len(decided)} by bulk action, not item-reviewed"
        st = self.status()
        carried = st["carried_from_version"] if st else None
        if carried is not None:  # the source version's decisions, copied (ADR-079)
            label += f"; carried from v{carried}"
        value = len(kept) / len(decided) if decided else None
        return {
            "value": None if value is None else round(value, 4),
            "kept": len(kept),
            "decided": len(decided),
            "model_relevant": len(relevant),
            "undecided": len(relevant) - len(decided),
            "target": PRECISION_TARGET,
            "meets_target": None if value is None else value >= PRECISION_TARGET,
            "label": label,
            "bulk_on_filter_verdict": len(on_verdict),
            "item_reviewed": len(decided) - len(on_verdict),
            "rubric_versions": sorted({c.rubric_version for c in relevant if c.rubric_version}),
            "carried_from_version": carried,
            "definition": (
                "accepted among field-panel candidates the filter judged relevant and the "
                "reviewer decided on (R4.7); named projects excluded; decisions from a bulk "
                "action on the filter's relevant verdict are not item reviews"
            ),
        }

    # --- decisions -----------------------------------------------------------------------------
    def _require_open(self) -> None:
        st = self.status()
        if st is None:
            raise NoShortlist(
                f"no shortlist for {self.brief_id} v{self.version} yet: run the brief first "
                f"(pigtail run --brief {self.brief_id})"
            )
        if st["status"] == "final":
            raise ShortlistFinal(
                f"the shortlist of {self.brief_id} v{self.version} is final; decisions are "
                "refused (edit the brief to start a new version's review)"
            )

    @staticmethod
    def _reason(reason: str) -> str:
        r = " ".join((reason or "").split())
        if not r:
            raise ShortlistError("every decision needs a reason (R4.7)")
        return r[:REASON_MAX]

    def _insert(
        self,
        ref: str,
        repo_id: str | None,
        decision: Decision,
        reason: str,
        reviewer: Reviewer,
        via: Via,
        brief_run_id: str | None,
        at: datetime,
        bulk: tuple[str, str | None] | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO shortlist_decision (brief_id, brief_version, brief_run_id, candidate_ref,"
            " candidate_repo_id, decision, reason, reviewer_role, via, decided_at, bulk_id,"
            " bulk_verdict) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                self.brief_id,
                self.version,
                brief_run_id,
                ref,
                repo_id,
                decision,
                reason,
                reviewer,
                via,
                at,
                bulk[0] if bulk else None,
                bulk[1] if bulk else None,
            ),
        )

    def _run_id(self) -> str | None:
        st = self.status()
        return st["brief_run_id"] if st else None

    def decide(
        self,
        refs: Iterable[str],
        decision: Literal["accept", "reject"],
        reason: str,
        *,
        reviewer: Reviewer = "user",
        via: Via = "cli",
        now: datetime | None = None,
        bulk: tuple[str, str | None] | None = None,
    ) -> int:
        """Accept or reject candidates (bulk allowed); one logged row per candidate. `bulk` is
        (bulk id, verdict filter) when `decide_where` settles candidates by filter."""
        self._require_open()
        why = self._reason(reason)
        at = now or utcnow()
        norm = list(dict.fromkeys(normalize_ref(r) for r in refs))
        if not norm:
            raise ShortlistError("no candidates given")
        cands = {c.ref: c for c in self.candidates.all()}
        unknown = [r for r in norm if r not in cands]
        if unknown:
            raise ShortlistError(
                f"{len(unknown)} candidate(s) not on this brief version's list: "
                + ", ".join(unknown[:5])
                + " (use `add` for a new repo)"
            )
        unresolved = [r for r in norm if cands[r].repo_full_name is None]
        if unresolved:
            raise ShortlistError(
                "an unresolved named project is confirmed by adding its repo "
                "(add <url> --resolves " + unresolved[0] + "), not accepted or rejected"
            )
        run_id = self._run_id()
        for r in norm:
            self._insert(r, cands[r].repo_id, decision, why, reviewer, via, run_id, at, bulk)
        self.sync_scope()
        return len(norm)

    def decide_where(
        self,
        decision: Literal["accept", "reject"],
        reason: str,
        *,
        verdict: str | None = None,
        panel: str | None = None,
        distance: int | None = None,
        undecided_only: bool = True,
        reviewer: Reviewer = "user",
        via: Via = "cli",
    ) -> int:
        """Bulk action on the candidates matching the filters (undecided ones by default)."""
        dec = self.latest()
        refs = [
            c.ref
            for c in self.candidates.all()
            if c.repo_full_name is not None
            and (
                verdict is None or c.verdict == verdict or (verdict == "none" and c.verdict is None)
            )
            and (panel is None or c.panel == panel)
            and (distance is None or c.distance == distance)
            and (not undecided_only or c.ref not in dec)
        ]
        if not refs:
            return 0
        bulk = ("bulk_" + secrets.token_hex(8), verdict)  # one id per bulk action (M22-P)
        return self.decide(refs, decision, reason, reviewer=reviewer, via=via, bulk=bulk)

    def add(
        self,
        url_or_name: str,
        reason: str,
        *,
        panel: Panel = "field",
        resolves: str | None = None,
        reviewer: Reviewer = "user",
        via: Via = "cli",
        now: datetime | None = None,
    ) -> str:
        """Add a repo by URL (or `owner/name`), logged as `add`. With `resolves`, the repo is the
        user's confirmation of an unresolved named project (ADR-054.3): it takes that project's
        panel and position, and the named row is marked confirmed. Metadata of an added repo is
        filled by the next run (`pigtail run --brief <id> --incremental`); no network here."""
        self._require_open()
        why = self._reason(reason)
        at = now or utcnow()
        ref = normalize_ref(url_or_name)
        if not ref.startswith("gh:"):
            raise ShortlistError("add takes a GitHub repository URL or owner/name")
        name = ref[3:]
        known = self.candidates.get(ref)
        if self.refused(name, known.repo_host_id if known else None):
            raise ShortlistError("this repository is on the refusal list (CB-13)")
        named_index: int | None = None
        rule = "user_added"
        if resolves is not None:
            pn = parse_named(resolves)
            target = self.candidates.get(resolves) if pn else None
            if pn is None or target is None:
                raise ShortlistError(f"{resolves!r} is not a named project of this brief version")
            panel, named_index = pn
            rule = "user_confirmed"
        existing = self.candidates.get(ref)
        self.candidates.upsert(
            Candidate(
                ref=gh_ref(name),
                repo_full_name=name,
                panel=panel,
                named_index=named_index,
                resolution="confirmed" if resolves else "resolved",
                resolution_rule=rule,
                sources=[{"source": "user_add", "rule": rule}],
                metadata={"added_by_review": True} if existing is None else {},
            ),
            brief_run_id=self._run_id(),
            now=at,
        )
        if resolves is not None:
            self.candidates.set_resolution(
                resolves, "confirmed", "user_confirmed", {"confirmed_repo": name}
            )
        repo_id = existing.repo_id if existing else None
        self._insert(ref, repo_id, "add", why, reviewer, via, self._run_id(), at)
        self.sync_scope()
        return ref

    # --- scope and finalization ----------------------------------------------------------------
    def members(self) -> tuple[list[str], list[str]]:
        """(on the shortlist, proposed) repo names: decided in, and undecided relevant ones."""
        dec = self.latest()
        on: list[str] = []
        proposed: list[str] = []
        for c in self.candidates.all():
            if c.repo_full_name is None or self.refused(c.repo_full_name, c.repo_host_id):
                continue
            inc = self.included(c, dec.get(c.ref))
            if inc is True:
                on.append(c.repo_full_name)
            elif inc is None and c.verdict == "relevant":
                proposed.append(c.repo_full_name)
        return on, proposed

    def sync_scope(self) -> None:
        """Mention scope while in review: accepted and proposed repos are `in_review`, the rest
        of this version's entries `removed` (Directive §8.3)."""
        from pigtail.capture.db import CaptureDB
        from pigtail.capture.scope import set_entries

        db = CaptureDB(self.conn)
        on, proposed = self.members()
        keep = set(on) | set(proposed)
        set_entries(db, self.brief_id, self.version, keep, "in_review")
        gone = [
            r[0]
            for r in self.conn.execute(
                "SELECT repo_full_name FROM brief_shortlist_entry WHERE brief_id = %s AND"
                " brief_version = %s AND status <> 'removed'",
                (self.brief_id, self.version),
            ).fetchall()
            if r[0] not in keep
        ]
        if gone:
            set_entries(db, self.brief_id, self.version, gone, "removed")

    def finalize(
        self, *, reviewer: Reviewer = "user", via: Via = "cli", now: datetime | None = None
    ) -> dict[str, Any]:
        """Mark the shortlist final (R4.7). Refused while relevant or uncertain candidates are
        undecided. Writes the final mention scope; unresolved named projects don't block
        (ADR-062) and are reported."""
        from pigtail.capture.db import CaptureDB
        from pigtail.capture.scope import set_entries

        self._require_open()
        open_ = self.undecided()
        if open_:
            raise ShortlistError(
                f"{len(open_)} relevant or uncertain candidate(s) are undecided; accept or "
                "reject them first (bulk: accept --verdict relevant / reject --verdict uncertain)"
            )
        at = now or utcnow()
        for c in self.candidates.all():  # CB-13 again: the refusal list may have grown
            if c.repo_full_name is not None and self.refused(c.repo_full_name, c.repo_host_id):
                self.forget(c.ref)
        on, _ = self.members()
        prec = self.precision()
        unresolved = [
            c.ref
            for c in self.candidates.all()
            if c.repo_full_name is None and c.resolution == "unresolved"
        ]
        prec_rec = {**prec, "unresolved_named": unresolved, "shortlisted": len(on)}
        # one transaction: the shortlist is never final without its scope written
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE brief_shortlist SET status = 'final', finalized_at = %s,"
                " finalized_role = %s, finalized_via = %s, precision = %s"
                " WHERE brief_id = %s AND brief_version = %s",
                (at, reviewer, via, Jsonb(prec_rec), self.brief_id, self.version),
            )
            db = CaptureDB(self.conn)
            set_entries(db, self.brief_id, self.version, on, "final")
            others = [
                r[0]
                for r in self.conn.execute(
                    "SELECT repo_full_name FROM brief_shortlist_entry WHERE brief_id = %s AND"
                    " brief_version = %s AND status <> 'removed'",
                    (self.brief_id, self.version),
                ).fetchall()
                if r[0] not in set(on)
            ]
            if others:
                set_entries(db, self.brief_id, self.version, others, "removed")
            # the run whose shortlist this is has done its M22 work
            self.conn.execute(
                "UPDATE brief_runs SET status = 'succeeded',"
                " finished_at = COALESCE(finished_at, %s)"
                " WHERE brief_id = %s AND brief_version = %s AND status = 'awaiting_review'",
                (at, self.brief_id, self.version),
            )
        return {
            "status": "final",
            "finalized_at": at,
            "shortlisted": len(on),
            "unresolved_named": unresolved,
            "precision": prec,
        }

    # --- view (CLI show, D7 page) --------------------------------------------------------------
    def view(
        self,
        *,
        verdict: str | None = None,
        panel: str | None = None,
        distance: int | None = None,
    ) -> dict[str, Any]:
        st = self.status()
        dec = self.latest()
        rows: list[dict[str, Any]] = []
        confirm: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        named_lists: dict[str, Sequence[Any]] = {
            "reference": self.brief.field.reference_cases,
            "exemplar": self.brief.distribution_exemplars.projects,
        }
        for c in self.candidates.all():
            if c.repo_full_name is not None and self.refused(c.repo_full_name, c.repo_host_id):
                continue
            if c.matches:
                c.matches = [m for m in c.matches if not self.refused(m.get("full_name"))]
            d = dec.get(c.ref)
            key = c.verdict or "not_judged"
            counts[key] = counts.get(key, 0) + 1
            row = c.to_dict()
            row["decision"] = (
                None
                if d is None
                else {
                    "decision": d.decision,
                    "reason": d.reason,
                    "reviewer_role": d.reviewer_role,
                    "via": d.via,
                    "decided_at": d.decided_at,
                }
            )
            inc = self.included(c, d)
            row["on_shortlist"] = inc
            row["proposed"] = inc is None and c.verdict == "relevant"
            if c.named_index is not None and c.panel in named_lists:
                lst = named_lists[c.panel]
                row["named_as"] = lst[c.named_index].name if c.named_index < len(lst) else None
            if c.repo_full_name is None:
                if c.resolution == "unresolved":
                    confirm.append(row)
                continue
            if verdict is not None and (c.verdict or "not_judged") != verdict:
                continue
            if panel is not None and c.panel != panel:
                continue
            if distance is not None and c.distance != distance:
                continue
            rows.append(row)
        on, proposed = self.members()
        return {
            "brief_id": self.brief_id,
            "brief_version": self.version,
            "status": st["status"] if st else None,
            "shortlist": st,
            "counts": {
                "candidates": sum(counts.values()),
                "by_verdict": counts,
                "on_shortlist": len(on),
                "proposed": len(proposed),
                "undecided": len(self.undecided()),
            },
            "precision": self.precision(),
            "candidates": rows,
            "reference_cases_to_confirm": confirm,
            "brief_warnings": self.brief.warnings(),
            "defaulted_fields": self.brief.defaulted_fields(),
            "filters": {"verdict": verdict, "panel": panel, "distance": distance},
        }


def resolve_refs(values: Sequence[str]) -> list[str]:
    return [normalize_ref(v) for v in values]
