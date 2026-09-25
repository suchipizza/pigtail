"""Run one `pigtail` command line in a child process (M1-T21).

A child process isolates each job (memory, crashes, a hung download) and can be killed on
timeout. The child is `python -m pigtail.scheduler.child <argv>`, which installs the CB-18
`RedactingFilter` before any job code runs, so every job logs through it, including commands that
do not configure logging themselves. The parent scrubs the captured output again before logging it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from pigtail.logsafe import scrub

log = logging.getLogger("pigtail.scheduler")
TAIL_CHARS = 1500


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def child_run_id(self) -> str | None:
        """The `run_id` a pigtail command printed in its JSON result, if any."""
        text = self.stdout.strip()
        for candidate in (text, *reversed(text.splitlines())):
            try:
                data = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("run_id"), str):
                return str(data["run_id"])
        return None

    def summary(self) -> str:
        """Scrubbed tail of stderr (for `runs.error` and logs; CB-18)."""
        what = "timed out" if self.timed_out else f"exit {self.returncode}"
        tail = self.stderr.strip()[-TAIL_CHARS:]
        return scrub(f"{what}: {tail}" if tail else what)


Runner = Callable[[Sequence[str], timedelta], CommandResult]


def subprocess_runner(env: Mapping[str, str] | None = None) -> Runner:
    def run(argv: Sequence[str], timeout: timedelta) -> CommandResult:
        cmd = [sys.executable, "-m", "pigtail.scheduler.child", *argv]
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout.total_seconds(),
                env=dict(env if env is not None else os.environ),
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raw = e.stderr
            err = raw.decode(errors="replace") if isinstance(raw, bytes) else (raw or "")
            return CommandResult(returncode=-9, stderr=err, timed_out=True)
        return CommandResult(p.returncode, p.stdout, p.stderr)

    return run
