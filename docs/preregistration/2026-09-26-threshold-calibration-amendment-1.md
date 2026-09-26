# Amendment 1 to the outcome-threshold calibration pre-registration: withdrawal

**Amends (withdraws):** `docs/preregistration/2026-09-25-threshold-calibration.md`, frozen at commit `a6a9e3d` (`a6a9e3db29f60e273fdeb94225118c27c60fa9da`, 2026-09-25T18:18:46+02:00). Freezing commit found with `git log --diff-filter=A --format=%h -- docs/preregistration/2026-09-25-threshold-calibration.md` (README rule 2); it is on `origin/main`. That file is not edited (README rule 1).
**Task:** M11 (withdrawal amendments, WORK_ORDER §4.4) · **Written:** 2026-09-26 · **Author role:** `analyst`
**Decided by:** ADR-047.7 (the global-calibration pre-registration is withdrawn by dated amendment, not deleted) and ADR-049.5 (the threshold calibration is withdrawn; ADR-019, ADR-039.5 and ADR-042.2–3 are superseded: threshold freeze, H-sealed holdout, holdout guard, settle-lag collection), with ADR-049.8 (global normalization cells retired) and ADR-049.12 (global classes and thresholds v0.2.0 retired as a selection method; the files stay in git as history).
**Code commit when written:** `0330bd4` (HEAD on 2026-09-26).
**Outcome data seen when written:** **none.** No outcome observation has been summarised, and no percentile, class, provisional label or class base rate has been computed or looked at, for any case in any split, under any thresholds version. The pilot selection run (its §4 step 1) never ran. Addendum 1 (the §1.3 manifest) was never written. K1 and K2 were never computed; no settle-lag re-fetch sample was analysed. `outcome-thresholds 1.0.0` does not exist. Outcome scoring is not implemented (`src/pigtail/` has no outcome-observation, percentile or class code), and the last verifier check of the database recorded 0 cases and no outcome tables (`ops/RUNLOG.md`, verifier @`c73bf8c`). Nothing in this amendment was informed by data.
**Timestamp:** the commit that adds this file (README rule 2).

## What is withdrawn

The whole file: the principle (§0), the data and split rules (§1, including H-eval and H-sealed), the calibrated and fixed parameters (§2), the report-only diagnostics D1–D12 (§3), the procedure and the v1.0.0 freeze (§4), the rules for changes after the freeze (§5), the report (§6), limitations (§7) and open issues (§8). Quoted below are the parts that define what it did; the withdrawal is not limited to them.

- **Old** (header, timestamp): "**It must be committed and pushed before any class, class base rate or outcome percentile is computed for any case.** The pilot's selection run is the first such computation, and pilot amendment 2 (B6) puts it after this commit."
- **Old** (§1.1, split table):
  > | `m ≥ 30` | **calibration** | 70 % | pilot selection and coding samples; everything in this document before the freeze |
  > | `m < 20` | **H-eval** (held-out, evaluation) | 20 % | untouched until `outcome-thresholds 1.0.0` is pushed; after that, classed under v1.0.0 and used by the forecasting test and the M6/M7 analyses |
  > | `20 ≤ m < 30` | **H-sealed** (held-out, sealed) | 10 % | never classed or inspected until a post-freeze threshold change needs a held-out re-run; used **once**, for that re-run (§5.3) |
- **Old** (§2.1): "**K1. `normalization.min_cell_n`** (v0.2.0: 30; OM §9 item 3). Menu: {30, 50}."
- **Old** (§4 step 6): "**Freeze.** Commit and push `schemas/outcome-thresholds/v1.0.0.json` and `docs/preregistration/YYYY-MM-DD-threshold-calibration-addendum-2.md`."
- **Old** (§5.3): "**The held-out re-run uses H-sealed, once.**"

**New:** "Withdrawn on 2026-09-26 (ADR-047.7, ADR-049.5). No calibration is run, `outcome-thresholds 1.0.0` is never frozen, and no addendum is written for this file. The split into calibration, H-eval and H-sealed, the K1 and K2 rules, the diagnostics and the post-freeze route bind no later analysis. `schemas/outcome-thresholds/v0.1.0.json` and `v0.2.0.json` stay in git as history (ADR-049.12); no case is ever classed under them for selection."

## Why

1. **There is nothing global left to calibrate.** Winners and losers are now chosen per brief by the brief's success definition (one primary dimension plus minimum thresholds; ADR-047.2, PRD R18.8, §8.2), with percentiles computed within the brief's final shortlist (ADR-049.8, PRD R3.4). The global classes, their thresholds and their (category, quarter) cells, which this file calibrated and froze, are retired as a selection method (ADR-049.12, PRD R3.5).
2. **Its held-out machinery is superseded.** ADR-019 (threshold freeze and held-out split) and ADR-039.5 (H-sealed) are superseded by ADR-049.5; the split code, the holdout guard and log, and settle-lag collection are removed in M11 (ADR-042.2–3 superseded). Within a brief, the protection against tuning after outcomes is different: the success definition and sensitivity alternatives are fixed in the brief version before its outcome sort (pre-registered for the pilot, WORK_ORDER §6), and every report carries a sensitivity check of its winner set (PRD R4.9).
3. **Its only consumers are withdrawn.** The pilot's selection (`2026-09-26-pilot-amendment-3.md`) and the forecasting test (`2026-09-26-forecasting-test-amendment-5.md`).
4. **Withdrawn, not deleted.** ADR-047.7 requires a dated amendment so that the record stays public.

## Analyses affected

Every analysis in the withdrawn file: the split and its test vectors, the calibration data set and manifest, K1 (`min_cell_n`) and K2 (per-source `settle_lag`), diagnostics D1–D12, the v1.0.0 assembly, verifier check and freeze, H-eval classing, the H-sealed re-run, and `docs/reports/threshold-calibration.md`. The per-source `settle_lag` default of 3 days remains a fixed measurement default in `docs/specs/outcome-model.md` v2 (§1.1), changeable only by ADR; it is not calibrated.
