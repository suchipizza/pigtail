# pigtail — Deliverables

Version 2.1 · This document defines what each user receives, starting with the owner, who is pigtail's first user. `PRD.md` specifies the requirements behind each deliverable (R-IDs), and `WORK_ORDER.md` schedules them. A deliverable is done only when every acceptance criterion below passes and the `verifier` agent has signed it off.

Repository: https://github.com/suchipizza/pigtail (public, MIT). Code, docs, methodology and a synthetic example brief live there. Briefs, raw data, snapshots, reports and person-level graphs never do: pigtail ships a method, not data, and each user's instance keeps its own.

Binding source for v2.1: `ops/OWNER_DIRECTIVE_001.md` (Owner Directive 001, public copy) and ADR-059 to ADR-073 in `ops/DECISIONS.md`. ADR-070 and ADR-071 are the owner's later decisions and override the directive where they differ; ADR-072 and ADR-073 record how it is applied (reports stay private: ADR-073.1).

## Change history

| Version | Date | Change |
|---|---|---|
| 2.1 | 2026-09-26 | Owner Directive 001 applied (Directive §0.3). Shared rules: roles and buckets instead of pseudonyms, one short excerpt per source, publishing and public mode disabled (Directive §8.1, §8.5, §8.6; ADR-066). D7: cost estimate before every run in UI **and** CLI against the per-brief and monthly API caps; briefs stored in `~/.pigtail/briefs` (Directive §6.4, ADR-064.4, ADR-071.3). D1: daily star-history counts labelled "unfiltered, anomaly-checked"; day-level attribution labels; hourly star lanes only for launch-mode cases; spread graph of roles and buckets (ADR-070, ADR-066.1). D2: per brief, with per-field α, sensitivity check, transferability labels, absolute numbers, code commit; public mode removed (Directive §7, §11; ADR-057, ADR-060, ADR-065). D5: launch-mode star polling (ADR-070.3). D6: product calls on `api`, subscription documented for individual non-commercial use; model assignment; "Responsible use" section; reports record the code commit (Directive §6, §8.8; ADR-064, ADR-066.8). Milestone column renumbered to M20–M28 (ADR-069; WORK_ORDER §4.5). M20 verifier fixes: D6 run command `pigtail run --brief <id> [--incremental]` (Directive §5.1); reports stay private, no public pilot or final report (Directive §8.6, ADR-073.1). |
| 2.0 | 2026-09-26 | Re-scoped per ADR-047 (CR-002: brief-driven neighbourhood analysis) and ADR-048 (CR-001: batch runs and launch mode). New D7 `/briefs`. D1 updates at every run. D2 becomes the per-brief neighbourhood report. D3 builds plans from that report. D5 keeps its scope and gains launch mode. D6 acceptance becomes "a new user runs a first brief in under an hour". Criteria that depended on the global library, tiers or continuous capture are removed. |
| 1.0 | 2026-09-25 | Global collection and mechanism-library scope. The full text is in git history (for example `git show archive/global-collection:docs/DELIVERABLES.md`). |

## Overview

