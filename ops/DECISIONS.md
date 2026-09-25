# Decision log (ADRs)
Format per entry: `## ADR-NNN — title (date)` · Context · Options · Decision · How to reverse.

## ADR-000 — Defaults adopted at handoff (2026-09-25)
Context: The owner handed off autonomous execution.
Decision: Adopt the PRD §12 defaults. No composite outcome score. The capture layer starts before the pilot gate; the analysis layer is gated on G1.
How to reverse: A new ADR, with rationale.

## ADR-001 — Switchable LLM backend, subscription by default (2026-09-25)
Context: The owner wants to use her Claude subscription for now, with the option to switch to the API.
Decision: Agents run through Claude Code on the subscription (`AGENT_BACKEND`). Product LLM calls go through `LLMClient` with `LLM_BACKEND=subscription|api` (PRD F15). The subscription path uses only the official `claude` CLI and Anthropic's login flow, and pigtail never touches credentials. In subscription mode, content is pseudonymized before it is sent to the model.
Risks:
- Usage limits throttle the scale runs. Mitigation: pause/resume, and per-job API overrides.
- Anthropic's terms restrict subscription use by third-party products. Mitigation: the subscription backend is scoped to operator self-use through the official CLI, and shared deployments must use `api`. Re-check the Claude Code legal and compliance page at M0 and before release.
How to reverse: Set both variables to `api`.

## ADR-002 — Public repository from day one (2026-09-25)
Context: The repo github.com/suchipizza/pigtail already exists, is public and is MIT-licensed.
Decision: Build in public. Private data lives only in private storage, and a CI private-data scan blocks leaks. H4 now covers publishing findings and tagging the release, not repo visibility.
How to reverse: Make the repo private on GitHub; nothing else changes.
