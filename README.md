# pigtail

Evidence-based forensics of how open-source projects grow. pigtail braids GitHub, Hacker News, Reddit and social signals into one timeline, builds a library of growth mechanisms tested against matched losers, and turns it into growth and distribution plans.

**Status:** being built autonomously by Claude Code agents. Progress: `ops/STATUS.md`. What you'll get: `docs/DELIVERABLES.md`.

| Path | Purpose |
|---|---|
| `docs/DELIVERABLES.md` | The pages and artifacts delivered (D1–D6) and their acceptance criteria |
| `docs/PRD.md` | Requirements, data model, evaluation, release criteria |
| `docs/WORK_ORDER.md` | Milestones, gates, human touchpoints and the session protocol for the agents |
| `docs/PLAN.md` | Original plan and rationale |
| `CLAUDE.md`, `.claude/agents/` | Agent instructions and subagents |
| `ops/` | Live state: STATE, BACKLOG, DECISIONS, RUNLOG, COSTS, STATUS, HUMAN_INPUTS |
| `scripts/run-autonomous.sh` | Headless loop runner (subscription or API backend) |
| `src/pigtail/` | Python engine (`pigtail` CLI) |
| `docs/guides/` | Developer and operator guides |
| `docker-compose.yml` | Postgres + S3-compatible snapshot storage |

## Kickoff (owner)
1. Copy this package into your clone of https://github.com/suchipizza/pigtail. Keep the existing `LICENSE`. Then commit and push.
2. Tick off what you can in `ops/HUMAN_INPUTS.md` → H1. At minimum: log in to Claude Code with your plan, make sure `ANTHROPIC_API_KEY` is **not** set, and check `/status`.
3. Run `claude` in the repo and paste:

   > You are the orchestrator for pigtail. Read CLAUDE.md, then docs/WORK_ORDER.md, docs/DELIVERABLES.md and docs/PRD.md in full. Start at M0, and run M1 (capture) and M2 (research) in parallel as early as their dependencies allow. Work autonomously per the work order. Only wait for me at the human gates H1–H6, and batch those requests in ops/HUMAN_INPUTS.md.

4. For unattended operation after M0, run `AGENT_BACKEND=subscription scripts/run-autonomous.sh` inside an isolated VM or container. Use `AGENT_BACKEND=api` to switch to API billing.
5. Each week: read `ops/STATUS.md` and answer anything new in `ops/HUMAN_INPUTS.md`.

## License
MIT
