# pigtail — Work Order for Autonomous Agents

Audience: Claude Code (the orchestrator session) and its subagents.
Mission: build pigtail as specified in `docs/PRD.md`, and deliver the pages and artifacts defined in `docs/DELIVERABLES.md` (D1–D6 in v1; D4 in v2). Run it, extract the data, produce the mechanism library, and document everything. Work end to end with **minimal human supervision**.
Repository: https://github.com/suchipizza/pigtail. It is **public**, MIT-licensed and on the `main` branch. Everything committed is visible to the world.

---

## 1. Authority

The orchestrator makes all technical, methodological and sequencing decisions itself. When a choice is ambiguous:

1. Pick the most **reversible** option that is consistent with the PRD.
2. Record it as an ADR in `ops/DECISIONS.md` (context, options, choice, how to reverse it).
3. Continue working. **Never stop to ask a question that isn't listed in §5.**

Agents may revise PRD defaults (§12 of the PRD) with an ADR. They may **not** relax PRD §4 non-goals, §5 principles, §9.2–9.3 gates or §10 compliance requirements without owner approval, which is requested through `ops/HUMAN_INPUTS.md`.

## 2. Operating model

- **Orchestrator** = the main Claude Code session. It owns planning, state, delegation, integration and documentation.
- **Subagents** (`.claude/agents/`):
  - `researcher`: literature, source audits, terms.
  - `engineer`: code, infrastructure, tests.
  - `analyst`: case coding, extraction, statistics.
  - `verifier`: independent checking and gates.
  - `compliance`: privacy, terms, retention.

  Subagents have no shared memory. The orchestrator passes each one a self-contained brief and gets back a result plus the paths of any files it produced.
- **Parallelism.** Run independent tracks concurrently by delegating to subagents. The tracks are:
  - A: capture
  - B: research
  - C: methodology
  - D: engine
  - E: analysis
- **Long-running autonomy.** `scripts/run-autonomous.sh` launches headless sessions in a loop. Each session follows §3 and leaves the repository in a consistent, committed state.
- **Model access.** Agents run on the owner's Claude subscription by default (`AGENT_BACKEND=subscription`). The runner removes `ANTHROPIC_API_KEY` from the environment so the subscription is used, and it pauses when a usage limit is hit, then resumes. Setting `AGENT_BACKEND=api` switches the agents to API billing.
  - The product's own LLM calls are configured separately through `LLM_BACKEND` (PRD F15).
  - Plan work around the subscription's usage windows: keep sessions small and resumable, and prefer cheap deterministic code over LLM calls wherever it is equally accurate.
- **Long-running services** (collectors, live detection, scoring jobs) are deployed to the host listed in `ops/HUMAN_INPUTS.md`. They must not depend on an agent session being alive.

## 3. Session protocol (every session)

1. Read `CLAUDE.md`, `ops/STATE.md`, and the top of `ops/BACKLOG.md`. Consult `docs/DELIVERABLES.md` for the acceptance criteria of whatever you're working on. Check whether any answers have arrived in `ops/HUMAN_INPUTS.md`.
2. Pick the highest-priority unblocked task(s). Delegate independent ones in parallel.
3. Complete the work. A task is done only when its acceptance criteria pass. Tests must be green, and the `verifier` must approve any gate-related work.
4. Update:
   - `ops/STATE.md`: milestone, track status, blockers.
   - `ops/BACKLOG.md`: tasks completed, added and re-prioritized.
   - `ops/RUNLOG.md`: append one entry per session with date, tasks, outcomes, costs and next steps.
   - `ops/COSTS.md`.
   - Documentation affected by the change.
5. Commit with a conventional-commit message that references task IDs. Push to `origin` (the public repo) only after the private-data and secret scans pass.
6. If every remaining task is blocked by a human gate, set `status: BLOCKED` in `ops/STATE.md` and stop. If the definition of done (§10) is met, set `status: DONE`.

**Failure handling.** After 3 failed attempts on a task, mark it `blocked`, write a diagnosis in the task, and move on. Never fabricate data, results or citations to unblock a task.

## 4. Milestones

