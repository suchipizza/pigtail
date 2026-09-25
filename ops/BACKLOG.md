# Backlog
Seeded by the orchestrator in M0 from docs/WORK_ORDER.md §4. Format: `- [ ] [ID] title — req IDs — depends on — acceptance`.
Priority order: top = next. `[x]` done · `[~]` in progress · `[!]` blocked (diagnosis inline).

## Now
- [ ] [M1-T27] When GITHUB_TOKEN arrives: validation runs M7 → M3 → M2 → M1 → M4 (detection-replan §8; order and commands in RUNLOG 2026-09-25)
- [ ] [M1-T28] Case API `detection_hours` should read `repo_count_snapshot` for v1 cases; daily wide-universe sweep (≥ 100 stars); lockstep alternative for events
- [x] [M3-T9] ADR-032..038 docs + M1-T24 code verified (round 2 PASS @80918ee)
- [ ] [M1-T25] D1 follow-ups: browser check (operator), p95 benchmark at 5,000 evidence items, generated TS types, streaming hash check for large snapshots, live refresh, audit-log reader
- [ ] [M1-T26] External liveness check for the scheduler (a dead scheduler can't alert itself)
- [x] [M1-T24] ADR-032 implemented on fixtures (ADR-037); live runs wait for GITHUB_TOKEN — was: Implement ADR-032: watch-list GraphQL snapshots, Search sweeps, HN→repo screen, star-history client (baseline, confirmation, scoring, backfill), per-repo events polling for tracked cases; case coverage fields — needs GITHUB_TOKEN (H1) for live runs; build and test on fixtures now
- [x] [M3-T6] Outcome spec updated to ADR-032 (star-history, day mapping)
- [x] [M3-T7] LIA/DPIA: per-repo events (A1b, D12/D13, R15), OpenDigger not processed
- [x] [M3-T8] ADR-035 applied (thresholds v0.2.0, spec, pre-reg amendment 1 ×2, case schema, codebook errata) — was: Apply ADR-035: thresholds v0.2.0 (new file, keep v0.1.0), spec §5, dated amendments to both pre-registrations, fix stargazers-API references in schemas/v0/case.schema.json (+example) and codebook depends_on_adrs; verifier check of M3-T6..T8
- [ ] [M1-T16] (revised) Validation plan M1–M7 from detection-replan §8, run with the project token
- [x] [M1-T21] Scheduler, health, alerts, Docker/systemd (ADR-033) — was: Scheduler: long-running `hn-ranks --loop`, daily `deletion-sync`, mentions for new cases within 48 h (R1.2); deploy when the host exists (H1)
- [ ] [M1-T22] Front-page minutes (`att.hn_frontpage_minutes`) from rank observations, handling polling gaps
- [ ] [M1-T23] HN follow-ups: clear `hn_story` titles for stories deleted upstream; a repo opt-out should also reach mentions of repos not in `repos`; `pigtail doctor` reports HN and ADR-022 flags; live check of Algolia
- [x] [M3-T5] Compliance docs: CB-02 per source + 16-day wording (ADR-038) — was: CB-02 status per source (HN implemented; GitHub events met by retention, ADR-038; Bluesky pending); update the 30→16-day wording in the LIA/DPIA text
- [x] [M1-T18] Detection re-plan verified → ADR-032; citation fixes applied; TM-32/33, LQ-27..29 added
- [ ] [M3-T7] LIA/DPIA: add per-repo events processing (TM-33) and LQ-29 to the balancing test and risk register: evaluate own GitHub event collection (Events API, full paging), candidate screening via Search API / HN / Bluesky / registries + stargazers API, and third-party trend services — feasibility, rate limits, cost, terms; recommendation checked by the verifier — ADR-028, G3, R1.1 — GITHUB_TOKEN (H1) for measurements
- [ ] [M1-T19] Retry and backfill missing GH Archive hours (404s are only marked missing today)
- [ ] [M1-T20] JSONL export alongside the DB (PRD §7); S3 service in CI; handles inside free text; run error-text redaction; batched detect() for backfills
- [x] [M2-T1] Literature review → docs/research/literature.md (80 citations; recommends StarScout for R3.3) — pending verifier spot-check M2-T4
- [x] [M2-T2] Source matrix + terms memos (25 sources: 12 cleared incl. conditions, 13 gaps) — pending verifier spot-check M2-T4
- [x] [M1-T1] Capture schema v0 (`evidence`, `case`, `repo`, `run`) as JSON Schemas + Postgres migrations; content-addressed snapshot store (SHA-256) on S3 — R1.4, §7 — M0 — examples + validation tests; forward-only tested migrations
- [x] [M1-T2] Connector interface: rate limiting, retries, terms metadata, enable flag, cost accounting, run records — R2.1 — M1-T1 — contract tests with recorded synthetic fixtures
- [x] [M1-T3] GH Archive velocity scan (daily; BigQuery if H1 GCP, else hourly dumps from data.gharchive.org) + bot filter v0 + case opening — R1.1 — M1-T2 — threshold configurable; opens cases on fixture data
- [x] [M1-T4] HN Algolia + Firebase connectors + `capture mentions` (off by default; ADR-031) — was: Mention capture: HN Algolia + Firebase — R1.2 — M1-T2, M2-T2 clearance — snapshot within 48 h
- [ ] [M1-T5] Mention capture: GitHub (REST/GraphQL) — R1.2 — M1-T2, GITHUB_TOKEN — snapshot within 48 h
- [ ] [M1-T6] Mention capture: Bluesky — R1.2 — M1-T2, M2-T2 clearance
- [ ] [M1-T7] Wayback save requests + CDX lookup — R1.2 — M1-T2, M2-T2 clearance
- [x] [M1-T8] Pseudonymization at ingest for every connector (keyed HMAC; key stored separately) — §10 — M1-T2
- [ ] [M1-T9] Announced-launch watchlist — R1.3 — M1-T2, M2-T2
- [ ] [M1-T10] Replay: rebuild stored records from snapshots — PRD §5.5 — M1-T3..T7 — replay reproduces records
- [ ] [M1-T11] Scheduler + health checks + alerting to ops/ALERTS.md; deploy to host — WO M1 — H1 host VM — 7 consecutive days of scans
- [x] [M1-T12] D1 preview built (API + login + audit + UI; ADR-034); browser check by operator pending — was: D1 preview: operator login, /cases, /cases/:id timeline + evidence tabs, "uncoded preview" label — D1 — M1-T3 — D1 preview criteria
- [x] [M1-T14] HN rank poller (`capture hn-ranks`), live-checked (ADR-031) — was: HN front-page rank polling (own rank history; cannot be backfilled later) — R1.2, §8.1 front-page minutes, lit. review open issue 1 — M1-T2 — polls every ≤ 5 min, snapshots stored
- [ ] [M1-T15] Confirm whether GH Archive records un-stars; define "net stars" for R1.1 accordingly — R1.1 — M1-T3
- [ ] [M1-T16] Star coverage check: GH Archive vs GitHub stargazers API on a sample; stargazer confirmation for candidate cases; recalibrate R1.1 thresholds — R1.1, ADR-009 — M1-T3, GITHUB_TOKEN
- [ ] [M1-T17] Early no-history connectors: Docker Hub pulls + Homebrew analytics daily snapshots — R2.2 — M1-T2
- [!] [M1-T13] Reddit connector — GAP per source matrix (commercial use needs a written agreement; no sharing with LLM providers). Revisit only after H2 (Q2)

## Compliance controls (docs/compliance/dpia.md; ADR-022 holds)
Before production capture on the host:
- [x] [CB-01] 24-month retention purge (`pigtail retention purge`; ADR-030)
- [~] [CB-03] Encryption at rest: SeaweedFS SSE via `S3_SSE_KEK` + `pigtail doctor`; open: enable on host, Postgres volume encryption (operator)
- [~] [CB-04] GH Archive raw-dump minimization: 30-day purge + verified re-fetch done (ADR-027.4); open: drop-after-parse, minimal-parse fallback when upstream disappears
- [ ] [CB-09] Pseudonym key rotation runbook
- [ ] [CB-12] Publish the privacy notice (needs owner placeholders); notice must also cover per-repo star/fork events (16-day retention) before that connector is enabled
- [ ] [CB-16] Breach runbook
- [ ] [CB-17] Encrypted backups; deletions re-applied after a restore
- [~] [CB-18] Log hygiene: redaction filter + `runs.error` scrubbing; open: install filter in all services, log rotation
Before enabling Bluesky, HN or V2EX:
- [~] [CB-02] Deletion sync: HN done (`privacy deletion-sync`); Bluesky push source open — R1.5
- [~] [CB-06] Profile URLs, DIDs, per-source namespaces redacted before LLM calls; open: gists, avatar URLs, bare handles in author fields
- [x] [CB-08] Data-subject requests (`pigtail privacy request access|erasure`); follow-up: rectification
- [x] [CB-13] Opt-out list enforced at ingest + purge (`pigtail privacy optout`)
Feature-specific:
- [ ] [CB-24] Drop raw GitHub search pages at parse (only owner type is used), like HN items (ADR-038)
- [ ] [CB-23b] Unparseable events pages: drop raw bytes immediately (today kept until the 30-day purge)
- [x] [CB-22] `person_level_30d` retention class + purge (migration 0007)
- [x] [CB-23] Per-repo events: star/fork only, raw dropped at parse, no stargazer-list path (tested)
- [x] [CB-05] LLM cache expiry (24 months) + evidence links + purge
- [ ] [CB-10] Account reach stored in bands
- [x] [CB-11] Codebook privacy rules (codebook v0.1.0 §12)
- [ ] [CB-14] Guard against naming people in outputs (before spread graphs, public mode, D3, D4)
- [ ] [CB-15] Record of processing activities
- [ ] [CB-19] UI login + audit log
- [ ] [CB-20] Suppress personal-account repos and matched losers in public outputs
- [ ] [CB-21] Operator guide: controller duties

## Next
- [ ] [M3-T10] Outcome spec: state μ_h = μ_d/24 in §2.1; point §5.4 and §9 to the calibration pre-registration; H-eval/H-sealed split
- [ ] [M4-T4] Implement the holdout split rule (H-cal / H-eval / H-sealed) with the pre-registered test vectors; scheduled star-history re-fetches at 1/3/7/14/21 days for the settle_lag calibration
- [x] [M3-T4] Compliance docs' CB statuses updated (ADR-030)
- [~] [CB-18b] RedactingFilter: all scheduled jobs + `capture scan`; open: other manual CLI commands
- [x] [LQ-27] Added to legal-review-questions (with LQ-28 OpenDigger, LQ-29 stargazer identities) — was: Add legal question: after an erasure, replay briefly re-processes the erased person's data before dropping it at ingest (ADR-030.1) — acceptable?
- [ ] [M5-T1] Coding prompts must include the codebook P1 instruction (ignore personal characteristics); verifier checks it — codebook §12, DPIA R9
- [ ] [M2-T3] Fake-star method: select, reproduce on a sample, document validation — R3.3 — M2-T1
- [x] [M2-T4] Verifier spot-check — round 1 FAIL, round 2 PASS @191c44d (2026-09-25)
- [ ] [M2-T5] Minor source-matrix follow-ups: Algolia earliest-item wording, cite yc-oss README, add #312 notes (Events API cache; merged-PR events) — verifier round 2 optional
- [ ] [M3-T0] Check whether GH Archive still records merged PRs (PullRequestEvent closed+merged) on a sample of real hours; if not, the §8.1 "returning external contributors" metric needs the GitHub API — §8.1 — M1-T3
- [x] [M3-T1] Outcome model spec + outcome-thresholds v0.1.0 (ADR-012..021) (2026-09-25) — verifier review pending with the M3 acceptance
- [x] [M4-T0b] Forecasting target decided (ADR-026) — was: target for §9.1 forecasting test ("30-day outcome class" vs classes needing T+90): provisional T+30 `attention_top_decile` label, or predict the T+90 class from day-7 data — decide before M4-T2 pre-registration
- [x] [M3-T3] Superseded by ADR-032 (star-history endpoint budget in detection-replan §6) — was: Cost estimate: stargazer-API full histories for ≥ 5,000 Tier 1 repos (rate limits, 40k cap, ordering) — ADR-012 — M1-T16
- [x] [M3-T2] Compliance pack (LIA, DPIA, retention, privacy notice, LQ-1..26); H2 raised (2026-09-25)
- [x] [CB-07] CLI telemetry/error reporting off in the subscription subprocess; BackendError no longer echoes output (2026-09-25)
- [x] [M4-T0] α threshold decided: gate 0.70 (PRD), 0.80 for `high` confidence (ADR-025)
- [x] [M4-T1] Codebook v0.1.0 + schemas/codebook/v0.1.0.json (ADR-024/025) (2026-09-25)
- [x] [M4-T1b] 13 seed candidate mechanism cards (docs/methodology/mechanisms/candidates.md) — was: Seed candidate mechanism cards from practitioner sources (lit. [63][64][65]) so the pilot can code C11a/C11b — M4-T1
- [ ] [M4-T1c] Align `reliability` description in schemas/v0/evidence.schema.json with codebook §2.3 anchors — M1 merged; ready
- [ ] [M5-T0] Citation validator must apply the same deterministic redaction to the snapshot before matching quoted spans (R7.1) — codebook open issue 2
- [x] [M4-T2] Pilot + forecasting-test pre-registrations committed (ADR-029) — was: Pre-register pilot analyses in docs/preregistration/ (incl. minimum units per core field so thin fields count as unassessed, codebook open issue 3; §9.1 forecasting target M4-T0b) — WO §6 — M4-T1
- [x] [M4-T2b] Threshold-calibration pre-registration written (ADR-039 sealed holdout) — was: Pre-register threshold calibration (ADR-019) before any class base rate is computed
- [x] [M4-T1d] Done in codebook v0.2.0 (ADR-039) — was: Codebook §3.1–3.2 burst/quiet derivation and §2.1/§2.3 examples: move from stargazers API / starscout_filtered to raw star-history (days); codebook patch version + changelog; must land before pilot unitizing
- [x] [M4-T2c] `novelty_claim` in codebook v0.2.0 — was: Codebook: add a general `novelty_claim` field (MC-09), in the next minor version
- [ ] [M4-T3] Pilot (blocked on: HN capture under ADR-022 controls; case pool depends on M1-T18; H1 training-off confirmation or API key): 3 winners + 3 matched losers across ≥ 3 strata; double coding + adjudication; G1 check by verifier — WO M4 — M4-T1, M1 snapshots

## Later (gated)
- [ ] [M5] Engine v1 — depends on G1
- [ ] [M6] Scale runs, G2 — depends on M5
- [ ] [M7] Evaluation — depends on M6 + ≥ 60 days of live data
- [ ] [M8] Plan generator (D3) — depends on G2
- [ ] [M9] v1 release — depends on M7, M8, H2, H4
- [ ] [M10] Execution engine (D4) — depends on M9, H5

## Done
- [x] [M0-T6] Pushed; CI green on GitHub; **M0 accepted by verifier** (2026-09-25)
- [x] [M0-T7] Verifier fixes: phone redaction, prompt fingerprint, cache-while-paused, runner scan-before-push (2026-09-25)
- [x] [M0-T1] Handoff package added to the repo; backlog seeded (2026-09-25)
- [x] [M0-T2] Scaffold: uv (Python 3.12), ruff, mypy strict, pytest, pre-commit, CI workflow, `.env.example` (2026-09-25)
- [x] [M0-T3] docker compose: Postgres 16 + SeaweedFS S3 + bucket init; verified locally (2026-09-25)
- [x] [M0-T4] `LLMClient` with subscription and api backends, cache, usage ledger, limit pause/resume, per-job overrides, redaction; subscription structured-output smoke test passed (2026-09-25)
- [x] [M0-T5] Private-data scan (CI + pre-commit) with tests; ops files; H1 raised (2026-09-25)
