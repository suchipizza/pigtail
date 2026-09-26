status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: re-plan per ADR-047/048 → first end-to-end neighbourhood report on the owner's project
updated: 2026-09-26
resumed: 2026-09-26 after CR-001/CR-002 (ADR-047/048); work locally, no server

| Milestone | Status | Blocker |
|---|---|---|
| M0, M2, M3 | accepted (v1) | — |
| M11 Re-scope cleanup | next | — |
| M12 Research brief v1 | next | M11 |
| M13 Discovery + shortlist | not started | M12, GITHUB_TOKEN |
| M14 Batch runs + launch mode | not started | M12, M13, H1 (FileVault) |
| M15 Pilot = owner's neighbourhood report | not started | M13, M14, CB-12, H3, H5 |
| M16–M19 | not started | M15+ |

Gates: G1/G2 retired (ADR-047); per-report quality rules instead
Human gates: H1 OPEN, H2 OPEN (both raised 2026-09-25, default action on 2026-10-09)
Interim holds: ADR-022 (no production capture until the CB controls exist)