Tracks run in parallel. The dependencies listed are the only blockers. Each milestone ends with a document in `docs/` and a status entry.

### M0 — Bootstrap (Track D) · depends on: nothing
- Clone https://github.com/suchipizza/pigtail. Keep its existing MIT `LICENSE`. Add this handoff package.
- Create the repo scaffold: monorepo layout, uv, pre-commit, CI (lint, types, tests, secret scan, private-data scan), docker compose skeleton, `.env.example`.
- Implement `LLMClient` with both backends (PRD F15), including limit handling and the usage ledger, plus a smoke test for each backend. The `api` backend's test is skipped when no key is present.
- Create the ops files (§3) and seed `BACKLOG.md` from this work order.
- Write `ops/HUMAN_INPUTS.md` with the H1 request (§5), batched into one list.
- **Accept when:** CI passes on an empty skeleton; `docker compose up` starts Postgres and object storage; the ops files exist; the `subscription` backend completes a structured-output smoke test; and usage has been confirmed on the subscription rather than API billing (check `/status`; record the result in RUNLOG).

### M1 — Capture layer v0 (Track A) · depends on: M0, H1 credentials (use whatever credentials are available; stub the rest)
- Capture schema v0, covering `evidence`, `case` and `repo`, with content-addressed snapshots.
- Daily GH Archive velocity scan, bot filtering (heuristic v0) and case opening (PRD R1.1).
- Mention capture for HN (Algolia and Firebase), Bluesky and GitHub. Wayback save requests. Reddit only after `compliance` has cleared its terms.
- Announced-launch watchlist (R1.3).
- Pseudonymization at ingest (§10).
- Deploy to the host with a scheduler, health checks and alerting to `ops/ALERTS.md` (and email if configured).
- **D1 preview:** a minimal web app with operator login, containing `/cases` and `/cases/:id` with the timeline and evidence tabs on uncoded captured data, labelled "uncoded preview". This gives the owner something to watch early.
- **Accept when:** the scan has run for 7 consecutive days, ≥ 20 cases have been opened automatically with snapshots, a replay from snapshots reproduces the stored records, and the D1 preview criteria in DELIVERABLES pass.
- **Priority:** highest. Evidence is lost for every day this doesn't run.

### M2 — Prior art and source matrix (Track B; runs alongside M0 and M1)
- Literature review (`researcher`): GitHub popularity, fake stars (StarScout and later work), diffusion and cascades, causal inference for observational data, OSS go-to-market writing and postmortems. **Deliverable:** `docs/research/literature.md`, containing methods to borrow and a list of hypotheses.
- Source audit (`researcher` + `compliance`): coverage, historical depth, cost, rate limits, terms, commercial-use status and deletion obligations for every source in PRD R2.2. **Deliverable:** `docs/research/source-matrix.md` plus the gap list.
- Fake-star method: select one, reproduce it on a sample, and document how it was validated.
- **Accept when:** every claim in both documents is cited, the matrix covers every source, and the `verifier` has spot-checked 10 citations.

### M3 — Phase 0 specs (Track C) · depends on: M2 source matrix (data depth)
- Finalize the outcome model (PRD §8) against actual data availability. **Deliverable:** `docs/specs/outcome-model.md`.
- Define and version the outcome-class thresholds.
- Compliance pack (`compliance`): legitimate-interest assessment, light DPIA, retention policy, public privacy notice, per-source terms memo, and a list of questions for the legal review. Then raise H2.
- **Accept when:** the specs are merged; the compliance pack is complete; H2 has been raised.

### M4 — Codebook v0 and pilot (Track C) · depends on: M2 literature review
- Codebook v0 plus adaptive modules (PRD F6).
- Select pilot cases: 3 winners and 3 matched losers across ≥ 3 strata. They can be hand-picked; the universe is not required.
- Code the pilot manually, with the `analyst` doing two independent passes plus adjudication. Revise the codebook.
- If H3 answers have arrived, compute LLM–human agreement against the human sample.
- **Gate G1** (checked by the `verifier`):
  - α ≥ 0.70 on core fields.
  - Fewer than 10% of codebook categories changed in the last revision round.
  - 100% of claims have snapshots.
