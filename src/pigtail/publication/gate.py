"""The publication gate: Research Bundle -> public projection + audit + manifest (PRD §5-§13).

The input Bundle is never modified. Every removal, redaction, relabel and truncation is a Finding.
Anything that needs a human decision is left out of the projection until a decision is recorded
(fail closed), so a non-PASS preview never contains unresolved content.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from markupsafe import escape

from pigtail.bundle.validator import validate_data
from pigtail.domain.time import iso, parse_dt
from pigtail.policies.loader import load_policies
from pigtail.policies.models import SourcePolicy
from pigtail.publication import (
    INDEPENDENCE_NOTICE,
    MACHINE_GENERATED_NOTICE,
    POLICY_VERSION,
    PROJECTION_VERSION,
    PUBLIC_EXCERPT_MAX_WORDS,
    SANITIZER_VERSION,
    THIRD_PARTY_NOTICE,
    scanners,
)
from pigtail.publication.identities import Redactor, neutral_source_label
from pigtail.publication.models import (
    Action,
    AuditRef,
    AuditSummary,
    Decision,
    Finding,
    PublicationAudit,
    PublicReportBundle,
    ReviewEntry,
    ReviewFile,
    Severity,
    Status,
    StepResult,
)

REF_TYPE = {
    "target": "target",
    "people": "person",
    "repositories": "repository",
    "sources": "source",
    "source_fetches": "source_fetch",
    "claims": "claim",
    "evidence_links": "evidence_link",
    "events": "event",
    "launch_episodes": "launch_episode",
    "growth_episodes": "growth_episode",
    "metric_snapshots": "metric_snapshot",
    "company_stages": "company_stage",
    "strategy_phases": "strategy_phase",
    "surfaces": "surface",
    "surface_presences": "surface_presence",
    "tactics": "tactic",
    "tactic_occurrences": "tactic_occurrence",
    "growth_engines": "growth_engine",
    "growth_engine_occurrences": "growth_engine_occurrence",
    "outcomes": "outcome",
    "prerequisites": "prerequisite",
    "constraints": "constraint",
    "conflicts": "conflict",
    "gaps": "gap",
    "narrative": "narrative",
}
COLL_FOR_REF = {v: k for k, v in REF_TYPE.items()}

# Pigtail-written text per collection. The first field is the one `public_text` replaces by default.
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "events": ("summary", "title"),
    "launch_episodes": ("summary", "title"),
    "growth_episodes": ("summary", "title"),
    "metric_snapshots": ("label", "value_text"),
    "company_stages": ("summary", "label"),
    "strategy_phases": ("summary", "label"),
    "tactics": ("description", "name", "mechanism"),
    "tactic_occurrences": ("implementation",),
    "growth_engines": ("description", "name", "mechanism"),
    "growth_engine_occurrences": ("strength",),
    "outcomes": ("summary",),
    "prerequisites": ("description", "name"),
    "constraints": ("description", "name"),
    "conflicts": ("summary",),
    "gaps": ("summary",),
}
# Collections whose objects rest on claims (dependency pruning, PUB-017), in cascade order.
CLAIM_OBJECTS = (
    "events",
    "launch_episodes",
    "growth_episodes",
    "metric_snapshots",
    "company_stages",
    "strategy_phases",
    "surface_presences",
    "tactics",
    "tactic_occurrences",
    "growth_engines",
    "growth_engine_occurrences",
    "outcomes",
    "prerequisites",
    "constraints",
)
NARRATIVE_KEYS = ("thirty_second", "origin", "first_users", "flywheel", "did_differently")
DISPLAY_ORDER = {"link_only": 0, "paraphrase_and_link": 1, "paraphrase_link_excerpt": 2}
SUCCESS = "success"
NEUTRAL_GAP = "Public evidence was insufficient to verify this detail."
# Pages written by the company/project itself (or its founders, in interviews) count as first-party.
FIRST_PARTY_TYPES = {"first_party", "founder_interview", "readme", "release_list", "repository_api"}
# Sites whose company figures are estimates or modelled data, not company reports (PUB-010).
ESTIMATE_HOSTS = (
    "getlatka.com",
    "growjo.com",
    "owler.com",
    "similarweb.com",
    "semrush.com",
    "zoominfo.com",
    "craft.co",
    "tracxn.com",
    "cbinsights.com",
    "crunchbase.com",
    "pitchbook.com",
    "builtwith.com",
)
METADATA_ONLY_NOTE = "Title and metadata only; page not read"
REVIEW_DECISIONS: tuple[Decision, ...] = ("approve_as_is", "approve_public_text", "exclude")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


@dataclass
class GateResult:
    status: Status
    audit: PublicationAudit
    public_bundle: dict | None
    report_html: str | None
    review_items: list[ReviewEntry]
    keep_identities: list = field(default_factory=list)

    @property
    def unresolved(self) -> int:
        return sum(1 for f in self.audit.findings if _unresolved(f))


def _unresolved(f: Finding) -> bool:
    return f.action in ("REQUIRE_REVIEW", "BLOCK") and f.resolution is None


def _words_clip(text: str, max_words: int, max_chars: int) -> str:
    words = " ".join(text.split()).split(" ")
    out = " ".join(words[:max_words])
    if len(out) > max_chars:
        out = out[: max(0, max_chars - 1)].rstrip()
    if out != " ".join(words):
        out = out.rstrip(" ,;:.") + "…"
    return out


class PublicationGate:
    def __init__(
        self,
        bundle: dict,
        review: ReviewFile | None = None,
        policies: dict[str, SourcePolicy] | None = None,
        input_hash: str = "",
        review_hash: str | None = None,
    ):
        self.src = bundle
        self.b = copy.deepcopy(bundle)
        self.review = review
        self.policies = policies if policies is not None else load_policies()
        self.input_hash = input_hash
        self.review_hash = review_hash
        self.decisions = {e.finding_id: e for e in (review.items if review else [])}
        # Decisions also match by rule + object + field, so they survive finding-ID changes.
        self.by_key = {(e.rule_id, e.object_ref.type, e.object_ref.id, e.field): e for e in self.decisions.values()}
        # One approved public_text settles every finding on the same text field.
        self.shared_text = {
            (e.object_ref.type, e.object_ref.id, e.field or "statement"): e
            for e in (review.items if review else [])
            if e.decision == "approve_public_text" and (e.public_text or "").strip()
        }
        self.findings: dict[str, Finding] = {}
        self.steps: list[StepResult] = []
        self.effects: dict[tuple[str, str], dict[str, str]] = {}
        self.overrides: dict[tuple[str, str, str], str] = {}
        self.cutoff = parse_dt(bundle.get("run", {}).get("source_cutoff_at"))

    # ---- findings ------------------------------------------------------------------------------

    def add(
        self,
        rule: str,
        action: Action,
        coll: str,
        oid: str,
        reason: str,
        *,
        fld: str | None = None,
        before: str | None = None,
        after: str | None = None,
        allowed: Iterable[Decision] = (),
        severity: Severity | None = None,
    ) -> Finding:
        ref = AuditRef(type=REF_TYPE.get(coll, coll), id=oid)
        scope = self.src.get("target", {}).get("id", "")
        fid = "pub-" + hashlib.sha256(f"{scope}|{rule}|{action}|{ref.type}|{oid}|{fld}".encode()).hexdigest()[:12]
        if fid in self.findings:
            return self.findings[fid]
        review = action in ("REQUIRE_REVIEW", "BLOCK")
        f = Finding(
            finding_id=fid,
            rule_id=rule,
            severity=severity or ("blocking" if review or rule in ("PUB-003", "PUB-005") else "info"),
            action=action,
            object_ref=ref,
            field=fld,
            reason=reason,
            before=before,
            after=after,
            review_required=review,
            allowed_decisions=list(allowed),
            resolution=None if review else "automatic",
        )
        self.findings[fid] = f
        return f

    def resolve(self, f: Finding, coll: str, oid: str, fld: str | None) -> str:
        """Apply any review decision. Returns 'keep' or 'drop' for the object."""
        entry = self.lookup(f)
        if (entry is None or entry.decision == "pending") and "approve_public_text" in f.allowed_decisions:
            entry = self.shared_text.get((f.object_ref.type, f.object_ref.id, fld or f.field or "statement"))
        dropping = f.action in ("DROP", "REQUIRE_REVIEW", "BLOCK")
        if entry is None or entry.decision == "pending":
            return "drop" if dropping else "keep"
        if entry.decision not in f.allowed_decisions:
            self.add(
                "PUB-REVIEW",
                "BLOCK",
                coll,
                oid,
                f"Decision '{entry.decision}' is not allowed for {f.rule_id} ({f.finding_id}). "
                f"Allowed: {', '.join(f.allowed_decisions) or 'none'}.",
                fld=f.finding_id,
                allowed=(),
            )
            return "drop" if dropping else "keep"
        if entry.decision == "approve_public_text":
            text = (entry.public_text or "").strip()
            if not text:
                self.add(
                    "PUB-REVIEW",
                    "BLOCK",
                    coll,
                    oid,
                    f"approve_public_text for {f.finding_id} has no public_text.",
                    fld=f.finding_id,
                )
                return "drop"
            target_field = fld or TEXT_FIELDS.get(coll, ("statement",))[0]
            self.overrides[(coll, oid, target_field)] = text
            for rule, hit in (
                ("PUB-005", scanners.finance_strong(text)),
                ("PUB-004", scanners.misconduct(text)),
            ):
                if hit:
                    self.add(
                        rule,
                        "BLOCK",
                        coll,
                        oid,
                        f"The approved public_text still contains {hit!r}.",
                        fld=f"{target_field}:public_text",
                        before=text,
                        allowed=("exclude",),
                    )
                    return "drop"
        f.resolution = entry.decision
        return "drop" if entry.decision == "exclude" else "keep"

    def lookup(self, f: Finding) -> ReviewEntry | None:
        return self.decisions.get(f.finding_id) or self.by_key.get(
            (f.rule_id, f.object_ref.type, f.object_ref.id, f.field)
        )

    def effect(self, f: Finding, coll: str, oid: str, fld: str | None = None) -> str:
        effects = self.effects.setdefault((coll, oid), {})
        if f.finding_id not in effects:
            effects[f.finding_id] = self.resolve(f, coll, oid, fld)
        return effects[f.finding_id]

    def alive(self, coll: str, oid: str) -> bool:
        return all(e == "keep" for e in self.effects.get((coll, oid), {}).values())

    def kill(self, coll: str, oid: str) -> None:
        self.effects.setdefault((coll, oid), {})["drop"] = "drop"

    # ---- main --------------------------------------------------------------------------------

    def run(self, render: bool = True) -> GateResult:
        b = self.b
        rep = validate_data(self.src)
        if not rep.ok:
            self.add(
                "PUB-000",
                "BLOCK",
                "target",
                "bundle",
                "The input Research Bundle is not valid: " + "; ".join(rep.errors[:5]),
                allowed=(),
            )
            self.steps.append(StepResult(name="Validate Research Bundle", result="FAIL"))
            return self._result(None, None)
        self.steps.append(StepResult(name="Validate Research Bundle", result="PASS"))

        keep_ids = {k.name for k in (self.review.keep_identities if self.review else [])}
        self.redactor = Redactor.from_bundle(b, keep_ids)
        self._apply_review_edits()

        self._source_policies()
        self._evidence_eligibility()
        n_sensitive = self._sensitive_claims()
        self._sensitive_texts()
        self._dates()
        self._prune()
        n_people = self._identities()
        self.steps.insert(
            3,
            StepResult(name="Minimize personal data", result=f"{n_people} removed" if n_people else "PASS"),
        )
        self.steps.insert(4, StepResult(name="Check sensitive claims", result=n_sensitive))
        n_trunc = self._excerpts()
        self.steps.append(StepResult(name="Apply excerpt limits", result=f"{n_trunc} changed" if n_trunc else "PASS"))

        public = self._project()
        self._record_orphan_edits()
        self._check_projection(public)
        html = None
        if render:
            from pigtail.renderer.render import render_public_html

            html = render_public_html(public)
            self._leak_scan(public, html)
            self._check_notices(public, html)
        else:
            self.steps.append(StepResult(name="Remove internal run metadata", result="SKIPPED"))
        return self._result(public, html)

    def _result(self, public: dict | None, html: str | None) -> GateResult:
        findings = list(self.findings.values())
        unresolved = [f for f in findings if _unresolved(f)]
        status: Status = (
            "BLOCKED" if any(f.action == "BLOCK" for f in unresolved) else "NEEDS_REVIEW" if unresolved else "PASS"
        )
        counts = {a: sum(1 for f in findings if f.action == a) for a in ("DROP", "REDACT", "RELABEL", "TRUNCATE")}
        summary = AuditSummary(
            kept=len(public["claims"]) if public else 0,
            dropped=counts["DROP"],
            redacted=counts["REDACT"],
            relabeled=counts["RELABEL"],
            truncated=counts["TRUNCATE"],
            requires_review=sum(1 for f in findings if f.action == "REQUIRE_REVIEW"),
            blocked=sum(1 for f in findings if f.action == "BLOCK"),
        )
        reviewers = sorted(
            {e.reviewer for e in self.decisions.values() if e.reviewer and e.decision != "pending"}
            | {k.reviewer for k in (self.review.keep_identities if self.review else []) if k.reviewer}
        )
        audit = PublicationAudit(
            policy_version=POLICY_VERSION,
            sanitizer_version=SANITIZER_VERSION,
            input_bundle_hash=self.input_hash,
            review_hash=self.review_hash,
            status=status,
            steps=self.steps,
            reviewers=reviewers,
            findings=findings,
            summary=summary,
        )
        items = self._review_items(findings)
        return GateResult(
            status=status,
            audit=audit,
            public_bundle=public,
            report_html=html,
            review_items=items,
            keep_identities=list(self.review.keep_identities) if self.review else [],
        )

    def _review_items(self, findings: list[Finding]) -> list[ReviewEntry]:
        items = []
        listed = set()
        for f in findings:
            existing = self.lookup(f)
            if not (f.review_required and f.allowed_decisions) and existing is None:
                continue
            if existing is None and f.resolution:  # settled by an approved text on the same field
                shared = self.shared_text.get((f.object_ref.type, f.object_ref.id, f.field or "statement"))
                existing = ReviewEntry(
                    finding_id=f.finding_id,
                    rule_id=f.rule_id,
                    object_ref=f.object_ref,
                    decision=f.resolution,  # type: ignore[arg-type]
                    public_text=shared.public_text if shared else None,
                    rationale=f"Covered by {shared.finding_id}." if shared else None,
                    reviewer=shared.reviewer if shared else None,
                )
            listed.add(f.finding_id)
            if existing is not None:
                listed.add(existing.finding_id)
            if existing is not None and f.rule_id == "PUB-REVIEW":
                items.append(existing)  # a reviewer's own edit keeps its ID
                continue
            items.append(
                ReviewEntry(
                    finding_id=f.finding_id,
                    rule_id=f.rule_id,
                    object_ref=f.object_ref,
                    field=f.field,
                    reason=f.reason,
                    context=f.before,
                    allowed_decisions=f.allowed_decisions,
                    decision=existing.decision if existing else "pending",
                    public_text=existing.public_text if existing else None,
                    rationale=existing.rationale if existing else None,
                    reviewer=existing.reviewer if existing else None,
                )
            )
        # Keep reviewer entries whose finding is no longer raised: their edits still apply.
        items += [e for fid, e in self.decisions.items() if fid not in listed and e.decision != "pending"]
        return items

    def _edit_target(self, e: ReviewEntry) -> tuple[str, str, str]:
        coll = COLL_FOR_REF.get(e.object_ref.type, e.object_ref.type)
        default = "text" if coll == "narrative" else "statement" if coll == "claims" else None
        fld = e.field or default or TEXT_FIELDS.get(coll, ("description",))[0]
        return coll, e.object_ref.id, fld

    def _apply_review_edits(self) -> None:
        """A reviewer's rewrite or exclusion sticks to its object, even if the finding that prompted it
        is no longer raised (e.g. another approval resolved a dependency) or was added by hand."""
        for e in self.decisions.values():
            if e.decision not in ("approve_public_text", "exclude"):
                continue
            coll, oid, fld = self._edit_target(e)
            if e.decision == "exclude":
                self.kill(coll, oid)
                continue
            text = (e.public_text or "").strip()
            if not text:
                continue
            self.overrides[(coll, oid, fld)] = text
            for rule, hit in (("PUB-005", scanners.finance_strong(text)), ("PUB-004", scanners.misconduct(text))):
                if hit:
                    self.add(
                        rule,
                        "BLOCK",
                        coll,
                        oid,
                        f"The approved public_text still contains {hit!r}.",
                        fld=f"{fld}:public_text",
                        before=text,
                        allowed=("exclude",),
                    )
                    self.kill(coll, oid)

    def _record_orphan_edits(self) -> None:
        for e in self.decisions.values():
            matched = e.finding_id in self.findings or any(self.lookup(f) is e for f in self.findings.values())
            if matched or e.decision not in ("approve_public_text", "exclude"):
                continue
            coll, oid, fld = self._edit_target(e)
            f = self.add(
                "PUB-REVIEW",
                "DROP" if e.decision == "exclude" else "RELABEL",
                coll,
                oid,
                "Reviewer edit applied" + (f" ({e.rationale})" if e.rationale else "") + ".",
                fld=fld,
                after=e.public_text,
            )
            f.resolution = e.decision

    def _first_party(self, source: dict) -> bool:
        host = (urlparse(source.get("url") or "").hostname or "").removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in ESTIMATE_HOSTS):
            return False
        domain = self.b["target"].get("domain")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
        for r in self.b["repositories"]:
            if host == "github.com" and (source.get("url") or "").lower().startswith(
                f"https://github.com/{r['owner'].lower()}/"
            ):
                return True
        return source.get("source_type") in FIRST_PARTY_TYPES

    def _relayed_third_party(self, text: str) -> bool:
        """'The New York Times reported…', 'according to Sacra', 'an unverified source said…' (PUB-010)."""
        own = {self.b["target"]["name"].lower(), "the company", "the founders", "the founder", "it", "its", "they"}
        for n in self.redactor.kept:  # the company's own named executives
            own |= {n.lower(), *(w.lower() for w in n.split(" "))}
        if re.search(r"\bunverified\b|\(\s*[^)]*\breport\)", text, re.I):
            return True
        for m in re.finditer(r"\b(?:according to|per|as reported by|reported by)\s+([^,.;]+)", text, re.I):
            if m.group(1).strip().lower().split(" ")[0] not in own and not m.group(1).lower().startswith(tuple(own)):
                return True
        for m in re.finditer(r"([A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*)*)\s+(?:reported|estimated|estimates)\b", text):
            if m.group(1).lower() not in own and m.group(1).lower() != self.b["target"]["name"].lower():
                return True
        return False

    def _metric_attribution(self, m: dict, links: dict[str, list[dict]], sources: dict[str, dict]) -> str:
        els = [el for cid in m["claim_ids"] for el in links.get(cid, [])]
        srcs = [sources[el["source_id"]] for el in els if el["source_id"] in sources]
        raw = " ".join(c["statement"] for c in self.b["claims"] if c["id"] in m["claim_ids"])
        stmts = raw.lower()
        if self._relayed_third_party(raw + " " + m["label"]):
            return "third-party estimate" if "estimat" in stmts else "third-party reported"
        if any(
            self._first_party(sources[el["source_id"]]) and el["evidence_class"] in ("documented", "company_measured")
            for el in els
        ):
            return "company reported"
        hosts = [(urlparse(x.get("url") or "").hostname or "").removeprefix("www.") for x in srcs]
        if "estimat" in stmts or any(any(h == e or h.endswith("." + e) for e in ESTIMATE_HOSTS) for h in hosts):
            return "third-party estimate"
        if any(el["evidence_class"] == "third_party_measured" for el in els):
            return "third-party measured"
        if els and all(el["evidence_class"] == "inferred" for el in els):
            return "Pigtail inference"
        return "third-party reported"

    def _display_name(self) -> str:
        """'pocketbase' -> 'PocketBase' when the sources consistently write it that way (PUB-015)."""
        name = self.b["target"]["name"]
        if name != name.lower():
            return name
        counts: dict[str, int] = {}
        for c in self.b["claims"]:
            for m in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])", c["statement"], re.I):
                if m.group(0) != name:
                    counts[m.group(0)] = counts.get(m.group(0), 0) + 1
        return max(counts, key=lambda k: counts[k]) if counts else name

    # ---- PUB-013 -----------------------------------------------------------------------------

    def _source_policies(self) -> None:
        self.display: dict[str, str] = {}
        self.max_chars: dict[str, int] = {}
        changed = 0
        for s in self.b["sources"]:
            pol = self.policies.get(s["policy"]["policy_key"])
            bundle_mode = s["policy"]["public_display_mode"]
            file_mode = pol.public_display.mode if pol else "link_only"
            eff = min(bundle_mode, file_mode, key=lambda m: DISPLAY_ORDER[m])
            self.display[s["id"]] = eff
            self.max_chars[s["id"]] = pol.retention.max_excerpt_chars if pol and pol.excerpt_allowed else 0
            if pol is None:
                changed += 1
                self.add(
                    "PUB-013",
                    "RELABEL",
                    "sources",
                    s["id"],
                    f"No current source policy '{s['policy']['policy_key']}'; the source is shown as a link only.",
                    fld="public_display_mode",
                    before=bundle_mode,
                    after=eff,
                )
            elif eff != bundle_mode:
                changed += 1
                self.add(
                    "PUB-013",
                    "RELABEL",
                    "sources",
                    s["id"],
                    "The current source policy is stricter than the one recorded in the bundle; the stricter one applies.",
                    fld="public_display_mode",
                    before=bundle_mode,
                    after=eff,
                )
        self.steps.append(
            StepResult(name="Check source publication policies", result=f"{changed} restricted" if changed else "PASS")
        )

    # ---- PUB-001, PUB-002 ----------------------------------------------------------------------

    def _evidence_eligibility(self) -> None:
        b = self.b
        fetch_status = {f["id"]: f["status"] for f in b["source_fetches"]}
        links: dict[str, list[dict]] = {}
        for el in b["evidence_links"]:
            links.setdefault(el["claim_id"], []).append(el)
        in_conflict = {cid for c in b["conflicts"] for cid in c["claim_ids"]}
        self.manually_verified: set[str] = set()
        self.dropped_links: set[str] = set()
        self.conflict_only: set[str] = set()
        n = 0
        for c in b["claims"]:
            cl = links.get(c["id"], [])
            ok = [el for el in cl if fetch_status.get(el["source_fetch_id"]) == SUCCESS]
            failed = [el for el in cl if fetch_status.get(el["source_fetch_id"]) != SUCCESS]
            if not ok:
                n += 1
                f = self.add(
                    "PUB-001",
                    "BLOCK",
                    "claims",
                    c["id"],
                    "No supporting source was read successfully"
                    + (
                        f" (fetch status: {', '.join(sorted({fetch_status.get(el['source_fetch_id'], 'missing') for el in cl}))})."
                        if cl
                        else " (no evidence link)."
                    )
                    + " Exclude it, or mark it manually verified after checking the source yourself.",
                    before=c["statement"],
                    allowed=("exclude", "mark_manually_verified"),
                )
                if self.effect(f, "claims", c["id"]) == "keep":
                    self.manually_verified.add(c["id"])
            else:
                for el in failed:
                    n += 1
                    self.dropped_links.add(el["id"])
                    self.add(
                        "PUB-001",
                        "DROP",
                        "evidence_links",
                        el["id"],
                        f"Evidence from a source Pigtail could not read ({fetch_status.get(el['source_fetch_id'])}) "
                        "is not shown as support; other successful evidence remains.",
                    )
            if c["status"] != "active":
                n += 1
                if c["id"] in in_conflict:
                    self.conflict_only.add(c["id"])
                    self.add(
                        "PUB-002",
                        "RELABEL",
                        "claims",
                        c["id"],
                        f"A {c['status']} claim is shown only inside its conflict, not as a fact.",
                        before=c["statement"],
                    )
                else:
                    self.kill("claims", c["id"])
                    self.add(
                        "PUB-002",
                        "DROP",
                        "claims",
                        c["id"],
                        f"A {c['status']} claim is not a public fact.",
                        before=c["statement"],
                    )
        self.steps.append(
            StepResult(name="Check evidence eligibility", result=f"{n} finding{'s' * (n != 1)}" if n else "PASS")
        )

    # ---- PUB-003, PUB-004, PUB-005, PUB-009 on claims --------------------------------------------

    def _person_context(self, text: str, subject_type: str | None = None) -> bool:
        return (
            subject_type == "person"
            or scanners.has_person_context(text)
            or self.redactor.mentions_any(text)
            or bool(self.redactor.kept and any(k.lower() in text.lower() for k in self.redactor.kept))
        )

    def _sensitive_claims(self) -> str:
        b = self.b
        sources = {s["id"]: s for s in b["sources"]}
        links: dict[str, list[dict]] = {}
        for el in b["evidence_links"]:
            links.setdefault(el["claim_id"], []).append(el)
        self.attribution: dict[str, str] = {}
        self.first_party_claims: set[str] = set()
        n_drop = n_review = 0
        who = "the project" if b["target"]["kind"] == "repository" else "the company"
        for c in b["claims"]:
            cid, text = c["id"], c["statement"]
            if c["claim_kind"] in scanners.ALLEGATION_KINDS:
                n_drop += 1
                self.kill("claims", cid)
                self.add("PUB-003", "DROP", "claims", cid, "Allegation-only content is never published.", before=text)
                continue
            if c["is_negative_sensitive"]:
                if c["review_state"] != "human_verified":
                    n_drop += 1
                    f = self.add(
                        "PUB-003",
                        "DROP",
                        "claims",
                        cid,
                        "Negative-sensitive claim that no human has verified.",
                        before=text,
                        allowed=("mark_manually_verified",),
                    )
                else:
                    n_review += 1
                    f = self.add(
                        "PUB-003",
                        "REQUIRE_REVIEW",
                        "claims",
                        cid,
                        "Human-verified negative-sensitive claim: publication needs an explicit approval.",
                        before=text,
                        allowed=REVIEW_DECISIONS,
                    )
                self.effect(f, "claims", cid)
            if hit := scanners.finance_strong(text):
                n_drop += 1
                f = self.add(
                    "PUB-005",
                    "DROP",
                    "claims",
                    cid,
                    f"Personal financial detail ({hit!r}) is excluded from Pigtail-hosted reports.",
                    before=text,
                    allowed=("approve_public_text",),
                )
                self.effect(f, "claims", cid, "statement")
            elif (hit := scanners.finance_weak(text) or "") and self._person_context(text, c["subject_ref"]["type"]):
                n_review += 1
                f = self.add(
                    "PUB-005",
                    "REQUIRE_REVIEW",
                    "claims",
                    cid,
                    f"Possible personal financial detail ({hit!r}); company-level figures may stay.",
                    before=text,
                    allowed=REVIEW_DECISIONS,
                )
                self.effect(f, "claims", cid, "statement")
            if hit := scanners.misconduct(text):
                n_review += 1
                f = self.add(
                    "PUB-004",
                    "REQUIRE_REVIEW",
                    "claims",
                    cid,
                    f"Misconduct language ({hit!r}): publish only with attributed wording a human approved.",
                    before=text,
                    allowed=REVIEW_DECISIONS,
                )
                self.effect(f, "claims", cid, "statement")
            elif (hit := scanners.intent(text) or "") and self._person_context(text, c["subject_ref"]["type"]):
                n_review += 1
                f = self.add(
                    "PUB-004",
                    "REQUIRE_REVIEW",
                    "claims",
                    cid,
                    f"Statement about a person's intention or motive ({hit!r}): check it is attributed and supported.",
                    before=text,
                    allowed=REVIEW_DECISIONS,
                )
                self.effect(f, "claims", cid, "statement")
            # PUB-009: first-party absolute statements keep a visible attribution.
            cl = links.get(cid, [])
            first_party = bool(cl) and all(self._first_party(sources[el["source_id"]]) for el in cl)
            if any(self._first_party(sources[el["source_id"]]) for el in cl):
                self.first_party_claims.add(cid)
            if (
                first_party
                and (hit := scanners.absolute(text))
                and not scanners.is_attributed(text)
                and not self._relayed_third_party(text)
            ):
                label = f"According to {who}"
                if any(sources[el["source_id"]]["source_type"] == "founder_interview" for el in cl):
                    label = "According to the founders"
                self.attribution[cid] = label
                self.add(
                    "PUB-009",
                    "RELABEL",
                    "claims",
                    cid,
                    f"First-party absolute statement ({hit!r}) is shown as attributed, not in Pigtail's voice.",
                    before=text,
                    after=f"{label}: {text}",
                )
        for e in b["events"]:
            if not e["is_negative_sensitive"]:
                continue
            if e["review_state"] != "human_verified":
                n_drop += 1
                f = self.add(
                    "PUB-003",
                    "DROP",
                    "events",
                    e["id"],
                    "Negative-sensitive event that no human has verified.",
                    before=e["title"],
                    allowed=("mark_manually_verified",),
                )
            else:
                n_review += 1
                f = self.add(
                    "PUB-003",
                    "REQUIRE_REVIEW",
                    "events",
                    e["id"],
                    "Human-verified negative-sensitive event: publication needs an explicit approval.",
                    before=e["title"],
                    allowed=REVIEW_DECISIONS,
                )
            self.effect(f, "events", e["id"])
        parts = []
        if n_drop:
            parts.append(f"{n_drop} removed")
        if n_review:
            parts.append(f"{n_review} need review")
        return ", ".join(parts) or "PASS"

    # ---- PUB-004/005/009 on Pigtail-written text ----------------------------------------------------

    def _texts(self) -> Iterable[tuple[str, str, str, str, dict]]:
        """(collection, id, field, text, object) for every Pigtail-written text."""
        b = self.b
        for coll, fields in TEXT_FIELDS.items():
            for o in b[coll]:
                for fld in fields:
                    if o.get(fld):
                        yield coll, o["id"], fld, o[fld], o
        for key in NARRATIVE_KEYS:
            blk = b["narrative"].get(key)
            if blk:
                yield "narrative", key, "text", blk["text"], blk
        for i, blk in enumerate(b["narrative"]["key_takeaways"]):
            yield "narrative", f"key_takeaways[{i}]", "text", blk["text"], blk

    def _sensitive_texts(self) -> None:
        relabeled_claims = set(self.attribution)
        for coll, oid, fld, text, o in list(self._texts()):
            if coll == "gaps":
                continue  # gaps are neutralized, not dropped (PUB-018)
            if hit := scanners.finance_strong(text):
                f = self.add(
                    "PUB-005",
                    "DROP",
                    coll,
                    oid,
                    f"Personal financial detail ({hit!r}) is excluded from Pigtail-hosted reports.",
                    fld=fld,
                    before=text,
                    allowed=("approve_public_text",),
                )
                self.effect(f, coll, oid, fld)
            elif (hit := scanners.finance_weak(text) or "") and self._person_context(text):
                f = self.add(
                    "PUB-005",
                    "REQUIRE_REVIEW",
                    coll,
                    oid,
                    f"Possible personal financial detail ({hit!r}).",
                    fld=fld,
                    before=text,
                    allowed=REVIEW_DECISIONS,
                )
                self.effect(f, coll, oid, fld)
            if hit := scanners.misconduct(text):
                f = self.add(
                    "PUB-004",
                    "BLOCK",
                    coll,
                    oid,
                    f"Pigtail-written text uses misconduct language ({hit!r}). Machine inference may not assert "
                    "misconduct; exclude it or supply attributed public_text.",
                    fld=fld,
                    before=text,
                    allowed=("approve_public_text", "exclude"),
                )
                self.effect(f, coll, oid, fld)
            elif (hit := scanners.intent(text) or "") and self._person_context(text):
                f = self.add(
                    "PUB-004",
                    "REQUIRE_REVIEW",
                    coll,
                    oid,
                    f"Pigtail-written text about a person's intention or motive ({hit!r}).",
                    fld=fld,
                    before=text,
                    allowed=REVIEW_DECISIONS,
                )
                self.effect(f, coll, oid, fld)
            if coll == "narrative" and (relabeled_claims | self.first_party_claims) & set(o.get("claim_ids", [])):
                hit = scanners.absolute(text)
                if hit and not scanners.is_attributed(text):
                    f = self.add(
                        "PUB-009",
                        "REQUIRE_REVIEW",
                        coll,
                        oid,
                        f"Pigtail's narrative states {hit!r} in its own voice, but the cited evidence is the "
                        "company's own statement. Approve, or supply attributed public_text.",
                        fld=fld,
                        before=text,
                        allowed=REVIEW_DECISIONS,
                    )
                    self.effect(f, coll, oid, fld)

    # ---- PUB-011 --------------------------------------------------------------------------------

    def _dates(self) -> None:
        n = 0
        for s in self.b["sources"]:
            why = scanners.suspicious_instant(s["published_at"], self.cutoff)
            host = (urlparse(s.get("url") or "").hostname or "").removeprefix("www.")
            if s["published_at"] and (host.endswith("wikipedia.org") or "/wiki/" in (s.get("url") or "")):
                why = "wiki pages are living documents; their date is usually the page's creation date"
            if why:
                n += 1
                self.add(
                    "PUB-011",
                    "RELABEL",
                    "sources",
                    s["id"],
                    f"Publication date shown as undated: {why}.",
                    fld="published_at",
                    before=s["published_at"],
                    after=None,
                )
                s["published_at"] = None
        dated = ("claims", "events", "launch_episodes", "company_stages", "strategy_phases", "tactic_occurrences")
        for coll in (*dated, "metric_snapshots"):
            for o in self.b[coll]:
                if coll == "metric_snapshots" and o["metric_key"].startswith("github_"):
                    continue
                why = scanners.suspicious_range(o["time"], self.cutoff)
                if not why:
                    continue
                n += 1
                before = f"{o['time']['start']} ({o['time']['precision']})"
                start = parse_dt(o["time"]["start"])
                if why.startswith("January") and start:
                    o["time"] = {
                        "start": iso(datetime(start.year, 1, 1, tzinfo=start.tzinfo)),
                        "end": iso(datetime(start.year, 12, 31, 23, 59, 59, tzinfo=start.tzinfo)),
                        "precision": "year",
                        "label": None,
                    }
                    after = f"{start.year} (year)"
                else:
                    o["time"] = {"start": None, "end": None, "precision": "unknown", "label": None}
                    after = "undated"
                self.add(
                    "PUB-011",
                    "RELABEL",
                    coll,
                    o["id"],
                    f"Date precision reduced: {why}.",
                    fld="time",
                    before=before,
                    after=after,
                )
        self.steps.append(StepResult(name="Check dates", result=f"{n} relabeled" if n else "PASS"))

    # ---- PUB-017 --------------------------------------------------------------------------------

    def _fact_claims(self) -> set[str]:
        return {
            c["id"] for c in self.b["claims"] if c["id"] not in self.conflict_only and self.alive("claims", c["id"])
        }

    def _depend(self, coll: str, oid: str, ids: list[str], live: set[str], text: str | None) -> None:
        if not ids or not self.alive(coll, oid):
            return
        remaining = [i for i in ids if i in live]
        if not remaining:
            self.kill(coll, oid)
            self.add(
                "PUB-017",
                "DROP",
                coll,
                oid,
                "Every claim this rests on was removed from the public report, so it is removed too.",
                before=text,
            )
        elif len(remaining) < len(ids):
            f = self.add(
                "PUB-017",
                "REQUIRE_REVIEW",
                coll,
                oid,
                f"{len(ids) - len(remaining)} of {len(ids)} cited claims were removed. Check the text is still "
                "supported by the rest (approve_as_is), rewrite it (approve_public_text), or exclude it.",
                fld=TEXT_FIELDS.get(coll, ("text",))[0],
                before=text,
                allowed=REVIEW_DECISIONS,
            )
            self.effect(f, coll, oid, TEXT_FIELDS.get(coll, ("text",))[0])

    def _prune(self) -> None:
        b = self.b
        for _ in range(20):
            before = sum(len(v) for v in self.effects.values())
            live = self._fact_claims()
            alive_ev = {e["id"] for e in b["events"] if self.alive("events", e["id"])}
            for coll in CLAIM_OBJECTS:
                for o in b[coll]:
                    text = next((o[f] for f in TEXT_FIELDS.get(coll, ()) if o.get(f)), None)
                    self._depend(coll, o["id"], o["claim_ids"], live, text)
            for le in b["launch_episodes"]:
                if self.alive("launch_episodes", le["id"]) and le["event_ids"]:
                    left = [i for i in le["event_ids"] if i in alive_ev]
                    if not left:
                        self.kill("launch_episodes", le["id"])
                        self.add("PUB-017", "DROP", "launch_episodes", le["id"], "All its events were removed.")
            for g in b["growth_episodes"]:
                gone = [i for i in g["related_event_ids"] if i not in alive_ev]
                if gone and self.alive("growth_episodes", g["id"]):
                    f = self.add(
                        "PUB-017",
                        "REQUIRE_REVIEW",
                        "growth_episodes",
                        g["id"],
                        f"{len(gone)} related event(s) were removed; check the summary does not describe them.",
                        fld="summary",
                        before=g["summary"],
                        allowed=REVIEW_DECISIONS,
                    )
                    self.effect(f, "growth_episodes", g["id"], "summary")
            for coll, parent, key in (
                ("tactic_occurrences", "tactics", "tactic_id"),
                ("growth_engine_occurrences", "growth_engines", "growth_engine_id"),
            ):
                for o in b[coll]:
                    if self.alive(coll, o["id"]) and not self.alive(parent, o[key]):
                        self.kill(coll, o["id"])
                        self.add("PUB-017", "DROP", coll, o["id"], f"Its {REF_TYPE[parent]} was removed.")
            for key in NARRATIVE_KEYS:
                blk = b["narrative"].get(key)
                if blk:
                    self._depend("narrative", key, blk["claim_ids"], live, blk["text"])
            for i, blk in enumerate(b["narrative"]["key_takeaways"]):
                self._depend("narrative", f"key_takeaways[{i}]", blk["claim_ids"], live, blk["text"])
            if sum(len(v) for v in self.effects.values()) == before:
                break

    # ---- PUB-006/007/008 ------------------------------------------------------------------------

    def _identities(self) -> int:
        b = self.b
        red = self.redactor
        n = 0
        for p in b["people"]:
            n += 1
            self.add(
                "PUB-006",
                "DROP",
                "people",
                p["id"],
                "People are not listed or shown in the header of Pigtail-hosted reports.",
            )
        tdomain = b["target"].get("domain")
        for s in b["sources"]:
            if s.get("author") and s["author"] not in red.kept:
                n += 1
                self.add(
                    "PUB-007",
                    "REDACT",
                    "sources",
                    s["id"],
                    "Author/account names are not shown on source cards.",
                    fld="author",
                    before=s["author"],
                )
            title = s.get("title") or ""
            personal_post = s["surface_key"] in ("x", "reddit")  # post titles there usually name the poster
            redacted = red.redact(title) or ""
            # Keep the real title traceable; only an anonymised person's name is replaced.
            if (
                title
                and not personal_post
                and not scanners.finance_strong(title)
                and redacted != title
                and not red.residual(redacted)
            ):
                self.add(
                    "PUB-008",
                    "REDACT",
                    "sources",
                    s["id"],
                    "A private individual's name in the title is replaced by their role.",
                    fld="title",
                    before=title,
                    after=redacted,
                )
                s["title"] = redacted
                continue
            if title and (personal_post or red.residual(title) or scanners.finance_strong(title)):
                label = neutral_source_label(s, tdomain)
                self.add(
                    "PUB-008",
                    "RELABEL",
                    "sources",
                    s["id"],
                    "The title names a person, handle or personal detail; a neutral label is shown instead.",
                    fld="title",
                    before=title,
                    after=label,
                )
                s["title"] = label
        return n

    def _public_text(self, coll: str, oid: str, fld: str, text: str | None) -> str | None:
        """Override (if approved), then redact, recording REDACT/residual findings."""
        if text is None:
            return None
        new = self.overrides.get((coll, oid, fld), text)
        red = self.redactor.redact(new) or ""
        if red != text:
            self.add(
                "PUB-006" if self.redactor.names_person(text) else "PUB-007",
                "REDACT" if (coll, oid, fld) not in self.overrides else "RELABEL",
                coll,
                oid,
                "Approved public text applied."
                if (coll, oid, fld) in self.overrides
                else "Names/handles replaced by role labels.",
                fld=fld,
                before=text,
                after=red,
            )
        left = self.redactor.residual(red)
        if left:
            f = self.add(
                "PUB-007",
                "REQUIRE_REVIEW",
                coll,
                oid,
                (
                    f"{', '.join(sorted(set(left) & self.redactor.ambiguous))!s} could refer to more than one "
                    "person; supply public_text with the right role, or exclude."
                )
                if set(left) & self.redactor.ambiguous
                else f"A name or handle is still present after redaction ({', '.join(left[:3])}).",
                fld=fld,
                before=red,
                allowed=REVIEW_DECISIONS,
            )
            eff = self.resolve(f, coll, oid, fld)
            if eff == "drop":
                return None
            if (coll, oid, fld) in self.overrides:
                return self.redactor.redact(self.overrides[(coll, oid, fld)])
        return red

    # ---- PUB-012/013 ----------------------------------------------------------------------------

    def _excerpts(self) -> int:
        n = 0
        shown: set[str] = set()
        for el in self.b["evidence_links"]:
            sid = el["source_id"]
            if not self.alive("claims", el["claim_id"]) or el["id"] in self.dropped_links:
                el["excerpt"] = None
            if el["excerpt"]:
                reason = None
                action: Action = "DROP"
                new: str | None = None
                if self.display.get(sid) != "paraphrase_link_excerpt":
                    reason = f"Source display policy is '{self.display.get(sid)}'; no excerpt is shown."
                elif sid in shown:
                    reason = "At most one excerpt per source is shown; this one is replaced by the claim and link."
                elif self.redactor.residual(el["excerpt"]):
                    reason = "The excerpt names a person or handle; quotes are not altered, so it is not shown."
                else:
                    new = _words_clip(el["excerpt"], PUBLIC_EXCERPT_MAX_WORDS, self.max_chars.get(sid, 0) or 0)
                    if self.max_chars.get(sid, 0) <= 0:
                        new = None
                        reason = "The source policy allows no excerpt."
                    elif new != " ".join(el["excerpt"].split()):
                        action = "TRUNCATE"
                        reason = f"Public excerpts are capped at {PUBLIC_EXCERPT_MAX_WORDS} words."
                if new:
                    shown.add(sid)
                if reason:
                    n += 1
                    self.add(
                        "PUB-012" if action == "TRUNCATE" or "per source" in reason else "PUB-013",
                        action,
                        "evidence_links",
                        el["id"],
                        reason,
                        fld="excerpt",
                        before=el["excerpt"],
                        after=new,
                    )
                el["excerpt"] = new
            if el["locator"]["kind"] == "text_fragment" and el["locator"]["value"]:
                self.add(
                    "PUB-012",
                    "REDACT",
                    "evidence_links",
                    el["id"],
                    "Text-fragment locators quote the source; only the locator kind is shown publicly.",
                    fld="locator",
                    before=el["locator"]["value"],
                )
                el["locator"] = {"kind": "text_fragment", "value": ""}
        return n

    # ---- projection ------------------------------------------------------------------------------

    def _project(self) -> dict:
        b = self.b
        live_claims = {c["id"] for c in b["claims"] if self.alive("claims", c["id"])}
        fact = self._fact_claims()
        alive = {coll: {o["id"] for o in b[coll] if self.alive(coll, o["id"])} for coll in CLAIM_OBJECTS}

        def ids(lst: list[str], pool: set[str]) -> list[str]:
            return [i for i in lst if i in pool]

        def txt(coll: str, o: dict) -> dict:
            o = dict(o)
            for fld in TEXT_FIELDS.get(coll, ()):
                if o.get(fld):
                    o[fld] = self._public_text(coll, o["id"], fld, o[fld])
                    if o[fld] is None:
                        return {}
            return o

        claims = []
        for c in b["claims"]:
            if c["id"] not in live_claims:
                continue
            stmt = self._public_text("claims", c["id"], "statement", c["statement"])
            if stmt is None:
                continue
            claims.append(
                {
                    **c,
                    "statement": stmt,
                    "public_attribution": self.attribution.get(c["id"]),
                    "manually_verified": c["id"] in self.manually_verified,
                }
            )
        live_claims = {c["id"] for c in claims}
        fact &= live_claims

        fetch_ok = {f["id"]: f["status"] == SUCCESS for f in b["source_fetches"]}
        links = []
        for el in b["evidence_links"]:
            if el["claim_id"] not in live_claims or el["id"] in self.dropped_links:
                continue
            if not fetch_ok.get(el["source_fetch_id"]) and el["claim_id"] not in self.manually_verified:
                continue
            t = el["target_ref"]
            if t["type"] != "claim" and t["type"] in COLL_FOR_REF:
                coll = COLL_FOR_REF[t["type"]]
                if coll in alive and t["id"] not in alive[coll]:
                    continue
            links.append({**el, "notes": self.redactor.redact(el["notes"])})
        cited_sources = {el["source_id"] for el in links}
        fetches_by_source: dict[str, list[dict]] = {}
        for fch in b["source_fetches"]:
            fetches_by_source.setdefault(fch["source_id"], []).append(fch)
        sources = []
        for s in b["sources"]:
            if s["id"] not in cited_sources:
                self.add(
                    "PUB-017",
                    "DROP",
                    "sources",
                    s["id"],
                    "The source supports no public claim, so it is not listed.",
                    before=s.get("title"),
                )
                continue
            pol = dict(s["policy"])
            pol["public_display_mode"] = self.display[s["id"]]
            fs = fetches_by_source.get(s["id"], [])
            note = None
            partly_read = any(fch["status"] == SUCCESS for fch in fs) and any(fch["status"] != SUCCESS for fch in fs)
            metadata_api = s["source_type"] in ("hn_story", "community") or any(
                fch["status"] == SUCCESS and not fch["parser_version"].startswith("trafilatura") for fch in fs
            )
            if partly_read and metadata_api:
                note = METADATA_ONLY_NOTE
                self.add(
                    "PUB-001",
                    "RELABEL",
                    "sources",
                    s["id"],
                    "Only the title and metadata were read (the page fetch failed); the citation says so.",
                    after=note,
                )
            sources.append({**s, "author": None, "policy": pol, "public_note": note})
        live_sources = {s["id"] for s in sources}
        fetches = [
            {k: f[k] for k in ("id", "source_id", "retrieved_at", "status", "content_hash")}
            for f in b["source_fetches"]
            if f["source_id"] in live_sources
        ]

        out: dict[str, list[dict]] = {}
        for coll in CLAIM_OBJECTS:
            kept = []
            for o in b[coll]:
                if o["id"] not in alive[coll]:
                    continue
                o2 = txt(coll, o)
                if not o2:
                    alive[coll].discard(o["id"])
                    continue
                o2["claim_ids"] = ids(o2["claim_ids"], fact)
                kept.append(o2)
            out[coll] = kept
        alive = {coll: {o["id"] for o in out[coll]} for coll in CLAIM_OBJECTS}
        ev, ms, oc, to = alive["events"], alive["metric_snapshots"], alive["outcomes"], alive["tactic_occurrences"]
        le_alive = alive["launch_episodes"]
        for e in out["events"]:
            e["metric_snapshot_ids"] = ids(e["metric_snapshot_ids"], ms)
            e["outcome_ids"] = ids(e["outcome_ids"], oc)
            e["tactic_occurrence_ids"] = ids(e["tactic_occurrence_ids"], to)
            if e["launch_episode_id"] not in le_alive:
                e["launch_episode_id"] = None
        for le in out["launch_episodes"]:
            le["event_ids"] = ids(le["event_ids"], ev)
        for g in out["growth_episodes"]:
            g["related_event_ids"] = ids(g["related_event_ids"], ev)
            for k in ("start_metric_id", "end_metric_id"):
                if g[k] not in ms:
                    g[k] = None
            if not g["related_event_ids"] and g["causal_attribution"] != "unknown":
                g["causal_attribution"] = "unknown"
        for t in out["tactic_occurrences"]:
            if t["event_id"] not in ev:
                t["event_id"] = None
            if t["strategy_phase_id"] not in alive["strategy_phases"]:
                t["strategy_phase_id"] = None
            t["outcome_ids"] = ids(t["outcome_ids"], oc)
        for go in out["growth_engine_occurrences"]:
            go["strategy_phase_ids"] = ids(go["strategy_phase_ids"], alive["strategy_phases"])
            go["tactic_occurrence_ids"] = ids(go["tactic_occurrence_ids"], to)
            go["outcome_ids"] = ids(go["outcome_ids"], oc)
        for o in out["outcomes"]:
            if o["event_id"] not in ev:
                o["event_id"] = None
            if o["tactic_occurrence_id"] not in to:
                o["tactic_occurrence_id"] = None
            o["metric_snapshot_ids"] = ids(o["metric_snapshot_ids"], ms)

        all_live: set[str] = {b["target"]["id"]} | live_claims | live_sources
        all_live |= {r["id"] for r in b["repositories"]} | {s["id"] for s in b["surfaces"]}
        for v in alive.values():
            all_live |= v
        for coll in ("prerequisites", "constraints"):
            for o in out[coll]:
                o["applies_to_refs"] = [r for r in o["applies_to_refs"] if r["id"] in all_live]

        conflicts = []
        for c in b["conflicts"]:
            cids = ids(c["claim_ids"], live_claims)
            if len(cids) < 2:
                if len(cids) < len(c["claim_ids"]):
                    self.add("PUB-017", "DROP", "conflicts", c["id"], "Fewer than two of its claims remain public.")
                continue
            summary = self._public_text("conflicts", c["id"], "summary", c["summary"])
            if summary is None:
                continue
            conflicts.append(
                {
                    **c,
                    "summary": summary,
                    "claim_ids": cids,
                    "related_refs": [r for r in c["related_refs"] if r["id"] in all_live],
                }
            )
        all_live |= {c["id"] for c in conflicts}

        gaps = []
        for g in b["gaps"]:
            refs = [r for r in g["related_refs"] if r["id"] in all_live]
            summary = g["summary"]
            why = None
            if len(refs) < len(g["related_refs"]):
                why = "It referred to content removed from the public report."
            elif scanners.finance_strong(summary) or scanners.misconduct(summary):
                why = "It repeated sensitive content."
            if why:
                self.add(
                    "PUB-018",
                    "RELABEL",
                    "gaps",
                    g["id"],
                    why + " A neutral gap is shown.",
                    fld="summary",
                    before=summary,
                    after=NEUTRAL_GAP,
                )
                summary = NEUTRAL_GAP
            else:
                summary = self._public_text("gaps", g["id"], "summary", summary) or NEUTRAL_GAP
            gaps.append({**g, "summary": summary, "related_refs": refs})

        def block(key: str, blk: dict | None) -> dict | None:
            if not blk or not self.alive("narrative", key):
                return None
            text = self._public_text("narrative", key, "text", blk["text"])
            if text is None:
                return None
            cids = ids(blk["claim_ids"], fact)
            return {**blk, "text": text, "claim_ids": cids} if cids else None

        n = b["narrative"]
        narrative: dict[str, Any] = {k: block(k, n.get(k)) for k in NARRATIVE_KEYS}
        narrative["key_takeaways"] = [
            x for i, t in enumerate(n["key_takeaways"]) if (x := block(f"key_takeaways[{i}]", t)) is not None
        ]

        src_by_id = {x["id"]: x for x in b["sources"]}
        links_by_claim: dict[str, list[dict]] = {}
        for el in links:
            links_by_claim.setdefault(el["claim_id"], []).append(el)
        for m in out["metric_snapshots"]:
            m["public_attribution"] = (
                None
                if m["metric_key"].startswith("github_")
                else self._metric_attribution(m, links_by_claim, src_by_id)
            )

        target = dict(b["target"])
        reviewer_desc = ("target", target["id"], "description") in self.overrides
        if target["description"] and not reviewer_desc:
            # The target's own marketing line is shown as a quoted self-description, never as Pigtail's words.
            self.add(
                "PUB-012",
                "RELABEL",
                "target",
                target["id"],
                "The target's self-description is shown as a quote with its source, not in Pigtail's voice.",
                fld="description",
                before=target["description"],
            )
        target["description"] = self._public_text("target", target["id"], "description", target["description"])
        desc_source = None if not target["description"] else "reviewer" if reviewer_desc else "self_description"
        shown = self._display_name()
        if shown != target["name"]:
            self.add(
                "PUB-015",
                "RELABEL",
                "target",
                target["id"],
                "Display name written the way the sources write it.",
                fld="name",
                before=target["name"],
                after=shown,
            )
            target["name"] = shown
        repos = [{**r, "claim_ids": ids(r["claim_ids"], fact)} for r in b["repositories"]]
        presences = out["surface_presences"]

        run = b["run"]
        return {
            "projection": "pigtail-public-report",
            "projection_version": PROJECTION_VERSION,
            "schema_version": b["schema_version"],
            "publication_policy_version": POLICY_VERSION,
            "report": {
                "status": run["status"],
                "source_cutoff_at": run["source_cutoff_at"],
                "engine_version": run["engine_version"],
            },
            "notices": {
                "independence": INDEPENDENCE_NOTICE.format(target=target["name"]),
                "third_party_content": THIRD_PARTY_NOTICE,
                "machine_generated": MACHINE_GENERATED_NOTICE,
            },
            "target": target,
            "target_description_source": desc_source,
            "repositories": repos,
            "sources": sources,
            "source_fetches": fetches,
            "claims": claims,
            "evidence_links": links,
            "events": out["events"],
            "launch_episodes": out["launch_episodes"],
            "growth_episodes": out["growth_episodes"],
            "metric_snapshots": out["metric_snapshots"],
            "company_stages": out["company_stages"],
            "strategy_phases": out["strategy_phases"],
            "surfaces": b["surfaces"],
            "surface_presences": presences,
            "tactics": out["tactics"],
            "tactic_occurrences": out["tactic_occurrences"],
            "growth_engines": out["growth_engines"],
            "growth_engine_occurrences": out["growth_engine_occurrences"],
            "outcomes": out["outcomes"],
            "prerequisites": out["prerequisites"],
            "constraints": out["constraints"],
            "conflicts": conflicts,
            "gaps": gaps,
            "narrative": narrative,
        }

    # ---- final mechanical checks -------------------------------------------------------------------

    def _check_projection(self, public: dict) -> None:
        from pigtail.publication.verify import check_public_bundle

        problems = check_public_bundle(public)
        try:
            PublicReportBundle.model_validate(public)
        except Exception as exc:  # pydantic ValidationError
            problems.append(f"schema: {str(exc).splitlines()[0]}")
        for i, p in enumerate(problems):
            self.add("PUB-STRUCT", "BLOCK", "target", public["target"]["id"], p, fld=str(i))

    def _leak_scan(self, public: dict, html: str) -> None:
        import json

        run = self.src["run"]
        public_json = json.dumps(public, ensure_ascii=False)
        needles = {
            "bundle_id": self.src["bundle_id"],
            "run_id": run["run_id"],
            "model": run["model"]["model"],
            "discovery provider": run["discovery"]["provider"],
        }
        keys = (
            "model_input_tokens",
            "model_output_tokens",
            "total_cost",
            "model_cost",
            "github_api_requests",
            '"retries"',
        )
        # Plain words ("none") cannot be scanned for; identifiers have separators or digits.
        leaks = [
            f"{k} ({v})"
            for k, v in needles.items()
            if v and len(v) > 3 and re.search(r"[-_/.0-9]", v) and (v in html or v in public_json)
        ]
        leaks += [k for k in keys if k in html or k in public_json]
        # Visible text only: names inside URLs are source locators (PUB-007).
        visible = re.sub(r"https?://[^\s\"'<>]+", " ", html)
        if re.search(
            r"(?:/Users/|/home/|[A-Z]:\\\\Users\\\\)", visible + re.sub(r"https?://[^\s\"]+", " ", public_json)
        ):
            leaks.append("local filesystem path")
        for name in self.redactor.full_names:
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", visible):
                leaks.append(f"person name {name!r}")
        for i, leak in enumerate(leaks):
            self.add(
                "PUB-014",
                "BLOCK",
                "target",
                public["target"]["id"],
                f"Internal or personal data found in the public artifacts: {leak}.",
                fld=str(i),
            )
        self.steps.append(
            StepResult(name="Remove internal run metadata", result="PASS" if not leaks else f"{len(leaks)} leaks")
        )

    def _check_notices(self, public: dict, html: str) -> None:
        missing = []
        for key in ("independence", "third_party_content"):
            if str(escape(public["notices"][key])) not in html:
                missing.append(key)
        for key in missing:
            self.add(
                "PUB-015" if key == "independence" else "PUB-016",
                "BLOCK",
                "target",
                public["target"]["id"],
                f"The rendered report is missing the {key.replace('_', ' ')} notice.",
                fld=key,
            )
        self.steps.append(StepResult(name="Verify publication notices", result="PASS" if not missing else "FAIL"))
