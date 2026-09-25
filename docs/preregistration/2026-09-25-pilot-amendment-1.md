# Amendment 1 to the M4 pilot pre-registration

**Amends:** `docs/preregistration/2026-09-25-pilot.md`, frozen at commit `3f07692` (`3f0769289e1842d24c1d861e4b05db9fe57ca855`, 2026-09-25T16:10:40+02:00; `git log --format=%h -1 -- docs/preregistration/2026-09-25-pilot.md`). That file is not edited (README rule 1).
**Task:** M3-T8 · **Written:** 2026-09-25 · **Author role:** `analyst`
**Decided by:** ADR-032 (star series without GH Archive or stargazer lists; supersedes ADR-012), ADR-035 (classes on the `raw` star-history series; `outcome-thresholds v0.2.0`; amends ADR-020), ADR-036 (per-repo events held).
**Code commit when written:** `85de195` (HEAD on 2026-09-25).
**Outcome data seen when written:** **none.** No outcome observation, percentile, class, provisional label or base rate has been computed or looked at, under any thresholds version. No case has been selected, unitized or coded, and no pilot attempt has started. Nothing in this amendment was informed by data.
**Timestamp:** the commit that adds this file (README rule 2).

## Why

On 2026-06-30 GitHub restricted the stargazer lists (REST `/stargazers` and GraphQL `stargazers`) to admins and collaborators, so ADR-012's source no longer exists. GH Archive holds about 2 % of stars in 2026 (ADR-028), so it cannot stand in either. ADR-032 makes GitHub's star-history endpoint (`GET /repos/{o}/{r}/stargazers/history`: daily net counts back to creation, no identities, no page cap measured) the scoring series. With no identities, `starscout_filtered` exists only where per-repo event data covers the window, and that source is held (ADR-036). Under `outcome-thresholds 0.1.0` almost every case in the §3.1 pool would therefore be `unclassified:missing_data`, and the pilot could not select any case. ADR-035 moves the class series to `raw` star-history (`outcome-thresholds 0.2.0`), keeps the filtered series as a sensitivity run, and requires this amendment.

Every change below replaces a dependency on the stargazers list API or on the filtered series. No gate, α threshold, minimum unit count, sample size, seed, stratum rule, caliper or matching weight changes.

## Changes

Section numbers refer to the original file. "Old" quotes the original; "New" replaces it for all purposes.

### A1. Pinned thresholds version (header, §1, §2 item 3, §3.1, §10 item 8)
- Old (header): "`outcome-thresholds 0.1.0`: `schemas/outcome-thresholds/v0.1.0.json`, spec `docs/specs/outcome-model.md` (OM);"
- New: "`outcome-thresholds 0.2.0`: `schemas/outcome-thresholds/v0.2.0.json`, spec `docs/specs/outcome-model.md` (OM)". The SHA-256 recorded in the selection addendum (§4.6) is that of the v0.2.0 file.
- Old (§1): "computed under `outcome-thresholds 0.1.0` as they stand." New: "computed under `outcome-thresholds 0.2.0` as they stand."
- Old (§2 item 3): "Outcome classes can be computed for the selection pool under `outcome-thresholds 0.1.0`". New: "… under `outcome-thresholds 0.2.0`".
- Old (§3.1): "Its class under `outcome-thresholds 0.1.0` is final and not `unclassified`." New: "Its class under `outcome-thresholds 0.2.0` (class series `raw` star-history, ADR-035) is final and not `unclassified`."
- Old (§10 item 8): "They are `outcome-thresholds 0.1.0`, before calibration." New: "They are `outcome-thresholds 0.2.0`, before calibration."
- **Effect:** selection classes use `A30`, `V90` and `P30` from `raw` star-history for every pool case. All numbers and rules are identical to 0.1.0.

### A2. E2: star series completeness (§3.2)
- Old: "**E2.** The repo is deleted, private or unreachable at `S`, or its stargazers-API series is truncated. The series counts as truncated when the connector detects it, or when the repo has more than 40,000 stars at `S` (the cap is unverified, OM §1.2)."
- New: "**E2.** The repo is deleted, private or unreachable at `S`, or its star-history series (ADR-032.3, OM §1.2) is incomplete. The series counts as incomplete when the connector reports an error or a missing page for any week the case's windows need, or when the all-time sum of the returned days differs from the repo's `stargazers_count` fetched in the same run by more than 1 %."
- **Why:** the 40,000-star cap belonged to the stargazers list API. The star-history endpoint returned full history for a 250k-star repo, and its all-time sum matched `stargazers_count` in the checks so far (OM §1.2, O10). The 1 % tolerance allows for stars added between the two requests. It is a design choice made before any data, not a measured value.

