# Amendment 4 — Forecasting test: restore amendment 1's A30 wording; correct section references in amendment 3

- **Amends:** `2026-09-25-forecasting-test-amendment-3.md` (frozen at `9178f90`), which amended `2026-09-25-forecasting-test.md` (frozen at `3f07692`), `2026-09-25-forecasting-test-amendment-1.md` (frozen at `2521652`) and `2026-09-25-forecasting-test-amendment-2.md` (frozen at `a6a9e3d`).
- **Date:** 2026-09-25.
- **Outcome data seen:** none. The database holds 0 cases; no outcome, class, percentile, evaluation target or model exists.
- **Why:** Amendment 3, change 2 (a) cited "original §2" for two sentences that are in **§1.1** (Primary target), and (b) wrote a new `A30` sentence that dropped amendment 1's day-mapping clause ("over the 30 endpoint days of `[T, T+30 d)` (OM §1.2 day mapping)") without saying so. The newest amendment governs, so the day mapping for the primary target had become unclear. All quotes below are copied verbatim from the frozen files.
- **Affects:** the primary target `A30` (§1.1) and, through it, the evaluation.

## Changes

### 1. `A30` definition (§1.1)
- **Old** (amendment 3, change 2): "`A30` is the mid-rank percentile of `raw` star-history stars in `[T, T+30 d)` within the case's (category, quarter) cell, with the OM §3.4 fallbacks. **Every cell population used for this percentile, for the secondary T+90 class inputs, for sensitivity run (a), and for the \"final\" status of labels contains calibration and H-eval cases only; H-sealed cases are never members** (calibration pre-registration §1.4)."
- **New:** "`A30` is the mid-rank percentile of `raw` star-history stars over the 30 endpoint days of `[T, T+30 d)` (OM §1.2 day mapping) within the case's (category, quarter) cell, with the OM §3.4 fallbacks. **Every cell population used for this percentile, for the secondary T+90 class inputs, for sensitivity run (a), and for the "final" status of labels contains calibration and H-eval cases only; H-sealed cases are never members** (calibration pre-registration §1.4)."

This is amendment 1's F-A2 sentence, unchanged, followed by amendment 3's H-sealed sentence, unchanged.

### 2. Section references in amendment 3, change 2
Both sentences amendment 3 cited as "original §2" are in **original §1.1** (Primary target): the `A30` sentence (original line 17) and the final-status sentence (original line 19): "The label is **final** only when the cell's percentiles are final. That means every case in the cell has passed `T+30 d + settle_lag`, and the cell isn't `small_cohort` (OM §2.4).". Amendment 3's reading of the final-status sentence ("every case in the cell" means every calibration or H-eval case in the cell) stands, now attached to §1.1.

## Not changed
Everything else in the original and amendments 1–3, including amendment 3's changes 1 and 3 (evaluation set and training set).
