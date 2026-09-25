# Amendment 3 — Forecasting test: H-sealed excluded from target cells; old/new text for amendment 2

- **Amends:** `2026-09-25-forecasting-test.md` (frozen at `3f07692`), `2026-09-25-forecasting-test-amendment-1.md` (frozen at `2521652`) and `2026-09-25-forecasting-test-amendment-2.md` (frozen at `a6a9e3d`).
- **Date:** 2026-09-25.
- **Outcome data seen:** none. The database holds 0 cases; no outcome, class, percentile, evaluation target or model exists.
- **Why:** Amendment 2 removed H-sealed cases from the evaluation set but said the targets were unchanged. The primary target `A30` is a percentile within the case's (category, quarter) cell, so read literally those cells still contained H-sealed cases. That conflicts with `2026-09-25-threshold-calibration.md` §1.4 ("H-sealed cases are excluded from every cell population"). Amendment 2 also didn't quote old text next to new text (README rule 3). This amendment does both.
- **Affects:** the primary and secondary targets, the evaluation set, sensitivity run (a), and the "final" status of targets.

## Changes

### 1. Evaluation set (replaces amendment 2, change 1, with quotes)
- **Old** (original §6.2): "The evaluation set is the **first 200 eligible cases, ordered by `t_det`**, after the §4 exclusions."
- **New:** "The evaluation set is the **first 200 eligible cases, ordered by `t_det`**, after the §4 exclusions **and after excluding H-sealed cases** (`20 ≤ m < 30` under `2026-09-25-threshold-calibration.md` §1.1)." The same exclusion applies to the secondary target's evaluation set (original §6.2, "Secondary target"). The report states how many live cases were excluded as H-sealed.

### 2. Target cells exclude H-sealed
- **Old** (original §2, as amended by amendment 1 to the `raw` series): "`A30` is the mid-rank percentile of … stars in `[T, T+30 d)` within the case's (category, quarter) cell, with the OM §3.4 fallbacks."
- **New:** "`A30` is the mid-rank percentile of `raw` star-history stars in `[T, T+30 d)` within the case's (category, quarter) cell, with the OM §3.4 fallbacks. **Every cell population used for this percentile, for the secondary T+90 class inputs, for sensitivity run (a), and for the \"final\" status of labels contains calibration and H-eval cases only; H-sealed cases are never members** (calibration pre-registration §1.4)."
- **Old** (original §2): "The label is **final** only when the cell's percentiles are final. That means every case in the cell has passed `T+30 d + settle_lag`, and the cell isn't `small_cohort` (OM §2.4)."
- **New:** the same sentence, where "every case in the cell" means every calibration or H-eval case in the cell.

### 3. Training set (restates amendment 2, change 2, unchanged)
Training uses calibration-split cases only. H-eval and H-sealed are never used for training.

## Not changed
Predictors, the baseline model, the Brier score, the 200-case minimum, bootstrap resamples and seeds, the pass rule, the other exclusions, and the other sensitivity runs.
