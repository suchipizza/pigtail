# pigtail — Work Order for Autonomous Agents

Version 2.1 · Audience: Claude Code (the orchestrator session) and its subagents.
Mission: build pigtail as specified in `docs/PRD.md` (v2.2), and deliver the pages and artifacts defined in `docs/DELIVERABLES.md` (D1–D3 and D5–D7 in v1; D4 in v2). pigtail ships a **method, not data**: every user runs their own instance with their own credentials. The owner is the first user, and her project's neighbourhood is the pilot. Work end to end with **minimal human supervision**.
Repository: https://github.com/suchipizza/pigtail. It is **public**, MIT-licensed and on the `main` branch. Everything committed is visible to the world.
Binding source for v2.1: `ops/OWNER_DIRECTIVE_001.md` (Owner Directive 001, public copy) and ADR-059 to ADR-071. ADR-070 and ADR-071 are the owner's later decisions and override the directive where they differ.

## Change history

| Version | Date | Change |
|---|---|---|
| 2.1 | 2026-09-26 | Owner Directive 001 applied (Directive §0.3). Milestone plan re-ordered into the directive's 8 steps (Directive §11, ADR-069): the v2.0 milestones M13–M19, none of them started, are retired and replaced by **M20–M27** (with D4 as M28); M11 and M12 stay accepted (§4.5, with a mapping table). Human gates replaced by Directive §10 / ADR-068 (§5). Agents stay on the owner's subscription at ≤ ~50% of the weekly allowance; product calls use `api` (§2; Directive §6.1–6.2, §6.4; ADR-064). Hard rules gain: no handles stored, publishing disabled, brief content never in git (§6; Directive §8, ADR-066, ADR-071.3). Definition of done aligned with the directive's acceptance (§10). |
| 2.0 | 2026-09-26 | Re-planned per ADR-047 (CR-002: brief-driven neighbourhood analysis) and ADR-048 (CR-001: batch runs on the owner's Mac, launch mode, evidence-decay study). M0, M2 and M3 are accepted history. M1 and M4 are closed as partly superseded (§4.2). M5–M10 are retired. New milestones M11–M19; the next major milestone is M15, the first end-to-end neighbourhood report on the owner's project (the pilot). Gates G1 and G2 are retired. H1 and H3 are redefined. The definition of done follows ADR-048.4. |
| 1.0 | 2026-09-25 | Global collection, mechanism library, M0–M10. The full text is in git history (for example `git show archive/global-collection:docs/WORK_ORDER.md`); the tag `archive/global-collection` marks the code before the re-scope. |

---

## 1. Authority

The orchestrator makes all technical, methodological and sequencing decisions itself. When a choice is ambiguous:

1. Pick the most **reversible** option that is consistent with the PRD.
2. Record it as an ADR in `ops/DECISIONS.md` (context, options, choice, how to reverse it).
3. Continue working. **Never stop to ask a question that isn't listed in §5.**

Agents may revise PRD defaults (§12 of the PRD) with an ADR. They may **not** relax PRD §4 non-goals, §5 principles, §9.2 quality rules or §10 compliance requirements without owner approval, which is requested through `ops/HUMAN_INPUTS.md`. Owner change requests are recorded as ADRs (as CR-001 and CR-002 were in ADR-048 and ADR-047) and override the PRD where they conflict until the PRD is updated.

## 2. Operating model

- **Orchestrator** = the main Claude Code session. It owns planning, state, delegation, integration and documentation.
- **Subagents** (`.claude/agents/`):
  - `researcher`: literature, source audits, terms.
  - `engineer`: code, infrastructure, tests.
  - `analyst`: case coding, extraction, statistics.
  - `verifier`: independent checking and milestone acceptance.
  - `compliance`: privacy, terms, retention, the compliance template.

  Subagents have no shared memory. The orchestrator passes each one a self-contained brief and gets back a result plus the paths of any files it produced.
- **Parallelism.** Run independent tracks concurrently by delegating to subagents. The tracks are:
  - A: runs and capture (batch runs, launch mode, connectors)
  - B: research (sources, terms)
  - C: methodology (codebook, pre-registration, patterns)
  - D: engine and UI (briefs, discovery, reports, plan, analyzer)
  - E: analysis (the pilot)
- **Long-running autonomy.** `scripts/run-autonomous.sh` launches headless sessions in a loop. Each session follows §3 and leaves the repository in a consistent, committed state.
- **Model access** (Directive §6.1–6.2, §6.4; ADR-064.1–2).
  - **Agents building pigtail** (Claude Code) stay on the owner's Claude subscription (`AGENT_BACKEND=subscription`) unless she changes this. They use **at most about 50% of her weekly allowance**; on a usage limit they pause, then resume. The runner removes `ANTHROPIC_API_KEY` from the agents' environment so the subscription is used. Agents **never switch backends on their own**; setting `AGENT_BACKEND=api` is the owner's decision.
  - **pigtail's own product LLM calls** run on `LLM_BACKEND=api`, on the owner's Anthropic API key under the Commercial Terms, with the per-stage models, Batch API, prompt caching and caps of PRD R15.8–R15.11. They are configured separately from the agents (PRD F15).
  - Plan work around the subscription's usage windows: keep sessions small and resumable, and prefer cheap deterministic code over LLM calls wherever it is equally accurate.
- **Runs, not services** (ADR-048). The owner's instance runs as scheduled batch runs on her Mac (launchd; Docker only during runs; FileVault required). A 24/7 host is optional. Runs must not depend on an agent session being alive.
- **Nothing owner-specific in code.** The owner's brief, data and reports live only in her instance's private storage. Tests and fixtures use the synthetic example brief or synthetic data.

## 3. Session protocol (every session)

1. Read `CLAUDE.md`, `ops/STATE.md`, and the top of `ops/BACKLOG.md`. Consult `docs/DELIVERABLES.md` for the acceptance criteria of whatever you're working on. Check whether any answers have arrived in `ops/HUMAN_INPUTS.md`.
2. Pick the highest-priority unblocked task(s). Delegate independent ones in parallel.
3. Complete the work. A task is done only when its acceptance criteria pass. Tests must be green, and the `verifier` must approve milestone acceptance.
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

Tracks run in parallel. The dependencies listed are the only blockers. **The current plan is §4.5** (Directive §11, ADR-069); §4.1–4.4 are history. Each milestone ends with a document in `docs/` (or a private report for the owner's instance, with a public method-level summary) and a status entry. Milestone IDs are never reused: retired milestones keep their numbers.

### 4.1 Accepted (history)

- **M0 — Bootstrap. Accepted** (verifier PASS, 2026-09-25). Scaffold, CI, docker compose, `LLMClient` with both backends, ops files, H1 raised.
- **M2 — Prior art and source matrix. Accepted** (verifier spot-check round 2 PASS, 2026-09-25). `docs/research/literature.md`, `docs/research/source-matrix.md`. Carried over: M2-T3 (fake-star reproduction), now limited by data availability (PRD R3.3), and minor follow-ups M2-T5. The matrix gains a Trendshift entry in M22 (formerly M13; ADR-069).
- **M3 — Phase 0 specs. Accepted** (verifier PASS round 4, 2026-09-25). Outcome model spec, outcome thresholds, compliance pack, H2 raised. Under ADR-047.9 the compliance pack becomes a template (M27, formerly M18; ADR-069); the outcome spec's metrics stand, and its global classes are no longer how winners are chosen (PRD §8.2).

### 4.2 Closed as partly superseded

- **M1 — Capture layer v0. Closed; not accepted as written.** Its acceptance (7 consecutive days of GH Archive scans, ≥ 20 cases opened automatically, deployment to a host) is replaced by ADR-048.4 and moves to M26 (formerly M14; ADR-069).
  - Carried into the new plan: capture schema v0 and content-addressed snapshots, the connector base, HN rank poller and HN connectors (held under ADR-022), star-history client, per-repo collectors, pseudonymization, privacy controls (CB-*), scheduler (now running batch and launch-mode runs), replay, JSONL export, backups, and the D1 preview (browser check and p95 benchmark still open: M1-T25).
  - Superseded: global GH Archive velocity scan as a case opener (R1.1), the 50k watch list, all-GitHub search sweeps, global breakout detection (deleted in M11), the announced-launch watchlist (R1.3), and the 24/7 host.
- **M4 — Codebook v0 and pilot. Closed; the method set carries over, the pilot is redefined.**
  - Carried: codebook v0.2.0 (the starting version for the new pilot), adaptive modules, double coding with adjudication, the seed candidate cards (as seed pattern codes), the citation validator requirement.
  - Superseded: the 3 + 3 hand-picked pilot and gate G1 as a blocking gate (replaced by per-field agreement with "low reliability" labels, ADR-047.7); the forecasting-test and global threshold-calibration pre-registrations (withdrawn by dated amendment in M11, ADR-047.7); the held-out splits that served them.

### 4.3 Retired

- **M5** (engine v1 on the global panel), **M6** (scale runs, Tier 1–3, gate G2), **M7** (§9.1 evaluation), **M8** (planner on the global library), **M9** (v1 release as defined in v1.0) and **M10** (v2) are retired. Their surviving content is re-planned below: engine pieces into M12–M14, analysis into M15, D3 into M16, D5 into M17, release into M18, D4 into M19. Those were in turn re-planned as M20–M28 (§4.5, ADR-069).
- **Gates G1 and G2 are retired.** Milestone acceptance by the `verifier` replaces them.

### 4.4 New milestones

*§4.4 as written in v2.0. M11 and M12 are **accepted** (2026-09-26, `ops/STATE.md`). M13–M19 were never started and are **retired** by ADR-069; their content moved into M20–M28 (§4.5). Their text stays below so that older references resolve.*

#### M11 — Re-scope cleanup (Tracks A, C) · depends on: nothing · **accepted 2026-09-26**
- Confirm the tag `archive/global-collection` is on `origin` and points at the pre-deletion commit.
- Delete the 50k-repo watch list, the all-GitHub search sweeps and the global breakout detection, with their CLI commands and scheduler jobs. Keep the HN front-page poller, the scheduler, the per-repo collectors and the star-history client (ADR-047.6).
- Inventory the data already collected as a cache that briefs can reuse (counts only in git).
- Commit dated withdrawal amendments for the forecasting test and the global threshold calibration (ADR-047.7), following the pre-registration rules; record the status of the 3 + 3 pilot pre-registration.
- Update the operator and developer guides, the agent definitions and the codebook wording that still describe the global scope.
- **Accept when:** the tag is on `origin`; no code path, CLI command, scheduler job or doc instruction for the three deleted features remains (search plus tests); CI is green; the amendments are committed and pushed; the verifier signs off.

#### M12 — Research brief v1 (Track D) · depends on: M11 · **accepted 2026-09-26**
- `schemas/brief/` (v1) with examples and validation tests; load, validate, version and diff briefs (`pigtail brief new|validate|show|diff|list`).
- `/briefs` list and editor with the guided form, YAML import and export, and version diff (D7).
- LLM expansion (R18.7), editable before save.
- Cost estimator and budget hard stop (R18.5); cache reuse on re-run (R18.4); provenance record (R18.6).
- The synthetic example brief; the private-data scan blocks brief files outside the example.
- **Accept when:** the D7 criteria for brief creation, validation, versioning, multiple briefs, cost estimate, hard stop, cache reuse and private storage pass; schema tests pass; the verifier signs off.

#### ~~M13~~ (retired by ADR-069) — Discovery, relevance filter and shortlist (Tracks B, D) · depends on: M12; H1 GitHub token for live runs (build on fixtures before)
- Candidate discovery per R4.5: GitHub search and topics, Show HN, awesome-lists, GH Archive restricted to the brief's topics (discovery signals only), Trendshift after a `researcher` + `compliance` terms audit adds it to the source matrix (off until cleared, ADR-010).
- LLM relevance filter against a written rubric, with the reason logged per candidate (R4.6).
- Shortlist review screen with logged accept/reject/add decisions and precision (R4.7).
- Outcome sort, winner and matched-loser selection, balance diagnostics, and the sensitivity check (R4.8, R4.3, R4.9).
- **Accept when:** on the example brief, run live, every candidate has a logged source, verdict and reason; every review decision is logged with reason and brief version; precision is computed; selection is deterministic for a given brief version and data version; balance diagnostics and the sensitivity check are produced; the D7 shortlist criteria pass; the verifier signs off.

#### ~~M14~~ (retired by ADR-069) — Batch runs, launch mode and operations (Track A) · depends on: M12, M13; H1 (FileVault, GitHub token)
- `pigtail run --incremental` per brief, with checkpoints and resume (R19.1–R19.3); the brief's initial backfill over its window; refresh cadence and the per-source ceiling (≤ 60 days).
- Tracked projects weekly; launch mode (daily for 14 days, every 3 h on launch day; triggered by a declared launch or a detected burst of a tracked project) (R19.4, R19.5).
- launchd schedule on macOS; Docker only during runs; run reports; local alerts with the sanitized export to `ops/ALERTS.md` and e-mail if configured (R19.6, R19.7).
- `pigtail doctor` checks FileVault (or volume encryption on the server path). Encrypted backups to external or private storage; the server path documented and tested through backup and restore.
- **Accept when (ADR-048.4, replacing M1's acceptance):** the brief's backfill completes; 2 incremental runs complete with no gaps in the brief's API series; replay reproduces the records; an interrupted run resumes correctly; D1 cases update at every run and launch-mode cases on their own schedule (a simulated burst and a declared launch both switch the schedule); a backup restores on a second machine or the server path; the verifier signs off.

#### ~~M15~~ (retired by ADR-069) — The pilot: first end-to-end neighbourhood report on the owner's project (Tracks C, E, D) · **next major milestone** · depends on: M13, M14; H1 (training off or API key), H3 (shortlist skim), H5 (the owner's brief); ADR-022 holds for person-level mention sources
- The owner writes (or approves) her brief with the form; it lives only in her instance (H5).
- **Pre-register** the pilot's analyses (success definition, selection, contrasts, sensitivity alternatives, agreement reporting, evidence-decay measurement) in `docs/preregistration/` and push it **before the outcome sort** (§6).
- Discovery, relevance filter, and the shortlist skim by the owner or the verifier (H3); precision logged against the ≥ 80% target.
- Outcome sort; 15–25 winners and 15–25 matched losers; balance diagnostics; sensitivity check.
- Deep forensics on every winner and loser; double coding with adjudication; per-field α; "low reliability" labels (ADR-047.7).
- Event studies and winner/loser contrasts; neighbourhood patterns with n, loser contrast and counterexamples; trends over the last 3–6 months.
- **Evidence-decay study** (R19.8): share of key evidence still retrievable 1, 7 and 30 days after first capture. If more than 10% is lost at 7 days, shorten the cadence for new breakouts through an ADR and report the finding.
- Once the pilot's shortlist is final, delete collected data that no brief references (ADR-047.6).
- D2 report page and D1 full case view on the pilot's cases.
- **Deliverables:** the neighbourhood report in the owner's private instance; `docs/reports/pilot.md` in the repo with method-level results only (precision, per-field agreement, balance, sensitivity, evidence decay, costs, run times, deviations from the pre-registration), with no brief content, case names or coded values unless the owner approves publishing them (H4).
- **Accept when:** the pre-registration was pushed before the outcome sort; the D2 acceptance criteria pass on the owner's brief; the D1 acceptance criteria pass on its cases; relevance precision is reported (and, if below 80%, stated as such); per-field agreement is shown with the "low reliability" label applied; the sensitivity check flags affected cases; the evidence-decay study reports its 1/7/30-day shares and any cadence change is recorded as an ADR; 100% of claims resolve to snapshots; the public pilot report passes the private-data scan; the verifier signs off.

#### ~~M16~~ (retired by ADR-069) — Plan generator (Track D) · depends on: M15
- D3 `/plan` from a brief's report (F10) and the experiment registry (F11).
- **Accept when:** the D3 acceptance criteria pass.

#### ~~M17~~ (retired by ADR-069) — Project analyzer & tracker (Track D) · depends on: M14 (tracking, launch mode); M15 for its validation cases
- D5 `/analyze`: stages 1–3, current state, benchmark, coverage score, tracking and launch mode (F17).
- **Accept when:** the D5 acceptance criteria pass, including the R17.5 validation on ≥ 5 of the pilot's deep-forensics cases.

#### ~~M18~~ (retired by ADR-069) — Install for any user, and v1 release (all tracks) · depends on: M15, M16, M17; H2 resolved; H4
- Install guide, brief guide, operator guide (macOS and optional server), methodology paper, codebook, schema reference, limitations (PRD §11).
- Compliance pack turned into a template each user adopts (ADR-047.9); the owner's pack stays the first filled-in copy.
- Cost expectations for the example brief, measured on real runs.
- The timed new-user install test (D6).
- Tag `v1.0.0` after H4. The code is already public; this step publishes any aggregate findings (after H2 and H4) and optionally enables D2 public mode for the owner's instance.
- Track pigtail's own launch in launch mode and lock its predictions before launch.
- **Accept when:** the D6 acceptance criteria pass (including the under-one-hour new-user test and 3 consecutive scheduled runs with alerts working); PRD §11 release criteria are met or their exceptions are approved through H4; the verifier signs off.

#### ~~M19~~ (retired by ADR-069) — v2: Execution Engine (Track D) · depends on: M18; H5
- D4 `/execute` (F12): asset drafting, schedule, an approval queue with **no unapproved external actions**, launch-mode monitoring, alerts and prediction scorecards.
- **Accept when:** the D4 acceptance criteria pass on 1 real launch (this requires H5).

### 4.5 Directive re-plan (current; Directive §11, ADR-069)

The directive's eight steps, in order. Tracks can overlap; the dependencies listed are the only blockers. New milestone IDs M20–M28 are used because milestone IDs are never reused (§4).

| Step (Directive §11) | Milestone | Takes over from v2.0 |
|---|---|---|
| 1. Documents | **M20** | — (new) |
| 2. Cleanup and privacy | **M21** | M11 (accepted; its code part is done, ADR-051); cost-estimator part of M12 |
| 3. Brief feature | **M22** | M12 (accepted); M13; the run core of M14 |
| 4. Owner brief #1, pilot | **M23** | first half of M15 |
| 5. Owner brief #1, full run | **M24** | second half of M15 |
| 6. D3 plan | **M25** | M16 |
| 7. Launch mode and D5 tracking | **M26** | launch-mode, scheduling and ops parts of M14; M17 |
| 8. Operator guide | **M27** | M18 (the publishing parts are dropped: Directive §8.6) |
| (v2) Execution engine | **M28** | M19 |

#### M20 — Documents (step 1) · depends on: nothing
- Apply Directive §0: commit the directive unchanged, log one ADR per section (ADR-059 to ADR-069, plus ADR-070 and ADR-071), and update `docs/PRD.md`, `docs/DELIVERABLES.md`, this work order, `CLAUDE.md`, `.env.example`, `ops/HUMAN_INPUTS.md` and `docs/compliance/LEGAL_REVIEW_H2.md`, each change traceable to a directive section or ADR.
- **Accept when:** the `verifier` confirms every listed document is consistent with the directive and ADR-059 to ADR-071, before implementation starts (Directive §0.4).

#### M21 — Cleanup and privacy (step 2; Tracks A, C) · depends on: M20
- §5.2 cleanup: the archive tag and deletions are done (M11, ADR-051); confirm nothing global remains.
- §8.1 **roles-and-buckets migration**: migrate coded data to roles and buckets, then purge handles and pseudonyms; keep only the opt-out HMAC fingerprint with its separately stored key (ADR-066.1, ADR-071.1). Bot outcome stored on the coded record (ADR-071.2).
- §8.2 **snapshot purge job**: scheduled, logged, report final + 12 months (PRD R19.9).
- `LLM_BACKEND=api` wiring for product calls; per-stage models, Batch API, prompt caching and evidence trimming (PRD R15.8–R15.10); reconcile the brief `budget` fields with ADR-064.4.
- **Cost estimator** against the per-brief and monthly API caps, shown in CLI and UI (PRD R15.11, R18.5).
- Brief store moved to `~/.pigtail/briefs` and included in the encrypted backup; the CI brief check extended (ADR-071.3).
- Aggregate star anomaly checks replace per-account filtering; `analysis-params` new version (ADR-070.4).
- **Accept when:** a search of the stored data finds no handles or personal names (Directive §11 acceptance); the purge job runs on a fixture and writes its log; a product call on the example brief goes through `api` with the stage's model and is logged with model ID and prompt version; the cost estimate appears in CLI and UI and a run hard-stops at a cap below its estimate; briefs load from the new directory and appear in a backup; CI green; the verifier signs off.

#### M22 — Brief feature (step 3; Tracks B, D) · depends on: M21 (API wiring, estimator); GitHub token (H1 ✓)
- M12 delivered the schema, `/briefs` form, versioning and expansion. Remaining: candidate discovery (R4.5), the relevance filter with logged reasons (R4.6), the shortlist review UI with logged decisions and precision (R4.7), outcome sort, matching, balance and the sensitivity check (R4.8, R4.3, R4.9), and `pigtail run --brief <id> [--incremental]` with checkpoints and resume (R19.1).
- Reddit metadata-only and Trendshift remain off until the source matrix clears them (PRD R2.2).
- **Accept when:** on the example brief, run live, every candidate has a logged source, verdict and reason; every review decision is logged with reason and brief version; precision is computed; selection is deterministic for a given brief version and data version; balance diagnostics and the sensitivity check are produced; an interrupted run resumes correctly; the D7 criteria pass; the verifier signs off.

#### M23 — Owner brief #1: pilot (step 4; Tracks C, E, D) · depends on: M22; the owner's brief (private, outside git)
- The owner's brief lives only in her instance (`~/.pigtail/briefs`). Missing brief fields get defaults and are listed generically in `ops/HUMAN_INPUTS.md` for confirmation in the shortlist review; unresolved reference cases don't block the run (ADR-062).
- Pre-register the brief-level hypotheses and tests in `docs/preregistration/` before the outcome data they test is examined (Directive §7, ADR-065; ADR-050.9 for how the brief's success definition is committed by hash).
- Shortlist review by the owner (if she doesn't do it, the verifier skims it and precision is labelled "verifier-checked, not owner-checked", as in the v2.0 H3 default).
- Run the **first 5 cases** end to end with double coding and adjudication.
- **Cost report:** actual cost per case and the projection for the full brief in `ops/COSTS.md` and `ops/STATUS.md`. If the projection exceeds the brief's API cap, stop and raise H6 (Directive §6.4, ADR-064.4).
- **Evidence-decay measurement** at 1, 7 and 30 days (PRD R19.8; Directive §5.4).
- **Accept when:** the pre-registration was pushed before the outcome data was examined; the 5 cases are double-coded with per-field α; the cost report and projection are written; the decay measurement is reported (and any cadence change is an ADR); the verifier signs off.

#### M24 — Owner brief #1: full run (step 5) · depends on: M23; projection within the cap (else H6)
- The full run within the API cap; the brief report (the per-brief D2) with per-field α, the sensitivity check, transferability labels, absolute numbers and loser contrasts.
- Once the shortlist is final, purge cached data that no brief references and log the purge (PRD R19.10).
- `docs/reports/pilot.md` in the repo holds method-level results only (precision, per-field agreement, balance, sensitivity, evidence decay, costs, run times, deviations from the pre-registration), with no brief content, case names or coded values (publishing disabled, Directive §8.6).
- **Accept when:** the run completed within the cap; the D2 acceptance criteria pass on the owner's brief; the D1 criteria pass on its cases; 100% of claims resolve to snapshots; the public pilot report passes the private-data scan; the verifier signs off.

#### M25 — D3 plan (step 6; Track D) · depends on: M24
- D3 `/plan` from brief #1's report (F10) and the experiment registry (F11); a plan for the owner's project, kept in her instance.
- **Accept when:** the D3 acceptance criteria pass and a plan exists for the owner's project (Directive §11 acceptance).

#### M26 — Launch mode and D5 tracking (step 7; Track A, D) · depends on: M22; **must be ready before the owner's first launch**
- Launch mode (daily for 14 days, every 3 h on launch day; star polling every 3 h, hourly on launch day; triggered by a declared launch or a detected burst) (PRD R19.4, R19.5; ADR-070.3). Launch mode may use standard (non-batch) LLM calls.
- Refresh cadence and the per-source ceiling (≤ 60 days, derivation documented); launchd schedule; Docker only during runs; run reports; local alerts with the sanitized export and e-mail if configured; `pigtail doctor` checks FileVault; encrypted backups, with a restore tested on a second machine or the server path (R19.3, R19.6, R19.7).
- D5 `/analyze` (F17): stages 1–3, current state, benchmark, coverage score, tracking.
- **Accept when:** a simulated burst and a declared launch both switch the schedule and D1 shows hourly star lanes only for launch-mode cases; the D5 acceptance criteria pass, including the R17.5 validation on ≥ 5 deep-forensics cases from brief #1; a backup restores; the verifier signs off.

#### M27 — Operator guide (step 8; all tracks) · depends on: M24, M25, M26
- Install guide, brief guide, operator guide (macOS and optional server) with the "Responsible use" section (also in the README), methodology paper, codebook, schema reference, limitations (PRD §11).
- Compliance pack rewritten as a template (Directive §9, ADR-067); the owner's pack stays outside git.
- Cost expectations for the example brief, measured on real runs.
- **Accept when:** a new user runs a first brief in under an hour using the example brief (D6 timed test); three consecutive scheduled runs succeed with alerts working; the D6 criteria pass; the verifier signs off. Tagging a code release publishes no findings (H4 disabled).

#### M28 — v2: Execution Engine (Track D) · depends on: M27; H5
- D4 `/execute` (F12), as the retired M19. **Accept when:** the D4 acceptance criteria pass on 1 real launch (this requires H5).

## 5. Human gates

These are the **only** reasons to wait for the owner. Batch requests in `ops/HUMAN_INPUTS.md`, each with a deadline, what is blocked, and the default action taken if nobody answers. Keep working on everything that isn't blocked.

Gates per Directive §10 and ADR-068 (they replace the v2.0 table).

| ID | What | Blocks | Default if no answer within 14 days |
|---|---|---|---|
| H1 | The owner's own credentials and machine setup: an **Anthropic API key** (Commercial Terms) **with spend limits set in the Console**; a **GitHub token**; **FileVault** confirmed on; an **encrypted local backup location**; the **Claude subscription login for the agents**. Optional: BigQuery billing (free tier only), SMTP. | Live runs; product LLM calls (API key); backups (backup location) | Continue with what is available. Record missing sources as gaps. |
| H2 | The **narrowed legal consult**: the three questions in `docs/compliance/LEGAL_REVIEW_H2.md` (Directive §9) | **Publishing only**, and publishing is disabled anyway. It does **not** block collection under the mitigations of PRD §10. | Collection continues under the mitigations. Nothing is published. |
| H3 | **Optional** human calibration coding (about 2 h) | Nothing | LLM–LLM agreement only, with the "LLM-coded, not human-validated" label. |
| H4 | Publishing anything (findings, data, reports, public mode) | Any publishing | **Disabled** until the owner revokes Owner Directive 001. |
| H5 | Any external action on the owner's behalf, including **publishing her privacy notice** (the draft is shown to her first; ADR-071.4) | That action only; M28 | Don't act. |
| H6 | Any spend above the caps of PRD R15.11 (per-brief API cap; USD 200/month API; USD 0 other paid services) | That spend only | Don't spend. Degrade gracefully (the run hard-stops at the cap with a checkpoint). |

## 6. Hard rules

- Obey the PRD §4 non-goals and §10 compliance requirements.
- Never scrape around authentication, paywalls or robots/terms restrictions. A blocked source is recorded as a gap.
- Respect rate limits with margin. Identify collectors with a contact user-agent where the platform expects one.
- The repo is **public**. Never commit secrets, snapshots, raw records, handles, person-level data or any user's brief, including in ops logs, reports, test fixtures and commit messages. Fixtures must use synthetic data. Raw data lives only in private storage.
- **Brief content never goes into git**, ops logs and ADRs included; briefs live in `~/.pigtail/briefs` (ADR-055.1, ADR-071.3).
- **No handles or personal names in coded data**: roles and buckets only; the opt-out HMAC fingerprint is the only person-derived value kept (Directive §8.1, ADR-066.1, ADR-071.1).
- **Publishing is disabled** (H4): no findings, data or reports leave the owner's instance; public reports are method-level only (Directive §8.6, ADR-066.6).
- **Product LLM calls use `api`**; agents stay on the subscription at ≤ ~50% of the weekly allowance; never switch backends on your own (Directive §6, ADR-064).
- Nothing owner-specific in code, defaults, prompts or fixtures.
- In `subscription` mode (agents; other users' individual, non-commercial use), never read, copy, log or transmit Claude credentials. Use only the official `claude` CLI and Anthropic's login flow.
- Never invent citations, numbers or cases. Unknown means "unknown" in the output.
- Pre-register analyses (hypotheses, tests, thresholds) in `docs/preregistration/` **before** looking at the outcome data they test. For the pilot, that means before the outcome sort.
- Work in the open: every artifact, prompt, model ID and dataset version is committed or referenced.

## 7. Engineering standards

- Typed Python (mypy strict on core), ruff, pytest; TypeScript strict for the UI.
- Every collector ships with recorded fixtures and contract tests. Every schema (including the brief schema) ships with example documents and validation tests.
- Migrations are forward-only and tested.
- Runs are idempotent and resumable from checkpoints. Every run writes a `run` record with its brief version and cost.
- LLM calls go through one client wrapper that handles retries, caching (ADR-006), prompt caching and batching (PRD R15.9), cost logging, budget enforcement and model/prompt-version logging.
- Performance targets are the ones in `docs/DELIVERABLES.md` (D1 p95 ≤ 2 s; D3 ≤ 2 min; D5 ≤ 10 min and ≤ 6 h; D6 new-user install in under an hour). The v1 targets for Tier 1 runs and 24-hour live detection are retired.

## 8. Documentation requirements

- `docs/` contains: research, specs, methodology (codebook, pattern schema and methods paper), compliance (as a template), preregistration, reports, install guide, brief guide, operator guide, developer guide and schema reference.
- `ops/DECISIONS.md` holds ADRs for every non-trivial choice.
- `ops/STATUS.md` is a weekly digest for the owner, readable in 2 minutes: progress per milestone, what's blocked on her, costs to date, and notable findings.
- Every report states its brief version (where applicable), data version, code commit and limitations (Directive §2.3, ADR-060).

## 9. Owner reporting

The owner does not supervise individual steps. She reads `ops/STATUS.md` weekly and answers `ops/HUMAN_INPUTS.md` when it changes. If email is configured (H1), send the weekly status, run-report alerts and any new H-gate request by email.

## 10. Definition of done (ADR-048.4)

- M11, M12 and M20–M27 are accepted, and deliverables D1, D2, D3, D5, D6 and D7 pass their acceptance criteria in `docs/DELIVERABLES.md`. D4 follows in M28.
- **Directive acceptance** (Directive §11, ADR-069): every document is consistent with the directive (verifier sign-off); the global-collection code is removed and the archive tag exists; no handles remain in the stored data; owner brief #1 completes within budget, with a report showing per-field α, loser contrasts, absolute numbers and transferability labels; a plan exists for the owner's project.
- PRD §11 release criteria are met, or their exceptions are documented and approved by the owner.
- **Three consecutive scheduled runs succeed, with alerts working.**
- `docs/reports/final.md` summarizes the method, the pilot's method-level results, limitations and next steps.
