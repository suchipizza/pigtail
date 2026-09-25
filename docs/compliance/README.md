# Compliance pack

**Status: draft for legal review — not legal advice.**

pigtail's compliance agent prepared these documents. It is not a lawyer. They prepare material for the owner's external lawyer at gate **H2** (WORK_ORDER §5). Until that review, pigtail enforces the conservative defaults stated in each document.

Pack version: 0.1 · 2026-09-25 · Task M3-T2. All sources cited were accessed on 2026-09-25.

## Files

| File | What it is | Main legal anchors |
|---|---|---|
| [lia.md](lia.md) | Legitimate-interest assessment: the purpose, necessity and balancing tests, the safeguards, the conditions, and the outcome for each processing activity (A1–A7) | GDPR Art. 6(1)(f); EDPB Guidelines 1/2024; FADP Art. 6, 30, 31 |
| [dpia.md](dpia.md) | Light DPIA: whether a DPIA is required (yes), the data inventory D1–D11, risks R1–R14 rated by likelihood and severity, measures mapped to implemented or planned controls, residual risk, and **backlog CB-01…CB-21** | GDPR Art. 35–36; WP248 rev.01; FADP Art. 22–23 |
| [retention-policy.md](retention-policy.md) | Retention classes (24 months for person-level data, unlimited for project-level), a store-by-store schedule, pseudonym key management, deletion sync per source, data-subject requests, LLM retention per backend, backups and logs | GDPR Art. 5(1)(e), 17; FADP Art. 6(4); terms memos |
| [privacy-notice.md](privacy-notice.md) | Public privacy notice template for data subjects, in plain language. The `[operator]` and contact fields must be filled in before publishing. | GDPR Art. 14, 21; FADP Art. 19, 20 |
| [terms-memos.md](terms-memos.md) | Per-source platform terms memos TM-01…TM-31 and the clearance decisions (M2) | Platform terms, cited per memo |
| [legal-review-questions.md](legal-review-questions.md) | A consolidated, numbered list for the lawyer: LQ-1…LQ-26, each with context, the default applied meanwhile and what it blocks. It carries over terms-memos Q1–Q12 (mapping table inside). | — |

## Reading order for the lawyer
1. [legal-review-questions.md](legal-review-questions.md), the summary table at the end
2. [lia.md](lia.md)
3. [dpia.md](dpia.md)
4. [retention-policy.md](retention-policy.md)
5. [privacy-notice.md](privacy-notice.md)
6. [terms-memos.md](terms-memos.md), as needed per source

## Key conclusions (draft)
- **DPIA required:** yes. WP248 criteria 3 (systematic monitoring), 5 (large scale) and 6 (combining datasets) are met. With the planned controls in place, no high residual risk remains, so no prior consultation is needed. The lawyer should confirm this (LQ-11).
- **Legitimate interest:** supported for the velocity scan, mention capture, LLM coding, scoring, the library and planner, and aggregate publication, on condition that the safeguards exist. **Account-level spread graphs are on hold** (LQ-8).
- **Controls that must exist before the capture layer runs on the production host:**
  - CB-01 retention job
  - CB-03 encryption at rest
  - CB-04 GH Archive raw-dump minimisation
  - CB-09 key management
  - CB-12 published notice
  - CB-16 breach runbook
  - CB-17 backups
  - CB-18 log hygiene
- **Before any person-level source beyond GH Archive** (Bluesky, HN, V2EX, Discord) is enabled, every pre-condition in **ADR-022** (`ops/DECISIONS.md`, the single authoritative list) must exist:
  - CB-01 retention purge
  - CB-02 deletion sync
  - CB-03 encryption at rest
  - CB-06 identifier redaction before LLM calls (redactor extensions)
  - CB-08 data-subject request tooling
  - CB-12 published notice
  - CB-13 honouring explicit refusals
- **Code status:** several capture-layer controls (pseudonymisation at ingest in `connectors/base.py`, `ConnectorGapError`, the bot drop, `velocity.py`, the `retention_class` and `deletion_state` fields) are M1 work that is **not yet merged to `main`**. The pack labels them "I (M1, pending merge)". GH Archive capture runs locally in development only; there is no production capture (ADR-022).
- **LLM:**
  - "Zero-retention" (PRD §10) is achievable only with `api` plus an Anthropic ZDR agreement.
  - `subscription` mode runs under the Consumer Terms: no DPA, no ZDR, training exceptions, and a "no commercial or business purposes" sentence (LQ-1, LQ-2). It stays limited to the owner's own use with training switched off.
  - CB-07 is implemented: the CLI subprocess runs with `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and `DISABLE_FEEDBACK_COMMAND` set, and `BackendError` no longer echoes CLI output (`src/pigtail/llm/subscription.py`, tested in `tests/unit/test_subscription_backend.py`).

## Not in this pack yet
- A record of processing activities (CB-15)
- A breach-response runbook (CB-16)
- An operator-guide section on controller duties (CB-21)

## Maintenance
Update the pack when a source is added, when a platform's or Anthropic's terms change, when H2 answers arrive, and at least every 12 months. Every change goes in the changelog below.

## Changelog
- 2026-09-25: v0.1. LIA, DPIA, retention policy, privacy notice, legal-review questions and this index created (M3-T2). terms-memos.md unchanged apart from a link to this index.
- 2026-09-25 — fixes after verifier M3 round 1: person-level-source pre-conditions now point to ADR-022 (CB-01, 02, 03, 06, 08, 12, 13); uncommitted M1 controls labelled "I (M1, pending merge)"; CB-07 marked implemented.
