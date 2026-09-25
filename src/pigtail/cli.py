"""pigtail CLI (PRD F14 / R14.1). Stages not yet built exit with code 2 and name their milestone."""

from __future__ import annotations

import argparse
import json
import sys

from pydantic import BaseModel

from pigtail import __version__

PENDING_STAGES = {
    "capture": "M1",
    "score": "M5",
    "panel": "M5",
    "extract": "M5",
    "analyze": "M5",
    "plan": "M8",
    "report": "M5",
}


class SmokeOutput(BaseModel):
    """Structured-output smoke test schema (M0 acceptance)."""

    answer: int
    backend_echo: str


SMOKE_SYSTEM = "You are a test fixture. Reply only through the requested JSON schema."
SMOKE_TEMPLATE = "Compute 17 + 25 and put it in `answer`. Put the word {input} in `backend_echo`."


def cmd_llm_smoke(args: argparse.Namespace) -> int:
    from pigtail.llm import PromptSpec, build_client

    client = build_client()
    if args.backend:
        client.default_backend = args.backend
    prompt = PromptSpec(id="smoke", version="1", system=SMOKE_SYSTEM, template=SMOKE_TEMPLATE)
    res = client.complete(prompt, "pigtail", SmokeOutput, job="smoke", use_cache=False)
    ok = res.output.answer == 42
    print(json.dumps({"ok": ok, **res.provenance(), "output": res.output.model_dump()}, indent=2))
    return 0 if ok else 1


def cmd_llm_status(_: argparse.Namespace) -> int:
    from pigtail.config import Settings
    from pigtail.llm.store import LLMStore

    s = Settings.from_env()
    store = LLMStore(s.data_dir / "llm.sqlite3")
    out = {
        "llm_backend": s.llm_backend,
        "overrides": s.llm_backend_overrides,
        "model": s.llm_model,
        "usage": store.summary(),
        "paused_until": {
            b: (p.isoformat() if (p := store.paused_until(b)) else None)
            for b in ("subscription", "api")
        },
    }
    print(json.dumps(out, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pigtail", description=__doc__)
    p.add_argument("--version", action="version", version=f"pigtail {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    llm = sub.add_parser("llm", help="LLM backend utilities (PRD F15)")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True)
    smoke = llm_sub.add_parser("smoke", help="structured-output smoke test on a real backend")
    smoke.add_argument("--backend", choices=("subscription", "api"))
    smoke.set_defaults(func=cmd_llm_smoke)
    status = llm_sub.add_parser("status", help="backend, usage ledger and pause state")
    status.set_defaults(func=cmd_llm_status)

    for stage, milestone in PENDING_STAGES.items():
        sp = sub.add_parser(stage, help=f"(not yet implemented; {milestone})")
        sp.set_defaults(func=lambda _a, s=stage, m=milestone: _pending(s, m))
    return p


def _pending(stage: str, milestone: str) -> int:
    print(f"pigtail {stage}: not implemented yet (scheduled for {milestone})", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
