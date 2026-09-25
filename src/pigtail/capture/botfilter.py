"""Bot filter v0 (R1.1). Heuristic and deliberately conservative; versioned as BOT_FILTER_VERSION.

Two layers:

1. **Login rules** (applied to the raw login *before* pseudonymization, in the connector):
   the actor is a bot if the login ends with `[bot]` (GitHub Apps), or matches a known automation
   account / naming pattern (`KNOWN_BOTS`, `-bot`/`_bot` suffix, `bot-`/`bot_` prefix). Events
   with no actor login are treated like bot events. Bot events are counted (`stars_bot`) and
   excluded from filtered counts; their logins are neither stored nor hashed.

2. **Lockstep bursts** (applied per repo-hour, on pseudonyms, in `pigtail.capture.velocity`):
   a *star-only* actor is one whose every event in the filter window (the scan chunk: the UTC day
   intersected with the requested range) is a WatchEvent. A repo-hour is flagged when it has at
   least `lockstep_min_stars` star-only actors **and** they make up at least `lockstep_share` of
   that repo-hour's distinct stargazers. In a flagged repo-hour the star-only actors' stars are
   removed (`stars_lockstep`); otherwise nothing is removed. Organic stargazers who do nothing
   else that day are common, which is why a high share *and* a minimum size are both required.

Known limitations: farms that also emit other events evade layer 2; a real burst of first-time
GitHub users can be flagged. Both raw and filtered counts are stored so the filter can be
re-calibrated without re-downloading.
"""

from __future__ import annotations

import re

BOT_FILTER_VERSION = "bot-filter-v0"

KNOWN_BOTS = frozenset(
    {
        "dependabot",
        "dependabot-preview",
        "renovate",
        "renovate-bot",
        "greenkeeper",
        "github-actions",
        "codecov-io",
        "codecov",
        "imgbot",
        "snyk-bot",
        "allcontributors",
        "pre-commit-ci",
        "mergify",
        "gitter-badger",
    }
)

_PATTERN = re.compile(r"(\[bot\]$)|([-_]bot$)|(^bot[-_])|(^bot$)", re.IGNORECASE)


def is_bot_login(login: str) -> bool:
    lo = login.strip().lower()
    return bool(_PATTERN.search(lo)) or lo in KNOWN_BOTS
