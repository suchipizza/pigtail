# Outcome model spec, v2 (per-brief outcome sort)

**Version:** 2.1 (2026-09-26) · **Status:** draft for verifier review (M11; v2.1 is the M21 verifier fix for ADR-070.4). v1 (M3-T1, 2026-09-25: global outcome classes, thresholds v0.1.0/v0.2.0, normalization cells, held-out split and pilot calibration) is retired as the way winners and losers are chosen (ADR-047.1–2, ADR-049.8, ADR-049.12); §10 lists what was retired and where the v1 text is.
**Requirements:** PRD v2.2 §5.3 (amended), §8.1, §8.2, §9.2, F3 (R3.1–R3.4, R3.3 as amended by ADR-070.4; R3.5 retired), R4.3, R4.8, R4.9, R18.1, R18.8; ADR-014, ADR-015, ADR-016, ADR-032, ADR-035, ADR-047 (items 1, 2, 7, 8), ADR-049 (items 1, 8, 9, 12), ADR-050 (item 6), ADR-070 (items 1, 4), ADR-074 (items 3, 9).
**Author role:** `analyst` (with researcher discipline: every factual claim about a source cites the source matrix `docs/research/source-matrix.md` (SM) or the literature review `docs/research/literature.md` (LR); anything we could not confirm is marked **unverified**).
**Data version:** none. The spec precedes data. No outcome has been computed or looked at, under v1 or v2.
**Code commit:** `0330bd4` (2026-09-26). Outcome scoring, percentiles and selection are not implemented yet (M13).
**Codebook:** `docs/methodology/codebook.md` v0.4.0 (CB). **Burst code and parameters:** `pigtail.analysis.bursts` (`src/pigtail/analysis/bursts.py`) with `schemas/analysis-params/v1.1.0.json` block `burst` (unchanged from v1.0.0; mirrored by `pigtail.analysis.params`; ADR-051.3). **Star anomaly checks:** `pigtail.analysis.anomaly` (`src/pigtail/analysis/anomaly.py`) with block `anomaly_check`, rule `anomaly-v0`, of the same file (ADR-070.4, ADR-074.9); the file's `fake_star_filter` block (`starscout-v0`) is marked retired and nothing applies it. The category taxonomy formerly in §3.1 of this spec now lives in CB §9.

## 0. Limitations (read first)

1. **Spec before data.** No shortlist, percentile or winner set has been computed. Every numeric default here (horizons, the minimum population, band ladder, calipers) is a v0 design choice, not a finding. It is fixed in the brief version before that brief's outcome sort (§5.8), and for the pilot pre-registered (WORK_ORDER §6).
2. **Percentiles are relative to one brief's shortlist** (ADR-049.8, PRD R3.4). "Top quartile on adoption" means top quartile among that brief's final shortlist, not among all of GitHub, and not among a global universe. The same repo can be a winner in one brief and a loser in another. The report always states the population and its n.
3. **The shortlist shapes the outcome scale.** Whatever discovery and the relevance filter let in, and what the reviewer accepts (R4.5–R4.7), changes every percentile. Shortlist decisions are logged with the brief version (R18.6), and the report states the shortlist size.
4. **Stars come only from the star-history endpoint, per repo** (ADR-032.3, ADR-047.8). It counts current stargazers by star date (net, survivor-biased), at day resolution in an inferred day zone (§1.2). GH Archive is a discovery signal only, never a source for stars, forks, issues or PRs (ADR-047.8). **Star metrics are "unfiltered, anomaly-checked"** (PRD R3.3, ADR-070.4): no star is removed; aggregate anomaly checks flag suspicious spikes and ratios, and the flags are shown with the numbers (§4). The checks are unvalidated heuristics: no precision or recall has been measured (§4.5).
5. **Business outcomes cannot be verified automatically.** TrustMRR, Crunchbase and the YC directory are GAPs (SM §3; ADR-010). Business metrics are `self_reported`, `verified` only for a few observable signals, or `unknown` (§1.5, §5.2).
6. **Several sources have no history** (Homebrew, Docker Hub, Discord invite counts, HN front-page rank). Their metrics exist only for tracked projects from pigtail's first snapshot onward. Retrospective candidates get `unknown`, never an imputed value.
7. **15–25 pairs are not an inferential sample.** Nothing here tests a hypothesis. Winner/loser contrasts are descriptive (CB §7.3), and the sensitivity check (§8) shows how much the winner set depends on the definition.

## 1. Metrics (§8.1)

### 1.1 Conventions for every metric