### A3. E3: StarScout campaign flag (§3.2)
- Old: unchanged rule ("A StarScout campaign flag is set (OM §4). If the filter hasn't run for the case's window, the flag is recorded as `unknown`, the case stays in, and the report says so.")
- New: the rule stays. Added: under ADR-032 and ADR-036 the flag will be `unknown` for most pool cases, because it needs identity-level star events. The report gives the counts of `campaign_flag` `true` / `false` / `unknown` per stratum (S1–S3) and for the pool, as ADR-035 requires (OM §4).

### A4. E5: required snapshots (§3.2)
- Old: "Concretely: no README at T can be retrieved, or no stargazers series exists."
- New: "Concretely: no README at T can be retrieved, or no star-history series can be retrieved for the repo."

### A5. `LSM` (§3.6)
- Old: "`log10(1 + filtered stars in [T, T+48 h))`, from the stargazers API, `starscout_filtered` (OM §1.2)"
- New: "`log10(1 + raw stars in the first 2 endpoint days of the case window)`, from star-history (ADR-032.3): the 2 star-history days starting at the window's first day under OM §1.2's day mapping (hour-precision T: the day containing T; day-precision T: the day with T's UTC date). `window_offset_hours` and `day_boundary` are recorded with the value."
- **Why these choices:** (a) star-history is the only star series that exists for every pool case; hourly `stargazerCount` snapshots (ADR-032.1) exist only for repos on the watch list from before T. Using them where they exist would put two series with different resolution into one caliper and one SD, the same mixing ADR-035 rejects for classes. Where hourly snapshots cover `[T, T+48 h)`, their net gain is recorded for the balance report as descriptive only; it is not used for matching. (b) `raw`, not `starscout_filtered`, for the same reason as ADR-035: the filtered series is `unknown` for most cases. (c) 2 endpoint days replace 48 hours. For an hour-precision T they start up to 24 h before T (OM §1.2 misalignment); that is accepted, and it is the same day window that `A30` uses.
- The note on overlap with `A30` still holds.

### A6. Founder-audience proxy (§3.6; ADR-029 item 2)
- Old: "the sum of stars with `starred_at < T` over the owner organisation's **other** public repos"
- New: "the sum, over the owner organisation's **other** public repos, of their star-history daily net counts for the endpoint days before the case window's first day (OM §1.2)". Bands r1–r4 (CB §4.5) are unchanged.
- **Why:** `starred_at` came from the stargazers list API. The star-history sum is the same quantity (current stargazers with a star date before T), at day resolution, with no person-level data. It stays at organisation level. Cost: one core request per 30 weeks of history per other repo (OM O3); this is a capture budget matter, not an analysis change.

### A7. Limitations (§10 items 6 and 7)
- Old (6): "The founder-audience proxy (§3.6) and the 40k-star cap (E2) are unverified design choices."
- New (6): "The founder-audience proxy (§3.6) and the E2 completeness tolerance (1 %) are unverified design choices."
- Old (7): "The stargazers-API series (ADR-012) is survivor-biased, which affects the classes used for selection."
- New (7): "The star-history series (ADR-032) counts current stargazers only, so it is survivor-biased (un-starred and deleted accounts vanish from past days, OM §1.2), and it has day, not hour, resolution. Selection classes and `LSM` use it without fake-star filtering (ADR-035); the report gives the raw vs filtered class flip rate on the cases where a filtered series is eligible (OM §4, §9 item 10), and says how many that is."

### A8. Open issue 8: where the pool comes from (§11 item 8)
- Old: "GH Archive can't detect breakouts, so the retrospective pool that §3.1 draws from depends on the screen M1-T18 chooses."
- New: the screen is now fixed by ADR-032.1 (watch-list hourly GraphQL counts, GitHub Search sweeps, HN-linked repos; GH Archive only as a control; OpenDigger off). Retrospective velocity cases are found by applying R1.1 to star-history days (OM §2.1, precision `day`). Addendum 1 records the pool's composition by entry path, as before.

## Not changed

- Objectives, G1, α statistics, minimum units, bootstrap, seeds, samples P/SE/SC/D and their sizes, strata, caliper (0.5 SD), distance formula, overrides, blinding, double-coding protocol, revision and stopping rules.
- E1, E4, E6.
- The codebook pin (`0.1.0`). Its `depends_on_adrs` now name ADR-032 and ADR-035 instead of ADR-012 (errata, no version bump; CB changelog). CB §3.1–3.2 still derive the `burst | quiet` layer, which defines the C9/C10 burst units, from "`starscout_filtered` from the stargazers API". That derivation needs a codebook fix before unitizing (§2 item 4); this amendment does not decide it. The orchestrator is asked to resolve it (a codebook errata or minor bump, with a further amendment if it changes the unit definition), before any unit count is computed.

## Analyses affected

Case selection (§3.1, §3.2 E2/E3/E5, §3.4 through `LSM`), the matching covariates and balance report (§3.6, §3.7), the report's exclusion counts and limitations (§9, §10). No reliability analysis (§4–§7) is affected.
