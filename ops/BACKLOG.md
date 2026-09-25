# Backlog
Seeded by the orchestrator in M0 from docs/WORK_ORDER.md §4. Format: `- [ ] [ID] title — req IDs — depends on — acceptance`.
Priority order: top = next. `[x]` done · `[~]` in progress · `[!]` blocked (diagnosis inline).

## Now
- [x] [M2-T1] Literature review → docs/research/literature.md (80 citations; recommends StarScout for R3.3) — pending verifier spot-check M2-T4
- [x] [M2-T2] Source matrix + terms memos (25 sources: 12 cleared incl. conditions, 13 gaps) — pending verifier spot-check M2-T4
- [ ] [M1-T1] Capture schema v0 (`evidence`, `case`, `repo`, `run`) as JSON Schemas + Postgres migrations; content-addressed snapshot store (SHA-256) on S3 — R1.4, §7 — M0 — examples + validation tests; forward-only tested migrations
- [ ] [M1-T2] Connector interface: rate limiting, retries, terms metadata, enable flag, cost accounting, run records — R2.1 — M1-T1 — contract tests with recorded synthetic fixtures
- [ ] [M1-T3] GH Archive velocity scan (daily; BigQuery if H1 GCP, else hourly dumps from data.gharchive.org) + bot filter v0 + case opening — R1.1 — M1-T2 — threshold configurable; opens cases on fixture data
- [ ] [M1-T4] Mention capture: HN Algolia + Firebase — R1.2 — M1-T2, M2-T2 clearance — snapshot within 48 h
- [ ] [M1-T5] Mention capture: GitHub (REST/GraphQL) — R1.2 — M1-T2, GITHUB_TOKEN — snapshot within 48 h
- [ ] [M1-T6] Mention capture: Bluesky — R1.2 — M1-T2, M2-T2 clearance
- [ ] [M1-T7] Wayback save requests + CDX lookup — R1.2 — M1-T2, M2-T2 clearance
- [ ] [M1-T8] Pseudonymization at ingest for every connector (keyed HMAC; key stored separately) — §10 — M1-T2
- [ ] [M1-T9] Announced-launch watchlist — R1.3 — M1-T2, M2-T2
- [ ] [M1-T10] Replay: rebuild stored records from snapshots — PRD §5.5 — M1-T3..T7 — replay reproduces records
- [ ] [M1-T11] Scheduler + health checks + alerting to ops/ALERTS.md; deploy to host — WO M1 — H1 host VM — 7 consecutive days of scans
- [ ] [M1-T12] D1 preview: operator login, /cases, /cases/:id timeline + evidence tabs, "uncoded preview" label — D1 — M1-T3 — D1 preview criteria
- [ ] [M1-T14] HN front-page rank polling (own rank history; cannot be backfilled later) — R1.2, §8.1 front-page minutes, lit. review open issue 1 — M1-T2 — polls every ≤ 5 min, snapshots stored
- [ ] [M1-T15] Confirm whether GH Archive records un-stars; define "net stars" for R1.1 accordingly — R1.1 — M1-T3
- [ ] [M1-T16] Star coverage check: GH Archive vs GitHub stargazers API on a sample; stargazer confirmation for candidate cases; recalibrate R1.1 thresholds — R1.1, ADR-009 — M1-T3, GITHUB_TOKEN
- [ ] [M1-T17] Early no-history connectors: Docker Hub pulls + Homebrew analytics daily snapshots — R2.2 — M1-T2
- [!] [M1-T13] Reddit connector — GAP per source matrix (commercial use needs a written agreement; no sharing with LLM providers). Revisit only after H2 (Q2)

