# Pre-registration: PRD §9.1 test 1 (forecasting test)

**Task:** M4-T2 · **Written:** 2026-09-25 · **Author role:** `analyst`
**Requirements:** PRD §9.1 test 1, R1.1, R3.1, R3.5, R11.2 (style of locking), §10 (reproducibility: fixed seeds); ADR-012, ADR-015, ADR-019, ADR-020, ADR-026. Hypothesis H-L4 (LR §7.2).
**Pinned definitions:** outcome classes and the `attention_top_decile` label come from `outcome-thresholds` **v1.0.0**, the version the M4 pilot freezes (ADR-019). If v1.0.0 doesn't exist when the model is frozen (§6.1), the test doesn't start.
**Code commit when written:** `4f55e73`. **Outcome data seen when written:** **none.** No live case has been detected for this test, and no class or label has been computed.
**Timestamp:** the commit that adds this file (see `README.md`).

PRD §9.1 test 1: *"Using live-detected cases, predict the 30-day outcome class from data available 7 days after detection. The model must beat a baseline (star velocity + category base rate) on Brier score over ≥ 200 cases, with a 95% bootstrap CI that excludes zero improvement."* ADR-026 fixes the targets below.

---

## 1. Targets

### 1.1 Primary target (pass/fail; ADR-026 option a)
`y = attention_top_decile`, a binary label: 1 if `A30 ≥ 90`, 0 otherwise.
- `A30` is the mid-rank percentile of `starscout_filtered` stars in `[T, T+30 d)` within the case's (category, quarter) cell, with the OM §3.4 fallbacks.
- **Zero floor:** A30 counts as ≥ 90 only if the star count is > 0 (OM §5.1).
- The label is **final** only when the cell's percentiles are final. That means every case in the cell has passed `T+30 d + settle_lag`, and the cell isn't `small_cohort` (OM §2.4).
- `T` follows ADR-015 / OM §2.2.

### 1.2 Secondary target (reported with equal prominence; not a pass criterion; ADR-026 option b)
The T+90 outcome class, as a 4-class label: {`winner`, `short_lived`, `attention_only`, `plateau`}.
- At T+90, `plateau` is provisional (OM §5.2). It is used here as observed at T+90 + settle lag, and it is not revised when the T+365 check happens.
- `slow_riser` needs "no burst in [T−30 d, T+90 d)", which can't happen for a case opened by a velocity detection. If a case has it anyway because the burst anchor was re-computed, the case is excluded and counted.
- `unclassified` cases are excluded (§4).

---

## 2. Cases and timing

### 2.1 Detection and prediction times
- **Live detection.** A case is "live-detected" if pigtail's **live R1.1 detection pipeline** opened it. That pipeline has two stages:
  1. whatever screening source is in force: the GH Archive scan, or its replacement from M1-T18 (ADR-028);
  2. the R1.1 threshold (`velocity-v0`: ≥ 100 filtered stars in 48 h and z ≥ 3), confirmed on the stargazers-API series (ADR-009, ADR-012).
- **The detection pipeline version is pinned in the freeze addendum (§6.1).** If it changes during evaluation, cases detected under the new version are excluded (X8), unless an amendment brings them in. That amendment must be committed before any of their targets is computed.
- **Detection time `t_det`:** when that pipeline opened the case.
- **Prediction time `p = t_det + 7 d`.**
- **"Data available 7 days after detection":** only data with event timestamps `< p`. For evaluation cases it must also have been **fetched by `p + 24 h`**.

### 2.2 Prospective predictions
- For every eligible live case, the scoring job computes the feature vector and both models' predictions (full and baseline, both targets) at `p`.
- It stores them with a SHA-256 and a timestamp, the same way R11.2 locks predictions.
- If the job fails, predictions may be **reconstructed** later from data with timestamps `< p`, using the same code. They are flagged `reconstructed`, and a sensitivity analysis excludes them (§7.3).

### 2.3 Training set (retrospective; frozen before evaluation)
The models are fit once, on **retrospective** cases:
- `T` is before the start of live capture;
- they are in the calibration split: `sha256("pigtail-outcome-holdout-v1" + case_id) mod 100 ≥ 30` (ADR-019);
- they have a final target.

