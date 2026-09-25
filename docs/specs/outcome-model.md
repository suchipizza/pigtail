# Outcome model spec (M3-T1)

**Status:** draft for verifier review (M3 acceptance); current thresholds `outcome-thresholds v0.2.0` (ADR-035), which supersede v0.1.0. Thresholds are **provisional** until the M4 pilot calibrates them.
**Requirements:** PRD §5.3, §5.6, F3 (R3.1–R3.5), §8.1, §8.2, §9.1; ADR-009, ADR-010, ADR-032 (supersedes ADR-012; star source and R1.1 basis, M3-T6), ADR-035 (class series `raw`, amends ADR-020; M3-T8).
**Author role:** `analyst` (with researcher discipline: every factual claim about a source cites the source matrix `docs/research/source-matrix.md` (SM) or the literature review `docs/research/literature.md` (LR); anything we could not confirm is marked **unverified**).
**Data version:** none. The spec precedes data. No outcome has been computed or looked at.
**Code commit:** `3f07692` (2026-09-25; includes the merged M1 capture core; outcome scoring itself is not implemented yet).
**Machine-readable thresholds:** `schemas/outcome-thresholds/v0.2.0.json` (`outcome-thresholds v0.2.0`, current). `schemas/outcome-thresholds/v0.1.0.json` is **superseded** by v0.2.0 (ADR-035) and kept unchanged as a historical record; no observation or class was ever computed under it.

## 0. Limitations (read first)

