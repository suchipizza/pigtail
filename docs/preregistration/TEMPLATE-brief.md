# Pre-registration template: one brief version (copy, fill in, commit, push)

> **This file is a template, not a pre-registration.** Copy it to
> `docs/preregistration/YYYY-MM-DD-brief-<short-slug>.md` (the slug must not reveal the brief:
> `brief-1`, `brief-2` is fine), fill it in, commit it, **push it**, then record it:
>
> ```
> pigtail brief preregister <brief-id> --print-hashes          # values for §1
> git add docs/preregistration/<file> && git commit && git push
> pigtail brief preregister <brief-id> --file docs/preregistration/<file> --commit <sha>
> ```
>
> The selection stage (`pigtail run --brief <id>`, the outcome sort) refuses to run until the
> brief version has a recorded pre-registration (PRD R8.2, ADR-065, outcome-model §5.8; exit
> code 7). Delete this quote block in your copy.

**Requirements:** PRD R8.2 (per-brief pre-registration), R4.3, R4.8, R4.9, R18.8; outcome-model
v2.1 §5.8; codebook §7.2 (writing a hypothesis); pre-registration README rules 1–7.
**Written:** YYYY-MM-DD · **Author role:** `analyst` or the brief's user
**Code commit when written:** `<short sha>` (`git rev-parse --short HEAD`)
**Outcome data seen when written:** none. The brief version's outcome sort has not run: no
percentile, winner, loser or balance statistic of this brief version exists. (If anything was
seen, say exactly what; the analysis is then exploratory, README rule 3.)
**Timestamp:** the commit that adds this file, as pushed to `origin` (README rule 2).

## Rule for this file: no brief content (R8.2, README rule 5)

This file is public. It must not contain the brief's text: no description, no field or
problem statement, no target users, no keywords, no reference cases or distribution exemplars
(names or URLs), no competitor names. Where the analysis depends on a brief value, this file
commits its **SHA-256** (§1) and the private brief file holds the value. `pigtail brief
preregister` refuses a file that quotes a free-text value of the brief.

## 1. What this pre-registration is bound to

Paste the output of `pigtail brief preregister <brief-id> --print-hashes`:

| Item | Value |
|---|---|
| `brief_id` (private id, not content) | `<brief-id>` |
| `brief_version` | `<n>` |
| `brief_sha256` (the whole brief version's content hash) | `<64 hex>` |
| `success_definition_sha256` (primary dimension, percentile thresholds and minimums, metrics, business minimum, weights, `if_not_applicable`, the no-measurable-adoption rule) | `<64 hex>` |
| `selection_params_sha256` (N winners and losers, exact-match keys, SMD target, headline exclusion SMD, calipers, matching rule, widening steps allowed, fallback steps, exemplar matching, sensitivity alternatives, selection / outcome-model / analysis-params versions) | `<64 hex>` |
| `selection_version` | `selection-v2` |
| `outcome_model_version` | `2.1` |
| Codebook version | `<x.y.z>` |

How the two partial hashes are computed (anyone holding the private brief can re-check them):
`success_definition_sha256 = sha256(canonical_json(Definition.from_brief(brief).to_dict()))` and
`selection_params_sha256 = sha256(canonical_json(Context.from_brief(brief).params()))`, where
`canonical_json` is JSON with sorted keys, no spaces and UTF-8 (`pigtail.briefs.model.sha256_json`;
`pigtail.briefs.preregistration.hashes`). Neither contains brief text.

## 2. Hypotheses (codebook §7.2)

List the pattern hypotheses this brief checks, before the outcome sort. Use the seed ids
(`MC-01` … `MC-13`, `docs/methodology/mechanisms/candidates.md`) where a seed fits; write brief-specific ones
in the same form without brief content (describe the mechanism, not the project).

| id | seed | Pattern (present / absent, coded field) | Expected direction among winners vs matched losers | Coded fields it rests on |
|---|---|---|---|---|
| H1 | MC-xx | … | … | … |

## 3. Tests and decision rules

- **Contrast:** per hypothesis, n present among winners vs among matched losers, with the
  counterexamples (PRD §5.2, F20), per outcome dimension. Headline patterns use headline pairs
  only (ADR-054.1, ADR-078); every other pair is shown in the case-level view.
- **Minimum evidence:** "insufficient evidence in this neighbourhood" below 10 known cases per
  side or 3 cases where the pattern is present (ADR-050.3).
- **Reliability:** per-field Krippendorff's α; findings resting on a field with α < 0.70 are
  labelled "low reliability" (ADR-065).
- **Balance:** SMD per covariate after matching, target |SMD| < 0.25; contrasts depending on a
  covariate that misses it are labelled `balance_limited` (outcome-model §5.6). No re-matching.
- **Sensitivity:** the alternatives fixed in the brief (committed in `selection_params_sha256`)
  are reported; a pattern that holds only under the baseline definition is labelled
  definition-sensitive (outcome-model §8).
- Anything else, or anything changed after the outcome sort, is **exploratory** (README rule 3).

## 4. Deviations

None at the time of writing. Later changes go into a dated amendment (README rule 3), never
into this file.