**How retrospective cases are prepared:**
- `t_det` is re-derived by applying the pinned detection rule (§2.1) to the stargazers-API series, which is the confirming series live detection uses.
- Retrospective screening can't reproduce live screening exactly: a retrospective case wasn't necessarily screened by the live source. The training set may therefore contain cases the live screen would have missed. This is a limitation (§8).
- Features are reconstructed **point-in-time**, from timestamped records `< p` only (§3).

Features that can't be reconstructed point-in-time aren't used in the primary model. Examples: HN points "as of fetch" (OM §1.2), and Bluesky engagement as of fetch.

---

## 3. Predictors (available at `p`)

All flow features count **filtered** stars (`starscout_filtered`, ADR-012 / OM §4) unless stated otherwise. `log1p` means ln(1 + x).

| Id | Feature | Point-in-time source |
|---|---|---|
| F1 | `log1p` stars in `[T, p)` | stargazers API `starred_at` |
| F2 | `log1p` stars in `[p − 48 h, p)` | same |
| F3 | `log1p` of the mean stars/day over `[T − 30 d, T)` | same |
| F4 | stars in `[p − 48 h, p)` ÷ max stars in any 48 h window within `[T, p)` (0 if the max is 0) | same |
| F5 | anchor type (`launch` / `burst`) and `log1p(p − T in days)` | case record |
| F6 | `log10` repo age at T | repo `created_at` |
| F7 | owner type (`Organization` / `User`) | GitHub API |
| F8 | owner prior-audience band: r1–r4 of the stars with `starred_at < T` on the owner's other public repos (the pilot's proxy, pilot §3.6) | stargazers API |
| F9 | primary category (ADR-017 / codebook §9, from inputs frozen at T), one-hot | category assignment |
| F10 | primary language at T: the 10 most frequent in the training set, one-hot, plus `other` | GitHub API |
| F11 | `log1p` issues + PRs opened in `[T, p)` by external non-bot authors | GitHub API `created_at` |
| F12 | `log1p` forks created in `[T, p)` | GitHub API `created_at` |
| F13 | `log1p` primary-ecosystem downloads in `[T, p)` (OM §1.3), plus an ecosystem indicator | registry daily series |
| F14 | count of matched HN stories created in `[T − 7 d, p)` (OM §1.2 matching) | HN Algolia `created_at` |
| F15 | **early breadth** (H-L4, LR [31]): the number of distinct community and publication nodes (codebook §4.2) with a mention item timestamped in `[t_det, min(t_det + 72 h, p))` | captured mention items |

**Missing values.**
- Each feature gets a missing indicator. The missing value is filled with the training median.
- This is how the model handles missing input. It doesn't impute stored data: stored observations stay `unknown` (ADR-014).
- A feature whose source is disabled or held (ADR-010, ADR-022) for the **whole** training set is dropped before freezing. That decision depends on which sources are available, not on outcomes.
- Examples: F14 and F15 while HN is held; F8 for user-owned repos while person-level holds apply.
- Sources enabled during evaluation but not in training are **not** added.

**Not used:** any data timestamped `≥ p`, the outcome percentiles of other cases, and any coded (LLM) field other than the category.

---

## 4. Exclusions (applied before any prediction is scored; counts reported by reason and category)

| Code | Excluded if |
|---|---|
| X1 | the case wasn't opened by the live velocity scan (backfilled, announced-launch without a detection, D5 analyze, manual) |
| X2 | `p` is earlier than the push time of the model-freeze addendum (§6.1) |
| X3 | the target isn't final or is `unknown`: `A30` unknown, `unclassified:small_cohort`, truncated stargazers series, or `missing_data`. For the secondary target, `unclassified` for any reason |
| X4 | `T + 30 d ≤ p`: the target window was already over when the prediction was made (primary target only) |
| X5 | the repo already has an earlier case in the evaluation set; only the first case per repo is kept |
| X6 | one of the owner's launches, or pigtail's own. They are controlled interventions, kept for §9.1 test 4 |
| X7 | the repo was deleted before the target was final (no target can be computed) |
| X8 | the case was detected under a detection pipeline version other than the one pinned in the freeze addendum (§2.1), unless an amendment committed before any of its targets was computed brings it in |

