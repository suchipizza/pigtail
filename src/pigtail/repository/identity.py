"""GitHub repository identity parsing."""

from __future__ import annotations

import re

_GH = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})(?:[/#?].*)?$")
_SHORT = re.compile(r"^([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$")


def parse_github(raw: str) -> tuple[str, str] | None:
    s = raw.strip()
    m = _GH.match(s)
    if m:
        repo = m.group(2)
        repo = repo[:-4] if repo.endswith(".git") else repo
        return m.group(1), repo
    return None


def parse_short(raw: str) -> tuple[str, str] | None:
    m = _SHORT.match(raw.strip())
    return (m.group(1), m.group(2)) if m and "." not in m.group(1) else None