- If G1 fails twice, write `docs/reports/pilot-failure.md`, re-scope the codebook and retry. Don't lower the threshold.
- **Deliverables:** `docs/methodology/codebook.md` and `docs/reports/pilot.md`.

### M5 — Engine v1: analysis layer (Track D) · depends on: G1
- Schemas v1 (PRD §7), with migration from capture v0.
- Connectors beyond the M1 set, in the source-matrix priority order.
- Outcome scoring (F3), universe and panel building with matching and balance diagnostics (F4).
- Extraction pipeline with a citation validator, double coding and a review queue (F7).
- Burst-to-trigger attribution (R5.5).
- Causal toolkit scripts (F8).
- CLI and API (F14). Trends computation (F16). On-demand analyzer and tracking, stages 1–2 (F17).
- UI built against real data:
  - D1 full (`/cases`, `/cases/:id` with every tab).
  - D2 "What's trending" plus the methods panel.
  - D5 basic (`/analyze`: stages 1–2, coverage score, tracking).
  - D6 `/admin` and `/settings`, including the backend switch.
- Run the backend parity check (R15.7) if an API key is available. Otherwise log the check as deferred.
- **Accept when:** the end-to-end pipeline runs on the pilot cases and reproduces the pilot coding with α ≥ 0.70; test coverage is ≥ 80% on the pipeline core; every PRD F-requirement has a test or a documented deferral; and the D1 and D5-basic acceptance criteria in DELIVERABLES pass.

### M6 — Scale runs (Track E) · depends on: M5
- Tier 1: ≥ 5,000 repos. Tier 2: ≥ 300. Tier 3: ≥ 30 matched pairs, stratified as in PRD R4.2.
- Work the review queue per §5 (the H3 fallback applies).
- Synthesize mechanisms and apply the promotion rule (PRD §9.3). The `verifier` audits every promoted mechanism.
- **Gate G2:** ≥ 10 promoted mechanisms, each with its loser contrast; balance diagnostics pass; the data-quality gates (PRD §9.2) pass.
- Build D2 "What works" (mechanism browser, stratum filters, channel/timing and asset/message analyses). Complete D5 with stage 3 coding and the R17.5 validation.
- **Deliverables:** `docs/reports/mechanism-library-v1.md`, a per-stratum coverage report, and D2 and D5 passing their DELIVERABLES acceptance criteria.

### M7 — Evaluation (Track E) · depends on: M6, plus ≥ 60 days of live data from M1
- Run the system tests in PRD §9.1: forecasting, loser-value, generality and prospective.
- Keep pre-registering predictions for announced launches from M1 onward, so the prospective test has ≥ 20 cases by this point.
- **Deliverable:** `docs/reports/evaluation-v1.md`, including tests that failed and what they imply.

### M8 — Plan Generator (Track D) · depends on: G2
- Launch planner (F10) and experiment registry (F11), delivered as D3 `/plan`.
- Register the owner's 2 launches as cases when she supplies their profiles (H5). They are not tuning targets.
- **Accept when:** the D3 acceptance criteria in DELIVERABLES pass.

### M9 — v1 release (all tracks) · depends on: M7, M8, H2 resolved, H4
- Documentation set (PRD §11). Final report `docs/reports/final.md`. D6 acceptance: a fresh-VM install in ≤ 30 min.
- Tag `v1.0.0`. The code is already public; this step publishes aggregate findings and optionally enables D2 public mode, after H2 and H4.
- Register pigtail's own launch as a case and lock its predictions before launch.

### M10 — v2: Execution Engine (Track D) · depends on: M9
- D4 `/execute` (F12): asset drafting, schedule, an approval queue with **no unapproved external actions**, monitoring, alerts and prediction scorecards.
- **Accept when:** the D4 acceptance criteria in DELIVERABLES pass on 1 real launch (this requires H5).

## 5. Human gates

These are the **only** reasons to wait for the owner. Batch requests in `ops/HUMAN_INPUTS.md`, each with a deadline, what is blocked, and the default action taken if nobody answers. Keep working on everything that isn't blocked.