## Compliance controls (docs/compliance/dpia.md; ADR-022 holds)
Before production capture on the host:
- [ ] [CB-01] 24-month retention purge job (person-level) — retention-policy.md
- [ ] [CB-03] Encryption at rest: snapshot bucket + database
- [ ] [CB-04] GH Archive raw-dump minimization (hash kept, bytes purged ≤ 30 d, replay re-downloads) — briefed to M1 engineer
- [ ] [CB-09] Pseudonym key rotation runbook
- [ ] [CB-12] Publish the privacy notice (needs owner placeholders)
- [ ] [CB-16] Breach runbook
- [ ] [CB-17] Encrypted backups; deletions re-applied after a restore
- [ ] [CB-18] Log hygiene (no content or handles in logs)
Before enabling Bluesky, HN or V2EX:
- [ ] [CB-02] Deletion sync (Bluesky ≤ 48 h) — R1.5
- [ ] [CB-06] Redact profile URLs, `did:` identifiers and bare handles before LLM calls
- [ ] [CB-08] Data-subject request tooling (access, objection, erasure)
- [ ] [CB-13] Honour explicit refusals (opt-out list)
Feature-specific:
- [ ] [CB-05] Time limit on the LLM cache, linked to evidence (before Tier 2 extraction)
- [ ] [CB-10] Account reach stored in bands
- [ ] [CB-11] Codebook privacy rules (M4)
- [ ] [CB-14] Guard against naming people in outputs (before spread graphs, public mode, D3, D4)
- [ ] [CB-15] Record of processing activities
- [ ] [CB-19] UI login + audit log
- [ ] [CB-20] Suppress personal-account repos and matched losers in public outputs
- [ ] [CB-21] Operator guide: controller duties

## Next
- [ ] [M2-T3] Fake-star method: select, reproduce on a sample, document validation — R3.3 — M2-T1
- [x] [M2-T4] Verifier spot-check — round 1 FAIL, round 2 PASS @191c44d (2026-09-25)
- [ ] [M2-T5] Minor source-matrix follow-ups: Algolia earliest-item wording, cite yc-oss README, add #312 notes (Events API cache; merged-PR events) — verifier round 2 optional
- [ ] [M3-T0] Check whether GH Archive still records merged PRs (PullRequestEvent closed+merged) on a sample of real hours; if not, the §8.1 "returning external contributors" metric needs the GitHub API — §8.1 — M1-T3
- [x] [M3-T1] Outcome model spec + outcome-thresholds v0.1.0 (ADR-012..021) (2026-09-25) — verifier review pending with the M3 acceptance
- [x] [M4-T0b] Forecasting target decided (ADR-026) — was: target for §9.1 forecasting test ("30-day outcome class" vs classes needing T+90): provisional T+30 `attention_top_decile` label, or predict the T+90 class from day-7 data — decide before M4-T2 pre-registration
- [ ] [M3-T3] Cost estimate: stargazer-API full histories for ≥ 5,000 Tier 1 repos (rate limits, 40k cap, ordering) — ADR-012 — M1-T16
- [x] [M3-T2] Compliance pack (LIA, DPIA, retention, privacy notice, LQ-1..26); H2 raised (2026-09-25)
- [x] [CB-07] CLI telemetry/error reporting off in the subscription subprocess; BackendError no longer echoes output (2026-09-25)
- [x] [M4-T0] α threshold decided: gate 0.70 (PRD), 0.80 for `high` confidence (ADR-025)
- [x] [M4-T1] Codebook v0.1.0 + schemas/codebook/v0.1.0.json (ADR-024/025) (2026-09-25)
- [ ] [M4-T1b] Seed candidate mechanism cards from practitioner sources (lit. [63][64][65]) so the pilot can code C11a/C11b — M4-T1
- [ ] [M4-T1c] Align `reliability` description in schemas/v0/evidence.schema.json with codebook §2.3 anchors — after M1 merge
- [ ] [M5-T0] Citation validator must apply the same deterministic redaction to the snapshot before matching quoted spans (R7.1) — codebook open issue 2
- [ ] [M4-T2] Pre-register pilot analyses in docs/preregistration/ (incl. minimum units per core field so thin fields count as unassessed, codebook open issue 3; §9.1 forecasting target M4-T0b) — WO §6 — M4-T1
- [ ] [M4-T3] Pilot: 3 winners + 3 matched losers across ≥ 3 strata; double coding + adjudication; G1 check by verifier — WO M4 — M4-T1, M1 snapshots

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
