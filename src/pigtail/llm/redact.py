"""Redaction on the LLM path: per-call, non-keyed aliases (PRD §10, DPIA CB-06; ADR-066
follow-up, M21b).

Every product LLM input goes through `alias_redact` before it leaves the process. It applies the
same identifier rules as `pigtail.pseudonymize.redact_identifiers` (profile URLs, DIDs, e-mails,
phone numbers, @mentions), but a person is replaced by an **alias local to that one input**:
`@user1`, `@user2`, ... in the order they are first redacted (profile URLs become
`[profile:github:user1]`, DIDs `[did:user2]`). The same handle within one input gets the same
alias (per source namespace), so the model can still follow a thread; there is **no key**, and
aliases are not stable across calls, so neither prompts nor cached outputs carry a keyed,
linkable token (Directive §8.1, ADR-066.1, ADR-074: no handles or pseudonyms stored). This is the
only alias implementation: `OptoutKey.strip_identifiers` delegates here.

`REDACTION_VERSION` is recorded with every output's provenance. Cached outputs written before
this change may quote keyed `@p_…` tokens: clear them once with `pigtail llm cache clear --all
--yes` (operator guide, "LLM backend").
"""

from __future__ import annotations

from pigtail.pseudonymize import redact_identifiers

REDACTION_VERSION = "alias-v1"


def alias_redact(text: str, namespace: str = "generic") -> str:
    """Redact identifiers in `text`, replacing each person with a per-call alias `userN`."""
    aliases: dict[tuple[str, str], str] = {}

    def alias(handle: str, ns: str) -> str:
        key = (ns, handle.strip().lstrip("@").lower())
        if key not in aliases:
            aliases[key] = f"user{len(aliases) + 1}"
        return aliases[key]

    return redact_identifiers(text, alias, namespace)