| ID | What | Blocks | Default if no answer within 14 days |
|---|---|---|---|
| H1 | Credentials and accounts: Claude subscription login on the agent machine and on the host (`claude setup-token` for headless use), with model-training turned off on the account; GitHub token and push access to suchipizza/pigtail; GCP/BigQuery billing; optional Anthropic API key; Reddit API approval; optional X/YouTube keys; a host VM (EU/CH); a private object storage bucket; a private database backup location. Plus a monthly budget ceiling. | M1 connectors that need them; M5–M6 scale | Continue with the sources that are available and mark the missing ones as gaps. Spending ceiling: USD 300/month until the owner sets one. |
| H2 | Legal review (external lawyer) of the compliance pack | Publishing anything (M9). **Does not block** collection under the documented safeguards. | Release stays blocked; everything else continues. |
| H3 | Human calibration coding: about 3–4 hours of coding pilot items in the review UI | Nothing; it strengthens G1 | Use LLM–LLM agreement only, and label all findings "LLM-coded, not human-validated". |
| H4 | Approval to publish aggregate findings, enable D2 public mode, and tag v1.0 (the code is already public) | M9 | Don't publish findings. |
| H5 | Any action on an external platform on someone's behalf (posting, emailing, joining communities), and profiles for the owner's launches | That action only | Don't take the action. |
| H6 | Spending above the budget ceiling or entering new paid tiers | That spend only | Don't spend. Degrade gracefully. |

## 6. Hard rules

- Obey the PRD §4 non-goals and §10 compliance requirements.
- Never scrape around authentication, paywalls or robots/terms restrictions. A blocked source is recorded as a gap.
- Respect rate limits with margin. Identify collectors with a contact user-agent where the platform expects one.
- The repo is **public**. Never commit secrets, snapshots, raw records, handles or person-level data, including in ops logs, reports, test fixtures and commit messages. Fixtures must use synthetic or pseudonymized data. Raw data lives only in private storage.
- In `subscription` mode, never read, copy, log or transmit Claude credentials. Use only the official `claude` CLI and Anthropic's login flow.
- Never invent citations, numbers or cases. Unknown means "unknown" in the output.
- Pre-register analyses (hypotheses, tests, thresholds) in `docs/preregistration/` **before** looking at the outcome data they test.
- Work in the open inside the private repo: every artifact, prompt, model ID and dataset version is committed or referenced.

## 7. Engineering standards

- Typed Python (mypy strict on core), ruff, pytest; TypeScript strict for the UI.
- Every collector ships with recorded fixtures and contract tests. Every schema ships with example documents and validation tests.
- Migrations are forward-only and tested.
- Pipelines are idempotent and resumable. Every job run writes a `run` record.
- LLM calls go through one client wrapper that handles retries, caching keyed on (prompt version, input hash), cost logging and zero-retention settings.
- Performance targets: the Tier 1 run completes in ≤ 48 h on the reference VM, and live detection finishes within 24 h of a breakout.

## 8. Documentation requirements

- `docs/` contains: research, specs, methodology (codebook and methods paper), compliance, preregistration, reports, operator guide, developer guide and schema reference.
- `ops/DECISIONS.md` holds ADRs for every non-trivial choice.
- `ops/STATUS.md` is a weekly digest for the owner, readable in 2 minutes: progress per milestone, gates passed or failed, what's blocked on her, costs to date, and notable findings.
- Every report states its data version, code commit and limitations.

## 9. Owner reporting

The owner does not supervise individual steps. She reads `ops/STATUS.md` weekly and answers `ops/HUMAN_INPUTS.md` when it changes. If email is configured (H1), send the weekly status and any new H-gate request by email.

## 10. Definition of done

- M0–M9 are accepted, G1 and G2 have passed, and deliverables D1, D2, D3, D5 and D6 pass their acceptance criteria in `docs/DELIVERABLES.md`. D4 follows in M10.
- PRD §11 release criteria are met, or their exceptions are documented and approved through H4.
- The live system has been running unattended for ≥ 30 days with alerts working.
- `docs/reports/final.md` summarizes the mechanisms, evaluation results, limitations and next steps.
