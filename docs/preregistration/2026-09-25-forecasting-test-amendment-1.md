# Amendment 1 to the forecasting-test pre-registration (PRD §9.1 test 1)

**Amends:** `docs/preregistration/2026-09-25-forecasting-test.md`, frozen at commit `3f07692` (`3f0769289e1842d24c1d861e4b05db9fe57ca855`, 2026-09-25T16:10:40+02:00; `git log --format=%h -1 -- docs/preregistration/2026-09-25-forecasting-test.md`). That file is not edited (README rule 1).
**Task:** M3-T8 · **Written:** 2026-09-25 · **Author role:** `analyst`
**Decided by:** ADR-032 (screening and star series without GH Archive or stargazer lists; supersedes ADR-012), ADR-035 (classes on the `raw` star-history series; `outcome-thresholds v0.2.0`; amends ADR-020), ADR-036 (per-repo events held).
**Code commit when written:** `85de195` (HEAD on 2026-09-25).
**Outcome data seen when written:** **none.** No live case has been detected for this test. No target, label, class, percentile, feature value, model fit or Brier score has been computed or looked at, for any training or evaluation case. The model-freeze addendum (§6.1) does not exist. Nothing in this amendment was informed by data.
**Timestamp:** the commit that adds this file (README rule 2).

## Why

The original names two sources that no longer work: the stargazers list API (`starred_at`, ADR-012), which GitHub restricted to admins and collaborators on 2026-06-30, and GH Archive as the screen, which holds about 2 % of stars in 2026 (ADR-028). ADR-032 replaces them: screening on hourly public star counts, Search and HN; the star series is GitHub's star-history endpoint (daily net counts, no identities). With no identities, `starscout_filtered` exists only where per-repo event data covers the window (held, ADR-036), so the original target and most star features would be `unknown` for nearly every case. ADR-035 moves the class series to `raw` star-history and requires this amendment.

No pass criterion, n, model form, penalty grid, seed, bootstrap or exclusion threshold changes. ADR-026's choice of primary and secondary target is unchanged.

## Changes

Section numbers refer to the original file. "Old" quotes the original; "New" replaces it for all purposes.

### F-A1. Requirements and pinned definitions (header)
- Old: "ADR-012, ADR-015, ADR-019, ADR-020, ADR-026."
- New: "ADR-015, ADR-019, ADR-020 as amended by ADR-035, ADR-026, ADR-032, ADR-035."
- The pin stays `outcome-thresholds` **v1.0.0**, the pilot freeze. The pilot calibrates from **v0.2.0** (pilot amendment 1), so v1.0.0's class series is `raw` star-history unless a later ADR, committed before any target for this test is computed, says otherwise.

### F-A2. Primary target: `A30` series (§1.1)
- Old: "`A30` is the mid-rank percentile of `starscout_filtered` stars in `[T, T+30 d)` within the case's (category, quarter) cell, with the OM §3.4 fallbacks."
- New: "`A30` is the mid-rank percentile of `raw` star-history stars over the 30 endpoint days of `[T, T+30 d)` (OM §1.2 day mapping) within the case's (category, quarter) cell, with the OM §3.4 fallbacks."
- The secondary target (§1.2) uses the v1.0.0 classes and so the same series; its text is otherwise unchanged.

### F-A3. Live detection (§2.1)
- Old: "1. whatever screening source is in force: the GH Archive scan, or its replacement from M1-T18 (ADR-028); 2. the R1.1 threshold (`velocity-v0`: ≥ 100 filtered stars in 48 h and z ≥ 3), confirmed on the stargazers-API series (ADR-009, ADR-012)."
- New: "1. the screens of ADR-032.1: hourly GraphQL star counts for the watch list, GitHub Search sweeps and HN-linked repos (GH Archive only as a control; OpenDigger off unless TM-32 is cleared); 2. the R1.1 threshold (`velocity-v0`: ≥ 100 stars in 48 h and z ≥ 3) on the public net star count, confirmed with star-history daily counts (ADR-032.2, OM §2.1). The bot filter is a confirmation step: each case records `bot_filter_basis`, `coverage_ratio` and `bot_filter_confirmed`."
- Whether a detection with `bot_filter_confirmed = false` opens a case is a capture decision (M1-T24). The rule in force is part of the detection pipeline version pinned in the freeze addendum (§6.1); X8 applies to changes as before.
- `t_det` (live) is unchanged: when that pipeline opened the case.

### F-A4. Retrospective `t_det` (§2.3)
- Old: "`t_det` is re-derived by applying the pinned detection rule (§2.1) to the stargazers-API series, which is the confirming series live detection uses."
- New: "`t_det` is re-derived by applying the pinned R1.1 thresholds to star-history daily counts (the confirming series live detection uses), with OM §2.1's day rule: for each endpoint day `d`, the 48-hour count is the sum of `d` and the day before it, and the baseline is the 30 endpoint days before those two, with ADR-027 item 3's √μ floor. `t_det` is the **end** of the first day `d` that meets both thresholds (the first moment a day-resolution pipeline could have seen it), precision `day`. The bot-filter confirmation cannot be reproduced for most retrospective cases (`bot_filter_basis` `none` or `gharchive`); training cases are **not** required to have `bot_filter_confirmed = true`, and their share with it is reported."

