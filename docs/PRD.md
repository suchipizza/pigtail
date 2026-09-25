# pigtail — Product Requirements Document

Version 1.0 · Owner: Noémie · Builders: autonomous Claude Code agents (see `WORK_ORDER.md`)
Repository: https://github.com/suchipizza/pigtail (public, MIT)
Source plan: `docs/PLAN.md`. Where this PRD and the plan disagree, this PRD wins. The plan explains the rationale, and this PRD defines what gets built.
User-facing deliverables (the pages and their acceptance criteria): `docs/DELIVERABLES.md`. The requirements below exist to deliver D1–D6.

---

## 1. Summary

pigtail is an open-source, self-hostable system that reconstructs **how and why** open-source projects grow. It joins GitHub event timelines to external evidence (Hacker News, Reddit, Bluesky, X, YouTube, blogs, package registries and Chinese platforms), stores a snapshot for every claim, and turns the cases into a library of **growth mechanisms tested against matched losers**. On top of the library, a **launch planner** and **growth engine** recommend and support a launch strategy for any open-source project profile.

## 2. Problem

Advice on growing open-source projects is mostly anecdotal and suffers from survivorship bias: it is written by winners, ignores the losers who did the same things, and rarely cites evidence. Existing tools (GH Archive, OSS Insight, star-history, Trendshift) show *who* grew and *when*, but not *why*, and not whether the same move fails for others.

## 3. Users

The system is general-purpose. No user's situation is privileged in the design, defaults or evaluation.

| User | Job to be done |
|---|---|
| OSS maintainer or founder (any audience size, from zero to large) | "Given my project, which launch mechanisms are likely to work, what do I need to prepare, and in what order?" |
| DevRel / GTM team at an open-source company | "Why did competitor X break out? What should our next launch look like? Is our momentum decaying?" |
| Researcher | "Give me a reproducible dataset and codebook for studying OSS diffusion." |
| Operator (self-hoster) | "Run the whole stack on my own infrastructure, with my own credentials, and keep it compliant." |

The owner's own launches (currently 2 planned) are **cases like any other**. They are valuable only because the intervention is controlled and pre-registered, not because the tool is tuned for them.

## 4. Goals and non-goals

**Goals**
- G1: An evidence-backed mechanism library, where every mechanism is tested against matched losers and every claim is linked to a snapshot.
- G2: Automated profiles for ≥ 5,000 repos, semi-automated evidence for ≥ 300, and deep forensics for ≥ 30 matched winner/loser pairs.
- G3: Continuous live detection that opens a case within 24 h of a breakout and starts capture within 48 h.
- G4: A launch planner that produces a ranked, conditional plan for **any** project profile, with confidence levels and the evidence behind each recommendation.
- G5: A fully open-source release: code, codebook, schemas, UI and docs, with optional aggregate findings.

**Non-goals**
- No fake engagement, star-buying, astroturfing, sockpuppets, vote manipulation or automated mass posting. The growth engine never posts on a platform without explicit per-post human approval.
- No public release of person-level data, raw snapshots or spread graphs that identify individuals.
- Not a general social-listening SaaS, and not a hosted multi-tenant service in v1.
- No circumvention of rate limits, paywalls, logins or platform terms.

## 5. Principles (binding)

1. **Snapshot or drop.** Every claim references an evidence record with a content hash. A claim without one is rejected by the pipeline, not flagged.
2. **Losers count.** A mechanism is promoted only if it is observed less often among matched losers, or its effect is visible against them (§9.3).
3. **Multiple outcomes, no composite score.** Attention, adoption, community and business are scored separately, and each value carries a verification tag.
4. **Reuse the plumbing, build the analysis.** Use existing data sources wherever their terms allow; build only what fills a gap.
5. **Replayable.** Any finding can be regenerated from the stored evidence plus versioned code, codebook and prompts.
6. **Generality.** Every stratum (category, audience size, geography, business model, backing, launch type, outcome) is covered, and results are reported per stratum.

## 6. Functional requirements

Requirement IDs are referenced by the work order and by tests.

