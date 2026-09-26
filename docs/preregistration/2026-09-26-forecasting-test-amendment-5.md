# Amendment 5 to the forecasting-test pre-registration (PRD §9.1 test 1): withdrawal

**Amends (withdraws):**
- `docs/preregistration/2026-09-25-forecasting-test.md`, frozen at commit `3f07692` (`3f0769289e1842d24c1d861e4b05db9fe57ca855`, 2026-09-25T16:10:40+02:00);
- `docs/preregistration/2026-09-25-forecasting-test-amendment-1.md`, frozen at commit `2521652` (`25216526e7ee1cbe857f7d540edbd5f9f4f9c81e`, 2026-09-25T17:18:53+02:00);
- `docs/preregistration/2026-09-25-forecasting-test-amendment-2.md`, frozen at commit `a6a9e3d` (`a6a9e3db29f60e273fdeb94225118c27c60fa9da`, 2026-09-25T18:18:46+02:00);
- `docs/preregistration/2026-09-25-forecasting-test-amendment-3.md`, frozen at commit `9178f90` (`9178f90c9cec73b57b3bdf9146eea26b3bc46a96`, 2026-09-25T18:22:55+02:00);
- `docs/preregistration/2026-09-25-forecasting-test-amendment-4.md`, frozen at commit `c73bf8c` (`c73bf8cb6c8a2a3198a54ab633de0e3f3b8574e6`, 2026-09-25T18:24:32+02:00).

Freezing commits found with `git log --diff-filter=A --format=%h -- <file>` (README rule 2). All five commits are on `origin/main`. None of the five files is edited (README rule 1).

**Task:** M11 (withdrawal amendments, WORK_ORDER §4.4) · **Written:** 2026-09-26 · **Author role:** `analyst`
**Decided by:** ADR-047.3 (the PRD §9.1 test suite is cut), ADR-047.7 (the forecasting pre-registration is withdrawn by dated amendment, not deleted), ADR-048.4 (§9.1 cut; launch-mode cases remain the prospective set for any later evaluation) and ADR-049.5 (the forecasting test, original and amendments 1–4, withdrawn; ADR-019, ADR-026, ADR-039.5 and ADR-042.2–3 superseded).
**Code commit when written:** `0330bd4` (HEAD on 2026-09-26).
**Outcome data seen when written:** **none.** No live case was ever detected for this test. No target, label, class, percentile, feature value, model fit, prediction or Brier score has been computed or looked at, for any training or evaluation case. The model-freeze addendum (original §6.1) was never written, and `outcome-thresholds 1.0.0`, which the test pins, was never frozen. Outcome scoring is not implemented (`src/pigtail/` has no outcome-observation, percentile or class code), and the last verifier check of the database recorded 0 cases and no outcome tables (`ops/RUNLOG.md`, verifier @`c73bf8c`). Nothing in this amendment was informed by data.
**Timestamp:** the commit that adds this file (README rule 2).

## What is withdrawn

The whole forecasting chain: the original and amendments 1–4, every section (targets, cases and timing, predictors, exclusions, models, evaluation, reporting, limitations). Quoted below are the parts that define the test; the withdrawal is not limited to them.

- **Old** (original, the test being pre-registered): "PRD §9.1 test 1: *"Using live-detected cases, predict the 30-day outcome class from data available 7 days after detection. The model must beat a baseline (star velocity + category base rate) on Brier score over ≥ 200 cases, with a 95% bootstrap CI that excludes zero improvement."* ADR-026 fixes the targets below."
- **Old** (original header): "**Pinned definitions:** outcome classes and the `attention_top_decile` label come from `outcome-thresholds` **v1.0.0**, the version the M4 pilot freezes (ADR-019). If v1.0.0 doesn't exist when the model is frozen (§6.1), the test doesn't start."
- **Old** (original §1.1): "`y = attention_top_decile`, a binary label: 1 if `A30 ≥ 90`, 0 otherwise."
- **Old** (original §6.5):
  > The test **passes** if and only if:
  > - n ≥ 200 evaluation cases;
  > - Δ > 0;
  > - the lower bound of the 95% bootstrap CI of Δ is > 0.
- **Old** (amendment 1, F-A2, new text): "`A30` is the mid-rank percentile of `raw` star-history stars over the 30 endpoint days of `[T, T+30 d)` (OM §1.2 day mapping) within the case's (category, quarter) cell, with the OM §3.4 fallbacks."
- **Old** (amendment 2, change 1): "The evaluation set is the eligible live-detected cases **excluding H-sealed** (`m` in 20–29 under the calibration pre-registration's split rule)."
- **Old** (amendment 3, change 1, new text): "The evaluation set is the **first 200 eligible cases, ordered by `t_det`**, after the §4 exclusions **and after excluding H-sealed cases** (`20 ≤ m < 30` under `2026-09-25-threshold-calibration.md` §1.1)."
- **Old** (amendment 4, change 1, new text): "`A30` is the mid-rank percentile of `raw` star-history stars over the 30 endpoint days of `[T, T+30 d)` (OM §1.2 day mapping) within the case's (category, quarter) cell, with the OM §3.4 fallbacks. **Every cell population used for this percentile, for the secondary T+90 class inputs, for sensitivity run (a), and for the "final" status of labels contains calibration and H-eval cases only; H-sealed cases are never members** (calibration pre-registration §1.4)."

**New:** "Withdrawn on 2026-09-26 (ADR-047.3, ADR-047.7, ADR-049.5). The forecasting test is not run. No model-freeze addendum is written. Its targets (`attention_top_decile`, the T+90 class), its training and evaluation sets (calibration split, H-eval, H-sealed), its baseline, predictors, pass criterion and sensitivity runs bind no later analysis."

## Why

1. **The test is cut.** PRD v2.0 §9.1 retires the v1 system tests, including this one (ADR-047.3, ADR-048.4). ADR-026, which fixed its targets, is superseded (ADR-049.5).
2. **Its inputs no longer exist.** It needed live-detected cases from global breakout detection (R1.1, retired; the detection code is deleted in M11, ADR-047.6), outcome classes from `outcome-thresholds 1.0.0` frozen by the M4 pilot's calibration (withdrawn by `2026-09-26-threshold-calibration-amendment-1.md`), (category, quarter) cells (retired, ADR-049.8), and the held-out split with H-eval and H-sealed (ADR-019 and ADR-039.5, superseded by ADR-049.5).
3. **Withdrawn, not deleted.** ADR-047.7 requires a dated amendment so that the record stays public.

**What survives elsewhere (not by reference to these files).** Launch-mode cases remain the prospective set for any later evaluation (ADR-048.4). Predictions for a launch are pre-registered and locked through the experiment registry (PRD R11.1–R11.2) and scored with the Brier score and calibration. Any future forecasting evaluation needs a new pre-registration written before its outcome data exists.

## Analyses affected

Every analysis in the five withdrawn files: the primary and secondary targets, prospective and reconstructed predictions, the training set and its point-in-time features, the baseline and full models, the H-L4 secondary analysis, the evaluation set, Brier scores, bootstrap CIs, the pass criterion, per-stratum and sensitivity reporting, and the exploratory analyses listed there. No other pre-registration depends on this chain.
