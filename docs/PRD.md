# pigtail — Product Requirements Document

Version 2.2 · Owner: Noémie (also pigtail's first user) · Builders: autonomous Claude Code agents (see `WORK_ORDER.md`)
Repository: https://github.com/suchipizza/pigtail (public, MIT)
Source plan: `docs/PLAN.md` (background only). Where this PRD and the plan disagree, this PRD wins. Where this PRD and an ADR in `ops/DECISIONS.md` disagree, the later-dated ADR wins until this PRD is updated.
Binding source for v2.2: `ops/OWNER_DIRECTIVE_001.md` (Owner Directive 001, 2026-09-26; public copy with §4 redacted), logged as ADR-059 to ADR-069, plus the owner's later decisions ADR-070 and ADR-071, which override the directive where they differ. Changes below cite "Directive §n" and the ADR.
User-facing deliverables (the pages and their acceptance criteria): `docs/DELIVERABLES.md`. The requirements below exist to deliver D1–D7.

## Change history

| Version | Date | Change |
|---|---|---|
| 2.2 | 2026-09-26 | Owner Directive 001 applied (Directive §0.3). Framing: personal project, Crawl4AI distribution model, nothing leaves the owner's instance (§1, ADR-059). Product LLM calls on `api`; `subscription` kept for other users' individual, non-commercial use (R15.1, R15.6; Directive §6.1, ADR-064.1). New R15.8 model assignment, R15.9 Batch API and prompt caching, R15.10 evidence trimming, R15.11 budget caps (Directive §6.3–6.4, ADR-064.3–4). Privacy model: roles and buckets instead of handles, opt-out HMAC only, bot flag on the coded record, snapshots kept until report final + 12 months, minimal collection, official APIs, Reddit metadata only, one short excerpt per source, publishing disabled, US inference (R2.2, R5.3, §7, §10; Directive §8, ADR-066, ADR-071). Stars: daily star-history counts, day-level attribution labels, launch-mode star polling, aggregate anomaly checks labelled "unfiltered, anomaly-checked" (R2.2, R3.3, R5.5, R19.4, §8.1; ADR-070, overriding Directive §5.3). Briefs stored outside git in `~/.pigtail/briefs` and backed up (R18.9; ADR-071.3). Brief fields aligned with Directive §2.1 (R18.1); reports record the code commit (§5.5, R18.6; ADR-060). New R19.9 snapshot purge job, R19.10 cache purge. R13.3 public mode disabled. §11 release criteria and §12 defaults updated. No requirement ID was renumbered. |
| 2.1 | 2026-09-26 | Matching and panel rules (ADR-054, owner decision): exact match on founder audience bucket and launch half-year; SMD < 0.25 a target for other characteristics, balance always shown, pairs > 0.5 excluded from headline patterns (R4.3, §9.2). New R4.10 field widening with distance labels; R4.11 named reference cases and the repo-choice rule. |
| 2.0 | 2026-09-26 | Re-scoped to brief-driven neighbourhood analysis (ADR-047, owner change request CR-002) and to batch runs with launch mode instead of continuous capture (ADR-048, CR-001). New: the research brief (F18), batch runs and launch mode (F19), neighbourhood patterns (F20). Cut: Tier 1 at scale, the global 24-month backfill, global breakout detection, the global mechanism library and its promotion rule, the §9.1 system tests, and the causal toolkit beyond event studies and winner/loser contrasts. §5.3 amended (ADR-047.2). Retired requirement IDs are listed where they stood and are never reused. |
| 1.0 | 2026-09-25 | Global collection and mechanism-library scope. The full text is in git history (for example `git show archive/global-collection:docs/PRD.md`); the tag `archive/global-collection` marks the code state before the re-scope. |

---

## 1. Summary

pigtail is an open-source, self-hostable **method** for learning how recent, relevant open-source launches grew, and why. A user describes their own project in a **research brief**. pigtail finds the projects in that neighbourhood, lets the user check the shortlist, sorts the candidates by the user's own success definition, and picks 15–25 winners and 15–25 matched losers. It then reconstructs each case from GitHub timelines and external evidence (Hacker News, Bluesky, blogs, package registries and other cleared sources), stores a snapshot for every claim, and produces a **neighbourhood report**: the patterns that separate winners from their matched losers, each with n, the loser contrast and the counterexamples, plus what has been trending in the last 3–6 months. A **plan generator** turns the report into a launch plan, and **launch mode** follows a launch closely while it happens.

**pigtail ships a method, not data.** Each user runs their own instance, with their own credentials, on their own machine. Nothing is shared or collected centrally. The owner is the first user; nothing in the code, defaults or fixtures is specific to her or her project.

**Framing (Directive §1, ADR-059).**
- pigtail is **a personal project for now**. It is not offered as a service, has no customers, and no findings leave the owner's instance.
- **Distribution follows the Crawl4AI model:** an open-source tool that each user installs and runs themselves, with their own credentials and their own data. pigtail ships code, method, templates and docs; it ships **no data and no hosted service**. Each user is responsible for their own instance, and pigtail's job is to make the safe setup the default (Directive §8.8, ADR-066.8).
- **Versatility means anyone can run it:** each user tailors the research to needs they state in a research brief (F18). There is no global or all-GitHub dataset.

## 2. Problem

Advice on growing open-source projects is mostly anecdotal and suffers from survivorship bias: it is written by winners, ignores the losers who did the same things, and rarely cites evidence. Existing tools (GH Archive, OSS Insight, star-history, Trendshift) show *who* grew and *when*, but not *why*, and not whether the same move failed for comparable projects. Generic advice also ignores the neighbourhood: what worked for a JavaScript UI kit says little about a Rust database driver.

## 3. Users

Any user can run pigtail on their own project's neighbourhood. No user's situation is privileged in the design, defaults or evaluation.

| User | Job to be done |
|---|---|
| OSS maintainer or founder (any audience size, from zero to large) | "Given my project and my definition of success, which recent projects like mine did well, which comparable ones didn't, what separated them, and what should I prepare, in what order?" |
| DevRel / GTM team at an open-source company | "Why did competitor X break out, and did the same moves fail for others in our space? What should our next launch look like? Is our momentum decaying?" |
| Researcher | "Give me a reproducible method, codebook and schemas I can run on a neighbourhood I choose." |
| Operator (every user is one) | "Run it on my own machine, with my own credentials and budget, and keep it compliant as the controller of my own instance." |

The owner is the first user. Her project's neighbourhood is the pilot (§9), and her launches are cases like any other: valuable because the intervention is declared and predictions are locked in advance, not because the tool is tuned for them.

## 4. Goals and non-goals

**Goals.** Goal IDs are not reused; retired goals are listed so that references in older documents stay resolvable.
- ~~G1: A global evidence-backed mechanism library.~~ **Retired** (ADR-047.3). Replaced by G6.
- ~~G2: Automated profiles for ≥ 5,000 repos, semi-automated evidence for ≥ 300, deep forensics for ≥ 30 matched pairs.~~ **Retired** (ADR-047.3: Tier 1 at scale and the global backfill are cut).
- ~~G3: Continuous live detection within 24 h of any breakout on GitHub.~~ **Retired** (ADR-047.6, ADR-048). Launch mode (R19.4) replaces it for tracked projects.
- G4 (amended): A plan generator that produces a ranked, conditional plan for **any user's brief**, with the evidence behind each recommendation, built from that brief's neighbourhood report.
- G5 (amended): A fully open-source release of the method: code, codebook, schemas, UI, docs, an example brief and a compliance template. No data is shipped.
- G6 (new): For any research brief, a neighbourhood report in which every pattern shows n, its loser contrast and its counterexamples, and every claim links to a snapshot.
- G7 (new): A new user installs pigtail and starts a first brief in under an hour, with a cost estimate before anything is spent.

**Non-goals**
- No fake engagement, star-buying, astroturfing, sockpuppets, vote manipulation or automated mass posting. The execution engine (D4) never posts on a platform without explicit per-post human approval.
- No public release of person-level data, raw snapshots or spread graphs that identify individuals.
- **No publishing from the owner's instance** (Directive §8.6, ADR-066.6, H4 disabled): no findings, data or reports leave it until the owner revokes the directive. The public repo holds only code, docs, templates and synthetic fixtures.
- No central service: pigtail doesn't collect, pool or receive any user's briefs, data, credentials or usage. Not a hosted multi-tenant service.
- No global collection: no watch list of all GitHub, no all-GitHub search sweeps, no global breakout detection, no global mechanism library (ADR-047.3, ADR-047.6).
- No circumvention of rate limits, paywalls, logins or platform terms.

## 5. Principles (binding)

1. **Snapshot or drop.** Every claim references an evidence record with a content hash. A claim without one is rejected by the pipeline, not flagged.
2. **Losers count.** No pattern is reported from winners alone. Every neighbourhood pattern shows how often it occurred among the brief's matched losers, with n on both sides, and lists its counterexamples (F20).
3. **Multiple outcomes; results always per dimension** (amended by ADR-047.2). Attention, adoption, community and business are scored separately, and each value carries a verification tag. A brief's success definition is one primary dimension plus minimum thresholds on the others. A composite (weights, an advanced option only) may be used to **rank candidates within a brief**, but results are always shown per dimension, and every report includes a sensitivity check of its winner set (R4.9).
4. **Reuse the plumbing, build the analysis.** Use existing data sources wherever their terms allow; build only what fills a gap. Data already collected is a cache that briefs reuse.
5. **Replayable.** Any finding can be regenerated from the stored evidence plus versioned code, codebook, prompts **and brief version**. Every report records the brief version, the shortlist decisions, the data version **and the code commit** (R18.6; Directive §2.3, ADR-060).
6. **Works for any user's neighbourhood** (replaces "Generality"). Nothing in the code, defaults, prompts, fixtures or evaluation is specific to the owner or her project. Field-specific knowledge enters only through a brief. The example brief shipped in the repo is synthetic.
7. **A method, not data; your instance, your credentials** (new; ADR-047). Each user runs their own instance with their own credentials and is the controller of their own data. Briefs, collected data and reports stay in that instance's private storage and are never pushed to the public repo or sent to any central service.

## 6. Functional requirements

Requirement IDs are referenced by the work order, by tests and by the backlog. Where a requirement survives, its ID is kept (marked "amended" if its text changed). Retired IDs are listed with their reason and are never reused. New requirements get new IDs.

### F1 Capture layer (runs inside batch runs; see F19)
- ~~R1.1 Global GH Archive velocity scan and case opening.~~ **Retired** (ADR-047.6: global breakout detection is deleted; ADR-047.8: GH Archive is discovery-only). Burst detection for tracked projects is R19.5.
- R1.2 (amended) For every case in a brief's winner/loser set and every tracked project, search each enabled source for mentions of the repo (URL, name, owner) at every run, and snapshot what is found at the run that first sees it: raw API JSON where available, rendered HTML or screenshot otherwise, plus a Wayback Machine save request where that is permitted. The evidence-decay study (R19.8) measures how much this cadence loses.
- ~~R1.3 Global watchlist of announced launches.~~ **Retired**. Its purpose was the prospective test (§9.1 v1, cut by ADR-047.3). A user declares their own launch through launch mode (R19.4), and launch-mode cases remain the prospective set for any later evaluation (ADR-048.4).
- R1.4 Snapshot storage is content-addressed (SHA-256), and each snapshot is stored with source, URL, fetch time, collector version and terms basis.
- R1.5 Deletion sync: re-check sources that impose deletion obligations at every scheduled run (and at least as often as the source's obligation requires); when content has been removed upstream, drop the raw copy and keep the hash plus coded facts (§10).

### F2 Source connectors
- R2.1 A connector interface with rate limiting, retries, terms metadata, a per-source enable flag and cost accounting.
- R2.2 (amended; ADR-047.8, ADR-010) Minimum set for v2:
  - **GitHub REST/GraphQL** for repo metadata, search, topics, releases, issues, PRs and contributors. Stars come from `GET /repos/{owner}/{repo}/stargazers/history`, the **star-history endpoint's daily net counts**; resolution is daily (ADR-032.3, ADR-070.1). Per-user star timestamps are not available: GitHub restricted stargazer lists to admins and collaborators on 2026-06-30. Directive §5.3's "stargazer timestamps; GraphQL sorted by star time" is therefore not implemented; the owner accepted daily counts instead (ADR-063, ADR-070). Known limits, documented in the source matrix: unstars can't be seen individually (the series is net), and very large repos can hit pagination caps (Directive §5.3).
  - **GH Archive / BigQuery**, restricted to the brief's topics and used for **discovery signals only** (activity, first releases). It has been close to push-events-only since mid-2025 and is never a source for stars, forks, issues or PRs (Directive §5.3, ADR-063).
  - **Hacker News**: the front-page rank poller (project-level), Show HN discovery, and mention search subject to ADR-022's person-level holds.
  - **Awesome-lists** (read through the GitHub API) for discovery.
  - Bluesky (subject to ADR-022), package registries (PyPI BigQuery, npm, crates.io, Homebrew, Docker Hub), deps.dev, and the Wayback Machine (off until H2 answers).
  - **Trendshift**: **off by default** (Directive §8.4, ADR-066.4); an optional, per-user discovery source (ADR-047.1), enabled only once its terms are cleared in the source matrix (ADR-010).
  - **Reddit: metadata only** (link, title, score, timestamp) until Reddit API approval is granted (Directive §8.4, ADR-066.4), and only through the official API once the source matrix records that clearance; until then it stays a documented gap (ADR-010).
  - Sources recorded as gaps in the source matrix (YouTube, X, Product Hunt and others) stay gaps until their clearance changes (R2.3).
  - **Official APIs only**, with rate limits respected with margin and deletions honoured (Directive §8.4, ADR-066.4).
  - **Minimal collection** (Directive §8.3, ADR-066.3): mention search covers **shortlisted projects only**. No sweeps of individual users or accounts, and no collection of follower lists.
- R2.3 A source that can't be used under its terms is recorded as a documented gap, never scraped around.
- R2.4 (new) Every connector uses **the operator's own credentials**, read from their own environment or secrets manager. pigtail ships no credentials, no shared keys and no proxy, and connectors send nothing to any pigtail-operated endpoint.

### F3 Outcome scoring
- R3.1 Score each repo at T+7, T+30, T+90 and T+365, where T follows ADR-015 (declared launch or burst onset). A horizon that hasn't been reached at run time is `pending`, never imputed.
- R3.2 Metrics per dimension: see §8.1. Each value carries `verified | self_reported | estimated | unknown` (ADR-014) plus its source.
- R3.3 (amended again, ADR-070.4) **Aggregate anomaly checks replace per-account fake-star filtering.** Methods that inspect individual stargazer accounts (StarScout-style) no longer work for repos the operator doesn't own. Instead pigtail flags star spikes with no matching forks, issues, downloads or external mentions, and unusual stars-to-activity ratios. Star metrics are labelled **"unfiltered, anomaly-checked"**. The check's parameters live in a new version of `analysis-params`; the outcome model, codebook and literature notes are updated to match (ADR-070.4).
- R3.4 (amended) Percentiles used in a success definition are computed **within the brief's reference population** (its final shortlist), and the report states that population's n. The global normalization cells of ADR-018 don't apply inside a brief.
- ~~R3.5 Global outcome classes with versioned thresholds.~~ **Retired as the way winners and losers are chosen** (ADR-047.1–2): a brief's success definition (R18.8, §8.2) does this instead.

### F4 Candidate discovery, shortlist and matched sets (per brief)
- ~~R4.1 Global universe of repos crossing the velocity threshold in the trailing 24 months.~~ **Retired** (ADR-047.3).
- ~~R4.2 Global stratification with case counts per cell.~~ **Retired** (ADR-047.3). A brief's field boundaries (R18.1) define its population.
- R4.3 (amended, ADR-054) Matched-loser selection within the brief's shortlist: **exact match** on founder audience bucket and launch period (same half-year); nearest-neighbour matching on launch-signal magnitude, repo age at launch and language. Report balance diagnostics for every match and every characteristic (§9.2).
- ~~R4.4 Tier assignment (Tier 1 ≥ 5,000; Tier 2 ≥ 300; Tier 3 ≥ 30 pairs).~~ **Retired** (ADR-047.3).
- R4.5 (new) **Candidate discovery** from the brief's expanded description (R18.7) and time window: GitHub search and topics, Show HN, awesome-lists, Trendshift (optional, per user, once cleared), and GH Archive restricted to the brief's topics (discovery signals only). Each candidate records which source found it.
- R4.6 (new) **LLM relevance filter.** Every candidate is judged against a written rubric derived from the brief (field boundaries, target users, problem). The verdict and the reason are logged per candidate, with the rubric version, prompt version and model.
- R4.7 (new) **Shortlist review.** The user accepts, rejects or adds candidates. Every decision is logged with the brief version and a reason. Relevance-filter **precision** (the share of filter-accepted candidates the reviewer keeps) is logged per brief version; the target is **≥ 80%**. For the pilot, the owner reviews the shortlist (or the verifier skims it if she doesn't; WORK_ORDER §4.5, M23). The review is also where brief fields that were filled with defaults, and unresolved reference cases, are confirmed (Directive §4 generic rule, ADR-062). A precision below target is reported, never hidden.
- R4.10 (new, ADR-054) **Field widening.** If the core field yields too few winners or losers, the brief widens step by step to adjacent fields it declares (for example: the tooling around the core field, then the broader developer-tool category). Every case is labelled with its distance from the core field (0 = core), and findings for the core field are always reported separately.
- R4.11 (new, ADR-054) **Named reference cases.** A brief may name reference projects that stay in the report whatever their outcome, labelled as reference cases. When a project has several repos, pigtail studies the repo its launch posts linked to; if both a site and an engine were promoted, the engine is studied and the site is treated as an asset. The choice is logged per project.
- R4.8 (new) **Outcome sort and selection.** The final shortlist is sorted by the brief's success definition (§8.2). pigtail selects the brief's number of winners (default 20, range 15–25) and the same number of matched losers (default 20, range 15–25; R4.3). The selection is deterministic for a given brief version and data version.
- R4.9 (new) **Sensitivity check of the winner set.** The winner set is recomputed under reasonable alternative success definitions (for example, each other dimension as primary, and thresholds one band looser and tighter). The report states how much the set changes and flags every case whose winner or loser role depends on the definition.

### F5 Case forensics (the brief's winners and losers, and tracked projects)
- R5.1 Evidence inventory: every evidence item with its source, date, reliability score and snapshot.
- R5.2 Timeline: events classified as `prep | launch | burst | quiet | pivot | relaunch`, each with supporting evidence (codebook v0.2.0 two-layer rule, ADR-024.1, ADR-039).
- R5.3 (amended, Directive §8.1, ADR-066.1) Spread graph: nodes are **roles and buckets**: maintainer, account by follower bucket, newsletter, community, organization. **Handles and personal names are never stored** in coded data; organizations and projects may be named. The earlier pseudonymized-account model (ADR-022, ADR-024.8, ADR-030/042/045) is replaced; existing data is migrated, then handles and pseudonyms are purged (WORK_ORDER M21). Edges are `published | redistributed | cited | replied` with an evidence level. An edge is `supported` only above the codebook's evidence threshold.
- R5.4 Asset gallery: the images, GIFs, demos, benchmark claims, titles, phrases and links that spread, each classified with the codebook's asset taxonomy.
- R5.5 (amended, ADR-070.2) Burst-to-trigger attribution: for each burst, rank candidate triggers by timing and reach, with a confidence level. Trigger times come from the timestamps on HN, Reddit and Bluesky posts; any attribution that relies on daily star data is labelled **"day-level"**. Intra-day attribution is possible only where launch-mode star polling (R19.4) exists.

### F6 Codebook and adaptive modules
- R6.1 (amended) The core codebook defines evidence types, the reliability scale, event taxonomy, edge evidence thresholds, asset categories and the **neighbourhood pattern** schema (F20; formerly the mechanism card schema).
- R6.2 Modules switch on by project profile: AI/hype-cycle, B2B open-source SaaS, the Chinese ecosystem, CLI/devtools, corporate-backed, relaunch/pivot.
- R6.3 The codebook is versioned (semver) with a changelog. Every coded item records the codebook version used.

### F7 Extraction pipeline
- R7.1 LLM extraction produces structured records that must cite `evidence_id`s. The validator rejects any claim whose citation doesn't exist or whose quoted span isn't found in the snapshot.
- R7.2 Double coding of **each brief's deep forensics**: two independent extraction runs (different prompts or models), with a third adjudicator run on disagreements. Log agreement statistics (Krippendorff's α per field).
- R7.3 Review queue: low-confidence items, disagreements and a random audit sample go to a queue. The queue can be worked by the user, by a separate verifier agent, or both.
- R7.4 Prompts, model IDs and parameters are versioned and stored with every output.
- R7.5 (new; ADR-047.7; confirmed by Directive §7, ADR-065) Agreement is shown **per field** in every report. Findings that rest on a field with α < 0.70 are labelled **"low reliability"**, not dropped. If the owner codes a calibration sample (optional H3), LLM–human agreement is shown; otherwise findings are labelled "LLM-coded, not human-validated".
- R7.6 (new; Directive §8.5, ADR-066.5) **Outputs never reproduce snapshots.** Any report, plan or export contains at most **one short attributed excerpt per source**.

### F8 Analysis toolkit (event studies and winner/loser contrasts only)
- R8.1 Event studies of star and download velocity around trigger events (front page, influencer post, newsletter, release).
- R8.2 (amended) Winner/loser contrasts within a brief, with the tests fixed in the method version before the brief's outcome sort. For the pilot they are pre-registered (WORK_ORDER §6).
- ~~R8.3 Difference-in-differences.~~ **Retired** (ADR-047.3).
- ~~R8.4 Survival analysis of momentum decay.~~ **Retired** (ADR-047.3). D5's descriptive burst/decay/stable status (R17.2) doesn't depend on it.
- R8.5 Every analysis is a reproducible script that writes to the instance's report store, with the brief version and data version pinned.

### F9 Mechanism library — **retired** (ADR-047.3)
- ~~R9.1 Mechanism card fields.~~ ~~R9.2 Promotion rule (§9.3 v1).~~ ~~R9.3 Global card history across cases.~~ All three are retired. Their successor is F20 (neighbourhood patterns). The seed candidate cards in `docs/methodology/mechanisms/` remain useful as seed codes for patterns.

### F10 Plan generator and adaptation notes (deliverable D3)
- R10.1 (amended) Input: a brief and a version of its neighbourhood report. The project profile (category, audience per channel, business model, geography, stage, available assets, constraints such as time budget and channels to avoid) comes from the brief and can be auto-filled from a GitHub URL through F17.
- R10.2 (amended) Output: the report's patterns ranked by relevance to the brief's success definition, unmet preconditions, required assets, a proposed sequence and timing, and the evidence behind each item, with each pattern's n, loser contrast, counterexamples and reliability labels. Show "insufficient evidence in this neighbourhood" rather than extrapolating.
- R10.3 (amended) **Adaptation note** per pattern (renamed from "adaptation brief" to avoid confusion with the research brief): how to test it on the user's project, and what result would falsify it.
- R10.4 (amended) Show similar projects: the brief's nearest winners **and** their matched losers, with why they are similar.
- R10.5 (amended) Show the report's trending section (F16) separately, labelled experimental. Trends never appear among the pattern-based recommendations.

### F11 Experiment registry
- R11.1 (amended) Pre-register predictions for any launch in launch mode (R19.4), the user's own included: pattern → expected effect → window → probability.
- R11.2 Predictions are locked (hashed and timestamped) before launch, and scored afterwards (Brier score, calibration).

### F12 Execution support (deliverable D4, phase v2)
- R12.1 (amended) Asset generation drafts (titles, READMEs, launch posts, demo scripts, comparison tables), generated from the plan's patterns.
- R12.2 A channel schedule with reminders. Posting always requires human approval of each post; there is no auto-posting.
- R12.3 (amended) Post-launch monitoring through launch mode (R19.4), with alerts to amplify, hold, relaunch or pivot.
- R12.4 (amended) Only patterns from a neighbourhood report are used, and each generated asset shows the pattern's n, loser contrast and reliability labels.

### F13 UI
- R13.1 (amended) The pages and acceptance criteria are defined in `docs/DELIVERABLES.md`:
  - `/briefs` (D7, new): create, edit, version and run briefs; review shortlists.
  - `/cases` and `/cases/:id` (D1)
  - `/insights` (D2): the neighbourhood report, one per brief
  - `/plan` (D3)
  - `/execute` (D4, v2)
  - `/analyze` (D5)
  - `/admin` and `/settings` (D6)
- R13.2 Every displayed claim can be traced to its evidence, and the snapshot opens with one click.
- R13.3 (amended, Directive §8.6, ADR-066.6) The app is private, behind operator authentication. **Public mode is disabled** on the owner's instance (H4 disabled until the owner revokes the directive). The code may keep the option for other users, off by default, serving only aggregate, anonymized content after that user's own legal check.

### F14 CLI and API
- R14.1 (amended) A CLI for every pipeline stage: `pigtail brief|run|capture|score|extract|analyze|plan|report` (plus the existing `privacy`, `retention`, `doctor`, `health`, `alerts` and `llm` commands).
- R14.2 A read-only HTTP API behind the UI.

### F15 LLM backend switch
- R15.1 (amended, Directive §6.1, ADR-064.1) All LLM calls — discovery expansion, relevance filter, extraction, plan generation and asset drafting — go through a single `LLMClient` interface with two backends, selected by `LLM_BACKEND=subscription|api`. It is also shown in `/settings`.
  - **The owner's instance runs every product LLM call on `api`** (her own Anthropic API key, under Anthropic's Commercial Terms; key verified 2026-09-26).
  - The shipped `.env.example` sets `LLM_BACKEND=api`. The code's built-in fallback when the variable is unset is `subscription` today; whether to change it is an implementation question for M21 (flagged, not decided here).
  - Pigtail never switches backends on its own (ADR-053, ADR-064.2).
- R15.2 The `subscription` backend calls the operator's locally installed, **official** Claude Code CLI in headless mode (`claude -p`, JSON output). It authenticates through Anthropic's own login flow or through a `CLAUDE_CODE_OAUTH_TOKEN` that the operator generated with `claude setup-token` and placed in their own environment.
  - pigtail never implements its own OAuth, and never collects, stores, proxies or transmits Claude credentials.
  - `ANTHROPIC_API_KEY` is removed from the subprocess environment, because it would override the subscription.
  - Bare mode is not used, because it ignores the OAuth token.
- R15.3 (amended, ADR-064.1) The `api` backend uses the Anthropic SDK with the operator's own `ANTHROPIC_API_KEY`, under Anthropic's Commercial Terms and the data processing agreement that comes with them (Directive §8.7). It is the backend for the owner's instance and for any use that isn't individual and non-commercial.
- R15.4 Both backends expose the same interface:
  - Structured-output validation.
  - A prompt/model version record.
  - A cache keyed per ADR-006 (prompt id and version, redacted-input hash, output-schema hash, model, backend). Re-running an edited brief reuses every cached call whose key is unchanged (R18.4).
  - A cost/usage ledger: tokens and cost in `api` mode; session counts and limit hits in `subscription` mode.
- R15.5 Usage-limit handling in `subscription` mode: detect limit-reached responses, pause the run until the reset time, resume, and log the pause. Runs are resumable, so nothing is lost. Optional per-job overrides allow a heavy job to run on `api`.
- R15.6 (amended, Directive §6.1, ADR-064.1; supersedes ADR-023 for product calls) The `subscription` backend stays in the code **for other users**. Documentation states its scope: **individual, non-commercial use only**, on the operator's **own** Claude plan, through the **official** Claude Code CLI only (ADR-008). pigtail never pays for, resells or intermediates anyone's Claude usage. Any commercial use, or any deployment that serves other people, must use `api`. The install guide points readers to Anthropic's current terms (the Claude Code legal and compliance page) rather than restating them. Operators who use it must turn off model training on their Claude account (install-guide step).
- R15.7 Output parity check: a fixed evaluation set is run on both backends, and extraction agreement must be α ≥ 0.70 between them before a backend switch is accepted for coded data.
- R15.8 (new, Directive §6.3, ADR-064.3) **Model assignment per stage** (the owner's instance; configurable per instance):

  | Stage | Model |
  |---|---|
  | Relevance filter | `claude-haiku-4-5-20251001` |
  | Extraction and coding | `claude-sonnet-5` |
  | Synthesis, report and plan | `claude-opus-5-5` |

  All three were visible on the owner's key when checked on 2026-09-26 (ADR-064.3). Model IDs and prompt versions are logged with every output (R7.4). The single `LLM_MODEL` setting used so far is replaced by this per-stage assignment in M21.
- R15.9 (new, Directive §6.3, ADR-064.3) **Batch API and prompt caching.** Every stage that isn't time-sensitive runs through the Message Batches API; launch mode (R19.4) may use standard calls. The codebook and system prompts are sent with prompt caching.
- R15.10 (new, Directive §6.3, ADR-064.3) **Evidence trimming before a run.** Long threads are truncated, reposts are deduplicated, and only the relevant excerpts of an evidence item are sent to the model.
- R15.11 (new, Directive §6.4, ADR-064.4) **Budget caps.**
  - **Per-brief API cap:** each brief sets a hard API cap in its `budget` section. Suggested default: USD 150 (the value the owner set for her first full brief). The run hard-stops at the cap with a resumable checkpoint (R18.5).
  - **Monthly API cap:** USD 200 per month per instance (owner's value; configurable).
  - **Pilot projection:** after the first 5 pilot cases, the actual cost per case and a projection for the full brief go in `ops/COSTS.md` and `ops/STATUS.md` (for the owner's development instance). If the projection exceeds the cap, the run stops and H6 is raised.
  - **Other paid services: USD 0.** BigQuery stays within the free tier; Trendshift and X are off. Before any step that would cost money outside the API cap, pigtail shows the estimate and waits for approval (H6).
  - **Subscription share** applies only to the agents building pigtail (at most about 50% of the owner's weekly allowance; pause on limits and resume; never switch backends) (ADR-064.2, ADR-064.4).
  - Schema note: ADR-064.4 says `budget.money_usd` covers the API cap plus other paid services, while brief schema v1.1 carries a separate `budget.llm_api_usd`. Reconciling the field names is an M21 task.

### F16 Trends (part of the neighbourhood report, D2)
- R16.1 (amended) Within the brief's neighbourhood, compute rising channels, communities, asset formats and message patterns over the **last 3–6 months** against the rest of the brief's window, with n and the window shown on every claim.
- ~~R16.2 Saturation detection (depended on R8.4).~~ **Retired** (ADR-047.3).
- R16.3 Trends are labelled descriptive ("emerging signal") and are kept separate from patterns everywhere.

### F17 On-demand analyzer and tracking (deliverable D5; unchanged except where ADR-047/048 require)
- R17.1 Given any GitHub URL, open a case and run a staged analysis:
  - Stage 1: GitHub history (star-history daily net counts, releases, issues, PRs, contributors), outcomes and a benchmark, in ≤ 10 min.
  - Stage 2: an external evidence backfill (HN, Bluesky, registries, Wayback, as cleared and enabled), in ≤ 6 h.
  - Stage 3: coding and matching against the patterns of any brief the project falls in.
- R17.2 Current-state summary: momentum versus the repo's own baseline, burst/decay/stable status, and mentions from the last 7 days.
- R17.3 Coverage score per source, showing each source's coverage window and stating that ephemeral evidence from before tracking started may be missing.
- R17.4 (amended; ADR-048.2) "Track this project" runs a first capture within 1 h, then enrols the project in the batch schedule (weekly by default) and in launch mode (R19.4). The operator can stop tracking at any time.
- R17.5 (amended) Validation: on ≥ 5 deep-forensics cases from the pilot brief, the D5 reconstruction recovers ≥ 80% of the key events verified in the pilot (formerly "Tier 3 cases", which no longer exist).

### F18 Research brief (new core object; ADR-047.1–2)
- R18.1 (amended, Directive §2.1, ADR-060) A **brief** is a versioned YAML document with these fields (the directive's name first; the brief-schema v1.1 name in brackets where it differs):
  - `project`: a description of the user's project and its **target users**.
  - `field`: include and exclude boundaries (problem, technology, category, audience), seed keywords and topics.
  - `time_window` [`window`]: the time window for discovery and backfill, **default the last 12–18 months** (ADR-047.1).
  - `success`: one **primary dimension** (attention, adoption, community or business) plus **minimum thresholds** on the others; default adoption in the top quartile with attention and community at or above the median (Directive §3.1, ADR-061). Weights are available only as an advanced option (§5.3). Fallbacks per ADR-053.2.
  - `own_audience`: the user's own audience size **per channel**, or none.
  - `channels` and `geographies` [`geography`]: those of interest, and those excluded.
  - `panels` [`panel`, `distribution_exemplars`]: the field panel and the distribution-examples panel (ADR-057.1).
  - `reference_cases` [`field.reference_cases`]: named projects always studied, whatever their outcome (R4.11, ADR-057.4).
  - `sizes` [`panel.winners`, `panel.losers`]: default 20 winners and 20 losers for the field panel (range 15–25 each).
  - `budget`: the brief's API cap and other paid services (R15.11; Directive §6.4, ADR-064.4).
  - Metadata: brief id, version, created and edited times, schema version.
  - Missing fields get defaults and are listed (generically, never with their content) in `ops/HUMAN_INPUTS.md` for confirmation in the shortlist review; unresolved reference cases never block a run (Directive §4 generic rule, ADR-062).
- R18.2 A guided form at `/briefs` creates and edits briefs, with YAML import and export. Form and YAML are equivalent.
- R18.3 An install holds **several briefs**. Each is run, reported and versioned independently.
- R18.4 **Editing and re-running.** Every edit creates a new brief version; old versions are kept. A re-run reuses cached evidence, scores and LLM calls wherever their inputs are unchanged, and the report shows what was recomputed.
- R18.5 (amended, Directive §6.4, ADR-064.4) **Cost estimate and hard stop.** Before every run, **in both the CLI and the UI**, pigtail estimates the API cost (per stage and model, R15.8–R15.10) and any other paid cost (and, in `subscription` mode, the expected number of model calls) and shows it against the brief's API cap and the monthly cap (R15.11). The run starts only after the user confirms (or within a pre-confirmed ceiling for scheduled runs). During the run, spending is metered, and the run **hard-stops** at the cap, leaving a resumable checkpoint.
- R18.6 (amended, Directive §2.3, ADR-060) **Provenance.** Every report records the brief version, the shortlist decisions (R4.7), the data version, **the code commit**, and the codebook, prompt and model versions.
- R18.7 **LLM expansion.** From the project description, pigtail proposes the problem statement, users, keywords, topics and competitors. The user edits them, and the edited expansion is part of the brief version.
- R18.8 **Success definition semantics.** A candidate qualifies when it meets every minimum threshold; qualifying candidates are ranked on the primary dimension (or on the weighted composite when the advanced option is used). Percentiles use the brief's reference population (R3.4). A value tagged `unknown` never counts as meeting a threshold, and the report says how many candidates that affected.
- R18.9 (amended, ADR-071.3; supersedes ADR-055.1's location) **Where briefs live.** Briefs, their expansions and their shortlists are stored **outside git**, by default in `~/.pigtail/briefs` (configurable), and that directory is included in the encrypted backup. They are never in the public repo; the CI check that blocks committed brief files stays and is extended. The repo ships one synthetic example brief.

### F19 Batch runs, launch mode and operations (new; ADR-048)
- R19.1 (amended, Directive §5.1, ADR-063) `pigtail run --brief <id> [--incremental]` runs one brief's pipeline on the operator's machine; Docker runs only during a run. Runs are idempotent and resume from checkpoints after an interruption.
- R19.2 A brief's **initial run** is its own backfill over its window (12–18 months). ADR-047 replaces CR-001's global 24-month backfill with this.
- R19.3 **Refresh cadence:** every 7 days by default, configurable from 1 to 8 weeks, with a hard ceiling between runs derived from each source's history window in the source matrix (crates.io, npm, Docker Hub and the others; never more than 60 days). The derivation is documented (Directive §5.1, ADR-063).
- R19.4 (amended, ADR-070.3) **Tracked projects and launch mode.** Tracked projects (D5) refresh weekly by default. **Launch mode** refreshes daily for 14 days around a declared or detected launch, and every 3 h on launch day. It starts automatically when a tracked project bursts (R19.5). In launch mode pigtail also **polls each tracked repo's current star count every 3 h (hourly on launch day)**, building an intra-day star curve from that point on; such a curve can't be reconstructed afterwards. Launch-mode LLM calls may use standard (non-batch) calls (R15.9).
- R19.5 Burst detection for tracked projects uses the star-history series and the project's own baseline (the per-repo parts of ADR-032/037 that survive ADR-047.6).
- R19.6 **Run report and alerts.** Every run writes a run report (what ran, what changed, costs, gaps, errors). Alerts are written locally; a sanitized summary may be exported to the instance's ops log (for the owner's development instance, `ops/ALERTS.md`, ADR-033.4); e-mail is sent if configured.
- R19.7 **Scheduling and hosting.** The reference setup is a macOS machine with a launchd schedule; Docker runs only during runs. A server setup is optional, documented, and tested through backup and restore (ADR-048.6).
- R19.8 **Evidence-decay study** (run in the pilot): the share of key evidence still retrievable 1, 7 and 30 days after first capture. If more than 10% is lost at 7 days, the cadence for new breakouts is shortened (through an ADR) and the finding goes in the pilot report.
- R19.9 (new, Directive §8.2, ADR-066.2) **Snapshot retention and purge job.** Raw snapshots are stored locally, encrypted at rest (FileVault, or volume encryption on the server path), and kept only until the brief's report is final **plus 12 months**. A scheduled purge job then deletes them, keeps the coded facts and the content hash, and writes a log. Snapshot-or-drop applies at coding time; after the purge, the hash and the coded facts are the permanent record.
- R19.10 (new, Directive §5.2, ADR-063) **Cache purge.** Once the owner's first brief shortlist is final, collected data that no brief references is purged, and the purge is logged.

### F20 Neighbourhood patterns (new; replaces F9)
- R20.1 A **pattern** records: name, description, why people took part and shared, preconditions, required assets, typical sequence and timing, outcome dimensions affected, **n among the brief's winners and n among its matched losers** (the loser contrast), the supporting cases, the **counterexamples** (losers that showed it; winners that didn't), the reliability label of every coded field it rests on (R7.5), and the sensitivity flags of the cases involved (R4.9).
- R20.2 There is no promotion rule and no `promoted` status. A pattern is always shown with its n and loser contrast, in descriptive language; it is not called a validated mechanism. Where n is too small for the contrast to say anything, the report says "insufficient evidence in this neighbourhood".
- R20.3 Patterns belong to a brief version. A re-run records, per pattern, which cases were added or removed and how the contrast changed.
- R20.4 (new, ADR-057.2–3; Directive §11 acceptance) Patterns from the distribution-examples panel state their conditions (audience, timing, category hype, assets) and carry a **transferability label**: transferable, conditional or not transferable. Reports show **absolute numbers** (stars, downloads, contributors) next to each winner and loser class, per panel.

## 7. Data model (versioned JSON Schemas in `schemas/`)

Core entities: `brief` (id, version, fields per R18.1, expansion), `candidate` (brief version, discovery source, relevance verdict, reason, rubric version), `shortlist_decision` (brief version, candidate, decision, reason, reviewer role), `repo`, `case` (with its role in each brief: winner, loser or tracked), `evidence` (id, source, url, fetched_at, content_hash, snapshot_ref, reliability, terms_basis, retention_class, deletion_state), `event`, `actor` (amended, Directive §8.1, ADR-066.1, ADR-071.2: a **role and bucket** — maintainer, account by follower bucket, newsletter, community, organization — never a handle, personal name or pseudonym; organizations and projects may be named; carries an `automated account` flag plus the version of the bot rule that set it, since bot filtering runs in memory), `optout_fingerprint` (new, ADR-071.1: an HMAC of an opted-out person's identifier under a secret key stored apart from the data, used only to exclude and purge that person in future runs), `edge`, `asset`, `outcome_observation`, `pattern` (replaces `mechanism`), `pattern_support` (case ↔ pattern, direction, strength), `report` (brief version, shortlist decisions, data version, code commit, run ids; ADR-060), `prediction`, `codebook_version`, `run` (code, prompt, model and brief versions; checkpoint state; cost).
Requirements: every record carries `schema_version`; records are stored in a mergeable, diffable format (JSONL export alongside the database; ADR-042.4); every run is recorded so that it can be replayed.

## 8. Outcome model

### 8.1 Metrics

| Dimension | Metric | Primary source | Default tag |
|---|---|---|---|
| Attention | Star velocity (daily net; labelled "unfiltered, anomaly-checked", R3.3; intra-day only for launch-mode polling, R19.4) | GitHub star-history endpoint (ADR-032, ADR-070) | verified |
| | HN points, comments, front-page minutes | HN Algolia + own rank polling (ADR-040.2, ADR-041) | verified |
| | Bluesky reach | Bluesky API (ADR-022 holds) | verified |
| Adoption | Registry downloads | PyPI BigQuery, npm, crates.io, Homebrew, Docker Hub | verified (noisy) |
| | Dependents | deps.dev, GitHub dependency graph | verified |
| Community | Returning external contributors (ADR-016) | GitHub API (ADR-013) | verified |
| | External issue/PR activity | GitHub API (ADR-013) | verified |
| | Discord/Slack size | Invite APIs | estimated |
| Business | MRR | Operator-entered with a citation (ADR-021) | self_reported |
| | Funding | Press, public announcements | self_reported |
| | Paid offering or pricing page exists | Wayback snapshots (when cleared) | verified |
| | Hiring | Careers pages, HN Who's Hiring | verified |

Reddit reach is a documented gap (ADR-010); at most Reddit post metadata (link, title, score, timestamp) is used once cleared (R2.2; Directive §8.4, ADR-066.4). GH Archive is not a metric source (ADR-047.8).

### 8.2 Success definition (per brief; replaces the v1 global outcome classes)
- The v1 global classes (`winner`, `attention_only`, `short_lived`, `plateau`, `slow_riser`) and their calibration are retired as the way winners and losers are chosen (ADR-047.1–2, ADR-047.7).
- A brief's **winners** are the top-ranked candidates under its success definition (R18.8). Its **losers** are candidates from the same shortlist that had a comparable launch signal but didn't qualify, matched to the winners per R4.3.
- The **sensitivity check** (R4.9) is part of every report.

## 9. Evaluation and quality

### 9.1 System tests — **retired** (ADR-047.3, ADR-048.4)
The v1 forecasting, loser-value, generality and prospective tests are cut, and their pre-registrations are withdrawn by dated amendment (ADR-047.7). Launch-mode cases remain the prospective set for any later evaluation.

### 9.2 Quality rules for every neighbourhood report (amended)
- 100% of claims resolve to an evidence record with a valid hash.
- Extraction agreement is reported per field (Krippendorff's α). Findings resting on a field with α < 0.70 are labelled "low reliability", not dropped (ADR-047.7). If a human calibration sample exists, LLM–human α is reported the same way.
- Relevance-filter precision is logged per brief version; target ≥ 80% (R4.7).
- Matched-set balance (ADR-054): exact match on founder audience bucket and launch half-year. For every other characteristic the standardized mean difference < 0.25 is a **target**, not a gate: balance is always shown per characteristic, and loser contrasts that depend on a characteristic missing the target are labelled. A pair that differs by > 0.5 on any characteristic is excluded from the headline patterns and shown only in the case-level view.
- The sensitivity check of the winner set is present, and affected cases are flagged (R4.9).
- Distribution-examples patterns carry transferability labels, and absolute numbers are shown per class and panel (R20.4).
- Star metrics carry "unfiltered, anomaly-checked" and star-based attributions carry "day-level" where applicable (R3.3, R5.5; ADR-070).

### 9.3 Mechanism promotion rule — **retired** (ADR-047.3)
Replaced by the pattern reporting rule in R20.2.

### 9.4 The pilot (new; ADR-047.10)
The first end-to-end neighbourhood report on the owner's project is the pilot. It exercises the full flow (F18, F4, F5, F7, F8, F16, F20), reports per-field agreement with the "low reliability" label, the relevance-filter precision from the shortlist review, the sensitivity check and the evidence-decay study (R19.8). Its analyses are pre-registered before the outcome sort (WORK_ORDER §6).
Per Directive §11 and ADR-069 it runs in two steps: a **pilot of the first 5 cases** end to end with double coding, followed by the cost report and projection (R15.11) and the evidence-decay measurement (WORK_ORDER M23); then the **full brief #1 run** within the cap (M24).

## 10. Non-functional requirements

- **Self-hosting, per user:** each user runs their own instance. The reference setup is a macOS machine with Docker (started only for runs) and a launchd schedule; `docker compose up` brings up the stack. A server setup (single VM, S3-compatible object storage) is optional and documented. The owner's instance uses EU or Swiss hosting for any remote storage.
- **Disk encryption:** FileVault (or, on the server path, volume encryption) is **required** on any machine that holds an instance's data (ADR-048.5). `pigtail doctor` reports it.
- **Reproducibility:** pinned dependencies, versioned briefs, data versions and reports, and a fixed seed for any sampling.
- **Cost control:** a cost estimate before every run in the CLI and UI, the per-brief API cap and the monthly API cap with hard stops (R15.11, R18.5), USD 0 for other paid services unless approved (H6), per-source rate budgets, and a running cost ledger (Directive §6.4, ADR-064.4).
- **Privacy and compliance** (GDPR and Swiss FADP; Directive §8, ADR-066, ADR-071). These protections are **on by default** for every user (Directive §8.8):
  - Each user is the controller of their own instance. The compliance pack in `docs/compliance/` becomes a **template** each user adopts; the owner's completed pack is the first filled-in copy, kept outside git or with personal details removed (Directive §9, ADR-067).
  - **Code, then discard identities** (§8.1, ADR-066.1): coded data holds roles and buckets, never handles or personal names (R5.3, §7). The pseudonymized-handle scheme is replaced; existing data is migrated and the handles and pseudonyms purged. The only person-derived value kept is the **opt-out HMAC fingerprint** (secret key stored apart from the data), used only to exclude and purge an opted-out person in future runs and described in the privacy notice (ADR-071.1).
  - **Bot filtering runs in memory**; its outcome is stored on the coded record ("automated account" plus the rule version), so results stay reproducible without the handle (ADR-071.2).
  - Identifier redaction before every model call (ADR-006) stays.
  - **Time-limited snapshots** (§8.2, ADR-066.2): raw snapshots are kept until the brief's report is final **plus 12 months**, encrypted at rest, then purged by a logged job (R19.9). This replaces the earlier 24-month rule for raw person-level data. Project-level data has no time limit.
  - **Minimal collection** (§8.3, ADR-066.3): mentions of shortlisted projects only; no sweeps of users or accounts; no follower lists.
  - **Sources** (§8.4, ADR-066.4): official APIs only, rate limits respected with margin, deletions honoured; Reddit metadata only until API approval; Trendshift off by default; no scraping around authentication, paywalls or terms.
  - **Outputs** (§8.5, ADR-066.5): snapshots are never reproduced; at most one short attributed excerpt per source (R7.6).
  - **Publishing disabled** (§8.6, ADR-066.6; H4): no findings, data or reports leave the owner's instance. The CI private-data scan stays.
  - **Purpose limitation:** collection is scoped to briefs and tracked projects. Data collected before the re-scope is a cache that briefs may reuse; once the first brief's shortlist is final, anything no brief references is deleted and the purge is logged (ADR-047.6, R19.10).
  - Deletion sync (R1.5), opt-outs, data-subject requests and the interim holds of ADR-022 (as amended) stay in force.
  - **Privacy notice** (ADR-071.4): the owner's notice is published in a **separate repo on GitHub Pages**, because it describes her instance, not the tool. It names the controller (the owner), a dedicated contact alias (not a personal e-mail), purpose, sources, what is and isn't stored (roles and buckets, no handles), retention (R19.9), legal basis, how to opt out or object, and US processing. The draft is shown to the owner before publication (H5). The pigtail repo keeps the generic template.
  - **LLM processing** (§8.7, ADR-066.7): the owner's instance uses `api` mode only, relying on the data processing agreement that comes with Anthropic's Commercial Terms. **Inference happens in the US**, and the compliance docs record this. Other users who choose `subscription` mode (individual, non-commercial use, R15.6) must turn off model training on their Claude account (install-guide step); redaction still runs before every call.
- **Responsible use** (Directive §8.8, ADR-066.8): the README and the operator guide carry a "Responsible use" section: each user is the controller of their own instance, must respect platform terms, and should adapt the compliance template. pigtail ships no data and no default targets. The MIT licence is unchanged.
- **Terms:** each connector documents the terms it relies on.
- **Security:** secrets are kept only in environment variables or a secrets manager, never in the repo. Private data is never pushed to the public repository; a CI check scans for this. Backups are encrypted and go to external or private storage (ADR-044, ADR-048.5).
- **Observability:** run reports, collector health, gaps, error rates and costs are visible in the UI (`/admin`) and in each run report.
- **Public repository:** https://github.com/suchipizza/pigtail.
  - Git contains only code, docs, templates, schemas, the codebook, the synthetic example brief and synthetic fixtures, pre-registrations and ops logs (Directive §8.6).
  - **Brief content never goes into git**, ops logs included (ADR-055.1, ADR-071.3).
  - A CI private-data scan blocks any snapshot, raw record, handle, secret or user brief.
  - Ops logs must never contain personal data or secrets.
- **Licensing:** MIT for code (already in the repo). The codebook and methodology are MIT too, unless the owner chooses CC BY 4.0.

## 11. Release criteria (v1)
- The pilot report is complete and verified (§9.4), including relevance precision, per-field agreement, the sensitivity check and the evidence-decay study.
- D1, D2, D3, D5, D6 and D7 pass their acceptance criteria in `docs/DELIVERABLES.md`.
- A new user can install pigtail and start a first brief in under an hour following the install guide, with credentials setup, cost expectations and the example brief (D6).
- Documentation is complete: install guide, operator guide (Mac and optional server), brief guide, methodology paper, codebook, schema reference, limitations, and the compliance template.
- Three consecutive scheduled runs succeed with alerts working (ADR-048.4).
- Owner brief #1 completes within its API cap, with a report that shows per-field α, loser contrasts, absolute numbers and transferability labels, and a D3 plan exists for the owner's project (Directive §11 acceptance, ADR-069).
- No handles remain in the stored data, and the global-collection code is removed with the archive tag in place (Directive §11 acceptance).
- The narrowed legal consult (H2, `docs/compliance/LEGAL_REVIEW_H2.md`) blocks **publishing only**; it doesn't block collection under the mitigations of §10 (Directive §9, ADR-067). Its answers, when they arrive, are applied to the owner's instance and to the template.
- **Publishing findings is disabled** (H4; Directive §8.6, §10). The code is public from the start; tagging a code release publishes no findings, data or reports.
- pigtail's own launch is tracked in launch mode, with predictions locked before launch.

## 12. Defaults assumed (agents may revise them with an ADR)
- LLM backend (amended, Directive §6.1, ADR-064.1): `api` for the owner's instance and in the shipped `.env.example`; `subscription` (the operator's own Claude plan via the official Claude Code CLI) remains available for individual, non-commercial use (R15.6). Models per stage as R15.8.
- Budget (Directive §6.4, ADR-064.4): per-brief API cap suggested at USD 150; monthly API cap USD 200; other paid services USD 0.
- Briefs directory: `~/.pigtail/briefs` (ADR-071.3).
- Stack: Python 3.12 + uv, Postgres, S3-compatible object storage, DuckDB for analytics, FastAPI, TypeScript/React UI, Cytoscape.js for graphs.
- Brief defaults: window 12–18 months; 20 winners and 20 losers (range 15–25 each); success definition = one primary dimension plus minimum thresholds (weights only as an advanced option).
- Refresh every 7 days; tracked projects weekly; launch mode daily for 14 days and every 3 h on launch day, with star-count polling every 3 h (hourly on launch day) (ADR-070.3).
- Results per dimension; a composite only to rank candidates within a brief (ADR-047.2, amending ADR-000's "no composite outcome score").
- The v1 default "the capture layer is exempt from the pilot gate" no longer applies: there is no continuous capture and no pilot gate (ADR-047, ADR-048).
