# Compliance pack

**Status: draft for legal review — not legal advice.**

pigtail's compliance agent prepared these documents. It is not a lawyer. They prepare material for the owner's external lawyer at gate **H2** (WORK_ORDER §5). Until that review, pigtail enforces the conservative defaults stated in each document.

Pack version: 0.1 · 2026-09-25 · Task M3-T2. All sources cited were accessed on 2026-09-25.

## Files

| File | What it is | Main legal anchors |
|---|---|---|
| [lia.md](lia.md) | Legitimate-interest assessment: the purpose, necessity and balancing tests, the safeguards, the conditions, and the outcome for each processing activity (A1–A7) | GDPR Art. 6(1)(f); EDPB Guidelines 1/2024; FADP Art. 6, 30, 31 |
| [dpia.md](dpia.md) | Light DPIA: whether a DPIA is required (yes), the data inventory D1–D14, risks R1–R15 rated by likelihood and severity, measures mapped to implemented or planned controls, residual risk, and **backlog CB-01…CB-33** | GDPR Art. 35–36; WP248 rev.01; FADP Art. 22–23 |
| [retention-policy.md](retention-policy.md) | Retention classes (24 months for person-level data, 16 days by default (ceiling 30; ADR-038) for per-repo GitHub event actors, unlimited for project-level), a store-by-store schedule, pseudonym key management, deletion sync per source, data-subject requests, LLM retention per backend, backups and logs | GDPR Art. 5(1)(e), 17; FADP Art. 6(4); terms memos |
| [privacy-notice.md](privacy-notice.md) | Public privacy notice template for data subjects, in plain language. The `[operator]` and contact fields must be filled in before publishing. | GDPR Art. 14, 21; FADP Art. 19, 20 |
| [terms-memos.md](terms-memos.md) | Per-source platform terms memos TM-01…TM-33 and the clearance decisions (M2; TM-32 and TM-33 from the detection re-plan, ADR-032) | Platform terms, cited per memo |
| [ropa.md](ropa.md) | Record of processing activities per activity (A1 GH Archive, A1b per-repo events, A1c GitHub detection, A2a HN rank poller, A2b HN mentions, A3 LLM coding, A7 publication, A8 UI audit log, A9 alerts and logs, A10 backups and exports, A11 requests and refusal list), with recipients, transfers, retention and security measures. Controller fields are placeholders (CB-15). | GDPR Art. 30; FADP Art. 12; DPO Art. 24 |
| [controller-duties.md](controller-duties.md) | What an operator must do as controller: adopt the LIA and DPIA, publish the notice, keep the RoPA, handle requests, retention, deletion sync, security and backups, breaches, subscription vs api scope, EU/CH hosting, purpose limits (CB-21). Linked from `docs/guides/operator.md`. | GDPR Art. 4(7), 5(2), 12, 14, 28, 30, 32–35; FADP Art. 5(j), 8, 9, 12, 19, 24, 25 |
| [runbooks/key-rotation.md](runbooks/key-rotation.md) | `PSEUDONYM_KEY` management: where it lives, separate backup, what a rotation breaks, manual rotation procedure, compromise response, missing tooling (CB-09; CB-25…CB-29) | GDPR Art. 4(5), 32; FADP Art. 8; EDPB Guidelines 9/2022 ¶77 |
| [runbooks/breach.md](runbooks/breach.md) | Breach response: detection, triage, containment (incl. public-repo leaks), risk assessment, notifications, breach register, templates (CB-16) | GDPR Art. 4(12), 33, 34; FADP Art. 5(h), 24; DPO Art. 15; EDPB Guidelines 9/2022 |
| [legal-review-questions.md](legal-review-questions.md) | A consolidated, numbered list for the lawyer: LQ-1…LQ-31, each with context, the default applied meanwhile and what it blocks. It carries over terms-memos Q1–Q12 and maps TM-32/TM-33 to LQ-28/LQ-29 (mapping table inside). | — |

