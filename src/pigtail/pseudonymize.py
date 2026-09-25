"""Keyed pseudonymization of handles and stripping of direct identifiers (PRD §10).

Handles are replaced with a keyed HMAC-SHA256 pseudonym. The key (`PSEUDONYM_KEY`) is stored
separately from the data, so pseudonyms are stable across runs but not reversible without it.

`strip_identifiers()` runs before every LLM call (ADR-006) and redacts (DPIA CB-06):

- e-mail addresses -> `[email]`; phone numbers in unambiguous forms -> `[phone]`;
- profile URLs -> `[profile:<platform>:<pseudonym>]`: `github.com/<login>` (a single path
  segment that is not a reserved GitHub page; `github.com/<owner>/<repo>` is kept),
  `github.com/sponsors/<login>`, `api.github.com/users/<login>`, `bsky.app/profile/<handle|did>`
  (the rest of the path, e.g. `/post/<rkey>`, is kept) and
  `news.ycombinator.com/{user,submitted,threads,favorites}?id=<name>`;
- AT Protocol identifiers `did:plc:…` and `did:web:…` -> `[did:<pseudonym>]`;
- `@mentions` -> `@<pseudonym>` in the caller's namespace (per source, e.g. "github", "hn").

A profile URL names its platform, so its pseudonym always uses that platform's namespace
(`PLATFORM_NAMESPACES`), whatever namespace the caller passed. Dates, counts, versions and DOIs
are left intact. `scrub_identifiers()` applies the same rules without a key (placeholders only)
for logs and error text (CB-18).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable

# Namespace per platform: the same handle on two platforms may be two different people.
PLATFORM_NAMESPACES: dict[str, str] = {
    "github": "github",
    "bluesky": "bluesky",
    "hn": "hn",
    "v2ex": "v2ex",
}

PSEUDONYM_RE = re.compile(r"^p_[0-9a-f]{16}$")

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

# URL-ish tail: stops at whitespace, quotes and closing brackets.
_URL_TAIL = r"[^\s<>\"'`)\]}]*"
_GITHUB_URL = re.compile(
    r"(?<![\w.@-])(?P<prefix>(?:https?://)?(?:www\.)?github\.com/)(?P<path>" + _URL_TAIL + ")",
    re.IGNORECASE,
)
_GITHUB_API_USER = re.compile(
    r"(?<![\w.@-])(?:https?://)?api\.github\.com/users/"
    r"(?P<login>[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})(?![A-Za-z0-9-])",
    re.IGNORECASE,
)
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
# Top-level github.com paths that are site pages, not accounts.
_GITHUB_RESERVED = frozenset(
    [
        "about",
        "account",
        "apps",
        "blog",
        "codespaces",
        "collections",
        "contact",
        "copilot",
        "customer-stories",
        "dashboard",
        "enterprise",
        "events",
        "explore",
        "features",
        "git-guides",
        "issues",
        "join",
        "login",
        "logout",
        "marketplace",
        "mobile",
        "models",
        "new",
        "notifications",
        "organizations",
        "orgs",
        "partners",
        "pricing",
        "pulls",
        "readme",
        "resources",
        "search",
        "security",
        "settings",
        "signup",
        "site",
        "solutions",
        "sponsors",
        "stars",
        "team",
        "topics",
        "trending",
        "users",
        "watching",
        "why-github",
    ]
)
# Two-segment paths whose second segment is an account (profile pages).
_GITHUB_ACCOUNT_PAGES = frozenset({"sponsors", "stars"})
_TRAILING_PUNCT = ".,;:!?"

_BSKY_PROFILE = re.compile(
    r"(?<![\w.@-])(?:https?://)?bsky\.app/profile/(?P<h>[A-Za-z0-9._:%-]+)", re.IGNORECASE
)
_HN_PROFILE = re.compile(
    r"(?<![\w.@-])(?:https?://)?news\.ycombinator\.com/"
    r"(?:user|submitted|threads|favorites)\?id=(?P<h>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_DID = re.compile(
    r"\bdid:(?:plc:[a-z2-7]{24}|web:[A-Za-z0-9.-]*[A-Za-z0-9](?::[A-Za-z0-9._%-]*[A-Za-z0-9])*)"
    r"(?![\w-])"
)

# (handle, namespace) -> pseudonym, or None for a keyless placeholder.
PseudoFn = Callable[[str, str], str | None]


def _profile(ns: str, p: str | None) -> str:
    return f"[profile:{ns}:{p}]" if p else f"[profile:{ns}]"


def _github(m: re.Match[str], pseudo: PseudoFn) -> str:
    path = m.group("path")
    stripped = path.rstrip(_TRAILING_PUNCT)
    trail = path[len(stripped) :]
    route = re.split(r"[?#]", stripped, maxsplit=1)[0]
    segs = [s for s in route.split("/") if s]
    login: str | None = None
    if len(segs) == 1 and segs[0].lower() not in _GITHUB_RESERVED:
        login = segs[0]
    elif len(segs) == 2 and segs[0].lower() in _GITHUB_ACCOUNT_PAGES:
        login = segs[1]
    if login is None or not _GITHUB_LOGIN.match(login):
        return m.group(0)  # repo path, site page or not a login: keep
    return _profile("github", pseudo(login, PLATFORM_NAMESPACES["github"])) + trail


def _bsky(m: re.Match[str], pseudo: PseudoFn) -> str:
    h = m.group("h")
    handle = h.rstrip(_TRAILING_PUNCT + "-")
    p = pseudo(handle, PLATFORM_NAMESPACES["bluesky"])
    return _profile("bluesky", p) + h[len(handle) :]


def redact_identifiers(text: str, pseudo: PseudoFn, namespace: str = "generic") -> str:
    """Apply every CB-06 rule; `pseudo` renders handles (keyed pseudonym or placeholder)."""
    bsky = PLATFORM_NAMESPACES["bluesky"]
    text = _BSKY_PROFILE.sub(lambda m: _bsky(m, pseudo), text)
    text = _HN_PROFILE.sub(
        lambda m: _profile("hn", pseudo(m.group("h"), PLATFORM_NAMESPACES["hn"])), text
    )
    text = _GITHUB_API_USER.sub(
        lambda m: _profile("github", pseudo(m.group("login"), PLATFORM_NAMESPACES["github"])),
        text,
    )
    text = _GITHUB_URL.sub(lambda m: _github(m, pseudo), text)
    text = _DID.sub(lambda m: f"[did:{p}]" if (p := pseudo(m.group(0), bsky)) else "[did]", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return _MENTION.sub(
        lambda m: "@" + (pseudo(m.group(1), namespace) or "[handle]"),
        text,
    )


def scrub_identifiers(text: str) -> str:
    """Keyless redaction for logs and error text (CB-18): same rules, placeholders only."""
    return redact_identifiers(text, lambda _h, _ns: None)


class Pseudonymizer:
    def __init__(self, key: str) -> None:
        if not key or len(key) < 16:
            raise ValueError("PSEUDONYM_KEY must be set and at least 16 characters")
        self._key = key.encode()

    def pseudonym(self, handle: str, namespace: str = "generic") -> str:
        """Stable pseudonym for a handle within a namespace (e.g. 'github', 'hn')."""
        norm = f"{namespace}:{handle.strip().lstrip('@').lower()}".encode()
        return "p_" + hmac.new(self._key, norm, hashlib.sha256).hexdigest()[:16]

    def keyed_hex(self, value: str, namespace: str) -> str:
        """Full HMAC-SHA256 hex of `namespace:value` (no normalization: the caller normalizes).

        Used for keyed lookup keys that are not handles, e.g. repo-name opt-outs (CB-13b)."""
        return hmac.new(self._key, f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()

    def strip_identifiers(self, text: str, namespace: str = "generic") -> str:
        """Redact e-mails, phones, profile URLs, DIDs and @mentions before an LLM call (CB-06).

        `namespace` is the source's pseudonym namespace for @mentions (e.g. "github").
        """
        return redact_identifiers(text, self.pseudonym, namespace)