| ID | Deliverable | Page / surface | Phase | Earliest milestone (WORK_ORDER §4.5, ADR-069) |
|---|---|---|---|---|
| D7 | Research Briefs | `/briefs` | v1 | Core after M12 (accepted); discovery, relevance filter and shortlist review in M22 |
| D1 | Forensics Explorer | `/cases`, `/cases/:id` | v1 | Preview built in M1; updates per run from M22; full in M23–M24 |
| D2 | Neighbourhood Report (per brief) | `/insights` | v1 | M23 (pilot, first 5 cases), M24 (full brief #1) |
| D3 | Plan Generator | `/plan` | v1 | M25 |
| D4 | Execution Engine | `/execute` | **v2** | M28 |
| D5 | Project Analyzer & Tracker (with launch mode) | `/analyze` | v1 | M26 (launch mode and tracking, before the owner's first launch) |
| D6 | Platform, install and operations | CLI, API, `/admin`, `/settings`, docs | v1 | Throughout; cost estimator in M21; install acceptance in M27 |

**Shared rules for every page.**
- Every claim, number and event is traceable to evidence: one click opens the evidence record and its snapshot.
- People appear only as **roles and buckets** (maintainer, account by follower bucket, newsletter, community, organization). Handles and personal names are never stored or shown; organizations and projects may be named (Directive §8.1, ADR-066.1).
- Snapshots are never reproduced: a page, report or export shows at most **one short attributed excerpt per source**; the full snapshot opens only inside the private instance (Directive §8.5, ADR-066.5).
- Every brief-derived view names the brief and brief version it comes from.
- The web app is **private**: it sits behind operator login, because it shows private data. **Publishing is disabled** on the owner's instance: no findings, data or reports leave it, and there is no public mode (Directive §8.6, ADR-066.6, H4). The code may keep a public-mode option for other users, off by default.
- Nothing on any page is specific to the owner. Everything field-specific comes from the user's brief.

---

## D7 — Research Briefs (new)

*Describe your project and what success means to you; pigtail finds and checks your neighbourhood.*

**`/briefs` — brief list**
- Every brief in the install, with its current version, last run, next scheduled run, status (draft, running, awaiting shortlist review, report ready, over budget, failed) and spend against budget.
- Create a brief from the guided form, import one from YAML, or duplicate an existing one.

**`/briefs/:id` — brief editor**
- Guided form with the fields of PRD R18.1: project and target users; field boundaries (include/exclude); time window (default 12–18 months); success definition (one primary dimension plus minimum thresholds; weights only under "advanced"); own audience size per channel; channels and geographies (of interest and excluded); panels (field panel and distribution examples); reference cases; number of winners and losers (default 20/20, range 15–25); budget (the brief's API cap, suggested default USD 150, and other paid services, default USD 0) (Directive §2.1, §6.4; ADR-060, ADR-064.4).
- **LLM expansion:** proposed problem statement, users, keywords, topics and competitors, each editable before the brief is saved (R18.7).
- YAML view and export. The form and YAML are equivalent.
- **Versions:** every save creates a new version; a diff view compares any two versions.
- **Run:** shows the cost estimate (API cost per stage and model; any other paid cost; model calls in `subscription` mode) against the brief's API cap and the monthly cap, and starts the run only after confirmation (R18.5, R15.11). The same estimate is printed by the CLI before `pigtail run` (Directive §6.4, ADR-064.4). Re-runs show which cached results will be reused.

**`/briefs/:id/shortlist` — shortlist review**
- Every candidate with its discovery source, the relevance filter's verdict and logged reason, and the rubric version.
- Accept, reject or add candidates, each with a reason; every decision is logged with the brief version (R4.7).
- Relevance-filter precision for this brief version, against the ≥ 80% target.
- Brief fields that were filled with defaults, and unresolved reference cases, are listed here for the user to confirm (Directive §4 generic rule, ADR-062).
- After the review: the outcome sort, the selected winners and matched losers, balance diagnostics and the sensitivity check (R4.8, R4.9, R4.3).

**Acceptance**
- A brief can be created from the form and from YAML, and the two produce the same stored brief. Invalid briefs (for example, 30 winners, or no primary dimension) are rejected with a message naming the field.
- Every edit creates a new version; old versions stay readable and diffable; reports name the version they came from.
- An install holds at least two briefs that run and report independently.
- A cost estimate is shown before every run, in the UI and in the CLI (Directive §6.4). A run given a budget below its estimate stops at the budget with a resumable checkpoint and says so in its run report (tested).
- Re-running an unchanged brief makes no new LLM calls for cached items, and re-running an edited brief recomputes only what the edit affects; the run report lists both.
- Every candidate has a logged reason; every shortlist decision is logged with reason and brief version; precision is computed and shown.
- Winner and loser selection is deterministic for a given brief version and data version.
- Briefs are stored only outside git, by default in `~/.pigtail/briefs`, and that directory is included in the encrypted backup; the private-data scan blocks a brief file in git (ADR-071.3).

---

## D1 — Forensics Explorer

*Watch the growth and distribution timeline of every case in your briefs and every project you track.*

**`/cases` — case feed**
- Lists every case: each brief's winners and matched losers, and projects tracked through D5.
- Filters: brief, role (winner, loser, tracked), status (launch mode, tracked, closed), dimension, date range. Sort by recency, velocity or outcome.
- A "Launch mode" strip shows tracked projects currently in launch mode, with velocity and the triggers detected so far.

**`/cases/:id` — case view**
- **Braided timeline:** time-aligned lanes for GitHub (daily net stars from star-history, labelled "unfiltered, anomaly-checked" with anomaly flags shown on the lane (ADR-070.1, ADR-070.4); forks, releases, contributors), Hacker News, Bluesky, blogs and newsletters, package downloads and business signals, plus any other cleared source.
  - Events are annotated `prep | launch | burst | quiet | pivot | relaunch`.
  - Each burst is linked to its ranked candidate triggers, with confidence levels. Attributions that rely on daily star data carry the **"day-level"** label (ADR-070.2).
  - Zoom from full history down to the finest resolution each lane has: days for stars, except that **hourly star lanes exist only for launch-mode cases**, from the moment launch-mode polling started (ADR-070.3).
- **Spread graph:** which roles and buckets published and redistributed (maintainer, account by follower bucket, newsletter, community, organization), and which edges the evidence supports. Edges are styled by evidence level, and each node opens its evidence. No node shows a handle or personal name (Directive §8.1, ADR-066.1).
- **Asset gallery:** the images, GIFs, demos, titles, phrases, benchmark claims and links that spread, grouped by asset category, each with its reach.
- **Evidence inventory:** a sortable table of every item with source, date, reliability, snapshot link and retention state.
- **Outcomes panel:** attention, adoption, community and business at T+7, T+30, T+90 and T+365, each with a verification tag, plus the case's role and rank in each brief it belongs to.
- **Patterns observed:** the neighbourhood patterns this case supports or contradicts, per brief.
- **Compare:** side by side with the case's matched loser (or winner), or with any other case.

**Acceptance**
- Every winner and loser case of a brief with a finished run renders all tabs, with no empty lane that isn't explained (for example "source not enabled", "gap in the source matrix" or "no coverage before tracking started").
- Clicking a random sample of 50 claims resolves each one to a valid snapshot 100% of the time.
- Cases update at every run; launch-mode cases update on their own schedule (ADR-048.4). Each case shows the time of its last update and the run (with its code commit) that produced it.
- Hourly star lanes appear only on launch-mode cases; every other star lane is daily, and star-based attributions carry the "day-level" label (ADR-070).
- No stored or displayed node, event or evidence row contains a handle or personal name (verifier search; Directive §8.1, §11 acceptance).
- The case page loads in ≤ 2 s at p95 for a case with 5,000 evidence items.
- Preview (built in M1, see WORK_ORDER): the timeline and evidence tabs work on uncoded captured data and are labelled "uncoded preview".

---

## D2 — Neighbourhood Report (per brief)

*What worked in your neighbourhood against comparable projects that didn't make it, and what's trending there now.*

One report **per brief** version and data version, at `/insights` (select the brief). It stays inside the owner's instance: publishing is disabled (Directive §8.6). The page has two sections that are **visibly separated**, because they make different kinds of claims.

**Header (provenance)**
- Brief name and version; data version; **code commit**; codebook, prompt and model versions (per stage, R15.8); generation date (Directive §2.3, ADR-060).
- The success definition used, the reference population's n, and the number of winners and matched losers.
- The shortlist record: candidates found, filter-accepted, reviewer-accepted, rejected and added; relevance-filter precision against the ≥ 80% target.
- The sensitivity check: how much the winner set changes under alternative success definitions, and which cases are flagged (Directive §3.2, ADR-061).
- **Absolute numbers:** stars, downloads and contributors next to each winner and loser class, per panel, so the scale of "winning" is visible (ADR-057.3).

**What worked against losers** (field panel; core-field findings reported separately, R4.10)
- **Neighbourhood patterns.** Each shows: description, why people took part and shared, preconditions, required assets, typical sequence and timing, outcome dimensions affected, **n among winners and n among matched losers**, the supporting cases, the **counterexamples**, reliability labels, and sensitivity flags on the cases involved. Every case listed links to D1. No pattern is called "validated" or "promoted".
- **Channel and timing analyses:** event-study charts (for example, star velocity around a front-page HN appearance), and how channels were sequenced among winners versus losers.
- **Asset and message analyses:** which asset types and framing patterns appeared more often among winners than among their matched losers, with n on both sides.
- "Insufficient evidence in this neighbourhood" wherever n is too small to say anything.

**Distribution lessons** (distribution-examples panel; ADR-056, ADR-057)
- Patterns from the examples panel, each stating its conditions (audience, timing, category hype, assets) and a **transferability label**: transferable, conditional or not transferable (ADR-057.2). Kept out of the field panel's headline patterns.

**What's trending** (descriptive, the last 3–6 months; labelled "emerging signal")
- Rising channels, communities, asset formats and message patterns in the neighbourhood over the last 3–6 months, compared with the rest of the brief's window.
- Recent breakouts among the brief's cases and tracked projects, and the triggers detected for them, linked to D1.

**Methods and data quality panel:** per-field agreement (Krippendorff's α) with "low reliability" labels for fields below 0.70, LLM–human agreement if the owner coded a calibration sample (H3) or else "LLM-coded, not human-validated" (Directive §7, ADR-065), star metrics labelled "unfiltered, anomaly-checked" (ADR-070.4), balance diagnostics per matching covariate, coverage per source, evidence counts, costs of the run, and known limitations.

**Acceptance**
- Every pattern shows n among winners, n among matched losers and its counterexamples ("none found" is stated explicitly). No pattern is shown without them.
- Agreement is shown per field, and every finding that rests on a field with α < 0.70 carries the "low reliability" label (ADR-047.7).
- The sensitivity check is present, and every affected case is flagged wherever it appears in the report.
- Distribution-examples patterns carry transferability labels, and absolute numbers are shown per class and panel (ADR-057; Directive §11 acceptance).
- The report records the brief version, the shortlist decisions, the data version and the code commit (R18.6; ADR-060), and the same brief version plus data version regenerate the same report from the cache.
- Trend claims show their window, their baseline and n. Nothing in "What's trending" is presented as a pattern.
- The report regenerates after each run of its brief. Every chart shows the data version and the date it was generated.
- The report can be exported as Markdown to the instance's private storage. It contains at most one short attributed excerpt per source (Directive §8.5).
- Nothing is published: there is no public mode on the owner's instance (Directive §8.6, H4).

---

## D3 — Plan Generator

*Turn your neighbourhood report into a growth and distribution plan.*

**Input**
- A brief and a version of its neighbourhood report. The profile (category, stage, audience per channel, business model, target users, geography/language, channels to avoid, success definition) comes from the brief; available assets, time budget and launch window are added here. A GitHub URL auto-fills what D5 can see.

**Output**
1. **Similar projects:** the brief's nearest winners **and** their matched losers, each with why it's similar and a link to its D1 timeline.
2. **Recommended patterns:** only patterns from the report, ranked for this profile and success definition. Each shows n among winners and losers, counterexamples, reliability labels, preconditions met or unmet, and the evidence behind it.
3. **Trending opportunities:** emerging signals from the report, kept visibly separate and labelled experimental.
4. **Readiness gaps:** what to fix before launch, such as README, demo, install time-to-first-success, docs or pricing page.
5. **Asset checklist:** the assets to produce, with examples from the brief's asset galleries.
6. **Sequenced plan:** a calendar covering pre-launch, launch day and the post-launch weeks, with channel order, spacing and timing windows.
7. **Predictions:** the expected outcome ranges, plus a "lock predictions" action that pre-registers them (PRD F11) for the launch followed in launch mode.
8. **Adaptation notes:** how to test each pattern, and what result would falsify it.
9. **Export** as Markdown or PDF. Saved plans are versioned and can be regenerated when the brief or report changes.

When the report has too little evidence for the profile, the plan says "insufficient evidence in this neighbourhood" and does not extrapolate.

**Acceptance**
- Produces plans from the pilot brief's report and from the example brief's report (D6), including correct "insufficient evidence" responses.
- Every recommendation cites its pattern and ≥ 3 cases, and shows the pattern's n, loser contrast and reliability labels.
- A plan generates in ≤ 2 min once the report exists.
- Plans are reproducible: the same brief version, report version and plan inputs give the same plan.

---

## D4 — Execution Engine (v2, next phase)

*Executes the plan from D3, with a human approving every external action.*

- **Asset drafting:** launch posts per channel, titles, README improvements, demo scripts and comparison tables, generated from the plan's patterns and the project's own material.
- **Channel schedule and approval queue:** each post is drafted, scheduled and then **approved by a human** before anything is published. Posting happens only after that approval, through official APIs, or through copy-and-open where no API exists.
- **Live monitoring:** the project automatically becomes a tracked D5 case in launch mode, and the plan is compared with what actually happens.
- **Alerts:** when to amplify, hold, relaunch or pivot.
- **Closing the loop:** outcomes are scored against the locked predictions, and the project's case feeds the next run of its brief.

**Acceptance (for v2)**
- Runs a full plan for 1 real launch end to end, with 0 unapproved external actions.
- The prediction scorecard is produced automatically at T+30.
- Every generated asset links to the pattern it implements.

---

## D5 — Project Analyzer & Tracker (with launch mode)

*Enter any GitHub project and see what has been done and what is still going on. Analyse it once, or keep tracking it.*

**Input:** a GitHub URL. Options: a one-time analysis, or **Track this project**, which enrols it in the batch schedule and makes it a D1 case that updates at every run.

**Output**
- **What has been done (reconstruction):**
  - A backfilled timeline built from the GitHub API (star-history daily net counts, releases, issues, PRs, contributors), HN and Bluesky search (as enabled), registry histories and Wayback snapshots of project pages (off by default; when enabled, ADR-072.7), rendered in the D1 case view.
  - Detected launches, bursts and their triggers.
  - The patterns observed, matched against the reports of any brief the project falls in.
- **What is going on now:**
  - Current momentum versus the project's own baseline, and whether it is in a burst, decaying or stable.
  - Active discussions and mentions from the last 7 days.
  - Upcoming signals, such as an announced release or a launch-week post.
- **Benchmark:** position against similar projects at the same age, with trajectory bands drawn from a relevant brief's winners and losers when one exists.
- **Gaps and opportunities:** patterns that a relevant brief's winners showed and this project hasn't, plus trending signals. A link sends the profile into D3.
- **Coverage score:** how complete the reconstruction is. The score states plainly that deleted or ephemeral evidence before tracking started may be missing, and shows each source's coverage window.

**Tracking mode (ADR-048.2):** a tracked project refreshes weekly by default. **Launch mode** refreshes daily for 14 days around a declared or detected launch and every 3 h on launch day; it starts automatically when a tracked project bursts, or when the user declares a launch date. In launch mode the current star count is also polled every 3 h (hourly on launch day), which is the only source of D1's hourly star lanes (ADR-070.3). Alerts fire on bursts, new mentions above a reach threshold, and momentum decay. The operator can stop tracking at any time.

**Acceptance**
- First results (GitHub timeline, outcomes, benchmark) in ≤ 10 min. The full external evidence backfill completes in ≤ 6 h for a typical repo.
- Works for repos of any size and age, including ones that never broke out, and shows a coverage score in every case.
- Tracking a project runs its first capture within 1 h, and the project appears in the D1 feed.
- Launch mode: a declared launch date, and a simulated burst on a tracked project, each switch the project to the launch-mode schedule (daily for 14 days, every 3 h on launch day, with star polling every 3 h and hourly on launch day), and D1 shows updates on that schedule.
- The reconstruction is validated against ≥ 5 deep-forensics cases from the pilot brief: it must recover ≥ 80% of their verified key events.

---

## D6 — Platform, install and operations

Required for everything above, and delivered in v1.

- **Data engine:** connectors, snapshot store, pipeline, briefs, reports and versioned data, all private to the instance. Data collected earlier is a cache that briefs reuse.
- **Batch operations (ADR-048; Directive §5.1, ADR-063):** `pigtail run --brief <id> [--incremental]`, idempotent and resumable from checkpoints; refresh every 7 days by default (1–8 weeks, never more than 60 days); a launchd schedule on macOS, with Docker running only during runs; a run report after every run; alerts written locally, with a sanitized export and e-mail if configured; encrypted backups to external or private storage. A server setup is optional, documented and tested through backup and restore.
- **LLM backend switch (`/settings` and `LLM_BACKEND` env):** `api` (the operator's own key under Anthropic's Commercial Terms; used for every product call on the owner's instance and set in `.env.example`) or `subscription` (the operator's own Claude plan through the official CLI, for individual, non-commercial use only) (Directive §6.1, ADR-064.1). Models per stage, the Batch API and prompt caching as PRD R15.8–R15.10. The switch changes nothing else in the product. Usage, cost and rate-limit state per backend are shown in `/admin`. See PRD F15.
- **Cost estimate before every run** in the UI and the CLI, against the per-brief and monthly API caps (PRD R15.11, R18.5; Directive §6.4).
- **`/admin`:** run history and run reports, collector health, gaps, error rates, costs and usage per brief, the review queue, and the retention/deletion-sync log.
- **CLI and API:** every pipeline stage and every page's data is available through the CLI and a read-only API.
- **Documentation:** install guide (new users), brief guide, operator guide (macOS reference setup and optional server) with a **"Responsible use"** section (also in the README: each user is the controller of their own instance, must respect platform terms and should adapt the compliance template; Directive §8.8, ADR-066.8), developer guide, methodology paper, codebook, schema reference, limitations, and the compliance template each user adopts as controller of their own instance.
- **Example brief:** a synthetic brief in the repo that a new user can run as-is or copy.
- **Reports:** the public repo holds the method documents (literature review, source matrix, codebook, specs, templates, guides). Pilot, brief and final reports, cost reports with case detail and evidence-decay results are stored in the instance's private data directory and included in its encrypted backup; they are never committed (Directive §8.6, ADR-073.1). Every report records the code commit (and, where applicable, the brief version and data version) (ADR-060).
- **Open-source release:** the public repo contains code, codebook, schemas, UI, docs, the example brief and the compliance template. No data. **Publishing the owner's findings is disabled** (H4; Directive §8.6, ADR-066.6): no pilot or final report is published; `docs/reports/` keeps only the counts-only data inventory and method notes without case content, and the private-data scan stays in CI (ADR-073.1).

**Acceptance**
- **New-user install in under an hour.** On a fresh macOS user account (and on the documented server path), someone who hasn't used pigtail, following only the install guide, within 60 minutes: installs the prerequisites; sets up **their own** credentials (GitHub token, an Anthropic API key or, for individual non-commercial use, a Claude login; optional BigQuery project) with FileVault/disk encryption checked by `pigtail doctor`; reads the guide's cost expectations; loads the example brief (or writes their own); sees its cost estimate; and starts its first run, which then continues unattended. The test is timed and logged.
- The install guide states expected costs and run times for the example brief, measured on a real run, not estimated.
- Three consecutive scheduled runs succeed, with alerts working (a deliberately broken run raises an alert) (ADR-048.4).
- An interrupted run resumes from its checkpoint and produces the same records as an uninterrupted one; replay from snapshots reproduces the stored records.
- Restore from an encrypted backup onto a second machine (or the server path) brings up a working instance with deletions and opt-outs re-applied (ADR-044).
- Switching `LLM_BACKEND` needs a restart and no other change.
- Nothing in the code, defaults, prompts or fixtures names the owner or her project (checked by the verifier with a search of the repo).
