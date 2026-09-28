"""Citation validation: snapshot or drop (PRD §5.1, R7.1, R7.6; codebook §11.1-11.3; Directive
§8.1, §8.5; ADR-075.4, ADR-086).

A coded value other than `unknown` (or `not_applicable`) is kept only when:

1. it cites at least one evidence id, and every cited id was offered to that pass;
2. every cited id has exactly one excerpt (the first is kept; later ones for the same item are
   dropped and counted: at most one short attributed excerpt per source, Directive §8.5);
3. every excerpt is at most 300 characters and, after Unicode NFC and whitespace collapsing, is
   an exact substring of the text of the cited item **as that pass saw it** (trimmed and
   redacted in the same call, so aliases match: ADR-075.4, codebook §11.2);
4. no excerpt carries a redaction alias or placeholder of a person (`@user1`, `[profile:…]`,
   `[did…]`, `[email]`, `[phone]`, `[handle]`): outputs name roles and buckets, never people
   (Directive §8.1).

Otherwise the value is dropped and becomes `unknown` with reason `citation_failed` (§11.1). An
`unknown` keeps its reason (default `insufficient_evidence`) and no citations.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from pigtail.forensics.frame import MAX_QUOTE_CHARS, CodedUnit

_WS = re.compile(r"\s+")
PERSON_TOKEN = re.compile(
    r"@user\d+|\[profile:[^\]]*\]|\[did[:\]]|\[email\]|\[phone\]|\[handle\]", re.IGNORECASE
)
SPECIAL = ("unknown", "not_applicable")


def normalize(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


@dataclass(frozen=True)
class Checked:
    unit: CodedUnit
    status: str  # ok | unknown | citation_failed | excluded
    problems: tuple[str, ...] = ()
    excerpts_dropped: int = 0


@dataclass
class CitationStats:
    checked: int = 0
    ok: int = 0
    failed: int = 0
    excerpts_dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def add(self, c: Checked) -> None:
        if c.status == "excluded":
            return
        self.checked += 1
        self.excerpts_dropped += c.excerpts_dropped
        if c.status == "citation_failed":
            self.failed += 1
            for p in c.problems:
                self.reasons[p] = self.reasons.get(p, 0) + 1
        elif c.status == "ok":
            self.ok += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "ok": self.ok,
            "citation_failed": self.failed,
            "excerpts_dropped": self.excerpts_dropped,
            "reasons": dict(sorted(self.reasons.items())),
        }


def check(unit: CodedUnit, texts: Mapping[str, str]) -> Checked:
    """Validate one unit against the item texts (`evidence_id -> text`) its pass saw."""
    if unit.excluded is not None:
        return Checked(unit, "excluded")
    if unit.value in SPECIAL:
        reason = unit.unknown_reason or "insufficient_evidence"
        return Checked(
            replace(unit, evidence_ids=(), excerpts=(), unknown_reason=reason), "unknown"
        )
    problems: list[str] = []
    ids = list(dict.fromkeys(unit.evidence_ids))
    if not ids:
        problems.append("no_evidence_id")
    unknown_ids = [e for e in ids if e not in texts]
    if unknown_ids:
        problems.append("evidence_not_offered")
    kept: dict[str, str] = {}
    dropped = 0
    for ev, quote in unit.excerpts:
        if ev in kept:
            dropped += 1
            continue
        kept[ev] = quote
    for ev in ids:
        q = kept.get(ev)
        if q is None:
            problems.append("missing_excerpt")
            continue
        if ev not in texts:
            continue
        if len(q) > MAX_QUOTE_CHARS or not q.strip():
            problems.append("excerpt_length")
        elif PERSON_TOKEN.search(q):
            problems.append("excerpt_names_person")
        elif normalize(q) not in normalize(texts[ev]):
            problems.append("excerpt_not_found")
    extra = [ev for ev in kept if ev not in ids]
    dropped += len(extra)
    if problems:
        failed = replace(
            unit, value="unknown", evidence_ids=(), excerpts=(), unknown_reason="citation_failed"
        )
        return Checked(failed, "citation_failed", tuple(dict.fromkeys(problems)), dropped)
    clean = replace(
        unit,
        evidence_ids=tuple(ids),
        excerpts=tuple((ev, kept[ev]) for ev in ids),
        unknown_reason=None,
    )
    return Checked(clean, "ok", (), dropped)
