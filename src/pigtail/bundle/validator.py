"""Research Bundle validation: JSON Schema + deterministic integrity rules (spec §21, §31)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from pydantic import ValidationError

from pigtail.bundle.models import COLLECTIONS, SCHEMA_VERSION, ResearchBundle
from pigtail.bundle.schema import load_checked_in_schema
from pigtail.domain.refs import REF_COLLECTION
from pigtail.domain.time import parse_dt

SUPPORTED_VERSIONS = {SCHEMA_VERSION}


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _iter_time_ranges(obj: Any, path: str):
    if isinstance(obj, dict):
        if set(obj.keys()) == {"start", "end", "precision", "label"}:
            yield path, obj
        for k, v in obj.items():
            yield from _iter_time_ranges(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_time_ranges(v, f"{path}[{i}]")


def validate_data(data: dict, *, published: bool = False) -> ValidationReport:
    """Validate a raw bundle dict. `published=True` applies example-publication rules."""
    rep = ValidationReport()
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version not in SUPPORTED_VERSIONS:
        rep.errors.append(f"unsupported schema_version {version!r}; this Pigtail reads {sorted(SUPPORTED_VERSIONS)}")
        return rep

    # 1. Normative JSON Schema (interoperability contract).
    validator = jsonschema.Draft202012Validator(load_checked_in_schema(), format_checker=jsonschema.FormatChecker())
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))[:50]:
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        rep.errors.append(f"schema: {loc}: {err.message[:300]}")
    if rep.errors:
        return rep

    # 2. Runtime model (also checks UUIDv7 + RFC3339 syntax).
    try:
        ResearchBundle.model_validate(data)
    except ValidationError as exc:
        for e in exc.errors()[:50]:
            rep.errors.append(f"model: {'/'.join(str(x) for x in e['loc'])}: {e['msg']}")
        return rep

    _integrity(data, rep, published=published)
    return rep


def _integrity(b: dict, rep: ValidationReport, *, published: bool) -> None:
    E, W = rep.errors.append, rep.warnings.append

    # Index all object IDs.
    index: dict[str, dict[str, dict]] = {c: {o["id"]: o for o in b[c]} for c in COLLECTIONS}
    all_ids = [b["bundle_id"], b["target"]["id"], b["run"]["run_id"]]
    for c in COLLECTIONS:
        all_ids.extend(o["id"] for o in b[c])
    dups = [i for i, n in Counter(all_ids).items() if n > 1]
    for d in dups:
        E(f"duplicate object id {d}")

    def exists(coll: str, oid: str | None) -> bool:
        return oid is not None and oid in index[coll]

    def ref_ok(ref: dict) -> bool:
        if ref["type"] == "target":
            return ref["id"] == b["target"]["id"]
        return ref["id"] in index[REF_COLLECTION[ref["type"]]]

    def check_ids(owner: str, coll: str, ids: list[str]) -> None:
        for i in ids:
            if i not in index[coll]:
                E(f"{owner}: dangling reference to {coll} {i}")

    claims = index["claims"]
    evidence_by_claim: dict[str, list[dict]] = {}
    for el in b["evidence_links"]:
        evidence_by_claim.setdefault(el["claim_id"], []).append(el)

    # Target
    t = b["target"]
    if t["primary_repository_id"] and not exists("repositories", t["primary_repository_id"]):
        E("target.primary_repository_id is dangling")

    # Generic claim_ids references on every object that carries them.
    for coll in COLLECTIONS:
        for o in b[coll]:
            if "claim_ids" in o:
                check_ids(f"{coll}/{o['id']}", "claims", o["claim_ids"])

    # 5. material Claim without EvidenceLink; 6. EvidenceLink → Source/Fetch.
    for c in b["claims"]:
        if not ref_ok(c["subject_ref"]):
            E(f"claim {c['id']}: dangling subject_ref {c['subject_ref']}")
        if c["status"] == "active" and c["id"] not in evidence_by_claim:
            E(f"claim {c['id']}: material claim has no EvidenceLink")
    fetches = index["source_fetches"]
    for el in b["evidence_links"]:
        if el["claim_id"] not in claims:
            E(f"evidence_link {el['id']}: missing claim {el['claim_id']}")
        if el["source_id"] not in index["sources"]:
            E(f"evidence_link {el['id']}: missing source {el['source_id']}")
        f = fetches.get(el["source_fetch_id"])
        if f is None:
            E(f"evidence_link {el['id']}: missing source_fetch {el['source_fetch_id']}")
        elif f["source_id"] != el["source_id"]:
            E(f"evidence_link {el['id']}: source_fetch belongs to a different source")
        if not ref_ok(el["target_ref"]):
            E(f"evidence_link {el['id']}: dangling target_ref {el['target_ref']}")
        src = index["sources"].get(el["source_id"])
        if src and el["excerpt"]:
            pol = src["policy"]
            if pol["retention_mode"] == "metadata_only" or pol["public_display_mode"] == "link_only":
                E(f"evidence_link {el['id']}: excerpt present but source policy forbids excerpts")
    for f in b["source_fetches"]:
        if f["source_id"] not in index["sources"]:
            E(f"source_fetch {f['id']}: missing source")
        # 13. retained artifact while policy disallows full retention
        src = index["sources"].get(f["source_id"])
        if f["retained_artifact_path"] is not None:
            if f["retention_mode"] != "full_permitted" or (src and src["policy"]["retention_mode"] != "full_permitted"):
                E(f"source_fetch {f['id']}: retained artifact while policy disallows full retention")
            if f["retained_artifact_path"].startswith("/") or ".." in f["retained_artifact_path"]:
                E(f"source_fetch {f['id']}: retained_artifact_path must be relative to the bundle")
        if f["status"] != "success":
            W(f"source {f['source_id']}: fetch status {f['status']}")

    # 7. Event has no supporting Claim
    for ev in b["events"]:
        if not ev["claim_ids"]:
            E(f"event {ev['id']}: no supporting claim")
        check_ids(f"event/{ev['id']}", "surfaces", ev["surface_ids"])
        check_ids(f"event/{ev['id']}", "metric_snapshots", ev["metric_snapshot_ids"])
        check_ids(f"event/{ev['id']}", "outcomes", ev["outcome_ids"])
        check_ids(f"event/{ev['id']}", "tactic_occurrences", ev["tactic_occurrence_ids"])
        if ev["launch_episode_id"] and not exists("launch_episodes", ev["launch_episode_id"]):
            E(f"event {ev['id']}: dangling launch_episode_id")
        if published and ev["is_negative_sensitive"] and ev["review_state"] != "human_verified":
            E(f"event {ev['id']}: negative-sensitive event in a published example is not human_verified")

    # 8. TimeRange.end < start
    for path, tr in _iter_time_ranges(b, "$"):
        s, e = parse_dt(tr["start"]), parse_dt(tr["end"])
        if s and e and e < s:
            E(f"{path}: time range ends before it starts")

    # 9. MetricSnapshot numeric xor text
    for m in b["metric_snapshots"]:
        if (m["value_numeric"] is None) == (m["value_text"] is None):
            E(f"metric_snapshot {m['id']}: exactly one of value_numeric/value_text must be set")
        if m["unit"] == "percent" and m["value_numeric"] is not None and not 0 <= m["value_numeric"] <= 100:
            W(f"metric_snapshot {m['id']}: percent outside 0..100")
        if m["repository_id"] and not exists("repositories", m["repository_id"]):
            E(f"metric_snapshot {m['id']}: dangling repository_id")
        if not m["claim_ids"]:
            E(f"metric_snapshot {m['id']}: no supporting claim")

    # 11. GrowthEpisode must reference a repository
    for g in b["growth_episodes"]:
        if not exists("repositories", g["repository_id"]):
            E(f"growth_episode {g['id']}: repository_id does not reference a repository")
        for k in ("start_metric_id", "end_metric_id"):
            if g[k] and not exists("metric_snapshots", g[k]):
                E(f"growth_episode {g['id']}: dangling {k}")
        check_ids(f"growth_episode/{g['id']}", "events", g["related_event_ids"])
        if not g["claim_ids"]:
            E(f"growth_episode {g['id']}: no supporting claim")

    # 12. LaunchEpisode → Events
    for le in b["launch_episodes"]:
        if not le["event_ids"]:
            E(f"launch_episode {le['id']}: groups no events")
        check_ids(f"launch_episode/{le['id']}", "events", le["event_ids"])

    for o in b["outcomes"]:
        if o["event_id"] and not exists("events", o["event_id"]):
            E(f"outcome {o['id']}: dangling event_id")
        if o["tactic_occurrence_id"] and not exists("tactic_occurrences", o["tactic_occurrence_id"]):
            E(f"outcome {o['id']}: dangling tactic_occurrence_id")
        check_ids(f"outcome/{o['id']}", "metric_snapshots", o["metric_snapshot_ids"])
        if not o["claim_ids"]:
            E(f"outcome {o['id']}: no supporting claim")

    # Interpretive objects must rest on lower-level claims (spec §31).
    for coll in (
        "company_stages",
        "strategy_phases",
        "tactics",
        "growth_engines",
        "tactic_occurrences",
        "growth_engine_occurrences",
        "prerequisites",
        "constraints",
    ):
        for o in b[coll]:
            if not o["claim_ids"]:
                E(f"{coll} {o['id']}: inferred object without supporting claims")
    for to in b["tactic_occurrences"]:
        if not exists("tactics", to["tactic_id"]):
            E(f"tactic_occurrence {to['id']}: dangling tactic_id")
        if to["event_id"] and not exists("events", to["event_id"]):
            E(f"tactic_occurrence {to['id']}: dangling event_id")
        if to["strategy_phase_id"] and not exists("strategy_phases", to["strategy_phase_id"]):
            E(f"tactic_occurrence {to['id']}: dangling strategy_phase_id")
        check_ids(f"tactic_occurrence/{to['id']}", "outcomes", to["outcome_ids"])
    for go in b["growth_engine_occurrences"]:
        if not exists("growth_engines", go["growth_engine_id"]):
            E(f"growth_engine_occurrence {go['id']}: dangling growth_engine_id")
        check_ids(f"growth_engine_occurrence/{go['id']}", "strategy_phases", go["strategy_phase_ids"])
        check_ids(f"growth_engine_occurrence/{go['id']}", "tactic_occurrences", go["tactic_occurrence_ids"])
        check_ids(f"growth_engine_occurrence/{go['id']}", "outcomes", go["outcome_ids"])
    for sp in b["surface_presences"]:
        if not exists("surfaces", sp["surface_id"]):
            E(f"surface_presence {sp['id']}: dangling surface_id")
    for coll in ("prerequisites", "constraints"):
        for o in b[coll]:
            for r in o["applies_to_refs"]:
                if not ref_ok(r):
                    E(f"{coll} {o['id']}: dangling applies_to_ref {r}")
    for coll in ("conflicts", "gaps"):
        for o in b[coll]:
            for r in o["related_refs"]:
                if not ref_ok(r):
                    E(f"{coll} {o['id']}: dangling related_ref {r}")
    for c in b["conflicts"]:
        if len(c["claim_ids"]) < 2:
            W(f"conflict {c['id']}: fewer than two claims")

    # 14. Narrative may only cite claims that exist.
    narr = b["narrative"]
    blocks = [narr.get(k) for k in ("thirty_second", "origin", "first_users", "flywheel", "did_differently")]
    blocks += narr.get("key_takeaways", [])
    for blk in blocks:
        if not blk:
            continue
        for cid in blk["claim_ids"]:
            if cid not in claims:
                E(f"narrative cites claim {cid} that is not in the bundle")
        if not blk["claim_ids"]:
            E("narrative block has no supporting claim ids")

    # 15. Published example: negative-sensitive claims need human review.
    if published:
        for c in b["claims"]:
            if c["is_negative_sensitive"] and c["review_state"] != "human_verified":
                E(f"claim {c['id']}: negative-sensitive claim in a published example is not human_verified")

    # Warnings (spec §21 SHOULD-warn list)
    for r in b["repositories"]:
        if r["star_history_quality"] in ("sampled", "reconstructed"):
            W(f"repository {r['owner']}/{r['name']}: star history is {r['star_history_quality']}")
        if r["star_history_quality"] == "unavailable":
            W(f"repository {r['owner']}/{r['name']}: star history unavailable")
    for coll in ("events", "tactics", "growth_engines", "strategy_phases"):
        if not b[coll]:
            W(f"optional section '{coll}' is empty")
    material = [g for g in b["gaps"] if g["severity"] == "material"]
    if material:
        W(f"{len(material)} material evidence gap(s) recorded")
