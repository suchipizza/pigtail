# pigtail — Deliverables

Version 2.0 · This document defines what each user receives, starting with the owner, who is pigtail's first user. `PRD.md` specifies the requirements behind each deliverable (R-IDs), and `WORK_ORDER.md` schedules them. A deliverable is done only when every acceptance criterion below passes and the `verifier` agent has signed it off.

Repository: https://github.com/suchipizza/pigtail (public, MIT). Code, docs, methodology and a synthetic example brief live there. Briefs, raw data, snapshots, reports and person-level graphs never do: pigtail ships a method, not data, and each user's instance keeps its own.

## Change history

| Version | Date | Change |
|---|---|---|
| 2.0 | 2026-09-26 | Re-scoped per ADR-047 (CR-002: brief-driven neighbourhood analysis) and ADR-048 (CR-001: batch runs and launch mode). New D7 `/briefs`. D1 updates at every run. D2 becomes the per-brief neighbourhood report. D3 builds plans from that report. D5 keeps its scope and gains launch mode. D6 acceptance becomes "a new user runs a first brief in under an hour". Criteria that depended on the global library, tiers or continuous capture are removed. |
| 1.0 | 2026-09-25 | Global collection and mechanism-library scope. The full text is in git history (for example `git show archive/global-collection:docs/DELIVERABLES.md`). |

## Overview

| ID | Deliverable | Page / surface | Phase | Earliest milestone (WORK_ORDER §4) |
|---|---|---|---|---|
| D7 | Research Briefs | `/briefs` | v1 | Core after M12, shortlist review after M13 |
| D1 | Forensics Explorer | `/cases`, `/cases/:id` | v1 | Preview built in M1; updates per run after M14; full in M15 |
| D2 | Neighbourhood Report (per brief) | `/insights` | v1 | M15 (the pilot) |
| D3 | Plan Generator | `/plan` | v1 | M16 |
| D4 | Execution Engine | `/execute` | **v2** | M19 |
| D5 | Project Analyzer & Tracker (with launch mode) | `/analyze` | v1 | Tracking and launch mode in M14; full in M17 |
| D6 | Platform, install and operations | CLI, API, `/admin`, `/settings`, docs | v1 | Throughout; install acceptance in M18 |

**Shared rules for every page.**
- Every claim, number and event is traceable to evidence: one click opens the evidence record and its snapshot.
- People appear only as pseudonyms, except for public figures when the codebook allows it.
- Every brief-derived view names the brief and brief version it comes from.
- The web app is **private by default**: it sits behind operator login, because it shows private data. An optional **public mode** exposes only aggregate, anonymized content from a neighbourhood report, after the user's own legal check.
- Nothing on any page is specific to the owner. Everything field-specific comes from the user's brief.

---

## D7 — Research Briefs (new)

*Describe your project and what success means to you; pigtail finds and checks your neighbourhood.*

**`/briefs` — brief list**
- Every brief in the install, with its current version, last run, next scheduled run, status (draft, running, awaiting shortlist review, report ready, over budget, failed) and spend against budget.
- Create a brief from the guided form, import one from YAML, or duplicate an existing one.

**`/briefs/:id` — brief editor**
- Guided form with the fields of PRD R18.1: project and target users; field boundaries (include/exclude); time window (default 12–18 months); success definition (one primary dimension plus minimum thresholds; weights only under "advanced"); own audience size per channel; channels; geographies; number of winners and losers (default 20/20, range 15–25); budget (LLM and BigQuery).
- **LLM expansion:** proposed problem statement, users, keywords, topics and competitors, each editable before the brief is saved (R18.7).
- YAML view and export. The form and YAML are equivalent.
- **Versions:** every save creates a new version; a diff view compares any two versions.
- **Run:** shows the cost estimate (LLM and BigQuery; model calls in `subscription` mode) against the budget, and starts the run only after confirmation (R18.5). Re-runs show which cached results will be reused.

**`/briefs/:id/shortlist` — shortlist review**
- Every candidate with its discovery source, the relevance filter's verdict and logged reason, and the rubric version.
- Accept, reject or add candidates, each with a reason; every decision is logged with the brief version (R4.7).
- Relevance-filter precision for this brief version, against the ≥ 80% target.
- After the review: the outcome sort, the selected winners and matched losers, balance diagnostics and the sensitivity check (R4.8, R4.9, R4.3).

**Acceptance**
- A brief can be created from the form and from YAML, and the two produce the same stored brief. Invalid briefs (for example, 30 winners, or no primary dimension) are rejected with a message naming the field.
- Every edit creates a new version; old versions stay readable and diffable; reports name the version they came from.
- An install holds at least two briefs that run and report independently.
- A cost estimate is shown before every run. A run given a budget below its estimate stops at the budget with a resumable checkpoint and says so in its run report (tested).
- Re-running an unchanged brief makes no new LLM calls for cached items, and re-running an edited brief recomputes only what the edit affects; the run report lists both.
- Every candidate has a logged reason; every shortlist decision is logged with reason and brief version; precision is computed and shown.
- Winner and loser selection is deterministic for a given brief version and data version.
- Briefs are stored only in the instance's private data directory; the private-data scan blocks a brief file in git.

