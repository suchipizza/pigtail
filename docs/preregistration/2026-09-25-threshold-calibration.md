# Pre-registration: outcome-threshold calibration in the M4 pilot

**Task:** M4-T2b (this document), carried out during M4-T3 · **Written:** 2026-09-25 · **Author role:** `analyst`
**Requirements:** ADR-019 (only the M4 pilot may calibrate; held-out split); OM §5.4 (change control), §9 (calibration list); PRD §8.2 ("v0 thresholds; calibrated in the pilot and versioned"), R3.4, R3.5; WORK_ORDER §6 (pre-register before looking at outcome data); pilot pre-registration open issue 1.
**Pinned inputs:**
- starting thresholds `outcome-thresholds 0.2.0`: `schemas/outcome-thresholds/v0.2.0.json` (ADR-035), spec `docs/specs/outcome-model.md` (OM);
- held-out rule: salt `pigtail-outcome-holdout-v1`, share 30 % (ADR-019; OM §5.4; thresholds JSON `holdout`), divided here into `H-eval` (20 %) and `H-sealed` (10 %) by digest range (§1.1; orchestrator decision of 2026-09-25, made before any data).

**Code commit when written:** `535ddd0` (HEAD on 2026-09-25). Outcome scoring isn't implemented yet.
**Outcome data seen when written:** **none.** No outcome observation has been summarised, and no percentile, class, provisional label or class base rate has been computed or looked at, for any case in either split, under any thresholds version. No case has been selected for the pilot. Nothing in this document was informed by data.
**Timestamp:** the commit that adds this file (README rule 2). **It must be committed and pushed before any class, class base rate or outcome percentile is computed for any case.** The pilot's selection run is the first such computation, and pilot amendment 2 (B6) puts it after this commit.

`CB` means `docs/methodology/codebook.md`. `OM` means `docs/specs/outcome-model.md`.

---

## 0. Principle: calibration is not tuning

pigtail has no external ground truth for "real winner", so no class can be scored as right or wrong. A procedure that tries threshold values and keeps the ones whose classes "look right" would choose definitions by their outcomes, which is the forking-paths problem the held-out split exists to prevent. This document therefore allows calibration only where a criterion exists that **doesn't use class labels**:

1. **Two operational parameters may change** (§2.1). Each has a finite menu fixed here, a mechanical decision rule, and a fallback to the v0.2.0 value if the rule can't be assessed. The inputs to these rules never include a class label, a mechanism code or a held-out case.
2. **Every other parameter is fixed** at its v0.2.0 value in v1.0.0 (§2.2). That includes the PRD-worded numbers (top decile, top quartile, 10 %), the class definitions, their precedence and the class inputs.
3. **The rest of OM §9 is answered by report-only diagnostics** (§3), computed after v1.0.0 is frozen and committed. A diagnostic can motivate a change only through ADR-019's post-freeze route (§5.3): an ADR, a new version, a held-out re-run, and results under both versions.

---

## 1. Data

### 1.1 Split rule (exact): calibration, H-eval, H-sealed
For each case compute

```
m = int(hashlib.sha256(("pigtail-outcome-holdout-v1" + case_id).encode("utf-8")).hexdigest(), 16) % 100
```

| `m` | Split | Share | Use |
|---|---|---|---|
| `m ≥ 30` | **calibration** | 70 % | pilot selection and coding samples; everything in this document before the freeze |
| `m < 20` | **H-eval** (held-out, evaluation) | 20 % | untouched until `outcome-thresholds 1.0.0` is pushed; after that, classed under v1.0.0 and used by the forecasting test and the M6/M7 analyses |
| `20 ≤ m < 30` | **H-sealed** (held-out, sealed) | 10 % | never classed or inspected until a post-freeze threshold change needs a held-out re-run; used **once**, for that re-run (§5.3) |

- **Same boundary as before.** "Held-out" is still exactly `m < 30`, with the salt and share of ADR-019, OM §5.4, the thresholds JSON `holdout` and pilot §3.1. This section adds the encoding, the integer reading of the digest, and the division of the held-out set by digest range. No second salt is used, so no case moves between calibration and held-out.
- `case_id` is the stored id string exactly as written (`case_…`, ADR-027 item 6), with no whitespace or case change.
- **Test vectors** (synthetic ids, not cases):

  | id | `m` | split |
  |---|---|---|
  | `case_example_04` | 5 | H-eval |
  | `case_example_00` | 20 | H-sealed |
  | `case_example_a` | 32 | calibration |
  | `case_example_b` | 58 | calibration |

  Any implementation must reproduce these before it is used. The verifier checks them.
