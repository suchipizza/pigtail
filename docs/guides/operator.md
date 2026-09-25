# Operator guide (draft — completed in M9)

## LLM backend (`LLM_BACKEND`, PRD F15)
| Value | What it uses | When |
|---|---|---|
| `subscription` (default) | Your locally installed, official Claude Code CLI (`claude -p`) on **your own** Claude plan | Running pigtail for yourself |
| `api` | Anthropic API with `ANTHROPIC_API_KEY` | Shared or hosted deployments, high-volume runs |

Switching needs a restart and nothing else.

**Scope of the subscription backend (R15.6).** It is only for an operator running pigtail for themselves, on their own Claude plan, within Anthropic's terms for Claude Code. Any deployment that serves other users must use `api`. Read Anthropic's current terms before deploying: https://code.claude.com/docs/en/legal-and-compliance

Subscription setup:
1. Install Claude Code and log in with your plan (`claude`, then `/login`), or on a headless host run `claude setup-token` yourself and export `CLAUDE_CODE_OAUTH_TOKEN` in the host environment. pigtail never reads, stores or transmits this token.
2. Make sure `ANTHROPIC_API_KEY` is **not** set for pigtail's processes. pigtail also strips it from the CLI subprocess, because it would override the subscription.
3. Turn off model training in your Claude account's privacy settings.
4. Check with `uv run pigtail llm smoke` and `uv run pigtail llm status`.

API mode: use zero data retention or a data processing agreement with Anthropic where available (PRD §10).

## Services
`docker compose up -d --wait` starts Postgres and S3-compatible object storage (SeaweedFS). Point `S3_ENDPOINT` at a private bucket in production. Default hosting region: EU or Switzerland.