---

## D1 — Forensics Explorer

*Watch the growth and distribution timeline of every case in your briefs and every project you track.*

**`/cases` — case feed**
- Lists every case: each brief's winners and matched losers, and projects tracked through D5.
- Filters: brief, role (winner, loser, tracked), status (launch mode, tracked, closed), dimension, date range. Sort by recency, velocity or outcome.
- A "Launch mode" strip shows tracked projects currently in launch mode, with velocity and the triggers detected so far.

**`/cases/:id` — case view**
- **Braided timeline:** time-aligned lanes for GitHub (daily net stars from star-history, filtered where available; forks, releases, contributors), Hacker News, Bluesky, blogs and newsletters, package downloads and business signals, plus any other cleared source.
  - Events are annotated `prep | launch | burst | quiet | pivot | relaunch`.
  - Each burst is linked to its ranked candidate triggers, with confidence levels.
  - Zoom from full history down to the finest resolution each lane has (days for stars; hours where polling exists).
- **Spread graph:** who published, who redistributed, and which edges the evidence supports. Edges are styled by evidence level, and each node opens its evidence. Account nodes stay off until LQ-8 is answered (ADR-022).
- **Asset gallery:** the images, GIFs, demos, titles, phrases, benchmark claims and links that spread, grouped by asset category, each with its reach.
- **Evidence inventory:** a sortable table of every item with source, date, reliability, snapshot link and retention state.
- **Outcomes panel:** attention, adoption, community and business at T+7, T+30, T+90 and T+365, each with a verification tag, plus the case's role and rank in each brief it belongs to.
- **Patterns observed:** the neighbourhood patterns this case supports or contradicts, per brief.
- **Compare:** side by side with the case's matched loser (or winner), or with any other case.

**Acceptance**
- Every winner and loser case of a brief with a finished run renders all tabs, with no empty lane that isn't explained (for example "source not enabled", "gap in the source matrix" or "no coverage before tracking started").
- Clicking a random sample of 50 claims resolves each one to a valid snapshot 100% of the time.
- Cases update at every run; launch-mode cases update on their own schedule (ADR-048.4). Each case shows the time of its last update and the run that produced it.
- The case page loads in ≤ 2 s at p95 for a case with 5,000 evidence items.
- Preview (built in M1, see WORK_ORDER): the timeline and evidence tabs work on uncoded captured data and are labelled "uncoded preview".

---

## D2 — Neighbourhood Report (per brief)

*What worked in your neighbourhood against comparable projects that didn't make it, and what's trending there now.*

One report per brief version and data version, at `/insights` (select the brief). The page has two sections that are **visibly separated**, because they make different kinds of claims.

**Header (provenance)**
- Brief name and version; data version; code, codebook, prompt and model versions; generation date.
- The success definition used, the reference population's n, and the number of winners and matched losers.
- The shortlist record: candidates found, filter-accepted, reviewer-accepted, rejected and added; relevance-filter precision against the ≥ 80% target.
- The sensitivity check: how much the winner set changes under alternative success definitions, and which cases are flagged.

**What worked against losers**
- **Neighbourhood patterns.** Each shows: description, why people took part and shared, preconditions, required assets, typical sequence and timing, outcome dimensions affected, **n among winners and n among matched losers**, the supporting cases, the **counterexamples**, reliability labels, and sensitivity flags on the cases involved. Every case listed links to D1. No pattern is called "validated" or "promoted".
- **Channel and timing analyses:** event-study charts (for example, star velocity around a front-page HN appearance), and how channels were sequenced among winners versus losers.
- **Asset and message analyses:** which asset types and framing patterns appeared more often among winners than among their matched losers, with n on both sides.
- "Insufficient evidence in this neighbourhood" wherever n is too small to say anything.

**What's trending** (descriptive, the last 3–6 months; labelled "emerging signal")
- Rising channels, communities, asset formats and message patterns in the neighbourhood over the last 3–6 months, compared with the rest of the brief's window.
- Recent breakouts among the brief's cases and tracked projects, and the triggers detected for them, linked to D1.

**Methods and data quality panel:** per-field agreement (α) with "low reliability" labels for fields below 0.70, "LLM-coded, not human-validated" where applicable, balance diagnostics per matching covariate, coverage per source, evidence counts, costs of the run, and known limitations.

