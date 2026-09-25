# pigtail — Deliverables

This document defines what the owner receives. `PRD.md` specifies the requirements behind each deliverable (R-IDs), and `WORK_ORDER.md` schedules them. A deliverable is done only when every acceptance criterion below passes and the `verifier` agent has signed it off.

Repository: https://github.com/suchipizza/pigtail (public, MIT). Code, docs and methodology live there. Raw data, snapshots and person-level graphs never do.

## Overview

| ID | Deliverable | Page / surface | Phase | Earliest milestone |
|---|---|---|---|---|
| D1 | Forensics Explorer | `/cases`, `/cases/:id` | v1 | Preview after M1, full after M5 |
| D2 | Insights & Trends | `/insights` | v1 | Trends after M5, "what works" after G2 |
| D3 | Plan Generator | `/plan` | v1 | After G2 (M8) |
| D4 | Execution Engine | `/execute` | **v2** | After v1 release |
| D5 | Project Analyzer & Tracker | `/analyze` | v1 | Basic after M5, full after M6 |
| D6 | Platform and foundations | CLI, API, `/admin`, `/settings`, docs, data | v1 | Throughout |

**Shared rule for every page.** Every claim, number and event is traceable to evidence: one click opens the evidence record and its snapshot. People appear only as pseudonyms, except for public figures when the codebook allows it. The web app is **private by default**: it sits behind operator login, because it shows private data. An optional **public mode** exposes only aggregate, anonymized D2 content.

---

## D1 — Forensics Explorer

*Watch the growth and distribution timeline of any tracked project.*

**`/cases` — case feed**
- Lists every tracked project: live breakouts (auto-detected), announced launches (pre-launch watch), Tier 2 and Tier 3 cases, and projects tracked through D5.
- Filters: category, stratum, outcome class, status (live, pre-launch, closed), date range. Sort by recency, velocity or outcome.
- A "Live now" strip shows projects currently in a burst, with velocity and the trigger detected so far.

**`/cases/:id` — case view**
- **Braided timeline:** time-aligned lanes for GitHub (stars raw and filtered, forks, releases, contributors), Hacker News, Reddit, Bluesky/X, YouTube/blogs/newsletters, package downloads and business signals.
  - Events are annotated `prep | launch | burst | quiet | pivot | relaunch`.
  - Each burst is linked to its ranked candidate triggers, with confidence levels.
  - Zoom from full history down to the hour.
- **Spread graph:** who published, who redistributed, and which edges the evidence supports. Edges are styled by evidence level, and each node opens its evidence. The view is pseudonymized.
- **Asset gallery:** the images, GIFs, demos, titles, phrases, benchmark claims and links that spread, grouped by asset category, each with its reach.
- **Evidence inventory:** a sortable table of every item with source, date, reliability, snapshot link and retention state.
- **Outcomes panel:** attention, adoption, community and business at T+7, T+30, T+90 and T+365, each with a verification tag. Shows the outcome class.
- **Mechanisms observed:** the mechanism cards this case supports or contradicts.
- **Compare:** side by side with the case's matched loser, or with any other case.
- **Live mode:** open live cases refresh automatically as new evidence is captured.

**Acceptance**
- Every Tier 3 case and ≥ 95% of Tier 2 cases render all tabs with no empty lane that has not been explained.
- Clicking a random sample of 50 claims resolves each one to a valid snapshot 100% of the time.
- A live case shows new evidence within 1 h of capture.
- The case page loads in ≤ 2 s at p95 for a case with 5,000 evidence items.
- Preview milestone (after M1): the timeline and evidence tabs work on captured data and are labelled "uncoded preview".

---

## D2 — Insights & Trends

*What works, and what's trending, in software growth and distribution.*

The page has two sections that are **visibly separated**, because they make different kinds of claims.

**What works** (validated; the promotion rule in PRD §9.3 applies)
- **Mechanism library browser.** Each card shows: description, why people took part and shared, preconditions, required assets, typical sequence and timing, outcome dimensions affected, effect estimate with uncertainty, confidence, supporting and contradicting cases, and the matched-loser contrast. Every case listed links to D1.
- **Filter by stratum:** category, founder audience size, business model, geography/language, backing, launch type. Show "insufficient evidence" for thin cells.
- **Channel and timing analyses:** event-study charts (for example, star velocity around a front-page HN appearance), momentum decay curves, and how channels are sequenced among winners versus losers.
- **Asset and message analyses:** which asset types and framing patterns appear more often among winners than among their matched losers.

**What's trending** (descriptive, recent, not yet validated; labelled "emerging signal")
- Rising channels, communities, asset formats and message patterns over the last 30 and 90 days, compared with the trailing 12-month baseline.
- **Saturating mechanisms:** effect declining over time (PRD R9.3 / R8.4).
- **Current breakouts** and the triggers detected for them, linked to D1.
- **Candidate mechanisms** that are gathering support but are not yet promoted.

**Methods and data quality panel:** panel coverage per stratum, extraction agreement (α), evidence counts, last update time, and known limitations.

**Acceptance**
- Every promoted mechanism shows its loser contrast and contradicting cases. No card is shown without them.
- Trend claims show their window, their baseline and the number of cases (n). Nothing in "What's trending" is presented as validated.
- The page regenerates automatically after each pipeline run. Every chart shows the data version and the date it was generated.
- Public mode renders only aggregate data and passes the private-data scan.

---

## D3 — Plan Generator

*Plug in your project and get a growth and distribution plan based on what worked for similar projects and what's trending.*

