# Data protection impact assessment (light DPIA)

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Task M3-T2
**Controller:** "[operator]", meaning whoever runs the deployment. For the owner's deployment this is the owner. Self-hosters must adopt their own DPIA (LQ-14).
**Access date:** every URL was accessed on **2026-09-25**.
**Related:** [lia.md](lia.md) · [retention-policy.md](retention-policy.md) · [privacy-notice.md](privacy-notice.md) · [terms-memos.md](terms-memos.md) · [legal-review-questions.md](legal-review-questions.md)

**Legal texts used:**
- GDPR Art. 35 and 36, from [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj), read via the Publications Office text at http://publications.europa.eu/resource/celex/32016R0679 because EUR-Lex returned a bot challenge.
- Swiss FADP Art. 22 and 23, from [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en), consolidated version of 2023-09-01, read via the fedlex filestore.
- Article 29 Working Party, *Guidelines on Data Protection Impact Assessment … WP248 rev.01* (adopted 4 April 2017, revised 4 October 2017), [PDF](https://ec.europa.eu/newsroom/article29/redirection/document/47711). The EDPB lists them as endorsed: [EDPB endorsed WP29 guidelines](https://www.edpb.europa.eu/our-work-tools/general-guidance/endorsed-wp29-guidelines_en).
- EDPB Guidelines 01/2025 on Pseudonymisation (adopted 16 January 2025, public consultation), [PDF](https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf).

---

## 1. Is a DPIA required?

### 1.1 GDPR
Art. 35(1) requires a DPIA where processing "is likely to result in a high risk". Art. 35(3) lists three mandatory cases:
- (a) systematic and extensive evaluation on which decisions with legal or similarly significant effect are based: **no**, because pigtail takes no decisions about individuals;
- (b) large-scale special categories: **no**, because none are sought (only incidental);
- (c) "systematic monitoring of a publicly accessible area on a large scale": **unclear**. The phrase is usually read as physical spaces, but continuous capture of public online activity streams is similar in effect.

WP248 rev.01 lists nine criteria and says processing that meets **two** of them will in most cases require a DPIA:

| WP248 criterion | pigtail | Met? |
|---|---|---|
| 1. Evaluation or scoring, including profiling | Projects are scored (outcome classes). Accounts are ranked by reach in burst-to-trigger attribution (R5.5). | **Partly**: projects yes, and accounts in Tier 2 and Tier 3 cases |
| 2. Automated decision with legal or similar effect | None | No |
| 3. Systematic monitoring ("including data collected through networks") | Continuous capture of GH Archive and, later, the Bluesky Jetstream (R1.1, R1.2) | **Yes** |
| 4. Sensitive or highly personal data | Not sought. May be present incidentally in free text. | Low |
| 5. Large scale | GH Archive covers all public GitHub activity, so millions of people | **Yes** |
| 6. Matching or combining datasets | GitHub, HN, Bluesky and registries are joined per case | **Yes** |
| 7. Vulnerable data subjects | Minors may be present but cannot be detected | Low |
| 8. Innovative use of technology | LLM coding of posts | **Arguably yes** |
| 9. Prevents exercising a right or using a service | No | No |

**Conclusion: at least three criteria are met (3, 5 and 6), so a DPIA is required under the GDPR.** A "light" DPIA is acceptable as a format only if it contains the Art. 35(7) elements: (a) a systematic description, (b) necessity and proportionality, (c) risks, and (d) measures. This document provides them in §2–§8.

Two things are still open:
- The views of data subjects (Art. 35(9), "where appropriate") have not been sought. We propose publishing this DPIA summary with the notice and inviting comment (CB-12).
- Each national supervisory authority publishes its own Art. 35(4) list. We have not checked the list of the operator's lead authority (LQ-11).

### 1.2 Swiss FADP
Art. 22(1) requires a DPIA if processing "is likely to result in a high risk to the data subject's personality or fundamental rights". Art. 22(2) says high risk depends on "the nature, extent, circumstances and purpose" and arises "in particular" in two cases:
- (a) large-scale processing of sensitive data: no;
- (b) systematic large-scale monitoring of public areas: arguable, as in §1.1.

**Conservative conclusion: treat the DPIA as required under the FADP as well.** The content requirements in Art. 22(3) (description, risk evaluation, measures) are covered below.

If after the measures a **high residual risk** remained, the controller would have to consult the FDPIC (FADP Art. 23) or the supervisory authority (GDPR Art. 36(1)). Our assessment in §8 is that no high residual risk remains **once** the planned controls are in place. Until they are, the related processing is on hold (see [lia.md](lia.md) §7).

---

## 2. Description of the processing

### 2.1 Purposes
Evidence-based research on how open-source projects grow, together with the planner and analyzer built on it ([lia.md](lia.md) §2, I1–I5). Personal data is a by-product: pigtail analyses projects, not people.

### 2.2 Data flow

```
platform APIs ──fetch──▶ snapshot store (raw bytes, SHA-256, private S3/local)   [raw, person-level]
                              │
                              ├─parse──▶ records: handles → HMAC pseudonyms        [pseudonymised]
                              │             │
                              │             ├─▶ Postgres: evidence, cases, repo_hourly_activity, (later) actors/edges
                              │             └─▶ in-memory per-actor features (never persisted)
                              │
                              └─text──▶ redactor (e-mail, phone, @mention) ──▶ LLMClient ──▶ Anthropic (api | subscription)
                                                                                   │
                                                                                   └─▶ llm_cache (SQLite → Postgres): coded outputs
private UI (operator auth) ◀── all of the above                      public outputs ◀── aggregates only (after H2 + H4)
```

### 2.3 Data inventory

| # | Category | Examples | Source (memo) | Stored where | Form | Retention class | Who can access |
|---|---|---|---|---|---|---|---|
| D1 | **GitHub event actors** | login, id, avatar URL, event type, time, repo | GH Archive (TM-01), GitHub API (TM-02) | Raw hourly dump in the snapshot store. Parsed actor as a pseudonym, in memory only. Aggregates in `repo_hourly_activity`. | Raw (snapshot) / pseudonymised / aggregate | `person_level_24m` (snapshot); aggregates `derived_aggregate` | Operator (host and storage admin) |
| D2 | **GitHub free text inside raw dumps** | issue, PR and comment bodies and titles, user objects in payloads. Historic dumps may include commit author names and e-mail addresses (older Events API format; the current docs list no `commits` array for PushEvent: https://docs.github.com/en/rest/using-the-rest-api/github-event-types). Treat them as present. | GH Archive | Snapshot store only. Not parsed today. | Raw | `person_level_24m` | Operator |
| D3 | **Posts and comments mentioning a repo** | text, author handle or DID, time, parent id, engagement counts | HN (TM-03, TM-04, TM-30), Bluesky (TM-06), V2EX (TM-19). **Not enabled yet.** | Snapshot store (raw), Postgres (parsed, pseudonymised) | Raw / pseudonymised | `person_level_24m` | Operator |
| D4 | **Account reach** | follower counts, karma | Same as D3 | Postgres | Pseudonymised (bands planned, CB-10) | `person_level_24m` | Operator |
| D5 | **Spread-graph nodes and edges** | pseudonym, platform, edge type, evidence level | Derived (R5.3) | Postgres. Not built yet. | Pseudonymised | `person_level_24m` | Operator, private UI |
| D6 | **LLM inputs** | redacted snapshot text | Derived | Sent to Anthropic. **Not stored by pigtail** except as an input hash (`src/pigtail/llm/client.py`). | Redacted | n/a (in transit) | Anthropic (see §2.5) |
| D7 | **LLM outputs (cache)** | coded fields, quoted spans (R7.1), provenance | Derived | `llm_cache` table (`src/pigtail/llm/store.py`), under `PIGTAIL_DATA_DIR` | Redacted/pseudonymised. **Quoted spans are verbatim text.** | Currently **none**: no TTL (CB-05) | Operator |
| D8 | **Project-level data** | repo id, `owner/repo` name, stars, downloads, releases | All cleared sources | Postgres, snapshot store | Project-level. `owner` may be a person's login. | `project_level` (unlimited) | Operator; aggregates may be public |
| D9 | **Operational logs and ledgers** | run records (`runs.error`, counts), LLM usage ledger, pause log | Internal | Postgres, SQLite | Should contain no personal data. `BackendError` messages no longer echo CLI output: they carry only the exit code, `subtype` and `api_error_status` (`src/pigtail/llm/subscription.py` `parse()`; tested by `test_cb07_error_message_does_not_echo_output` in `tests/unit/test_subscription_backend.py`) (CB-07, implemented). | 12 months (policy, [retention-policy.md](retention-policy.md)) | Operator |
| D10 | **Pseudonym key** | `PSEUDONYM_KEY` | Operator | Host environment / secrets manager, backed up separately (H1) | Secret | For the life of the dataset | Operator only |
| D11 | **Manual entries** | press citations (TM-31), careers facts (TM-29) | Operator | Postgres | Organisation-level. Investors are named as organisations only. | `project_level` | Operator |

**Hosting:** the default region is the EU or Switzerland (PRD §10). The current compose file binds Postgres and object storage to `127.0.0.1` only (`docker-compose.yml`).

### 2.4 Who accesses the data
- The operator and anyone they authorise on the private UI (R13.3; operator authentication is **planned**).
- Hosting and storage providers chosen by the operator, acting as processors.
- Anthropic, for D6 (see §2.5).
- No one else. The public repository must never contain D1–D7 (CI private-data scan: `scripts/private_data_scan.py`, `.github/workflows/ci.yml`).

### 2.5 LLM processing by backend

| | `api` backend (`src/pigtail/llm/api.py`) | `subscription` backend (`src/pigtail/llm/subscription.py`) |
|---|---|---|
| Contract | Anthropic Commercial Terms ([link](https://www.anthropic.com/legal/commercial-terms)). They incorporate the DPA ([link](https://www.anthropic.com/legal/data-processing-addendum)): "Customer is the controller and Anthropic is Customer's processor." | Consumer Terms for Free, Pro and Max (https://code.claude.com/docs/en/legal-and-compliance). For EEA and Swiss consumers the counterparty is Anthropic Ireland, Limited ([consumer terms](https://www.anthropic.com/legal/consumer-terms), effective 8 October 2025). |
| Anthropic's role | Processor | **Unclear.** Anthropic's Privacy Policy names Anthropic Ireland as the controller for EEA, UK and Swiss users ([privacy](https://www.anthropic.com/legal/privacy)). There is no DPA. Is the operator disclosing data to an independent controller? See LQ-1. |
| Training | "Anthropic may not train models on Customer Content from Services." (Commercial Terms) | Training happens if the account setting is on. Even when it is off, Materials are used for training if they are flagged for safety review or submitted as feedback (Consumer Terms). |
| Retention at Anthropic | Default: deleted "within 30 days of receipt or generation" ([privacy center](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)). ZDR is available by agreement per organisation, not per request ([API retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)). Flagged content may be kept up to 2 years even under ZDR. | Training off: 30 days. Training on: up to 5 years. Flagged content: 2 years; classifier scores: 7 years; feedback: 5 years ([privacy center](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data)). **ZDR is not available** for consumer plans (API retention page: "What ZDR does not cover … Claude Free, Pro, and Max plans, including … Claude Code"). |
| Commercial use | Allowed | The Consumer Terms (EEA/CH) say: "You agree that you will not use our Services for any commercial or business purposes". The sentence sits in the liability section; its scope is LQ-2. ADR-008 already limits subscription mode to operator self-use. |
| Transfers | DPA: EU SCCs (Modules Two and Three), UK Addendum, Swiss addendum. The US is adequate for DPF-certified organisations (EU: [Commission list](https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/adequacy-decisions_en); CH: [Federal Council, 14 Aug 2024](https://www.admin.ch/gov/en/start/documentation/media-releases/media-releases-federal-council.msg-id-102054.html), in force 15 September 2024). Whether Anthropic is DPF-certified: **unknown**, not checked. | Anthropic Ireland, then onward processing by Anthropic in the US under Anthropic's own safeguards (Privacy Policy: standard contractual clauses and adequacy decisions). |
| pigtail controls | Redaction before every call (ADR-006). Closed output schemas. | Same, plus: no tools (`--tools ""`), an empty temporary working directory, `--no-session-persistence` (no local transcripts), API credential variables stripped. Telemetry, error-report and feedback opt-outs are set in the subprocess environment (`PRIVACY_ENV` in `src/pigtail/llm/subscription.py`: `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_FEEDBACK_COMMAND=1`), because Claude Code otherwise sends error reports to a third-party service by default for Pro and Max sign-ins ([data usage](https://code.claude.com/docs/en/data-usage)). CLI output is not echoed in error messages (CB-07, implemented). These opt-outs do not change what Anthropic itself keeps for the model requests (see the rows above). |

---

## 3. Necessity and proportionality
Covered in [lia.md](lia.md) §3. In summary:
- pseudonyms are used wherever identity is only needed for de-duplication or linking;
- raw text is kept only because the citation validator (R7.1) and replay (PRD §5.5) require it;
- the 24-month limit follows from T+365 scoring (R3.1) and the 24-month universe window (R4.1);
- spread graphs are limited to Tier 2 and Tier 3 cases.

**Minimisation gap:** D2. Whole GH Archive hourly dumps, including free text and possibly e-mail addresses, are kept for replay even though the analysis uses six fields. Fix (CB-04), **partly implemented** (ADR-027.4; items marked I are in `src/pigtail/capture/retention.py`, the rest P):
- I: keep the SHA-256 and the source URL;
- I: drop the raw bytes after 30 days (P: immediately after parsing);
- I: replay re-downloads the dump from `data.gharchive.org` and checks its hash;
- P: if the upstream copy disappears, replay falls back to the stored minimal parse (today it raises `NotFound`).

Replay stays verifiable, and the largest pool of raw person-level data goes away.

---

## 4. Rights of data subjects

| Right | GDPR | FADP | How pigtail meets it | Status |
|---|---|---|---|---|
| Information | Art. 14 (one month); exception in Art. 14(5)(b), "disproportionate effort … in particular for … scientific or historical research purposes or statistical purposes", with "appropriate measures … including making the information publicly available" | Art. 19 (one month after receipt); exception in Art. 20(2)(b), "disproportionate effort" | A public notice ([privacy-notice.md](privacy-notice.md)). No individual notification: millions of GH Archive actors, contact data not held (LQ-10). | Notice drafted; publication **P** (CB-12) |
| Access | Art. 15 | Art. 25 | The requester gives a handle; we compute the pseudonym with the key and export the records. | **P** (CB-08) |
| Erasure / objection | Art. 17, Art. 21(1) | Art. 30(2)(b), Art. 32(2) | Suppression list of pseudonyms. Purge from every store. Drop at ingest from then on. | **P** (CB-08) |
| Rectification | Art. 16 | Art. 32(1) | Coded facts can be marked disputed. Raw snapshots are records of what was public and are not "corrected". | **P** (CB-08) |
| Art. 11 | If the controller "is not in a position to identify the data subject" | n/a | Does **not** apply as a blanket excuse: the operator holds the key and can re-compute pseudonyms from a handle. | — |

---

## 5. Risks to data subjects

Likelihood (L) and severity (S) are rated 1–3 (1 = remote or minimal, 2 = possible or significant, 3 = likely or serious). Inherent risk is the rating without the controls listed in §6.

| ID | Risk | Description | L | S | Inherent |
|---|---|---|---|---|---|
| R1 | **Re-identification from pseudonyms** | Pseudonyms are stable. Quoted text (D7), timestamps, repo and reach can single a person out through a web search. The operator holds the key. A 64-bit truncated HMAC is not reversible without the key, but a key leak reverses every pseudonym by brute force over known handles. | 3 | 2 | **High** |
| R2 | **Profiling** | Accounts ranked by reach and timing as "triggers". Maintainers' projects classed as `plateau` or `short_lived`. | 2 | 2 | Medium |
| R3 | **Spread graphs** | A graph of who amplified whom is a social map. If leaked or published it exposes relationships, and it can be used to target influencers. | 2 | 3 | **High** |
| R4 | **Over-retention of raw data** | Raw dumps (D2) and snapshots hold far more than is used. No purge job exists yet. | 3 | 2 | **High** |
| R5 | **Deleted content kept** | A user deletes a post or account upstream and pigtail keeps the raw copy. This breaches the platform terms (TM-06) and the person's expectation. | 3 | 2 | **High** |
| R6 | **LLM provider processing** | Text leaves the controller. Subscription mode means no DPA, possible training, and retention of up to 5 years. Flagged content is kept even in api mode. The redactor misses names, bare handles, profile URLs and DIDs. | 2 | 2 | Medium |
| R7 | **Public repo leakage** | Handles, snapshots or fixtures are committed to the **public** repo, or appear in commit messages or ops logs. | 2 | 3 | **High** |
| R8 | **Security breach of private storage** | Unauthorised access to the host, the bucket or backups. There is no encryption at rest in code, and local dev keys are defaults. | 2 | 3 | **High** |
| R9 | **Incidental special-category data** | Posts reveal views or health. The LLM or the codebook could extract them. | 1 | 3 | Medium |
| R10 | **Rights not exercisable** | No channel or tooling for access, objection or erasure. People are unaware of the processing. | 3 | 2 | **High** |
| R11 | **Cache and log persistence** | `llm_cache` has no TTL and is not linked to evidence, so deletion sync and retention miss it. Error messages may carry text. | 3 | 1 | Medium |
| R12 | **Function creep by self-hosters** | Someone uses pigtail for people-tracking or lead generation. | 2 | 3 | **High** |
| R13 | **Backups outlive deletions** | Restoring a backup re-introduces purged or deleted data. | 2 | 2 | Medium |
| R14 | **Maintainer identity in project-level data** | `owner/repo` is kept without a time limit and names a person, joined to outcome classes. | 3 | 1 | Medium |

---

## 6. Measures, mapped to controls

**I** = implemented and on `main` (evidence cited). **P** = planned (backlog id, §9). **O** = operator action.

| Risk | Measures | Status |
|---|---|---|
| R1 | Keyed HMAC-SHA256 pseudonyms (`src/pigtail/pseudonymize.py`). | I |
| | Pseudonymisation at ingest with a per-platform namespace (`src/pigtail/connectors/base.py` `records()`, `github` namespace in `connectors/gharchive.py`). The LLM-path redactor uses one `generic` namespace for @mentions ([lia.md](lia.md) S9). | I |
| | Key ≥ 16 characters, required, and stored apart from the data (`Pseudonymizer.__init__`; `build_client` refuses to start without it). | I |
| | The key is backed up separately from data backups. | O (H1) |
| | Key rotation and escrow procedure; the key is never placed in DB dumps. | P CB-09 |
| | No handles in public outputs; quoted spans never published. | I (policy, PRD §4) / P (output guard CB-14) |
| R2 | No decisions about individuals. Outcome classes are for projects. Reach is stored in bands. The codebook forbids scoring individuals. | I (design) / P CB-10, CB-11 |
| | Matched losers are not named publicly without consent. | P CB-20 |
| R3 | Graphs only for Tier 2 and Tier 3. Nodes pseudonymised. Private UI only. No public identifying graphs (PRD §4). | P (not built) |
| | Hold A4 until LQ-8 is answered or CB-14 is in place. | P |
| R4 | Retention job: 24 months for `person_level_24m`, then aggregate or delete. | P CB-01 |
| | Minimise GH Archive raw dumps (hash + re-fetch). | I (partly) CB-04: 30-day purge + verified re-fetch; P: drop-after-parse, minimal-parse fallback |
| | Retention fields exist (`evidence.retention_class`, `migrations/0001_capture_v0.sql`, `src/pigtail/capture/models.py`). | I |
| R5 | Deletion sync per source ([retention-policy.md](retention-policy.md) §4); `deletion_state` field (`migrations/0001_capture_v0.sql`). | I (field) / P CB-02 |
| | Bluesky is not enabled before CB-02. | P (gate) |
| R6 | Redaction before every call on both backends (`src/pigtail/llm/client.py`, ADR-006). | I |
| | Closed output schemas (`src/pigtail/llm/types.py`). | I |
| | Subscription mode: no tools, empty working directory, no session persistence, API credential variables stripped (`subscription.py`). | I |
| | Training opt-out on the operator's Claude account. | O (H1, still open) |
| | Subscription mode only for the operator's own non-commercial use (ADR-001, ADR-008). | I (policy) |
| | Telemetry, error-report and feedback opt-outs in the CLI subprocess environment (`PRIVACY_ENV`: `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_FEEDBACK_COMMAND`; `src/pigtail/llm/subscription.py`). | I (CB-07) |
| | Extend the redactor to profile URLs, DIDs, bare handles in author fields, and signature names. | P CB-06 |
| | `api` mode: sign the DPA (automatic under the Commercial Terms); request ZDR where eligible. | O |
| R7 | Public repo rules (CLAUDE.md, WORK_ORDER §6). | I |
| | CI private-data scan (secrets, data paths, personal e-mail addresses, fixture manifest). | I (`scripts/private_data_scan.py`) |
| | Commits gated on the scan (ADR-011). | I |
| | The scan does not detect pseudonyms or bare handles in prose. | Residual |
| R8 | Bind services to localhost. | I (`docker-compose.yml`) |
| | Encryption at rest for the bucket and DB volumes (required by TM-06). | P CB-03 |
| | Strong credentials in production (defaults only for local dev). | O |
| | Access control and an audit log on the private UI. | P CB-19 |
| | Breach runbook: GDPR Art. 33 ("not later than 72 hours" where feasible); FADP Art. 24 ("as quickly as possible"). | P CB-16 |
| R9 | No Art. 9 attributes in the codebook. Closed schemas. Prompts instruct the model to ignore personal characteristics. | I (schemas) / P CB-11 |
| R10 | Public notice with a contact route. | P CB-12 |
| | Access, objection and erasure tooling. | P CB-08 |
| | Honour explicit refusals (FADP Art. 30(2)(b)). | P CB-13 |
| R11 | Give the cache a retention class and evidence links; purge with its source. | P CB-05 |
| | `BackendError` no longer echoes CLI output (tested in `tests/unit/test_subscription_backend.py`). | I (CB-07) |
| | No raw handles or text in other logs or `runs.error`; 12-month log retention. | P CB-18 |
| R12 | Purpose limits in the operator guide and README. No person search in the UI. Controller duties explained to self-hosters. | P CB-21 |
| R13 | Encrypted backups, 35-day rotation, deletions replayed after a restore (tombstone log). | P CB-17 |
| R14 | In public outputs, suppress or aggregate repos owned by personal accounts unless they are public-figure projects or the owner consents. | P CB-20 |

---

## 7. Residual risk after all measures

| Risk | Residual (L × S) with I + P + O in place | Residual today (I only) |
|---|---|---|
| R1 | Low–medium (quoted spans remain linkable internally) | **High** |
| R2 | Low | Low (the relevant features are not built yet) |
| R3 | Medium (see LQ-8) | n/a (not built; on hold) |
| R4 | Low | **High** (raw dumps accumulate wherever capture runs; today that is local development only) |
| R5 | Low | n/a (no deletion-duty source enabled) → becomes **High** if Bluesky is enabled first |
| R6 | Low (api) / Medium (subscription) | Medium |
| R7 | Low | Low–medium |
| R8 | Low | **Medium–high** (no encryption at rest) |
| R9 | Low | Low |
| R10 | Low | **High** (no notice, no tooling) |
| R11 | Low | Medium |
| R12 | Medium (outside the controller's control) | Medium |
| R13 | Low | Medium (no backups defined) |
| R14 | Low | Low (nothing is published) |

**Assessment.** With the planned measures in place, no **high** residual risk remains, so prior consultation (GDPR Art. 36(1) / FADP Art. 23(1)) is not required. The lawyer should confirm this (LQ-11).

**Today**, R1, R4, R8 and R10 are high for the GH Archive capture as built. It runs locally in development only; there is no production capture (ADR-022). This is why the ADR-022 production controls (CB-01, CB-03, CB-04, CB-09, CB-12, CB-16, CB-17, CB-18) are **must-fix before the capture layer runs on the production host** (M1 acceptance on the host), and why person-level sources beyond GH Archive stay off until every ADR-022 pre-condition for them exists: CB-01, CB-02, CB-03, CB-06, CB-08, CB-12 and CB-13. ADR-022 (`ops/DECISIONS.md`) is the single authoritative list.

---

## 8. Decision and sign-off

| Item | Decision |
|---|---|
| DPIA required? | Yes (GDPR, WP248: three or more criteria; FADP treated as required). |
| Proceed? | Yes, under the conditions in [lia.md](lia.md) §6–§7. |
| Prior consultation? | Not needed if the planned measures are implemented. Re-assess at H2. |
| Review triggers | A new source, a new processing purpose, spread graphs going live, public mode (D2), any breach, H2 answers, or 12 months. |
| Sign-off | Controller: "[operator]" — pending. Legal review: pending (H2). |

---

## 9. Backlog: controls required but not implemented

These are for the orchestrator to add to `ops/BACKLOG.md`. "Blocks" says which processing stays off until the item is done.

| ID | Control | Blocks | Suggested owner |
|---|---|---|---|
| CB-01 | Retention job: purge or aggregate `person_level_24m` evidence, snapshots and derived pseudonymous rows at 24 months. Dry-run report. Tested. | Production capture on the host (M1 acceptance); any person-level source beyond GH Archive (ADR-022) | engineer |
| CB-02 | Deletion-sync framework (R1.5): Bluesky Jetstream delete and account events with raw copy dropped in ≤ 48 h; propagation of the HN `deleted` flag; propagation to snapshots, LLM cache, derived rows and the backup tombstone log | Any person-level source beyond GH Archive (ADR-022): Bluesky, HN, V2EX and Discord connectors | engineer |
| CB-03 | Encryption at rest for the snapshot bucket (SSE or an encrypted volume) and the Postgres volume. Documented in the operator guide. | Production host; any person-level source beyond GH Archive (ADR-022; also a TM-06 condition for Bluesky) | engineer |
| CB-04 | Minimise GH Archive raw dumps: keep hash and URL, drop bytes after parse or after ≤ 30 days, replay re-fetches and verifies | Production capture on the host | engineer — **Partly done 2026-09-25** (30-day purge, verified re-fetch); open: drop-after-parse, minimal-parse fallback |
| CB-05 | Give `llm_cache` a retention class, `evidence_id` links and a TTL of ≤ 24 months; purge with its source | Tier 2 LLM extraction at scale (M5) | engineer |
| CB-06 | Extend the redactor: profile URLs (github.com/<login>, bsky.app/profile/<handle>, news.ycombinator.com/user?id=), `did:` identifiers, bare handles from known author fields. Add tests. | Any person-level source beyond GH Archive (ADR-022); mention capture (A2) being sent to the LLM | engineer |
| CB-07 | Subscription subprocess: set `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1` (or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`). Redact or truncate CLI output in `BackendError` messages. **Done 2026-09-25** (`PRIVACY_ENV` sets all four variables; `BackendError` carries no CLI output). | — (was: subscription-mode coding of person-level text) | engineer |
| CB-08 | Data-subject tooling: `pigtail privacy lookup|export|suppress|erase <platform> <handle>`. Suppression list checked at ingest. Request log without handles. | Any person-level source beyond GH Archive; publication of the notice | engineer |
| CB-09 | Pseudonym-key management runbook: generation, separate backup, rotation (re-pseudonymisation job), never in DB dumps | Production host | engineer + operator |
| CB-10 | Reach bands for person accounts instead of exact counts | Spread graphs / R5.5 | engineer |
| CB-11 | Codebook privacy rules: no Art. 9 or FADP Art. 5(c) attributes, no individual scoring, no cross-platform identity resolution, the public-figure rule | Codebook v0 (M4) | analyst |
| CB-12 | Publish the privacy notice (operator site plus a link from the README and the UI), fill in the placeholders, publish the DPIA summary | Any person-level source beyond GH Archive; production host | operator |
| CB-13 | Honour explicit refusals (a Bluesky user-intents opt-out once adopted; objections received) | Any person-level source beyond GH Archive (ADR-022); Bluesky connector (re-check TM-06) | engineer |
| CB-14 | Output guard: no private individuals named in planner, growth-engine or trend outputs; a public-figure allowlist; a minimum cell size (e.g. k ≥ 10) for public aggregates | Spread graphs (A4), D2 public mode, D3/D4 | engineer |
| CB-15 | Record of processing activities (GDPR Art. 30 / FADP Art. 12) template for operators | H2 / release | compliance |
| CB-16 | Breach-response runbook (GDPR Art. 33/34, FADP Art. 24) | Production host | compliance + operator |
| CB-17 | Backup policy: encrypted, 35-day rotation, deletion tombstones re-applied on restore | Production host | engineer |
| CB-18 | Log hygiene: no raw handles or text in logs or `runs.error`; 12-month log retention | Production host | engineer |
| CB-19 | Private UI authentication, role-based access and an audit log of snapshot views | UI (D1) | engineer |
| CB-20 | Public outputs: suppress repos owned by personal accounts and matched losers unless the owner consents or the project is a public-figure project | D2 public mode / M9 | engineer |
| CB-21 | Operator guide section "Your duties as controller": adopt the LIA and DPIA, publish a notice, subscription restrictions, training and telemetry opt-outs, purpose limits | Release (M9); any third-party self-hosting | compliance |

## Changelog
- 2026-09-25: v0.1 created (M3-T2).
- 2026-09-25 — fixes after verifier M3 round 1: uncommitted M1 controls relabelled "I (M1, pending merge)" (§6 R1, R4, R5); CB-07 marked implemented (§2.3 D9, §2.5, §6 R6 and R11, §9); §7 and the §9 "Blocks" column aligned with ADR-022 and with capture running in local development only.
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — fixes after verifier M3 round 2: CB-04 marked partly implemented (30-day purge + verified re-fetch; drop-after-parse and minimal-parse fallback still planned); stale 'pending merge' conditions removed.