### F-A5. Star predictors (§3 lead sentence, F1–F4, F8)
- Old (lead): "All flow features count **filtered** stars (`starscout_filtered`, ADR-012 / OM §4) unless stated otherwise."
- New (lead): "All star features count `raw` star-history daily net counts (ADR-032.3, ADR-035), for both training and evaluation cases, even where hourly snapshots exist, so that the frozen model sees one series. A day is used only if it has **ended** (in `star_history_day_tz`) before `p`. The case window's first day is the one OM §1.2 maps T to."
- F1 old: "`log1p` stars in `[T, p)` | stargazers API `starred_at`". New: "`log1p` stars on the endpoint days from the window's first day to the last day ended before `p` | star-history".
- F2 old: "`log1p` stars in `[p − 48 h, p)` | same". New: "`log1p` stars on the last 2 endpoint days ended before `p` | star-history".
- F3 old: "`log1p` of the mean stars/day over `[T − 30 d, T)` | same". New: "`log1p` of the mean stars/day over the 30 endpoint days before the window's first day | star-history".
- F4 old: "stars in `[p − 48 h, p)` ÷ max stars in any 48 h window within `[T, p)` (0 if the max is 0) | same". New: "F2's count ÷ the max count over any 2 consecutive endpoint days within F1's days (0 if the max is 0) | star-history".
- F8 old: "r1–r4 of the stars with `starred_at < T` on the owner's other public repos (the pilot's proxy, pilot §3.6) | stargazers API". New: "r1–r4 of the sum, over the owner's other public repos, of star-history daily counts for the endpoint days before the case window's first day (pilot amendment 1, A6) | star-history".
- Hourly-snapshot versions of F1–F4, where they exist, may be reported only as exploratory (§7.4).

### F-A6. Baseline velocity (§5.1)
- Old: "`v7` = filtered stars in `[p − 7 d, p)` ÷ 7"
- New: "`v7` = `raw` star-history stars on the last 7 endpoint days ended before `p` ÷ 7".

### F-A7. Exclusion X3 (§4)
- Old: "`A30` unknown, `unclassified:small_cohort`, truncated stargazers series, or `missing_data`."
- New: "`A30` unknown, `unclassified:small_cohort`, an incomplete star-history series (pilot amendment 1, A2 definition), or `missing_data`."

### F-A8. Fake-star reporting (§4 "Not excluded", §7.2)
- The original keeps cases with a StarScout campaign flag; that stays. Added to §7.2: the counts of `campaign_flag` `true` / `false` / `unknown` per stratum (category, owner type) and overall, as ADR-035 requires (OM §4). Under ADR-032 and ADR-036 most flags will be `unknown`.

### F-A9. Sensitivity (a) (§7.3)
- Old: "(a) Target and features on the `raw` star series instead of `starscout_filtered` (ADR-020). A pass that flips to a fail is flagged `sensitive_to_fake_star_filter`."
- New: "(a) For each of `bot_filtered` and `starscout_filtered`: on the evaluation cases for which the variant is eligible for the target window **and** the feature windows (every endpoint day covered by identity-level event data, no window overflow, `coverage_ratio ≥ 0.90`; OM §1.2, §4), recompute the target (`A30` percentiles within the same cells, over the cases eligible for the variant) and the star features on the variant, apply the frozen models, and report n, the number of label flips, Δ and its CI (paired bootstrap, `default_rng(20260930)` for `bot_filtered`, `default_rng(20260931)` for `starscout_filtered`). With n < 30 the result is reported as 'insufficient evidence (n = x)'. If the primary pass on the same subset flips to a fail, the test result is flagged `sensitive_to_fake_star_filter`. Ineligible cases are never imputed."
- **Why:** the direction reverses: `raw` is now the primary series (ADR-035). Live cases are tracked from detection, which is after a burst anchor's onset, so the eligible subset may be small; its size is reported either way.

### F-A10. Limitations (§8 items 1 and 5)
- Old (1): "Stars from the stargazers API are net of un-stars and deleted accounts (ADR-012)."
- New (1): "Star-history counts are net of un-stars and deleted accounts, and have day resolution (ADR-032). Retrospective cases are fetched long after `p`, so their features lose more stars than live ones (OM §1.2 `fetch_lag_days`). The target and features are not fake-star filtered (ADR-035); §7.3 (a) covers that where it can."
- Old (5): "ADR-028 found GH Archive coverage of about 0.007 in the M1 smoke run, and the replacement screen (M1-T18) isn't chosen."
- New (5): "The screen is chosen (ADR-032.1). Repos outside the watch list depend on Search and HN picking them up first, and live `t_det` has hour precision while retrospective `t_det` has day precision (F-A4), so training and evaluation cases can differ in how early `p` falls after the burst. The retrospective training set follows the pinned detection rule, not the live screen (§2.3)."

## Not changed

Targets' definitions other than the series, ADR-026's primary/secondary choice, `T` (ADR-015 / OM §2.2), `p = t_det + 7 d`, the availability rule, F5–F7 and F9–F15, missing-value handling, exclusions other than X3, the models and penalty grid, freezing, the evaluation set (first 200 by `t_det`), Brier scores, bootstrap seeds for the primary, secondary and H-L4 analyses, the pass criterion, and sensitivities (b)–(e).

## Analyses affected

Primary and secondary targets (series), live and retrospective case definitions (§2.1, §2.3), star features F1–F4 and F8, the baseline input `v7`, exclusion X3, the per-stratum report (§7.2), sensitivity (a), and limitations 1 and 5.
