"""`subscription` backend: the operator's official Claude Code CLI in headless mode (R15.2).

pigtail never reads, stores or transmits Claude credentials. Authentication is whatever the
operator configured for their own `claude` install (`/login`, or `CLAUDE_CODE_OAUTH_TOKEN` they
generated with `claude setup-token`). `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are removed
from the subprocess environment because they would override the subscription. `--bare` is not
used because it ignores the OAuth token.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pigtail.llm.errors import BackendError, UsageLimitReached
from pigtail.llm.types import BackendResponse

# Env vars that would make the CLI bill an API or 3P provider instead of the subscription.
STRIPPED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

LIMIT_PATTERN = re.compile(
    r"usage limit|rate limit|limit reached|limit will reset|resets? at|out of (extra )?usage",
    re.IGNORECASE,
)
_RESET_EPOCH = re.compile(r"\|(\d{10})\b")  # e.g. "Claude AI usage limit reached|1760000000"

Runner = Callable[..., subprocess.CompletedProcess[str]]


# Opt out of telemetry, error reporting and other non-essential traffic from the CLI, so
# snapshot-derived content never reaches third-party error trackers (compliance CB-07; env var
# names from https://code.claude.com/docs/en/env-vars, accessed 2026-09-25).
PRIVACY_ENV = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_FEEDBACK_COMMAND": "1",
}


def subprocess_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in STRIPPED_ENV:
        env.pop(k, None)
    env.update(PRIVACY_ENV)
    return env


def parse_reset(text: str, now: datetime | None = None) -> datetime | None:
    m = _RESET_EPOCH.search(text)
    if m:
        return datetime.fromtimestamp(int(m.group(1)), UTC)
    return None


class SubscriptionBackend:
    name = "subscription"

    def __init__(
        self,
        claude_bin: str = "claude",
        timeout_s: int = 600,
        runner: Runner = subprocess.run,
    ) -> None:
        self.claude_bin = claude_bin
        self.timeout_s = timeout_s
        self._run = runner

    def command(self, *, system: str, json_schema: dict[str, Any], model: str) -> list[str]:
        return [
            self.claude_bin,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(json_schema),
            "--system-prompt",
            system,
            "--model",
            model,
            "--tools",
            "",
            "--no-session-persistence",
        ]

    supports_batch = False

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        model: str,
        context: str = "",
    ) -> BackendResponse:
        # No prompt caching or batches through the CLI: the context joins the system prompt.
        full_system = f"{system}\n\n{context}" if context else system
        cmd = self.command(system=full_system, json_schema=json_schema, model=model)
        # Empty working dir so no project CLAUDE.md or settings leak into product calls.
        with tempfile.TemporaryDirectory(prefix="pigtail-llm-") as cwd:
            try:
                proc = self._run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    env=subprocess_env(),
                    cwd=cwd,
                    check=False,
                )
            except FileNotFoundError as e:
                raise BackendError(f"claude CLI not found ({self.claude_bin})") from e
            except subprocess.TimeoutExpired as e:
                raise BackendError(f"claude CLI timed out after {self.timeout_s}s") from e
        return self.parse(proc.stdout, proc.stderr, proc.returncode, model)

    @staticmethod
    def parse(stdout: str, stderr: str, returncode: int, model: str) -> BackendResponse:
        try:
            payload: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {}
        text = " ".join(str(x) for x in (payload.get("result", ""), stdout[-2000:], stderr[-2000:]))
        is_error = bool(payload.get("is_error")) or returncode != 0 or not payload
        if is_error:
            if payload.get("api_error_status") == 429 or LIMIT_PATTERN.search(text):
                raise UsageLimitReached("subscription usage limit reached", parse_reset(text))
            # No CLI output in the message: it may echo prompt content (compliance CB-07).
            raise BackendError(
                f"claude CLI failed (exit {returncode}, subtype={payload.get('subtype')!r}, "
                f"api_error_status={payload.get('api_error_status')!r})"
            )
        data = payload.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(payload.get("result") or "")
            except (json.JSONDecodeError, TypeError) as e:
                raise BackendError("claude CLI returned no structured output") from e
        if not isinstance(data, dict):
            raise BackendError("structured output is not a JSON object")
        usage = payload.get("usage") or {}
        used_model = next(iter(payload.get("modelUsage") or {}), model)
        return BackendResponse(
            data=data,
            model=str(used_model),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cost_usd=float(payload.get("total_cost_usd", 0.0)),
            raw_meta={
                "session_id": payload.get("session_id"),
                "num_turns": payload.get("num_turns"),
            },
        )


def default_pause(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)