**Input**
- A GitHub URL, which auto-fills the profile through D5; or a manual profile for projects that aren't public yet.
- Profile fields: category, stage, founder or team audience (per channel), business model, target users, geography/language, available assets, time budget, launch window, channels to avoid, and goal dimension (attention, adoption, community or business).

**Output**
1. **Similar projects:** the nearest winners **and** their matched losers. Each is shown with why it's similar and links to its D1 timeline.
2. **Recommended mechanisms:** only validated ones, ranked by expected effect for this profile. Each shows confidence, preconditions met or unmet, and the evidence behind it.
3. **Trending opportunities:** emerging signals from D2, kept visibly separate and labelled experimental.
4. **Readiness gaps:** what to fix before launch, such as README, demo, install time-to-first-success, docs or pricing page.
5. **Asset checklist:** the assets to produce, with examples from the asset gallery.
6. **Sequenced plan:** a calendar covering pre-launch, launch day and the post-launch weeks, with channel order, spacing and timing windows.
7. **Predictions:** the expected outcome ranges, plus a "lock predictions" action that pre-registers them (PRD F11).
8. **Adaptation briefs:** how to test each mechanism, and what result would falsify it.
9. **Export** as Markdown or PDF. Saved plans can be versioned and re-generated as the library updates.

When evidence for the profile is thin, the planner says "insufficient evidence for this profile", names the nearest profile it does have evidence for, and does not extrapolate.

**Acceptance**
- Produces plans for ≥ 10 held-out profiles across strata, including correct "insufficient evidence" responses.
- Every recommendation cites its mechanism card and ≥ 3 cases.
- A plan generates in ≤ 2 min.
- Plans are reproducible: the same profile and library version give the same plan.

---

## D4 — Execution Engine (v2, next phase)

*Executes the plan from D3, with a human approving every external action.*

- **Asset drafting:** launch posts per channel, titles, README improvements, demo scripts and comparison tables, generated from the mechanism cards and the project's own material.
- **Channel schedule and approval queue:** each post is drafted, scheduled and then **approved by a human** before anything is published. Posting happens only after that approval, through official APIs, or through copy-and-open where no API exists.
- **Live monitoring:** the project automatically becomes a tracked D5 case, and the plan is compared with what actually happens.
- **Alerts:** when to amplify, hold, relaunch or pivot, based on the decay models.
- **Closing the loop:** outcomes are scored against the locked predictions and fed back into the library.

**Acceptance (for v2)**
- Runs a full plan for 1 real launch end to end, with 0 unapproved external actions.
- The prediction scorecard is produced automatically at T+30.
- Every generated asset links to the mechanism it implements.

---

## D5 — Project Analyzer & Tracker

*Enter any GitHub project and see what has been done and what is still going on. Analyse it once, or keep tracking it.*

**Input:** a GitHub URL. Options: a one-time analysis, or **Track this project**, which adds it to live capture and turns it into a continuously updated D1 case.

**Output**
- **What has been done (reconstruction):**
  - A backfilled timeline built from GH Archive's full history, HN/Reddit/Bluesky search, registry histories and Wayback snapshots, rendered in the D1 case view.
  - Detected launches, bursts and their triggers.
  - The mechanisms observed, matched against the library.
- **What is going on now:**
  - Current momentum versus the project's own baseline, and whether it is in a burst, decaying or stable.
  - Active discussions and mentions from the last 7 days.
  - Upcoming signals, such as an announced release or a launch-week post.
- **Benchmark:** position against similar projects at the same age, with trajectory bands drawn from winners and losers.
- **Gaps and opportunities:** validated mechanisms that similar winners used and this project hasn't, plus trending signals. A link sends the profile into D3.
- **Coverage score:** how complete the reconstruction is. The score states plainly that deleted or ephemeral evidence before tracking started may be missing, and shows each source's coverage window.

**Tracking mode:** capture runs daily, as for any live case. Alerts fire on bursts, new mentions above a reach threshold, and momentum decay. The operator can stop tracking at any time.

**Acceptance**
- First results (GitHub timeline, outcomes, benchmark) in ≤ 10 min. The full external evidence backfill completes in ≤ 6 h for a typical repo.
- Works for repos of any size and age, including ones that never broke out, and shows a coverage score in every case.
- Tracking a project starts capture within 1 h, and the project appears in the D1 feed.
- Analysis of an arbitrary repo is validated against ≥ 5 existing Tier 3 cases: the D5 reconstruction must recover ≥ 80% of their human-verified key events.

---

## D6 — Platform and foundations

Required for everything above, and delivered in v1.

- **Data engine:** collectors, snapshot store, pipeline, the mechanism library data, and versioned data releases (private).
- **Operations:** live detection and capture running unattended for ≥ 30 days with alerting.
- **LLM backend switch (`/settings` and `LLM_BACKEND` env):** `subscription` (the default for now) or `api`. The switch changes nothing else in the product. Usage, cost and rate-limit state per backend are shown in `/admin`. See PRD F15.
- **`/admin`:** collector health, backlog, error rates, costs and usage, the review queue, gate status and the retention/deletion-sync log.
- **CLI and API:** every pipeline stage and every page's data is available through the CLI and a read-only API.
- **Documentation:** install and operator guide, developer guide, methodology paper, codebook, schema reference, data card, limitations, and compliance docs.
- **Reports:** literature review, source matrix, pilot report, mechanism library v1, evaluation v1 and a final report.
- **Open-source release:** the public repo contains code, codebook, schemas, UI and docs. Aggregate findings are published only after H2 and H4.

**Acceptance:** `docker compose up` on a fresh VM, following the operator guide, brings up all v1 pages working on a sample dataset within 30 min. Switching `LLM_BACKEND` needs a restart and no other change.
