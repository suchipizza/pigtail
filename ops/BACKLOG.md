# Backlog
Seeded by the orchestrator in M0 from docs/WORK_ORDER.md §4. Format: `- [ ] [ID] title — req IDs — depends on — acceptance`.
Priority order: top = next. `[x]` done · `[~]` in progress · `[!]` blocked (diagnosis inline).

## Now
- [~] [M0-T6] Push to origin and confirm CI green on GitHub — WO §3.5 — M0-T1..T5 — all CI jobs pass
- [x] [M2-T1] Literature review → docs/research/literature.md (80 citations; recommends StarScout for R3.3) — pending verifier spot-check M2-T4
- [~] [M2-T2] Source matrix + terms memos → docs/research/source-matrix.md, docs/compliance/terms-memos.md — R2.2, R2.3 — — covers every R2.2 source; cited
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
- [ ] [M1-T13] Reddit connector — R2.2 — compliance clearance + H1 Reddit approval

## Next
- [ ] [M2-T3] Fake-star method: select, reproduce on a sample, document validation — R3.3 — M2-T1
- [ ] [M2-T4] Verifier spot-check of 10 citations across M2 docs — WO M2 — M2-T1, M2-T2
- [ ] [M3-T1] Outcome model spec → docs/specs/outcome-model.md; versioned class thresholds — §8, R3.5 — M2-T2
- [ ] [M3-T2] Compliance pack: LIA, light DPIA, retention policy, privacy notice, legal-review questions; raise H2 — §10 — M2-T2
- [ ] [M4-T0] ADR: stricter α ≥ 0.80 for promotion-deciding fields (lit. review open issue 4; stricter than PRD §9.2, not a relaxation) — decide before pilot pre-registration
- [ ] [M4-T1] Codebook v0 + adaptive modules → docs/methodology/codebook.md — F6 — M2-T1
- [ ] [M4-T2] Pre-register pilot analyses in docs/preregistration/ — WO §6 — M4-T1
- [ ] [M4-T3] Pilot: 3 winners + 3 matched losers across ≥ 3 strata; double coding + adjudication; G1 check by verifier — WO M4 — M4-T1, M1 snapshots

## Later (gated)
- [ ] [M5] Engine v1 — depends on G1
- [ ] [M6] Scale runs, G2 — depends on M5
- [ ] [M7] Evaluation — depends on M6 + ≥ 60 days of live data
- [ ] [M8] Plan generator (D3) — depends on G2
- [ ] [M9] v1 release — depends on M7, M8, H2, H4
- [ ] [M10] Execution engine (D4) — depends on M9, H5

## Done
- [x] [M0-T1] Handoff package added to the repo; backlog seeded (2026-09-25)
- [x] [M0-T2] Scaffold: uv (Python 3.12), ruff, mypy strict, pytest, pre-commit, CI workflow, `.env.example` (2026-09-25)
- [x] [M0-T3] docker compose: Postgres 16 + SeaweedFS S3 + bucket init; verified locally (2026-09-25)
- [x] [M0-T4] `LLMClient` with subscription and api backends, cache, usage ledger, limit pause/resume, per-job overrides, redaction; subscription structured-output smoke test passed (2026-09-25)
- [x] [M0-T5] Private-data scan (CI + pre-commit) with tests; ops files; H1 raised (2026-09-25)
