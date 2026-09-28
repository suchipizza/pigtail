status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: Owner Directive 001 (ADR-059..073) → M20 docs check, then M21
updated: 2026-09-28
resumed: 2026-09-26 after CR-001/CR-002 (ADR-047/048); work locally, no server

| Milestone (ADR-069/072) | Status | Blocker |
|---|---|---|
| M0, M2, M3, M11, M12 | accepted | — |
| M20 Documents per Owner Directive 001 | **accepted** (2026-09-26) | — |
| M21 Cleanup and privacy | **accepted** (2026-09-26) | — |
| M22 Discovery, relevance, shortlist review, selection (outcome sort, matched losers, balance, sensitivity), `pigtail run --brief` | **accepted 2026-09-28** (verifier round 9, @811eec3; history in RUNLOG and ADR-076–085; selection-v12 / anchor-v11) | brief 1: outcome sort done 2026-09-28 (amendment 2 recorded; 20/20 in views A, B and B-undeclared); next: the coding step (extraction, adjudication) needs the owner's H6 approval; doc minors in BACKLOG M22-D |
| M23 Owner brief #1 pilot (5 cases, cost projection, evidence decay) | **built** 2026-09-28 (ADR-086, migration 0029; synthetic tests only); **fixed** 2026-09-28 after the first live pilot's coder calls were all refused (HTTP 400, grammar too large; no money spent): flat coder output 2.0.0, schema-size guard, exit 7 on all-failed coding, no model or projection from zero cost, re-run redoes failed cases (ADR-086 addendum 1, migration 0030); **fixed again** 2026-09-28 after the second live pilot (coder calls spent all 16,000 output tokens on thinking, `max_tokens`, recorded at USD 0): thinking disabled per job, billed failures recorded as `error_billed` and counted by the budget, a failed standard fallback fails only its case (ADR-086 addendum 2, ADR-087; selection-v13, brief 1 not re-pre-registered); live schema and thinking smoke tests pending (orchestrator); **verifier round 1 conditions fixed** (ADR-086 addendum 3, migration 0031): cost model `case-cost-v3` per call from current-setting ledger rows only (diagnostic, aggregate and superseded rows handled; `pigtail brief pilot-cost --rebuild`), ×1.25 contingency, consistent cost block; per-invocation commit provenance and `pigtail brief pilot-annotate`; decay age at check and late flags; reports in the encrypted backup; OPS-2 | the owner's live pilot run (paid: her `--approve-paid` of the estimate, H6 above the caps); +1/+7-day decay results; verifier; person-level sources stay gaps until CB-12 (H5) and CB-06b |
| M23b Outcome connectors (npm, crates.io, returning contributors; PyPI/deps.dev optional) | not started (ADR-080) | M23; before the owner's second report |
| M24 Owner brief #1 full run and report | not started | M23, cost within cap |
| M25 D3 plan | not started | M24 |
| M26 Launch mode + D5 | not started | M22; M24 (R17.5 validation cases) |
| M27 Operator guide, first brief in under an hour | not started | M24, M25, M26 |
| M28 D4 (v2) | not started | M27, H5 |

Gates: G1/G2 retired (ADR-047); per-report quality rules instead
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 as amended by ADR-073.2 (person-level sources stay off until CB-12, the privacy notice published after the owner's approval via H5, and CB-06b are done). Reports stay in the instance's private data directory (ADR-073.1).
