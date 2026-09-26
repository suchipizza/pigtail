# Legal review H2: the narrowed consult

**Status:** open (gate H2). This is not legal advice; it was prepared by pigtail's documentation agent, which is not a lawyer.
**Version:** 1.0 · 2026-09-26 · Source: Owner Directive 001 §9 (`ops/OWNER_DIRECTIVE_001.md`), logged as ADR-067; gates per Directive §10 / ADR-068.
**Replaces** the broad consult in [legal-review-questions.md](legal-review-questions.md) (LQ-1 to LQ-31, v0.1 of 2026-09-25), which is kept as **historical** background. Facts and article quotations below that come from that file were accessed on 2026-09-25 and are cited to it.

## What H2 blocks

- **H2 blocks publishing only**, and publishing is disabled (H4; Directive §8.6, §9, §10; ADR-066.6, ADR-067).
- **H2 does not block collection** under the mitigations listed below (Directive §9).
- Separately, the interim holds of ADR-022 still apply: person-level mention sources (HN and Bluesky mention search) stay off until the owner's privacy notice is published (CB-12, ADR-049.2). Publishing the notice is an H5 action; the draft is shown to the owner first (ADR-071.4).

## Setting (for the lawyer)

- pigtail is open-source software (MIT) that each user installs and runs on their own machine with their own credentials. It ships no data, no hosted service and no default targets (Directive §1, §8.8; ADR-059, ADR-066.8).
- The owner runs one instance, as a **personal project for now**: no service, no customers, and no findings leave the instance (Directive §1, ADR-059).
- The instance studies a small set of **shortlisted open-source projects** chosen for one research brief, plus their matched comparison projects, and collects public posts that **mention those projects** (Directive §2, §8.3).
- Product LLM calls run on the owner's Anthropic API key under Anthropic's Commercial Terms and the data processing agreement that comes with them. **Inference happens in the US** (Directive §6.1, §8.7; ADR-064.1, ADR-066.7).

## Mitigations in force (apply to all three questions)

Directive §8 (ADR-066) and the owner's decisions in ADR-071:

1. **Code, then discard identities.** Coded data records **roles and buckets** (maintainer, account by follower bucket, newsletter, community, organization). **Handles and personal names are never stored** in coded data; organizations and projects may be named. The earlier pseudonymized-handle data is migrated, then the handles and pseudonyms are purged (§8.1, ADR-066.1).
2. **Opt-out fingerprint only.** For a person who opts out, pigtail keeps an HMAC fingerprint under a secret key stored apart from the data, used only to exclude and purge that person in future runs, and described in the privacy notice (ADR-071.1).
3. **Bot filtering in memory.** The outcome is stored on the coded record ("automated account" plus the rule version), not as a handle (ADR-071.2).
4. **Time-limited snapshots.** Raw snapshots are stored locally, encrypted at rest (FileVault required), and kept only until the brief's report is final **plus 12 months**; a scheduled, logged purge job then deletes them and keeps only the coded facts and the content hash (§8.2, ADR-066.2).
5. **Minimal collection.** Mentions of **shortlisted projects only**; no sweeps of individual users or accounts; no follower lists (§8.3, ADR-066.3).
6. **Sources.** Official APIs only; rate limits respected with margin; deletions honoured. Reddit metadata only (link, title, score, timestamp) until API approval. Trendshift off by default. No scraping around authentication, paywalls or terms (§8.4, ADR-066.4).
7. **Outputs.** Snapshots are never reproduced; at most one short attributed excerpt per source (§8.5, ADR-066.5).
8. **Publishing disabled.** No findings, data or reports leave the instance. The public repo holds only code, docs, templates and synthetic fixtures, and a CI private-data scan enforces this (§8.6, ADR-066.6).
9. **LLM processing** on the API only, under the Commercial Terms' data processing agreement; US inference recorded in the compliance docs (§8.7, ADR-066.7).
10. **Public privacy notice** for the owner's instance, in a separate repo on GitHub Pages: controller, a dedicated contact alias, purpose, sources, what is and isn't stored, retention, legal basis, how to opt out or object, and US processing (ADR-071.4).

---

## Q1 · Legitimate interest and GDPR territorial scope

**Question (Directive §9.1):** Does legitimate interest (FADP; GDPR Art. 6(1)(f)) hold for collecting and coding public posts about shortlisted projects under these mitigations? Does the GDPR apply to the owner through Art. 3(2)(b) ("monitoring behaviour")?

**Context:**
- Purpose: learn how comparable open-source launches spread, by coding who published and redistributed public posts about a handful of shortlisted projects, at the level of roles and buckets, and comparing winners with matched losers (PRD F4, F5, F20).
- Scale: one brief's shortlist (by default 20 winners and 20 matched losers in the field panel, plus any distribution examples; PRD R18.1), not a sweep of platforms or users.
- What is kept long term: coded facts about roles and buckets, content hashes, and opt-out fingerprints; raw snapshots only until report final + 12 months.
- GDPR Art. 3(2)(b) applies to controllers not established in the Union where processing relates to "monitoring … behaviour" of people in the Union; Recital 24 refers to whether persons "are tracked on the internet" ([legal-review-questions.md](legal-review-questions.md), LQ-13).
- The earlier assessment of this basis is in [lia.md](lia.md); it was written for the pre-directive scope (global collection, pseudonymized handles) and is to be rewritten as a template in a later step (Directive §9, ADR-067).