### F1 Capture layer (runs continuously from the first week)
- R1.1 Scan GH Archive hourly or daily for star and fork velocity, and open a `case` when a repo crosses a configurable threshold (default: ≥ 3σ above its own 30-day baseline **and** ≥ 100 net stars within 48 h, after bot filtering).
- R1.2 For open cases, search every enabled source for mentions of the repo (URL, name, owner) and capture a snapshot within 48 h: raw API JSON where available, rendered HTML or screenshot otherwise, plus a Wayback Machine save request where that is permitted.
- R1.3 Maintain a watchlist of **announced launches** (Product Hunt upcoming, "launching on…" posts, launch-week announcements) and capture their preparation phase before launch day.
- R1.4 Snapshot storage is content-addressed (SHA-256), and each snapshot is stored with source, URL, fetch time, collector version and terms basis.
- R1.5 Deletion sync: re-check sources that impose deletion obligations on a schedule; when content has been removed upstream, drop the raw copy and keep the hash plus coded facts (§10).

### F2 Source connectors
- R2.1 A connector interface with rate limiting, retries, terms metadata, a per-source enable flag and cost accounting.
- R2.2 Connectors are built in the priority order set by the Phase 1 source matrix. The minimum set for v1 is GH Archive / BigQuery, GitHub REST/GraphQL, HN (Algolia + Firebase), Reddit (if terms permit), Bluesky, package registries (PyPI BigQuery, npm, crates.io, Homebrew, Docker Hub), deps.dev and the Wayback Machine. Stretch goals: YouTube, X (if affordable), Product Hunt, Lobste.rs, dev.to, V2EX, Juejin, Zhihu and Bilibili.
- R2.3 A source that can't be used under its terms is recorded as a documented gap, never scraped around.

### F3 Outcome scoring
- R3.1 Score each repo at T+7, T+30, T+90 and T+365, where T is the first burst or the declared launch date.
- R3.2 Metrics per dimension: see §8. Each value carries `verified | self_reported | estimated` plus its source.
- R3.3 Fake-star filtering reproduces a published method (StarScout or its successor, to be confirmed in the literature review). Both the raw and the filtered series are stored.
- R3.4 Normalize by category and quarterly cohort.
- R3.5 Assign outcome classes (§8.2) with versioned thresholds.

### F4 Candidate universe and panel
- R4.1 Build the universe from repos that crossed the velocity threshold in the trailing 24 months, plus repos with launch signals (Show HN, Product Hunt, a first burst) that did not win.
- R4.2 Stratify by category, business model, founder audience size, geography/language, launch type, repo age at launch, backing and outcome class. Report how many cases fall in each cell.
- R4.3 Matched-loser selection: nearest-neighbour matching on launch-signal magnitude, category, launch quarter, repo age, founder audience bucket and language. Report balance diagnostics for every match.
- R4.4 Assign tiers: Tier 1 ≥ 5,000; Tier 2 ≥ 300; Tier 3 ≥ 30 matched pairs.

### F5 Case forensics (Tier 2 and Tier 3)
- R5.1 Evidence inventory: every evidence item with its source, date, reliability score and snapshot.
- R5.2 Timeline: events classified as `prep | launch | burst | quiet | pivot | relaunch`, each with supporting evidence.
- R5.3 Spread graph: nodes are accounts, publications or communities (pseudonymized); edges are `published | redistributed | cited | replied` with an evidence level. An edge is `supported` only above the codebook's evidence threshold.
- R5.4 Asset gallery: the images, GIFs, demos, benchmark claims, titles, phrases and links that spread, each classified with the codebook's asset taxonomy.
- R5.5 Burst-to-trigger attribution: for each burst, rank candidate triggers by timing and reach, with a confidence level.

### F6 Codebook and adaptive modules
- R6.1 The core codebook defines evidence types, the reliability scale, event taxonomy, edge evidence thresholds, asset categories and the mechanism card schema.
- R6.2 Modules switch on by project profile: AI/hype-cycle, B2B open-source SaaS, the Chinese ecosystem, CLI/devtools, corporate-backed, relaunch/pivot.
- R6.3 The codebook is versioned (semver) with a changelog. Every coded item records the codebook version used.

