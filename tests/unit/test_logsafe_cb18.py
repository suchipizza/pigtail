"""DPIA CB-18: log hygiene (logging filter, truncation, `runs.error`)."""

from __future__ import annotations

import io
import logging

import pytest

from pigtail.capture.models import Run
from pigtail.capture.runs import RUN_ERROR_MAX_CHARS, RunRecorder
from pigtail.logsafe import RedactingFilter, install, scrub
from pigtail.pseudonymize import Pseudonymizer


@pytest.fixture
def logger():
    log = logging.getLogger("pigtail.test.cb18")
    log.propagate = False
    log.setLevel(logging.DEBUG)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    log.handlers = [h]
    install(log)
    yield log, buf
    log.handlers = []


def test_cb18_filter_strips_handles_emails_and_urls(logger):
    log, buf = logger
    log.warning(
        "fetch failed for %s (%s) by @%s",
        "https://github.com/user0001",
        "user0001@example.org",
        "user0002",
    )
    out = buf.getvalue()
    for raw in ("user0001", "user0002", "example.org"):
        assert raw not in out
    assert "[profile:github]" in out and "[email]" in out and "@[handle]" in out


def test_cb18_filter_truncates_payloads(logger):
    log, buf = logger
    log.info("payload %s", "x" * 10_000)
    line = buf.getvalue().strip()
    assert len(line) < 2100 and "truncated" in line


def test_cb18_exception_text_is_scrubbed(logger):
    log, buf = logger
    try:
        raise ValueError("bad record from @user0003")
    except ValueError:
        log.exception("parse error")
    out = buf.getvalue()
    assert "user0003" not in out and "ValueError" in out and "Traceback" in out


def test_cb18_filter_is_idempotent_and_installed_once(logger):
    log, _ = logger
    install(log)
    assert sum(isinstance(f, RedactingFilter) for f in log.handlers[0].filters) == 1


def test_cb18_scrub_with_pseudonymizer_keeps_correlation():
    pz = Pseudonymizer("test-key-not-secret-0123456789")
    assert scrub("@user0001", pz=pz) == "@" + pz.pseudonym("user0001")


def test_cb18_run_error_is_scrubbed_and_truncated():
    seen: list[Run] = []
    with (
        pytest.raises(RuntimeError),
        RunRecorder("job", sink=seen.append, detect_commit=False),
    ):
        raise RuntimeError("row from @user0004 <user0004@example.org>: " + "y" * 5000)
    err = seen[-1].error or ""
    assert "user0004" not in err and "example.org" not in err
    assert err.startswith("RuntimeError: row from @[handle]")
    assert len(err) <= RUN_ERROR_MAX_CHARS + 40