- **Anchor and offsets.** `T` is the case anchor (§2). An observation point `k ∈ {7, 30, 90, 365}` days.
- **Flow metrics** (stars, downloads, PRs, posts) are counted over the half-open window `[T, T+k)` in UTC unless stated otherwise. **Stars are the main exception:** they come in daily buckets of the star-history endpoint's own calendar, and the window is mapped to those days by the rule in §1.2. A pre-anchor baseline `[T−30d, T)` is stored for every flow metric for reporting lift; it is never used in the outcome sort.
- **Stock metrics** (dependents, community size, pricing page) take the snapshot closest to `T+k` within a tolerance of `max(1 d, 0.1·k)` (±1 d at T+7, ±3 d at T+30, ±9 d at T+90, ±36.5 d at T+365). No snapshot inside the tolerance → `unknown`. No interpolation.
- **No look-ahead.** A value at `T+k` uses only data timestamped before `T+k`. It is computed from the first fetch made at or after `T+k + settle_lag` and is not overwritten by later fetches; later fetches create new versioned observations. `settle_lag` is **3 days for every source**, a fixed measurement default changeable only by ADR (its pilot calibration is withdrawn: `docs/preregistration/2026-09-26-threshold-calibration-amendment-1.md`).
- **Observation record.** Each value is an `outcome_observation` (PRD §7) with at least: `metric_id`, `metric_version`, `case_id`, `anchor` (type and time), `offset_days`, `value`, `unit`, `series_variant` (`raw` is the only value for stars since v2.1; the field is kept for other metrics' variants and so that older records stay readable), `status`, `verification_tag`, `source` (TM id), `coverage` (window start/end, observed fraction, `coverage_ratio` where measured), for stars also `day_boundary` (§1.2), the label `"unfiltered, anomaly-checked"` and a pointer to the case's anomaly report (§4.3), `evidence_ids`, `run_id`, `outcome_model_version`. Values are per repo and anchor, not per brief, so briefs that share a candidate reuse them (R18.4); percentiles and roles are per brief (§3, §5) and record `brief_id`, `brief_version` and the shortlist's data version. The observation schema is written with the brief pipeline (M13); this list is the requirement.
- **Status** (separate from the tag): `observed | pending | unknown | not_applicable`.
  - `pending`: `T+k + settle_lag` is in the future (right-censored, §2.4).
  - `unknown`: the value should exist but no cleared source can provide it for this case/window (GAP source, connector off, no history, incomplete series, coverage below gate). **Never imputed silently.** `value` is `null`.
  - `not_applicable`: the metric does not exist for this repo (e.g. no package in any registry). `value` is `null`.
- **Verification tags** (R3.2, extended with `unknown` per ADR-010):
  - `verified`: directly observed from a cleared source that measures the quantity itself (counts from an official API or dataset), with a snapshot.
  - `estimated`: observed but approximate or incomplete by construction (e.g. Discord's "approximate" counts; HN front-page minutes with coverage gaps, `lower_bound: true`).
  - `self_reported`: a claim by the project or its founders, or press repeating it (SM §2.31), with a snapshot of the claim.
  - `unknown`: no value (status `unknown` or `pending`).
- **Zero is a value.** An observed count of 0 is `observed`/`verified`. Absence of a capture or a post is **not** evidence of absence for boolean business signals (§1.5).

### 1.2 Attention

**A1 `att.stars` — stars gained**
- Definition: net stars gained in the window, i.e. the number of **current** stargazers whose star date falls in the window's days (below). Unit: stars. One series: `raw` star-history, labelled **"unfiltered, anomaly-checked"** (PRD R3.3, ADR-070.4). The `bot_filtered` and `starscout_filtered` variants of v2.0 are retired (below and §4).
- **Primary source (ADR-032.3; replaces ADR-012's stargazers API, which GitHub closed to non-collaborators on 2026-06-30):** GitHub star-history endpoint `GET /repos/{owner}/{repo}/stargazers/history` (TM-33 under TM-02, CLEARED-WITH-CONDITIONS; no personal data). It returns weekly totals with 7 daily net counts per week, most recent first, back to the repo's creation week; `per_page` ≤ 30 weeks (https://docs.github.com/en/rest/activity/starring?apiVersion=2026-03-10#get-repository-star-history, accessed 2026-09-25; detection-replan §0). **Measured:** the sum of all weeks equals the repo's current `stargazers_count` (2 repos), and full history came back for a 250k-star repo, so no page cap applies at that size. `raw` = the star-history daily counts. Tag `verified`.
- **Day mapping (how `[T, T+k)` maps to the endpoint's days).** GitHub documents only that "Week and day boundaries are not guaranteed to align with UTC". Our tests fit **US Pacific days** (America/Los_Angeles) far better than UTC days; this is an **inference** from small samples (detection-replan §0, §7.4), to be confirmed across the DST change on 2026-11-01 (detection-replan §8 M2). The zone is a config value, `star_history_day_tz` (default `America/Los_Angeles`).
  - Let `D(t)` be the endpoint-calendar date that contains the instant `t` in `star_history_day_tz`. For an anchor with `hour` precision, the first day is `D(T)`. For an anchor with `day` precision (a dated launch without a time, stored as 00:00 UTC, §2.1), the first day is the endpoint day whose date equals T's UTC date, because the evidence names a date, not an instant (00:00 UTC is the previous evening in Pacific time).
  - The window `[T, T+k)` is the `k` endpoint days starting at that first day; the pre-anchor baseline `[T−30d, T)` is the 30 endpoint days before it; the `[T+k−7d, T+k)` velocity window is the last 7 of the `k` days.
  - **Misalignment.** For an hour-precision T the day window starts at endpoint midnight `δ = T − start(D(T))` before T (`0 ≤ δ < 24 h`), so it includes up to 24 h of pre-anchor stars and leaves out up to 24 h at the end. Using the day that contains T keeps the anchor day, where launch and burst stars concentrate, inside the window; starting a day later would lose it. If the endpoint's real zone differs from `star_history_day_tz`, `D(T)` can be off by one more day for T near midnight. **Handling:** day buckets are never split or interpolated into hours; each star observation records `day_boundary = {tz, tz_status: inferred|confirmed, window_offset_hours: δ, first_day, last_day}`; if M2 shows a different zone, `star_history_day_tz` is corrected and every star observation is recomputed under a new `metric_version` (a measurement fix, not a threshold change). On DST-switch days an endpoint day is 23 or 25 hours long; that is accepted and not corrected.
  - A day is used only after it has ended in the endpoint's calendar (the current day fills in during the day; responses are cached about 60 s, detection-replan §0). `settle_lag` (3 d) covers this.
- Derived: `att.star_velocity_7d@k` = mean stars/day over the last 7 endpoint days of the window; `att.star_peak_velocity_30` = max over day indices `d ∈ [7, 30]` (days after the first day) of the mean stars/day over days `[d−7, d)` (7-day rolling means on daily endpoint-calendar buckets, so peak and later velocity are comparable).
- **Anomaly checks instead of filtered variants (ADR-070.4).** v2.0 stored `bot_filtered` and `starscout_filtered` variants built from identity-level star events (`WatchEvent.actor`). They are **retired**: StarScout-style methods need every stargazer's identity and activity, GitHub restricted stargazer lists to admins and collaborators on 2026-06-30 (LR §2.4), and migration 0017 dropped the per-repo identity-level event table (`repo_event_actor`), keeping only hourly and daily counts (ADR-074.3). No star is subtracted from `raw`. Instead every candidate's star series is run through the aggregate anomaly checks of §4, and the flags are reported next to its star metrics. Star values are never imputed, and a flag never changes a star value.
- **No fallback for `raw`.** Where star-history can't serve the window (for example the repo has since been deleted), `att.stars` is `unknown`. The v1 GH Archive fallback is removed (ADR-047.8). OpenDigger is never a scoring series (TM-32 condition), even if cleared.
- Known biases:
  - **Net, current stargazers only (survivor bias).** Star-history counts current stargazers bucketed by star date. Un-starred and deleted accounts vanish from past days, including fake accounts that GitHub later deletes (StarScout reports 57 % of flagged accounts deleted by Jan 2025, LR §2.1), so `raw` is already partly de-faked. How much of a campaign survives in `raw` at fetch time is **unknown**. Whether stars from spam-flagged or suspended (not deleted) accounts are excluded is **unverified** (detection-replan §10).
  - **Values drift down as fetch time moves on.** The scored value is the earliest fetch after `T+k+settle_lag` (§1.1); later fetches are new versioned observations. Each observation records `fetched_at` and `fetch_lag_days` = days from `T+k` to the fetch. Retrospective (backfilled) cases are fetched months or years after `T+k`, so their survivor loss is larger than for live cases, and larger for older cohorts. Within a brief, candidates are fetched in the same backfill, so lags differ mainly with T: older launches have lost more. Each report states the range of `fetch_lag_days` among winners and among matched losers, and the matching on launch quarter (§5.6) keeps pairs at similar lags.
  - **Day, not hour, resolution** for every candidate, so that all candidates in a brief use the same series. No hourly star-count cache exists: the watch list's hourly snapshots (`repo_count_snapshot`, ADR-032.1) were deleted when migration 0014 dropped the table (ADR-047.6, ADR-051.1). Hourly snapshots, if launch mode ever collects them (ADR-048.2, ADR-049.1; M14), would refine burst onsets only (§2.1).
  - Cost: 1 core REST request per 30 weeks of history, so 1–33 requests per repo for a full history (detection-replan §2.2, §6.2). Budgeted under ADR-032.4 (one token).

**A2 `att.hn_*` — Hacker News**
- Matching: HN stories whose URL points to the repo URL or the project's own domain, or whose title names the repo (`owner/name` or the project name as recorded at T), with `created_at ∈ [T−7d, T+k)`. The 7-day lead catches the launch post when T is a burst. Matching rules are versioned in the codebook.
- `att.hn_points@k` = max `points` over matched stories; `att.hn_comments@k` = sum of `num_comments`. Source: HN Algolia (TM-03, CLEARED-WITH-CONDITIONS, pending LQ-6 (was H2 Q4) for commercial operators). Depth: from 2006-10-09 (observed, SM §2.3). Tag `verified`. Bias: values are **as of fetch**, not as of `T+k`; stories created long before the fetch are treated as final. The ~1,000-hit cap per query (observed, undocumented) requires windowed queries.
- `att.hn_front_page_tag@k` = whether any matched story carries Algolia's `front_page` tag. Tag `verified` as an observed field; its exact semantics are undocumented (SM §2.3), so it is descriptive only.
- `att.hn_frontpage_minutes@k` = the minutes that matched stories spent at rank ≤ 30 in `topstories` within `[T, T+k)`, measured from pigtail's own rank polls. Source: HN Firebase, own polling at ≤ 1/min (TM-04; default interval 5 min). Implemented in `src/pigtail/capture/hn_frontpage.py` (M1-T22, ADR-040.2), with these rules:
  - **Step function.** Each poll's ranks hold until the next poll. Two consecutive polls `p < q` form a **covered** segment when `q − p ≤ 2 ×` the poll interval; a story at rank ≤ 30 at `p` is credited `q − p` minutes.
  - **Gaps.** A gap longer than 2 × the interval is **uncovered**. It isn't counted for any story, and the whole gap is reported as uncovered minutes. Per story, the uncovered minutes are the gaps that began while it was at rank ≤ 30 (minutes it may have lost).
  - **Tail rule.** The segment after the latest poll counts up to the time of computation only if that is within 2 × the interval. Otherwise the whole tail is uncovered (poller down).
  - **Window.** Segments are clipped to `[T, T+k)`. Time in the window before the first poll is recorded as `before_polling_minutes` and is never counted as 0 minutes on the front page.
  - **Tags.** `coverage` = covered minutes ÷ window minutes. Coverage 1 → `verified`. Some but not all minutes covered → `estimated`, `lower_bound: true` (gaps, a down tail, or a window that starts before polling began). No covered minute → `unknown`, value `null`; this includes every window that ends before polling began. **Deviation from §2.4:** a window that starts before polling but is partly covered gets an `estimated` lower bound, not `unknown` for the whole window. The metric is not a default dimension metric (§5.2).
  - **Matching: URL only.** A story matches when its URL normalises to the repo's `github.com/owner/name` (`hn_story.repo_full_name`, set by the rank poller). **Title matching (the A2 matching rule above) isn't built yet, and neither is matching on the project's own domain.** Until they are, the value can undercount stories that link the project site or name the repo only in the title. Each value records the matching rule it used.
  - **Depth: only while pigtail's poller runs.** Since ADR-049.1 the poller runs during every scheduled run (one snapshot of the current front page) and continuously only while a brief or tracked project is in launch mode; rank history outside those windows is lost and reported as uncovered. It cannot be backfilled (LR §7.3 item 1, an inference from the returned fields). For retrospective candidates this metric is almost always `unknown`; the Algolia `front_page` tag is the only front-page evidence for them. The value 30 (one HN page) is an assumption, **unverified** here (O7); it is a config parameter (`max_rank`), as are the interval and the gap factor (2).

**A3 `att.reddit_reach`** — Reddit is a GAP (TM-05). Value `unknown` by default. Only an operator with a signed commercial agreement may enable it (new ADR per ADR-010). Reddit URLs seen in other sources are recorded as links only.

**A4 `att.bsky_*` — Bluesky reach**
- `att.bsky_posts@k` = posts in `[T, T+k)` that link the repo URL or project domain; `att.bsky_engagement@k` = sum of likes + reposts + replies + quotes on those posts, as of fetch; `att.bsky_distinct_authors@k` = distinct (pseudonymised) authors. No follower-based reach: it is person-level and not needed.
- Source: Jetstream (live; replay needs a metered key, 36 h live lookback) and authenticated `searchPosts` (TM-06, CLEARED-WITH-CONDITIONS). Search depth is **unverified** (SM §2.6). Before the connector's coverage start → `unknown`.
- Bias: deletions are honoured (R1.5), so counts shrink over time; the scored value is the earliest fetch after `T+k+settle_lag`. X and YouTube are GAPs (TM-14, TM-15), so "social reach" is Bluesky + HN only; patterns and reports must say so (SM §5 item 8). Bluesky and HN mention search stay held by ADR-022 until its controls exist (ADR-049.2).

### 1.3 Adoption

**B1 `adopt.downloads` — registry downloads**
- Package mapping: packages whose source-repo metadata points to the repo (deps.dev project mapping or the registry's repository field), as known at T. Mapping is versioned and stored with evidence.
- **Primary ecosystem** is fixed at T: the ecosystem of the package declared in the repo's root manifest at T; tie → the one matching the repo's primary language; still tied → most dependents at T. The adoption dimension uses only the primary ecosystem (§5.2). Other ecosystems are stored and reported. (Fixing it at T avoids picking the "best" ecosystem after seeing outcomes.)
- Definition: downloads of all mapped packages in the primary ecosystem over `[T, T+k)`. Unit: downloads. Never summed across ecosystems (different counting semantics).
- Sources and depth:
  - npm downloads API (TM-08, **CLEARED**): daily from 2015-01-10; ≤ 18 months per query (chunk); bulk queries exclude scoped packages. Tag `verified`.
  - PyPI BigQuery `file_downloads` (TM-07, CLEARED-WITH-CONDITIONS, CC BY 4.0 attribution): complete from 2018-07-26; earlier rows under-counted ~10× → windows before 2018-07-26 are `unknown`. Tag `verified`.
  - crates.io version-downloads archive (TM-09, CLEARED-WITH-CONDITIONS): daily from Nov 2014. Tag `verified`.
  - Homebrew (TM-10): rolling 30/90/365-day installs only, no history. `k=30/90/365` values can be read directly from a snapshot taken at `T+k` (within tolerance); `k=7` → `unknown`. Only from pigtail's first snapshot. Tag `verified`.
  - Docker Hub (TM-11): lifetime `pull_count` only. Flow = difference of two own snapshots at T and `T+k` (each within tolerance). No snapshot at T → `unknown`. Tag `verified`.
  - Ecosystems with no download data source in the matrix (e.g. Go, Maven) → `unknown` (not `not_applicable`: the repo has adoption, we just cannot see it).
  - No package in any registry → `not_applicable`.
- Biases: mirrors, caches and CI inflate counts (SM §2.7; PRD "verified (noisy)"); download counts are not comparable across ecosystems; Homebrew/Docker Hub windows exist only for tracked projects, so retrospective self-hosted apps are systematically `unknown` on downloads (a brief in such a neighbourhood should choose another adoption metric or treat adoption as `if_not_applicable: skip`, §5.3).

**B2 `adopt.dependents` — dependents (stock)**
- Definition: number of distinct packages that depend on any mapped package (direct dependents if the source separates them; field names to be confirmed at connector build, **unverified**), at `T+k`.
- Primary: deps.dev (TM-12, CLEARED-WITH-CONDITIONS; CC-BY 4.0): dependents only in v3alpha and only for npm, Cargo, Maven, PyPI. History via the BigQuery snapshots dataset, whose start date and frequency are **unverified** (SM §2.12); where no historical snapshot is within tolerance → `unknown`.
- GitHub dependents ("Used by") are a GAP: no API, robots.txt disallows the page (TM-26). Not used.
- Tag `verified`. Bias: narrower ecosystem coverage than GitHub's own graph.
- Not a default dimension metric (a stock, often without history); a brief may choose it as its adoption metric (§5.2), where history exists.

### 1.4 Community

**C1 `comm.returning_external_contributors`**
- PRD: "≥ 2 merged PRs across ≥ 2 months". Operationalised (ADR-016) as: the number of distinct **external** non-bot authors with **≥ 2 merged PRs** whose merge times fall in `[T, T+k)` **and** whose first and last merge in that window are **≥ 30 days apart**. Unit: contributors. Computed at `k = 90, 365`; at `k = 7, 30` it is `not_applicable` (the span rule cannot be met or is degenerate).
- External: PR `author_association` at the time of observation is not `OWNER`, `MEMBER` or `COLLABORATOR`, and the login is not a bot (`bot-filter-v0` rules). Bias: the API reports the *current* association, so contributors later promoted to collaborator are under-counted as external.
- Source: GitHub REST/GraphQL pull requests with `merged_at` (TM-02), per repo (ADR-047.8). Complete for the repo's history. Tag `verified`. Deleted accounts appear as a ghost user and cannot be linked across PRs → slight under-count. **No GH Archive fallback** (ADR-047.8: GH Archive is never a source for issues or PRs).

**C2 `comm.external_activity`**
- `comm.external_issues_opened@k`, `comm.external_prs_opened@k`: issues and PRs opened in `[T, T+k)` by external non-bot authors; `comm.external_authors@k`: distinct such authors. Units: counts.
- Source: GitHub API (TM-02), per repo; no GH Archive fallback (ADR-047.8). Tag `verified`.
- Reported; `comm.external_authors@90` is an alternative community metric a brief may choose (§5.2).

**C3 `comm.discord_members`, `comm.slack_members` (stock)**
- Discord: `approximate_member_count` from the invite endpoint, for invite codes the project itself publishes (TM-27, CLEARED-WITH-CONDITIONS, **off by default until LQ-21 (was H2 Q12)**). No history → only from first snapshot. Tag `estimated` (Discord calls it approximate).
- Slack: GAP (TM-28) → `unknown`; `self_reported` only if the project's admin shares counts.
- Not a dimension metric.

### 1.5 Business (reported; thresholds only by default, §5.2)

| Metric | Definition | Source (clearance) | Tag |
|---|---|---|---|
| `biz.mrr` | Monthly recurring revenue stated for a date ≤ `T+k` | TrustMRR is a GAP (TM-23). Only a public founder statement in a cleared source (HN, Bluesky, project site via TM-29/TM-13), snapshotted | `self_reported` or `unknown` |
| `biz.funding` | Disclosed rounds (amount, date, round, investor orgs) announced ≤ `T+k` | Press, operator-entered (TM-31, manual); Launch HN title (TM-03); Crunchbase only with the operator's own licence (TM-24; internal only, never public mode); YC directory GAP (TM-25) | `self_reported`; `verified` only via a licensed Crunchbase record |
| `biz.pricing_page` | A pricing/paid-plan page on the project's own domain exists at `T+k` | Wayback CDX (TM-13, off until H2 answers LQ-15); direct fetch under TM-29 conditions | `verified` when a capture ≤ `T+k` shows it. No capture → `unknown` (never `false` from absence). `false` only from a live fetch whose site navigation was captured, and only for that fetch date |
| `biz.hiring_hn_posts` | Count of "Who is hiring?" top-level comments matched to the project in months overlapping `[T, T+k)` | HN APIs (TM-30) | `verified` count; 0 is a count of posts, not proof of no hiring |
| `biz.careers_roles` | Open roles on the project's careers page at `T+k` | TM-29 (per-site check, Wayback first) | `verified` when captured; else `unknown` |

The PRD §8.1 lists MRR as "verified"; under current clearances pigtail **cannot** deliver verified MRR (SM §5 item 4). This spec does not promise it.

## 2. Time anchor and observation points (R3.1)

### 2.1 Candidate anchors
- **Declared launch `T_launch`:** the timestamp of a dated evidence record declaring a launch: a Show HN / Launch HN post, a first-party launch post on a cleared source, a launch declared in launch mode (R19.4), or an operator-entered launch with a cited source. Precision is recorded (`hour` if the evidence has a time, else `day`, stored as 00:00 UTC). The v1 announced-launch watchlist (R1.3) is retired.
- **First burst `T_burst`:** the onset of the first burst in the brief's window, detected per repo on star-history days (CB §3.2; code `pigtail.analysis.bursts`, parameters `schemas/analysis-params/v1.0.0.json` block `burst`): rule `velocity-v0`, i.e. endpoint day `d` fires when `n(d−1) + n(d) ≥ 100` net stars and `z ≥ 3` against a baseline of the 30 endpoint days before `d−1`, converted to 48-hour sums, with ADR-027 item 3's √μ floor. A firing whose 48-hour window starts on or before the previous burst's last day (`d−1 ≤` last burst day; the burst's end is the first calm day after it) is ignored, neither a new burst nor a merge; a firing whose window starts on the first calm day or later counts normally (ADR-052, CB v0.3.1 §3.2), so a single-day spike is one burst, not a `multi_peak` one. These are the v1 R1.1 defaults, kept as the anchor rule (R1.1 itself, global breakout detection, is retired; ADR-047.6). The same rule detects bursts of tracked projects for launch mode (R19.5).
  - **Onset.** Onsets are **day-precision** today: no hourly star-count cache survives migration 0014 (ADR-051.1), and no current collector writes hourly counts. If launch mode (ADR-048.2, ADR-049.1; M14) collects hourly count snapshots of the repo that cover the whole 48-hour detection window, the **onset hour** is the earliest hour `h` in the window whose net star gain exceeds `μ_h + 3·√μ_h` (`μ_h + 1` when `μ_h = 0`), with `μ_h = μ_d / 24` and `μ_d` the mean stars per endpoint day over the 30-day baseline (ADR-039.4); if no single hour qualifies, the start of the window; precision `hour`. Otherwise the same rule on endpoint days with `μ_d`: the start of the first qualifying day among `d−1`, `d`, else the start of `d−1`; precision `day`. DST-switch days are not corrected (§1.2). The onset precision is stored with the anchor and decides the day mapping in §1.2. GH Archive is never used for onsets (ADR-047.8).
  - **Bot filter and anomaly flags.** Neither is an anchor condition. A burst is detected on `raw` whatever its anomaly flags (§4); a flagged spike that overlaps a burst is reported with the burst. Where per-repo event counts exist (tracked projects; hourly counts of star events from non-automated and automated accounts, `repo_event_hourly_agg`, migration 0017), the automated-account count is reported as a descriptive number; it is never subtracted from `raw` and never an input to the outcome sort.

### 2.2 Choosing T (per candidate, per ADR-015)
1. A declared launch in `[T_burst − 30 d, T_burst]` → `T = T_launch` (anchor type `launch`). If several, the earliest.
2. A burst without such a launch → `T = T_burst` (anchor type `burst`).
3. A declared launch and no burst in `[T_launch, T_launch + 90 d)` → `T = T_launch`. A later burst is recorded as an event, not as the anchor.
4. No declared launch and no burst in the brief's window → no anchor. The candidate's outcomes are `unknown` (reason `no_anchor`); it can't be sorted, and it counts as "no launch signal" in the report.
5. **Ties:** `T_launch == T_burst` → anchor type `launch`. Two declared launches with the same timestamp → the one with the lower evidence id.
6. **One anchor per candidate per brief.** T must fall inside the brief's window (R18.1). A later relaunch inside the window is recorded as an event (CB §3.6); it doesn't create a second candidate.

Rationale (unchanged from v1): taking the launch when it precedes the burst keeps the launch-day attention inside the windows and gives winners and losers the same kind of anchor (a launch signal). Anchoring on bursts alone would select on the outcome.

### 2.3 Observation points
T+7, T+30, T+90, T+365 days (R3.1). Windows and tolerances follow §1.1. All times UTC, hourly resolution for flows, except stars, which use star-history endpoint days (§1.2). Each dimension's metric is read at its horizon (§5.2).

### 2.4 Right-censoring and truncation
- **Right-censoring.** If `now < T+k + settle_lag`, the observation at `k` is `pending`; nothing is extrapolated. A candidate that is `pending` on a dimension the success definition uses can't be sorted on it (§5.4) and is counted. With a 12–18-month window and a T+90 horizon, candidates launched in the last ~3 months before the run are `pending` (open issue O15).
- **Left truncation (source starts after T).** If a source's coverage starts after the window starts (HN rank polling, Homebrew, Docker Hub, Discord, the Bluesky connector's start, PyPI before 2018-07-26), the metric is `unknown` for that window. For the anomaly checks, an activity channel whose source doesn't cover a day treats that day as unknown, not zero (§4.2). Coverage windows are per-source metadata (R17.3; SM §5 item 7).
- **Short baselines.** A repo created less than 30 days before T has a `partial` or `none` burst baseline (`baseline_quality`); its pre-anchor baseline windows are marked partial.
- **Shortlist maturity.** Percentiles at horizon k are computed over the candidates whose value is `observed` (§3). Candidates still `pending` are left out of the population and counted; when they mature, a re-run recomputes the percentiles and reports what changed (R18.4, R20.3).

## 3. Percentiles within the brief's shortlist (R3.4; replaces the v1 normalization cells)

- **Reference population.** The brief version's **final shortlist** (after the review of R4.7), restricted to candidates with an anchor (§2.2). ADR-018's (category, quarter) cells, their fallbacks and `min_cell_n` are retired (ADR-049.8).
- **Per metric and horizon**, the population is the reference candidates whose value has status `observed`. `unknown`, `pending` and `not_applicable` values are left out; their counts are reported next to every percentile, and a population where more than 50 % of the reference candidates are `unknown` is flagged.
- **Adoption downloads** are compared **within the primary ecosystem**: the population for `adopt.downloads` is the reference candidates with the same primary ecosystem (§1.3), because counts aren't comparable across ecosystems. A brief in a single-ecosystem neighbourhood loses nothing; a mixed one has several smaller populations.
- **Mid-rank percentile:** `p = 100 · (n_below + 0.5 · n_equal) / n`, where `n_equal` counts the candidate itself. Ties share one percentile; no random tie-breaking.
- **Minimum population (v0 design choice): n ≥ 20.** Below it the percentile is `unknown`, reason `small_population`; there is no fallback. A brief may raise the minimum before its outcome sort, never lower it after. The report states n for every population used (R3.4).
- **Zero floor.** A percentile threshold (§5.3) is met only if the underlying value is > 0.
- **Series.** Stars use `raw` star-history (ADR-035), labelled "unfiltered, anomaly-checked" (§4). Anomaly-flagged candidates stay in the population; sensitivity alternative D (§8.1) recomputes percentiles without them.
- **No held-out split.** Per-brief percentiles use every reference candidate. ADR-019's split, H-eval and H-sealed are superseded (ADR-049.5). Protection against choosing definitions by their results comes from fixing the success definition and the sensitivity alternatives before the outcome sort (§5.8) and from the sensitivity check (§8).
- **Recording.** Every percentile stores the brief id and version, the population's n and data version, the metric, horizon and series, and `outcome_model_version`.

## 4. Star anomaly checks (R3.3 as amended by ADR-070.4; replaces fake-star filtering)

### 4.1 Why there is no fake-star filter
- StarScout (He et al., ICSE 2026; LR §2.1) and every other account-level method (LR §2.2) classify **individual stargazer accounts**: an account with one star and almost no other activity, or groups of accounts that star the same repos in lockstep. That needs the identity and activity of every stargazer of the repo.
- GitHub restricted the List stargazers endpoint to admins and collaborators on 2026-06-30 (LR §2.4). The star-history endpoint that pigtail uses (§1.2) returns counts without identities. Migration 0017 dropped the per-repo identity-level event table and keeps only hourly and daily counts (ADR-066.1, ADR-074.3), and GH Archive is not a star source (ADR-047.8). So for repos the operator doesn't own, no account-level method can run (ADR-070.4).
- **Consequence:** star metrics are **unfiltered**. Every star metric, percentile and chart that uses stars carries the label **"unfiltered, anomaly-checked"** (PRD R3.3; `ANOMALY.label` in `pigtail.analysis.params`). The v2.0 series `bot_filtered` and `starscout_filtered`, the `campaign_flag` and the `starscout-v0` parameters are retired (§4.6).

### 4.2 What is checked (rule `anomaly-v0`, `schemas/analysis-params/v1.1.0.json` block `anomaly_check`)
The checks look only at a repo's **aggregate daily counts**: `raw` star-history days (§1.2) and daily counts on four activity channels. Code: `pigtail.analysis.anomaly` (pure functions; `check_population` runs both checks over a brief's candidates). Every number below is a v0 design choice, not a finding (§0 item 1).

- **Activity channels** (`channels`), each a daily count on the same endpoint days (wiring in M13; the channel definitions here are the requirement):
  - `forks`: new forks per day (GitHub API, TM-02; counts only).
  - `issues`: issues opened per day by non-bot authors (GitHub API, TM-02; `bot-filter-v0`), external or not.
  - `downloads`: daily downloads in the primary ecosystem (§1.3), only where a daily source exists (npm, PyPI from 2018-07-26, crates.io). Homebrew and Docker Hub have no daily history, so the channel is not used for them.
  - `mentions`: captured external items that link or name the repo (the §1.2 A2/A4 matching rules), per day, only on days a mention source covers. Held or GAP channels (ADR-010, ADR-073.2) contribute nothing.
  - **A day that is absent is unknown, not zero.** A channel with no known day in a spike's window is not used for that spike.
- **Check 1, spike without matching activity (`spike_no_activity`).**
  - Endpoint day `d` is a spike day when `n(d) ≥ 50` net stars (`min_spike_stars`) and `(n(d) − mean) / σ ≥ 3` (`spike_sigma`), with `mean` over the known days within the 30 calendar days before `d` (a channel with no known baseline day has an expected count of 0) (`baseline_days`; at least 14 known, `min_baseline_days`, else `d` cannot spike) and `σ = max(sample SD, √mean, 1)` (the √μ floor of ADR-027 item 3; `min_sigma` = 1). Consecutive spike days form one spike.
  - A channel **matches** a spike when, over the window from 1 day before its first day to 3 days after its last (`window_before_days`, `window_after_days`), the channel's count is at least 2× its expected count (`min_activity_lift`; expected = the channel's mean daily count over the 30 days before the window × the number of known window days) **and** exceeds it by at least 3 forks, 3 issues, 100 downloads or 1 mention (`min_activity_excess`).
  - A spike checked against at least one channel, with **no** channel matching, is flagged. A spike no channel could be checked against is `unchecked` and **never flagged**.
- **Check 2, odd stars-to-activity ratio (`ratio_outlier`).**
  - `ratio = stars / (forks + issues + 1)` over the days passed to the check (`ratio_channels`). A candidate is judged only if it has at least 200 stars in those days (`ratio_min_stars`) and known days on both ratio channels.
  - With at least 8 judged candidates in the brief (`ratio_min_population`), a candidate is flagged when `log10(ratio)` has a robust z ≥ 3.5 (`ratio_robust_z`; median and MAD × 1.4826) among them. With fewer, it is flagged when the ratio exceeds 50 (`ratio_max_stars_per_activity`). The population is the brief's final shortlist with anchors (§3), so the same repo can be flagged in one brief and not in another.
- **Input window per candidate (v2.1 design choice; fixed in the brief version before the outcome sort, §5.8).** Stars and channels on the endpoint days `[T − 60 d, T + k_max)`, where `k_max` is the longest star horizon the brief's success definition uses (30 by default, §5.2). This gives every day from `T − 30 d` on a full 30-day baseline. The ratio is computed over the same days, which have the same length for every candidate in the brief, so ratios are comparable. A candidate's case-level flags (§4.3) are the spike flags whose spike overlaps `[T − 30 d, T + k_max)` plus any ratio flag; earlier spikes are reported but don't flag the case.

### 4.3 What is recorded per candidate
- The check's report (`AnomalyReport.to_dict()`): `label` ("unfiltered, anomaly-checked"), `rule_version` (`anomaly-v0`), `params_version` (`1.1.0`), `status`, `flagged`, `flags` (kind, dates, detail), `spikes` (dates, stars, baseline mean, max z, channels checked and matching), `ratio`, `stars_total`, plus the input window, the brief id and version and the data version.
- `status` ∈ {`no_spikes`, `checked`, `partially_checked`, `unchecked`}: whether each spike could be compared with at least one channel. It describes Check 1 only; whether Check 2 judged the candidate is recorded separately (`ratio` null, or below `ratio_min_stars`).
- **`star_anomaly_flag`** ∈ {`true`, `false`, `unknown`} (case level; used by codebook §2.4 and seed MC-12):
  - `true`: at least one case-level flag (§4.2 window).
  - `false`: no case-level flag, `status` is `no_spikes` or `checked` for the spikes in the window, **and** Check 2 either judged the candidate or couldn't only because it had fewer than `ratio_min_stars` stars.
  - `unknown`: otherwise (an unchecked or partly checked spike, a missing ratio channel, or no star-history for the window).
- Flags are reported **per case** wherever its star metrics appear (the report, the D1 star lane, the matched-pair table), and per brief as counts of `true` / `false` / `unknown` and of each `status`, among winners and among matched losers separately.

### 4.4 How flags are used
- **Never as a filter.** A flag never changes a star value, a percentile, a threshold decision (§5.3), an anchor (§2.1) or a rank. A flagged candidate stays sorted on `raw`, with its flag shown.
- **Sensitivity alternative D** (§8.1) recomputes the outcome sort without the flagged candidates.
- **Coding:** the codebook's manipulation flag `star_anomaly_flagged` and seed hypothesis MC-12 are derived from `star_anomaly_flag` (CB §2.4, §7.4).
- **Wording:** a flag is "anomaly-flagged" or "suspected", never proof of fake stars. No repo is called fake in any output (LR §2.3); flags stay in the instance's private reports (ADR-073.1).

### 4.5 Validity limits (read with every flag)
- **Unvalidated heuristics.** `anomaly-v0` was designed for pigtail (ADR-070.4). It has no ground truth, and **no precision or recall has been measured**, for either check or for `star_anomaly_flag`. Its thresholds are ad hoc v0 choices, as the StarScout authors say of their own (LR §2.1). The limits below are reasoning about the design, not measurements (LR §2.4).
- **Likely false positives:** organic spikes driven by channels pigtail can't see (Reddit, X, YouTube and other GAPs; chats; uncaptured newsletters) have no matching mentions, and forks or issues can lag by more than the 3-day window; some kinds of repo (lists, tutorials, collections) plausibly attract stars with few forks or issues, which would raise their ratio.
- **Likely false negatives:** stars bought slowly (below 50 a day or 3σ), campaigns that come with fake forks or issues, campaigns before `T − 30 d`, and candidates with fewer than 200 stars (not ratio-judged). Fake accounts that GitHub has since deleted have already vanished from star-history (§1.2), which hides old campaigns further.
- **Coverage drives the status.** Retrospective candidates often lack mention coverage and daily downloads, so many spikes will be checked against forks and issues only, or be `unchecked`. The report shows the status counts so that a low flag rate isn't read as a clean neighbourhood.
- **Brief-relative.** Check 2 compares candidates within one brief's shortlist; a neighbourhood where most repos are inflated looks normal to it.

### 4.6 Retired (kept for citation only)
- The `bot_filtered` and `starscout_filtered` series, their eligibility gate (full-window event coverage, `coverage_ratio ≥ 0.90`), `bot_filter.basis`, and `campaign_flag`.
- StarScout `starscout-v0` (n = 50, m = 10, Δt = 30 d, ρ = 0.5; campaign rule > 50 fake stars and > 50 % share in a month, > 10 % of all-time stars): kept in `schemas/analysis-params/v1.1.0.json` block `fake_star_filter` with `status: retired` so that older documents stay citable; no code applies it.
- The v2.0 sensitivity alternative "fake-star filter" and the flag `sensitive_to_fake_star_filter` (§8).

## 5. Success definition and outcome sort (R18.8, R4.8; replaces the v1 outcome classes)

### 5.1 What the brief sets (R18.1 `success`)
- one **primary dimension**: `attention`, `adoption`, `community` or `business`;
- **minimum thresholds** on any dimension, the primary included (for example "at least the median on attention");
- optionally **weights** (advanced, §6);
- optionally a non-default **metric** and **horizon** per dimension, from the list in §5.2;
- optionally `if_not_applicable: skip` per dimension (§5.3);
- the number of winners (and losers): default 20, range 15–25 (R18.1 `selection`);
- the **sensitivity alternatives** (§8), defaulting to the set in §8.1.

These are part of the brief version. The schema fields (`metric`, `horizon`, `if_not_applicable`, `sensitivity`) are to be added in M12 (open issue O17).

### 5.2 Dimension metrics (defaults and allowed alternatives)
| Dimension | Default metric (horizon) | Allowed alternatives | Rankable |
|---|---|---|---|
| attention | percentile of `att.stars` `raw` over `[T, T+30 d)` (§1.2) | `att.stars` at 90; `att.hn_points` (as of fetch) | yes |
| adoption | percentile of `adopt.downloads`, primary ecosystem, over `[T, T+90 d)`, within the ecosystem (§3) | downloads at 365; `adopt.dependents` at 90 or 365 (where history exists) | yes |
| community | percentile of `comm.returning_external_contributors@90` (§1.4, ADR-016) | the same at 365; `comm.external_authors` at 90 | yes |
| business | none by default: thresholds only, on **verified** signals (`biz.pricing_page`, `biz.hiring_hn_posts > 0`, `biz.careers_roles > 0`) at 365 | as primary: `biz.verified_signal_count@365` (the number of those three signals that are `verified` true); ties are broken per §5.4 and the report warns that the ranking is coarse | only through the count |

- `self_reported` business values (`biz.mrr`, `biz.funding`) never meet a threshold unless the brief sets `accept_self_reported: true`; then every finding that depends on them is labelled "self-reported" (ADR-021).
- Results are always shown **per dimension** (PRD §5.3), whatever the ranking.

### 5.3 Thresholds
- A threshold is a percentile floor `≥ p` within the brief's shortlist (§3), with `p` on the band ladder {25, 50, 75, 90}. The sensitivity check steps along the extended ladder {none, 25, 50, 75, 90, 95} (§8.1). For business it is a required set of verified signals.
- **Zero floor** (§3): a percentile floor is met only if the value is > 0.
- A value that is `unknown` or `pending` **never** meets a threshold (R18.8). The report says how many candidates that affected, per dimension.
- **Star anomaly flags don't change threshold decisions** (§4.4). A flagged candidate's `raw` star value meets or misses a threshold like any other; the flag is shown next to it, and sensitivity alternative D (§8.1) shows what happens without the flagged candidates.
- `not_applicable` (for example no package in any registry) fails the threshold by default. With `if_not_applicable: skip` on that dimension, the threshold is waived for such candidates, and the report counts them. (v1's substitute of community for adoption in `short_lived` is retired with the classes.)

### 5.4 Qualification and ranking
1. A candidate **qualifies** when it has an anchor and meets every threshold.
2. Qualifiers are ranked by the primary dimension's percentile, descending (or by the composite, §6). A qualifier whose primary value is not `observed`, or whose percentile is `unknown`, is **unrankable** and counted.
3. **Ties** (equal percentiles or composites) are broken by `sha256(brief_id + ":" + brief_version + ":" + repo_id)` ascending, so the order is deterministic for a given brief version and data version (R4.8).

### 5.5 Winners
- The top `N` ranked qualifiers are the **winners** (`N` = `selection.winners`, default 20).
- If fewer than `N` qualify, all rankable qualifiers are winners, and the report says so. If fewer than 15 qualify, the report states "fewer winners than the minimum (15)". Thresholds are **not** loosened automatically. Editing the success definition afterwards creates a new brief version whose report records that it was edited after an outcome sort; for the pilot, that is a deviation and the results are exploratory.
- Rankable qualifiers below rank `N` are **qualified, not selected**. They are neither winners nor losers.

### 5.6 Loser pool and matched losers (R4.3, PRD §8.2)
- **Loser pool:** candidates with an anchor that **fail** at least one threshold on an `observed` value. Candidates that fail only because a value is `unknown` or `pending` are **undetermined**: they are not losers, because the evidence can't say they missed; they are counted.
- **Matching covariates** (all measured at or before T, except `LSM`):
  | Covariate | Definition |
  |---|---|
  | Launch-signal magnitude `LSM` | `log10(1 + raw stars in the first 2 endpoint days of the window)` from star-history (§1.2 day mapping). It overlaps the first days of the attention metric; that is intended (R4.3 matches on launch signal, so pairs differ in follow-through). Unfiltered (§4): the matched-pair table shows each side's `star_anomaly_flag`. |
  | Launch quarter | UTC quarter of T |
  | Repo age at T | `log10(days from repo creation to T)` |
  | Founder audience bucket | Reach band (CB §4.5, r1–r4) of the owner's prior audience: the sum over the owner's **other** public repos of their star-history counts before T (ADR-029.2's proxy). For repos owned by a personal account this is person-level data; until ADR-022's controls allow it (ADR-049.2), the bucket is `unknown` and matched as its own level (open issue O14). |
  | Language | GitHub primary language at T |
- **Algorithm (v0 default; the M13 selection spec may refine it before any outcome sort, never after):** winners in rank order each take the nearest unmatched loser (without replacement), using `d = |ΔLSM|/SD_LSM + |Δlog10 age|/SD_age + |Δaudience band|/SD_band + |Δquarter| + 1[language differs]`, with SDs over winners ∪ loser pool. Calipers: `|ΔLSM| ≤ 0.5 · SD_LSM` and `|Δquarter| ≤ 1`. Ties by the §5.4 hash. A winner with no loser inside the calipers is **unmatched**: it stays a winner, and the report counts it; loser contrasts use matched pairs only (CB §7.1).
- **Balance diagnostics (PRD §9.2):** standardized mean difference per covariate (pooled-SD form, LR [45]; SMD per level for categorical covariates), plus the variance ratio; no p-values. Target |SMD| < 0.25. Where the shortlist can't reach it, the report says so per covariate and labels every loser contrast that depends on it `balance_limited` (ADR-049 item 10, the relaxation question, is with the owner). Re-matching until balance looks good is not allowed.

### 5.7 Determinism and recording
For a given brief version, data version and `outcome_model_version`, qualification, ranking, winners, loser pool and matches are deterministic (R4.8). The run records: every candidate's anchor, dimension values with tags and statuses, percentiles with population n, qualification and the reason for any failure, rank, role (`winner`, `matched_loser`, `qualified_not_selected`, `loser_pool_unmatched`, `undetermined`, `unrankable`, `no_anchor`), matches and distances, balance diagnostics, and the sensitivity results (§8).

### 5.8 The outcome sort is the point of no return
- The **outcome sort** is the first computation of any dimension percentile (§3) on the brief version's final shortlist. Before it, the brief version fixes: the success definition (§5.1), metrics and horizons, thresholds, weights, `if_not_applicable`, the minimum population, the sensitivity alternatives, the matching settings, the codebook version and the pattern hypotheses to check (CB §7.2).
- For the pilot these are **pre-registered** and pushed before the outcome sort (WORK_ORDER §6). The pre-registration is public and must not contain brief content (PRD R18.9, pre-registration README rule 5): brief-specific values are committed as the SHA-256 of a private file (open issue O18).
- Anything changed after the outcome sort is labelled exploratory in that brief's report (analyst rule).

## 6. Weights (advanced option) and no global composite (PRD §5.3 as amended, ADR-047.2, ADR-049.9)

- A brief may set **weights** `w_d ≥ 0` over the rankable dimensions (attention, adoption, community), summing to 1. The composite is `Σ w_d · percentile_d`, used **only to rank qualifiers within that brief** (§5.4). Thresholds still apply first.
- A qualifier with an `unknown` or `pending` value on any weighted dimension is unrankable (counted).
- Business has no percentile and can't be weighted.
- The composite is never shown as an outcome, never compared across briefs, and never stored as a repo attribute. Every report shows each winner's and loser's values **per dimension**. ADR-000's "no composite outcome score" is amended only this far (ADR-049.9).
- The sensitivity check always includes the primary-only ranking and equal weights when weights are used (§8.1).

## 7. Data-availability matrix

"Depth" = history available for a retrospective case. "Live only" = only from pigtail's first snapshot or poll. Clearance per SM §1 / ADR-010.

| Metric | Primary source (TM) | Clearance | Depth | Tag | Fallback / when gap | Default use (§5.2) |
|---|---|---|---|---|---|---|
| `att.stars` `raw` ("unfiltered, anomaly-checked") | GitHub star-history endpoint, per repo (TM-33 under TM-02; ADR-032, ADR-047.8) | CWC | Full history back to creation, daily, current stargazers only (net); days likely US Pacific | verified | unknown (no GH Archive fallback, ADR-047.8) | attention dimension (`@30`); `LSM` for matching |
| Star anomaly checks (`anomaly-v0`; not a metric) | `att.stars` `raw` plus daily forks, issues (TM-02), downloads (TM-07/08/09) and mentions (TM-03, TM-06) | as for each input | Wherever `raw` has the window `[T − 60 d, T + k_max)`; each channel only on days its source covers (unknown days are not zeros) | flags, not a tagged value; label "unfiltered, anomaly-checked"; unvalidated (§4.5) | spike `unchecked` / `star_anomaly_flag` `unknown` | reported per case (§4.3); sensitivity alternative D (§8.1) |
| `att.hn_points`, `att.hn_comments` | HN Algolia (TM-03) | CWC (LQ-6) | Since 2006-10-09 (observed) | verified (as of fetch) | unknown | reported |
| `att.hn_front_page_tag` | HN Algolia (TM-03) | CWC (LQ-6) | Since 2006-10-09 | verified (semantics undocumented) | unknown | reported |
| `att.hn_frontpage_minutes` | HN Firebase polling (TM-04), during runs and launch mode only (ADR-049.1); URL matching only (ADR-040.2) | CWC (LQ-6) | Live only, while the poller runs | verified at coverage 1 / estimated (lower bound) with gaps or a window starting before polling | unknown with no covered minute | reported; alternative attention metric only for tracked projects |
| `att.reddit_reach` | Reddit (TM-05) | GAP | — | unknown | unknown | reported |
| `att.bsky_*` | Bluesky Jetstream + search (TM-06) | CWC | Live; search depth unverified | verified | unknown | reported |
| `adopt.downloads` npm | npm API (TM-08) | CLEARED | Daily since 2015-01-10 | verified (noisy) | unknown | adoption dimension (`@90`, within ecosystem) |
| `adopt.downloads` PyPI | BigQuery (TM-07) | CWC | Complete since 2018-07-26 | verified (noisy) | unknown before 2018-07-26 | adoption dimension (`@90`, within ecosystem) |
| `adopt.downloads` crates | crates.io archive (TM-09) | CWC | Daily since Nov 2014 | verified | unknown | adoption dimension (`@90`, within ecosystem) |
| `adopt.downloads` Homebrew | formulae.brew.sh (TM-10) | CWC | Live only (rolling 30/90/365) | verified | unknown | adoption dimension (`@90`, within ecosystem; tracked projects only) |
| `adopt.downloads` Docker Hub | Hub API (TM-11) | CWC | Live only (lifetime counter diffs) | verified | unknown | adoption dimension (`@90`, within ecosystem; tracked projects only) |
| `adopt.downloads` other ecosystems | none audited | — | — | unknown | unknown | adoption `unknown` |
| `adopt.dependents` | deps.dev (TM-12) | CWC | BigQuery snapshots, start unverified; npm/Cargo/Maven/PyPI only | verified | GitHub dependents page GAP (TM-26) → unknown | alternative adoption metric |
| `comm.returning_external_contributors` | GitHub API PRs (TM-02) | CWC | Full history | verified | unknown (no GH Archive fallback, ADR-047.8) | community dimension (`@90`) |
| `comm.external_activity` | GitHub API (TM-02) | CWC | Full history | verified | unknown (no GH Archive fallback, ADR-047.8) | reported; `comm.external_authors@90` is an alternative community metric |
| `comm.discord_members` | Discord invite API (TM-27) | CWC, off until LQ-21 | Live only | estimated | unknown | reported |
| `comm.slack_members` | none (TM-28) | GAP | — | unknown | self_reported via project admin | reported |
| `biz.mrr` | TrustMRR (TM-23) | GAP | — | unknown | self_reported from cleared sources | business thresholds (verified values only by default) |
| `biz.funding` | Press, manual (TM-31) | CWC (manual) | Operator-entered | self_reported | Crunchbase BYO licence (TM-24) → verified, internal only; YC directory GAP | business thresholds (verified values only by default) |
| `biz.pricing_page` | Wayback CDX (TM-13) | CWC, off for commercial until LQ-15 | Since 1996, per URL | verified | Direct fetch per TM-29; else unknown | business thresholds (verified values only by default) |
| `biz.hiring_hn_posts` | HN APIs (TM-30) | CWC (LQ-6) | Monthly since 2011 | verified | unknown | business thresholds (verified values only by default) |
| `biz.careers_roles` | Careers pages (TM-29) | CWC per site | Via Wayback, per URL | verified | unknown | business thresholds (verified values only by default) |

CWC = CLEARED-WITH-CONDITIONS.


## 8. Sensitivity check of the winner set (R4.9; part of every report)

### 8.1 Alternatives (defaults; fixed in the brief version before the outcome sort)
- **A. Primary swap.** For each other rankable dimension: rank qualifiers by it instead of the primary. All thresholds stay as in the brief.
- **B. Band shift.** Each percentile threshold moved one step looser and one step tighter on the ladder {none, 25, 50, 75, 90, 95}, one threshold at a time; plus all thresholds looser together and all tighter together. (A primary floor, if the brief set one, is included.)
- **C. Weights** (only when the brief uses them): primary-only ranking, and equal weights.
- **D. Exclude anomaly-flagged candidates** (§4; replaces v2.0's "fake-star filter" of ADR-050.6). The candidates with `star_anomaly_flag = true` are removed from the final shortlist, and the whole outcome sort (percentiles, qualification, ranking, winners) is recomputed on the rest. It is run only when the success definition uses a star metric (as the primary dimension, a threshold or a weight); otherwise the report states "D not applicable: no star metric in the success definition". If no candidate is flagged, D is reported as "no flagged candidates" and changes nothing.
  - *Why this alternative.* ADR-050.6 asked whether the winner set depends on possibly inflated star counts. Without identities pigtail cannot tell which stars are fake, so it cannot build a filtered series. Excluding whole flagged candidates asks the same question without pretending to know which stars to remove. Two other options were rejected: subtracting a flagged spike's excess stars would be a filter of unknown accuracy that also removes organic stars from false-positive spikes, contradicting the "unfiltered" label (ADR-070.4); also excluding `unknown` candidates would, with the coverage retrospective cases have, remove most of the shortlist and measure coverage rather than inflation. D is descriptive, like every alternative, and inherits the checks' validity limits (§4.5).
  - *Brief schema.* Brief schemas v1 to v1.2 (`schemas/brief/v1.2.json`, `pigtail.briefs.model.Sensitivity`) still call this value `fake_star_filter`. Until a brief-schema version renames it (proposed: `exclude_anomaly_flagged`; engineer follow-up), `fake_star_filter` in a brief means alternative D as defined here.
- A brief may add alternatives (for example another horizon) before its outcome sort.

### 8.2 What is computed
- For each alternative: the number of qualifiers, the winner set (same `N`), and its Jaccard overlap with the baseline winner set. Losers are not re-matched.
- For each baseline winner and matched loser: its status under every alternative (winner, qualifier, non-qualifier, undetermined, unrankable).
- **Flags:** a baseline winner that is not a winner under at least one alternative, and a baseline matched loser that qualifies under at least one alternative, are flagged **`definition_sensitive`** (with the list of alternatives that change it). Under D, the excluded (flagged) cases are listed as `excluded_anomaly_flagged`, and a remaining case whose status changes is flagged **`sensitive_to_star_anomaly`** instead (it moved because flagged candidates left the population, not because of its own flags). `sensitive_to_star_anomaly` replaces v2.0's `sensitive_to_fake_star_filter`.

### 8.3 What is reported
- The baseline winner set's stability: the minimum and mean Jaccard overlap across alternatives, and the share of winners that stay winners under every alternative.
- The flagged cases (by `case_id` in the private report; counts only in public outputs).
- Every pattern lists the sensitivity flags of the cases it involves (CB §7.1), so a reader can see whether a contrast rests on definition-sensitive cases.
- The sensitivity check never changes the baseline winner set. It is descriptive.

## 9. Open questions

Closed or retired v1 questions keep their numbers: O1 (forecasting target; withdrawn with the forecasting test), O2 and O4 (`slow_riser`, `plateau`; retired with the classes), O3 (Tier 1 cost; Tier 1 cut), O5 (GH Archive merged PRs; GH Archive is never a PR source, ADR-047.8), O8 (universe-relative percentiles; replaced by limitation 3), O11 (class series; closed by ADR-035).

- **O6 — Primary-ecosystem rule** for multi-ecosystem repos (e.g. a Rust core with Python bindings) may pick the less-used ecosystem.
- **O7 — Front page = rank ≤ 30** is unverified.
- **O9 — deps.dev BigQuery history** (start date, frequency) must be checked before `adopt.dependents` can be used retrospectively.
- **O10 — Star-history day zone and completeness.** Confirm the day zone across the DST change on 2026-11-01 (detection-replan §8 M2); check full history for the largest repos in a shortlist; check whether stars from spam-flagged or suspended accounts are excluded (detection-replan §10).
- **O12 — Business as a primary dimension.** `biz.verified_signal_count` is coarse (0–3) and depends on Wayback (off until LQ-15) and careers pages. A neighbourhood where business matters may need an operator-entered, cited metric (ADR-021). Needs a decision before the first brief that picks business.
- **O13 — Minimum population n ≥ 20** is a design choice. With a shortlist of 40–80, adoption populations split by ecosystem may fall below it. Revisit after the pilot.
- **O14 — Founder-audience proxy for personal-account owners** is person-level data (ADR-022, ADR-049.2). Until cleared it is `unknown` for those candidates, which weakens matching on that covariate. Compliance input needed.
- **O15 — Brief window versus horizons.** With a 12–18-month window, candidates launched in the last ~90 days are `pending` at T+90. The brief schema (M12) could default the discovery window's end to 90 days before the run, or report pending candidates separately. Decide in M12.
- **O16 — Outcome visibility during shortlist review.** Reviewers judge relevance (R4.7), but star counts are visible on GitHub pages and could bias accept/reject decisions. The review screen (M13) should hide pigtail's outcome values; the report states this as a limitation.
- **O17 — Brief schema fields.** `success.metric`, `success.horizon`, `success.if_not_applicable`, `success.accept_self_reported`, `success.min_population_n` and `sensitivity` need adding to the brief schema in M12.
- **O19 — Anomaly checks are unvalidated** (§4.5). No precision or recall is known for `anomaly-v0`. A validation needs a labelled set of repos with known campaigns whose star-history still shows them, which pigtail doesn't have; the public ground truth StarScout used (LR [22]) is about accounts and repos, 90.42 % of StarScout-flagged repos had been deleted by January 2025 (LR §2.1), and whether any labelled set still usable with star-history counts exists is **unverified**. Until a validation exists, every flag carries the §4.5 limits, and a change of thresholds is a new `analysis-params` version and ADR, fixed per brief before its outcome sort.
- **O18 — Public pre-registration without brief content.** The pilot's success definition is brief content; the pre-registration can commit its SHA-256 (README rule 5) but then outsiders can't read the definition until the owner approves publishing it (H4). Decide in M15 with the owner.

## 10. Retired in v2 (history)

The v1 text is at `git show archive/global-collection:docs/specs/outcome-model.md` (tag `archive/global-collection`, commit `4237b3a`; identical to the file at `0330bd4`). Retired, with the reason:

| v1 content | Retired by |
|---|---|
| §5 outcome classes (`winner`, `short_lived`, `attention_only`, `slow_riser`, `plateau`, `unclassified`), their inputs (`A30`, `AD90`, `CM90`, `V90`, `P30`, …), precedence and edge rules | ADR-047.1–2, ADR-049.12, PRD R3.5, §8.2 |
| `schemas/outcome-thresholds/v0.1.0.json`, `v0.2.0.json` | ADR-049.12 (files stay in git as history; nothing is classed under them) |
| §3.2–3.4 normalization cells (category, quarter; ecosystem), fallbacks, `min_cell_n` | ADR-049.8 (percentiles within the brief's shortlist, §3) |
| §3.1 category taxonomy | moved to CB §9 (a case descriptor, not a cell) |
| §3.3 and §5.4 held-out split (salt `pigtail-outcome-holdout-v1`, 30 %), H-eval / H-sealed | ADR-049.5 (ADR-019, ADR-039.5 superseded) |
| §5.4 and §9 pilot calibration (K1, K2), diagnostics D1–D12, the v1.0.0 freeze | ADR-049.5; `docs/preregistration/2026-09-26-threshold-calibration-amendment-1.md` |
| §2.1 R1.1 check on watch-list snapshots, bot filter as a case-opening confirmation step, R1.3 announced-launch watchlist | ADR-047.6 (global breakout detection deleted), PRD R1.1 and R1.3 retired |
| §6 "no composite outcome score" (absolute) | amended by ADR-047.2, ADR-049.9 (§6 here) |
| GH Archive as fallback for stars and community metrics, and as a bot-filter basis | ADR-047.8 |
| §5.3 provisional T+30 label `attention_top_decile` | withdrawn with the forecasting test (`docs/preregistration/2026-09-26-forecasting-test-amendment-5.md`) |

Kept from v1 (sections 1, 2, 4 and 7, edited as noted in the changelog): the metrics and their sources, tags and statuses (ADR-014), the star-history day mapping, the anchor rule (ADR-015), the burst rule `velocity-v0` and its onset rule, and the data-availability matrix. Fake-star filtering (v1 and v2.0 §4) is retired in v2.1 and replaced by aggregate anomaly checks (ADR-070.4, §4).

## Changelog

### v2
- 2026-09-26 — **v2.1 (M21 verifier fix; ADR-070.4, ADR-074.9, PRD R3.3 as amended; written before any outcome data).** Per-account fake-star filtering (StarScout-style) is replaced by the aggregate anomaly checks that `pigtail.analysis.anomaly` implements (rule `anomaly-v0`, `schemas/analysis-params/v1.1.0.json` block `anomaly_check`). Header: codebook v0.4.0, analysis-params v1.1.0 (burst block unchanged), anomaly code named. §0 item 4 and §1.1: star metrics labelled "unfiltered, anomaly-checked"; `series_variant` for stars is `raw` only; `bot_filter.basis` and the "no identity-level data" unknown reason removed. §1.2 A1: `bot_filtered` and `starscout_filtered` retired (stargazer lists restricted 2026-06-30; migration 0017 dropped identity-level events, ADR-074.3); anomaly flags reported per case. §2.1: bursts detected on `raw` whatever the flags; automated-account event counts descriptive only. §2.4, §3: filtered-series references removed. §4 rewritten: why there is no filter, the two checks with every threshold, the input window `[T − 60 d, T + k_max)` (new v2.1 design choice), the per-case record and `star_anomaly_flag` (true/false/unknown), how flags are used, validity limits (no precision or recall measured), what is retired. §5.3: flags never change threshold decisions. §5.6: pair table shows flags. §7: filtered-series row replaced by an anomaly-check row. §8.1: alternative D is now "exclude anomaly-flagged candidates" (replacing ADR-050.6's "fake-star filter"; the choice and the rejected options are justified there); §8.2: `sensitive_to_star_anomaly` replaces `sensitive_to_fake_star_filter`, excluded cases listed. §9: new O19. §8.1 notes that brief schemas up to v1.2 still name D `fake_star_filter` (rename proposed as an engineer follow-up). §10: fake-star filtering no longer listed as kept. **Version decision:** minor (2.0 → 2.1). The outcome sort's inputs (`raw` star-history, metrics, horizons, thresholds, percentiles, matching) are unchanged; what changes is a sensitivity alternative, a retired series that no outcome sort ever used (ADR-035), and the per-case reporting. No outcome was computed under v2.0, so nothing is recomputed. ADR-050.6 names "fake-star filter" as the fourth alternative; this version reads it through ADR-070.4, which retires that filter (logging an ADR note on ADR-050.6 is left to the orchestrator).
- 2026-09-26 — **v2.0 (M11; ADR-047, ADR-049).** Written before any outcome data. New §5 (success definition: primary dimension plus minimum thresholds; default metrics and horizons per dimension; qualification, deterministic ranking and tie-break; winners; loser pool with `undetermined` candidates; matching covariates, calipers and balance diagnostics; the outcome sort as the point after which changes are exploratory), §6 (weights as an advanced option, ranking only), §3 (percentiles within the brief's final shortlist, minimum population 20, adoption within ecosystem, no held-out split), §8 (sensitivity check: primary swap, band shift, weights, fake-star filter; `definition_sensitive` flags; winner-set stability). §0 rewritten. §1: GH Archive fallbacks and the `gharchive` bot-filter basis removed (ADR-047.8); `settle_lag` fixed at 3 days (calibration withdrawn); observation records carry `outcome_model_version`; HN front-page minutes only while the poller runs (ADR-049.1); "class" wording replaced. §2: anchors per candidate within the brief window; burst rule kept as the anchor and launch-mode rule, not as R1.1; watchlist and case-opening bot-filter confirmation removed. §4: identities from per-repo events only; sensitivity run on the outcome sort. §7: last column now "Default use (§5.2)"; GH Archive fallbacks removed. §9: open questions renumbered around the retired ones; O12–O18 new. §10 lists what was retired and where the v1 text is. The category taxonomy moved to CB §9.
- 2026-09-26 — **v2.0, M11 verifier fixes (no threshold or metric changed; the burst rule's intent is restored per ADR-052; written before any outcome data).** Header: codebook v0.3.1; the burst code (`pigtail.analysis.bursts`) and parameters file (`schemas/analysis-params/v1.0.0.json`) are named (ADR-051.3). §1.2 A1 and §2.1: no hourly watch-list cache survives migration 0014 (ADR-051.1), so onsets are day-precision unless launch mode collects hourly snapshots (ADR-048.2, ADR-049.1). §2.1: a firing whose 48-hour window starts inside the previous burst is ignored (ADR-052). §4: the StarScout parameters now live in `schemas/analysis-params/v1.0.0.json`.

### v1 (history; section numbers refer to the v1 text)
- 2026-09-25 — v0.1.0 draft (M3-T1).
- 2026-09-25 — fixes after verifier M3 round 1: `botfilter.py` and `velocity.py` marked "M1, pending merge"; H2 Q4/Q5/Q12 references replaced by LQ-6, LQ-15 and LQ-21 (§1.2, §1.4, §1.5, §7); "adoption is flat" (`AD90 < 50`) and the `attention_only` burst choice documented (§5.2) and added to the pilot list (§9 items 2 and 11); ADR-012's 0.95 vs the 0.90 gate explained (§5.3). The thresholds JSON gains `burst_detection` (`velocity-v0`: 100 stars in 48 h, 3σ, per PRD R1.1) and `fake_star_filter` (`starscout-v0`: n = 50, m = 10, Δt = 30 d, ρ = 0.5, campaign level, per §4). These record parameters already fixed in §2.1 and §4; no threshold, input or class changed, so the file stays provisional `outcome-thresholds v0.1.0` and the version number is not bumped.
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — fixes after verifier M3 round 2: header commit updated; thresholds JSON gains `burst_detection.baseline_days` (30) and `fake_star_filter.campaign_rule` (> 50 fake stars/month, > 50 % share, > 10 % of all-time stars), recording values already fixed in §2.1 and §4 (no threshold changed).
- 2026-09-25 — M3-T6, aligned with ADR-032 (supersedes ADR-012). §0 item 2: stargazers API closed, star-history is the scoring source, and the consequence for classes. §1.1: star windows use endpoint days; observation records gain `day_boundary` and `bot_filter_basis`. §1.2 A1 rewritten: star-history endpoint (TM-33) as `raw`, `verified`; the day-mapping rule (the k endpoint days starting at the day containing T; day-precision anchors start on T's UTC date; ≤ 24 h offset recorded as `window_offset_hours`, never split into hours; zone `America/Los_Angeles` inferred, recompute under a new `metric_version` if M2 disproves it); filtered variants only from identity-level events (`repo_events`, `gharchive`; OpenDigger off), `estimated`, never imputed, class-eligible only with full-window coverage ≥ 0.90; GH Archive fallback kept but unreachable at ~2 %. Removed: the stargazers-API source, the 40k-cap note and its truncation rule. Kept, because star-history is also net of current stargazers: the survivor-bias caveat and the earliest-fetch rule, now with `fetch_lag_days`. §2.1: R1.1 on the public net count (hourly GraphQL snapshots, star-history confirmation), bot filter as a confirmation step with `bot_filter_basis`, `coverage_ratio` and `bot_filter_confirmed` per case; onset on net hourly gains, else day precision. §4: flags applied by subtraction from star-history; identity sources and LQ-29 limits; `campaign_flag` may be `unknown`. §5.3: the 0.90 per-case gate now measured against star-history and applied to identity-level event data; the 0.95 figure now comes from ADR-032's reversal condition (screening, sampled from `U` or star-history, not from the mirror) instead of ADR-012. §7 table, O3, O10, new O11 (class series), §9 items 5 and 12 updated. **Version decision:** the thresholds file stays provisional `outcome-thresholds v0.1.0`. No threshold number, class input symbol (`A30`, `V90`, `P30`, …), class rule, precedence or class series changed. What changed is how the star inputs are measured: the quantity is the same net count of current stargazers by star date that ADR-012 assumed, from the only source that still provides it, at day instead of hour resolution. No observation was ever computed under the old source, and every class record stores the file's SHA-256, which tells the two contents apart. That is more than wording (patch) but not a change of inputs in the §5.4 sense (major); we follow the precedent of the round-1 and round-2 entries and do not bump. The material change, moving classes off `starscout_filtered`, **would** be a change of inputs and is left to an ADR (O11). If that ADR is adopted before the pilot, the bump should be to `0.2.0`, not `1.0.0`, because `1.0.0` is reserved for the pilot freeze; §5.4 would need a note that before 1.0.0 an input change bumps the minor digit.
- 2026-09-25 — M3-T8, ADR-035 (amends ADR-020), before any outcome data exists: thresholds bumped to **`outcome-thresholds v0.2.0`** (`schemas/outcome-thresholds/v0.2.0.json`). The class series for `A30`, `V90`, `P30` is `raw` star-history for every case; `bot_filtered` and `starscout_filtered` become a pre-registered sensitivity run (eligible only with full-window event data and `coverage_ratio ≥ 0.90`; class flips flagged `sensitive_to_fake_star_filter`; StarScout campaign flags reported per stratum). Every threshold number, rule, precedence, window and normalization setting is unchanged. v0.1.0 is superseded and kept unchanged as a record (its JSON changelog lacks the round-2 line; v0.2.0's changelog carries it as history). Updated: header, §0 item 2, §1.2 filtered variants, §3.3, §4 defaults, §5 header and §5.1 inputs, §5.2 attention_only note, §5.3 coverage gate and class series, §5.4 (current version; before 1.0.0 a change of class inputs bumps the minor version), §7 rows, §9 items 1, 10, 12; O11 closed. Pre-registration amendments: `docs/preregistration/2026-09-25-pilot-amendment-1.md`, `docs/preregistration/2026-09-25-forecasting-test-amendment-1.md`.
- 2026-09-25 — fixes after verifier (ADR-036 alignment): header names the frozen v0.1.0 content (`3f07692:schemas/outcome-thresholds/v0.1.0.json`; the tree file was later edited before ADR-035) and the current code commit (`7c3016c`); §4 series stored "wherever they can be computed" (ADR-035) instead of "always"; §1.1, §1.2, §2.1 and §4 use the case-schema names `bot_filter.status`, `bot_filter.basis`, `bot_filter.confirmed` (and `bot_filter.coverage_ratio`) instead of `bot_filter_basis` / `bot_filter_confirmed`; §2.1 states `bot_filter.confirmed` is null until applied (as in the case schema).
- 2026-09-25 — §2.1 bot-filter paragraph aligned with ADR-037.1/.2 (cases open before bot filtering; login rules only on per-repo events); verifier M3-T9.
- 2026-09-25 — M3-T10, aligned with ADR-039, ADR-040 and the threshold-calibration pre-registration (no threshold, input, class rule or schema changed; thresholds stay `outcome-thresholds v0.2.0`): header code commit `cbb50c1` and a pointer to the calibration pre-registration; §2.1 states `μ_h = μ_d / 24` for a daily baseline at hourly precision (ADR-039.4, same wording as codebook v0.2.0 §3.2); §3.3 limits cell populations by the held-out split (calibration-only before the freeze; H-sealed excluded after it, ADR-039.5), a stated departure from "all observed cases in the cell"; §5.4 names `docs/preregistration/2026-09-25-threshold-calibration.md` as binding (only `min_cell_n` and per-source `settle_lag` may change), adds the H-eval / H-sealed division and the single-use H-sealed re-run; §9 rewritten so each item is calibrated (K1, K2) or a post-freeze diagnostic (D1–D12), consistent with the pre-registration; §0 item 1, §2.2, §3.4, §5.2 and §5.3 wording on "calibrated in the pilot" corrected to match; A2 `att.hn_frontpage_minutes` aligned with the implementation (ADR-040.2: rank ≤ 30, gaps > 2× interval uncovered, tail rule, `verified`/`estimated`/`unknown`, URL-only matching; title and project-domain matching not built; deviation from §2.4 for windows starting before polling stated) and its §7 row.
- 2026-09-26 — wording fixes after verifier M21 round 2 (no method change).
