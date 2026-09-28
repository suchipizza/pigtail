"""The pilot's coding frame (`pilot-frame-v1`): which codebook 0.4.0 fields are coded per case,
their values, levels and alpha variants, and the structured output the coders return (PRD F6,
F7, R7.1-R7.5; codebook §2.3, §3.4-3.7, §5.1, §7.4, §8, §9, §10, §11; ADR-086).

**What is coded** (project-level evidence only, ADR-073.2):

| Field | Codebook | Unit | Level | alpha |
|---|---|---|---|---|
| `category_primary` | C4, §9 | case | nominal | nominal |
| `module_active.<m>` (6) | C5, §8 | case | nominal (yes/no) | nominal per module |
| `novelty_claim` | §5.1 | case | nominal | nominal |
| `novelty_kind.<k>` (4) | §5.1, split per value (§10.2) | case | nominal (yes/no) | nominal |
| `pattern.<MC-xx>` (12) | C11a, §7.4 | case x pattern | nominal | nominal, pooled and per pattern |
| `reliability` | C1, §2.3 | evidence item | ordinal (high > medium > low) | ordinal + known |
| `first_party` | C2 | evidence item | nominal | nominal |
| `event_type_supported` | C3 | evidence item | nominal | nominal |

Not coded in the pilot (no units without person-level sources or burst derivation): C6/C7 edges,
C8 asset categories, C9/C10 triggers. MC-12 is **derived** from the selection's star anomaly
flag (§7.4: pipeline-set presence, excluded from alpha). `novelty_kind.*` of a coder whose
`novelty_claim` is not `present` is `not_applicable` (excluded, §10.2).

The value enums below mirror `schemas/codebook/v0.4.0.json` (a test checks them against it, as
`pigtail.analysis.params` mirrors the analysis parameters), so the app image doesn't need the
`schemas/` directory. The coder output's JSON Schema is `schemas/coding/v1.0.0.json`
(`output_schema()`; a test keeps the file in sync).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel

from pigtail.llm.types import schema_of

FRAME_VERSION = "pilot-frame-v1"
CODEBOOK_VERSION = "0.4.0"
OUTPUT_SCHEMA_VERSION = "1.0.0"  # schemas/coding/v1.0.0.json
MAX_QUOTE_CHARS = 300  # codebook §11.2

Category = Literal[
    "ai-apps-agents",
    "ml-infra",
    "devtools",
    "web-frontend",
    "backend-libs",
    "data-db",
    "infra-ops",
    "security",
    "self-hosted-apps",
    "mobile-desktop",
    "lang-runtime",
    "crypto-web3",
    "lists-learning",
    "media-games-science",
    "other",
]
UnknownReason = Literal[
    "no_evidence",
    "insufficient_evidence",
    "citation_failed",
    "coverage_gap",
    "unobservable_channel",
    "held",
    "conflicting_evidence",
]
Confidence = Literal["low", "medium", "high"]
YesNo = Literal["yes", "no", "unknown"]
Presence = Literal["present", "absent", "unknown"]
ReliabilityValue = Literal["high", "medium", "low", "unknown"]
EventType = Literal["prep", "launch", "relaunch", "pivot", "none", "unknown"]
NoveltyKind = Literal["new_in_kind", "new_approach", "new_combination", "other"]

MODULES: tuple[str, ...] = (
    "ai_hype",
    "b2b_oss_saas",
    "chinese_ecosystem",
    "cli_devtools",
    "corporate_backed",
    "relaunch_pivot",
)
NOVELTY_KINDS: tuple[str, ...] = get_args(NoveltyKind)
# the seed hypotheses the pilot codes (candidates.md 0.4.0); MC-12 is derived, never coded
CODED_PATTERNS: tuple[str, ...] = (
    "MC-01",
    "MC-02",
    "MC-03",
    "MC-04",
    "MC-05",
    "MC-06",
    "MC-07",
    "MC-08",
    "MC-09",
    "MC-10",
    "MC-11",
    "MC-13",
)
DERIVED_PATTERNS: tuple[str, ...] = ("MC-12",)
RELIABILITY_ORDER: tuple[str, ...] = ("low", "medium", "high")  # ordinal, lowest first


def pattern_attr(pid: str) -> str:
    """`MC-01` -> `mc_01` (the output field name)."""
    return pid.lower().replace("-", "_")


# --- coder output (structured output; every object closed) -------------------------------------
class Excerpt(BaseModel):
    """One short attributed excerpt: a verbatim span (<= 300 chars) of the cited item's text."""

    evidence_id: str
    quote: str