- Split membership is written once into the calibration manifest (§1.3) and never recomputed. A case opened later gets its split from the same rule.

### 1.2 Calibration data set
At the calibration date `S_c` (the day step 3 of §4 runs):
- **D90:** calibration-split cases in the case store with an anchor `T` (OM §2.2) and `S_c − 730 d ≤ T ≤ S_c − 93 d`. Their T+90 classes are final (OM §2.4).
- **D365:** the cases in D90 with `T ≤ S_c − 368 d`, for the items that need T+365 (`slow_riser`, final `plateau`).
- **Excluded:**
  - the owner's registered launches, pigtail itself, and cases registered for a prospective prediction (R11.1), as in pilot E4;
  - cases with no anchor (`no_anchor`).
- **Not excluded:**
  - personal-account repos. The calibration uses only aggregate statistics and names no repo. The pilot's E1 exists for coding privacy, which doesn't arise here.
  - the pilot's own cases, which are ordinary calibration-split cases.

### 1.3 Manifest (addendum before computing anything)
Before step 3 of §4, the orchestrator commits `docs/preregistration/YYYY-MM-DD-threshold-calibration-addendum-1.md` (README rule 4). It contains:
- the SHA-256 of the private calibration manifest: `S_c`, the universe version, and the case ids of D90 and D365;
- the numbers of cases per split and in D90 and D365 (counts of cases, not outcomes);
- the code commit of the class and percentile engine;
- the SHA-256 of `schemas/outcome-thresholds/v0.2.0.json`;
- the category-taxonomy version used for cells (§4 step 3);
- the `star_history_day_tz` status (inferred or confirmed by detection-replan M2).

It names no repo.

### 1.4 Held-out cases (H-eval and H-sealed)
- **Before `outcome-thresholds 1.0.0` is committed and pushed**, for both held-out parts:
  - no percentile, class, provisional label or summary statistic of any outcome metric is computed on a held-out case;
  - nobody inspects a held-out case's outcome values;
  - the capture pipeline may fetch and store held-out observations, and data-quality monitoring may count records and fetch errors, but not values.
- **Every pre-freeze percentile uses calibration-split-only cell populations.** Queries drop held-out cases before reading any observation. This covers the pilot's selection run (pilot amendment 2, B6) and everything in this document. Held-out values never enter a cell, so no held-out observation shapes any calibration-split percentile.
- Held-out cases are never used to choose, check or tune any parameter here (ADR-019, OM §5.4).
- **H-sealed stays sealed after the freeze** (until §5.3 unseals it):
  - no percentile, class, provisional label, forecasting target or outcome summary is computed for an H-sealed case, and nobody inspects its outcome values;
  - H-sealed cases are excluded from every cell population, so their values never shape another case's percentile;
  - they are excluded from every analysis, including the forecasting test's evaluation set, M6 and M7;
  - capture may keep storing their observations, and record counts may be monitored, as before the freeze.
- **The analysis universe after the freeze** is calibration ∪ H-eval. "Held-out" in any post-freeze analysis report means H-eval unless the report is a §5.3 re-run.

### 1.5 What the procedure never uses
- Class labels, in any decision rule (§2.1). Classes are computed only for diagnostics, after the freeze (§3).
- Pilot codes of any kind (mechanism support, triggers, events, assets).
- Any outcome data other than what the rule in §2.1 names.

---

## 2. Parameters

### 2.1 Calibrated (the complete list)

