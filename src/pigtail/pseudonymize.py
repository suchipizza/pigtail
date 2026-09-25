"""Keyed pseudonymization of handles and stripping of direct identifiers (PRD §10).

Handles are replaced with a keyed HMAC-SHA256 pseudonym. The key (`PSEUDONYM_KEY`) is stored
separately from the data, so pseudonyms are stable across runs but not reversible without it.
"""

from __future__ import annotations

import hashlib
import hmac
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# @handle mentions (GitHub, X, Bluesky style). Requires a non-word char (or start) before '@'
# so that e-mail addresses (already replaced) and package scopes like `npm i @scope/pkg` are
# handled separately.
_MENTION = re.compile(
    r"(?<![\w/@.])@([A-Za-z0-9][A-Za-z0-9_.-]{0,62}[A-Za-z0-9_]|[A-Za-z0-9])(?![\w/])"
)
# Phone numbers only in unambiguous forms: international "+<digits>" or labelled ("tel:",
# "phone:", "call"). Bare digit runs are left alone because dates, counts, timestamps and DOIs
# must reach the model intact.
_PHONE = re.compile(
    r"(?<![\w/+])\+\d{1,3}(?:[ .\-]?\(?\d{1,4}\)?){2,6}(?![\w/])"
    r"|(?i:\b(?:tel|phone|mobile|call)\b[:.]?\s*)\(?\d[\d \-().]{6,}\d"
)


class Pseudonymizer:
    def __init__(self, key: str) -> None:
        if not key or len(key) < 16:
            raise ValueError("PSEUDONYM_KEY must be set and at least 16 characters")
        self._key = key.encode()

    def pseudonym(self, handle: str, namespace: str = "generic") -> str:
        """Stable pseudonym for a handle within a namespace (e.g. 'github', 'hn')."""
        norm = f"{namespace}:{handle.strip().lstrip('@').lower()}".encode()
        return "p_" + hmac.new(self._key, norm, hashlib.sha256).hexdigest()[:16]

    def strip_identifiers(self, text: str, namespace: str = "generic") -> str:
        """Replace e-mails and phone numbers with placeholders and @mentions with pseudonyms."""
        text = _EMAIL.sub("[email]", text)
        text = _PHONE.sub("[phone]", text)
        return _MENTION.sub(lambda m: "@" + self.pseudonym(m.group(1), namespace), text)