class _Coded(BaseModel):
    evidence_ids: list[str]
    excerpts: list[Excerpt]
    unknown_reason: UnknownReason | None
    confidence: Confidence


class CodedCategory(_Coded):
    value: Category | Literal["unknown"]


class CodedYesNo(_Coded):
    value: YesNo


class CodedPresence(_Coded):
    value: Presence


class CodedReliability(_Coded):
    value: ReliabilityValue


class CodedEventType(_Coded):
    value: EventType


class CodedNoveltyKinds(_Coded):
    """Multi-valued (§5.1): every kind the novelty claim shows; empty unless `present`."""

    value: list[NoveltyKind]


class Modules(BaseModel):
    ai_hype: CodedYesNo
    b2b_oss_saas: CodedYesNo
    chinese_ecosystem: CodedYesNo
    cli_devtools: CodedYesNo
    corporate_backed: CodedYesNo
    relaunch_pivot: CodedYesNo


class Patterns(BaseModel):
    mc_01: CodedPresence
    mc_02: CodedPresence
    mc_03: CodedPresence
    mc_04: CodedPresence
    mc_05: CodedPresence
    mc_06: CodedPresence
    mc_07: CodedPresence
    mc_08: CodedPresence
    mc_09: CodedPresence
    mc_10: CodedPresence
    mc_11: CodedPresence
    mc_13: CodedPresence


class ItemCoding(BaseModel):
    evidence_id: str
    reliability: CodedReliability
    first_party: CodedYesNo
    event_type_supported: CodedEventType


class CaseCoding(BaseModel):
    """One coder's coding of one case (pass A or B)."""

    category_primary: CodedCategory
    modules: Modules
    novelty_claim: CodedPresence
    novelty_kind: CodedNoveltyKinds
    patterns: Patterns
    items: list[ItemCoding]


class Decision(BaseModel):
    """The adjudicator's final value for one disagreeing unit."""

    unit: str
    value: str
    reason: str
    evidence_ids: list[str]
    excerpts: list[Excerpt]
    unknown_reason: UnknownReason | None
    confidence: Confidence


class Adjudication(BaseModel):
    decisions: list[Decision]


def output_schema() -> dict[str, Any]:
    """The coder output's JSON Schema (`schemas/coding/v1.0.0.json`)."""
    s = schema_of(CaseCoding)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "pigtail/coding/v1.0.0",
        "title": "pigtail case coding (pilot-frame-v1, codebook 0.4.0)",
        **s,
    }


# --- fields and units -----------------------------------------------------------------------
FieldLevel = Literal["nominal", "ordinal"]


@dataclass(frozen=True)
class FieldSpec:
    field: str  # e.g. category_primary, module_active.ai_hype, pattern.MC-01, reliability
    codebook: str
    unit: Literal["case", "item"]
    level: FieldLevel
    values: tuple[str, ...]  # substantive values (without unknown)
    group: str  # alpha pooling group (pattern: C11a pooled)

    @property
    def order(self) -> tuple[str, ...] | None:
        return RELIABILITY_ORDER if self.level == "ordinal" else None


def _vals(t: Any) -> tuple[str, ...]:
    out: list[str] = []
    for a in get_args(t):
        out.extend(get_args(a) if get_args(a) else [a])
    return tuple(v for v in out if v != "unknown")


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("category_primary", "C4", "case", "nominal", _vals(Category), "C4"),
    *(
        FieldSpec(f"module_active.{m}", "C5", "case", "nominal", ("yes", "no"), "C5")
        for m in MODULES
    ),
    FieldSpec("novelty_claim", "§5.1", "case", "nominal", ("present", "absent"), "novelty_claim"),
    *(
        FieldSpec(f"novelty_kind.{k}", "§5.1", "case", "nominal", ("yes", "no"), "novelty_kind")
        for k in NOVELTY_KINDS
    ),
    *(
        FieldSpec(f"pattern.{p}", "C11a", "case", "nominal", ("present", "absent"), "C11a")
        for p in CODED_PATTERNS
    ),
    FieldSpec("reliability", "C1", "item", "ordinal", RELIABILITY_ORDER, "C1"),
    FieldSpec("first_party", "C2", "item", "nominal", ("yes", "no"), "C2"),
    FieldSpec(
        "event_type_supported",
        "C3",
        "item",
        "nominal",
        ("prep", "launch", "relaunch", "pivot", "none"),
        "C3",
    ),
)
FIELD_BY_NAME = {f.field: f for f in FIELDS}


