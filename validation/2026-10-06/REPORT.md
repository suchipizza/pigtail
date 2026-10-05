# Non-curated validation — 2026-10-06 (PRD §13, spec §48.4)

**Engine:** pigtail 0.1.0 at commits `319bb78`–`c1d82a9` · **Model:** `anthropic/claude-opus-5-5` ·
**Discovery:** `anthropic_web_search` (≤10 queries) · default config, no developer cache.

Targets were picked to cover different shapes, not because they are easy: a very large viral repo
(Excalidraw), a large organic repo with many unexplained spikes (Stirling-PDF), a launch-heavy repo
(Bruno), an older organic tool (Datasette), a modest niche project (gokrazy), an archived/abandoned
project with a generic name (maybe-finance/maybe), and four non-OSS products (Linear, beehiiv,
Raycast, Carrd — Carrd chosen as a likely sparse solo-founder case).

Raw runs are kept locally (`validation/2026-10-06/runs/`, gitignored: they contain third-party
excerpts and are not reviewed for publication).

## Results per target

| Target | Kind | Status | Time | Cost | Sources | Claims | Events | Growth episodes (unexplained) | Conflicts | Gaps |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| excalidraw/excalidraw ¹ | repo | completed_with_gaps | 3.3 min | $2.07 | 57 | 239 | 62 | 8 (3) | 2 | 14 |
| usebruno/bruno ² | repo | completed_with_gaps | 4.4 min | $2.39 | 60 | 268 | 65 | 8 (6) | 5 | 18 |
| simonw/datasette | repo | completed_with_gaps | 4.3 min | $2.79 | 69 | 313 | 80 | 8 (1) | 2 | 11 |
| Stirling-Tools/Stirling-PDF | repo | completed_with_gaps | 2.2 min | $1.10 | 23 | 118 | 41 | 8 (6) | 2 | 16 |
| gokrazy/gokrazy | repo | completed_with_gaps | 1.7 min | $0.83 | 23 | 63 | 15 | 8 (2) | 0 | 13 |
| maybe-finance/maybe ³ | repo | completed_with_gaps | 1.9 min | $0.56 | 10 | 37 | 13 | 8 (4) | 0 | 15 |
| linear.app | product | completed | 5.8 min | $4.10 | 47 | 468 | 33 | — | 15 | 10 |
| beehiiv.com | product | completed | 6.2 min | $4.90 | 48 | 583 | 31 | — | 14 | 9 |
| raycast.com | product | completed | 4.7 min | $2.93 | 45 | 328 | 29 | — | 5 | 11 |
| carrd.co | product | completed | 3.5 min | $2.05 | 31 | 199 | 19 | — | 9 | 12 |

¹ First run used the old growth-episode thresholds (scaled to today's total) and found 0 episodes
for a 133K-star repo; fixed (thresholds now use the project's size at the time) and re-run.
² First run **failed**: merging duplicate claims left an event pointing to a removed claim, so the
validator refused to write the bundle (as designed — PRD E8). Fixed (references are remapped) and
re-run.
³ First run found **no** Hacker News stories: the generic name "maybe" pulled in high-scoring
unrelated title matches that crowded out the URL-matched stories. Fixed (URL matches always kept;
title-only matches always go through triage). Re-run after the fix ($0.59): 6 Hacker News stories found; the
January 2024 open-sourcing spike (+12,358 stars) is now associated (timing only) with the HN post linking the
repository; unexplained episodes fell from 4 to 3.

**Totals for the 12 runs (10 targets + 2 re-runs):** ≈ $28.9 model + search; 10 web searches/run
($0.10). Runtime 1.7–6.2 min. Repositories: $0.56–2.79 (median ≈ $2.1). Products: $2.05–4.90
(median ≈ $3.5) — output tokens dominate (extraction of many first-party posts).

## Manual citation audit

32 extracted claims were sampled at random (4 per bundle, seed 20261006, web-sourced claims only)
and checked by fetching each source and reading the cited passage (two independent reviewers).

| Check | Result |
|---|---|
| Source URL is real and about the target | 28 / 28 verifiable (4 unverifiable: the site returns HTTP 403 to automated fetchers) |
| Fabricated sources | **0** |
| Quote present in the source | 28 / 28 verifiable |
| Source supports the statement as written | 26 fully, 2 partially (one turned an implication into a flat fact; one rendered "will offer" as already shipped) |
| Correct entity | 32 / 32 |
| Date correct (where a date was assigned) | 5 / 6 — one "7× since the start of 2026" growth stored as January 2026 instead of Jan–Aug 2026 |
| Unsupported causal statement | **0** |

A separate scan of all narrative blocks, outcomes, event summaries and episode summaries in the 10
bundles for causal wording ("caused", "drove", "led to", "resulted in", …) found only (a) explicit
disclaimers ("not proof that the launch caused the change") and (b) causes attributed by the
company in a cited first-party source ("the company says …"). **No unsupported causal claims.**

Fixes made from the audit (generalizable, not target-specific): extraction rules now require
period claims to carry a start *and* end, and future plans to stay in the future tense; undated
announcements in first-party posts are anchored to the post's publication date with an explicit
label.

## Failure modes observed

1. **Integrity bug caught by the validator** (Bruno): fixed; the validator worked as intended and no
   invalid bundle was written as `research-bundle.json`.
2. **Generic project names** (maybe) confuse name-based Hacker News matching: fixed as above.
3. **Old thresholds hid early spikes of large repos** (Excalidraw): fixed.
4. **Unexplained spikes are common** for organic projects (Stirling-PDF 6/8, Bruno 6/8): these are
   shown as "No high-confidence public event found." Likely causes are link-only platforms (Reddit,
   X) and third-party posts that do not link the repository.
5. **Some sites block automated fetches** (HTTP 403); these become `blocked` fetches and gaps.
6. **Product runs cost more** than the original $1–3 estimate because first-party sitemaps surface
   many milestone posts; documented as $2–5.
7. **Noisy metrics** (technical benchmarks extracted as "metrics"); reconstruction prompt now
   restricts metrics to growth/business measures.

## Quality bar and release decision

Targets recorded before release (PRD §13.3): 100% of displayed material claims traceable (enforced
by the validator — met); 0 fabricated sources (met in audit); 0 unsupported causal statements (met);
≥ 90% full citation entailment on the audit sample (met: 26/28 = 93%); sparse targets degrade
honestly (maybe-finance: 37 claims, 15 gaps, 4 unexplained episodes — met).

**Caveat:** the audit was performed by AI reviewers under the maintainer's direction, not by a
human. The owner should spot-check before the public launch.
