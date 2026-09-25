"""CB-18b: every CLI command installs the RedactingFilter (centrally in `pigtail.cli.main`), and
uncaught-exception tracebacks are scrubbed. Synthetic identifiers only."""

from __future__ import annotations

import logging
import sys

import pytest

from pigtail.cli import main
from pigtail.logsafe import RedactingFilter, _scrubbing_excepthook


@pytest.fixture
def root_handler():
    """A fresh root handler, so the test sees exactly what `main()` installs."""
    root = logging.getLogger()
    h = logging.Handler()
    records: list[logging.LogRecord] = []
    h.emit = records.append  # type: ignore[method-assign]
    root.addHandler(h)
    yield h, records
    root.removeHandler(h)


@pytest.mark.parametrize("argv", [["score"], ["report"], ["llm", "status"]])
def test_cb18b_every_command_installs_the_filter(argv, root_handler, monkeypatch, tmp_path):
    monkeypatch.setenv("PIGTAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test
    h, records = root_handler
    main(argv)
    assert any(isinstance(f, RedactingFilter) for f in h.filters)
    logging.getLogger("pigtail.test.cb18b").warning(
        "failed for synthetic-user@example.org see https://github.com/synthetic-user1"
    )
    msg = records[-1].getMessage()
    assert "synthetic-user@example.org" not in msg and "synthetic-user1" not in msg
    assert sys.excepthook is _scrubbing_excepthook


def test_cb18b_uncaught_traceback_is_scrubbed(capsys):
    try:
        raise RuntimeError("payload from synthetic-user@example.org @synthetichandle")
    except RuntimeError as e:
        _scrubbing_excepthook(RuntimeError, e, e.__traceback__)
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "Traceback" in err
    assert "synthetic-user@example.org" not in err and "@synthetichandle" not in err
