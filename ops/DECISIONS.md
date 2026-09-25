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

## ADR-024 — Codebook v0.1.0 adopted for the pilot (2026-09-25)
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
