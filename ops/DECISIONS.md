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

## ADR-003 — Repository layout (2026-09-25)
Context: The work order asks for a monorepo with uv. The Python engine comes first; the TypeScript UI starts with the D1 preview (M1).
Options: (a) uv workspace with several Python packages; (b) one Python package `src/pigtail` with subpackages, plus `ui/` for TypeScript later.
Decision: (b). One package (`pigtail.llm`, later `pigtail.capture`, `pigtail.connectors`, …), `schemas/` for JSON Schemas, `infra/` for deploy config, `ui/` for the React app when it starts. Python pinned to 3.12 via `.python-version` and `uv.lock`.
How to reverse: Split subpackages into uv workspace members; imports stay the same.

## ADR-004 — LLM cache and usage ledger in local SQLite for M0 (2026-09-25)
Context: R15.4 needs a cache and ledger now; the Postgres capture schema arrives in M1.
Decision: `pigtail.llm.store.LLMStore` uses SQLite under `PIGTAIL_DATA_DIR` (gitignored). The interface (cache_get/put, record, pause, summary) stays the same when it moves to Postgres.
How to reverse: Implement the same interface on Postgres and swap it in `build_client`.

## ADR-005 — SeaweedFS instead of MinIO for local object storage (2026-09-25)
Context: `docker compose up` must start object storage. The `minio/minio` image (Docker Hub and quay.io) can no longer be pulled anonymously.
Options: SeaweedFS (Apache-2.0, S3 API), RustFS (young project), Garage.
Decision: SeaweedFS `chrislusf/seaweedfs:4.47`, pinned, with S3 credentials from `S3_ACCESS_KEY`/`S3_SECRET_KEY`, and a bucket created by an `aws-cli` init container. The code only uses the S3 API, so any private S3-compatible bucket works in production.
How to reverse: Replace the `objectstore` service; nothing else depends on the implementation.

## ADR-006 — LLM cache key and redaction scope (2026-09-25)
Context: R15.4 keys the cache on (prompt version, input hash). The parity check (R15.7) must compare backends, and a model change must not serve stale outputs.
Decision: The key is (prompt id, prompt version, redacted-input hash, output-schema hash, model, backend). Identifier stripping (e-mails, phone numbers, @handles → keyed pseudonyms) runs before **every** model call on **both** backends, not only in subscription mode. It's the conservative reading of PRD §10 and costs nothing.
How to reverse: Drop fields from `LLMClient.cache_key`; make redaction conditional on the backend.

## ADR-007 — Default product model `claude-opus-5` (2026-09-25)
Context: PRD F15 doesn't name a model. The same model ID has to work on both backends so the parity check compares like with like.
Decision: `LLM_MODEL=claude-opus-5` by default for both backends. Cheaper models can be set per deployment. Bulk jobs may get a per-job model once the pilot shows the agreement holds (R15.7-style check, logged as an ADR).
How to reverse: Set `LLM_MODEL`.