**Not excluded:**
- cases with a StarScout campaign flag (§7.3 sensitivity);
- personal-account repos (the test reports aggregates only; no repo is named);
- repos that also appear in the training set as an earlier case (§7.3 sensitivity).

---

## 5. Models

### 5.1 Baseline ("star velocity + category base rate", PRD §9.1)
**Primary target.** An unpenalized logistic regression (`C = 1e6`) on two inputs:
1. `log1p(v7)`, where `v7` = filtered stars in `[p − 7 d, p)` ÷ 7 (star velocity at day 7 after detection);
2. `logit(r_cat)`, where `r_cat = (k_cat + 1)/(n_cat + 2)` is the smoothed training share of `y = 1` in the case's category.

**Secondary target.** A multinomial logistic regression on the same `log1p(v7)` plus `log(r_cat,c)` for each class `c`, with `r_cat,c = (k_cat,c + 1)/(n_cat + 4)`.

### 5.2 Full model
**Form:** L2-penalized logistic regression; multinomial for the secondary target.
- Inputs: F1–F15 after the §3 drop rule, plus the two baseline inputs.
- Continuous features are standardized with the training mean and SD.

**Penalty C:**
- chosen from {0.01, 0.1, 1, 10};
- by 5-fold cross-validation **on the training set only**, with folds grouped by cohort quarter;
- selection criterion: mean Brier score;
- seed 20260925.

**Implementation:** scikit-learn `LogisticRegression` (`lbfgs`, `max_iter = 5000`), with the version pinned in the addendum. The model form is fixed now. Changing it (for example to gradient boosting or a Hawkes model) after this file is frozen needs an amendment, and it is exploratory if written after evaluation data has been seen.

### 5.3 Pre-registered secondary analysis: H-L4 (not a pass criterion)
- **Model:** the baseline plus F15 (early breadth) only, fit the same way as the baseline.
- **Falsification (LR §7.2):** H-L4 is falsified if the Brier improvement over the baseline has a 95% CI that includes 0 or lies below 0.
- **If F15 had to be dropped (§3):** H-L4 is reported as "not testable: breadth sources held".

---

## 6. Evaluation

### 6.1 Freezing
Before the first evaluation case's `p`, the orchestrator commits an **addendum**. It records:
- the SHA-256 of the fitted baseline and full-model artifacts, and of the training-set manifest (stored privately);
- the feature list after the §3 drop rule, and the chosen C;
- the scikit-learn and numpy versions;
- the `outcome-thresholds` version (v1.0.0) and its SHA-256.

The models don't change after that.

### 6.2 Evaluation set and when the test runs
- **Primary target.** The evaluation set is the **first 200 eligible cases, ordered by `t_det`**, after the §4 exclusions.
- **When it runs.** The primary test runs **once**, as soon as all 200 have final targets. Nobody computes a Brier score, an accuracy or a label distribution for evaluation cases before then. Counting eligible and matured cases is allowed, because it reveals only whether a target exists, not its value.
- **Secondary target.** The secondary test runs once, on the first 200 eligible cases (by `t_det`) with final T+90 classes, as soon as all of them are final.
- **At the M7 report (`docs/reports/evaluation-v1.md`).**
  - If fewer than 200 eligible cases are final, the report states "not yet run (n eligible = x, n final = y)".
  - The test then counts as **not passed** for PRD §11, unless the owner accepts the exception (PRD §11, H4).
  - Evaluation continues after M7 until n = 200, and the result is added to the report when it exists.
- **Additional eligible cases.** Cases that become eligible beyond the first 200 may be reported, but only in a section labelled **exploratory**.

### 6.3 Score
**Brier score (primary):** `BS = (1/n) Σ (p̂_i − y_i)²`.

