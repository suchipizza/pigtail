status: ACTIVE
# ACTIVE | BLOCKED (every remaining task waits on a human gate) | DONE

milestone_focus: M0 → M1, M2
updated: 2026-09-25

| Track | Milestone | Status | Blocker |
|---|---|---|---|
| A capture | M1 | ready to start | host VM and GCP (H1) for deploy / BigQuery; local dev not blocked |
| B research | M2 | in progress (literature review, source matrix) | — |
| C methodology | M3, M4 | not started | M2 |
| D engine | M0 | done locally; CI on GitHub pending first push | — |
| D engine | M5, M8 | not started | G1 / G2 |
| E analysis | M6, M7 | not started | M5 |
| D engine (v2) | M10 | not started | M9 |

Gates: G1 pending · G2 pending
Human gates: H1 OPEN (raised 2026-09-25, default action on 2026-10-09)