**K1. `normalization.min_cell_n`** (v0.2.0: 30; OM §9 item 3). Menu: {30, 50}.
- **Why a rule exists here.** The criterion is how stable top-decile membership is, which is a property of the percentile machinery, not of which cases "should" be winners.
- **Data:** D90. For each case: `A30`'s underlying value, i.e. `raw` star-history stars over the 30 endpoint days (OM §1.2), status `observed`. Cells are (category, cohort quarter), calibration-only. No class, no adoption and no community metric is used.
- **Statistic.** For each cell `c` with `n_c ≥ 30` observed cases:
  1. Compute each case's mid-rank percentile in the full cell (OM §3.3) and its top-decile status `s_i = [p_i ≥ 90 and value_i > 0]`.
  2. Draw B = 2,000 bootstrap resamples of the cell's cases with replacement. In each resample, recompute the mid-rank percentiles within the resample (duplicates count as separate members) and the status of each distinct original case present.
  3. The flip rate `f_i` is the share of resamples containing case `i` in which its status differs from `s_i`.
  4. The cell instability `U_c` is the mean of `f_i` over the cases with `s_i = 1`. It is undefined if no case has `s_i = 1`, and the cell is then left out.
- **Bands:** L = cells with 30 ≤ n_c < 50; H = cells with n_c ≥ 50.
- **Assessable** only if L and H each have ≥ 5 cells with a defined `U_c`. Otherwise K1 stays 30 and the report says "not assessed".
- **Rule.** Choose 50 if and only if **all** of these hold:
  - (a) median(`U` over L) − median(`U` over H) > 0.10;
  - (b) median(`U` over L) > 0.20;
  - (c) re-applying the OM §3.4 fallbacks with `min_cell_n = 50` leaves ≤ 30 % of D90 cases with no percentile at any level. This is computed from case counts per cell only.

  Otherwise choose 30.
- **Why these numbers.** They are design choices made now, not published rules. (a) asks whether small cells are materially less stable than large ones. (b) asks whether a top-decile member in a small cell loses its status in more than 1 of 5 resamples. (c) keeps the class system usable. The fallback order itself is not calibrated (§2.2).
- **Seeds:** `numpy.random.default_rng(SeedSequence(20261001).spawn(N)[j])`, where the cells are sorted by (category id, quarter) and `j` is the cell's index among the N cells with n_c ≥ 30.

