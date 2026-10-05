"""Logging setup. Never logs secrets, authorization headers, retained third-party content or model reasoning."""

from __future__ import annotations

import json
import logging
import re

_SECRET_RE = re.compile(r"(sk-ant-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]+|Bearer\s+\S+)")


def redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text)


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(str(a)) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "time": record.created,
            }
        )


def setup_logging(level: str = "WARNING", fmt: str = "human") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RedactingFilter())
    handler.setFormatter(
        _JsonFormatter() if fmt == "json" else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("pigtail")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    for noisy in ("httpx", "httpcore", "anthropic", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pigtail.{name}")
