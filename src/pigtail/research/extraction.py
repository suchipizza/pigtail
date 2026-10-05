"""Atomic claim extraction from transiently fetched page text.

Every extracted claim must carry a verbatim quote that is found in the page text. Claims whose
quote cannot be located are discarded, which blocks invented facts from entering the bundle.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from pigtail.logging import get_logger
from pigtail.providers.base import ModelProvider, StructuredModelRequest

log = get_logger("extraction")

ClaimKind = Literal[
    "founding",
    "origin",
    "first_users",
    "launch",
    "metric",
    "acquisition_channel",
    "tactic",
    "strategy",
    "product_change",
    "pricing",
    "business_model",
    "funding",
    "team",
    "partnership",
    "press",
    "community",
    "milestone",
    "attribution",
    "positioning",
    "failure_or_reversal",
    "constraint",
    "other",
]


class ExtractedMetric(BaseModel):
    name: str = Field(description="e.g. 'MRR', 'users', 'GitHub stars', 'weekly signups', 'ARR', 'customers'")
    value: float | None = Field(description="Numeric value on a human scale (20700 for $20.7K). Null if not numeric.")
    value_text: str | None = Field(description="Use only when the value is not a single number, e.g. '1.5K–3K'.")
    unit: str = Field(description="count | percent | currency | users | other short unit")
    currency: str | None = Field(description="ISO 4217 code when the metric is money, else null")
    approximate: bool


class ExtractedClaim(BaseModel):
    statement: str = Field(description="One standalone factual sentence naming the subject. No interpretation.")
    kind: ClaimKind
    date: str | None = Field(description="When the fact happened: YYYY, YYYY-MM or YYYY-MM-DD; null if not stated.")
    date_end: str | None = Field(description="End of a period if the fact spans one, same format; else null.")
    date_label: str | None = Field(description="The source's own wording of the time, e.g. 'late 2020', 'year one'.")
    quote: str = Field(
        description="Exact, verbatim text copied from the page that supports the statement (max 300 chars)."
    )
    speaker: Literal["company_or_founder", "third_party", "community", "unknown"]
    evidence_class: Literal["documented", "company_measured", "third_party_measured", "third_party_reported", "unknown"]
    metric: ExtractedMetric | None = None
    is_negative_sensitive: bool = Field(
        description="True for allegations, controversies, legal/security incidents or criticism of people."
    )
    is_allegation_only: bool = Field(description="True if the claim reports an unproven allegation.")
    certainty: float = Field(ge=0, le=1, description="How clearly the quote supports the statement.")


class PageExtraction(BaseModel):
    about_target: bool = Field(description="False if the page is mainly about a different product or entity.")
    page_kind: Literal[
        "first_party",
        "founder_interview",
        "news",
        "third_party_analysis",
        "community",
        "launch_page",
        "directory_or_listing",
        "other",
    ]
    published_date: str | None = Field(description="Publication date of the page if visible, YYYY-MM-DD or YYYY-MM.")
    author: str | None
    claims: list[ExtractedClaim]


SYSTEM = (
    "You extract atomic, verifiable facts about how a specific product or open-source project grew. "
    "Rules: (1) Only facts stated in the page. Never add knowledge from outside the page. "
    "(2) Each claim is one small fact: a date, a number, a launch, a channel, a decision, a stated result. "
    "(3) The quote must be copied character-for-character from the page text. "
    "(4) Dates: use the precision the page gives; never guess a day when only a month is stated; resolve "
    "relative dates ('last month') only when the page's publication date makes them unambiguous, otherwise "
    "leave the date null and put the wording in date_label. "
    "(5) Attribute causes only when the source itself states them (e.g. the founder says a channel drove signups); "
    "use kind='attribution' for those. Never infer causation from timing. "
    "(6) Do not extract claims about unrelated entities, generic advice, or marketing superlatives. "
    "(7) Do not infer misconduct, fraud, intention or motive. "
    "Focus on: origin and founding context, first users and how they were reached, launches (Product Hunt, "
    "Hacker News, Reddit, social), historical metrics (users, revenue, stars, signups) with dates, acquisition "
    "channels, tactics, strategy and pricing changes, positioning, partnerships, funding, team size, "
    "failures/reversals, and constraints."
)

MAX_CHARS = 60_000


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    s = re.sub(r"[*_`#>\[\]]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_text(quote: str, norm_text: str) -> bool:
    q = _norm(quote).strip(" .\"'…")
    if len(q) < 12:
        return False
    if q in norm_text:
        return True
    # Allow an ellipsis-joined quote if every part is present.
    parts = [p.strip(" .") for p in re.split(r"\.\.\.|…", q) if len(p.strip(" .")) >= 12]
    return bool(parts) and all(p in norm_text for p in parts)


@dataclass
class VerifiedClaim:
    claim: ExtractedClaim
    quote: str


def chunk_text(text: str, size: int = MAX_CHARS) -> list[str]:
    if len(text) <= size:
        return [text]
    paras = text.split("\n")
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 > size and cur:
            chunks.append(cur)
            cur = ""
        cur += p + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks[:4]


async def extract_claims(
    *, model: ModelProvider, text: str, url: str, title: str | None, target_desc: str, cutoff: str
) -> tuple[PageExtraction | None, list[VerifiedClaim], int]:
    """Returns (page metadata, verified claims, number of rejected claims)."""
    norm_text = _norm(text)
    verified: list[VerifiedClaim] = []
    rejected = 0
    meta: PageExtraction | None = None
    for part_no, part in enumerate(chunk_text(text)):
        prompt = (
            f"Target: {target_desc}\nResearch cutoff date: {cutoff}\n"
            f"Page URL: {url}\nPage title: {title or '(unknown)'}"
            + (f"\n(Part {part_no + 1} of a long page)" if part_no else "")
            + "\n\n<page_text>\n"
            + part
            + "\n</page_text>\n\n"
            "Extract the growth-relevant factual claims about the target (usually 3–25). "
            "If the page is not about the target, set about_target=false and return no claims."
        )
        res = await model.structured(
            StructuredModelRequest(
                system=SYSTEM,
                prompt=prompt,
                output_type=PageExtraction,
                purpose="claim extraction",
                max_tokens=16000,
                effort="low",
            )
        )
        if meta is None:
            meta = res
        if not res.about_target:
            if part_no == 0:
                return res, [], 0
            continue
        for c in res.claims:
            if c.is_allegation_only:
                rejected += 1
                continue
            if not quote_in_text(c.quote, norm_text):
                rejected += 1
                log.info("dropped claim with unverifiable quote from %s", url)
                continue
            verified.append(VerifiedClaim(claim=c, quote=c.quote.strip()))
    return meta, verified, rejected
