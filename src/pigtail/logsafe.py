"""Log hygiene (DPIA CB-18): no handles, e-mails or long payloads in logs or `runs.error`.

`scrub()` runs the pseudonymizer's redaction rules (`pigtail.pseudonymize`) and truncates the
result. By default it is keyless: handles become placeholders (`@[handle]`, `[profile:github]`),
so logs carry no pseudonyms either. Pass a `Pseudonymizer` to keep keyed pseudonyms instead
(useful for correlating one subject across lines; they are still personal data).

`RedactingFilter` applies `scrub()` to the formatted message, the exception text and stack info
of every record that passes a handler. `configure_logging()` installs it on the root handlers.
"""

from __future__ import annotations

import logging

from pigtail.pseudonymize import Pseudonymizer, scrub_identifiers

MAX_LOG_CHARS = 2000
MAX_TRACE_CHARS = 8000


def scrub(text: str, limit: int = MAX_LOG_CHARS, pz: Pseudonymizer | None = None) -> str:
    """Redact identifiers, then truncate to `limit` characters (CB-18)."""
    out = pz.strip_identifiers(text) if pz is not None else scrub_identifiers(text)
    if len(out) > limit:
        out = f"{out[:limit]}… [truncated {len(out) - limit} chars]"
    return out


class RedactingFilter(logging.Filter):
    """Rewrites each record in place so that no handler ever sees the raw text (CB-18)."""

    def __init__(
        self,
        limit: int = MAX_LOG_CHARS,
        trace_limit: int = MAX_TRACE_CHARS,
        pz: Pseudonymizer | None = None,
    ) -> None:
        super().__init__()
        self.limit = limit
        self.trace_limit = trace_limit
        self.pz = pz
        self._fmt = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_pigtail_scrubbed", False):
            return True
        try:
            msg = record.getMessage()
        except Exception:  # a bad format string must not leak the args either
            msg = str(record.msg)
        record.msg = scrub(msg, self.limit, self.pz)
        record.args = None
        if record.exc_info:
            record.exc_text = scrub(
                self._fmt.formatException(record.exc_info), self.trace_limit, self.pz
            )
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrub(record.exc_text, self.trace_limit, self.pz)
        if record.stack_info:
            record.stack_info = scrub(record.stack_info, self.trace_limit, self.pz)
        record._pigtail_scrubbed = True
        return True


def install(
    logger: logging.Logger | None = None,
    *,
    limit: int = MAX_LOG_CHARS,
    pz: Pseudonymizer | None = None,
) -> RedactingFilter:
    """Attach one `RedactingFilter` to every handler of `logger` (default: root)."""
    target = logger or logging.getLogger()
    flt = RedactingFilter(limit=limit, pz=pz)
    for h in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(flt)
    return flt


def configure_logging(level: int = logging.INFO) -> None:
    """`logging.basicConfig` plus the CB-18 filter on every root handler."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install()