### F7 Extraction pipeline
- R7.1 LLM extraction produces structured records that must cite `evidence_id`s. The validator rejects any claim whose citation doesn't exist or whose quoted span isn't found in the snapshot.
- R7.2 Double coding: two independent extraction runs (different prompts or models), with a third adjudicator run on disagreements. Log agreement statistics (Krippendorff's α per field).
- R7.3 Review queue: low-confidence items, disagreements and a random audit sample go to a queue. The queue can be worked by a human, by a separate verifier agent, or both (see work order §5).
- R7.4 Prompts, model IDs and parameters are versioned and stored with every output.

### F8 Causal analysis toolkit
- R8.1 Event studies of star and download velocity around trigger events (front page, influencer post, newsletter, release).
- R8.2 Matched winner/loser comparisons with pre-registered tests.
- R8.3 Difference-in-differences where a natural comparison group exists.
- R8.4 Survival analysis of momentum decay, used to detect saturation.
- R8.5 Every analysis is a reproducible notebook or script that writes to `reports/`, with its data version pinned.

### F9 Mechanism library
- R9.1 Each mechanism card records: name, description, why people took part and shared, preconditions, required assets, typical sequence and timing, outcome dimensions affected, supporting cases, contradicting cases, loser contrast result, effect estimate with uncertainty, confidence (`low | medium | high`), applicable strata, saturation trend, and the version history.
- R9.2 Promotion rule: §9.3. Cards that don't qualify stay as `candidate`.
- R9.3 Each new case either adds support to a card or contradicts it, and the card's history records which.

### F10 Launch planner and adaptation briefs (deliverable D3)
- R10.1 Input: a project profile (category, audience size, business model, geography, stage, available assets, constraints such as time budget and channels to avoid). The profile can be auto-filled from a GitHub URL through F17.
- R10.2 Output: ranked applicable mechanisms, unmet preconditions, required assets, a proposed sequence and timing, expected effects with uncertainty, and the evidence behind each item. Show "insufficient evidence for this profile" rather than extrapolating.
- R10.3 Adaptation brief per mechanism: how to test it on the given product, and what result would falsify it.
- R10.4 Show similar projects (nearest winners **and** their matched losers) together with why they are similar.
- R10.5 Show trending opportunities from F16 in a separate section labelled experimental. They never appear among the validated recommendations.

### F11 Experiment registry
- R11.1 Pre-register predictions for any tracked launch (owner launches, other users' launches, and announced third-party launches from R1.3): mechanism → expected effect → window → probability.
- R11.2 Predictions are locked (hashed and timestamped) before launch, and scored afterwards (Brier score, calibration).

### F12 Growth engine (execution support; deliverable D4, phase v2)
- R12.1 Asset generation drafts (titles, READMEs, launch posts, demo scripts, comparison tables), generated from mechanism cards.
- R12.2 A channel schedule with reminders. Posting always requires human approval of each post; there is no auto-posting.
- R12.3 Post-launch monitoring, with alerts to amplify, hold, relaunch or pivot based on decay models.
- R12.4 Only mechanisms that passed §9.3 are used.

### F13 UI
- R13.1 The pages and acceptance criteria are defined in `docs/DELIVERABLES.md`:
  - `/cases` and `/cases/:id` (D1)
  - `/insights` (D2)
  - `/plan` (D3)
  - `/execute` (D4, v2)
  - `/analyze` (D5)
  - `/admin` and `/settings` (D6)
- R13.2 Every displayed claim can be traced to its evidence, and the snapshot opens with one click.
- R13.3 The app is private by default, behind operator authentication. An optional public mode serves only aggregate, anonymized D2 content.

### F14 CLI and API
- R14.1 A CLI for every pipeline stage (`pigtail capture|score|panel|extract|analyze|plan|report`).
- R14.2 A read-only HTTP API behind the UI.

### F15 LLM backend switch
- R15.1 All LLM calls — the pipeline, extraction, planner and asset drafting — go through a single `LLMClient` interface with two backends, selected by `LLM_BACKEND=subscription|api` (default: `subscription`). It is also shown in `/settings`.
- R15.2 The `subscription` backend calls the operator's locally installed, **official** Claude Code CLI in headless mode (`claude -p`, JSON output). It authenticates through Anthropic's own login flow or through a `CLAUDE_CODE_OAUTH_TOKEN` that the operator generated with `claude setup-token` and placed in their own environment.
  - pigtail never implements its own OAuth, and never collects, stores, proxies or transmits Claude credentials.
  - `ANTHROPIC_API_KEY` is removed from the subprocess environment, because it would override the subscription.
  - Bare mode is not used, because it ignores the OAuth token.
- R15.3 The `api` backend uses the Anthropic SDK with `ANTHROPIC_API_KEY`. It is the recommended backend for shared or hosted deployments, and for high-volume runs.
- R15.4 Both backends expose the same interface:
  - Structured-output validation.
  - A prompt/model version record.
  - A cache keyed on (prompt version, input hash).
  - A cost/usage ledger: tokens in `api` mode; session counts and limit hits in `subscription` mode.
- R15.5 Usage-limit handling in `subscription` mode: detect limit-reached responses, pause the job queue until the reset time, resume, and log the pause. Jobs are resumable, so nothing is lost. Optional per-job overrides allow a heavy job (for example Tier 2 extraction) to run on `api` while everything else stays on `subscription`.
- R15.6 Documentation states the scope of the subscription backend: it is for an operator running pigtail for themselves on their own Claude plan, within Anthropic's terms for Claude Code. Deployments that serve other users must use `api`. The operator guide links to Anthropic's current legal and compliance page for Claude Code.
- R15.7 Output parity check: a fixed evaluation set is run on both backends, and extraction agreement must be α ≥ 0.70 between them before a backend switch is accepted for coded data.

### F16 Trends (deliverable D2, "What's trending")
- R16.1 Compute rising channels, communities, asset formats and message patterns over 30- and 90-day windows against a trailing 12-month baseline, with n and the window shown on every claim.
- R16.2 Detect saturation: mechanisms whose effect is declining over time (see R8.4).
- R16.3 Trends are labelled descriptive ("emerging signal") and are kept separate from promoted mechanisms everywhere.

### F17 On-demand analyzer and tracking (deliverable D5)
- R17.1 Given any GitHub URL, open a case and run a staged analysis:
  - Stage 1: GitHub history, outcomes and a benchmark, in ≤ 10 min.
  - Stage 2: an external evidence backfill (HN, Reddit, Bluesky, registries, Wayback), in ≤ 6 h.
  - Stage 3: coding and mechanism matching.
- R17.2 Current-state summary: momentum versus the repo's own baseline, burst/decay/stable status, and mentions from the last 7 days.
- R17.3 Coverage score per source, showing each source's coverage window and stating that ephemeral evidence from before tracking started may be missing.
- R17.4 "Track this project" moves the case into live capture (F1) within 1 h. The operator can stop tracking at any time.
- R17.5 Validation: on ≥ 5 Tier 3 cases, the reconstruction recovers ≥ 80% of human-verified key events.

## 7. Data model (versioned JSON Schemas in `schemas/`)

Core entities: `repo`, `case`, `evidence` (id, source, url, fetched_at, content_hash, snapshot_ref, reliability, terms_basis, retention_class, deletion_state), `event`, `actor` (pseudonymized), `edge`, `asset`, `outcome_observation`, `mechanism`, `mechanism_support` (case ↔ mechanism, direction, strength), `prediction`, `codebook_version`, `run` (code/prompt/model versions).
Requirements: every record carries `schema_version`; records are stored in a mergeable, diffable format (JSONL export alongside the database); every run is recorded so that it can be replayed.

## 8. Outcome model

### 8.1 Metrics

| Dimension | Metric | Primary source | Default tag |
|---|---|---|---|
| Attention | Star velocity (raw and filtered) | GH Archive | verified |
| | HN points, comments, front-page minutes | HN Algolia + own rank polling | verified |
| | Reddit and Bluesky reach | Platform APIs | verified |
| Adoption | Registry downloads | PyPI BigQuery, npm, crates.io, Homebrew, Docker Hub | verified (noisy) |
| | Dependents | deps.dev, GitHub dependency graph | verified |
| Community | Returning external contributors (≥ 2 merged PRs across ≥ 2 months) | GH Archive | verified |
| | External issue/PR activity | GH Archive | verified |
| | Discord/Slack size | Invite APIs | estimated |
| Business | MRR | Stripe-verified dashboards (e.g. TrustMRR) | verified |
| | Funding | Crunchbase, YC directory, press | self_reported |
| | Paid offering or pricing page exists | Wayback snapshots | verified |
| | Hiring | Careers pages, HN Who's Hiring | verified |

### 8.2 Outcome classes (v0 thresholds; calibrated in the pilot and versioned)
- `winner`: top decile of its cohort on attention at T+30 **and** top quartile on adoption or community at T+90.
- `attention_only`: a burst without adoption or community follow-through.
- `short_lived`: crossed the threshold, but T+90 velocity is < 10% of the T+30 peak and adoption is flat.
- `plateau` (matched loser): had a comparable launch signal but never reached the winner threshold.
- `slow_riser`: no burst, but winner-level adoption by T+365.

## 9. Evaluation and success criteria

### 9.1 System tests (all must pass for v1)
1. **Forecasting test.** Using live-detected cases, predict the 30-day outcome class from data available 7 days after detection. The model must beat a baseline (star velocity + category base rate) on Brier score over ≥ 200 cases, with a 95% bootstrap CI that excludes zero improvement.
2. **Loser-value test.** On held-out Tier 3 pairs, a mechanism ranking built with loser matching must identify the winner's actual mechanisms better than a naive ranking by frequency among winners. If it fails, the report says so explicitly.
3. **Generality test.** Planner recommendations are evaluated per stratum on held-out cases. The planner must return "insufficient evidence" for strata with too few cases instead of extrapolating. Coverage is reported for every stratum.
4. **Prospective test.** Pre-registered predictions for ≥ 20 announced launches (third-party and owner) are scored with Brier score and calibration curves, and compared with the naive baseline.

### 9.2 Data-quality gates
- 100% of published claims resolve to an evidence record with a valid hash.
- Extraction agreement: Krippendorff's α ≥ 0.70 on core fields between independent coders. If a human calibration sample exists, LLM–human α ≥ 0.70 on it.
- Matched-pair balance: standardized mean difference < 0.25 on every matching covariate.

### 9.3 Mechanism promotion rule
A mechanism becomes `promoted` only if all of the following hold:
- ≥ 5 supporting winner cases from ≥ 2 strata.
- A higher prevalence among winners than among their matched losers, or an event-study effect whose 90% CI excludes zero.
- Contradicting cases are listed and explained.
- Its preconditions are stated in a testable form.

Otherwise it stays `candidate`. Confidence levels (`low | medium | high`) are defined in the codebook.

## 10. Non-functional requirements

- **Self-hosting:** `docker compose up` brings up the full stack. It runs on a single mid-size VM, with object storage (S3-compatible) for snapshots. Default hosting region: EU or Switzerland.
- **Reproducibility:** pinned dependencies, versioned data releases, and a fixed seed for any sampling.
- **Cost control:** per-source and per-LLM budgets with hard stops; a running cost ledger.
- **Privacy and compliance** (GDPR and Swiss FADP):
  - Pseudonymize handles at ingest with a keyed hash, and store the key separately.
  - Raw person-level data is retained for 24 months, then aggregated or deleted. Project-level data has no time limit.
  - Deletion sync (R1.5).
  - A public privacy notice.
  - A legitimate-interest assessment and a light DPIA, kept in `docs/compliance/`.
  - LLM processing:
    - `api` mode: use zero-retention settings or a data processing agreement where available.
    - `subscription` mode: pseudonymize handles and strip direct identifiers **before** any snapshot content is sent to the model, and the operator must turn off model-training on their Claude account (an H1 item).
- **Terms:** each connector documents the terms it relies on. Commercial-use terms are assumed.
- **Security:** secrets are kept only in environment variables or a secrets manager, never in the repo. Private data is never pushed to the public repository; a CI check scans for this.
- **Observability:** collector health, backlog, error rates and costs are visible in the UI.
- **Public repository from day one:** https://github.com/suchipizza/pigtail.
  - Git contains only code, docs, schemas, the codebook, pre-registrations and ops logs.
  - A CI private-data scan blocks any snapshot, raw record, handle or secret.
  - Ops logs must never contain personal data or secrets.
- **Licensing:** MIT for code (already in the repo). The codebook and methodology are MIT too, unless the owner chooses CC BY 4.0.

## 11. Release criteria (v1)
- All of §9.1 and §9.2 pass, or failures are documented in the final report and the owner accepts them.
- The mechanism library contains ≥ 10 promoted mechanisms, each with its loser contrast.
- Documentation is complete: install guide, operator guide, methodology paper, codebook, schema reference, data card and limitations.
- Legal review is completed and its findings applied.
- The owner approves publishing aggregate findings and tagging the v1 release (a human gate). The code is public from the start.
- pigtail's own launch is registered as a case, with predictions locked before launch.

## 12. Defaults assumed (agents may revise them with an ADR)
- LLM backend: `subscription` (owner's Claude plan via the Claude Code CLI); `api` is available and can be switched per job or globally.
- Stack: Python 3.12 + uv, Postgres, S3-compatible object storage, DuckDB for analytics, FastAPI, TypeScript/React UI, Cytoscape.js for graphs.
- No composite outcome score.
- The capture layer is exempt from the "pilot first" gate; the analysis layer is not.
