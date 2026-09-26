# pigtail

Evidence-based forensics of how open-source projects grow, run on **your own project's neighbourhood**. You describe your project and what success means to you in a research brief. pigtail finds recent, relevant projects, lets you check the shortlist, picks winners and matched losers by your success definition, braids GitHub, Hacker News and other cleared sources into one timeline per case, and produces a neighbourhood report: the patterns that separated winners from comparable losers (each with n, the loser contrast and counterexamples), what's trending now, and a launch plan built from it.

pigtail ships a **method, not data**. Everyone runs their own instance with their own credentials and budget; nothing is shared or collected centrally.

**Status:** being built autonomously by Claude Code agents; re-scoped on 2026-09-26 (ADR-047, ADR-048 in `ops/DECISIONS.md`; the earlier global-collection code is tagged `archive/global-collection`). Progress: `ops/STATUS.md`. What you'll get: `docs/DELIVERABLES.md`. An install guide for new users is part of the v1 release (D6).

| Path | Purpose |
|---|---|
| `docs/DELIVERABLES.md` | The pages and artifacts delivered (D1–D7) and their acceptance criteria |
| `docs/PRD.md` | Requirements, data model, evaluation, release criteria |
| `docs/WORK_ORDER.md` | Milestones, gates, human touchpoints and the session protocol for the agents |
| `docs/PLAN.md` | Original plan and rationale |
| `CLAUDE.md`, `.claude/agents/` | Agent instructions and subagents |
| `ops/` | Live state: STATE, BACKLOG, DECISIONS, RUNLOG, COSTS, STATUS, HUMAN_INPUTS |
| `scripts/run-autonomous.sh` | Headless loop runner (subscription or API backend) |
| `src/pigtail/` | Python engine (`pigtail` CLI) |
| `docs/guides/` | Developer and operator guides |
| `docker-compose.yml` | Postgres + S3-compatible snapshot storage (started for runs) |

## Kickoff (owner)
1. Copy this package into your clone of https://github.com/suchipizza/pigtail. Keep the existing `LICENSE`. Then commit and push.
2. Tick off what you can in `ops/HUMAN_INPUTS.md` → H1. At minimum: turn on FileVault, log in to Claude Code with your plan, make sure `ANTHROPIC_API_KEY` is **not** set, and check `/status`.
3. Run `claude` in the repo and paste:

   > You are the orchestrator for pigtail. Read CLAUDE.md, then docs/WORK_ORDER.md, docs/DELIVERABLES.md and docs/PRD.md in full. Continue from ops/STATE.md and the milestones in WORK_ORDER §4. Work autonomously per the work order. Only wait for me at the human gates H1–H6, and batch those requests in ops/HUMAN_INPUTS.md.

4. For unattended operation after M0, run `AGENT_BACKEND=subscription scripts/run-autonomous.sh` inside an isolated VM or container. Use `AGENT_BACKEND=api` to switch to API billing.
5. Each week: read `ops/STATUS.md` and answer anything new in `ops/HUMAN_INPUTS.md`.

## License
MIT