def allowed_values(field: str) -> tuple[str, ...]:
    """Every value a coder or the adjudicator may give `field` (unknown included)."""
    return (*FIELD_BY_NAME[field].values, "unknown")


def unit_key(field: str, evidence_id: str | None = None) -> str:
    """Unit key within a case: the field, plus the evidence id for item fields."""
    return f"{field}@{evidence_id}" if evidence_id else field


def field_of(unit: str) -> str:
    return unit.split("@", 1)[0]


@dataclass(frozen=True)
class CodedUnit:
    """One coded value of one pass, flattened (before citation validation)."""

    unit: str
    field: str
    value: str
    evidence_ids: tuple[str, ...]
    excerpts: tuple[tuple[str, str], ...]  # (evidence_id, quote)
    unknown_reason: str | None
    confidence: str
    excluded: str | None = None  # not_applicable | derived | None


def _unit(field: str, c: _Coded, value: str, ev: str | None = None) -> CodedUnit:
    return CodedUnit(
        unit=unit_key(field, ev),
        field=field,
        value=value,
        evidence_ids=tuple(c.evidence_ids),
        excerpts=tuple((e.evidence_id, e.quote) for e in c.excerpts),
        unknown_reason=c.unknown_reason,
        confidence=c.confidence,
    )


def flatten(coding: CaseCoding, offered: tuple[str, ...]) -> Iterator[CodedUnit]:
    """Every unit of one coding, in frame order. Item fields cover exactly the `offered`
    evidence ids: an item the coder left out is `unknown` (insufficient_evidence); items not
    offered are ignored."""
    yield _unit("category_primary", coding.category_primary, coding.category_primary.value)
    for m in MODULES:
        c = getattr(coding.modules, m)
        yield _unit(f"module_active.{m}", c, c.value)
    yield _unit("novelty_claim", coding.novelty_claim, coding.novelty_claim.value)
    present = coding.novelty_claim.value == "present"
    kinds = set(coding.novelty_kind.value)
    for k in NOVELTY_KINDS:
        u = _unit(f"novelty_kind.{k}", coding.novelty_kind, "yes" if k in kinds else "no")
        if not present:
            u = CodedUnit(
                u.unit,
                u.field,
                "not_applicable",
                (),
                (),
                None,
                u.confidence,
                excluded="not_applicable",
            )
        yield u
    for p in CODED_PATTERNS:
        c = getattr(coding.patterns, pattern_attr(p))
        yield _unit(f"pattern.{p}", c, c.value)
    by_ev: dict[str, ItemCoding] = {}
    for it in coding.items:
        by_ev.setdefault(it.evidence_id, it)
    for ev in offered:
        item = by_ev.get(ev)
        for name in ("reliability", "first_party", "event_type_supported"):
            if item is None:
                yield CodedUnit(
                    unit_key(name, ev), name, "unknown", (), (), "insufficient_evidence", "low"
                )
                continue
            c = getattr(item, name)
            yield _unit(name, c, c.value, ev)


def derived_units(star_anomaly_flag: str | None) -> list[CodedUnit]:
    """Pipeline-set values (§7.4): MC-12 from the selection's star anomaly flag."""
    flag = (star_anomaly_flag or "unknown").lower()
    value = {"true": "present", "false": "absent"}.get(flag, "unknown")
    return [
        CodedUnit(
            unit_key("pattern.MC-12"),
            "pattern.MC-12",
            value,
            (),
            (),
            None if value != "unknown" else "insufficient_evidence",
            "high",
            excluded="derived",
        )
    ]
