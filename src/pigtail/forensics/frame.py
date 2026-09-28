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
`schemas/` directory. The coder output's JSON Schema is `schemas/coding/v2.0.0.json`
(`output_schema()`; a test keeps the file in sync): a flat list of units (`unit`, `value`,
`unknown_reason`, `evidence_ids`, `excerpts`, `confidence`), each value checked against its
field's type in code (`flatten`; ADR-086 addendum 1). 1.0.0 (nested, one object per field) is
kept for the record: the API refused its compiled grammar as too large.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, TypeAdapter, ValidationError

from pigtail.llm.types import schema_of

FRAME_VERSION = "pilot-frame-v1"
CODEBOOK_VERSION = "0.4.0"
OUTPUT_SCHEMA_VERSION = "2.0.0"  # schemas/coding/v2.0.0.json (flat units)
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
# Output schema 2.0.0 (ADR-086 addendum 1): one flat list of units. The nested 1.0.0 form (one
# object type per field, 12 pattern objects, enums per field) compiled to a grammar the API
# refused as too large (HTTP 400, "The compiled grammar is too large"), so the schema now has
# the shape of the adjudicator's decisions, which the API accepts: `unit` and `value` are plain
# strings, and each unit's value is checked against the field's enum in code after parsing
# (`VALUE_TYPES`); an invalid value becomes `unknown` with reason `schema_invalid`.
class Excerpt(BaseModel):
    """One short attributed excerpt: a verbatim span (<= 300 chars) of the cited item's text."""

    evidence_id: str
    quote: str


class UnitCoding(BaseModel):
    """One coded unit: a case field (`category_primary`, `module_active.ai_hype`,
    `novelty_kind.new_approach`, `pattern.MC-01`, ...) or an item field of one evidence item
    (`reliability@<evidence id>`)."""

    unit: str
    value: str
    unknown_reason: UnknownReason | None
    evidence_ids: list[str]
    excerpts: list[Excerpt]
    confidence: Confidence


class CaseCoding(BaseModel):
    """One coder's coding of one case (pass A or B), flat (output schema 2.0.0)."""

    units: list[UnitCoding]


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
    """The coder output's JSON Schema (`schemas/coding/v2.0.0.json`)."""
    s = schema_of(CaseCoding)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"pigtail/coding/v{OUTPUT_SCHEMA_VERSION}",
        "title": "pigtail case coding (pilot-frame-v1, codebook 0.4.0, flat units)",
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
    confidence: str | None
    excluded: str | None = None  # not_applicable | derived | None
    problems: tuple[str, ...] = ()  # schema problems (schema_invalid)


# The value type of each field (pydantic, checked per unit after parsing; mirrors
# `allowed_values`). A value outside it is `unknown` with reason `schema_invalid`.
SCHEMA_INVALID = "schema_invalid"  # a pipeline reason (like `coding_failed`), never offered
_TYPE_OF_GROUP: dict[str, Any] = {
    "C4": Category | Literal["unknown"],
    "C5": YesNo,
    "novelty_claim": Presence,
    "novelty_kind": YesNo,
    "C11a": Presence,
    "C1": ReliabilityValue,
    "C2": YesNo,
    "C3": EventType,
}
VALUE_TYPES: dict[str, TypeAdapter[Any]] = {
    f.field: TypeAdapter(_TYPE_OF_GROUP[f.group]) for f in FIELDS
}


def value_ok(field: str, value: str) -> bool:
    """Whether `value` is one of `field`'s values (`unknown` included)."""
    try:
        VALUE_TYPES[field].validate_python(value)
    except ValidationError:
        return False
    return True


def expected_units(offered: Sequence[str]) -> list[str]:
    """Every unit key of a case, in frame order: case fields, then per offered evidence id its
    item fields."""
    out = [f.field for f in FIELDS if f.unit == "case"]
    item_fields = [f.field for f in FIELDS if f.unit == "item"]
    for ev in offered:
        out.extend(unit_key(name, ev) for name in item_fields)
    return out


@dataclass(frozen=True)
class Flattened:
    units: list[CodedUnit]
    schema_invalid: int = 0  # units whose value is not in the field's enum
    ignored: tuple[str, ...] = ()  # unit keys not offered (or repeated): dropped, counted


def _unit(field: str, u: UnitCoding, unit: str) -> CodedUnit:
    if not value_ok(field, u.value):
        return CodedUnit(
            unit, field, "unknown", (), (), SCHEMA_INVALID, u.confidence,
            problems=("value_not_allowed",),
        )  # fmt: skip
    return CodedUnit(
        unit=unit,
        field=field,
        value=u.value,
        evidence_ids=tuple(u.evidence_ids),
        excerpts=tuple((e.evidence_id, e.quote) for e in u.excerpts),
        unknown_reason=u.unknown_reason,
        confidence=u.confidence,
    )


def flatten(coding: CaseCoding, offered: tuple[str, ...]) -> Flattened:
    """Every unit of one coding, in frame order, each value checked against its field's type.
    Item fields cover exactly the `offered` evidence ids. A unit the coder left out is
    `unknown` (insufficient_evidence); a unit key not expected, or given twice, is ignored
    (the first one counts) and listed. `novelty_kind.*` is not applicable unless this coder's
    (valid) `novelty_claim` is `present` (§5.1, §10.2)."""
    expected = expected_units(offered)
    wanted = set(expected)
    got: dict[str, UnitCoding] = {}
    ignored: list[str] = []
    for u in coding.units:
        key = u.unit.strip()
        if key not in wanted or key in got:
            ignored.append(key[:80])
            continue
        got[key] = u
    out: list[CodedUnit] = []
    invalid = 0
    claim = got.get("novelty_claim")
    present = claim is not None and claim.value == "present"
    for key in expected:
        field = field_of(key)
        found = got.get(key)
        if field.startswith("novelty_kind.") and not present:
            out.append(
                CodedUnit(key, field, "not_applicable", (), (), None,
                          found.confidence if found else None, excluded="not_applicable")
            )  # fmt: skip
            continue
        if found is None:
            out.append(CodedUnit(key, field, "unknown", (), (), "insufficient_evidence", "low"))
            continue
        cu = _unit(field, found, key)
        invalid += 1 if cu.problems else 0
        out.append(cu)
    return Flattened(out, invalid, tuple(ignored))


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
