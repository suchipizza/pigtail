# pigtail — Agent Instructions

Repo: https://github.com/suchipizza/pigtail. It is **PUBLIC** and MIT-licensed, so every commit is visible to the world.

pigtail is an open-source, self-hostable method for evidence-based forensics of how open-source projects grow. A user writes a research brief about their own project; pigtail analyses that project's neighbourhood (winners against matched losers) and produces a neighbourhood report, a launch plan, and launch-mode tracking. It ships a method, not data: each user runs their own instance with their own credentials, and nothing is shared or collected centrally. The owner is the first user; nothing in the code is specific to her. Scope since 2026-09-26: ADR-047 (CR-002) and ADR-048 (CR-001).

## Read order at the start of every session
1. `ops/STATE.md` — where we are
2. `ops/BACKLOG.md` — what's next
3. `ops/HUMAN_INPUTS.md` — any new answers from the owner
4. `docs/WORK_ORDER.md` — how to work (§3 session protocol, §5 human gates, §6 hard rules)
5. `docs/DELIVERABLES.md` — what each user receives (pages D1–D7 and their acceptance criteria)
6. `docs/PRD.md` — what to build (requirement IDs R*.*)
7. `docs/PLAN.md` — original plan and rationale (background only; the PRD wins on conflicts)

## Non-negotiables
- Snapshot or drop: no claim without an evidence record and a content hash.
- Losers count: no pattern is reported from winners alone. Every neighbourhood pattern shows n among winners and matched losers, and its counterexamples (PRD §5.2, F20). Results are always shown per outcome dimension.
- A method, not data: nothing owner-specific in code, defaults, prompts or fixtures. Users' briefs, data and reports stay in their own instance, never in git.
- No fake engagement, no auto-posting, no scraping around terms, auth or paywalls.
- The repo is public: no secrets, snapshots, raw records, handles or person-level data in git. This includes ops logs, fixtures and commit messages. Raw data lives only in private storage.
- All product LLM calls go through `LLMClient` (PRD F15). `LLM_BACKEND=subscription` (the default) uses the official `claude` CLI on the operator's own plan; `LLM_BACKEND=api` uses `ANTHROPIC_API_KEY`. Never handle, log or store Claude credentials.
- Never fabricate data, citations or results. Write "unknown" instead.
- Don't wait for the owner except at the human gates H1–H6. Otherwise decide, log an ADR in `ops/DECISIONS.md`, and continue.

## Delegation
Use the subagents in `.claude/agents/`: researcher, engineer, analyst, verifier, compliance. Give each one a self-contained brief with the requirement IDs, the input paths, the expected output paths and the acceptance criteria. Run independent tasks in parallel. Milestone acceptance always goes to the `verifier`, never to the agent that did the work.

## Conventions
- Python 3.12 + uv; ruff, mypy, pytest. TypeScript strict for the UI.
- Conventional commits that reference task IDs (e.g. `feat(briefs): cost estimate before every run [M12-T3]`).
- Schemas live in `schemas/` and are versioned. Reports go in `docs/reports/` and state their data version and code commit.
- Every session ends with the ops files updated and the work committed.