**Acceptance**
- Every pattern shows n among winners, n among matched losers and its counterexamples ("none found" is stated explicitly). No pattern is shown without them.
- Agreement is shown per field, and every finding that rests on a field with α < 0.70 carries the "low reliability" label (ADR-047.7).
- The sensitivity check is present, and every affected case is flagged wherever it appears in the report.
- The report records the brief version, the shortlist decisions and the data version (R18.6), and the same brief version plus data version regenerate the same report from the cache.
- Trend claims show their window, their baseline and n. Nothing in "What's trending" is presented as a pattern.
- The report regenerates after each run of its brief. Every chart shows the data version and the date it was generated.
- The report can be exported as Markdown.
- Public mode renders only aggregate data and passes the private-data scan.

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
  - A backfilled timeline built from the GitHub API (star-history daily net counts, releases, issues, PRs, contributors), HN and Bluesky search (as enabled), registry histories and Wayback snapshots (when cleared), rendered in the D1 case view.
  - Detected launches, bursts and their triggers.
  - The patterns observed, matched against the reports of any brief the project falls in.
- **What is going on now:**
  - Current momentum versus the project's own baseline, and whether it is in a burst, decaying or stable.
  - Active discussions and mentions from the last 7 days.
  - Upcoming signals, such as an announced release or a launch-week post.
- **Benchmark:** position against similar projects at the same age, with trajectory bands drawn from a relevant brief's winners and losers when one exists.
- **Gaps and opportunities:** patterns that a relevant brief's winners showed and this project hasn't, plus trending signals. A link sends the profile into D3.
- **Coverage score:** how complete the reconstruction is. The score states plainly that deleted or ephemeral evidence before tracking started may be missing, and shows each source's coverage window.

**Tracking mode (ADR-048.2):** a tracked project refreshes weekly by default. **Launch mode** refreshes daily for 14 days around a declared or detected launch and every 3 h on launch day; it starts automatically when a tracked project bursts, or when the user declares a launch date. Alerts fire on bursts, new mentions above a reach threshold, and momentum decay. The operator can stop tracking at any time.

**Acceptance**
- First results (GitHub timeline, outcomes, benchmark) in ≤ 10 min. The full external evidence backfill completes in ≤ 6 h for a typical repo.
- Works for repos of any size and age, including ones that never broke out, and shows a coverage score in every case.
- Tracking a project runs its first capture within 1 h, and the project appears in the D1 feed.
- Launch mode: a declared launch date, and a simulated burst on a tracked project, each switch the project to the launch-mode schedule (daily for 14 days, every 3 h on launch day), and D1 shows updates on that schedule.
- The reconstruction is validated against ≥ 5 deep-forensics cases from the pilot brief: it must recover ≥ 80% of their verified key events.

---

## D6 — Platform, install and operations

Required for everything above, and delivered in v1.

- **Data engine:** connectors, snapshot store, pipeline, briefs, reports and versioned data, all private to the instance. Data collected earlier is a cache that briefs reuse.
- **Batch operations (ADR-048):** `pigtail run --incremental` per brief, idempotent and resumable; refresh every 7 days by default (1–8 weeks, never more than 60 days); a launchd schedule on macOS, with Docker running only during runs; a run report after every run; alerts written locally, with a sanitized export and e-mail if configured; encrypted backups to external or private storage. A server setup is optional, documented and tested through backup and restore.
- **LLM backend switch (`/settings` and `LLM_BACKEND` env):** `subscription` (the default, on the operator's own Claude plan) or `api` (the operator's own key). The switch changes nothing else in the product. Usage, cost and rate-limit state per backend are shown in `/admin`. See PRD F15.
- **`/admin`:** run history and run reports, collector health, gaps, error rates, costs and usage per brief, the review queue, and the retention/deletion-sync log.
- **CLI and API:** every pipeline stage and every page's data is available through the CLI and a read-only API.
- **Documentation:** install guide (new users), brief guide, operator guide (macOS reference setup and optional server), developer guide, methodology paper, codebook, schema reference, limitations, and the compliance template each user adopts as controller of their own instance.
- **Example brief:** a synthetic brief in the repo that a new user can run as-is or copy.
- **Reports:** literature review, source matrix, pilot report and a final report.
- **Open-source release:** the public repo contains code, codebook, schemas, UI, docs, the example brief and the compliance template. No data. The owner's aggregate findings are published only after H2 and H4.

**Acceptance**
- **New-user install in under an hour.** On a fresh macOS user account (and on the documented server path), someone who hasn't used pigtail, following only the install guide, within 60 minutes: installs the prerequisites; sets up **their own** credentials (GitHub token, Claude login or API key, optional BigQuery project) with FileVault/disk encryption checked by `pigtail doctor`; reads the guide's cost expectations; loads the example brief (or writes their own); sees its cost estimate; and starts its first run, which then continues unattended. The test is timed and logged.
- The install guide states expected costs and run times for the example brief, measured on a real run, not estimated.
- Three consecutive scheduled runs succeed, with alerts working (a deliberately broken run raises an alert) (ADR-048.4).
- An interrupted run resumes from its checkpoint and produces the same records as an uninterrupted one; replay from snapshots reproduces the stored records.
- Restore from an encrypted backup onto a second machine (or the server path) brings up a working instance with deletions and opt-outs re-applied (ADR-044).
- Switching `LLM_BACKEND` needs a restart and no other change.
- Nothing in the code, defaults, prompts or fixtures names the owner or her project (checked by the verifier with a search of the repo).