## Reading order for the lawyer
1. [legal-review-questions.md](legal-review-questions.md), the summary table at the end
2. [lia.md](lia.md)
3. [dpia.md](dpia.md)
4. [retention-policy.md](retention-policy.md)
5. [privacy-notice.md](privacy-notice.md)
6. [ropa.md](ropa.md), [controller-duties.md](controller-duties.md) and the [runbooks](runbooks/)
7. [terms-memos.md](terms-memos.md), as needed per source

## Key conclusions (draft)
- **DPIA required:** yes. WP248 criteria 3 (systematic monitoring), 5 (large scale) and 6 (combining datasets) are met. With the planned controls in place, no high residual risk remains, so no prior consultation is needed. The lawyer should confirm this (LQ-11).
- **Legitimate interest:** supported for the velocity scan, mention capture, LLM coding, scoring, the library and planner, and aggregate publication, on condition that the safeguards exist. **Account-level spread graphs are on hold** (LQ-8).
- **Controls that must exist before the capture layer runs on the production host:**
  - CB-01 retention job (**done**: `pigtail retention purge`)
  - CB-03 encryption at rest (**partly done**: SeaweedFS SSE via `S3_SSE_KEK` and the `pigtail doctor` check exist; enabling it on the host and encrypting the Postgres volume are operator duties)
  - CB-04 GH Archive raw-dump minimisation (**partly done**)
  - CB-09 key management (**runbook done**: [runbooks/key-rotation.md](runbooks/key-rotation.md); rotation tooling CB-25…CB-27 open, so the key is rotated only after a compromise)
  - CB-12 published notice
  - CB-16 breach runbook (**documentation done**: [runbooks/breach.md](runbooks/breach.md); the operator fills in roles and contacts)
  - CB-17 backups
  - CB-18 log hygiene (**partly done**: redaction filter and `runs.error` scrubbing; open: the filter in every service, log rotation)
- **Before any person-level source beyond GH Archive** (Bluesky, HN, V2EX, Discord) is enabled, every pre-condition in **ADR-022** (`ops/DECISIONS.md`, the single authoritative list) must exist:
  - CB-01 retention purge (**done**)
  - CB-02 deletion sync (per source: **HN done**, `pigtail privacy deletion-sync --source hn`; **GitHub per-repo events met** by short retention plus the raw drop at parse, no sync source, ADR-038; **GH Archive**: raw purge ≤ 30 days and replay drops opted-out persons; **Bluesky pending**, no connector yet)
  - CB-03 encryption at rest (**partly done**, see above)
  - CB-06 identifier redaction before LLM calls (**partly done**: profile URLs, DIDs and per-source namespaces; open: gists, avatar URLs, bare handles in author fields)
  - CB-08 data-subject request tooling (**done** for access and erasure; rectification and a separate lookup command are not built)
  - CB-12 published notice (open)
  - CB-13 honouring explicit refusals (**done**: `pigtail privacy optout`)

  Status after the privacy-controls merge (`83843ca`, ADR-030) and ADR-038: CB-01, CB-08 and CB-13 are done; CB-03 and CB-06 are partly done; CB-02: HN done / GitHub events met via ADR-038 / Bluesky pending; CB-12 is open. No person-level source beyond GH Archive may be enabled yet.
