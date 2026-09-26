"""Log hygiene (DPIA CB-18): no handles, e-mails or long payloads in logs or `runs.error`.

`scrub()` runs the CB-06 redaction rules (`pigtail.pseudonymize.scrub_identifiers`) and truncates
the result. It is always keyless: handles become placeholders (`@[handle]`, `[profile:github]`),
so logs carry no handle, no alias and no keyed token. The opt-out key is never used for log
correlation (Directive §8.1, ADR-066.1, ADR-074).

`RedactingFilter` applies `scrub()` to the formatted message, the exception text and stack info
of every record that passes a handler. `configure_logging()` installs it on the root handlers and
routes `warnings` through logging; `pigtail.cli.main()` calls it for every command (CB-18b), and
`install_excepthook()` scrubs the traceback of an uncaught exception before it reaches stderr.
"""

from __future__ import annotations

import logging
import sys
import traceback
from types import TracebackType

from pigtail.pseudonymize import scrub_identifiers

MAX_LOG_CHARS = 2000
MAX_TRACE_CHARS = 8000


def scrub(text: str, limit: int = MAX_LOG_CHARS) -> str:
    """Redact identifiers with keyless placeholders, then truncate to `limit` chars (CB-18)."""
    out = scrub_identifiers(text)
    if len(out) > limit:
        out = f"{out[:limit]}… [truncated {len(out) - limit} chars]"
    return out


class RedactingFilter(logging.Filter):
    """Rewrites each record in place so that no handler ever sees the raw text (CB-18)."""

    def __init__(
        self,
        limit: int = MAX_LOG_CHARS,
        trace_limit: int = MAX_TRACE_CHARS,
    ) -> None:
        super().__init__()
        self.limit = limit
        self.trace_limit = trace_limit
        self._fmt = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_pigtail_scrubbed", False):
            return True
        try:
            msg = record.getMessage()
        except Exception:  # a bad format string must not leak the args either
            msg = str(record.msg)
        record.msg = scrub(msg, self.limit)
        record.args = None
        if record.exc_info:
            record.exc_text = scrub(self._fmt.formatException(record.exc_info), self.trace_limit)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrub(record.exc_text, self.trace_limit)
        if record.stack_info:
            record.stack_info = scrub(record.stack_info, self.trace_limit)
        record._pigtail_scrubbed = True
        return True


def install(
    logger: logging.Logger | None = None,
    *,
    limit: int = MAX_LOG_CHARS,
) -> RedactingFilter:
    """Attach one `RedactingFilter` to every handler of `logger` (default: root)."""
    target = logger or logging.getLogger()
    flt = RedactingFilter(limit=limit)
    for h in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(flt)
    return flt


def configure_logging(level: int = logging.INFO) -> None:
    """`logging.basicConfig` plus the CB-18 filter on every root handler. Idempotent."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.captureWarnings(True)  # warnings.warn() text goes through the filter too
    # HTTP clients log every request URL at INFO; queries can carry brief text (ADR-076.6)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    install()


def _scrubbing_excepthook(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(scrub(text, MAX_TRACE_CHARS) + "\n")


def install_excepthook() -> None:
    """CB-18b: scrub uncaught-exception tracebacks (they can quote payloads and handles)."""
    sys.excepthook = _scrubbing_excepthook
