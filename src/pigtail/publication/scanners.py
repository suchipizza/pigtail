"""Small deterministic classifiers for categories the Bundle does not store as fields (PRD §9.2).

A match can only create a review/drop/block finding. It never grants permission to publish.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from pigtail.domain.time import parse_dt

_I = re.IGNORECASE

# PUB-005: personal finances of identifiable people. Strong matches are dropped; weak matches
# (only counted when a person is in context) go to review.
FINANCE_STRONG = re.compile(
    r"(?<!time )(?<!cost )(?<!tax )\bsavings\b"
    r"|\bsalar(?:y|ies)\b"
    r"|\bunpaid\b"
    r"|\bwithout (?:any |a )?(?:pay|paycheck|income)\b"
    r"|\bliving (?:expenses|costs)\b"
    r"|\bpersonal (?:money|finances?|funds|loans?|debts?|income|wealth)\b"
    r"|\bnet worth\b"
    r"|\bmortgage\b"
    r"|\bpaid (?:themselves|himself|herself|ourselves|myself)\b"
    r"|\bpay(?:ing)? (?:their|his|her|our|my) (?:own )?(?:rent|bills)\b"
    r"|\b(?:can|could) (?:now )?pay (?:the |their |his |her )?rent\b",
    _I,
)
FINANCE_WEAK = re.compile(
    r"\brent\b|\bincome\b|\b(?:can(?:'t|not)?|could(?:n't| not)?) afford\b|\bdonations?\b|\bday job\b"
    r"|\bquit (?:his|her|their|my) job\b|\bdebts?\b|\bloans?\b|\bbroke\b|\bfunding loss\b",
    _I,
)
PERSON_CONTEXT = re.compile(
    r"\b(?:co-?founders?|founders?|maintainers?|creators?|he|she|his|her|him|themselves|himself|herself"
    r"|personally|family|CEO|CTO)\b",
    _I,
)

# PUB-004: intent and motive language; misconduct language.
INTENT = re.compile(
    r"\b(?:intends?|intended|intention|motives?|motivat\w+|wants? to|wanted to|plans? to|planned to|planning to"
    r"|consider(?:s|ed|ing)?|aims? to|aimed to|hopes? to|hoped to|deliberately|purposely|on purpose)\b",
    _I,
)
MISCONDUCT = re.compile(
    r"\b(?:fraud\w*|scam\w*|dishonest\w*|deceiv\w*|deceptive\w*|lied|liars?|cheat(?:ed|ing|s)?|stole|stolen"
    r"|illegal\w*|unlawful\w*|misled|mislead(?:ing)?|embezzl\w*|plagiari\w*(?! (?:detection|checker|checking|check))|brib\w+|astroturf\w*|sock ?puppets?"
    r"|fake (?:reviews|accounts|users|stars|upvotes|votes)|bought (?:stars|upvotes|votes|reviews)"
    r"|vote manipulation|star farming)\b",
    _I,
)

# PUB-009: absolute wording that should stay attributed when it comes from the company itself.
ABSOLUTE = re.compile(
    r"\b(?:never|always|only|no one|nobody|100 ?%|solely|exclusively|entirely|completely|purely|zero"
    r"|first-ever|the first|best|largest|biggest|fastest|most popular|number one)\b|#1\b",
    _I,
)
ATTRIBUTED = re.compile(
    r"\b(?:says?|said|states?|stated|according to|reports?|reported|claims?|claimed|wrote|writes|describes?"
    r"|described|announced|told|tweeted|posted|calls?|called|estimates?|estimated)\b",
    _I,
)

ALLEGATION_KINDS = frozenset({"allegation", "rumor", "rumour", "accusation"})

_MONTH_WORD = re.compile(r"\b(?:jan(?:uary)?|1st|first)\b", _I)


def finance_strong(text: str) -> str | None:
    m = FINANCE_STRONG.search(text or "")
    return m.group(0) if m else None


def finance_weak(text: str) -> str | None:
    """Only meaningful with a person in context; the caller checks that."""
    m = FINANCE_WEAK.search(text or "")
    return m.group(0) if m else None


def intent(text: str) -> str | None:
    m = INTENT.search(text or "")
    return m.group(0) if m else None


def misconduct(text: str) -> str | None:
    m = MISCONDUCT.search(text or "")
    return m.group(0) if m else None


def absolute(text: str) -> str | None:
    m = ABSOLUTE.search(text or "")
    return m.group(0) if m else None


def is_attributed(text: str) -> bool:
    return bool(ATTRIBUTED.search(text or ""))


def has_person_context(text: str) -> bool:
    return bool(PERSON_CONTEXT.search(text or ""))


def suspicious_instant(value: str | None, cutoff: datetime | None) -> str | None:
    """Why a timestamp looks like a placeholder or parser default, or None if it looks fine."""
    dt = parse_dt(value)
    if dt is None:
        return None
    if dt.year < 1995:
        return "it is before 1995"
    if cutoff is not None and dt > cutoff + timedelta(days=1):
        return "it is after the research cutoff"
    if dt.month == 1 and dt.day == 1 and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        return "January 1 at midnight is a common parser default"
    return None


def suspicious_range(tr: dict, cutoff: datetime | None) -> str | None:
    """Like suspicious_instant, for a TimeRange shown with day-or-finer precision."""
    if tr.get("precision") not in ("second", "minute", "hour", "day"):
        return None
    why = suspicious_instant(tr.get("start"), cutoff)
    # A label in the source's own words that names January 1st means the date is real.
    if why and why.startswith("January") and _MONTH_WORD.search(str(tr.get("label") or "")):
        return None
    return why