**K2. `windows.settle_lag_days`**, per source (v0.2.0: default 3, "per source configurable"; OM §1.1, §9 item 6). Menu per source: {1, 3, 7, 14}.
- **Sources:** star-history (TM-33), npm downloads (TM-08), PyPI BigQuery (TM-07), crates.io (TM-09), GitHub merged PRs (TM-02). Homebrew and Docker Hub have no history to settle and keep 3.
- **Data.** Live repeated fetches of the same source-day, for repos and packages of calibration-split cases only. Each sampled source-day is fetched at lags ℓ ∈ {1, 3, 7, 14, 21} days after the day ended (in the source's own calendar; star-history in `star_history_day_tz`). The reference is ℓ = 21.
- **Statistic.** A source-day is settled at lag ℓ if `|v(ℓ) − v(21)| ≤ max(1, 0.005 · v(21))`. The rule uses only fetch-to-fetch differences, never levels, and never a class.
- **Assessable** for a source only with ≥ 200 source-days from ≥ 20 distinct repos or packages. Otherwise that source keeps 3, reported as "not assessed".
- **Rule.** Take the smallest ℓ in {1, 3, 7, 14} at which ≥ 99 % of the sampled source-days are settled. If none qualifies, take 14 and flag the source.
- **Sample:** source-days are drawn with `default_rng(20261002)`, uniformly over (repo or package, day) pairs whose day ended within the collection period. The collection period starts when the capture scheduler supports lagged re-fetches (an engineering task, not blocking), and lasts until the sample size is reached or `S_c`, whichever comes first.
- **Note.** Star-history counts also drift down as accounts un-star or are deleted (OM §1.2). The tolerance allows 0.5 %, and larger drift counts as unsettled. That is intended, because drift is also what `settle_lag` protects against.
- **Output.** The chosen values go into v1.0.0 as `windows.settle_lag_days_by_source`, with `settle_lag_days_default` kept for sources without an entry.

### 2.2 Fixed in v1.0.0 at their v0.2.0 values (not calibrated)
| Parameter (thresholds JSON) | v0.2.0 | Why it is not calibrated here | OM §9 item |
|---|---|---|---|
| Attention cut 90 (`winner`, `attention_only`, `slow_riser`, the provisional label) | 90 | The PRD's own wording ("top decile"); no class-free criterion | 2 |
| Adoption/community cut 75 (`winner`, `slow_riser`) | 75 | The PRD's wording ("top quartile") | 2 |
| Decay ratio 0.10 · P30 (`short_lived`) | 0.10 | The PRD's wording ("< 10 % of the T+30 peak") | 2 |
| "Adoption is flat": `AD90 < 50` (`CM90 < 50` if not applicable) | 50 | An operationalisation, but the only criteria available would use class results; reported (D2) | 2 |
| Zero floor | true | A logic safeguard: a zero count is never "top" | 2 |
| Class definitions, precedence, inputs (`A30`, `AD90`, `AD365`, `CM90`, `V90`, `P30`, `burst_30`, `burst_any`, `launch_signal`), class series `raw` (ADR-035), unknown propagation, edge rules | as v0.2.0 | Definitions, not calibration targets; changing them is a version change by ADR (OM §5.4) | — |
| `anchor.launch_lookback_days` | 30 | A longer look-back moves T away from the burst and shifts `A30`'s window. No outcome-free criterion picks the value; reported (D4) | 4 |
| `anchor.burst_onset_rule`, `burst_detection` (100 stars / 48 h, 3σ, 30-day baseline) | as v0.2.0 | PRD R1.1 defaults and ADR-032.2. They are shared with capture, whose recall validation (detection-replan M4) is separate. Reported (D4) | 4 |
| `star_series.gharchive_min_coverage_ratio` (0.90) | 0.9 | Under v0.2.0 it gates only the GH Archive fallback and the fake-star sensitivity run, not `raw` classes; reported (D5) | 5 |
| `windows.stock_tolerance` | 1 d, 0.1 · k | Trades bias against missingness, with no outcome-free optimum; reported (D6) | 6 |
| `returning_external_contributor` (≥ 2 PRs, ≥ 30 days, exclusions) | as v0.2.0 | ADR-016's operationalisation of the PRD; reported (D7) | 7 |
| `normalization.fallback_order`, `percentile_method`, `flag_cell_if_unknown_share_above` | as v0.2.0 | Structure, not a threshold; only `min_cell_n` (K1) moves | 3 |
| `fake_star_filter` (StarScout parameters) | as v0.2.0 | Taken from the paper (OM §4); not estimable here | 10 |
| `holdout` | salt, 30 % | Fixed before any data (ADR-019) | — |
| `observation_points_days`, other `windows` | as v0.2.0 | R3.1 | — |

**Reference updates that are not calibration.** v1.0.0 also:
- sets `normalization.taxonomy_version` to the category-taxonomy version the cells used (§4 step 3);
- records the `star_history_day_tz` status.

A zone correction from detection-replan M2 is a measurement fix, applied as a new star `metric_version` (OM §1.2), not a threshold change. If it lands after the freeze, every star observation is recomputed and classes are re-derived under the same v1.0.0 thresholds. The report says so.

---

## 3. Report-only diagnostics (after the freeze)

These are computed on the calibration split only (D90, and D365 where T+365 is needed), **under v1.0.0** and, for comparison, **under v0.2.0**, after step 6 of §4. They answer OM §9 items 1–12. They are descriptive: shares carry Wilson 95 % intervals, and there are no p-values. **They cannot change v1.0.0** except through §5.3.

| Id | OM §9 | Content |
|---|---|---|
| D1 | 1 | Class base rates overall and per stratum (category, cohort quarter, entry path), with n; the `winner` share and whether it is at most top-decile-sized |
| D2 | 2 | One-at-a-time sensitivity grid, class counts only: attention {85, 90, 95}; adoption/community {70, 75, 80}; decay {0.05, 0.10, 0.20}; flat {25, 50, 75}; zero floor on/off. Labelled "descriptive; not a calibration" |
| D3 | 3 | Cell sizes, the fallback level used, and the `small_cohort` share, under `min_cell_n` 30 and 50 |
| D4 | 4 | Onset-precision shares (hour/day); for cases whose pilot codes include a `launch` (adjudicated, and only if C3 passed G1), the lag from launch to `T_burst` and the share of cases whose T would change with a look-back of 14 or 60 days |
| D5 | 5 | Distribution of identity-level `coverage_ratio` against star-history, by basis and cohort |
| D6 | 6 | Share of stock observations `unknown` for lack of a snapshot within tolerance, per source and offset; the same under half and double the tolerance |
| D7 | 7 | Zero inflation of `comm.returning_external_contributors@90`; the share of cells where a single returning contributor already reaches `CM90 ≥ 75` |
| D8 | 8 | Share of `other` and of low-confidence category assignments (the taxonomy itself is the pilot's G1 matter) |
| D9 | 9 | `unclassified:missing_data` share by category and cohort. **Pre-registered consequence:** a stratum with > 50 % `missing_data` is labelled "class system not usable for this stratum" in every report and D-page that uses classes for it |
| D10 | 10 | Raw vs `bot_filtered` / `starscout_filtered` class flip counts and rates on the eligible cases, the eligible share, and StarScout `campaign_flag` counts per stratum |
| D11 | 11 | Share of `attention_only` cases without `burst_30`; class counts with `burst_30` required |
| D12 | 12 | Filtered-series eligibility share by cohort and entry path; `fetch_lag_days`, live vs backfilled within cells; the share of day-precision anchors; the number of cases whose class changes when the star-day mapping shifts by ±1 day |

---

## 4. Procedure (in this order)

0. **Commit and push this file** (with pilot amendment 2). Until then, no class, class base rate or outcome percentile is computed for any case.
1. **Pilot selection** may run (pilot amendment 2, B6): v0.2.0, with calibration-only cells. It uses class labels to pick cases. **No rule in §2.1 uses class labels, so seeing them there can't steer calibration.** The selection script writes only what selection needs to the manifest. Nobody tabulates class counts from it before step 6.
2. **K2 collection** runs live whenever the scheduler supports it (§2.1).
3. **Addendum 1** (§1.3), then **K1 and K2 are computed once**, as follows.
   - **Timing:** step 3 runs after the codebook's G1 verdict, so that cells use the frozen category taxonomy. If M4-T3 is marked `blocked` (pilot §7.2), it runs on taxonomy `v0.1.0` (ADR-017) and the report says so.
   - **Recording:** the decision and every statistic in §2.1 go in a private run log. The aggregate statistics (medians, band counts, settled shares) go in the report.
4. **Assemble the candidate v1.0.0.** It is `schemas/outcome-thresholds/v0.2.0.json` with:
   - K1 and K2 applied;
   - the reference updates of §2.2;
   - `version: "1.0.0"`, `status: "frozen"`, `supersedes: "0.2.0"`;
   - a `calibration` block recording: this file and its freezing commit, addendum 1, `S_c`, the data and universe version, the code commit, the K1 and K2 decisions with their aggregate statistics, and which parameters were "not assessed".
5. **Verifier check.** The verifier recomputes K1 and K2 from the stored inputs with the §2.1 seeds. The decisions must match exactly, and the statistics to 3 decimals (bootstrap statistics within 0.01). The **verifier's** verdict decides; a mismatch is resolved as an implementation bug (§5.2).
6. **Freeze.** Commit and push `schemas/outcome-thresholds/v1.0.0.json` and `docs/preregistration/YYYY-MM-DD-threshold-calibration-addendum-2.md`. The addendum holds the file's SHA-256 and the commit. OM's header and §5 are updated to name v1.0.0 as current. From this point every class record stores v1.0.0 and its SHA-256 (OM §5.3).
7. **Diagnostics** (§3) on the calibration split, then the report (§6).
8. **H-eval cases** are classed under v1.0.0 only after step 6, by whichever analysis needs them first (M6, or the forecasting test's evaluation set). That analysis's report includes, once, the H-eval D1 and D3 shares next to the calibration-split ones. This is confirmatory and descriptive, and it never changes v1.0.0 (§5.3). **H-sealed cases are not classed at this step or any later one**, except under §5.3.

If v1.0.0 is identical to v0.2.0 apart from version, status, reference fields and the `calibration` block (K1 = 30 and K2 = 3 or not assessed everywhere), it is still frozen as v1.0.0.

---

## 5. Rules

### 5.1 Prohibited
- Changing, before the freeze, any parameter outside §2.1, or taking a value outside a §2.1 menu.
- Running K1 or K2 more than once for a decision, or with other seeds, bands, bootstrap sizes, tolerances or lags than §2.1.
- Using a held-out case, a class label, a pilot code or a mechanism result in any §2.1 decision.
- Computing a §3 diagnostic before step 6.
- Lowering or relaxing any threshold because a diagnostic looks bad.

### 5.2 Deviations
An implementation bug found before step 6 (a wrong cell, a wrong seed use, a split bug) may be fixed, and K1/K2 re-run **with the same rules**. The fix is logged as a deviation in the report and the addendum. If a split bug had let a held-out case into any computation, the report states which computation and how many cases; the computation is redone without it.

### 5.3 Changes after the freeze (ADR-019, OM §5.4)
Any change to v1.0.0 made after outcome data has been seen, including anything motivated by a §3 diagnostic or by H-eval results, requires all of:
1. an ADR in `ops/DECISIONS.md`, stating the proposed change, what data motivated it, and the **acceptance criterion, written and committed before the held-out re-run is computed**;
2. a new version: `1.1.0` for a numeric threshold, `2.0.0` for inputs, definitions or precedence (OM §5.4);
3. a re-run on held-out cases, as set out below;
4. results reported under **both** versions.

**The held-out re-run uses H-sealed, once.**
- The **first** post-freeze threshold change unseals H-sealed:
  - its cases are classed under both versions;
  - cell populations then include them, together with calibration and H-eval cases, under each version;
  - the acceptance criterion from the ADR is applied;
  - the report labels the result **"held-out re-run on H-sealed (single use)"**.
- A re-run on H-eval may be reported alongside, labelled **"re-run on seen data (H-eval)"**. It never replaces the H-sealed run.
- **After that single use, no held-out set remains.** Any further threshold change can only be evaluated **prospectively**: on cases whose T falls after the ADR is committed and pushed, with the acceptance criterion and the minimum n stated in that ADR. Until that evaluation is complete, results under the new version are labelled exploratory.
- If one ADR proposes several changes, they share the single H-sealed run. They can't be split into separate ADRs to reuse it.

Analyses that depend on a change are labelled **exploratory** until these steps are complete (README rule 3).

---

## 6. Report (`docs/reports/threshold-calibration.md`)
The report contains:
- versions and hashes (v0.2.0, v1.0.0, addenda), data and universe version, code commit;
- the split counts;
- K1 and K2: statistics, decisions, and "not assessed" items;
- the verifier's verdict;
- D1–D12;
- deviations (or "none");
- limitations (§7).

All content is aggregate. No repo, matched loser, account or case-level value is named (ADR-022, CB-20).

---

## 7. Known limitations (stated in advance)
1. **Most of OM §9 is not calibrated but reported.** This is deliberate (§0). Thresholds that the PRD words as "calibrated in the pilot" (§8.2) stay at their v0 values unless a post-freeze ADR changes them.
2. **K2 needs live repeated fetches.** If the scheduler can't collect them before `S_c`, every source keeps 3 days ("not assessed").
3. **K1 is a stability criterion only.** It says nothing about validity: a stable percentile can still rank a biased series. Star-history is survivor-biased and has day resolution (OM §1.2).
4. **Calibration-only cells are about 30 % smaller** than the production cells used after the freeze. So K1 sees smaller cells than production, and `small_cohort` shares before the freeze overstate those after it.
5. **Blindness is spent in two stages.** H-eval is seen at step 8. H-sealed is blind until its single use (§5.3). After that, threshold changes can only be tested prospectively. H-sealed holds about 10 % of cases, so the re-run has lower power than a 30 % held-out set would, and small strata may be "insufficient evidence" in it.

## 8. Open issues (for the orchestrator)
1. **Record the H-eval / H-sealed division as an ADR** (it amends ADR-019's use of the held-out set; the 30 % boundary and salt are unchanged). The forecasting pre-registration draws its training set from the calibration split, and its live evaluation set from all live cases. It doesn't know H-sealed exists, and it is frozen. Excluding H-sealed live cases from its evaluation set (§1.4) therefore needs a **forecasting-test amendment 2**, committed before any evaluation target is computed. Until then, the README records the pointer.
2. **K2 needs a capture feature:** scheduled re-fetches of the same source-day at lags 1, 3, 7, 14 and 21 days (engineer backlog).
3. **The split code must match §1.1**, including the test vectors, before the pilot's selection run. No implementation exists yet (`grep holdout src/` finds nothing).
4. **OM should point to this file.** Its §5.4 and §9 should reference this pre-registration and state that §9 items are either calibrated (K1, K2) or report-only. This is an OM changelog entry outside this brief's edit scope.
