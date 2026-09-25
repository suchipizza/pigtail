"""`run` records (PRD §7): every pipeline execution is recorded so it can be replayed (M1-T2).

    with RunRecorder("capture.scan", config={...}, sink=db.upsert_run) as run:
        run.incr("hours_scanned")

The record is written when the run starts (status `running`) and again when it ends
(`succeeded`, or `failed` with the error). Exceptions are never swallowed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from pigtail.capture.models import Run, new_run_id

RunSink = Callable[[Run], None]

_HEX = re.compile(r"^[0-9a-f]{7,40}$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def git_commit(cwd: Path | None = None) -> str | None:
    """`PIGTAIL_CODE_COMMIT` if set (container builds), else `git rev-parse HEAD`, else None."""
    env = os.environ.get("PIGTAIL_CODE_COMMIT", "").strip().lower()
    if env:
        return env if _HEX.match(env) else None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd or Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if _HEX.match(out) else None


def jsonl_sink(path: Path) -> RunSink:
    """Append each run state as one JSON line (dev fallback when no database is configured)."""

    def _write(run: Run) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run.to_json_dict(), sort_keys=True) + "\n")

    return _write


class RunRecorder:
    def __init__(
        self,
        job: str,
        config: dict[str, Any] | None = None,
        sink: RunSink | None = None,
        *,
        code_commit: str | None = None,
        detect_commit: bool = True,
        prompt_versions: dict[str, str] | None = None,
        model_versions: dict[str, str] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.job = job
        self.config = dict(config or {})
        self.sink = sink
        self.clock = clock
        self.id = new_run_id()
        self.code_commit = code_commit or (git_commit() if detect_commit else None)
        self.prompt_versions = prompt_versions
        self.model_versions = model_versions
        self.counts: dict[str, int | float] = {}
        self._lock = threading.Lock()
        self.started_at: datetime | None = None
        self.run: Run | None = None

    def incr(self, key: str, n: int | float = 1) -> None:
        with self._lock:
            self.counts[key] = self.counts.get(key, 0) + n

    def _emit(self, **kw: Any) -> Run:
        assert self.started_at is not None
        run = Run(
            id=self.id,
            job=self.job,
            started_at=self.started_at,
            code_commit=self.code_commit,
            config=self.config,
            counts=dict(self.counts),
            prompt_versions=self.prompt_versions,
            model_versions=self.model_versions,
            **kw,
        )
        self.run = run
        if self.sink is not None:
            self.sink(run)
        return run

    def __enter__(self) -> RunRecorder:
        self.started_at = self.clock()
        self._emit(status="running")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is None:
            self._emit(status="succeeded", finished_at=self.clock())
        else:
            msg = f"{type(exc).__name__}: {exc}"[:1000]
            self._emit(status="failed", finished_at=self.clock(), error=msg)
