"""Evidence trimming before an LLM call (PRD R15.10; Directive §6.3, ADR-064.3).

Three operations, applied in this order by `trim_evidence` (and usable one by one):

1. **Deduplicate reposts** (`dedupe_reposts`): items whose normalised text (case, whitespace,
   URL query strings and tracking fragments, punctuation) is identical, or whose word 3-shingle
   Jaccard similarity is at least `near_dup_jaccard`, are sent once; the first occurrence is
   kept and the report counts the rest.
2. **Truncate long threads** (`truncate_thread`): a thread keeps its first `head` and last
   `tail` items plus a marker saying how many were left out; each item is cut to
   `max_item_chars` on a word boundary.
3. **Relevant excerpts** (`relevant_excerpts`): when an item is still longer than
   `excerpt_above_chars` and relevance terms are given (the brief's project and field words, the
   case's repo name, ...), only windows of `window_chars` around term matches are kept, merged
   when they overlap, joined with an ellipsis marker. Without a match the item's head is kept.

Finally the whole text is capped at `max_total_chars`. Everything is deterministic, so the
trimmed text (and hence the LLM cache key) is stable across runs, and `TRIM_VERSION` is recorded
with each output's provenance. The report holds counts only, never content.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

TRIM_VERSION = "trim-v1"
OMITTED = "[… {n} item(s) omitted …]"
ELLIPSIS = " […] "

_URL = re.compile(r"https?://\S+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class TrimPolicy:
    max_item_chars: int = 4_000
    head: int = 20
    tail: int = 10
    excerpt_above_chars: int = 2_000
    window_chars: int = 400
    max_excerpts: int = 6
    near_dup_jaccard: float = 0.85
    max_total_chars: int = 60_000

    def __post_init__(self) -> None:
        if self.head < 1 or self.tail < 0 or self.max_item_chars < 100:
            raise ValueError("trim policy: head >= 1, tail >= 0, max_item_chars >= 100")
        if not 0 < self.near_dup_jaccard <= 1:
            raise ValueError("trim policy: near_dup_jaccard must be in (0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {"version": TRIM_VERSION, **self.__dict__}


@dataclass
class TrimReport:
    items_in: int = 0
    items_out: int = 0
    duplicates_dropped: int = 0
    thread_items_omitted: int = 0
    items_cut: int = 0
    items_excerpted: int = 0
    chars_in: int = 0
    chars_out: int = 0
    version: str = TRIM_VERSION

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Trimmed:
    text: str
    report: TrimReport = field(default_factory=TrimReport)


def normalize(text: str) -> str:
    """Text for duplicate detection: URLs without query/fragment, no punctuation, lower case."""
    t = _URL.sub(lambda m: m.group(0).split("?")[0].split("#")[0], text)
    t = _PUNCT.sub(" ", t.lower())
    return _WS.sub(" ", t).strip()


def _shingles(norm: str, k: int = 3) -> set[str]:
    words = norm.split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def dedupe_reposts(
    items: Sequence[str], *, near_dup_jaccard: float = 0.85
) -> tuple[list[str], int]:
    """Keep the first of each group of identical or near-identical items; return (kept, dropped)."""
    kept: list[str] = []
    seen_exact: set[str] = set()
    seen_shingles: list[set[str]] = []
    dropped = 0
    for item in items:
        norm = normalize(item)
        digest = hashlib.sha256(norm.encode()).hexdigest()
        if not norm or digest in seen_exact:
            dropped += 1
            continue
        sh = _shingles(norm)
        if any(jaccard(sh, other) >= near_dup_jaccard for other in seen_shingles):
            dropped += 1
            continue
        seen_exact.add(digest)
        seen_shingles.append(sh)
        kept.append(item)
    return kept, dropped


def cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut on a word boundary, with an ellipsis when cut."""
    if len(text) <= limit:
        return text
    head = text[: max(0, limit - 1)]
    space = head.rfind(" ")
    if space > limit // 2:
        head = head[:space]
    return head.rstrip() + "…"


def truncate_thread(items: Sequence[str], *, head: int, tail: int) -> tuple[list[str], int]:
    """First `head` and last `tail` items with an omission marker; return (items, omitted)."""
    if len(items) <= head + tail:
        return list(items), 0
    omitted = len(items) - head - tail
    tail_items = list(items[len(items) - tail :]) if tail else []
    return [*items[:head], OMITTED.format(n=omitted), *tail_items], omitted


def relevant_excerpts(
    text: str, terms: Iterable[str], *, window_chars: int = 400, max_excerpts: int = 6
) -> str | None:
    """Windows around case-insensitive term matches, merged and joined; None without a match."""
    pats = [re.escape(t.strip()) for t in terms if t and t.strip()]
    if not pats:
        return None
    rx = re.compile("|".join(sorted(set(pats), key=len, reverse=True)), re.IGNORECASE)
    half = window_chars // 2
    spans: list[list[int]] = []
    for m in rx.finditer(text):
        s, e = max(0, m.start() - half), min(len(text), m.end() + half)
        if spans and s <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
        if len(spans) > max_excerpts:
            break
    if not spans:
        return None
    parts = [text[s:e].strip() for s, e in spans[:max_excerpts]]
    prefix = "" if spans[0][0] == 0 else "[…] "
    suffix = "" if spans[min(len(spans), max_excerpts) - 1][1] >= len(text) else " […]"
    return prefix + ELLIPSIS.join(parts) + suffix


def trim_evidence(
    items: Sequence[str],
    *,
    terms: Iterable[str] = (),
    policy: TrimPolicy | None = None,
    separator: str = "\n\n---\n\n",
) -> Trimmed:
    """Trim a thread or a list of evidence excerpts for one call (see the module docstring)."""
    p = policy or TrimPolicy()
    terms = list(terms)
    rep = TrimReport(items_in=len(items), chars_in=sum(len(i) for i in items))
    kept, rep.duplicates_dropped = dedupe_reposts(items, near_dup_jaccard=p.near_dup_jaccard)
    kept, rep.thread_items_omitted = truncate_thread(kept, head=p.head, tail=p.tail)
    out: list[str] = []
    for item in kept:
        if item.startswith("[… ") and item.endswith(" omitted …]"):
            out.append(item)
            continue
        text = item
        if len(text) > p.excerpt_above_chars and terms:
            ex = relevant_excerpts(
                text, terms, window_chars=p.window_chars, max_excerpts=p.max_excerpts
            )
            if ex is not None and len(ex) < len(text):
                text = ex
                rep.items_excerpted += 1
        if len(text) > p.max_item_chars:
            text = cut(text, p.max_item_chars)
            rep.items_cut += 1
        out.append(text)
    joined = separator.join(out)
    if len(joined) > p.max_total_chars:
        joined = cut(joined, p.max_total_chars)
    rep.items_out = len(out)
    rep.chars_out = len(joined)
    return Trimmed(joined, rep)


def trim_text(text: str, *, terms: Iterable[str] = (), policy: TrimPolicy | None = None) -> Trimmed:
    """Trim one evidence text (a single item: excerpts and length cap only)."""
    return trim_evidence([text], terms=terms, policy=policy)
