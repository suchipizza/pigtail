"""Frozen evidence/confidence vocabularies (spec §6)."""

from __future__ import annotations

from enum import StrEnum


class EvidenceClass(StrEnum):
    documented = "documented"
    company_measured = "company_measured"
    third_party_measured = "third_party_measured"
    third_party_reported = "third_party_reported"
    inferred = "inferred"
    unknown = "unknown"


class ReviewState(StrEnum):
    machine_extracted = "machine_extracted"
    machine_inferred = "machine_inferred"
    human_verified = "human_verified"
    community_verified = "community_verified"


class SourceDirectness(StrEnum):
    primary_direct = "primary_direct"
    primary_indirect = "primary_indirect"
    independent_direct = "independent_direct"
    secondary_synthesis = "secondary_synthesis"
    community_report = "community_report"
    unknown = "unknown"


class Corroboration(StrEnum):
    single_source = "single_source"
    multi_source_consistent = "multi_source_consistent"
    multi_source_mixed = "multi_source_mixed"
    contradicted = "contradicted"
    unknown = "unknown"


class InferenceStrength(StrEnum):
    explicit = "explicit"
    strong_inference = "strong_inference"
    moderate_inference = "moderate_inference"
    weak_inference = "weak_inference"
    not_applicable = "not_applicable"


class CausalAttribution(StrEnum):
    directly_measured = "directly_measured"
    company_attributed = "company_attributed"
    strongly_associated = "strongly_associated"
    weakly_associated = "weakly_associated"
    unknown = "unknown"


CAUSAL_ORDER = [
    CausalAttribution.unknown,
    CausalAttribution.weakly_associated,
    CausalAttribution.strongly_associated,
    CausalAttribution.company_attributed,
    CausalAttribution.directly_measured,
]


def cap_temporal_attribution(value: CausalAttribution) -> CausalAttribution:
    """Temporal proximity alone may never exceed `weakly_associated` (spec §6.8)."""
    if CAUSAL_ORDER.index(value) > CAUSAL_ORDER.index(CausalAttribution.weakly_associated):
        return CausalAttribution.weakly_associated
    return value
