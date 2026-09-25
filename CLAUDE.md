# pigtail — Agent Instructions

Repo: https://github.com/suchipizza/pigtail. It is **PUBLIC** and MIT-licensed, so every commit is visible to the world.

pigtail is an open-source, self-hostable system for evidence-based forensics of how open-source projects grow. It builds a library of growth mechanisms tested against matched losers, plus a launch planner. It is general-purpose, not tailored to any one user.

## Read order at the start of every session
1. `ops/STATE.md` — where we are
2. `ops/BACKLOG.md` — what's next
3. `ops/HUMAN_INPUTS.md` — any new answers from the owner
4. `docs/WORK_ORDER.md` — how to work (§3 session protocol, §5 human gates, §6 hard rules)
5. `docs/DELIVERABLES.md` — what the owner receives (pages D1–D6 and their acceptance criteria)
6. `docs/PRD.md` — what to build (requirement IDs R*.*)
7. `docs/PLAN.md` — original plan and rationale (background only; the PRD wins on conflicts)

## Non-negotiables
- Snapshot or drop: no claim without an evidence record and a content hash.
- A mechanism is promoted only against matched losers (PRD §9.3).
- No fake engagement, no auto-posting, no scraping around terms, auth or paywalls.
- The repo is public: no secrets, snapshots, raw records, handles or person-level data in git. This includes ops logs, fixtures and commit messages. Raw data lives only in private storage.
- All product LLM calls go through `LLMClient` (PRD F15). `LLM_BACKEND=subscription` (the default) uses the official `claude` CLI on the operator's own plan; `LLM_BACKEND=api` uses `ANTHROPIC_API_KEY`. Never handle, log or store Claude credentials.
- Never fabricate data, citations or results. Write "unknown" instead.
- Don't wait for the owner except at the human gates H1–H6. Otherwise decide, log an ADR in `ops/DECISIONS.md`, and continue.

## Delegation
Use the subagents in `.claude/agents/`: researcher, engineer, analyst, verifier, compliance. Give each one a self-contained brief with the requirement IDs, the input paths, the expected output paths and the acceptance criteria. Run independent tasks in parallel. Gate checks (G1, G2, milestone acceptance) always go to the `verifier`, never to the agent that did the work.

## Conventions
- Python 3.12 + uv; ruff, mypy, pytest. TypeScript strict for the UI.
- Conventional commits that reference task IDs (e.g. `feat(capture): GH Archive velocity scan [M1-T3]`).
- Schemas live in `schemas/` and are versioned. Reports go in `docs/reports/` and state their data version and code commit.
- Every session ends with the ops files updated and the work committed.