**Mitigations in force:** all ten above; most relevant are 1, 4, 5 and 8 (identities discarded after coding, time-limited snapshots, shortlisted projects only, nothing published).

**Default meanwhile:** collection and coding continue under the mitigations; nothing is published (Directive §9, §10).

## Q2 · Is a public privacy notice enough?

**Question (Directive §9.2):** Is a public privacy notice enough (GDPR Art. 14(5)(b) and the FADP equivalent), or must individuals be informed?

**Context:**
- The data is not collected from the people concerned, and pigtail holds no contact data for them.
- GDPR Art. 14(5)(b) exempts cases where informing people "proves impossible or would involve a disproportionate effort", provided "appropriate measures" are taken, "including making the information publicly available". FADP Art. 20(2)(b) exempts "disproportionate effort" when data is not collected from the data subject ([legal-review-questions.md](legal-review-questions.md), LQ-10).
- Contacting posters individually would mean replying to or messaging them on the platforms, which the earlier consult judged intrusive and possibly platform spam (LQ-10), and which would be an external action needing H5.
- The planned notice: a separate repo on GitHub Pages describing the owner's instance (not the tool), with the contents listed in mitigation 10. The draft is shown to the owner before publication (ADR-071.4, H5). The pigtail repo keeps a generic template ([privacy-notice.md](privacy-notice.md), to be rewritten in a later step).

**Mitigations in force:** all ten above; most relevant are 1, 2, 5 and 10 (no handles kept, an opt-out that works without storing identities, minimal collection, the public notice).

**Default meanwhile:** a public notice only, no individual contact. Person-level mention sources stay off until the notice is published (CB-12, via H5) and the rest of the LLM-path redaction (CB-06b) is done (ADR-022 as amended by ADR-073.2). Nothing is published.

## Q3 · Reddit's terms (optional)

**Question (Directive §9.3):** Optional, **only if Reddit is enabled beyond metadata**: Reddit's terms for this use.

**Context:**
- Reddit is a documented gap in the source matrix (TM-05, ADR-010).
- The earlier consult recorded that Reddit's Wiki says keeping deleted content, "even if … de-identified or anonymized", violates its terms, and asked whether a content hash plus coded, non-verbatim facts could be kept, and whether sending text to an LLM provider under a DPA counts as "sharing with a third party" under the Developer Terms §7.2 ([legal-review-questions.md](legal-review-questions.md), LQ-3). Those texts were not re-checked for this document.
- Under the directive, Reddit is **metadata only** (link, title, score, timestamp) until Reddit API approval is granted (Directive §8.4, ADR-066.4).

**Mitigations in force:** 6 (metadata only, official API, deletions honoured) plus 1, 4 and 7.

**Default meanwhile:** Reddit stays metadata only, and only once the source matrix records that clearance; this question is not asked unless the owner decides to use Reddit beyond metadata.

---

## Closed: the Anthropic questions

**Status: CLOSED** (Directive §9, ADR-067), because pigtail's product calls run on the owner's Anthropic API key under the Commercial Terms and their data processing agreement (Directive §6.1, §8.7; ADR-064.1, ADR-066.7), not on a consumer subscription.

| Question in [legal-review-questions.md](legal-review-questions.md) | Topic | Status |
|---|---|---|
| LQ-1 | Subscription-mode LLM processing: is Anthropic a processor or an independent controller? | **Closed**: product calls don't use the subscription. |
| LQ-2 | Consumer Terms: "not … for any commercial or business purposes" | **Closed**: same reason. The `subscription` backend stays in the code only for other users' individual, non-commercial use on their own plan (PRD R15.6). |
| LQ-3 | (see note) | See note. |

**Note on numbering.** The directive refers to "the Anthropic questions (LQ-1 to LQ-3)". In the historical file, only **LQ-1 and LQ-2** concern Anthropic; **LQ-3 is the Reddit question** (formerly terms-memos Q2), which is carried forward as Q3 above rather than closed. The related question **LQ-22** (whether pseudonymized or redacted text is personal data *for the LLM provider*) only qualified LQ-1; it is not part of the narrowed consult. This discrepancy is flagged for the owner to confirm.

## Other historical questions

LQ-4 to LQ-31 in [legal-review-questions.md](legal-review-questions.md) are **not part of the narrowed consult** (Directive §9). That file is kept unchanged as history; several of its premises (global collection, pseudonymized handles, 24-month retention, subscription-mode coding) no longer describe the product.

## Changelog
- 2026-09-26: v1.0 created from Owner Directive 001 §9 (ADR-067), with the mitigations of §8 (ADR-066) and ADR-071.
