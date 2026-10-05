#!/usr/bin/env python3
"""Prepare a reviewed example under examples/reviewed/<slug>/ from a run directory (spec §28).

    uv run python scripts/publish_example.py <run-dir> <slug> --title T --description D [--featured]

Publication rules (PRD §12.5, spec §47): negative-sensitive claims/events that are not
`human_verified` are removed, together with anything that depended only on them. Nothing is marked
as human-verified by this script; that is a human decision recorded by editing the bundle.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pigtail.bundle.models import ResearchBundle
from pigtail.bundle.validator import validate_data
from pigtail.bundle.writer import dump_json, source_index
from pigtail.renderer.render import render_html

ROOT = Path(__file__).resolve().parents[1]

CLAIM_LIST_COLLECTIONS = (
    "people",
    "repositories",
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
    "conflicts",
)


def prune_unreviewed_negative(b: dict) -> dict[str, int]:
    removed = {"claims": 0, "events": 0}
    bad_claims = {c["id"] for c in b["claims"] if c["is_negative_sensitive"] and c["review_state"] != "human_verified"}
    bad_events = {e["id"] for e in b["events"] if e["is_negative_sensitive"] and e["review_state"] != "human_verified"}
    # Events resting only on removed claims go too.
    for e in b["events"]:
        if e["claim_ids"] and all(c in bad_claims for c in e["claim_ids"]):
            bad_events.add(e["id"])
    removed["claims"], removed["events"] = len(bad_claims), len(bad_events)
    b["claims"] = [c for c in b["claims"] if c["id"] not in bad_claims]
    b["events"] = [e for e in b["events"] if e["id"] not in bad_events]
    b["evidence_links"] = [
        el
        for el in b["evidence_links"]
        if el["claim_id"] not in bad_claims
        and not (el["target_ref"]["type"] == "event" and el["target_ref"]["id"] in bad_events)
    ]
    for coll in CLAIM_LIST_COLLECTIONS:
        for o in b[coll]:
            if "claim_ids" in o:
                o["claim_ids"] = [c for c in o["claim_ids"] if c not in bad_claims]
    for le in b["launch_episodes"]:
        le["event_ids"] = [i for i in le["event_ids"] if i not in bad_events]
    b["launch_episodes"] = [le for le in b["launch_episodes"] if le["event_ids"] and le["claim_ids"]]
    live_le = {le["id"] for le in b["launch_episodes"]}
    for e in b["events"]:
        if e["launch_episode_id"] not in live_le:
            e["launch_episode_id"] = None
    for g in b["growth_episodes"]:
        g["related_event_ids"] = [i for i in g["related_event_ids"] if i not in bad_events]
    for to in b["tactic_occurrences"]:
        if to["event_id"] in bad_events:
            to["event_id"] = None
    for o in b["outcomes"]:
        if o["event_id"] in bad_events:
            o["event_id"] = None
    # Drop interpretive objects left without support.
    for coll in (
        "metric_snapshots",
        "company_stages",
        "strategy_phases",
        "tactics",
        "tactic_occurrences",
        "growth_engines",
        "growth_engine_occurrences",
        "outcomes",
        "prerequisites",
        "constraints",
    ):
        b[coll] = [o for o in b[coll] if o["claim_ids"]]
    live = {
        k: {o["id"] for o in b[k]}
        for k in ("tactics", "growth_engines", "outcomes", "tactic_occurrences", "metric_snapshots", "events")
    }
    b["tactic_occurrences"] = [o for o in b["tactic_occurrences"] if o["tactic_id"] in live["tactics"]]
    b["growth_engine_occurrences"] = [
        o for o in b["growth_engine_occurrences"] if o["growth_engine_id"] in live["growth_engines"]
    ]
    live["tactic_occurrences"] = {o["id"] for o in b["tactic_occurrences"]}
    for e in b["events"]:
        e["outcome_ids"] = [i for i in e["outcome_ids"] if i in live["outcomes"]]
        e["tactic_occurrence_ids"] = [i for i in e["tactic_occurrence_ids"] if i in live["tactic_occurrences"]]
        e["metric_snapshot_ids"] = [i for i in e["metric_snapshot_ids"] if i in live["metric_snapshots"]]
    for o in b["outcomes"]:
        o["metric_snapshot_ids"] = [i for i in o["metric_snapshot_ids"] if i in live["metric_snapshots"]]
        if o["tactic_occurrence_id"] not in live["tactic_occurrences"]:
            o["tactic_occurrence_id"] = None
    for go in b["growth_engine_occurrences"]:
        go["tactic_occurrence_ids"] = [i for i in go["tactic_occurrence_ids"] if i in live["tactic_occurrences"]]
        go["outcome_ids"] = [i for i in go["outcome_ids"] if i in live["outcomes"]]
    for to in b["tactic_occurrences"]:
        to["outcome_ids"] = [i for i in to["outcome_ids"] if i in live["outcomes"]]
    for g in b["growth_episodes"]:
        for k in ("start_metric_id", "end_metric_id"):
            if g[k] not in live["metric_snapshots"]:
                g[k] = None
    b["conflicts"] = [c for c in b["conflicts"] if len(c["claim_ids"]) >= 2]
    live_ids = {o["id"] for k in b if isinstance(b[k], list) for o in b[k] if isinstance(o, dict) and "id" in o}
    live_ids.add(b["target"]["id"])
    for coll in ("conflicts", "gaps"):
        for o in b[coll]:
            o["related_refs"] = [r for r in o["related_refs"] if r["id"] in live_ids]
    for coll in ("prerequisites", "constraints"):
        for o in b[coll]:
            o["applies_to_refs"] = [r for r in o["applies_to_refs"] if r["id"] in live_ids]
    for g in b["growth_episodes"]:
        if not g["related_event_ids"]:
            g["causal_attribution"] = "unknown"
    n = b["narrative"]
    for k in ("thirty_second", "origin", "first_users", "flywheel", "did_differently"):
        if n.get(k):
            n[k]["claim_ids"] = [c for c in n[k]["claim_ids"] if c not in bad_claims]
            if not n[k]["claim_ids"]:
                n[k] = None
    n["key_takeaways"] = [t for t in n["key_takeaways"] if [c for c in t["claim_ids"] if c not in bad_claims]]
    for t in n["key_takeaways"]:
        t["claim_ids"] = [c for c in t["claim_ids"] if c not in bad_claims]
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("slug")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", required=True)
    ap.add_argument("--featured", action="store_true")
    a = ap.parse_args()
    b = json.loads((a.run_dir / "research-bundle.json").read_text())
    removed = prune_unreviewed_negative(b)
    rep = validate_data(b, published=True)
    if not rep.ok:
        print("Not publishable:\n" + "\n".join(rep.errors[:30]))
        return 1
    out = ROOT / "examples" / "reviewed" / a.slug
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    bundle = ResearchBundle.model_validate(b)
    (out / "research-bundle.json").write_text(dump_json(bundle.model_dump(mode="json")))
    (out / "report.html").write_text(render_html(bundle))
    (out / "source-index.json").write_text(dump_json(source_index(bundle)))
    meta = {
        "slug": a.slug,
        "title": a.title,
        "description": a.description,
        "kind": b["target"]["kind"],
        "featured": a.featured,
        "published_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bundle_schema_version": b["schema_version"],
    }
    (out / "metadata.json").write_text(dump_json(meta))
    print(
        f"published {a.slug}: removed {removed['claims']} sensitive claim(s), {removed['events']} event(s); "
        f"{len(rep.warnings)} warning(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
