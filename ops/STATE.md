status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: Owner Directive 002 (ADR-088) → M24 report and M25 D3 plan delivered 2026-09-29; awaiting the owner's acceptance
updated: 2026-09-30

## Handover (2026-09-30)
- Repository renamed to `suchipizza/pigtail-analysis` (ADR-092). A separate project named `pigtail` (other path, own `pigtail` command) may later be combined with this one, or not; internal names stay unchanged until the owner decides.
- Nothing is running. main is pushed. Postgres must be started by hand after a reboot (`brew services run postgresql@16`); the CLI needs `.env` loaded.
- Waiting on the owner (private detail outside git): acceptance of brief 1's report (then `report brief --final`, after checking the cache-purge-v2 preview); plan inputs (then re-run `pigtail plan` and `plan lock` before launch); post-D3 scope decision (fast-path verdict v2 under ADR-088.6d: continue; only the star-series and burst components caught what the fast path missed); two exemplar questions; removal of three merged agent worktrees.
- Due: M23 +7-day evidence-decay check after 2026-10-05 17:39Z, then a short M23 sign-off.
- Known local issue: two backup integration tests fail on this machine (`pg_dump` connects with a role that doesn't exist locally); unrelated to recent changes.
resumed: 2026-09-26 after CR-001/CR-002 (ADR-047/048); work locally, no server

| Milestone (ADR-069/072) | Status | Blocker |
|---|---|---|
| M0, M2, M3, M11, M12 | accepted | — |
| M20 Documents per Owner Directive 001 | **accepted** (2026-09-26) | — |
| M21 Cleanup and privacy | **accepted** (2026-09-26) | — |
| M22 Discovery, relevance, shortlist review, selection (outcome sort, matched losers, balance, sensitivity), `pigtail run --brief` | **accepted 2026-09-28** (verifier round 9, @811eec3; history in RUNLOG and ADR-076–085; selection-v12 / anchor-v11) | brief 1: outcome sort done 2026-09-28 (amendment 2 recorded; 20/20 in views A, B and B-undeclared); next: the coding step (extraction, adjudication) needs the owner's H6 approval; doc minors in BACKLOG M22-D |
| M23 Owner brief #1 pilot (5 cases, cost projection, evidence decay) | **built** 2026-09-28 (ADR-086, migration 0029; synthetic tests only); **fixed** 2026-09-28 after the first live pilot's coder calls were all refused (HTTP 400, grammar too large; no money spent): flat coder output 2.0.0, schema-size guard, exit 7 on all-failed coding, no model or projection from zero cost, re-run redoes failed cases (ADR-086 addendum 1, migration 0030); **fixed again** 2026-09-28 after the second live pilot (coder calls spent all 16,000 output tokens on thinking, `max_tokens`, recorded at USD 0): thinking disabled per job, billed failures recorded as `error_billed` and counted by the budget, a failed standard fallback fails only its case (ADR-086 addendum 2, ADR-087; selection-v13, brief 1 not re-pre-registered); live schema and thinking smoke tests pending (orchestrator); **verifier round 1 conditions fixed** (ADR-086 addendum 3, migration 0031): cost model `case-cost-v3` per call from current-setting ledger rows only (diagnostic, aggregate and superseded rows handled; `pigtail brief pilot-cost --rebuild`), ×1.25 contingency, consistent cost block; per-invocation commit provenance and `pigtail brief pilot-annotate`; decay age at check and late flags; reports in the encrypted backup; OPS-2 | the owner's live pilot run (paid: her `--approve-paid` of the estimate, H6 above the caps); +1/+7-day decay results; verifier; person-level sources stay gaps until CB-12 (H5) and CB-06b |
| M23b Outcome connectors (npm, crates.io, returning contributors; PyPI/deps.dev optional) | not started (ADR-080) | M23; before the owner's second report |
| M24 Owner brief #1 full run and report | **delivered 2026-09-29, awaiting owner acceptance** (ADR-088/089 + addenda 1–5; migration 0032): 147 cases coded (USD 13.51), report-facts-v3, report with 25 narratives (100% of claims resolve). Verifier: round 1 CONDITIONAL, round 2 (the single pass under ADR-088.5) FAIL with 5 fixes; fixes merged (e1668aa) and checked by the orchestrator by test and count (HN confirmation bases only links_repo/renamed/homepage, 0 stale labels, open bursts never shown as size). API spend 2026-09-29: USD 14.44 of ADR-088's USD 25 | owner acceptance; then `report brief --final` (check the cache-purge-v2 preview first); tightened asset rules (assets-v3) not re-measured on real data |
| M25 D3 plan | **delivered 2026-09-29, awaiting owner acceptance** (ADR-091 + addenda 1–2; rank-v3: view A only, same direction in other views, within-pair permutation check): plan on brief 1 generated and reproducible (USD 0; 4 recommendations, permutation p = 0.07, labelled "not distinguishable from chance"; calendar insufficient evidence; plan inputs missing). Verifier: round 2 (single pass) FAIL; fixes merged (ccea9ff) and checked by the orchestrator | owner plan inputs (assets, hours/week, launch window), then `plan lock` before launch; post-D3 scope decision (fast-path verdict v2: continue under ADR-088.6d; keep star-series and burst components) |
| M26 Launch mode + D5 | not started | M22; M24 (R17.5 validation cases) |
| M27 Operator guide, first brief in under an hour | not started | M24, M25, M26 |
| M28 D4 (v2) | not started | M27, H5 |

Gates: G1/G2 retired (ADR-047); per-report quality rules instead
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 as amended by ADR-073.2 (person-level sources stay off until CB-12, the privacy notice published after the owner's approval via H5, and CB-06b are done). Reports stay in the instance's private data directory (ADR-073.1).