- **Code status:** the capture-layer controls (pseudonymisation at ingest in `connectors/base.py`, `ConnectorGapError`, the bot drop, `velocity.py`, the `retention_class` and `deletion_state` fields, CB-04 raw purge) are merged to `main` and labelled "I". The privacy operations of ADR-030 are merged at `83843ca`: the retention purge (CB-01), LLM cache expiry and evidence links (CB-05), redactor extensions (CB-06, partly), access and erasure requests (CB-08), the opt-out list (CB-13), log scrubbing (CB-18, partly) and the `pigtail doctor` encryption check (CB-03, partly). Code: `src/pigtail/privacy/`, `src/pigtail/logsafe.py`, `src/pigtail/pseudonymize.py`, `src/pigtail/llm/store.py`, `migrations/0003_privacy_operations.sql`; operator commands in `docs/guides/operator.md` "Privacy operations". GH Archive capture runs locally in development only; there is no production capture (ADR-022).
- **GitHub detection v1 (M1-T24, ADR-037):** the project-level `github` connector (watch-list GraphQL counts, search sweeps, star-history) runs once `GITHUB_TOKEN` is set; search pages are kept as `person_level_24m` because they embed owner objects. The person-level `github_events` connector (per-repo star and fork events) is built with **CB-22** (`person_level_30d`, named after its 30-day ceiling: `GITHUB_EVENTS_RETENTION_DAYS` is 16 days by default (ceiling 30; ADR-038), daily purge) and **CB-23** (only `WatchEvent`/`ForkEvent`, raw pages dropped at parse with hash and tombstone kept, no stargazer-list path; tested), but stays **off by default** (`PIGTAIL_ENABLE_GITHUB_EVENTS` plus the ADR-022 flag; ADR-036) until CB-12 exists (CB-03 and CB-06 partly done). There is no GitHub deletion-sync source; CB-02 for this source is met by the 16-day expiry plus the raw drop at parse (ADR-038), and LQ-29 items 4–5 stay open for the lawyer. Detection-v1 cases open with `bot_filter` `unavailable` meanwhile (ADR-037.1).
- **LLM:**
  - "Zero-retention" (PRD §10) is achievable only with `api` plus an Anthropic ZDR agreement.
  - `subscription` mode runs under the Consumer Terms: no DPA, no ZDR, training exceptions, and a "no commercial or business purposes" sentence (LQ-1, LQ-2). It stays limited to the owner's own use with training switched off.
  - CB-07 is implemented: the CLI subprocess runs with `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and `DISABLE_FEEDBACK_COMMAND` set, and `BackendError` no longer echoes CLI output (`src/pigtail/llm/subscription.py`, tested in `tests/unit/test_subscription_backend.py`).

## Not in this pack yet
- A signed controller version of any document: every file here is a template or the owner's draft until H2.
- Tooling for key rotation (CB-25…CB-29) and backups (CB-17); see [dpia.md](dpia.md) §9.

## Maintenance
Update the pack when a source is added, when a platform's or Anthropic's terms change, when H2 answers arrive, and at least every 12 months. Every change goes in the changelog below.

## Changelog
- 2026-09-25: v0.1. LIA, DPIA, retention policy, privacy notice, legal-review questions and this index created (M3-T2). terms-memos.md unchanged apart from a link to this index.
- 2026-09-25 — fixes after verifier M3 round 1: person-level-source pre-conditions now point to ADR-022 (CB-01, 02, 03, 06, 08, 12, 13); uncommitted M1 controls labelled "I (M1, pending merge)"; CB-07 marked implemented.
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — CB statuses updated after privacy-controls merge (ADR-030): CB-01, CB-05, CB-08, CB-13 done; CB-03, CB-06, CB-18 partly done (key conclusions, code status).
- 2026-09-25 — detection re-plan (ADR-032): terms memos TM-32 (OpenDigger mirror, GAP pending LQ-28, off by default) and TM-33 (GitHub star-history endpoint and per-repo events, cleared with conditions); legal questions LQ-27, LQ-28 and LQ-29; index updated.
- 2026-09-25 — CB-22/23 implemented (M1-T24, ADR-037): key conclusions add the GitHub detection v1 status (connector off by default, remaining holds CB-02 and CB-12); file index counts updated (D1–D14, R1–R15, CB-01…CB-23).
- 2026-09-25 — ADR-038 wording (16-day default; CB-02 per source)
- 2026-09-25 — backlog count CB-01…CB-24 (CB-24 added, ADR-038).
- 2026-09-25 — CB-09, CB-15, CB-16, CB-21: added ropa.md, controller-duties.md, runbooks/key-rotation.md and runbooks/breach.md; operator guide links to them; key conclusions updated (CB-09 runbook and CB-16 documentation done); backlog count CB-01…CB-33 (CB-25…CB-33 added); LQ-30 and LQ-31 added.