**Multi-class Brier (secondary):** `BS = (1/n) Σ_i Σ_c (p̂_ic − y_ic)²`, over the 4 classes. The range is [0, 2].

**Improvement:** `Δ = BS_baseline − BS_model`, where Δ > 0 means the model is better. The Brier skill score `1 − BS_model/BS_baseline` is also reported.

### 6.4 Bootstrap CI
- **Method:** a paired nonparametric bootstrap over evaluation cases. Each draw resamples n cases with replacement and recomputes both Brier scores and Δ on the same resample.
- **Resamples:** B = 10,000.
- **Interval:** percentile 95% CI.
- **Seed:** `numpy.random.default_rng(20260925)` for the primary target, `default_rng(20260928)` for the secondary target and `default_rng(20260929)` for H-L4.
- **Scope:** the models are **not** refit inside the bootstrap. The CI covers uncertainty in the evaluation sample given the frozen models, which is what "beat the baseline on these cases" asks.

### 6.5 Pass criterion (primary target only)
The test **passes** if and only if:
- n ≥ 200 evaluation cases;
- Δ > 0;
- the lower bound of the 95% bootstrap CI of Δ is > 0.

Otherwise it **fails**. The report states the failure with the same prominence as a pass would get (analyst rule; WORK_ORDER M7).

---

## 7. Reporting

### 7.1 Always reported
- n eligible, and n excluded by reason and by category;
- the prevalence of `y = 1` (primary) and the class shares (secondary);
- BS for both models, with Δ and its CI;
- reliability diagrams with 10 equal-count bins;
- the Murphy decomposition (reliability, resolution, uncertainty).

### 7.2 Per stratum (descriptive; PRD §5.6)
BS and Δ by category and by owner type, with n shown. Any cell with n < 30 is shown as "insufficient evidence". No per-stratum CI is used for pass or fail.

### 7.3 Sensitivity (pre-registered; doesn't change pass/fail)
- (a) Target and features on the `raw` star series instead of `starscout_filtered` (ADR-020). A pass that flips to a fail is flagged `sensitive_to_fake_star_filter`.
- (b) Excluding `reconstructed` predictions.
- (c) Excluding cases whose repo appears in the training set.
- (d) A block bootstrap that resamples cohort quarters instead of cases. Cases in a quarter share percentile cells, so their targets are not independent.
- (e) Burst-anchored cases only. For launch-anchored cases, part of `[T, T+30)` has already passed at `p`.

### 7.4 Exploratory (labelled as such)
- A self-exciting final-size forecaster (SEISMIC-style, LR [33]) compared with the baseline.
- Features that exist only live (HN points and front-page minutes at `p`, Bluesky engagement at `p`).
- An overall base-rate ("climatology") model.
- Any result on more than 200 cases.

---

## 8. Known limitations (stated in advance)
1. **Retrospective training data is survivor-biased and partial.**
   - Stars from the stargazers API are net of un-stars and deleted accounts (ADR-012).
   - Features rebuilt point-in-time lose whatever was deleted before the fetch.
   - A model trained this way may be miscalibrated on live cases. Calibration is reported (§7.1) rather than corrected after the fact.
2. **Few positives.** With a top-decile target, only a minority of cases are positive, so a Brier difference among 200 cases may be hard to separate from zero. n = 200 is the PRD minimum, not the result of a power calculation; no base rate existed to run one.
3. **Shared percentile cells.** Targets are relative to pigtail's universe and its (category, quarter) cells (OM §0.6), so cases in the same cell aren't independent. §7.3 (d) addresses this.
4. **Held sources.** Sources held under ADR-022 may remove F14 and F15. In that case H-L4 can't be tested.
5. **Live detection isn't settled yet.** ADR-028 found GH Archive coverage of about 0.007 in the M1 smoke run, and the replacement screen (M1-T18) isn't chosen. Until live detection opens cases, no evaluation case can accrue. The retrospective training set follows the pinned detection rule, not the live screen (§2.3).
6. **Leakage the design accepts.** The baseline and the full model both use stars in `[T, p)`, which is part of the target window. The PRD test is designed this way, so it is accepted rather than removed.
