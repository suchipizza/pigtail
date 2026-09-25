# Amendment 2 — Forecasting test (PRD §9.1 test 1): exclude the sealed holdout

- **Amends:** `2026-09-25-forecasting-test.md` (frozen at `3f07692`) and `2026-09-25-forecasting-test-amendment-1.md` (frozen at `2521652`).
- **Date:** 2026-09-25.
- **Outcome data seen:** none. No case exists in the database, no outcome, class or evaluation target has been computed, and no model has been fitted.
- **Reason:** ADR-039 item 5 and `2026-09-25-threshold-calibration.md` §1.1 split the 30 % held-out set into H-eval (20 % of all cases) and H-sealed (10 %). H-sealed must never be classed or inspected except for a single post-freeze threshold re-run. The frozen forecasting text evaluates on all eligible live cases, which would include H-sealed cases.

## Changes
1. **Evaluation set.** The evaluation set is the eligible live-detected cases **excluding H-sealed** (`m` in 20–29 under the calibration pre-registration's split rule). The "first 200 eligible cases by detection time" count is taken after this exclusion.
2. **Training set.** Unchanged: calibration-split cases only. H-eval and H-sealed are never used for training.
3. **Reporting.** The report states how many live cases were excluded as H-sealed.
4. **Everything else** (targets, predictors, baseline, Brier score, the 200-case minimum, bootstrap resamples and seeds, the pass rule, exclusions, sensitivity runs) is unchanged.
