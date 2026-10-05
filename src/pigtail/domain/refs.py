"""Object reference types (spec §6.1)."""

from __future__ import annotations

OBJECT_REF_TYPES = (
    "target",
    "person",
    "repository",
    "source",
    "claim",
    "event",
    "launch_episode",
    "growth_episode",
    "metric_snapshot",
    "company_stage",
    "strategy_phase",
    "surface",
    "surface_presence",
    "tactic",
    "tactic_occurrence",
    "growth_engine",
    "growth_engine_occurrence",
    "outcome",
    "prerequisite",
    "constraint",
    "conflict",
    "gap",
)

# ObjectRef.type -> top-level bundle collection
REF_COLLECTION = {
    "person": "people",
    "repository": "repositories",
    "source": "sources",
    "claim": "claims",
    "event": "events",
    "launch_episode": "launch_episodes",
    "growth_episode": "growth_episodes",
    "metric_snapshot": "metric_snapshots",
    "company_stage": "company_stages",
    "strategy_phase": "strategy_phases",
    "surface": "surfaces",
    "surface_presence": "surface_presences",
    "tactic": "tactics",
    "tactic_occurrence": "tactic_occurrences",
    "growth_engine": "growth_engines",
    "growth_engine_occurrence": "growth_engine_occurrences",
    "outcome": "outcomes",
    "prerequisite": "prerequisites",
    "constraint": "constraints",
    "conflict": "conflicts",
    "gap": "gaps",
}
