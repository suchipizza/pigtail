# Cost ledger
Budget ceilings: USD 200/month for the API (owner, Anthropic Console limit and `BUDGET_USD_MONTH`); per brief `budget.money_usd` (brief 1: USD 90); other paid services USD 0 (H6).

| Month | LLM | BigQuery | Hosting | Other APIs | Total |
|---|---|---|---|---|---|
| 2026-09 | 2.76 (API, all brief 1: expansion 0.04, relevance 0.59, selection-stage Haiku 0.27, M23 pilot 0.49, billed failed pilot attempts and diagnostics 1.38) | 0 | 0 | 0 | 2.76 |

## Pilot (M23), brief 1 — counts and averages only (case detail in the private report)

| Date | Pilot | Cost | Projection for the full brief |
|---|---|---|---|
| 2026-09-28 | 5 cases double-coded and adjudicated (claude-sonnet-5, batch, thinking disabled) | pilot cases' actual spend USD 0.667 (USD 0.133/case, includes one billed failed attempt under the superseded thinking setting); measured model `case-cost-v3` from the clean calls only: USD 0.0977/case (coder A 0.0396, coder B 0.0396, adjudication 0.0185) | USD 18.01 for 147 cases (142 remaining), USD 21.83 with the ×1.25 contingency; within the USD 90 brief cap |

Billed-but-unrecorded spend found 2026-09-28 (max_tokens results ledgered at USD 0 before ADR-087): USD 1.38, back-filled in the ledger; the monthly figure (local usage store) lacks it (BACKLOG OPS-2).