1. **Spec before data.** No base rate, cohort size or coverage ratio has been measured. Every numeric threshold here is a v0 choice to be calibrated in the M4 pilot (§9).
2. **Stars in GH Archive are unusable for scoring, and the stargazers API is closed.** GH Archive has under-captured stars since 2025-05 (ADR-009); on 2026-09-24 it held about 2 % of stars (ADR-028, detection-replan §7.2). GitHub restricted the stargazer lists (REST and GraphQL) to admins and collaborators on 2026-06-30, so ADR-012's source is gone (detection-replan §0). Under **ADR-032** the scoring star series is the daily net count from GitHub's star-history endpoint (§1.2). It has no identities, so the bot- and fake-star-filtered series exist only where identity-level event data exists. **Consequence:** classes cannot rely on the filtered series. Under v0.1.0, which classed on `starscout_filtered`, `A30`, `V90` and `P30` would have been `unknown` for most retrospective windows. **ADR-035** therefore moves the class series to `raw` star-history for every case (`outcome-thresholds v0.2.0`, §4, §5); the filtered series are a sensitivity analysis where they are eligible (O11, closed).
3. **Community events in GH Archive may be incomplete too.** The same page-1-only crawler loss (#310) would affect PR and issue events, and GitHub trimmed PR payloads from 2025-10-07 (SM §2.1). Whether merged-PR events are still usable is **to be checked** (backlog M3-T0). Until then, community metrics are specified with the GitHub API as primary for scored cases.
4. **Business outcomes cannot be verified automatically.** TrustMRR, Crunchbase and the YC directory are GAPs (SM §3; ADR-010). Business metrics are `self_reported` or `unknown` and are **not** used in outcome classes.
5. **Several sources have no history** (Homebrew, Docker Hub, Discord invite counts, HN front-page rank). Their metrics exist only from the first pigtail snapshot onward. Retrospective cases get `unknown`, never an imputed value.
6. **Percentiles are relative to pigtail's candidate universe** (R4.1: threshold-crossers plus launch-signal non-winners), not to all of GitHub. "Top decile" means top decile of that universe's cell.
7. **Categories are provisional.** The v0 taxonomy (§3.1) is a proposal until the codebook (M4-T1) adopts or replaces it.

## 1. Metrics (§8.1)

### 1.1 Conventions for every metric

- **Anchor and offsets.** `T` is the case anchor (§2). An observation point `k ∈ {7, 30, 90, 365}` days.
- **Flow metrics** (stars, downloads, PRs, posts) are counted over the half-open window `[T, T+k)` in UTC unless stated otherwise. **Stars are the main exception:** they come in daily buckets of the star-history endpoint's own calendar, and the window is mapped to those days by the rule in §1.2. A pre-anchor baseline `[T−30d, T)` is stored for every flow metric for reporting lift; it is never used in classes.
- **Stock metrics** (dependents, community size, pricing page) take the snapshot closest to `T+k` within a tolerance of `max(1 d, 0.1·k)` (±1 d at T+7, ±3 d at T+30, ±9 d at T+90, ±36.5 d at T+365). No snapshot inside the tolerance → `unknown`. No interpolation.
- **No look-ahead.** A value at `T+k` uses only data timestamped before `T+k`. It is computed from the first fetch made at or after `T+k + settle_lag` (default 3 days, per source configurable) and is not overwritten by later fetches; later fetches create new versioned observations.
- **Observation record.** Each value is an `outcome_observation` (PRD §7) with at least: `metric_id`, `metric_version`, `case_id`, `anchor` (type and time), `offset_days`, `value`, `unit`, `series_variant` (e.g. `raw | bot_filtered | starscout_filtered`), `status`, `verification_tag`, `source` (TM id), `coverage` (window start/end, observed fraction, `coverage_ratio` where measured), for stars also `day_boundary` (§1.2) and `bot_filter_basis`, `evidence_ids`, `run_id`, `outcome_thresholds_version`. The v1 schema is written in M5; this list is the requirement.
- **Status** (separate from the tag): `observed | pending | unknown | not_applicable`.
  - `pending`: `T+k + settle_lag` is in the future (right-censored, §2.4).
  - `unknown`: the value should exist but no cleared source can provide it for this case/window (GAP source, connector off, no history, truncated API, no identity-level data for a filtered star series, coverage below gate). **Never imputed silently.** `value` is `null`.
  - `not_applicable`: the metric does not exist for this repo (e.g. no package in any registry). `value` is `null`.
- **Verification tags** (R3.2, extended with `unknown` per ADR-010):
  - `verified`: directly observed from a cleared source that measures the quantity itself (counts from an official API or dataset), with a snapshot.
  - `estimated`: observed but approximate or incomplete by construction (e.g. Discord's "approximate" counts; an event-archive star count with coverage below 1, which is a lower bound, `lower_bound: true`; a bot-filtered star series, §1.2).
  - `self_reported`: a claim by the project or its founders, or press repeating it (SM §2.31), with a snapshot of the claim.
  - `unknown`: no value (status `unknown` or `pending`).
- **Zero is a value.** An observed count of 0 is `observed`/`verified`. Absence of a capture or a post is **not** evidence of absence for boolean business signals (§1.5).

### 1.2 Attention

**A1 `att.stars` — stars gained**
- Definition: net stars gained in the window, i.e. the number of **current** stargazers whose star date falls in the window's days (below). Unit: stars. Stored for three variants: `raw`, `bot_filtered` (capture layer `bot-filter-v0`, `src/pigtail/capture/botfilter.py`), `starscout_filtered` (§4).
- **Primary source (ADR-032.3; replaces ADR-012's stargazers API, which GitHub closed to non-collaborators on 2026-06-30):** GitHub star-history endpoint `GET /repos/{owner}/{repo}/stargazers/history` (TM-33 under TM-02, CLEARED-WITH-CONDITIONS; no personal data). It returns weekly totals with 7 daily net counts per week, most recent first, back to the repo's creation week; `per_page` ≤ 30 weeks (https://docs.github.com/en/rest/activity/starring?apiVersion=2026-03-10#get-repository-star-history, accessed 2026-09-25; detection-replan §0). **Measured:** the sum of all weeks equals the repo's current `stargazers_count` (2 repos), and full history came back for a 250k-star repo, so no page cap applies at that size. `raw` = the star-history daily counts. Tag `verified`.
- **Day mapping (how `[T, T+k)` maps to the endpoint's days).** GitHub documents only that "Week and day boundaries are not guaranteed to align with UTC". Our tests fit **US Pacific days** (America/Los_Angeles) far better than UTC days; this is an **inference** from small samples (detection-replan §0, §7.4), to be confirmed across the DST change on 2026-11-01 (detection-replan §8 M2). The zone is a config value, `star_history_day_tz` (default `America/Los_Angeles`).
  - Let `D(t)` be the endpoint-calendar date that contains the instant `t` in `star_history_day_tz`. For an anchor with `hour` precision, the first day is `D(T)`. For an anchor with `day` precision (a dated launch without a time, stored as 00:00 UTC, §2.1), the first day is the endpoint day whose date equals T's UTC date, because the evidence names a date, not an instant (00:00 UTC is the previous evening in Pacific time).
  - The window `[T, T+k)` is the `k` endpoint days starting at that first day; the pre-anchor baseline `[T−30d, T)` is the 30 endpoint days before it; the `[T+k−7d, T+k)` velocity window is the last 7 of the `k` days.
  - **Misalignment.** For an hour-precision T the day window starts at endpoint midnight `δ = T − start(D(T))` before T (`0 ≤ δ < 24 h`), so it includes up to 24 h of pre-anchor stars and leaves out up to 24 h at the end. Using the day that contains T keeps the anchor day, where launch and burst stars concentrate, inside the window; starting a day later would lose it. If the endpoint's real zone differs from `star_history_day_tz`, `D(T)` can be off by one more day for T near midnight. **Handling:** day buckets are never split or interpolated into hours; each star observation records `day_boundary = {tz, tz_status: inferred|confirmed, window_offset_hours: δ, first_day, last_day}`; if M2 shows a different zone, `star_history_day_tz` is corrected and every star observation is recomputed under a new `metric_version` (a measurement fix, not a threshold change). On DST-switch days an endpoint day is 23 or 25 hours long; that is accepted and not corrected.
  - A day is used only after it has ended in the endpoint's calendar (the current day fills in during the day; responses are cached about 60 s, detection-replan §0). `settle_lag` (3 d) covers this.
- Derived: `att.star_velocity_7d@k` = mean stars/day over the last 7 endpoint days of the window; `att.star_peak_velocity_30` = max over day indices `d ∈ [7, 30]` (days after the first day) of the mean stars/day over days `[d−7, d)` (7-day rolling means on daily endpoint-calendar buckets, so peak and later velocity are comparable).
- **Filtered variants (ADR-032.3).** The endpoint has no identities, so `bot_filtered` and `starscout_filtered` need identity-level star events (`WatchEvent.actor`, pseudonymised at ingest):
  - Bases, recorded per observation as `bot_filter_basis`: `repo_events` (per-repo Events API polling for tracked cases, from the start of tracking; TM-33, conditions pending LQ-29), `gharchive` (GH Archive, about 2 % of stars in 2026, detection-replan §7.2) or `none`. `opendigger` is a valid value only if TM-32 is ever cleared; today OpenDigger is off (GAP pending LQ-28) and is not a basis.
  - `coverage_ratio` = identity-level star events seen on the window's endpoint days ÷ star-history net stars for the same days (the reference is star-history, no longer the stargazers API). It can exceed 1, because events are gross and star-history is net.
  - `bot_filtered` = `raw` − the stars on the window's days from actors that `bot-filter-v0` drops, floored at 0 (a floor that binds is flagged). Tag `estimated`: removal counts gross events, so a bot that has since un-starred or been deleted is subtracted from a count that no longer holds it (over-removal), and bot stars the capture missed are not subtracted (under-removal). `removed_count` is stored with the value.
  - Where no identity-level event data exists for the window, both filtered variants are `unknown` with reason `no_identity_data`. They are **never imputed**, and `raw` is never copied into them.
  - A filtered value is **class-eligible** only if event data covers every day of the window, no window overflow was detected (the per-repo Events feed holds 300 events, detection-replan §1.3), and `coverage_ratio ≥ 0.90` (the existing per-case gate, §5.3). Otherwise it is stored but its sensitivity value is `unknown`. In practice only cases tracked from before T meet this. Under v0.2.0 (ADR-035) filtered values feed only the fake-star sensitivity run, never the classes (§4, §5.3).
- **Fallback for `raw`:** an event-archive `WatchEvent` count (GH Archive, TM-01) may stand in only where star-history cannot serve the window (for example the repo has since been deleted) **and** the case's per-window `coverage_ratio`, measured against star-history, is ≥ 0.90. It is then tagged `estimated`, `lower_bound: true`. This gate cannot be met at GH Archive's current ~2 % coverage, so in practice the value is `unknown`. OpenDigger is never a scoring series (TM-32 condition), even if cleared.
- Known biases:
  - **Net, current stargazers only (survivor bias).** Star-history counts current stargazers bucketed by star date. Un-starred and deleted accounts vanish from past days, including fake accounts that GitHub later deletes (StarScout reports 57 % of flagged accounts deleted by Jan 2025, LR §2.1), so `raw` is already partly de-faked and the raw/filtered gap is smaller than in event data. Whether stars from spam-flagged or suspended (not deleted) accounts are excluded is **unverified** (detection-replan §10).
  - **Values drift down as fetch time moves on.** The scored value is the earliest fetch after `T+k+settle_lag` (§1.1); later fetches are new versioned observations. Each observation records `fetched_at` and `fetch_lag_days` = days from `T+k` to the fetch. Retrospective (backfilled) cases are fetched months or years after `T+k`, so their survivor loss is larger than for live cases, and larger for older cohorts. Cohort cells partly absorb this (similar lags within a quarter), but live and backfilled cases can share a cell. The pilot reports the effect (§9 item 12).
  - **Day, not hour, resolution** for every case scored from star-history, including where hourly watch-list snapshots exist, so that all cases in a cell use the same series. Hourly snapshots are used for burst onset (§2.1) and screening only.
  - GH Archive (fallback and `gharchive` basis only): under-capture since 2025-05, ~2 % in 2026, burst-correlated (ADR-009, ADR-028); no un-star events, so its counts are gross (LR §1.6, §7.3 item 3).
  - Cost: 1 core REST request per 30 weeks of history, so 1–33 requests per repo for a full history (detection-replan §2.2, §6.2). Budgeted under ADR-032.4 (one token).

**A2 `att.hn_*` — Hacker News**
- Matching: HN stories whose URL points to the repo URL or the project's own domain, or whose title names the repo (`owner/name` or the project name as recorded at T), with `created_at ∈ [T−7d, T+k)`. The 7-day lead catches the launch post when T is a burst. Matching rules are versioned in the codebook.
- `att.hn_points@k` = max `points` over matched stories; `att.hn_comments@k` = sum of `num_comments`. Source: HN Algolia (TM-03, CLEARED-WITH-CONDITIONS, pending LQ-6 (was H2 Q4) for commercial operators). Depth: from 2006-10-09 (observed, SM §2.3). Tag `verified`. Bias: values are **as of fetch**, not as of `T+k`; stories created long before the fetch are treated as final. The ~1,000-hit cap per query (observed, undocumented) requires windowed queries.
- `att.hn_front_page_tag@k` = whether any matched story carries Algolia's `front_page` tag. Tag `verified` as an observed field; its exact semantics are undocumented (SM §2.3), so it is descriptive only.
- `att.hn_frontpage_minutes@k` = sum over matched stories of polled minutes with rank ≤ 30 in `topstories` within `[T, T+k)`. Source: HN Firebase, own polling at ≤ 1/min (TM-04). **Depth: only from the start of pigtail's live polling.** Rank history cannot be backfilled (LR §7.3 item 1, an inference from the returned fields). Before polling coverage → `unknown`, never 0. The value 30 (one HN page) is an assumption, **unverified** here; it is a config parameter. Polling gaps are recorded as coverage; minutes are a lower bound when coverage < 1 (`estimated`).

**A3 `att.reddit_reach`** — Reddit is a GAP (TM-05). Value `unknown` by default. Only an operator with a signed commercial agreement may enable it (new ADR per ADR-010). Reddit URLs seen in other sources are recorded as links only.

**A4 `att.bsky_*` — Bluesky reach**
- `att.bsky_posts@k` = posts in `[T, T+k)` that link the repo URL or project domain; `att.bsky_engagement@k` = sum of likes + reposts + replies + quotes on those posts, as of fetch; `att.bsky_distinct_authors@k` = distinct (pseudonymised) authors. No follower-based reach: it is person-level and not needed.
- Source: Jetstream (live; replay needs a metered key, 36 h live lookback) and authenticated `searchPosts` (TM-06, CLEARED-WITH-CONDITIONS). Search depth is **unverified** (SM §2.6). Before the connector's coverage start → `unknown`.
- Bias: deletions are honoured (R1.5), so counts shrink over time; the scored value is the earliest fetch after `T+k+settle_lag`. X and YouTube are GAPs (TM-14, TM-15), so "social reach" is Bluesky + HN only; mechanism cards must say so (SM §5 item 8).

### 1.3 Adoption

**B1 `adopt.downloads` — registry downloads**
- Package mapping: packages whose source-repo metadata points to the repo (deps.dev project mapping or the registry's repository field), as known at T. Mapping is versioned and stored with evidence.
- **Primary ecosystem** is fixed at T: the ecosystem of the package declared in the repo's root manifest at T; tie → the one matching the repo's primary language; still tied → most dependents at T. Classes use only the primary ecosystem. Other ecosystems are stored and reported. (Fixing it at T avoids picking the "best" ecosystem after seeing outcomes; P7.)
- Definition: downloads of all mapped packages in the primary ecosystem over `[T, T+k)`. Unit: downloads. Never summed across ecosystems (different counting semantics).
- Sources and depth:
  - npm downloads API (TM-08, **CLEARED**): daily from 2015-01-10; ≤ 18 months per query (chunk); bulk queries exclude scoped packages. Tag `verified`.
  - PyPI BigQuery `file_downloads` (TM-07, CLEARED-WITH-CONDITIONS, CC BY 4.0 attribution): complete from 2018-07-26; earlier rows under-counted ~10× → windows before 2018-07-26 are `unknown`. Tag `verified`.
  - crates.io version-downloads archive (TM-09, CLEARED-WITH-CONDITIONS): daily from Nov 2014. Tag `verified`.
  - Homebrew (TM-10): rolling 30/90/365-day installs only, no history. `k=30/90/365` values can be read directly from a snapshot taken at `T+k` (within tolerance); `k=7` → `unknown`. Only from pigtail's first snapshot. Tag `verified`.
  - Docker Hub (TM-11): lifetime `pull_count` only. Flow = difference of two own snapshots at T and `T+k` (each within tolerance). No snapshot at T → `unknown`. Tag `verified`.
  - Ecosystems with no download data source in the matrix (e.g. Go, Maven) → `unknown` (not `not_applicable`: the repo has adoption, we just cannot see it).
  - No package in any registry → `not_applicable`.
- Biases: mirrors, caches and CI inflate counts (SM §2.7; PRD "verified (noisy)"); download counts are not comparable across ecosystems; Homebrew/Docker Hub windows exist only for live-tracked repos, so retrospective self-hosted apps are systematically `unknown`.

**B2 `adopt.dependents` — dependents (stock)**
- Definition: number of distinct packages that depend on any mapped package (direct dependents if the source separates them; field names to be confirmed at connector build, **unverified**), at `T+k`.
- Primary: deps.dev (TM-12, CLEARED-WITH-CONDITIONS; CC-BY 4.0): dependents only in v3alpha and only for npm, Cargo, Maven, PyPI. History via the BigQuery snapshots dataset, whose start date and frequency are **unverified** (SM §2.12); where no historical snapshot is within tolerance → `unknown`.
- GitHub dependents ("Used by") are a GAP: no API, robots.txt disallows the page (TM-26). Not used.
- Tag `verified`. Bias: narrower ecosystem coverage than GitHub's own graph.
- Not used in outcome classes v0 (a stock, often without history; reported alongside).

### 1.4 Community

**C1 `comm.returning_external_contributors`**
- PRD: "≥ 2 merged PRs across ≥ 2 months". Operationalised (P5) as: the number of distinct **external** non-bot authors with **≥ 2 merged PRs** whose merge times fall in `[T, T+k)` **and** whose first and last merge in that window are **≥ 30 days apart**. Unit: contributors. Computed at `k = 90, 365`; at `k = 7, 30` it is `not_applicable` (the span rule cannot be met or is degenerate).
- External: PR `author_association` at the time of observation is not `OWNER`, `MEMBER` or `COLLABORATOR`, and the login is not a bot (`bot-filter-v0` rules). The field exists in GitHub's PR objects to our knowledge; its presence in trimmed GH Archive payloads after 2025-10-07 is **to be checked** in M3-T0. Bias: the API reports the *current* association, so contributors later promoted to collaborator are under-counted as external.
- Primary (proposed, P2): GitHub REST/GraphQL pull requests with `merged_at` (TM-02). Complete for the repo's history, consistent across cohorts. Tag `verified`. Deleted accounts appear as a ghost user and cannot be linked across PRs → slight under-count.
- Fallback: GH Archive `PullRequestEvent` with action `closed` and `merged = true` (TM-01). **Flag: possibly missing or incomplete merged-PR events since 2025-05 / payload trimming since 2025-10-07 — to be checked (M3-T0).** Until M3-T0 reports, GH Archive values are tagged `estimated` (lower bound) and may not feed classes.

**C2 `comm.external_activity`**
- `comm.external_issues_opened@k`, `comm.external_prs_opened@k`: issues and PRs opened in `[T, T+k)` by external non-bot authors; `comm.external_authors@k`: distinct such authors. Units: counts.
- Primary: GitHub API (P2). Fallback: GH Archive `IssuesEvent`/`PullRequestEvent` `opened`, same M3-T0 caveat. Tag `verified` (API) / `estimated` (GH Archive).
- Reported, not used in classes v0.

**C3 `comm.discord_members`, `comm.slack_members` (stock)**
- Discord: `approximate_member_count` from the invite endpoint, for invite codes the project itself publishes (TM-27, CLEARED-WITH-CONDITIONS, **off by default until LQ-21 (was H2 Q12)**). No history → only from first snapshot. Tag `estimated` (Discord calls it approximate).
- Slack: GAP (TM-28) → `unknown`; `self_reported` only if the project's admin shares counts.
- Not used in classes.

### 1.5 Business (reported only; never in classes v0)

| Metric | Definition | Source (clearance) | Tag |
|---|---|---|---|
| `biz.mrr` | Monthly recurring revenue stated for a date ≤ `T+k` | TrustMRR is a GAP (TM-23). Only a public founder statement in a cleared source (HN, Bluesky, project site via TM-29/TM-13), snapshotted | `self_reported` or `unknown` |
| `biz.funding` | Disclosed rounds (amount, date, round, investor orgs) announced ≤ `T+k` | Press, operator-entered (TM-31, manual); Launch HN title (TM-03); Crunchbase only with the operator's own licence (TM-24; internal only, never public mode); YC directory GAP (TM-25) | `self_reported`; `verified` only via a licensed Crunchbase record |
| `biz.pricing_page` | A pricing/paid-plan page on the project's own domain exists at `T+k` | Wayback CDX (TM-13, off by default for commercial operators until LQ-15 (was H2 Q5)); direct fetch under TM-29 conditions | `verified` when a capture ≤ `T+k` shows it. No capture → `unknown` (never `false` from absence). `false` only from a live fetch whose site navigation was captured, and only for that fetch date |
| `biz.hiring_hn_posts` | Count of "Who is hiring?" top-level comments matched to the project in months overlapping `[T, T+k)` | HN APIs (TM-30) | `verified` count; 0 is a count of posts, not proof of no hiring |
| `biz.careers_roles` | Open roles on the project's careers page at `T+k` | TM-29 (per-site check, Wayback first) | `verified` when captured; else `unknown` |

The PRD §8.1 lists MRR as "verified"; under current clearances pigtail **cannot** deliver verified MRR (SM §5 item 4). This spec does not promise it.

## 2. Time anchor and observation points (R3.1)

### 2.1 Candidate anchors
- **Declared launch `T_launch`:** the timestamp of a dated evidence record declaring a launch: a Show HN / Launch HN post, a launch post on a cleared source, an announced date on the R1.3 watchlist, or an operator-entered launch with a cited source. Precision is recorded (`hour` if the evidence has a time, else `day` with 00:00 UTC).
- **First burst `T_burst`:** for the first velocity detection of the case (rule `velocity-v0`: ≥ 100 net stars in 48 h and z ≥ 3 against the 30-day baseline, the PRD R1.1 defaults; recorded as `burst_detection` in the thresholds JSON).
  - **R1.1 check (ADR-032.2).** The thresholds are evaluated on the **public net star count**: hourly watch-list `stargazerCount` snapshots (GraphQL, TM-02), confirmed with star-history daily counts (TM-33). The 30-day baseline comes from star-history daily counts converted to 48-hour sums, with ADR-027 item 3's √μ floor. Where only daily data exists (backfill), "48 h" is the last 2 complete endpoint days plus the current day so far, with precision `day` (detection-replan §6.3). The GH Archive scan is kept as a screen and a control only.
  - **Bot filter as a confirmation step.** A screen triggers on raw net counts; the case opens only after `bot-filter-v0` (and the lockstep flag, ADR-027 item 2) has run on the identity-level star events available for the detection window. Each case records `bot_filter_basis` (`repo_events`, `gharchive` or `none`; `opendigger` only if TM-32 is cleared), `coverage_ratio` (identity-level stars seen ÷ star-history net stars for the window, ADR-009 field) and `bot_filter_confirmed` (true only when the filtered count still meets R1.1 on data that passes the §1.2 class-eligibility conditions). Backfilled windows have no identity-level data unless GH Archive saw some, so their confirmation runs with basis `none` (or `gharchive` at ~2 %) and records `bot_filter_confirmed: false`. Whether such detections open cases is a capture decision (M1-T24, ADR-032); the outcome model treats `bot_filter_confirmed` as a recorded attribute that is reported per cohort, not as a class input. R1.1's thresholds are unchanged; only the data source changed (ADR-032).
  - **Onset.** Where hourly count snapshots cover the detection window, the **onset hour** = the earliest hour `h` in the 48-hour window whose net star gain exceeds `μ_h + 3·√μ_h`, where `μ_h` is the baseline mean stars per hour (floor `μ_h + 1` when `μ_h = 0`); precision `hour`. If no single hour qualifies (a diffuse rise), onset = start of the 48-hour window. Where only star-history days exist, the same rule is applied to endpoint days (`μ_d` per day), onset = start of the first qualifying endpoint day, precision `day`. The onset precision is stored with the anchor and decides the day mapping in §1.2. Onsets computed from GH Archive (earlier detections) are kept for audit only.

### 2.2 Choosing T (per case)
1. Velocity case with a declared launch in `[T_burst − 30d, T_burst]` → `T = T_launch` (anchor type `launch`). If several, the earliest.
2. Velocity case without such a launch → `T = T_burst` (anchor `burst`).
3. Announced / manual / analyze case with a declared launch → `T = T_launch`. A burst within `[T_launch, T_launch + 90d)` is recorded as an event, not as the anchor.
4. No declared launch and no burst → no anchor; outcomes are not scored (status `unknown`, reason `no_anchor`).
5. **Ties:** `T_launch == T_burst` → anchor type `launch`. Two declared launches with the same timestamp → the one with the lower evidence id (deterministic).
6. A repo can have several cases (relaunch, pivot). Each case gets its own T and its own classes. Repo-level summaries use the case with the earliest T and flag relaunches.

Rationale: taking the launch when it precedes the burst keeps the launch-day attention inside the windows and gives winners and losers the same kind of anchor (a launch signal), which matters because anchoring on bursts alone selects on the outcome. The 30-day look-back is a v0 choice (pilot item).

### 2.3 Observation points
T+7, T+30, T+90, T+365 days (R3.1). Each observation point's windows and tolerances follow §1.1. All times UTC, hourly resolution for flows, except stars, which use star-history endpoint days (§1.2).

### 2.4 Right-censoring and truncation
- **Right-censoring (repo too young).** If `now < T+k + settle_lag`, the point is `pending`; nothing is extrapolated. Classes that need a pending point are `unclassified` with reason `pending` (§5.4). In survival analyses (R8.4) a case is censored at its last observed offset.
- **Left truncation (source starts after T).** If a source's coverage starts after the window starts (e.g. HN rank polling, Homebrew, Docker Hub, Discord, Bluesky connector start, PyPI before 2018-07-26, per-repo event polling for the filtered star series), the metric is `unknown` for that window. Coverage windows are per-source metadata (R17.3; SM §5 item 7).
- **Short baselines.** A repo created less than 30 days before T has a `partial`/`none` detection baseline (already recorded as `baseline_quality`); its pre-anchor baseline windows are marked partial.
- **Cohort maturity.** Cohort percentiles at offset k are `provisional` until every case in the cohort cell has passed `T+k + settle_lag` (§3.4). Classes use final percentiles only.

## 3. Normalization by category and quarterly cohort (R3.4)

### 3.1 Category taxonomy v0 (provisional until codebook M4-T1)

| id | Covers |
|---|---|
| `ai-apps-agents` | LLM apps, agents, AI assistants, prompt tooling |
| `ml-infra` | ML/DL frameworks, training/inference engines, model serving, vector search |
| `devtools` | CLIs, editors and plugins, build tools, testing, linters, dev productivity |
| `web-frontend` | UI frameworks, component libraries, CSS, static-site tools |
| `backend-libs` | general-purpose libraries, web/API frameworks, SDKs |
| `data-db` | databases, data engineering, analytics, BI |
| `infra-ops` | containers, orchestration, IaC, CI/CD, observability, networking |
| `security` | security tools, auth, secrets, scanners |
| `self-hosted-apps` | end-user apps, self-hosted alternatives to SaaS |
| `mobile-desktop` | mobile, desktop and cross-platform app frameworks and apps |
| `lang-runtime` | programming languages, compilers, runtimes, package managers |
| `crypto-web3` | blockchain, crypto, web3 |
| `lists-learning` | awesome-lists, tutorials, books, courses, interview prep, prompt collections |
| `media-games-science` | games, graphics, audio/video, scientific and hardware/embedded |
| `other` | anything else |

`crypto-web3` and `lists-learning` are separate because they behave differently on stars (StarScout reports fake stars concentrated in AI/LLM, blockchain, tool and tutorial repos, LR §2.1) and `lists-learning` has no adoption metric.

**Assignment method (v0):**
1. Inputs frozen **as of T**: repo description, GitHub topics, primary language, and the first 4,000 characters of the README at the last commit before T. No post-T information (avoids outcome leakage).
2. Rule pre-pass: names starting with `awesome-` or READMEs that are mostly link lists → `lists-learning`.
3. Otherwise an LLM classification through `LLMClient` (F15) returns one primary category, an optional secondary, and a confidence. Double-coded on a random 10 % sample with a different prompt (R7.2); Krippendorff's α reported; confidence < 0.6 or disagreement → review queue (R7.3).
4. Stored with `taxonomy_version`, `prompt_version`, model id and codebook version. The category never changes after the outcome is known, except by a taxonomy version bump applied to all cases.
The pilot and codebook may replace this taxonomy; any replacement re-runs normalization for all cases.

### 3.2 Cohort
The **quarterly cohort** is the UTC calendar quarter containing T (e.g. `2026Q1`). The **normalization cell** is (category, cohort). For adoption, the cell is additionally restricted to cases with the same primary ecosystem.

### 3.3 Percentile computation
- Population: cases in the cell with status `observed` for that metric/offset (unknowns excluded; the share of unknowns is reported per cell and a cell with > 50 % unknown is flagged).
- Mid-rank percentile: `p = 100 · (n_below + 0.5 · n_equal) / n`, where `n_equal` counts the case itself. Ties share one percentile; no random tie-breaking.
- Computed on the class-relevant series (`raw` star-history stars, ADR-035; primary-ecosystem downloads; C1 contributors). Filtered-series percentiles (`bot_filtered`, `starscout_filtered`) are also computed for sensitivity, within the same cell over the cases eligible for that variant (§4).
- Percentiles are versioned by universe version: backfill adds cases to old cohorts, so percentiles are recomputed and changes of class are logged.

### 3.4 Minimum cell size and fallback
- A percentile is reported only if the cell has **n ≥ 30** observed cases.
- Fallback, in order, recorded as `normalization_cell_used`:
  1. (category, calendar year of T)
  2. (all categories, cohort quarter)
  3. none → percentile `unknown`, reason `small_cohort`.
- For adoption, the ecosystem restriction is kept at every fallback level.
- n = 30 means the top decile is ~3 cases; the pilot must check whether this is stable enough (§9).

## 4. Fake-star filtering (R3.3)

- **Method:** StarScout (He et al., ICSE 2026), the current reference method; no peer-reviewed successor was found (LR §2.1–2.2). Reproduced with the authors' local DuckDB pipeline at a pinned commit, parameters pinned in a versioned config: low-activity signature; lockstep (CopyCatch) n = 50, m = 10, Δt = 30 d, ρ = 0.5; campaign post-processing: a month with > 50 fake stars and > 50 % fake share, and fake stars > 10 % of all-time stars (LR §2.1, §2.3). The lockstep parameters and the campaign-level removal rule are recorded as `fake_star_filter` (`starscout-v0`) in the thresholds JSON.
- **Series stored (all three, always):** `raw`, `bot_filtered`, `starscout_filtered`.
- **Filtered series definition:** for a repo **with** a StarScout campaign flag, remove stars from all accounts flagged by either signature on that repo; for a repo **without** a campaign flag, `starscout_filtered = bot_filtered`. Account-level flags alone are not used to remove stars, because the low-activity signature also matches legitimate new users; the campaign post-processing is the paper's own false-positive control (P9).
- **Applying flags to the star-history series (ADR-032.3):** star-history has no identities, so flags cannot be joined to it. Flagged stars are counted in the identity-level star events of the window (basis and `coverage_ratio` as in §1.2; actors pseudonymised with the same keyed hash in every source) and subtracted from the star-history day counts, like `bot_filtered`. The same over- and under-removal caveats apply, so `starscout_filtered` is tagged `estimated`.
- **Where identities come from now.** Per-repo Events for tracked cases from the start of tracking (TM-33; LQ-29 conditions: pseudonymised at ingest, person-level rows ≤ 30 days, never a stargazer list); GH Archive (~2 % of stars in 2026) before that. OpenDigger is off (TM-32, GAP pending LQ-28). The stargazers API is closed. LQ-29's default also blocks a StarScout-style reproduction on identities beyond the tracked set and cross-repo stargazer graphs, so the lockstep signature can only compare tracked repos, and the low-activity signature relies on GH Archive's degraded activity data.
- **`campaign_flag`** ∈ {`true`, `false`, `unknown`}. It is `true` when the campaign rule is met on the observed events. It is `false` only when identity-level data for the months the rule looks at passes the class-eligibility conditions of §1.2 and no month qualifies. Otherwise it is `unknown`, and so is `starscout_filtered`. The all-time share criterion (> 10 % of all-time stars) uses star-history's all-time total as denominator.
- **Defaults (v0.2.0, ADR-035, amending ADR-020):** classes use the **`raw`** star-history series for every case. v0.1.0 used `starscout_filtered`, which ADR-032 makes `unknown` for most windows (O11, closed). The filtered series are a **pre-registered sensitivity analysis** (`sensitivity_series: [bot_filtered, starscout_filtered]` in the thresholds JSON):
  - **Eligibility:** a case enters the sensitivity run for a variant only if that variant is class-eligible for the window (§1.2: event data covers every day of the window, no window overflow, `coverage_ratio ≥ 0.90`). Ineligible cases are counted, never imputed.
  - **Method:** `A30`, `V90` and `P30` are recomputed on the variant (percentiles within the same cell, over the cases eligible for that variant) and the class rules re-run. A case whose class flips is flagged `sensitive_to_fake_star_filter` (LR §2.3 step 5); class-level findings report the flip count and rate.
  - **Campaign report:** known fake-star campaigns (`campaign_flag`, below) are reported per stratum (category, cohort quarter, entry path) as counts of `true` / `false` / `unknown`. A flagged case stays classed on `raw`; the flag is shown with it.
  - PRD R3.3 still holds: `raw`, `bot_filtered` and `starscout_filtered` are stored wherever they can be computed.
- **Coverage caveat:** StarScout was designed for GH Archive's full event stream. GH Archive's loss since 2025-05 (ADR-009; ~2 % of stars in 2026, ADR-028) can (a) make accounts look low-activity because their other events were lost (false positives) and (b) break lockstep groups (false negatives). For windows after 2025-05 the filter's validity is **unknown** until the M2-T3 reproduction reports coverage for its window (ADR-009); M2-T3 must also report the share of windows where `starscout_filtered` is `unknown`. Each case records `starscout_version`, `bot_filter_basis`, window `coverage_ratio` and `campaign_flag`. StarScout has no direct precision estimate (LR §7.3 item 2); recall on the Check Point list was 81 % of repos (LR §2.1).
- **Public outputs:** a flag is "suspected", never proof; no repo is named as fake publicly (LR §2.3).

## 5. Outcome classes (§8.2) — `outcome-thresholds v0.2.0`

v0.2.0 (ADR-035) differs from v0.1.0 in one class input only: the star series behind `A30`, `V90` and `P30` is `raw` star-history instead of `starscout_filtered` (§4). Every threshold number, rule, precedence, window and normalization setting is unchanged.

### 5.1 Inputs
| Symbol | Meaning |
|---|---|
| `A30` | Percentile of `att.stars` (`raw` star-history, ADR-035) over `[T, T+30)` in its cell |
| `AD90`, `AD365` | Percentile of `adopt.downloads` (primary ecosystem) over `[T, T+90)`, `[T, T+365)` |
| `CM90` | Percentile of `comm.returning_external_contributors@90` |
| `V90` | `att.star_velocity_7d@90` (`raw`, stars/day over `[T+83d, T+90d)`, i.e. the last 7 endpoint days of the 90-day window, §1.2) |
| `P30` | `att.star_peak_velocity_30` (`raw`) |
| `burst_30` | A velocity detection whose onset is in `[T, T+30d)` (or T is a burst anchor) |
| `burst_any` | A velocity detection with onset in `[T−30d, T+90d)` |
| `launch_signal` | A declared launch or a burst (i.e. the case has an anchor) |

**Zero floor:** a percentile criterion (`≥ 75`, `≥ 90`) is satisfied only if the underlying value is > 0.

### 5.2 Classes (evaluated in precedence order; the first match wins)
1. **`winner`** (final at T+90): `A30 ≥ 90` **and** (`AD90 ≥ 75` **or** `CM90 ≥ 75`).
2. **`short_lived`** (final at T+90): `burst_30` **and** `P30 > 0` **and** `V90 < 0.10 · P30` **and** adoption is flat: `AD90 < 50`; if adoption is `not_applicable`, `CM90 < 50` takes its place.
3. **`attention_only`** (final at T+90): `A30 ≥ 90` and not winner and not short_lived (reached the attention leg, no adoption/community follow-through, without the fast decay).
4. **`slow_riser`** (final at T+365): not `burst_any` **and** `A30 < 90` **and** `AD365 ≥ 75`.
5. **`plateau`** (provisional at T+90, final at T+365): `launch_signal` and none of the above.
6. **`unclassified`** with a reason: `pending`, `small_cohort`, `missing_data`, `no_anchor`.

**"Adoption is flat" (short_lived).** PRD §8.2 does not define "flat". v0 operationalises it as **below the cell median**: `AD90 < 50` (primary-ecosystem downloads percentile at T+90), with `CM90 < 50` in its place when adoption is `not_applicable`. "Flat" is therefore relative to the cohort cell, not a zero or near-zero growth rule; an absolute rule would depend on ecosystem-specific download noise (mirrors, CI). The value 50 is provisional and is on the pilot calibration list (§9 item 2).

**Why `attention_only` does not require `burst_30`.** PRD §8.2 describes attention_only as "a burst without adoption or community follow-through". v0 reads "a burst" as the attention leg of `winner` (`A30 ≥ 90`, top decile of 30-day stars in the cell) and does **not** also require a `velocity-v0` detection (`burst_30`), for three reasons:
1. With the same attention leg, attention_only is exactly "winner's attention without winner's follow-through", so the classes partition the top-attention cases cleanly.
2. `velocity-v0` uses an absolute 100-star floor that is not cohort-relative, and whether a detection exists depends on the screens that found the repo (the watch universe, Search, HN; ADR-032; GH Archive before that, which under-captured stars from 2025-05, ADR-009). Requiring it would make the class depend on screen coverage and on the case's entry path (velocity vs declared launch).
3. A top-decile case without a detected burst (for example a launch-anchored diffuse rise) would otherwise fall to `plateau`, whose PRD definition is "never reached the winner threshold". That case did reach the attention leg.

`burst_30` is stored on every class record. The pilot reports the share of `attention_only` cases without `burst_30` and re-runs the class results with the stricter "`burst_30` required" variant (§9 item 11). Adopting that variant would be a major version change (class definition).

`plateau` is a class. Being a **matched loser** is a role in matching (R4.3), not a class: a winner's matched loser may be `plateau`, `attention_only`, `short_lived` or `slow_riser`. Business metrics play no part in any class.

### 5.3 Edge rules
- **Inclusive thresholds.** `≥` and `<` are applied to unrounded percentiles and velocities.
- **Unknown propagation.** A class is assigned only if it is determined whatever the unknown values could be:
  - `A30` unknown → `unclassified:missing_data`.
  - Winner: one leg `≥ 75` suffices even if the other is unknown. If neither leg qualifies and at least one is `unknown` (not `not_applicable`) → `unclassified:missing_data`.
  - `not_applicable` adoption (no package) counts as "does not qualify", not as unknown.
  - short_lived with `V90` or `P30` unknown → cannot be ruled in or out → `unclassified:missing_data` if the case would otherwise fall to attention_only or plateau.
- **Right-censoring.** Before T+90 matures, every case is `unclassified:pending`, except that a T+30 provisional label `attention_top_decile` (`A30 ≥ 90`) may be shown, clearly marked provisional. `plateau` becomes final only at T+365 (slow_riser check).
- **Percentile unavailable** (cell below 30 at every fallback level) → `unclassified:small_cohort`.
- **Coverage gate (per case, per window; 0.90, unchanged).** Any series built from identity-level events feeds `A30`, `V90` or `P30` only if its `coverage_ratio ≥ 0.90` for the window, measured against star-history day counts for the same endpoint days (§1.2). It applies to (a) an event-archive count standing in for `raw` where star-history cannot serve the window (below the gate, the class input is `unknown`), and (b) the event data behind `bot_filtered` and `starscout_filtered` (eligibility for the fake-star sensitivity run, §1.2, §4; below the gate, the case is ineligible for that run).
- **Class series (v0.2.0, ADR-035).** Classes use `raw` star-history for every case, so a missing filtered series never makes a case `unclassified`. Under v0.1.0 (`starscout_filtered`) almost every retrospective case, and every live case first seen after T, would have been `unclassified:missing_data`, because the filtered series is eligible only for windows covered by per-repo event polling (O11, closed). The filtered series run as the sensitivity analysis in §4.
- **The 0.95 figure now lives in ADR-032's reversal condition, and it gates screening, not scoring.** ADR-012 (superseded) used 0.95 as a global bar for GH Archive becoming the primary *scoring* source. Under ADR-032 the scoring series is star-history, and no event archive becomes the scoring series again (for OpenDigger, TM-32 forbids it). ADR-032's 0.95 decides whether GH Archive or OpenDigger (if cleared) may become the primary **screen** again, through a new ADR. That coverage is **global** and must be sampled **from the watch universe `U` or from star-history counts, never from the mirror under test**, so that the source's misses can be observed (detection-replan §7.4, §8 M1). The per-case 0.90 gate is a different decision: whether one case's event data is complete enough to stand in for, or filter, that case's star-history series; each such value is tagged `estimated` so the residual loss stays visible. The gap between 0.90 and 0.95 still keeps a source from flipping on noise near one threshold. 0.90 is calibrated in the pilot (§9 item 5); 0.95 changes only by ADR.
- **Several cases per repo.** Each case is classified on its own T.
- **Recording.** Every class record stores `outcome_thresholds_version`, the SHA-256 of the thresholds JSON, the inputs with their tags, the normalization cells used, the universe version and the run id.

### 5.4 Calibration and change control
- v0.2.0 thresholds are **provisional** (v0.1.0 is superseded and kept as a record). They may be changed **only in the M4 pilot**, and only on the pilot's calibration data, before any held-out outcomes are looked at. The pilot output is frozen as `outcome-thresholds v1.0.0`, hashed and referenced from the pre-registration (`docs/preregistration/`).
- After v1.0.0, **any change made after seeing outcomes requires an ADR in `ops/DECISIONS.md` and a re-run on held-out data**; the report shows results under both versions. (analyst rule; WORK_ORDER §6.)
- Held-out set: cases whose `SHA-256(salt + case_id)` mod 100 < 30, salt `pigtail-outcome-holdout-v1` (fixed now, before any data). Held-out cases are never used to tune thresholds.
- Versioning: patch = wording/doc only; minor = numeric threshold change; major = change of inputs, class definitions or precedence.
- **Before 1.0.0** (ADR-035): `1.0.0` is reserved for the pilot freeze, so a change of class inputs, class definitions or precedence made before the freeze bumps the **minor** version (0.1.0 → 0.2.0 for the class series change), not the major. It still needs an ADR and dated amendments to the pre-registrations that pin the old version, stating whether any outcome data had been seen.

## 6. No composite score (PRD §5.3)

pigtail computes **no composite outcome score**: no weighted sum, index, or single ranking across attention, adoption, community and business. Each dimension is scored, stored and shown separately with its verification tag and source. The outcome classes are rule-based labels built from separately reported dimensions, used for stratification and matching; they are not a score, and every class record carries the dimension values that produced it. Adding a composite later would need a new ADR that reverses ADR-000.

## 7. Data-availability matrix

"Depth" = history available for a retrospective case. "Live only" = only from pigtail's first snapshot or poll. Clearance per SM §1 / ADR-010.

| Metric | Primary source (TM) | Clearance | Depth | Tag | Fallback / when gap | In classes v0 |
|---|---|---|---|---|---|---|
| `att.stars` `raw` | GitHub star-history endpoint (TM-33 under TM-02; ADR-032) | CWC | Full history back to creation, daily, current stargazers only (net); days likely US Pacific | verified | GH Archive (TM-01, ~2 % coverage in 2026) only where star-history can't serve the window and coverage ≥ 0.90 → estimated; else unknown | yes under v0.2.0 (`A30`, `V90`, `P30`; ADR-035) |
| `att.stars` `bot_filtered`, `starscout_filtered` | star-history minus flagged stars from identity-level events: per-repo Events for tracked cases (TM-33, LQ-29), GH Archive before | CWC (actor use pending LQ-29) | From the start of tracking; GH Archive ~2 % before; OpenDigger off (TM-32 GAP) | estimated | unknown (never imputed) | no under v0.2.0: fake-star sensitivity run only, eligible only with full-window coverage ≥ 0.90 (v0.1.0 classed on `starscout_filtered`) |
| `att.hn_points`, `att.hn_comments` | HN Algolia (TM-03) | CWC (LQ-6) | Since 2006-10-09 (observed) | verified (as of fetch) | unknown | no |
| `att.hn_front_page_tag` | HN Algolia (TM-03) | CWC (LQ-6) | Since 2006-10-09 | verified (semantics undocumented) | unknown | no |
| `att.hn_frontpage_minutes` | HN Firebase polling (TM-04) | CWC (LQ-6) | Live only | verified / estimated if polling gaps | unknown before polling | no |
| `att.reddit_reach` | Reddit (TM-05) | GAP | — | unknown | unknown | no |
| `att.bsky_*` | Bluesky Jetstream + search (TM-06) | CWC | Live; search depth unverified | verified | unknown | no |
| `adopt.downloads` npm | npm API (TM-08) | CLEARED | Daily since 2015-01-10 | verified (noisy) | unknown | yes |
| `adopt.downloads` PyPI | BigQuery (TM-07) | CWC | Complete since 2018-07-26 | verified (noisy) | unknown before 2018-07-26 | yes |
| `adopt.downloads` crates | crates.io archive (TM-09) | CWC | Daily since Nov 2014 | verified | unknown | yes |
| `adopt.downloads` Homebrew | formulae.brew.sh (TM-10) | CWC | Live only (rolling 30/90/365) | verified | unknown | yes (when live) |
| `adopt.downloads` Docker Hub | Hub API (TM-11) | CWC | Live only (lifetime counter diffs) | verified | unknown | yes (when live) |
| `adopt.downloads` other ecosystems | none audited | — | — | unknown | unknown | unknown leg |
| `adopt.dependents` | deps.dev (TM-12) | CWC | BigQuery snapshots, start unverified; npm/Cargo/Maven/PyPI only | verified | GitHub dependents page GAP (TM-26) → unknown | no |
| `comm.returning_external_contributors` | GitHub API PRs (TM-02) | CWC | Full history | verified | GH Archive (TM-01) merged-PR events — to be checked (M3-T0) → estimated | yes (`CM90`) |
| `comm.external_activity` | GitHub API (TM-02) | CWC | Full history | verified | GH Archive (TM-01) → estimated | no |
| `comm.discord_members` | Discord invite API (TM-27) | CWC, off until LQ-21 | Live only | estimated | unknown | no |
| `comm.slack_members` | none (TM-28) | GAP | — | unknown | self_reported via project admin | no |
| `biz.mrr` | TrustMRR (TM-23) | GAP | — | unknown | self_reported from cleared sources | no |
| `biz.funding` | Press, manual (TM-31) | CWC (manual) | Operator-entered | self_reported | Crunchbase BYO licence (TM-24) → verified, internal only; YC directory GAP | no |
| `biz.pricing_page` | Wayback CDX (TM-13) | CWC, off for commercial until LQ-15 | Since 1996, per URL | verified | Direct fetch per TM-29; else unknown | no |
| `biz.hiring_hn_posts` | HN APIs (TM-30) | CWC (LQ-6) | Monthly since 2011 | verified | unknown | no |
| `biz.careers_roles` | Careers pages (TM-29) | CWC per site | Via Wayback, per URL | verified | unknown | no |

CWC = CLEARED-WITH-CONDITIONS.

## 8. Open questions

- **O1 — §9.1 test 1 target.** The forecasting test predicts "the 30-day outcome class", but §8.2 classes need T+90 data (adoption/community). Options: (a) redefine the target as the provisional `attention_top_decile` label at T+30; (b) predict the T+90 class from day-7 data. Needs an ADR before pre-registration (M4-T2).
- **O2 — `slow_riser` uses adoption only** (PRD wording). Repos without a package (self-hosted apps, lists) can never be slow risers. Should community (`CM365 ≥ 75`) be a second route?
- **O3 — star-history cost for Tier 1 (updated for ADR-032).** Full histories cost 1–33 core requests per repo (30 weeks per request), so 5k–165k requests for 5,000 repos, about 1–33 h at the full core budget; the R4.1 24-month backfill is about 0.5–2 M requests, 4–17 days at 100 % of one token (detection-replan §6.2). Feasible as a background job; the Tier 1 age distribution decides the real figure.
- **O4 — `burst` with low `A30` and high later adoption** falls into `plateau` (slow_riser requires no burst). Is that intended?
- **O5 — M3-T0 outcome.** If GH Archive still has complete merged-PR events before 2025-05, GH Archive could be primary for older cohorts. This spec keeps the API primary for all cohorts for consistency.
- **O6 — Primary-ecosystem rule** for multi-ecosystem repos (e.g. a Rust core with Python bindings) may pick the less-used ecosystem.
- **O7 — Front page = rank ≤ 30** is unverified.
- **O8 — Universe-relative percentiles.** The percentile depends on how many non-winning launch signals R4.1 captures (Product Hunt is a GAP, SM §2.16), so the effective bar may drift by cohort.
- **O9 — deps.dev BigQuery history** (start date, frequency) must be checked before B2 can be used retrospectively.
- **O10 — Star-history day zone and completeness** (replaces the stargazers-API truncation question, which no longer applies). Confirm the day zone across the DST change on 2026-11-01 (detection-replan §8 M2); check that the endpoint returns full history for the largest repos in the universe; check whether stars from spam-flagged or suspended accounts are excluded (detection-replan §10).
- **O11 — Class series under ADR-032. Closed: resolved by ADR-035 (2026-09-25, before any outcome data).** v0.1.0 classed on `starscout_filtered`, which is `unknown` for most windows under ADR-032, so the pilot as pre-registered could not have classified its cases. Options were (a) keep it, (b) class on `raw` star-history for every case with the filtered series as a sensitivity run where eligible, (c) mix series per case (rejected: one cell would compare two series). ADR-035 adopted (b) as `outcome-thresholds v0.2.0` (§4, §5, §5.4), with dated amendments to both pre-registrations (`docs/preregistration/2026-09-25-pilot-amendment-1.md`, `2026-09-25-forecasting-test-amendment-1.md`). Reversal (ADR-035): class on the filtered series again once per-repo event coverage makes it eligible for ≥ 90 % of cases.

## 9. What the M4 pilot must calibrate

1. Base rates of each class under v0.2.0 on the pilot universe, and whether `winner` is ~top-decile-sized as intended.
2. The attention (90), adoption/community (75) and decay (0.10 · P30) thresholds; the "adoption is flat" operationalisation (`AD90 < 50`, below the cell median, with `CM90 < 50` when adoption is `not_applicable`); the zero floor.
3. The minimum cell size (30) and the fallback hierarchy: cell sizes per (category, quarter) and the share of `small_cohort`.
4. The T-anchor look-back (30 days) and the burst-onset rule, against human-coded launch dates.
5. The per-case coverage gate (0.90) against measured coverage ratios of identity-level event data versus star-history (ADR-009, ADR-032, M1-T16 with the star-history method).
6. The stock-metric tolerance (`max(1 d, 0.1·k)`) and `settle_lag` (3 d) per source.
7. The C1 span rule (≥ 30 days) and the base rate of returning external contributors at T+90 (zero inflation).
8. Category taxonomy: coverage, α of the LLM assignment, merge/split of small categories.
9. Share of `unclassified:missing_data` by category and cohort (a high share means the class system is not usable for that stratum).
10. The raw vs filtered class flip rate (`bot_filtered` and `starscout_filtered`, on the cases eligible for each; §4), and the per-stratum StarScout campaign-flag counts.
11. The `attention_only` burst choice (§5.2): the share of `attention_only` cases without `burst_30`, and the class results with `burst_30` required.
12. Star-history effects (ADR-032): the share of cases whose filtered star series is eligible for the sensitivity run (ADR-035), by cohort and entry path; how much `fetch_lag_days` (survivor loss) differs between live and backfilled cases in the same cell; the share of anchors with `day` onset precision; and whether the day mapping (§1.2) moves any case across a class threshold when shifted by one day.

Calibration happens once, on pilot data, and produces `outcome-thresholds v1.0.0` (§5.4).

## Changelog
- 2026-09-25 — v0.1.0 draft (M3-T1).
- 2026-09-25 — fixes after verifier M3 round 1: `botfilter.py` and `velocity.py` marked "M1, pending merge"; H2 Q4/Q5/Q12 references replaced by LQ-6, LQ-15 and LQ-21 (§1.2, §1.4, §1.5, §7); "adoption is flat" (`AD90 < 50`) and the `attention_only` burst choice documented (§5.2) and added to the pilot list (§9 items 2 and 11); ADR-012's 0.95 vs the 0.90 gate explained (§5.3). The thresholds JSON gains `burst_detection` (`velocity-v0`: 100 stars in 48 h, 3σ, per PRD R1.1) and `fake_star_filter` (`starscout-v0`: n = 50, m = 10, Δt = 30 d, ρ = 0.5, campaign level, per §4). These record parameters already fixed in §2.1 and §4; no threshold, input or class changed, so the file stays provisional `outcome-thresholds v0.1.0` and the version number is not bumped.
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — fixes after verifier M3 round 2: header commit updated; thresholds JSON gains `burst_detection.baseline_days` (30) and `fake_star_filter.campaign_rule` (> 50 fake stars/month, > 50 % share, > 10 % of all-time stars), recording values already fixed in §2.1 and §4 (no threshold changed).
- 2026-09-25 — M3-T6, aligned with ADR-032 (supersedes ADR-012). §0 item 2: stargazers API closed, star-history is the scoring source, and the consequence for classes. §1.1: star windows use endpoint days; observation records gain `day_boundary` and `bot_filter_basis`. §1.2 A1 rewritten: star-history endpoint (TM-33) as `raw`, `verified`; the day-mapping rule (the k endpoint days starting at the day containing T; day-precision anchors start on T's UTC date; ≤ 24 h offset recorded as `window_offset_hours`, never split into hours; zone `America/Los_Angeles` inferred, recompute under a new `metric_version` if M2 disproves it); filtered variants only from identity-level events (`repo_events`, `gharchive`; OpenDigger off), `estimated`, never imputed, class-eligible only with full-window coverage ≥ 0.90; GH Archive fallback kept but unreachable at ~2 %. Removed: the stargazers-API source, the 40k-cap note and its truncation rule. Kept, because star-history is also net of current stargazers: the survivor-bias caveat and the earliest-fetch rule, now with `fetch_lag_days`. §2.1: R1.1 on the public net count (hourly GraphQL snapshots, star-history confirmation), bot filter as a confirmation step with `bot_filter_basis`, `coverage_ratio` and `bot_filter_confirmed` per case; onset on net hourly gains, else day precision. §4: flags applied by subtraction from star-history; identity sources and LQ-29 limits; `campaign_flag` may be `unknown`. §5.3: the 0.90 per-case gate now measured against star-history and applied to identity-level event data; the 0.95 figure now comes from ADR-032's reversal condition (screening, sampled from `U` or star-history, not from the mirror) instead of ADR-012. §7 table, O3, O10, new O11 (class series), §9 items 5 and 12 updated. **Version decision:** the thresholds file stays provisional `outcome-thresholds v0.1.0`. No threshold number, class input symbol (`A30`, `V90`, `P30`, …), class rule, precedence or class series changed. What changed is how the star inputs are measured: the quantity is the same net count of current stargazers by star date that ADR-012 assumed, from the only source that still provides it, at day instead of hour resolution. No observation was ever computed under the old source, and every class record stores the file's SHA-256, which tells the two contents apart. That is more than wording (patch) but not a change of inputs in the §5.4 sense (major); we follow the precedent of the round-1 and round-2 entries and do not bump. The material change, moving classes off `starscout_filtered`, **would** be a change of inputs and is left to an ADR (O11). If that ADR is adopted before the pilot, the bump should be to `0.2.0`, not `1.0.0`, because `1.0.0` is reserved for the pilot freeze; §5.4 would need a note that before 1.0.0 an input change bumps the minor digit.
- 2026-09-25 — M3-T8, ADR-035 (amends ADR-020), before any outcome data exists: thresholds bumped to **`outcome-thresholds v0.2.0`** (`schemas/outcome-thresholds/v0.2.0.json`). The class series for `A30`, `V90`, `P30` is `raw` star-history for every case; `bot_filtered` and `starscout_filtered` become a pre-registered sensitivity run (eligible only with full-window event data and `coverage_ratio ≥ 0.90`; class flips flagged `sensitive_to_fake_star_filter`; StarScout campaign flags reported per stratum). Every threshold number, rule, precedence, window and normalization setting is unchanged. v0.1.0 is superseded and kept unchanged as a record (its JSON changelog lacks the round-2 line; v0.2.0's changelog carries it as history). Updated: header, §0 item 2, §1.2 filtered variants, §3.3, §4 defaults, §5 header and §5.1 inputs, §5.2 attention_only note, §5.3 coverage gate and class series, §5.4 (current version; before 1.0.0 a change of class inputs bumps the minor version), §7 rows, §9 items 1, 10, 12; O11 closed. Pre-registration amendments: `docs/preregistration/2026-09-25-pilot-amendment-1.md`, `docs/preregistration/2026-09-25-forecasting-test-amendment-1.md`.
