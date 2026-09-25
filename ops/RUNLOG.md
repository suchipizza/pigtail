# Run log
One entry per session: date · session id · tasks · outcomes · cost · next.

## 2026-09-25 · session 1 (interactive orchestrator)
- Tasks: M0-T1..T5 done; M2-T1, M2-T2 started with research subagents.
- Outcomes: scaffold (uv/py3.12, ruff, mypy strict, pytest: 36 passed, 2 smoke tests skipped by default); `LLMClient` (F15) with both backends. **Subscription smoke test passed** (structured output, `claude -p --json-schema`). Billing check: `claude auth status` reports authMethod `claude.ai`, subscription plan, `ANTHROPIC_API_KEY` not set → usage is on the subscription, not API billing (M0 acceptance). The api smoke test is skipped: no key. docker compose: Postgres + SeaweedFS healthy, S3 round-trip and bad-credential rejection verified. MinIO image unavailable → ADR-005.
- Cost: LLM ≈ 2 smoke calls on subscription (no API spend). No BigQuery or hosting spend.
- Next: push + CI green (M0-T6); M1-T1..T3 capture core; merge M2 research outputs; verifier spot-check.
- 2026-09-25 incident: commit "docs(research): source matrix…" was pushed after the private-data scan flagged 4 findings, because of a shell chaining mistake. The findings were one platform's public role address (hello@…), not personal data. Fix: scan allowlists role addresses (ADR-011); commits are gated on a passing scan.