## ADR-008 — Subscription-backend scope confirmed against Anthropic's Claude Code legal page (2026-09-25)
Context: ADR-001 asked for a re-check at M0.
Finding (https://code.claude.com/docs/en/legal-and-compliance, accessed 2026-09-25): Free/Pro/Max use of Claude Code falls under the Consumer Terms. Running Claude Code inside products or services requires the Commercial Terms, an unmodified binary, and each end user authenticating with their own credentials; the provider may not pay for, resell or intermediate Claude usage for end users.
Decision: Keep R15.6 as written. The `subscription` backend is for an operator running pigtail for themselves on their own plan, through the unmodified official CLI. Any deployment that serves other users must use `api`. The operator guide says so. Re-check before release (M9).
How to reverse: n/a (compliance constraint). Revisit if Anthropic's terms change.

## ADR-009 — Don't rely on GH Archive alone for star velocity (2026-09-25)
Context: The source audit (docs/research/source-matrix.md) found open, unanswered community issues on the gharchive repo (#310, #320). They report event volume dropping after May 2025 and star (WatchEvent) capture at about 10–20% since Feb 2026. These are community reports, not confirmed. Verifier check (M2-T4): #320's comparison covers only 2 repos. #310's comments point to a likely systematic cause (the crawler polls only page 1 of the Events API). #312 reports a further ~100× drop from 2025-10-09. If the cause is systematic, the loss is probably biased toward high-activity periods, which are exactly the bursts pigtail detects. So the coverage ratio has to be measured per case, not assumed constant. GH Archive also records only star `started` events, so R1.1 "net stars" can't be computed from it.
Options: (a) trust GH Archive; (b) switch star series to the GitHub API only (rate-limited; not feasible for universe-wide scanning); (c) use GH Archive for screening, confirm candidates against the GitHub stargazers API, and record per-case coverage.
Decision: (c). Detection stays pluggable by source. Every case records a coverage ratio (observed vs reference). The absolute thresholds in R1.1 are read as "observed in GH Archive" until they are recalibrated against measured coverage (M1-T16). The fake-star reproduction (R3.3) must report coverage for its window.
How to reverse: If coverage is confirmed to be ≥ 95%, drop the confirmation step and keep the coverage field.

## ADR-010 — Source clearances adopted from the M2 source matrix (2026-09-25)
Context: The source matrix classified 25 sources: 1 cleared, 11 cleared with conditions, 13 gaps.
Decision: Connectors are built only for CLEARED or CLEARED-WITH-CONDITIONS sources, in the matrix's priority order, and must meet the conditions listed in docs/compliance/terms-memos.md. Wayback and V2EX stay off by default until H2 answers Q5 and Q8. Reddit, YouTube, X, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu, Bilibili, TrustMRR, Crunchbase (unless the operator brings a licence) and the YC directory are documented gaps (R2.3). Outcome metrics that depend on gap sources are tagged `self_reported` or `unknown` in the M3 outcome model. This is conservative until the legal review; it does not relax any PRD requirement.
How to reverse: Change a source's clearance after H2 answers; a new ADR per source.

## ADR-011 — Role e-mail addresses allowed by the private-data scan (2026-09-25)
Context: The source matrix cites a platform's published contact address (hello@…). The scan blocked it as personal data.
Decision: Generic role addresses (hello@, info@, support@, legal@, …) are allowed; personal addresses are still blocked. Commits are gated on the scan passing (it failed once without gating: see RUNLOG 2026-09-25; no personal data was involved).
How to reverse: Remove the role-address pattern from `EMAIL_ALLOW`.

## ADR-012 — Stars for scoring come from the GitHub stargazers API (2026-09-25) — SUPERSEDED by ADR-032
Context: PRD §8.1 names GH Archive as the primary source for stars. ADR-009 records under-capture since 2025-05, and GH Archive has no un-star events. The 24-month universe falls almost entirely in that period.
Options: GH Archive primary; API only; API for scored cases, with GH Archive as a fallback only when the measured coverage is ≥ 0.90.
Decision: The third option. Store three series: `raw`, `bot_filtered` and `starscout_filtered`. The API series is net of un-stars and survivor-biased, so scoring uses the first fetch after T+k (docs/specs/outcome-model.md).
How to reverse: If M1-T16 measures coverage ≥ 0.95, GH Archive becomes primary again.

## ADR-013 — Community metrics come from the GitHub API for scored cases (2026-09-25)
Context: The crawler loss may also affect PR and issue events, PR payloads have been trimmed since 2025-10-07, and M3-T0 hasn't run yet.
Decision: The API is primary for every cohort, so cohorts stay comparable. GH Archive values are tagged `estimated` and can't feed outcome classes until M3-T0 reports.
How to reverse: M3-T0 shows that merged-PR events are complete.

## ADR-014 — Verification tags plus a status field; nothing is imputed (2026-09-25)
Decision: Tags are `verified | self_reported | estimated | unknown`, extending R3.2 with `unknown` as ADR-010 set out. A separate status field takes `observed | pending | unknown | not_applicable`. A project with no package is `not_applicable`; an ecosystem we have no source for is `unknown`. Values are never imputed.
How to reverse: Fold status back into tags.

## ADR-015 — How the time anchor T is chosen (2026-09-25)
Context: R3.1 says "first burst or declared launch" without saying which wins. Anchoring on the burst alone would select on the outcome.
Decision: Use the declared launch if it falls within 30 days before the burst, otherwise the burst onset hour (found with a per-hour excess rule). Announced and manual cases use the launch date. Ties go to the launch, then to the lowest evidence id.
How to reverse: Change the look-back window or the anchor priority, then recompute every case.

## ADR-016 — "Returning external contributor" (2026-09-25)
Decision: At least 2 merged PRs in [T, T+k), with the first and last merge ≥ 30 days apart. This interprets PRD §8.1's "across ≥ 2 months". "External" means `author_association` is not OWNER, MEMBER or COLLABORATOR, and the author is not a bot. The metric is not applicable at T+7 or T+30.
How to reverse: Use a calendar-month rule instead.

## ADR-017 — Provisional category taxonomy and how it is assigned (2026-09-25)
Decision: 15 provisional categories. The inputs are frozen as of T. A rule-based pre-pass runs first, then an LLM classification through `LLMClient`. 10% of cases are double-coded, and low-confidence cases go to the review queue. Codebook v0 (M4-T1) may replace the taxonomy; if it does, every case is normalized again.
How to reverse: Adopt the codebook taxonomy.

## ADR-018 — Normalization cells (R3.4) (2026-09-25)
Decision: A cell is (category, quarter of T). Adoption cells are further restricted to the primary ecosystem at T. Percentiles are mid-rank within pigtail's universe, not within all of GitHub. A cell needs n ≥ 30. The fallbacks, in order, are (category, year), then (all categories, quarter), then `unknown`.
How to reverse: Change n or the fallback order through a new thresholds version.

## ADR-019 — Outcome thresholds v0.1.0 (2026-09-25)
Decision: `schemas/outcome-thresholds/v0.1.0.json` (provisional). Classes are checked in this order: winner, short_lived, attention_only, slow_riser, plateau, unclassified. A zero value never counts as "top". An unknown input blocks a class only when it could change the result. Business metrics are excluded from classes. "Matched loser" is a matching role, separate from the `plateau` class. Only the M4 pilot may calibrate the thresholds, and it freezes them as v1.0.0. Any later change needs an ADR and a held-out re-run on a 30% hash split (salt `pigtail-outcome-holdout-v1`).
How to reverse: A new thresholds version plus an ADR.

## ADR-020 — The fake-star filter removes stars at campaign level (2026-09-25) — default series for classes amended by ADR-035
Options: Remove every star from a flagged account; or remove them only for repos StarScout flags as targeted by a campaign.
Decision: Campaign level. The low-activity signature also matches legitimate new users, and the campaign rule is the paper's own guard against false positives. Classes are computed on the filtered series, then re-run on the raw series, and any flip is flagged.
How to reverse: Remove stars at account level.

## ADR-021 — MRR is never tagged "verified" under current clearances (2026-09-25)
Context: PRD §8.1 lists MRR as `verified` from Stripe-verified dashboards such as TrustMRR. TrustMRR is a GAP (ADR-010).
Decision: MRR is `self_reported` when the operator enters it with a citation, and `unknown` otherwise. This changes a metric default, not a §4, §5, §9.2–9.3 or §10 requirement.
How to reverse: A source with verifiable MRR is cleared.

## ADR-022 — Interim holds until compliance controls exist (2026-09-25)
Context: The compliance pack (docs/compliance/dpia.md) concludes a full DPIA is needed. It also found that capture currently has no retention purge, no encryption at rest, no minimization of raw GH Archive dumps, and no published notice or request handling.
Decision (in force until the listed controls exist and/or H2 answers):
- No production capture on the host until CB-01 (retention purge), CB-03 (encryption at rest), CB-04 (raw-dump minimization), CB-09 (key rotation runbook), CB-12 (published notice), CB-16 (breach runbook), CB-17 (encrypted backups) and CB-18 (log hygiene) exist. Local development continues.
- No person-level source beyond GH Archive (Bluesky, HN, V2EX, Discord) until CB-01 (retention purge), CB-02 (deletion sync), CB-03 (encryption at rest), CB-06 (identifier redaction before LLM calls), CB-08 (data-subject request tooling), CB-12 (published notice) and CB-13 (honouring refusals) exist. **This ADR is the single authoritative list**; the compliance docs refer to it (verifier M3 round 1).
- No account-level spread graphs until LQ-8 is answered.
- No private individuals, matched losers or personal-account repos named in any output.
This is stricter than the PRD and doesn't relax any requirement.
Update (2026-09-25): CB-04 is partly met by ADR-027.4 (30-day raw purge + verified re-fetch); drop-after-parse and a minimal-parse fallback are still open.
How to reverse: Lift each hold when its controls are verified, or when H2 answers the related question.

## ADR-023 — Subscription backend stays the default, limited to the owner's own use (2026-09-25)
Context: PRD §12 makes `subscription` the default. The compliance pack found that Anthropic's Consumer Terms (Pro/Max) in the EEA/CH include a no-commercial-or-business-use clause, that no zero-retention option exists on those plans (30-day retention even with training off), and that there is no DPA. PRD §10 requires only pseudonymization plus training off in subscription mode, so this isn't a PRD violation, but it is a legal risk.
Options: (a) switch the product default to `api` now; (b) keep `subscription` for the owner's own non-commercial research use, with telemetry off (CB-07, done), pseudonymized inputs, no bulk person-level coding until LQ-1/2 are answered, and heavy jobs routed to `api` per job when a key exists.
Decision: (b). It's the most reversible option, it matches the owner's explicit choice (ADR-001), and the question is raised in H2 (LQ-1, LQ-2).
How to reverse: Set `LLM_BACKEND=api`. Nothing else changes (D6 acceptance).

## ADR-024 — Codebook v0.1.0 adopted for the pilot (2026-09-25) — items 1 and 3 amended by ADR-039
Context: M4-T1 produced docs/methodology/codebook.md and schemas/codebook/v0.1.0.json. It hasn't been tested on coded data yet.
Decisions (all v0, calibrated in the M4 pilot; any change needs a semver bump plus a changelog entry, R6.3):
1. **Two event layers.** `burst` and `quiet` are computed from the filtered star series (onset rule from outcome-model §2.1; burst end 3 d, merge gap 7 d, minimum quiet 7 d). `prep`, `launch`, `relaunch` and `pivot` are coded from evidence. This avoids having to agree on where events start and end. To reverse: code all six and add them as core fields.
2. **Spread-graph edges** are `supported` only at evidence level ≥ "attributed", with valid hashes on both ends and the source posted before the target. To reverse: raise the minimum to "explicit".
3. **Trigger windows:** 48 h before to 6 h after onset; 7 days for newsletters. Candidates are ranked in a fixed order, not by weights. `none_observed` does not mean "organic", and every attribution lists the channels we can't observe. The pilot calibrates this.
4. **Reach bands** (log10): r0 none, r1 < 1k, r2 1k–9,999, r3 10k–99,999, r4 ≥ 100k, stored at ingest (CB-10). Changing them is a major version bump.
5. **Categories:** ADR-017's 15 categories adopted unchanged, with boundary rules added. This closes ADR-017's open point.
6. **`unknown` in α:** counts as a value for nominal fields; counts as missing for ordinal fields, plus a separate known-vs-unknown α that must also pass. This stops coders inflating α by answering "unknown".
7. **G1's "< 10% of categories changed"** is counted over the enum values in the codebook JSON (79 in v0.1.0, so at most 7 may change per revision round).
8. **Account nodes** are defined but disabled (`account_nodes_enabled: false`, ADR-022). The public-figure rule stays inactive until LQ-7 is answered.
How to reverse: Each item as noted, through a codebook version bump.

## ADR-025 — Mechanism confidence levels, and the α threshold for promotion-deciding fields (resolves M4-T0) (2026-09-25)
Context: PRD §9.3 leaves confidence levels to the codebook. The literature review notes that α ≥ 0.70 is in Krippendorff's "tentative conclusions" band.
Options: (a) require α ≥ 0.80 on the promotion-deciding fields for any promotion; (b) keep the PRD gate at 0.70 and tie 0.80 to `high` confidence.
Decision: (b). G1 and promotion use the PRD's α ≥ 0.70. `low` = candidate; `medium` = promoted under §9.3; `high` = promoted plus ≥ 10 cases across ≥ 3 strata, both §9.3 routes, α ≥ 0.80 on the promotion-deciding fields (C3, C4, C9, C11a, C11b) and no sensitivity flags. α is reported per field on every card. This is stricter than the PRD for `high` and relaxes nothing.
How to reverse: Move to (a) through an ADR before the pilot is pre-registered; after pre-registration, only with a held-out re-run.

## ADR-026 — Target for the §9.1 forecasting test (resolves M4-T0b) (2026-09-25)
Context: PRD §9.1 test 1 predicts "the 30-day outcome class" from data available 7 days after detection. The §8.2 classes need T+90 (and T+365) data, so there is no class at 30 days.
Options: (a) primary target is a provisional T+30 label (`attention_top_decile`, a binary label from outcome-thresholds v0.1.0), predicted from day-7 data; (b) predict the full T+90 class from day-7 data; (c) change the PRD test.
Decision: (a) is the primary, pre-registered target, because it matches the PRD's 30-day horizon and data from day 7. (b) is pre-registered as a secondary test and reported with equal prominence. Both use the PRD baseline (star velocity + category base rate), Brier score, ≥ 200 cases, and a 95% bootstrap CI. Nothing is relaxed: the pass criterion applies to (a).
How to reverse: Swap primary and secondary through an ADR before any outcome data for the test exists (the pre-registration timestamp governs).

## ADR-027 — Capture-layer design choices, M1 core (2026-09-25)
1. Hourly aggregates are stored in Postgres (`repo_hourly_activity`), not DuckDB/parquet. `gharchive_hours` tracks which hours were scanned, so the baseline can tell a quiet hour from an unscanned one. To reverse: export to parquet for analytics (DuckDB stays the analytics engine per PRD §12).
2. Bot filter v0: logins matching `[bot]` or known bot patterns are dropped before pseudonymization. A repo-hour is flagged as lockstep when ≥ 20 of its stargazers had no other activity in the window and they make up ≥ 90% of its stargazers. Raw and filtered counts are both stored. "Net stars" = distinct stars observed, because GH Archive has no un-star events.
3. Baseline: only scanned hours count; unscanned hours are unknown, not zero. Variability below the square root of the mean is ignored. With no history the absolute threshold applies and the case is labelled `baseline_quality: none`. Cases have a 14-day cooldown and deterministic ids.
4. Raw hourly dumps are snapshotted whole, then purged after `GHARCHIVE_RAW_RETENTION_DAYS` (default 30; CB-04). The evidence record keeps the URL, hash and fetch time and is marked `raw_dropped`. Replay re-downloads and refuses the file if its hash has changed.
5. Snapshot layout is `sha256/ab/cd/<hash>` with a `.meta.json` sidecar holding the first capture's metadata. Snapshot refs are `local:` or `s3://`. Reads re-check the hash.
6. Record ids are deterministic (`case_…`, `ev_…`; `run_` + uuid). Migrations are forward-only, and editing an applied file is rejected.
How to reverse: Per item, through a migration and an ADR.

## ADR-028 — GH Archive can't be used to detect breakouts; M1 detection re-plan (2026-09-25)
Context: The M1 real smoke run (17 real hours, 2026-09-24/25) observed 108–428 stars per hour across all of GitHub. For the 25 repos with the most GH Archive stars, public star counts rose by 548 in 37 minutes, while GH Archive recorded 4 stars: a ratio of about 0.007. That is far below the 10–20% in the community reports (ADR-009). With this coverage R1.1's threshold can't be reached, and scanning GH Archive would open almost no cases. G3 (open a case within 24 h of a breakout) can't be met from GH Archive.
Options: (a) wait for a GH Archive fix (unmaintained); (b) pigtail runs its own GitHub event collection within API terms (Events API with a token; the loss mechanism is page-limited polling, so polling more often and paging fully might work); (c) screen for candidates through other signals (GitHub Search API for recently starred or created repos, HN/Show HN and Bluesky launch posts, registry download spikes), then measure star velocity with the stargazers API; (d) third-party trend services (terms to check).
Decision: Keep the GH Archive scan as one screen, since it costs nothing and its coverage is measured. Open research task M1-T18 to evaluate (b), (c) and (d) on feasibility, rate limits, cost and terms, with a verifier-checked recommendation. G3 is at risk until then, and STATUS says so. Nothing in the PRD is relaxed: R1.1's thresholds stay; only the data source changes.
How to reverse: If GH Archive coverage is restored (checked monthly: M1-T16), it becomes primary for screening again.

## ADR-029 — Pilot pre-registration decisions (2026-09-25; made before any pilot coding and before any outcome data exists)
1. **Reliability supplement.** Extra units are coded only to measure α. Six cases can't supply ≥ 30 pairable units for the per-case and per-burst fields (C4, C5, C9, C10), and without the supplement those fields would fail G1 automatically. This adds units and relaxes nothing.
2. **Founder-audience proxy (provisional).** The owning organisation's stars on its other repos before T, in bands. The codebook or M5 may replace it with channel-level audience measures.
3. **Known/unknown companion statistics** (extras added by ADR-024, not required by the PRD). When unknowns fall below the prevalence floor, the α companion is replaced by an agreement bound: the coders' unknown rates differ by ≤ 0.05 and raw known/unknown agreement is ≥ 0.95 over ≥ 30 units. The PRD §9.2 core-field α ≥ 0.70 is unchanged.
4. **Backend for pilot coding.** About 50–100 cases is a pilot, not "bulk" coding under ADR-023. It may run on `subscription`, with pseudonymized, redacted inputs (CB-06) and telemetry off (CB-07), **only after the owner confirms model training is off** (H1). Otherwise it runs on `api` if a key exists, and waits if neither is available. Pass B uses a different model when one is available.
How to reverse: An amendment in docs/preregistration/ before attempt 1 starts; after that, only with a new dated amendment stating what data has been seen.

## ADR-030 — Privacy-operations design (CB-01, 03, 05, 06, 08, 13, 18) (2026-09-25)
1. **Erasure drops whole snapshots.** A snapshot can't be edited without breaking its content hash, so a data-subject erasure drops every snapshot containing the person and records the drop in the append-only `deletion_log`. Replay re-downloads the snapshot and drops the person at ingest, because they are on the opt-out list. To reverse: store per-record snapshots (more storage, more objects).
2. **Retention periods are hard caps.** Setting a value above the retention policy (24 months person-level; 12 months for run error text) is rejected at startup.
3. **CLI names:** `pigtail privacy optout|request|requests` and `pigtail retention purge`, rather than the DPIA's `privacy lookup|export|suppress|erase`. Rectification and a separate lookup command aren't built (follow-up).
4. **No raw handles are stored for opt-outs or requests.** Opt-out entries must be pseudonyms, enforced by a database CHECK. Request logs store no handle and no pseudonym.
5. **Person-level tables must be registered.** Any table holding pseudonyms is registered in `PERSON_TABLES` (`src/pigtail/privacy/deletion.py`) so that retention and erasure reach it. Today person-level data lives only in snapshots and the LLM cache.
6. **Encryption at rest (CB-03, partly done).** SeaweedFS SSE-S3 is turned on by setting `S3_SSE_KEK` (bucket default encryption is set by the init job). `pigtail doctor` reports bucket encryption. Postgres volume encryption is an operator duty, reported as MANUAL.
How to reverse: Per item, through a new ADR plus a migration.

## ADR-031 — Hacker News capture design (2026-09-25)
1. **The rank poller (`hn_ranks`) runs without the ADR-022 flag and is on by default.** HN rank history can't be backfilled (literature review §7.3), and the poller keeps only project-level data: story ids, ranks, URLs, titles, scores and times. The raw item JSON contains `by`, so it is snapshotted as `person_level_24m` (snapshot before parse) and its bytes are dropped right after parsing (hash kept, `raw_dropped`, logged). The id-list snapshot is project-level and kept. Intervals under 1 minute are refused (TM-04).
2. **Person-level HN connectors (`hn_firebase`, `hn_algolia`) are off by default.** Enabling them raises `PersonSourceHold` unless `PIGTAIL_ADR022_PERSON_SOURCES_OK=1`, which the operator sets only when ADR-022's preconditions are met.
3. **Deletion sync (CB-02, HN).** `dead`, `deleted` and missing items count as deleted upstream. Every snapshot holding such an item is dropped (this extends ADR-030.1), the evidence is marked `deleted_upstream` (hash kept; this overrides `raw_dropped`), and derived rows are removed. Items linked to open cases are checked daily, others monthly, and each must be acted on within 7 days. Push sources (Bluesky) plug into the same interface.
4. **Mention matching.** The queries are the `github.com/owner/name` URL, `owner/name`, and name plus owner. Name-only hits are kept only with `--loose`. The owner is never searched alone, since it may be a person.
5. **Connector base additions:** a group enable flag, `person_level_hold`, a `check()` that stores nothing and runs while disabled, and a per-fetch `retention_class`.
How to reverse: Per item, through an ADR (1: turn off `hn_ranks`; 3: switch to per-item snapshots to reduce collateral drops).

## ADR-032 — Breakout detection and star series without GH Archive or stargazer lists (supersedes ADR-012; updates ADR-028) (2026-09-25)
Context (docs/research/detection-replan.md, verified 2026-09-25 apart from citation fixes):
- GitHub restricted `/repos/{o}/{r}/stargazers` to admins and collaborators on 2026-06-30, and GraphQL `stargazers` returns nothing. ADR-012's source is gone.
- New endpoint `GET /repos/{o}/{r}/stargazers/history` (2026-09-04): weekly and daily net counts back to the repo's creation, no identities, no 40k cap. Day boundaries are "not guaranteed to align with UTC" (they are consistent with US Pacific time).
- GH Archive captured ≈ 2% of stars on 2026-09-24. Our own `/events` polling is limited by GitHub (300 events, a 60 s poll interval) to ≤ ~3%, and faster polling would break ToS §H.
- The OpenDigger mirror (gharchive issue #323) has no stated licence and a single operator. Its counts agreed with GitHub for the repos it saw (1.013×), but its misses can't be observed with that method.
Decision:
1. **Screening (candidate discovery):** hourly GraphQL star and fork counts for a watch list of up to 50k repos (100 repos per query at cost 1). GitHub Search sweeps. GitHub URLs from the HN rank poller (plus Show HN). GH Archive stays on as a cheap control. **OpenDigger is off by default** until H2 answers LQ-28 and a TM-32 memo clears it; if cleared, it becomes a drop-in screen.
2. **R1.1 check:** on the public net star count (watch-list snapshots, confirmed with the star-history endpoint). The bot filter becomes a confirmation step based on per-repo event polling (15–60 min intervals, within GitHub's 60 s poll interval) for tracked cases. Each case records which data its bot filter used and a coverage ratio.
3. **Scoring series (replaces ADR-012):** daily net stars from the star-history endpoint (`verified`, net, days not UTC-aligned: documented). `raw` = star-history. `bot_filtered` and `starscout_filtered` are computed only where per-repo event data exists (from tracking onwards, or through the GH Archive ~2% before that); otherwise they are `unknown`. They are never imputed.
4. **One identity:** a single `GITHUB_TOKEN`. No GitHub App on top of a PAT to double the limits (ToS §H, conservative reading). The steady-state budget is ≈ 61% of REST, 14% of GraphQL and 12% of search.
5. **G3:** repos on the watch list are detected within ~1–2 h. Repos outside it depend on Search and HN picking them up first; this is stated as a limitation.
Nothing in the PRD is relaxed. R1.1's thresholds are unchanged; only the data source changes. R3.3 fake-star filtering is limited by data availability, and every window reports it.
How to reverse: If GH Archive or OpenDigger is cleared with verified coverage ≥ 0.95 (sampled from the universe or star-history, not from the mirror itself), it may become the primary screen again through a new ADR.

## ADR-033 — Unattended runtime: scheduler, health and alerts (2026-09-25)
1. **A stdlib loop, not APScheduler.** State lives in the existing `runs` table (`scheduler.<job>` records), so no new dependency or migration is needed, a restart loses nothing, and `pigtail health` reads the same state from any process.
2. **One Postgres advisory lock per job.** After taking the lock the job must still be due, so two schedulers never repeat work. Runs left "running" by a killed scheduler are marked abandoned and retried.
3. **Jobs run in child processes** that install the log-safety filter before any job code runs (CB-18b for scheduled jobs). Retries back off exponentially, capped at the job's interval.
4. **Alerts stay on the host** (`PIGTAIL_DATA_DIR/alerts/`, mode 0600), with optional SMTP. Only a sanitized summary (rule, severity, counts, times) goes to `ops/ALERTS.md`, via `pigtail alerts export`. WORK_ORDER M1 says alerts go to `ops/ALERTS.md`, but the repo is public, so alert details stay local.
5. **`/healthz` returns 503 only when the loop is dead or the database is unreachable.** Job failures and doctor warnings raise alerts instead of restarts.
6. **The GH Archive scan window** runs from midnight two days back to now − 2 h, so the scheduler catches up after up to 2 days of downtime.
7. **Mention capture** is skipped (logged, not failed) unless the HN person-level flags and the ADR-022 flag are set. Run records store case ids, never repo names.
How to reverse: Replace the loop with APScheduler behind the same job config; move alert delivery elsewhere.

## ADR-034 — D1 preview app: access, audit and what it shows (2026-09-25)
1. **The private, operator-only UI shows repo `full_name`, including personal-account owners.** ADR-022's "no personal-account repos named in any output" and DPIA R14/CB-20 are read as covering **public** outputs. The private app is behind operator authentication (R13.3). Public mode (D2) must still suppress them.
2. **The audit log stores no IP addresses.** It keeps a keyed hash of the address cut to its network (IPv4 /24, IPv6 /48), just enough to rate-limit logins and spot brute force. The key comes from the password hash, so it changes with the password. Rows expire after `LOG_RETENTION_DAYS`. Uvicorn's access log is off by default.
3. **Snapshots are served raw to the logged-in operator.** They are sandboxed (a CSP with no scripts or external requests; gzip dumps download instead of opening), the hash is re-checked, the view is audited, and dropped or deleted-upstream content returns 410. They are not redacted: the operator is the controller's own staff, and redaction would break hash verification.
4. **Sessions are stored server-side in Postgres, as hashes only.** Logout and expiry happen on the server (12 h absolute, 120 min idle).
5. **All reads go through a pool that Postgres forces into read-only mode** (R14.2).
How to reverse: Per item, through an ADR (for 1: suppress personal-account repos in the private UI as well).

## ADR-035 — Outcome classes use the raw star-history series; thresholds v0.2.0 (amends ADR-020; made before any outcome data exists) (2026-09-25)
Context: Under ADR-032, `starscout_filtered` and `bot_filtered` need stargazer identities, which exist only for repos polled from before T (or through GH Archive's ~2%). With outcome-thresholds v0.1.0 classing on the filtered series, almost every retrospective case would be `unclassified:missing_data`, and the pilot couldn't classify its cases (outcome-model O11).
Options: (a) keep v0.1.0 (pilot can't run); (b) mix series per case (one cell would compare two different series); (c) class on `raw` star-history for every case, with the filtered series as a pre-registered sensitivity analysis where eligible, and a flag on class flips.
Decision: (c). Thresholds bump to **v0.2.0**. v1.0.0 stays reserved for the pilot freeze; before 1.0.0, a change to a class input bumps the minor version. ADR-020's default changes accordingly. Cases where the filtered series is available and the class flips are flagged, and known fake-star campaigns (StarScout campaign flag) are reported per stratum. Both pre-registrations get dated amendments stating that no outcome data had been seen. PRD R3.3 still holds: raw and filtered series are both stored wherever they can be computed.
How to reverse: Class on the filtered series again once per-repo event coverage makes it available for ≥ 90% of cases (thresholds version bump plus a held-out re-run if after the pilot freeze).

## ADR-036 — ADR-022 applies to per-repo GitHub events, plus two controls (amends ADR-022) (2026-09-25)
Context: Per-repo event polling (ADR-032, TM-33) is a person-level source other than GH Archive. DPIA R15: it could rebuild stargazer lists that GitHub restricted on 2026-06-30.
Decision: Per-repo events fall under ADR-022's full person-level preconditions (CB-01, 02, 03, 06, 08, 12, 13) and additionally need:
- **CB-22:** a `person_level_30d` retention class with purge (≤ 30 days);
- **CB-23:** keep only star and fork events at parse, drop the raw bytes immediately (hash kept), and never build or export a per-repo stargazer list.
The connector stays off (`PIGTAIL_ENABLE_GITHUB_EVENTS=0` plus the ADR-022 flag) until then, and LQ-29 may stop it altogether. Until it runs, the bot-filter confirmation and the filtered series going forward stay `unavailable`, which ADR-035 makes non-blocking for classes.
How to reverse: Drop the per-repo events source entirely (the bot filter stays unavailable) or lift the hold after H2 answers LQ-29.

## ADR-037 — Detection v1 implementation choices (M1-T24) (2026-09-25)
1. **Cases open on the public net star count with the bot filter `pending`; confirmation comes later.** R1.1 says "after bot filtering", but for most repos no bot-filter data exists until tracking starts (ADR-032, ADR-036). Waiting would lose the evidence that capture exists to save (the capture layer is exempt from the pilot gate, PRD §12). Every case records `bot_filter.status|basis|confirmed`. Analyses and outcome classes treat `confirmed = false` cases as flagged, and report `pending`/`unavailable` shares per stratum. This adapts R1.1's source, not its threshold; §9.2–9.3 gates are untouched.
2. **Only the login-based bot rules are applied to per-repo events.** The lockstep rule needs each account's activity elsewhere on GitHub, which per-repo events don't show; applied to stars and forks alone, it would flag organic bursts. It's recorded as not applied (`layers=["login_rules"]`, `stars_lockstep=null`).
3. **v0 (GH Archive) and v1 share the `velocity` trigger and the 14-day cooldown,** so one breakout opens one case. Agreement is recorded in `detection_agreement`.
4. **Watch-list policy:** up to 50k repos. Opted-out, missing and private repos are dropped. Unpinned entries not nominated for 30 days are dropped unless they gained ≥ 30 stars in 7 days. Above the cap, the lowest priority goes first.
5. **GitHub budget:** an hourly cap per bucket (core 70%, GraphQL 70%, search 1,260/h), per-run caps, and a server-side reserve (hard stop when under 30% is left). Spend is shared across processes through `github_budget_ledger`.
6. **Star-history day labels are stored as GitHub returns them.** America/Los_Angeles is assumed only to decide "today" and window days, until validation M2 (the DST change on 2026-11-01) confirms it.
7. **A candidate is rejected when star-history shows < 90 stars for the 48 h window** (90% of the R1.1 minimum, to allow for day misalignment). Calibrate this with validation M4.
8. **Search snapshots are classed `person_level_24m`,** because results embed owner objects.
9. **API version pinned to `2026-03-10`,** the version the star-history docs are published under.
How to reverse: Per item, through an ADR (1: open cases only after confirmation, accepting evidence loss).

## ADR-038 — Per-repo GitHub events: 16-day default retention; CB-02 met by short retention (amends ADR-036) (2026-09-25)
Context: The compliance update for CB-22/23 found two things. (1) GitHub imposes no deletion obligation on API consumers, and there is no GitHub deletion-sync source, while ADR-036 lists CB-02 as a precondition for this source. (2) ADR-037.2 means the lockstep rule isn't applied to per-repo events, so the pseudonymous actor rows only de-duplicate stars within a case window (14-day cooldown + 48 h), which needs ~16 days, not 30.
Decision: The default `GITHUB_EVENTS_RETENTION_DAYS` becomes **16** (ceiling stays 30), following data minimization. For this source, CB-02 is satisfied by raw bytes dropped at parse plus actor rows expiring within 16 days (≤ 30 max). Un-stars and deleted accounts upstream are therefore reflected within that period, and a separate sync would add nothing. The other ADR-022 preconditions still apply (CB-12 open; CB-03 and CB-06 partial). LQ-29 items 4–5 stay open for the lawyer.
Also: raw GitHub search pages should be dropped at parse (only the owner type is used), as HN items are (backlog CB-24).
How to reverse: Raise the default (≤ 30) with a documented purpose; add a GitHub deletion-sync source if GitHub or H2 requires one.

## ADR-039 — Codebook v0.2.0, a pre-1.0 semver rule, and a sealed second holdout (2026-09-25; before any coding or outcome data)
1. **Codebook v0.2.0.** It combines M4-T1d (burst and quiet periods derived from raw star-history days, with hourly watch-list snapshots only setting onset precision) and M4-T2c (a case-level `novelty_claim` field). A new field is a minor change. Changing the series a derived field comes from would be major, but 1.0.0 is reserved for the pilot freeze, so **before 1.0.0 a would-be-major change bumps the minor version** (same rule as ADR-035 for the thresholds). v0.1.0 was never used for coding. The nine enums counted for G1 (79 values) are unchanged.
2. **Day-precision trigger window.** Bursts whose onset is known only to the day get a day-level trigger window and can't get `high` trigger confidence. Most pilot bursts will be day-precision (watch-list snapshots only exist from 2026-09).
3. **ADR-024 items 1 and 3** now refer to raw star-history (ADR-035) instead of the filtered series.
4. **μ_h = μ_d / 24** when a daily baseline is used at hourly precision.
5. **Sealed second holdout.** The 30% held-out set is split deterministically into H-eval (20% of all cases: the forecasting test and M6 analyses after the freeze) and H-sealed (10%: never classed or inspected until a post-freeze threshold change needs a held-out re-run; used once and labelled). After that single use, further changes can only be evaluated prospectively on new cases. Without this, "held-out re-runs" after the freeze would be on data already seen.
How to reverse: 1–4 through a codebook or spec version bump; 5 only before the calibration pre-registration is committed.

## ADR-040 — Capture follow-ups: backfill, front-page minutes, opt-outs by name, raw drops (2026-09-25)
1. **GH Archive backfill (M1-T19).** A missing hour (404, 5xx/429 after retries, network error, or a dump that won't decompress) is retried with backoff (1 h, 2 h, 4 h … capped at 24 h), within a 7-day window. Days that come back are re-aggregated and detection re-runs for the following 47 hours.
2. **Front-page minutes (M1-T22).** Minutes at rank ≤ 30 are counted between polls. A gap longer than 2× the poll interval isn't counted and is reported as uncovered; the tail after the last poll counts only if it's within 2× the interval. Results are tagged `verified`, `estimated` (lower bound) or `unknown`. Stories are matched by URL only; title matching (outcome-model A2) isn't built yet.
3. **HN rank-poller stories are tracked by deletion sync.** A story deleted upstream has its title and URL cleared (`deletion_log` action `fields_cleared`), while the rank history stays.
4. **Opt-out by repo name** for repos not yet in `repos`. The implementation stores an **unkeyed** SHA-256 of the name. Repo names are public and enumerable, so that hash is reversible by dictionary and reveals which repos opted out. **It must become a keyed hash (HMAC with `PSEUDONYM_KEY`) before production (CB-13b).** GH Archive ingest still filters by repo id only, so the id is added once the repo enters `repos`.
5. **Raw drops.** GitHub search pages are dropped at parse (CB-24). Person-level pages that fail to parse are dropped immediately, and only counts go into the run record (CB-23b).
6. **Every CLI command installs the redacting log filter** (CB-18b) and logs at INFO level to stderr.
How to reverse: Per item, through an ADR.

## ADR-041 — HN front-page minutes may be a labelled lower bound when polling starts mid-window (2026-09-25)
Context: Outcome-model §2.4 says a window that starts before a source's coverage is `unknown`. The implemented HN front-page metric (ADR-040.2) returns an `estimated` value with `lower_bound: true` when polling covers part of the window.
Decision: Allowed for this metric only, because it is descriptive and never an input to classes, the forecasting test or mechanism promotion. It must carry `estimated`, `lower_bound: true` and its uncovered minutes wherever it's shown. Any analysis that uses it as a covariate or outcome must either treat such values as `unknown` or pre-register how it handles lower bounds.
How to reverse: Return `unknown` for partly covered windows (a one-line change in `hn_frontpage.py`).

## ADR-042 — Keyed name opt-outs, holdout guard, settle-lag collection, JSONL export, liveness (2026-09-25)
1. **Name opt-outs (CB-13b)** are `rk_` + HMAC-SHA256(`PSEUDONYM_KEY`, `repo_name:<owner/name lowercase>`). Existing unkeyed rows are **kept and still matched** (kind `repo_name_unkeyed`), because dropping them would silently resume collecting from repos whose owners objected. A trigger blocks new unkeyed rows, `pigtail privacy optout rekey` converts entries whose names appear in local data, and `pigtail doctor` warns while any unkeyed row remains. With keyed entries present and no key, the refusal list fails closed (`MissingNameKey`). Rotating `PSEUDONYM_KEY` requires re-adding name opt-outs (CB-09 runbook).
2. **Holdout guard (M4-T4).** `pigtail.analysis.split` implements calibration §1.1 exactly. H-sealed needs an `UnsealToken` naming an ADR in `ops/DECISIONS.md`, and every unseal is written to the append-only `holdout_unseal_log` before the computation. H-eval is refused until the thresholds are frozen (calibration §1.4). "Used once" is logged, not enforced; the verifier checks it.
3. **settle_lag collection (K2).** Only repos whose cases are all in the calibration split are enrolled (cap 100 per day). Each is re-fetched at +1/3/7/14/21 days, with fetches more than 24 h late marked `missed`. Rows are append-only. It only runs once `GITHUB_TOKEN` exists.
4. **JSONL export (M1-T20, PRD §7).** Every table must be classified `project`, `person` or `never`; an unclassified table stops the export. `repo_event_actor` is **never** exported (ADR-036, CB-23). Output inside the pigtail repo is refused, and person-level output inside any git working tree is refused.
5. **Liveness (M1-T26).** The scheduler writes a heartbeat file on every tick. `pigtail health --liveness-file … --alert` is run by a systemd timer or cron, preferably on a second host, and raises `scheduler_dead`.
6. **Case API:** detection-v1 cases show hourly rows from `repo_count_snapshot` (`detection_hours_source`).
How to reverse: Per item, through an ADR.

## ADR-043 — Key-change detection is a production precondition; no scheduled key rotation yet (amends ADR-022) (2026-09-25)
Context: The CB-09 runbook found that changing `PSEUDONYM_KEY` silently disables every opt-out. The refusal list stores people only as keyed pseudonyms, so under a new key the entries stop matching and collection resumes for people who objected. Erasure would also miss earlier pseudonymous rows. Nothing detects a key change today.
Decision:
- **CB-25 (key fingerprint: store a fingerprint of the key and make `pigtail doctor` and every collector refuse to run on a mismatch) joins ADR-022's production preconditions** (the list of controls needed before production capture on the host).
- Until CB-26/27 (re-derivation under a new key; dual-key matching during rotation) exist, the key is rotated **only after a compromise**, following the runbook, not on a schedule. retention-policy §3.5 is changed accordingly.
- CB-29 (the `ui` service loading the whole `.env` including the key; `Settings.pseudonym_key` visible in `repr()`) is fixed before production too.
How to reverse: Once CB-26/27 exist, scheduled rotation can return through an ADR.

## ADR-044 — Repo opt-out purge covers every repo-keyed table; encrypted backups whose restore re-applies deletions (2026-09-25)
1. **`REPO_TABLES` registry (CB-13c).** Every column that ties a row to a repo is registered, with its purge action (delete, or keep the row with the repo link removed). A schema test fails if any repo-keyed column is unregistered. On opt-out, watch-list rows are **deleted**, not deactivated: the keyed opt-out entry is the record that the repo opted out, and a plain-text row would show which repos opted out. Snapshots shared with other repos (GraphQL batches, GH Archive dumps, HN front pages) are kept; only the opted-out repo's derived rows are deleted.
2. **Backups (CB-17).** A backup is one stream (header, manifest, `pg_dump`) encrypted to `BACKUP_RECIPIENT` with age, or gpg as a fallback. Unencrypted output and output inside a git working tree are refused. Backups are pruned after 35 days. Snapshot bytes aren't in the backup; they rely on bucket replicas with the same 35-day expiry.
3. **Restore re-applies deletions.** Restore carries the live `deletion_log`, opt-out list and request log over (opt-outs are only ever added back), restores in one transaction, replays every tombstone, then re-applies the opt-out list and runs the retention purge. So deleted data can't come back. If the live database is lost too, deletions made after the last backup are lost; the command warns, and the operator re-enters them from the request records (follow-up: ship `deletion_log` and opt-outs off the host continuously).
4. Backups run on the host, which needs `pg_dump` 16 and age. The app image has neither (follow-up: a backup container or scheduler job).
How to reverse: Per item, through an ADR.

## ADR-045 — Key fingerprint check, secret hygiene, retention caps, unparseable snapshots (2026-09-25)
1. **CB-25.** The fingerprint is `kfp1_` + 128 bits of HMAC(`PSEUDONYM_KEY`, "pigtail-key-fingerprint-v1"), recorded on first use. Every path that pseudonymizes or matches opt-outs refuses on a mismatch (exit 2, alert): the opt-out list, the connector base, privacy commands, backup restore before anything is replaced, and scheduler startup. `pigtail privacy key-fingerprint --reset --confirm-rotation` is the only way to accept a new key, and resets go to an append-only history table plus a run record. On restore, the live fingerprint wins. `pigtail doctor` reports ok, mismatch or unset. `retention purge` isn't gated on the key, because deleting is always safe. The LLM client doesn't check it (it has no database connection).
2. **CB-29.** The `ui` service gets an explicit list of environment variables (no `PSEUDONYM_KEY`, tokens, SMTP or API keys). `repr(Settings)` masks secrets, including `database_url` and the S3 keys.
3. **CB-32.** `GHARCHIVE_RAW_RETENTION_DAYS` is capped at 30.
4. **CB-33.** `retention purge` (daily job) deletes UI audit rows older than `LOG_RETENTION_DAYS`, plus expired sessions.
5. **CB-34.** An unparseable snapshot no longer aborts access, erasure or opt-out purges. Erasure and opt-out purges drop it if any of its evidence is person-level (deleting is the safer side), with a tombstone.
6. **Bug fixed:** the connector base used `suppression or Suppressions()`, so an empty opt-out list was replaced and the key check silently skipped. It now uses `is not None`.
How to reverse: Per item, through an ADR.

## ADR-046 — Key rotation by re-derivation; CB-27 not needed; alert and backup operations (amends ADR-043) (2026-09-25)
1. **`pigtail privacy rekey` (CB-26)** rotates the key in one transaction. It refuses while other database sessions are connected and locks the opt-out list, the fingerprint table and the person tables. Old→new pseudonyms are re-derived from handles the operator supplies (a file outside git, mode 0600), from repo names pigtail still holds, and from retained raw snapshots. **No opt-out may be lost:** any unmappable opt-out makes the command refuse, with no override. Unmappable person-level rows are deleted only with `--drop-unmapped` or `--purge-person-level` (logged as `key_rotation`). The LLM cache is cleared, and the fingerprint switches as the last write.
2. **CB-27 (dual-key matching) is not needed.** The rotation is atomic and CB-25 refuses any process running with the wrong key, so there is never a mixed-key period.
3. **Scheduled rotation may return** now that CB-26 exists (reversing ADR-043's "only after compromise"). The runbook defines the procedure.
4. **Restore refuses backups taken before the last `rekey`,** which would bring old-key pseudonyms back. **Decision: also refuse backups taken before a bare `--reset`** (same risk; follow-up CB-35).
5. **Alerts (CB-30, CB-31):** failed-login and snapshot-integrity rules on the UI audit log, which report counts only. Alert files rotate at 1 MB or 30 days, and archives are deleted by their first event within `LOG_RETENTION_DAYS` (≤ 365).
6. **Backups (CB-17b):** `pigtail doctor` checks `BACKUP_RECIPIENT` and backup age (warn after 2 days, fail after 7). Optional scheduler jobs for backup create and prune ship disabled.
7. **CB-28:** `pigtail llm cache clear`.
How to reverse: Per item, through an ADR.

## ADR-047 — CR-002: re-scope to brief-driven neighbourhood analysis (owner decision; the owner called it "ADR-004", but that number was already taken) (2026-09-26)
Context: The owner's goal is to launch her own open-source project well, by learning from recent, relevant launches. Pigtail must stay versatile: any user can run it on their own project's neighbourhood. Pigtail ships a method, not data. Each user runs their own instance with their own credentials, and nothing is shared or collected centrally. Nothing in the code is specific to the owner.
Decision (CR-002, with the owner's answers of 2026-09-26):
1. **Research brief** (new core object): versioned YAML plus a guided form at `/briefs`; several briefs per install. Flow: project description → LLM expansion (problem, users, keywords, topics, competitors; editable by the user) → candidate discovery (GitHub search and topics, Show HN, awesome-lists, Trendshift as an optional per-user source, GH Archive restricted to the brief's topics and used for discovery signals only) → LLM relevance filter against a written rubric, with the reason logged per candidate → shortlist review (accept, reject or add, all logged; precision logged, target ≥ 80%) → outcome sort → 15–25 winners and 15–25 matched losers → deep forensics → neighbourhood report → plan. Window: 12–18 months.
2. **Success definition**: one primary dimension plus minimum thresholds on the others (for example, "top quartile on adoption and at least the median on attention"). Weights are an advanced option only. Every report includes a sensitivity check (does the winner set change under reasonable alternative definitions?) and flags the affected cases. **PRD §5.3 is amended:** a composite may rank candidates within a brief, but results are always shown per dimension.
3. **Cut:** Tier 1 at scale, the global 24-month backfill, the global mechanism library and its promotion rule, the PRD §9.1 test suite, and the causal toolkit beyond event studies and winner/loser contrasts. Mechanism cards become **neighbourhood patterns**, each showing n, the loser contrast and counterexamples.
4. **Keep:** snapshot or drop, matched losers, multi-metric outcomes, batch runs and launch mode (CR-001, ADR-048), pseudonymization, and the subscription/API switch (always the operator's own credentials).
5. **Pages:** `/briefs` is new. D1 and D5 are unchanged. D2 becomes the per-brief neighbourhood report (what worked against losers; what's trending in the last 3–6 months). D3 builds plans from that report. D4 stays v2.
6. **Continuous-collection code:** the current state is tagged `archive/global-collection`. The 50k-repo watch list, the all-GitHub search sweeps and the global breakout detection are then deleted. Kept for launch mode and briefs: the HN front-page poller, the scheduler (now running batch and launch-mode runs) and the per-repo collectors. Data already collected is kept as a cache briefs can reuse. After the first brief's shortlist is final, anything no brief references is deleted (purpose limitation).
7. **Reliability:** each brief's deep forensics are double-coded, and the report shows agreement **per field**. Findings on fields with α < 0.70 are labelled "low reliability", not dropped. The earlier forecasting and global-calibration pre-registrations are withdrawn through a dated amendment, not deleted.
8. **Data sources:** GH Archive has been nearly push-events-only since mid-2025, so stars, forks, issues and PRs come from the GitHub API. Stars come from the star-history endpoint's daily net counts (ADR-032). Per-user star timestamps are no longer available: GitHub restricted stargazer lists on 2026-06-30.
9. **Compliance:** the compliance pack becomes a template each user adopts as controller of their own instance. Privacy-protective defaults ship in the tool (pseudonymization on, retention limits on). The owner's completed pack is the first filled-in copy.
10. **Next major milestone:** the first end-to-end neighbourhood report on the owner's project, which is also the pilot. PRD, DELIVERABLES and WORK_ORDER are updated accordingly.
How to reverse: Check out `archive/global-collection` and supersede this ADR.

## ADR-048 — CR-001: batch runs on the owner's Mac instead of continuous capture (owner decision; the owner called it "ADR-003", but that number was already taken) (2026-09-26)
Decision (CR-001, reconciled with CR-002 / ADR-047):
1. **Batch runs:** `pigtail run --incremental` (per brief), idempotent and resumable from checkpoints. The initial run is the brief's own backfill (12–18 months; CR-002 replaces CR-001's global 24-month backfill). Refreshes default to every 7 days (configurable 1–8 weeks), with a hard ceiling between runs derived from each source's history window in the source matrix (at most 60 days).
2. **Tracked projects (D5):** weekly by default. **Launch mode:** daily for 14 days around a declared or detected launch, every 3 h on launch day, and triggered automatically when a tracked project bursts.
3. **Evidence-decay study** (in the pilot, which is the owner's neighbourhood): the share of key evidence still retrievable at 1, 7 and 30 days. If more than 10% is lost at 7 days, the cadence for new breakouts is shortened and the finding goes in the pilot report.
4. **Acceptance criteria replaced:**
   - M1: the brief's backfill completes, 2 incremental runs complete with no gaps in the brief's API series (GH Archive hours no longer apply, ADR-047.8), replay reproduces the records, and an interrupted run resumes correctly.
   - D1: cases update at every run; launch-mode cases update on their own schedule.
   - Definition of done: 3 consecutive scheduled runs succeed with alerts working.
   - PRD §9.1: the test suite is cut (ADR-047.3). Launch-mode cases remain the prospective set for any later evaluation.
5. **Ops:** a launchd schedule on macOS; Docker runs only during runs; **FileVault is required** (added to H1); encrypted backups to external or private storage; a run report, with alerts written locally and a sanitized export to `ops/ALERTS.md` (ADR-033.4), emailed if configured.
6. **Server path:** optional, documented, and tested through backup and restore.
Supersedes the always-on parts of ADR-033 and ADR-032.1; the 24/7 host in H1 becomes optional.
How to reverse: Supersede this ADR and re-enable continuous capture from `archive/global-collection`.

## ADR-049 — Reconciling earlier decisions with ADR-047/048 (2026-09-26)
Context: The v2.0 rewrite of PRD, DELIVERABLES and WORK_ORDER listed 12 conflicts between CR-001/CR-002 and earlier ADRs. Items 1–9, 11 and 12 are decided here. Item 10 (matched-set balance) would relax a PRD §9.2 gate, so it waits for the owner.
1. **HN rank poller (ADR-031.1 vs ADR-048.5).** No always-on process on the Mac. The poller runs during every scheduled run (one snapshot of the current front page) and continuously while a brief or tracked project is in launch mode. Rank history outside those windows is accepted as lost and reported as a coverage gap. Historical front-page presence comes from the Algolia `front_page` tag, labelled as such.
2. **Person-level holds (ADR-022) stay in force.** Show HN discovery, HN/Bluesky mention search and the pilot's deep forensics wait for the remaining preconditions, above all CB-12 (published notice; owner's controller details). This is recorded as the M15 blocker. No relaxation.
3. **ADR-022's "production capture on the host"** now means scheduled runs on the operator's instance, including a Mac. For CB-03, FileVault covers Postgres and the local snapshot store at rest on a Mac (the Docker volumes live on the encrypted disk). `pigtail doctor` must check FileVault on macOS (backlog).
4. **Trendshift:** a terms audit is part of M13, and the source stays off until it is cleared (ADR-010).
5. **Withdrawn through dated amendments:** the 3 + 3 pilot pre-registration and its amendments 1–2, the forecasting test (original and amendments 1–4) and the threshold calibration. **Superseded:** ADR-019, 026, 039.5 and 042.2–3 (threshold freeze, forecasting target, H-sealed holdout, holdout guard, settle-lag collection). Their code (`pigtail.analysis.split`, the holdout guard and log, and settle-lag collection) is removed in M11; the tables are dropped by a forward-only migration. A new pilot pre-registration for the owner's brief is pushed before its outcome sort (M15).
6. **G1 remnants:** ADR-024.7 (the "< 10% categories changed" rule) and ADR-029.1 (the reliability supplement) are superseded by per-brief, per-field α with "low reliability" labels (ADR-047.7). The codebook drops its "fails G1" wording in its next version.
7. **Confidence levels (ADR-025)** are retired. Patterns carry n, the loser contrast (winner vs loser prevalence), counterexamples and per-field reliability labels, with no low/medium/high scale. That avoids implying inference from 15–25 pairs.
8. **Percentile population (ADR-018):** outcome percentiles are computed within the brief's final shortlist (winners and losers are chosen relative to the neighbourhood). ADR-018's global category-and-quarter cells are retired.
9. **Composite scores:** ADR-000's "no composite outcome score" is amended the same way as PRD §5.3 (ADR-047.2). A composite may rank candidates within a brief only as the advanced weights option; results are always shown per dimension.
11. **Interpretations accepted:** brief window 12–18 months, chosen per brief; R1.3 retired (launch mode covers declared launches); D6 "under an hour" = the first run is *started* after its cost estimate; D5 keeps first capture within 1 h, then weekly; D5 validation uses pilot cases.
12. **Global outcome classes and thresholds v0.2.0** are retired as a selection method; the files stay in git as history. Brief-level success definitions (ADR-047.2) replace them.
How to reverse: Per item, through an ADR.

## ADR-050 — Codebook v0.3.0 and outcome-model v2: per-brief method details (2026-09-26; before any outcome data)
1. **Coders never see outcomes.** C11b's `pattern_support.direction` is replaced by a reading derived from presence and the case's role.
2. **Outcome percentiles** are computed within the brief's final shortlist, with a minimum population of 20. Adoption is compared within the same package ecosystem.
3. **A pattern is labelled "insufficient evidence in this neighbourhood"** below 10 known cases per side or 3 cases where it's present in total.
4. **Candidates that miss a threshold only because a value is unknown** are "undetermined", not losers.
5. **Business** is scored as a count of verified signals; `if_not_applicable: skip` is allowed.
6. **Sensitivity check:** four alternatives (swap the primary dimension, stricter/looser thresholds, weights, fake-star filter). Cases whose role changes are flagged. The threshold steps and the default matching calipers are those in outcome-model v2.
7. `settle_lag` is fixed at 3 days.
8. **Codebook 1.0.0** is frozen after the M15 report.
9. **Pilot pre-registration and publishing:** the brief's success definition is the owner's brief content. The public pre-registration commits its SHA-256 hash, and the text becomes public only if the owner approves (H4).
10. **Old pre-registrations** are withdrawn by dated amendments (pilot amendment 3, forecasting amendment 5, calibration amendment 1). The seed hypotheses MC-01…13 stay as a starter list a brief picks from before its outcome sort.
How to reverse: Codebook or spec version bump + ADR; before the M15 pre-registration is pushed.

## ADR-051 — M11 code cleanup: what was kept and how launch mode starts (2026-09-26)
1. **Removed:** the watch list, search sweeps and screens, global velocity detection (GH Archive scan, backfill, detection v1), the holdout split and guard, settle-lag collection, and their scheduler jobs. Migration 0014 drops their tables. It first writes count-only `deletion_log` rows with the new reason `purpose_limitation` (ADR-047.6).
2. **Kept:** per-repo collectors (star history, repo events, still gated), HN connectors, privacy, backups, UI and export. Also `botfilter.py` (login rules used by the connectors), the GH Archive connector (brief-restricted discovery, M13), and a generic `search_repos` (one caller-supplied query, raw pages dropped at parse).
3. **Burst logic** is now `pigtail.analysis.bursts`: pure functions over one repo's star-history days, per codebook §3. Its parameters and the StarScout parameters live in `schemas/analysis-params/v1.0.0.json`, mirrored by `pigtail.analysis.params`, which replaces the retired thresholds JSON.
4. **Launch-mode stub:** the table `launch_mode_window`, the override `PIGTAIL_LAUNCH_MODE`, and job flags `run_at_start` / `launch_mode_only`. The HN poller takes one snapshot per scheduled run and polls continuously only in launch mode (ADR-049.1). Launch-mode-only jobs are never flagged as stale; M14 reworks staleness for weekly batch runs.
5. **The scheduler moves to the Compose `server` profile,** so a plain `docker compose up` on a Mac doesn't start an always-on service (ADR-048).
6. **`repo-events` polls any live case,** not only velocity-triggered ones.
Incident: docs commit 214986f accidentally included the engineer's staged deletions. That left HEAD broken and CI failed on that commit. It was repaired in the next commit, which completes M11's code part. Lesson: check `git diff --cached` before committing while another agent is working in the same tree.
How to reverse: `archive/global-collection` has the removed code.

## ADR-052 — Burst rule: no phantom re-firing after a single-day spike (2026-09-26; before any brief data)
Context: The M11 verifier found that, under codebook v0.3.0 §3.2, a single-day spike is always marked `multi_peak=True`. The burst ends on the next calm day, but that day's 48-hour window still contains the spike, so it fires again, and the merge rule then joins the two into a "multi-peak" burst.
Decision: A firing whose 48-hour window starts on or before the previous burst's **last day** (d−1 ≤ last burst day; a burst's "end" is the first calm day after it) is ignored. It is neither a new burst nor a merge. Codebook becomes v0.3.1 (a patch: it corrects the rule's intent) and `analysis-params` stays v1.0.0 (no parameter changes). A regression test covers the single-spike case.
How to reverse: Codebook patch + ADR.

## ADR-053 — Brief budget model and success fallbacks (owner decisions, 2026-09-26)
1. **Two separate caps in every brief**, both in the brief schema:
   - `budget.money_usd`: non-LLM paid services (BigQuery, Trendshift, X, any paid API). Default **0**: free tiers only. Before any step that would cost money, pigtail shows a cost estimate and waits for explicit approval (H6). Optional paid sources (Trendshift, X) are off unless the user enables them.
   - `budget.subscription_share`: the maximum share of the user's weekly Claude subscription allowance that pigtail runs may use. Default **0.5**. Pigtail estimates usage before each run, runs heavy stages (double coding, extraction) in chunks, pauses when a limit is hit and resumes later. **It never switches to the API on its own.** Per-job API overrides (ADR-001, R15.5) apply only when the user sets them explicitly.
   - Limitation: Claude Code exposes no machine-readable remaining weekly allowance. The share is enforced against pigtail's own usage ledger (sessions, tokens, limit hits), calibrated from observed limit events and the user's plan. Estimates are labelled as estimates.
2. **Success fallbacks** (brief options, the owner's values as defaults):
   - `success.fallbacks.no_measurable_adoption`: a comparable that isn't a package (no registry downloads, no dependents) uses community as its primary dimension and is flagged in the report.
   - `success.fallbacks.too_few_winners`: if fewer than 10 winners qualify, relax the primary threshold from top quartile to top third, then drop the community minimum. Each step is logged, and the sensitivity check is reported.
3. **Seed-project links** aren't required up front: discovery finds candidate matches, and the user confirms or corrects them in the shortlist review.
How to reverse: Brief values can be changed per brief; defaults through an ADR.

## ADR-054 — Matching rules, panel widening and reference cases (owner decisions; resolves DEC-10) (2026-09-26)
1. **Matching (amends PRD R4.3 and §9.2).** Winners and losers are **exactly matched** on founder audience size (bucketed) and launch period (same half-year), the two characteristics that matter most for the owner's decision. For every other characteristic a standardized mean difference < 0.25 is a **target**, not a gate: balance is always shown per characteristic, and contrasts that depend on a characteristic missing the target are labelled. A pair that differs by **> 0.5 on any characteristic** is excluded from the headline patterns and shown only in the case-level view. The owner approved this change to a §9.2 gate (WORK_ORDER §1).
2. **Panel from the wider field (new R4.10).** A brief's panel is filled from its declared core field. If that yields too few winners or losers, it widens step by step to the adjacent fields the brief declares (the owner's brief declares two). Every case is labelled with its distance from the core field (0 = core), and core-field findings are always reported separately.
3. **Named reference cases (new R4.11).** The owner's four comparables stay in the report whatever their outcome, labelled as reference cases. When a project has several repos, pigtail studies the repo its launch posts linked to (HN, Reddit, X, Product Hunt); if both a site and an engine were promoted, the engine is studied and the site is treated as an asset. The choice is logged per project. The owner's own identification of three of these projects is still open; until then this rule applies, and the result is shown in the shortlist review.
How to reverse: A new ADR (these are the owner's decisions).

## ADR-055 — Brief store, allowance source, cache keys, estimates and threshold fallbacks (M12) (2026-09-26)
1. **Store:** briefs live only under `PIGTAIL_DATA_DIR/briefs/<id>/vNNNN.yaml` (directories 0700, files 0600). Versions are immutable, saving unchanged content creates no new version, and stale edits are refused. The private-data scan blocks brief files by path (`*/briefs/*`, file names containing `brief`) and by content (a top-level `brief_id:` plus `project:`); only `docs/examples/brief-example.yaml` is allowlisted. **Brief content, including a brief's named reference projects, never goes into tracked files, ops logs included.** (The owner's seed-project names had been written into BACKLOG and ADR-054 and were made generic here; earlier commits still contain them.)
2. **Weekly subscription allowance:** taken from `PIGTAIL_SUBSCRIPTION_WEEKLY_TOKENS` if configured, otherwise calibrated from the last limit hit in the usage ledger, otherwise an assumed low default of 5M tokens per week, labelled as assumed. Money above the cap needs explicit approval (`--approve-paid`, recorded).
3. **Cache keys:** per-item keys don't include upstream fingerprints, so unchanged items are reused across edits and briefs. Each stage declares which brief fields it reads, and `plan_rerun` recomputes only the stages affected by an edit.
4. **Estimates:** the `estimate-v0` per-unit figures are placeholders until they are measured on a real run (M13, D6).
5. **Threshold fallbacks:** ADR-053's owner-set fallbacks (top quartile → top third, then drop the community minimum) are an explicit, pre-declared brief rule. They override outcome-model §5.5 ("thresholds are not loosened automatically") only when a brief declares them. Each step is logged and the sensitivity check reported. `top_third` is added to the threshold ladder in outcome-model §5.3 (spec update in M13).
How to reverse: Per item, through an ADR.

## ADR-056 — Distribution examples in a brief (owner decision) (2026-09-26)
Context: The owner noted that launches within a narrow core field may be too few or too small to teach distribution. Some projects with a different audience distributed very well and are worth studying for *how* they spread.
Decision: A brief may name **distribution examples**: projects outside the core field that are studied mainly for their distribution (launch sequence, channels, assets, contribution loops). They are:
- not part of the core winner/loser panel;
- given matched losers from their own niche where the shortlist allows, so the lessons still have a loser contrast;
- reported in their own "distribution lessons" section, labelled cross-field and with their distance from the core field;
- kept out of core-field headline patterns.

For now they sit in `field.reference_cases` with a `distribution_exemplar` label. A dedicated schema field comes in M13 (brief schema v1.1). Brief content, including the named projects, stays in the private brief store (ADR-055.1).
How to reverse: Remove the examples from the brief; drop the report section through an ADR.

## ADR-057 — Two panels per brief, transferability labels, absolute numbers, reference cases in the schema (owner decisions) (2026-09-26)
1. **Two panels per brief:**
   - **Field panel:** the brief's core field (widened per R4.10). Winners and losers are matched per ADR-054 (exact on audience bucket and launch half-year). It shows what works in the user's own market, at a realistic scale.
   - **Distribution examples panel** (refines ADR-056): projects chosen for exceptional distribution, whatever their field or audience. Each gets **1–2 losers matched on launch type, launch period and audience bucket (not field)**.
2. **Transferability:** every pattern from the examples panel states its conditions (audience, timing, category hype, assets) and whether they apply to the user's project. Each is labelled **transferable**, **conditional** or **not transferable**.
3. **Absolute numbers:** reports show absolute stars, downloads and contributors next to each winner and loser class, per panel, so the scale of "winning" is visible.
4. **Reference cases** become a brief-schema field (`field.reference_cases`, structured): named projects always studied whatever their outcome class. The repo follows the launch-link rule (ADR-054.3) and is confirmed by the user in the shortlist review. Unresolved ones don't block the run. Brief schema v1.1 also adds `distribution_exemplars` and the report options.
How to reverse: Per item, through an ADR.

## ADR-058 — Brief expansion, stale-edit defaults, brief schema v1.1 (M12 fixes) (2026-09-26)
1. **Expansion (R18.7):** `pigtail brief expand` and `POST /api/briefs/{id}/expansion` return a **proposal** and save nothing. Only the description, target users, business model and field include/exclude are sent to the model; never the name, context, reference cases, examples, channels or notes. The prompt is versioned (`brief_expansion` v1) with a closed output schema, runs through `LLMClient` as job `brief_expansion`, and is checked by `BudgetGuard` (never an automatic backend switch; ADR-053). Accepting saves a new version with provenance. Accepting is refused if the brief changed after the proposal. Migration 0016 adds the audit event `brief_expansion`.
2. **Stale edits:** an edit is based on `--base-version`, else the file's `version:`, else the latest version with a warning. The API uses `base_version`, else the payload's `version`, else returns 409 unless `force_latest: true`. An edit exported from an older version is refused, so it can't silently undo newer changes.
3. **Brief schema v1.1** (ADR-057): structured `field.reference_cases`, a `distribution_exemplars` section (`losers_per_exemplar` 1–2; `match_on` launch type, launch period, audience bucket), and `report.show_absolute_numbers` / `report.transferability_labels`. v1 briefs are converted in memory and written as v1.1 on the next save.
4. **Estimate** is now `estimate-v1` (supersedes ADR-055.4's `estimate-v0`). Its per-unit figures are still placeholders until measured.
Also fixed: a time-dependent test (`due_case_repos` now takes an injectable clock).
How to reverse: Per item, through an ADR.

# Owner Directive 001 (2026-09-26): one ADR per section
Source: `ops/OWNER_DIRECTIVE_001.md` (public copy, §4 redacted by owner decision; the full copy is private). Chat references: CR-001 = batch runs (ADR-048), CR-002 = brief-based research (ADR-047, which the owner called "ADR-004"), CR-003 = legal mitigations (this directive, §8–§9). The directive is binding and supersedes conflicting text in PRD, DELIVERABLES, WORK_ORDER and CLAUDE.md.

## ADR-059 — Directive §1: project framing
pigtail is a personal project for now: no service, no customers, no findings leave the owner's instance. Distribution follows the Crawl4AI model: open-source code, method, templates and docs that each user installs and runs with their own credentials and data. No data and no hosted service ship. Nothing in the code is owner-specific. Confirms ADR-047.

Replaces chat reference: CR-002 (ADR-073.3; Directive §1).

## ADR-060 — Directive §2: brief-based research
Brief fields and the per-brief flow as in §2.1–2.3 (confirms ADR-047, ADR-055, ADR-058). Every report records the brief version, shortlist decisions, data version **and code commit**. Cuts as in ADR-047.3. Findings are per-brief patterns showing n, the loser contrast and counterexamples.

Replaces chat reference: CR-002 (ADR-073.3; Directive §2).

## ADR-061 — Directive §3: success definition and matching
Confirms ADR-047.2 (primary dimension plus minimums; weights advanced only; PRD §5.3 amended), ADR-053.2 (fallbacks), and ADR-054.1 (exact match on audience bucket and launch half-year; SMD < 0.25 as a target; > 0.5 excluded from headline patterns). Every report includes the sensitivity check.

Replaces chat reference: CR-002 (ADR-073.3; Directive §3).

## ADR-062 — Directive §4: the owner's first brief (content private)
Per the owner's option (b), §4's content lives only in the owner's private brief: the field panel, widening steps, distribution exemplars, reference cases, audience and channels. The generic rules are public (ADR-054.3, ADR-057). Missing brief fields get defaults and are listed in `ops/HUMAN_INPUTS.md` (generically) for confirmation in the shortlist review. Unresolved reference cases never block a run.

Replaces chat reference: CR-002 (ADR-073.3; Directive §4).

## ADR-063 — Directive §5: collection model
Confirms ADR-048 and ADR-049.1 (batch runs `pigtail run --brief <id> [--incremental]`; 7-day default, 1–8 weeks; ceiling ≤ 60 days derived from per-source history windows; launch mode; D5 weekly; launchd; server optional). Confirms ADR-051 (archive tag; global code deleted; poller, scheduler and per-repo collectors kept). The **purge of cached data that no brief references**, once the first brief's shortlist is final, is logged. GH Archive is used for discovery signals only. §5.3's "stargazer timestamps" is **not implementable** (GitHub restricted them on 2026-06-30); the owner accepted daily counts instead (ADR-070). Evidence-decay study as ADR-048.3.

Replaces chat reference: CR-001 (ADR-073.3; Directive §5).

## ADR-064 — Directive §6: LLM backends, models and cost (supersedes ADR-023 and ADR-053.1 for product calls)
1. **Product LLM calls use `LLM_BACKEND=api`** (the owner's Anthropic API key, Commercial Terms, verified 2026-09-26). The `subscription` backend stays in the code for other users, documented as individual, non-commercial use on the operator's own plan through the official CLI only, with a pointer to Anthropic's terms.
2. **Agents building pigtail** stay on the owner's subscription (`AGENT_BACKEND=subscription`), using at most ~50% of the weekly allowance, with pause and resume; never an automatic backend switch.
3. **Models:** relevance filter `claude-haiku-4-5-20251001`; extraction and coding `claude-sonnet-5`; synthesis, report and plan `claude-opus-5-5`. All three are visible on the owner's key (checked 2026-09-26). The **Batch API** is used for every stage that isn't time-sensitive; launch mode may use standard calls. Prompt caching covers the codebook and system prompts. Evidence is cut down before sending (truncation, deduplication, relevant excerpts). Model IDs and prompt versions are logged with every output.
4. **Budget:** API hard cap USD 150 for the owner's first full brief and USD 200 per month. After the first 5 pilot cases, the cost per case and a projection go in `ops/COSTS.md` and `ops/STATUS.md`; if the projection exceeds the cap, stop and raise H6. Other paid services USD 0 (BigQuery free tier only; Trendshift and X off). A cost estimate is shown before every run (CLI and UI). Brief schema: `budget.money_usd` covers the API cap plus other paid services; `budget.subscription_share` now applies to agents only.

Replaces chat reference: none (new in the directive; ADR-073.3, Directive §6).

## ADR-065 — Directive §7: quality and pre-registration
Confirms ADR-047.7 and ADR-050: double coding with adjudication, Krippendorff's α per field, "low reliability" below 0.70 (also on findings resting on such fields), and the optional owner calibration (H3) or the "LLM-coded, not human-validated" label. Old pre-registrations are withdrawn by amendment, not deleted (done: ADR-049.5). Brief-level hypotheses are pre-registered before the outcome data they test is examined.

Replaces chat reference: CR-002 (ADR-073.3; Directive §7).

## ADR-066 — Directive §8: privacy and legal mitigations (supersedes the pseudonymized-handle model of ADR-030/042/045 for coded data)
1. **Code, then discard identities:** spread-graph nodes are roles and buckets (maintainer, account by follower bucket, newsletter, community, organization). Handles and personal names are never stored in coded data; organizations and projects may be named. The pseudonymized-handle scheme is replaced; existing data is migrated, then the handles and pseudonyms are purged. Kept: an HMAC fingerprint only for people who opted out (ADR-071).
2. **Time-limited snapshots:** raw snapshots are stored locally, encrypted at rest (FileVault), and kept until the brief's report is final **plus 12 months**. Then they're deleted, keeping the coded facts and the content hash. A scheduled purge job writes a log. Snapshot-or-drop applies at coding time.
3. **Minimal collection:** mentions of **shortlisted projects only**; no sweeps of users or accounts; no follower lists.
4. **Sources:** official APIs only, rate limits with margin, deletions honoured. Reddit is metadata only (link, title, score, timestamp) until API approval. Trendshift off by default.
5. **Outputs:** snapshots are never reproduced; at most one short attributed excerpt per source.
6. **Publishing (H4) is disabled.** No findings, data or reports leave the instance. The repo holds only code, docs, templates and synthetic fixtures. The CI private-data scan stays.
7. **LLM processing:** API mode only, under the data processing agreement in the Commercial Terms. The compliance docs record that inference happens in the US.
8. **Distribution safeguards:** no data and no default targets ship; the protections are on by default; a "Responsible use" section goes in the README and operator guide (each user is the controller of their own instance). MIT licence unchanged.

Replaces chat reference: CR-003 (ADR-073.3; Directive §8).

## ADR-067 — Directive §9: compliance documents
`docs/compliance/` becomes a **template**: a one-page LIA, a short privacy notice, a light DPIA, a retention policy matching §8.2, and a terms memo per source. The owner's completed pack is the first filled-in copy, kept outside git. `docs/compliance/LEGAL_REVIEW_H2.md` is narrowed to three questions (legitimate interest and GDPR Art. 3(2)(b); whether a public notice under Art. 14(5)(b) is enough; Reddit terms, only if Reddit is used beyond metadata). The Anthropic questions (LQ-1 to LQ-3) are closed, because product calls run on the API. H2 blocks publishing only, and publishing is disabled.

Replaces chat reference: CR-003 (ADR-073.3; Directive §9).

## ADR-068 — Directive §10: human gates
H1: Anthropic API key with Console spend limits (✓ key verified 2026-09-26; spend limits to be confirmed by the owner), GitHub token (✓), FileVault (✓), an encrypted local backup location, and the Claude subscription login for agents (✓); optional BigQuery (free tier) and SMTP. H2: the narrowed legal consult (collection continues under the mitigations). H3: optional calibration (~2 h). **H4: publishing disabled** until the owner revokes the directive. H5: no external action without approval (the privacy-notice publication falls under H5). H6: no spend above the ADR-064 caps.

Replaces chat reference: none (new in the directive; ADR-073.3, Directive §10).

## ADR-069 — Directive §11: milestone re-plan
Order: (1) documents, with the verifier's consistency check; (2) cleanup and privacy (ADR-051 done; §8.1 migration and handle purge; §8.2 purge job; `LLM_BACKEND=api` wiring; cost estimator); (3) brief feature (M12 done; discovery, relevance filter and shortlist review remain); (4) owner brief #1 pilot: the first 5 cases end to end with double coding, then the cost report and projection and the evidence-decay measurement; (5) full brief #1 run within the cap: report with per-field α, sensitivity check and transferability labels; (6) D3 plan; (7) launch mode and D5 before the owner's first launch; (8) operator guide: a new user runs a first brief in under an hour. Acceptance as in the directive. The WORK_ORDER milestones M13–M19 are renumbered accordingly.

Replaces chat reference: none (new in the directive; ADR-073.3, Directive §11).

## ADR-070 — Daily star counts, day-level attribution, launch-mode star polling, aggregate anomaly checks (owner decision; resolves the directive's §5.3 conflict)
1. **Stars** come from `GET /repos/{owner}/{repo}/stargazers/history` (daily net counts). Resolution is **daily**.
2. **Trigger attribution** uses the timestamps on HN, Reddit and Bluesky posts. Attributions that rely on daily star data are labelled **"day-level"**.
3. **Launch mode** polls each tracked repo's current star count every 3 h (hourly on launch day), to build an intra-day curve from now on. It can't be reconstructed afterwards. D1's hourly star lanes exist only for launch-mode cases.
4. **Fake-star filtering:** methods that inspect individual stargazer accounts (StarScout-style) no longer work for repos we don't own. They are replaced by **aggregate anomaly checks**: star spikes with no matching forks, issues, downloads or external mentions, and odd stars-to-activity ratios. Star metrics are labelled **"unfiltered, anomaly-checked"**. The outcome model, codebook and literature notes are updated; `analysis-params` gets an anomaly-check section in a new version.

## ADR-071 — Opt-out fingerprints, bot flags on coded records, brief storage, privacy-notice publication (owner decisions)
1. **Opt-out fingerprint:** an HMAC with a secret key stored separately from the data. It is used only to exclude and purge that person in future runs, and is described in the privacy notice.
2. **Bot filtering** runs in memory. The outcome is stored on the coded record ("automated account" plus the version of the rule that flagged it), so results stay reproducible without the handle.
3. **Brief storage outside git:** the default briefs directory moves to `~/.pigtail/briefs` (configurable) and is included in the encrypted backup. The CI check that blocks committed brief files stays (it exists; it is extended). A synthetic example brief ships in the repo.
4. **Privacy notice:** published in a **separate repo on GitHub Pages**, because it describes the owner's instance, not the tool. It contains: the controller (the owner), a dedicated contact alias (not her personal email), purpose, sources, what is and isn't stored (roles and buckets, no handles), retention (§8.2), legal basis, how to opt out or object, and US processing. **The draft is shown to the owner before publication (H5).** The pigtail repo keeps the generic template.
5. **Git history:** brief-specific content pushed on 2026-09-26 was removed by an owner-approved history rewrite (0 forks), done the same day. See `ops/history-rewrite-2026-09-26.md`.

## ADR-072 — Applying Owner Directive 001 to the documents: interpretations (2026-09-26)
1. **Milestone IDs:** WORK_ORDER never reuses IDs, so M13–M19 are retired and the directive's 8 steps are **M20–M27** (D4 = M28). STATE and BACKLOG are remapped.
2. **Batch runs:** `pigtail run --brief` with checkpoints goes in M22 (the pilot needs it). launchd, cadence, backup/restore and 3 consecutive runs go in M26 and M27.
3. **"LQ-1 to LQ-3 closed" (directive §9)** means the Anthropic questions. In `legal-review-questions.md` those are LQ-1 and LQ-2. LQ-3 is about Reddit and becomes question 3 of the narrowed H2.
4. **Budget fields:** `budget.money_usd` is the brief's total money cap, including API spend (the owner's first brief: USD 150). Any other paid service is off unless enabled individually, and each enabled one needs an H6 approval against that cap. The monthly cap (USD 200) is instance-wide (`BUDGET_USD_MONTH`). Schema v1.1's separate `llm_api_usd` is folded into `money_usd` in M21 (schema v1.2).
5. **Code defaults that don't yet match ADR-064/066/071** are fixed in M21: `LLM_BACKEND` defaults (the code keeps the safe `subscription` default when unset, and the owner's `.env` sets `api`), per-stage model variables, `BUDGET_USD_MONTH` default 200, `PIGTAIL_BRIEFS_DIR`, and snapshot retention (report final + 12 months).
6. **Reddit metadata:** allowed only once the source matrix records its clearance and a Reddit API app exists (H1, optional). Until then it's a gap.
7. **Wayback Machine:** off by default. When enabled, it is used only for project pages (sites, READMEs, pricing and docs pages) and never for person-level content. Narrowed H2 doesn't cover it; the source matrix keeps its conditions.
8. **Shortlist check:** the old H3 shortlist skim moves into M23. The owner reviews the shortlist; if she doesn't, the verifier skims it and precision is labelled "verifier-checked, not owner-checked". H3 is now the optional calibration coding.

## ADR-073 — Reports stay private; ADR-022 aligned with Owner Directive 001 (2026-09-26)
1. **No reports in git** (Directive §8.6, ADR-066.6). Pilot, brief and final reports, cost reports with case detail, and evidence-decay results stay inside the owner's instance (private data directory, backed up), not in the public repo. The repo holds only code, method documents (codebook, specs, templates, guides), synthetic fixtures and ops logs with counts and method-level status only. Earlier plans for public `docs/reports/pilot.md` and `final.md` are withdrawn; `docs/reports/` keeps only the counts-only data inventory and method notes without case content.
2. **ADR-022 amended:**
   - Naming organisations, projects and matched losers is allowed **inside the instance** (private UI and private reports), because nothing is published (H4 disabled).
   - Person-level sources (HN mentions and comments, Bluesky, per-repo events) may be enabled once **CB-12** (the owner's privacy notice published after her approval, H5) and **CB-06b** (the rest of the LLM-path redaction) are done, together with the controls already built (CB-01, 02, 03 via FileVault, 08, 13, 22, 23, 25, 29).
   - Spread graphs are roles and buckets (ADR-066.1), so the old LQ-8 condition (account-level graphs) no longer applies.
3. **Chat references:** ADR-059 to ADR-069 each derive from Owner Directive 001 (CR-003 for §8–§9; CR-002 for §1–§4 and §7; CR-001 for §5; §6, §10 and §11 are new in the directive). The mapping is recorded here and in the directive preamble.
How to reverse: A new owner directive.

## ADR-074 — M21 implementation choices: privacy model, LLM path, cost, briefs store (2026-09-26)
**Privacy (M21a, ADR-066/071):**
1. **Roles and buckets** are coded at ingest (`pigtail.privacy.roles`: maintainer, account, newsletter, community, organization, automated_account; buckets r0–r4). Handle fields are set to None before storage. The `maintainer` role for HN mentions is a heuristic (HN name equals the GitHub owner, `roles-v1`).
2. **The opt-out fingerprint keeps the old `p_` + 16-hex value**, so existing opt-outs keep matching. `OPTOUT_KEY` is the variable name, with `PSEUDONYM_KEY` accepted as an alias (refused if both are set and differ). The CB-25 key check still applies.
3. **Migration 0017** removes stored authors and pseudonyms (`hn_mention.author`, `upstream_items.author_pseudonym`) and drops `repo_event_actor`. Per-repo events keep only hourly and daily counts, de-duplicated within a poll (the same account can be counted twice across polls; accepted). Migrated rows get role `account`, bucket r0, `automated_account` unknown.
4. **Snapshot retention (§8.2):** snapshots are deleted 12 months after the latest final report of the briefs that used them. Snapshots with no brief or a pending report fall back to the 730-day ceiling from fetch. The class name `person_level_24m` is kept for now.
5. **Collection scope (§8.3):** mention capture refuses any repo that isn't on an in-review or final shortlist, so a tracked launch-mode project must be shortlisted.

**LLM path and cost (M21b, ADR-064):**
6. **Stages:** relevance → Haiku 4.5; extraction, coding and adjudication → Sonnet 5; patterns, report, plan and asset drafting → Opus 5.5. `brief_expansion` runs on the synthesis model as a standard (non-batch) call because it's interactive. Unknown jobs are refused. Launch mode and interactive jobs never use batches.
7. **Model inputs are redacted with per-call, keyless aliases** (`user1`, `user2`, …; `llm/redact.py`, provenance `alias-v1`). Nothing keyed reaches the model, the LLM cache or logs. Logs use keyless placeholders only. The local LLM cache was cleared (0 rows).
8. **Cost:** `BudgetGuard` hard-stops at the brief's `money_usd` (API included, spend across all runs) and at `BUDGET_USD_MONTH` (calendar month UTC, default 200), citing H6. The `money_usd` code default stays 0; the example suggests 150. Estimate model v2 assumes 0.5 cache hits in batches and 0.8 in standard calls, labelled as assumptions. Prices come from the model table dated 2026-06-24 (`PRICES_AS_OF`); check the pricing page before large runs. Actual cost goes to `llm_cost_ledger` per brief run and case (migration 0020, ids, hashes and token counts only). Brief schema v1.2 folds `llm_api_usd` into `money_usd`.
9. **Anomaly checks** (ADR-070.4): `anomaly-v0` in `analysis-params` v1.1.0; StarScout parameters are retired.
10. **Briefs store:** default `~/.pigtail/briefs`. The owner's brief was moved there with `migrate-store` (counts only printed). Backups add a second encrypted archive of the briefs directory next to the database backup; merging them into one stream is a follow-up. The `ui` container mounts the briefs directory.
How to reverse: Per item, through an ADR.

## ADR-075 — Method docs after the anomaly-check switch; no cross-platform name matching (2026-09-26)
1. **Outcome model v2.1 and codebook v0.4.0** follow ADR-070.4. The star series is raw daily star-history, labelled "unfiltered, anomaly-checked", with anomaly flags reported per case. The flags never change a value, threshold or rank. `fake_star_campaign_suspected` is renamed `star_anomaly_flagged`, and MC-12 is a proxy, not detection. The anomaly checks are unvalidated heuristics (outcome-model O19).
2. **ADR-050.6's fourth sensitivity alternative is now "exclude anomaly-flagged candidates"** (alternative D): flagged candidates are removed and the whole outcome sort is redone, only when the success definition uses a star metric. The brief-schema value `fake_star_filter` is read as D until it is renamed `exclude_anomaly_flagged` in brief schema v1.3 (engineer, backlog).
3. **The `maintainer` role is never inferred from matching names across platforms** (HN name = GitHub owner), because codebook rule P3 forbids cross-platform identity resolution. The role is assigned only from the item itself: GitHub-native activity by the repo owner, or a post that states first-party authorship ("I built…", "we launched…"), or a post that links the author's own project profile in its text. The `roles-v1` heuristic is replaced by `roles-v2` (engineer, backlog). Existing coded rows: none (dev DB empty).
4. **Quote checks with aliases:** the check that a coded quote matches its snapshot (codebook §11.2) must alias the quote and the snapshot in the same pass. This stays open until the coding pipeline is built (M22–M23).
How to reverse: Per item, through an ADR.

## ADR-076 — M22 design: brief runs, discovery, relevance filter, shortlist (2026-09-26)
1. **Runs:** `pigtail run --brief <id>` resumes the same `brief_runs` row after a crash, budget stop or waiting batch; batch ids are keyed on the run. `--incremental` creates a new row (`resumed_from`). One run per brief at a time (advisory lock). On the api backend a run needs `--approve-paid` after the estimate is shown.
2. **Discovery defaults:** GitHub search per keyword and topic, restricted to the brief's window, ≥ 10 stars, 1 page per query, ≤ 60 queries, ≤ 1,500 candidates; Show HN; awesome-list READMEs. GH Archive signals are off by default. Opted-out repos are never candidates. Widening-step searches (R4.10) are a follow-up; for now the model only labels distance.
3. **Show HN before CB-12:** allowed because only project-level fields are requested and stored (title, url, points, time). The author is never read, and raw pages are dropped at parse.
4. **Relevance filter:** Haiku via the Batch API, 20 candidates per request, rubric built only from the brief's field boundaries, widening steps, target users and problem (versioned by hash). Model inputs are public repo metadata with the owner login replaced by `[owner]`, then alias-v1 redaction. The budget is checked per group of 25 requests. Reuse across runs happens per candidate and rubric version, plus the LLM cache per chunk.
5. **Shortlist:** every decision carries a reason, role, channel (CLI or UI), time, brief version and run. Finalizing is refused while any relevant or uncertain candidate is undecided. Precision is kept ÷ decided among model-relevant field candidates, excluding named projects, and labelled with who checked. Finalizing writes the mention scope and links the evidence used to the brief (retention).
6. **Named projects** (reference cases, distribution examples) are stored in the database only as `named:<panel>:<i>`, never with brief text. Repo full names of candidates are stored (project-level).
How to reverse: Per item, through an ADR.

## ADR-077 — M22 selection: outcome sort, matched losers, balance and sensitivity (choices the specs leave open) (2026-09-26)
Context: PRD R4.3, R4.8–R4.11, §8.2, §9.2 and outcome-model v2.1 §2–§8 define the selection; ADR-053.2, ADR-054, ADR-055.5, ADR-057 and ADR-075.2 add the owner's rules. The points below were open. Code: `pigtail.briefs.selection` (pure), `outcomes` (inputs), `selection_store` (stage and storage), migration 0022.
1. **Stage:** `selection` is the fourth stage of `pigtail run --brief`. It runs only on a final shortlist; after `shortlist finalize` the next run continues the same `brief_runs` row with this stage alone. It makes no model call. Its `as_of` date (for `pending`) is fixed in the run's checkpoint, so a resume judges alike.
2. **Outcome data:** the stage fetches star history for every shortlisted repo (conditional requests, back to 60 days before the window; checkpointed; the GitHub budget pauses it) and fills missing metadata for repos added by URL. Only `att.stars@30/@90` and `att.hn_points` (Show HN posts recorded by discovery) have data. Downloads, dependents, contributor metrics and business signals are `unknown` with reason `no_connector` until their connectors exist; nothing is imputed, so a brief that ranks on them gets no winners and says so. The founder audience band is `unknown` for every candidate (O14) and is matched as its own level. Language is the current primary language, not the one at T. Launch type (for exemplars) is `show_hn` or `burst`.
3. **Anchor:** §2.2's rules 2 and 3 overlap when a launch precedes the first burst by more than 30 days. A launch in `[T_burst − 30 d, T_burst]` wins; otherwise the first launch wins when it precedes the first burst and no burst follows it within 90 days; otherwise the burst; otherwise the launch. Without star history a launch is used and the burst rule is noted as not checked.
4. **Populations:** the percentile population is the final shortlist's field and reference repos with an anchor (exemplars are outside the field). The minimum population stays 20 (no brief field yet, O17). Reference cases take part like any field candidate and are always listed with `is_reference`. A candidate without a relevance distance (added in review) counts as distance 0.
5. **Order of fallbacks:** widening first (R4.10), then threshold steps (ADR-053.2). The panel widens one declared step at a time while it has fewer than 15 winners or matched losers (the R4.8 range floor), up to `min(2, len(widening_steps))`; candidates beyond the final distance are `outside_widening`. Then, while fewer than `too_few_winners.min_winners` rankable qualifiers remain, the brief's steps apply in order. Nothing else is loosened. Each step is logged with its counts; a widening step that adds nobody is logged as not applied. Reason: widening keeps the success definition intact, and core-field findings are reported separately anyway.
6. **Matching:** exact on `panel.exact_match`, calipers `|ΔLSM| ≤ 0.5 SD` and `|Δquarter| ≤ 1`, distance per §5.6 with a missing numeric value costing one SD. Winners in rank order take the nearest unused loser; further rounds run until `panel.losers` losers are matched, so a winner can get a second loser. A winner and its losers share one `pair_id`. Balance covariates are LSM, repo age and language, plus audience band and quarter only when they are not exact-matched (a quarter inside an exact half-year is not a separate characteristic).
7. **Headline exclusion (ADR-054.1), read literally:** a pair is excluded when its standardized difference exceeds `headline_exclusion_smd` on any balance covariate, using the pooled SD of winners and loser pool, and a language mismatch counts as 1. On the synthetic test fixtures it excluded between 3 of 16 and 17 of 20 pairs, mostly on language or repo age. The rule is the owner's; it can be relaxed per brief (`headline_exclusion_smd`) or by a new ADR.
8. **Exemplar losers (ADR-057.1):** they come from the loser pool at any distance, excluding field winners and matched losers. They are matched exactly on `match_on` and ordered by the nearest LSM (no caliper, since exemplars are exceptional by design), with ties broken by hash. They are not reused across exemplars.
9. **Sensitivity:** alternatives apply to the final (post-fallback) definition at the final distance, with the same N and no re-matching. Band shifts use the §8.1 ladder, with `top_third` between 50 and 75. A primary swap to a dimension with no observed value is reported as not run. D (`fake_star_filter`, ADR-075.2) runs only when a star metric is in the definition and some candidate is flagged. Min and mean Jaccard use only the alternatives that ran.
10. **Determinism:** inputs are sorted by `candidate_ref`. Ties are broken by `sha256(brief_id:brief_version:candidate_ref)`, not by `repo_id` as §5.4 says, because discovery candidates often have no `repos` row. Each selection stores `inputs_hash` and `result_hash`, and the same inputs give the same result hash.
11. **Storage and privacy:** `brief_selection` holds the provenance (brief version and content hash, run, data version after the fetch, `as_of`, selection, outcome-model and analysis-params versions, code commit) and only counts and statistics. Repo names appear only in `brief_selection_case` rows, which are registered in `REPO_TABLES`, the export and the inventory, so an opt-out removes them. Selections are append-only history, and the latest one per brief version is shown (`pigtail brief selection show`).
How to reverse: Per item, through an ADR; item 7 by the owner.

## ADR-078 — M22 verifier round 2: headline-first matching, pre-registration gate, fresh budget checks (2026-09-26; before any outcome sort)
Context: verifier M22 round 2 @851d58c. No outcome sort has run on any real brief, so outcome-model §5.6 still allows the matching to be refined ("before any outcome sort, never after"); the headline rule itself (ADR-054.1, owner decision) is unchanged. Code: `pigtail.briefs.selection` (`SELECTION_VERSION` `selection-v1` → `selection-v2`), `preregistration`, `budget`, `outcomes`, `shortlist`; migration 0023.
1. **Headline-first matching (refines ADR-077.6).** Eligible losers are unchanged: the same `panel.exact_match` keys, `|ΔLSM| ≤ 0.5 SD`, `|Δquarter| ≤ 1`. Round 1: each winner in rank order takes one eligible loser, **a loser whose pair passes the headline rule first**, then the nearest by §5.6 distance, then the hash. Later rounds (while fewer than `panel.losers` are matched) first hand out only headline-passing losers, then any eligible loser, so a second loser never crowds out a pair that can be reported in the headline, and every matchable winner still gets its first loser. Repo age at T (`log10` days from creation to the anchor) was already a distance term and a balance covariate; it stays both. `prefer_headline=False` keeps the v1 rule for comparison only.
2. **Missing language:** a language missing on either side counts as a mismatch, for the headline check (standardized difference 1) and in the distance (+1).
3. **Before/after on the seeded fixtures** (`tests/selection_fake.population`, example brief's success definition, 20 matched losers; pairs excluded from the headline under the unchanged ADR-054.1 rule). 80 cases, seeds 1–10: v1 **15, 15, 14, 11, 13, 13, 18, 8, 11, 15** (total 133, range 8–18); v2 **15, 15, 13, 11, 10, 10, 17, 3, 11, 15** (total 120, range 3–17). 60 cases, seeds 1–10: v1 17, 17, 16, 9, 15, 13, 17, 13, 16, 14 (147); v2 17, 16, 15, 9, 14, 11, 17, 13, 16, 14 (142). Pairs, matched winners and unmatched winners are the same under both rules on every seed; exclusions never rise. What remains is data-limited: on the 80-case fixtures the number of distinct losers inside the calipers whose pair would pass the headline rule with some winner is 5, 5, 7, 11, 11, 11, 4, 18, 9, 5, so at least 15, 15, 13, 9, 9, 9, 16, 2, 11, 15 of 20 pairs must be excluded whatever the matching does; v2 is within 0–2 pairs of that bound on every seed. The fixture's repo ages and languages are drawn independently of launch signal, so most exclusions stay on age and language; a real neighbourhood may differ. Test: `tests/unit/test_m22_round2.py`.
4. **Balance counting:** after matching, each matched winner counts once in the SMD even when it has two losers (unweighted); the balance record says so (`winner_counting`).
5. **Pre-registration gate (PRD R8.2, ADR-065, outcome-model §5.8).** The selection stage refuses to run unless `brief_preregistration` (migration 0023) has a row for the exact brief version, content hash and selection-parameter hash: `pigtail run` exits 7 before the run row is touched or anything is fetched, computed or stored. `pigtail brief preregister <id> --file <path> [--commit <sha>]` records brief id, version and content hash, `success_definition_sha256` (`Definition.from_brief(...).to_dict()`), `selection_params_sha256` (`Context.params()`, which includes `SELECTION_VERSION`), the file's path and SHA-256 and the commit; `--print-hashes` prints the values to paste into the public file (`docs/preregistration/TEMPLATE-brief.md`), which never holds brief content. A file quoting a free-text brief value (≥ 16 characters, not a schema choice) is refused; so is a pre-registration once the version has a stored selection. The table holds ids and hashes only (no repo key, so no `REPO_TABLES` entry); it is in the export (project level) and the inventory.
6. **Fresh spend at every budget check:** `BudgetGuard.brief_ledger` re-reads the brief's API total from the cost ledger before every money check (the monthly total already came from the usage ledger), so the charges of a batch's own items count before its fallback calls are checked (verifier probe: $0.10 charged + $0.20 fallback > $0.25 cap now stops).
7. **Smaller fixes:** `as_of` is folded into the selection's data version (`dv1-…@YYYY-MM-DD`), so determinism is keyed on (brief version, data version); the refusal list is checked again at finalize (with any GitHub id pigtail already holds for the name) and in `fetch_outcome_data` before any fetch and with the id the metadata query returns (a refused repo leaves the brief version, no star history); bulk actions are logged with `bulk_id`/`bulk_verdict` and precision made only of bulk decisions on the filter's `relevant` verdict is labelled "not item-reviewed" (M22-P); `shortlist show` prints defaulted fields and brief warnings; the web page says why Finalize is disabled; sensitivity alternatives that don't apply say why (for example a minimum a fallback dropped).
How to reverse: items 1–2 only before a brief's outcome sort, by a new ADR and selection version; item 5 by the owner (it enforces R8.2); the others per item through an ADR.
