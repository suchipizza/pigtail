# Data-cache inventory — 2026-09-26

**Purpose.** WORK_ORDER §4.4 (M11): inventory the data already collected, kept as a cache that
briefs can reuse (ADR-047.6, ADR-051.2). Counts only: no repo names, handles, URLs, ids or row
content.

- **Data version:** migration `0014` (latest applied; `0014_rescope_drop_global_collection.sql`).
- **Code commit:** `f9e2b29` (HEAD when generated), plus the uncommitted M11 verifier fixes that
  add the command below.
- **Instance:** the operator's local Compose database (`DATABASE_URL` on `localhost:5432`).
- **Generated with:** `uv run pigtail report inventory` (read-only; `--json` for machine output).
  The output below is verbatim.

## Output

```text
data version (latest migration): 0014
code commit: f9e2b29f49fab5c7290306b39b6f8e49c8486c15

| table | group | rows | distinct repos | first day (UTC) | last day (UTC) |
|---|---|---:|---:|---|---|
| repos | cache | 0 | 0 | — | — |
| cases | cache | 0 | 0 | — | — |
| evidence | cache | 17 | 0 | 2026-09-25 | 2026-09-25 |
| evidence_upstream_items | cache | 0 | — | — | — |
| repo_star_daily | cache | 0 | 0 | — | — |
| star_history_fetch | cache | 0 | 0 | — | — |
| repo_event_actor | cache | 0 | 0 | — | — |
| repo_event_poll | cache | 0 | 0 | — | — |
| repo_event_daily_agg | cache | 0 | 0 | — | — |
| hn_story | cache | 0 | 0 | — | — |
| hn_mention | cache | 0 | 0 | — | — |
| hn_rank_poll | cache | 0 | — | — | — |
| hn_rank_observation | cache | 0 | — | — | — |
| upstream_items | cache | 0 | — | — | — |
| github_http_cache | cache | 0 | — | — | — |
| github_budget_ledger | cache | 0 | — | — | — |
| launch_mode_window | cache | 0 | 0 | — | — |
| runs | operational | 4 | — | 2026-09-25 | 2026-09-25 |
| deletion_log | operational | 2 | — | 2026-09-26 | 2026-09-26 |
| privacy_requests | operational | 0 | — | — | — |
| privacy_suppression | operational | 0 | — | — | — |
| pseudonym_key_fingerprint | operational | 0 | — | — | — |
| pseudonym_key_fingerprint_log | operational | 0 | — | — | — |
| ui_sessions | operational | 1 | — | 2026-09-25 | 2026-09-25 |
| ui_audit_log | operational | 1 | — | 2026-09-25 | 2026-09-25 |
| schema_migrations | operational | 14 | — | 2026-09-25 | 2026-09-26 |
```

`—` = the table has no repo reference or no time column, or has no rows. Days are UTC.
`cache` tables are reusable by briefs; `operational` tables hold runs, deletion tombstones,
privacy, UI and migration state.

## Reading

- The cache is effectively empty. The only captured rows are 17 `evidence` records from the
  GH Archive connector, all with raw bytes already dropped (`deletion_state = raw_dropped`),
  linked to no repo or case (distinct repos 0).
- The 2 `deletion_log` rows are the count-only tombstones migration 0014 wrote when it dropped
  the global-collection tables (reason `purpose_limitation`).
- No repos, cases, star history, per-repo events or HN data are held, so a first brief starts
  from a cold cache.

## Purpose limitation (ADR-047.6)

This data is kept only as a cache briefs can reuse. After the first brief's shortlist is final,
data that no brief references is deleted (logged in `deletion_log` with reason
`purpose_limitation`, counts only).
