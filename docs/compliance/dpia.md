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
| 3. Systematic monitoring ("including data collected through networks") | Continuous capture of GH Archive and, later, the Bluesky Jetstream (R1.1, R1.2); polling of who stars repos with an open case every 15–60 min (per-repo Events API, ADR-032, D12; built in M1-T24, off by default) | **Yes** |
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
                              └─text──▶ redactor (e-mail, phone, profile URL, DID, @mention) ──▶ LLMClient ──▶ Anthropic (api | subscription)
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
| D7 | **LLM outputs (cache)** | coded fields, quoted spans (R7.1), provenance | Derived | `llm_cache` table (`src/pigtail/llm/store.py`), under `PIGTAIL_DATA_DIR` | Redacted/pseudonymised. **Quoted spans are verbatim text.** | `person_level_24m`: rows expire after `LLM_CACHE_RETENTION_DAYS` (default and maximum 730) and are never served once expired; rows linked to evidence (`llm_cache_evidence`) are purged with it (CB-05, implemented: `src/pigtail/llm/store.py`) | Operator |
| D8 | **Project-level data** | repo id, `owner/repo` name, stars, downloads, releases | All cleared sources | Postgres, snapshot store | Project-level. `owner` may be a person's login. | `project_level` (unlimited) | Operator; aggregates may be public |
| D9 | **Operational logs and ledgers** | run records (`runs.error`, counts), LLM usage ledger, pause log | Internal | Postgres, SQLite | Should contain no personal data. `BackendError` messages no longer echo CLI output: they carry only the exit code, `subtype` and `api_error_status` (`src/pigtail/llm/subscription.py` `parse()`; tested by `test_cb07_error_message_does_not_echo_output` in `tests/unit/test_subscription_backend.py`) (CB-07, implemented). `runs.error` is scrubbed of handles, e-mails, profile URLs and DIDs and truncated (`src/pigtail/capture/runs.py` → `pigtail.logsafe.scrub`; CB-18, partly implemented). | 12 months (policy, [retention-policy.md](retention-policy.md)). `runs.error` text is cleared after `LOG_RETENTION_DAYS` (max 365) and the LLM ledger after 24 months by `pigtail retention purge` (I). Container and system logs: rotation is an operator duty (P, CB-18). | Operator |
| D10 | **Pseudonym key** | `PSEUDONYM_KEY` | Operator | Host environment / secrets manager, backed up separately (H1) | Secret | For the life of the dataset | Operator only |
| D11 | **Manual entries** | press citations (TM-31), careers facts (TM-29) | Operator | Postgres | Organisation-level. Investors are named as organisations only. | `project_level` | Operator |
| D12 | **Stargazer events for tracked repos** (ADR-032.2; **built in M1-T24, connector `github_events` off by default**) | `WatchEvent` and `ForkEvent` actor login, repo, time (fork actors are kept because fork farms are part of the bot features, and the PRD §8.1 attention metrics count forks). The raw per-repo response also holds that repo's other recent public events (pushes, issues, PRs, with actors and payload text). | Per-repo Events API (TM-33 under TM-02; actor use pending LQ-29). Polled only for repos with a live `velocity` case opened in the last 14 days, and, only with `--prethreshold` (not set in `infra/schedule.toml`), watch-list repos that gained ≥ 30 public stars in 24 h (`RepoEventsPoller.targets()`, `src/pigtail/capture/repo_events.py`); every 15 min for cases, 60 min for pre-threshold repos, never faster than `X-Poll-Interval` | Raw response: snapshot store, bytes dropped right after parse (`drop_after_parse` in `RepoEventsPoller._ingest`; hash and URL kept, evidence `raw_dropped`, tombstone in `deletion_log`; I, CB-23). Parsed: only `WatchEvent`s and `ForkEvent`s (`KEPT_EVENT_TYPES`, `src/pigtail/connectors/github.py`), bot logins dropped before hashing (stored as `is_bot = true` with a NULL actor), other actors as pseudonyms in `repo_event_actor` (`migrations/0007_github_detection.sql`: CHECK `^p_[0-9a-f]{16}$` on the actor, CHECK `event_type IN ('WatchEvent', 'ForkEvent')`; registered in `PERSON_TABLES`). Aggregates without pseudonyms: `repo_event_daily_agg` (per repo and UTC day: distinct non-bot stars and forks, bot stars and forks) and the case's `bot_filter` block (`stars_seen`, `stars_bot`, `coverage_ratio`, `confirmed`, `events_from`, `window_overflow`). No lockstep flag: only the login rules run on per-repo events (`layers = ["login_rules"]`, `stars_lockstep = null`; ADR-037.2). | Raw → pseudonymised → aggregate | **`person_level_30d`** (named after its 30-day ceiling; the operative default is 16 days, ADR-038): raw evidence 16 days by default (ceiling 30) from `fetched_at` if a raw copy survives parsing; `repo_event_actor` rows 16 days by default (ceiling 30; ADR-038) from the event's `created_at`; both follow `GITHUB_EVENTS_RETENTION_DAYS` (default 16, maximum 30) and purged by `pigtail retention purge` (I, CB-22). Aggregates: no limit, no pseudonyms (`derived_aggregate` in substance; the tables carry no evidence class). | Operator |
| D13 | **Star counts and the watch list** (M1-T24; ADR-032, ADR-037) | Hourly GraphQL counts per watch-list repo (`stargazerCount`, `forkCount`, `pushedAt`, `createdAt`, `isPrivate`, `owner.__typename`) in `repo_count_snapshot` with the batch cost in `github_graphql_batch`; star-history daily net counts in `repo_star_daily` and `star_history_fetch`; the watch list itself (`watchlist`: repo id, node id, `owner/name`, nominating source, owner **type** only, never an owner login field); `detection_agreement`, `hn_show_screen` (HN item id and repo name), `github_budget_ledger`, `github_http_cache` (URL, ETag, hash) | GitHub GraphQL and star-history endpoint (TM-02, TM-33) | Postgres; raw GraphQL and star-history answers in the snapshot store | Project-level; **no data about stargazers**. As with D8, `owner/name` can contain a personal login (R14). No table has a login, actor or owner-login column (asserted in `test_m1_t24_migration_tables_and_constraints`, `tests/integration/test_github_detection_m1t24.py`). | `project_level` (GraphQL and star-history snapshots: `retention_class="project_level"` in `src/pigtail/connectors/github.py`) | Operator; aggregates may be public |
| D14 | **Watch-list screen inputs** (M1-T24) | (a) GitHub `search/repositories` result pages: each item embeds the repo's `owner` object (login, id, avatar and profile URLs). Only repo id, node id, `owner/name`, counts, dates and the owner **type** are parsed (`parse_search_page`). (b) HN "Show HN" items: the raw item JSON holds the poster's `by` handle; only the GitHub URL is kept. | GitHub Search API (TM-02); HN Firebase via the project-level rank poller (TM-04, ADR-031) | (a) Raw search pages in the snapshot store, kept (not dropped at parse). (b) HN item bytes dropped right after parsing (`drop_after_parse`, `src/pigtail/capture/github_screens.py`) | (a) Raw, person-level (owner objects) / parsed: project-level. (b) Raw dropped at once | (a) **`person_level_24m`** because results embed owner objects (ADR-037.8; `search_repositories()` in `src/pigtail/connectors/github.py`; asserted in `test_m1_t24_search_sweep_splits_ranges_under_1000_cap`), purged at 24 months by CB-01. (b) raw not retained | Operator |

**Detection v1 cases (ADR-037.1).** Detection-v1 cases (project-level, D8) open on the public net star count before any bot filtering, because bot-filter data does not exist for most repos until tracking starts. Each case records `bot_filter.status | basis | confirmed`: `pending` with basis `repo_events` while `github_events` is enabled, else `unavailable` with basis `none` (`src/pigtail/capture/detection_v1.py`). With the connector off by default, cases carry `unavailable`; analyses treat `confirmed = false` or unconfirmed cases as flagged. No personal data is involved in opening a case.

**Minimisation note (D14a).** Raw search pages keep owner objects for up to 24 months although only the owner type is used. Dropping them right after parsing (as for HN items) would remove this pool; it is recorded as a recommendation to the orchestrator, not a control yet.

**Not processed:** the OpenDigger GH-event mirror (TM-32) is a GAP pending LQ-28 and off by default (ADR-032.1); no connector exists. Enabling it would add a D1-like pool of person-level event data at much higher capture and needs a DPIA update first. (The M1-T18 research streamed its files once on 2026-09-25, kept no identities and deleted the files after the analysis; `docs/research/detection-replan.md` §7.) The GitHub stargazers API is no longer used (restricted by GitHub on 2026-06-30; ADR-012 superseded by ADR-032).

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
| Access | Art. 15 | Art. 25 | The requester gives a handle; `pigtail privacy request access` computes the pseudonym with the key and exports the parsed records in retained snapshots, person-level rows and LLM cache rows to a 0600 JSON file (`src/pigtail/privacy/requests.py` `access()`). The request log holds no handle and no pseudonym. Evidence whose raw bytes were already dropped cannot be searched for a person. | **I** (CB-08) |
| Erasure / objection | Art. 17, Art. 21(1) | Art. 30(2)(b), Art. 32(2) | Suppression list of pseudonyms (`privacy_suppression`, pseudonyms only by database CHECK). Purge: whole raw snapshots containing the person, registered person-level rows, and LLM cache rows derived from them or mentioning the pseudonym (`pigtail privacy request erasure`, `pigtail privacy optout add`). Connectors drop the person at ingest from then on (`connectors/base.py`). Tombstones in the append-only `deletion_log`. Project owners can opt a repo out. | **I** (CB-08, CB-13) |
| Rectification | Art. 16 | Art. 32(1) | Coded facts can be marked disputed. Raw snapshots are records of what was public and are not "corrected". **Not built** (ADR-030.3); handled manually by the operator meanwhile. | **P** (CB-08 follow-up) |
| Art. 11 | If the controller "is not in a position to identify the data subject" | n/a | Does **not** apply as a blanket excuse: the operator holds the key and can re-compute pseudonyms from a handle. | — |

---

## 5. Risks to data subjects

Likelihood (L) and severity (S) are rated 1–3 (1 = remote or minimal, 2 = possible or significant, 3 = likely or serious). Inherent risk is the rating without the controls listed in §6.

| ID | Risk | Description | L | S | Inherent |
|---|---|---|---|---|---|
| R1 | **Re-identification from pseudonyms** | Pseudonyms are stable. Quoted text (D7), timestamps, repo and reach can single a person out through a web search. The operator holds the key. A 64-bit truncated HMAC is not reversible without the key, but a key leak reverses every pseudonym by brute force over known handles. | 3 | 2 | **High** |
| R2 | **Profiling** | Accounts ranked by reach and timing as "triggers". Maintainers' projects classed as `plateau` or `short_lived`. | 2 | 2 | Medium |
| R3 | **Spread graphs** | A graph of who amplified whom is a social map. If leaked or published it exposes relationships, and it can be used to target influencers. | 2 | 3 | **High** |
| R4 | **Over-retention of raw data** | Raw dumps (D2) and snapshots hold far more than is used. Without a purge job they would accumulate (the purge now exists: CB-01, CB-04). | 3 | 2 | **High** |
| R5 | **Deleted content kept** | A user deletes a post or account upstream and pigtail keeps the raw copy. This breaches the platform terms (TM-06) and the person's expectation. | 3 | 2 | **High** |
| R6 | **LLM provider processing** | Text leaves the controller. Subscription mode means no DPA, possible training, and retention of up to 5 years. Flagged content is kept even in api mode. The redactor misses names, bare handles in author fields, gist and avatar URLs (profile URLs and DIDs are now redacted, CB-06 partly implemented). | 2 | 2 | Medium |
| R7 | **Public repo leakage** | Handles, snapshots or fixtures are committed to the **public** repo, or appear in commit messages or ops logs. | 2 | 3 | **High** |
| R8 | **Security breach of private storage** | Unauthorised access to the host, the bucket or backups. Encryption at rest depends on the operator enabling it (SSE for the bundled SeaweedFS via `S3_SSE_KEK`; an encrypted Postgres volume), and local dev keys are defaults. | 2 | 3 | **High** |
| R9 | **Incidental special-category data** | Posts reveal views or health. The LLM or the codebook could extract them. | 1 | 3 | Medium |
| R10 | **Rights not exercisable** | Without a channel and tooling, access, objection and erasure cannot be exercised (tooling now exists: CB-08, CB-13; no published notice yet: CB-12). People are unaware of the processing. | 3 | 2 | **High** |
| R11 | **Cache and log persistence** | Without a TTL and evidence links, deletion sync and retention would miss `llm_cache` (both now exist: CB-05). Error messages may carry text (`runs.error` is now scrubbed: CB-18, partly). | 3 | 1 | Medium |
| R12 | **Function creep by self-hosters** | Someone uses pigtail for people-tracking or lead generation. | 2 | 3 | **High** |
| R13 | **Backups outlive deletions** | Restoring a backup re-introduces purged or deleted data. | 2 | 2 | Medium |
| R14 | **Maintainer identity in project-level data** | `owner/repo` is kept without a time limit and names a person, joined to outcome classes. | 3 | 1 | Medium |
| R15 | **Rebuilding stargazer lists against GitHub's restriction (D12)** | GitHub limited stargazer lists to admins and collaborators on 2026-06-30 because they were "misused to collect user data for spam activities" (TM-33; LQ-29). Per-repo `WatchEvent`s still show who starred. Without controls, polling them would rebuild those lists (in plain logins, kept indefinitely, across many repos), expose people to the spam and outreach harm GitHub named, go against users' post-restriction expectations, and could be read as circumventing the restriction (ToS §H, AUP). Even with controls, 16 days (ceiling 30; ADR-038) of pseudonymised star rows for a repo are a partial pseudonymised stargazer list. | 3 | 2 | **High** |

---

## 6. Measures, mapped to controls

**I** = implemented and on `main` (evidence cited). **P** = planned (backlog id, §9). **O** = operator action.

| Risk | Measures | Status |
|---|---|---|
| R1 | Keyed HMAC-SHA256 pseudonyms (`src/pigtail/pseudonymize.py`). | I |
| | Pseudonymisation at ingest with a per-platform namespace (`src/pigtail/connectors/base.py` `records()`, `github` namespace in `connectors/gharchive.py`). The LLM-path redactor takes the caller's per-source namespace for @mentions (`LLMClient.complete(namespace=…)`, default `generic`) and always uses the platform's namespace for profile URLs and DIDs ([lia.md](lia.md) S9). | I |
| | Key ≥ 16 characters, required, and stored apart from the data (`Pseudonymizer.__init__`; `build_client` refuses to start without it). | I |
| | The key is backed up separately from data backups. | O (H1) |
| | Key rotation and escrow procedure; the key is never placed in DB dumps. | P CB-09 |
| | No handles in public outputs; quoted spans never published. | I (policy, PRD §4) / P (output guard CB-14) |
| R2 | No decisions about individuals. Outcome classes are for projects. Reach is stored in bands. The codebook forbids scoring individuals. | I (design); I CB-11 (codebook v0.1.0 §12, `schemas/codebook/v0.1.0.json` `privacy_rules_cb_11`); P CB-10 |
| | Matched losers are not named publicly without consent. | P CB-20 |
| R3 | Graphs only for Tier 2 and Tier 3. Nodes pseudonymised. Private UI only. No public identifying graphs (PRD §4). | P (not built) |
| | Hold A4 until LQ-8 is answered or CB-14 is in place. | P |
| R4 | Retention job: 24 months for `person_level_24m`, then aggregate or delete. `pigtail retention purge` drops the raw bytes of `person_level_24m` evidence older than `PERSON_LEVEL_RETENTION_DAYS` (default and maximum 730; longer values rejected at startup), keeps hash, URL and fetch time (`raw_dropped`), deletes registered person-level rows, writes tombstones to the append-only `deletion_log`, and has a dry run (`src/pigtail/privacy/retention.py`, `migrations/0003_privacy_operations.sql`; `tests/integration/test_privacy_ops.py`). The operator schedules it daily. | I CB-01 / O (schedule) |
| | Minimise GH Archive raw dumps (hash + re-fetch). | I (partly) CB-04: 30-day purge + verified re-fetch; P: drop-after-parse, minimal-parse fallback |
| | Retention fields exist (`evidence.retention_class`, `migrations/0001_capture_v0.sql`, `src/pigtail/capture/models.py`). | I |
| R5 | Deletion sync per source ([retention-policy.md](retention-policy.md) §4); `deletion_state` field (`migrations/0001_capture_v0.sql`). | I (field). CB-02 per source: HN I (`pigtail privacy deletion-sync --source hn`); GitHub per-repo events met by short retention plus raw drop at parse, no sync source (ADR-038); GH Archive raw purge ≤ 30 days (CB-04) and replay drops opted-out persons (CB-13); Bluesky P (no connector yet) |
| | Bluesky is not enabled before CB-02. | P (gate) |
| R6 | Redaction before every call on both backends (`src/pigtail/llm/client.py`, ADR-006). | I |
| | Closed output schemas (`src/pigtail/llm/types.py`). | I |
| | Subscription mode: no tools, empty working directory, no session persistence, API credential variables stripped (`subscription.py`). | I |
| | Training opt-out on the operator's Claude account. | O (H1, still open) |
| | Subscription mode only for the operator's own non-commercial use (ADR-001, ADR-008). | I (policy) |
| | Telemetry, error-report and feedback opt-outs in the CLI subprocess environment (`PRIVACY_ENV`: `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_FEEDBACK_COMMAND`; `src/pigtail/llm/subscription.py`). | I (CB-07) |
| | Redactor extensions: GitHub, Bluesky and HN profile URLs → `[profile:<platform>:<pseudonym>]`, `did:plc`/`did:web` → `[did:<pseudonym>]`, per-source @mention namespaces (`src/pigtail/pseudonymize.py`; `tests/unit/test_redaction_cb06.py`). | I CB-06 (partly) |
| | Still open: gist URLs, avatar URLs, bare handles in author fields, and signature names. | P CB-06 |
| | `api` mode: sign the DPA (automatic under the Commercial Terms); request ZDR where eligible. | O |
| R7 | Public repo rules (CLAUDE.md, WORK_ORDER §6). | I |
| | CI private-data scan (secrets, data paths, personal e-mail addresses, fixture manifest). | I (`scripts/private_data_scan.py`) |
| | Commits gated on the scan (ADR-011). | I |
| | The scan does not detect pseudonyms or bare handles in prose. | Residual |
| R8 | Bind services to localhost. | I (`docker-compose.yml`) |
| | Encryption at rest for the bucket and DB volumes (required by TM-06). Code side: SeaweedFS SSE-S3 when `S3_SSE_KEK` is set (the init job sets bucket default encryption, `docker-compose.yml`); `pigtail doctor` reports bucket encryption and lists Postgres volume encryption as MANUAL (`src/pigtail/privacy/doctor.py`; `tests/unit/test_doctor_cb03.py`). Host side: set the KEK and encrypt the Postgres volume. | I CB-03 (partly) / O (enable on host, Postgres volume) |
| | Strong credentials in production (defaults only for local dev). | O |
| | Access control and an audit log on the private UI. | P CB-19 |
| | Breach runbook: GDPR Art. 33 ("not later than 72 hours" where feasible); FADP Art. 24 ("as quickly as possible"). | P CB-16 |
| R9 | No Art. 9 attributes in the codebook. Closed schemas. Prompts instruct the model to ignore personal characteristics. | I (schemas); I CB-11 (codebook v0.1.0 §12) |
| R10 | Public notice with a contact route. | P CB-12 |
| | Access, objection and erasure tooling (`pigtail privacy request access` or `erasure`, `pigtail privacy requests`; `src/pigtail/privacy/requests.py`). Rectification and a separate lookup command are not built. | I CB-08 / P (rectification) |
| | Honour explicit refusals (FADP Art. 30(2)(b)): opt-out list of pseudonyms and repo ids, checked at ingest, with purge of existing data (`pigtail privacy optout`; `src/pigtail/privacy/suppression.py`, `connectors/base.py`; `tests/unit/test_connector_suppression_cb13.py`). A Bluesky user-intents signal is not consumed yet (no Bluesky connector). | I CB-13 |
| R11 | Give the cache a retention class and evidence links; purge with its source. Rows expire after 24 months and are not served once expired; `purge_for_evidence()` runs on retention and erasure (`src/pigtail/llm/store.py`; `tests/unit/test_llm_cache_cb05.py`). | I CB-05 |
| | `BackendError` no longer echoes CLI output (tested in `tests/unit/test_subscription_backend.py`). | I (CB-07) |
| | No raw handles or text in other logs or `runs.error`; 12-month log retention. `RedactingFilter` and `runs.error` scrubbing exist (`src/pigtail/logsafe.py`, `src/pigtail/capture/runs.py`; `tests/unit/test_logsafe_cb18.py`); `runs.error` is cleared after 12 months by the retention purge. The filter is installed only by `capture scan` today. | I CB-18 (partly) |
| | Install the filter in every service and CLI command; container and system log rotation (12 months). | P CB-18 / O (rotation) |
| R12 | Purpose limits in the operator guide and README. No person search in the UI. Controller duties explained to self-hosters. | P CB-21 |
| R13 | Encrypted backups, 35-day rotation, deletions replayed after a restore (tombstone log). | P CB-17 |
| R14 | In public outputs, suppress or aggregate repos owned by personal accounts unless they are public-figure projects or the owner consents. | P CB-20 |
| R15 | Scope: per-repo events only for repos with a live `velocity` case opened in the last 14 days, plus (only with `--prethreshold`, not scheduled) watch-list repos above the 30-stars-in-24-h pre-threshold; never a general sweep (`RepoEventsPoller.targets()`, `src/pigtail/capture/repo_events.py`). "Tracked repos" (R17.4) are not a target class in the code. | I (M1-T24) |
| | Cadence: every 15 min for cases and 60 min for pre-threshold repos, never faster than `X-Poll-Interval`, intervals below 15 min rejected (`EventsConfig`); ETag, a `304` ends the poll; at most 3 pages (300 events); one operator token, per-resource budgets and hard stops (ADR-032.4, ADR-037.5; `src/pigtail/connectors/github.py`). Tested: `test_m1_t24_repo_events_etag_poll_interval_and_overflow`. | I (M1-T24) |
| | Pseudonymise `actor` at ingest with the keyed HMAC (`github` namespace); bot logins dropped before hashing (`GitHubRepoEventsConnector._pre_pseudonymize`). The table rejects anything but a pseudonym (CHECK in `migrations/0007_github_detection.sql`). Tested: `test_m1_t24_repo_events_pseudonymized_minimised_and_bot_filter_applied` (no raw handle in any table) and `test_m1_t24_migration_tables_and_constraints`. | I |
| | Minimise at parse: only `WatchEvent` and `ForkEvent` survive (`KEPT_EVENT_TYPES` and `_parse`, `src/pigtail/connectors/github.py`; CHECK on `repo_event_actor.event_type`); payloads are never kept; each events page's raw bytes are dropped right after parsing (`drop_after_parse` in `RepoEventsPoller._ingest`, run in a `finally`), keeping hash and URL (`raw_dropped`) with a tombstone in `deletion_log`. A page whose parsing never starts (e.g. malformed JSON) keeps its bytes until the retention purge (16 days by default, ceiling 30; ADR-038). Tested: the same test asserts the Push/Issues/PR events are dropped, both evidence rows are `raw_dropped` with class `person_level_30d`, the blobs are gone and two `raw_dropped` tombstones exist. | I CB-23 |
| | Retention: `person_level_30d` (`migrations/0007_github_detection.sql` widens the `evidence.retention_class` CHECK). `pigtail retention purge` drops the raw bytes of `person_level_30d` evidence older than `GITHUB_EVENTS_RETENTION_DAYS` and deletes `repo_event_actor` rows older than the same cap by event time (`RetentionConfig.github_events_days`, `src/pigtail/privacy/retention.py`; `PersonTable(..., retention_days=30, retention_class="person_level_30d")`, `src/pigtail/privacy/deletion.py`). The setting defaults to 16 (`GITHUB_EVENTS_DEFAULT_DAYS`, ADR-038) and values above 30 are rejected at startup (`_days()` in `src/pigtail/config.py`); the `PersonTable` ceiling of 30 and the setting are combined as the shorter of the two. The purge is scheduled daily (`[jobs.retention_purge]`, `infra/schedule.toml`). Erasure by pseudonym reaches the table through `PERSON_TABLES`. After the cap only `repo_event_daily_agg` and the case `bot_filter` counts remain. Tested: `test_m1_t24_cb22_repo_event_rows_and_raw_purged_after_30_days` (40-day row and raw purged, 5-day row kept, 24-month class untouched, erasure, idempotent second run) and `test_m1_t24_cb22_retention_setting_capped_at_30_days` (also asserts the default of 16). | I CB-22 |
| | No-list guard: `repo_event_actor` is referenced only by the poller and the person-table registry; every `SELECT` on it returns counts or `min()` only, never pseudonym rows; no CLI command names stargazers or actors, and the API code does not reference the table or the word `stargazers` (`tests/unit/test_github_privacy_m1t24.py`: `test_m1_t24_cb23_no_cli_command_lists_stargazers`, `test_m1_t24_cb23_repo_event_actor_only_in_poller_and_registry`, `test_m1_t24_cb23_repo_event_actor_is_read_only_in_aggregate`). No cross-repo stargazer graph exists (no code joins the table across repos). The only per-person read is the data subject's own access export (`pigtail privacy request access`, which reads `PERSON_TABLES` for the requester's pseudonym, CB-08). The stargazers API is not called. | I CB-23 / P CB-14 (outputs) |
| | Identities used only for the login-based bot flag and for de-duplicating stars and forks within a case window (distinct counts); this purpose is what sets the 16-day default (ADR-038). The lockstep rule is **not** applied to per-repo events (ADR-037.2), so no per-actor feature is computed from D12. | I (ADR-037.2) |
| | Record per case: `bot_filter.status`, `basis`, `confirmed`, `coverage_ratio`, `window_overflow` (`RepoEventsPoller.apply_bot_filter`; schema `schemas/v0/case.schema.json`, tested in `test_m1_t24_detection_v1_schema_matches_model`). | I (M1-T24) |
| | Gate: the connector is off unless `PIGTAIL_ENABLE_GITHUB_EVENTS=1` **and** `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` (`enabled_by_default = False`, `person_level_hold = True`), and the scheduler job `gh_repo_events` is `enabled = false` in `infra/schedule.toml`. Tested: `test_m1_t24_schedule_jobs_skip_without_token` (skip reasons `connector_disabled`, then `person_source_hold`) and `test_m1_t24_cli_gates_token_and_events`. ADR-036 requires the ADR-022 person-level pre-conditions before the flag is set; CB-12 is still open and CB-03 and CB-06 are partly done; CB-02 is met for this source by ADR-038 (see §7). | I (gate) / hold in force (ADR-022, ADR-036) |
| | Deletion sync (CB-02) for this source: no GitHub `DeletionSource` exists (`pigtail privacy deletion-sync` only knows `hn`, `src/pigtail/cli.py` `cmd_deletion_sync`). GitHub states no deletion duty (TM-02). **CB-02 is met for this source by short retention (ADR-038):** raw pages are dropped at parse and actor rows expire after 16 days by default (ceiling 30), so an un-star or account deletion upstream is reflected at the latest when the row expires. A GitHub sync source is added only if GitHub or H2 requires one (LQ-29 item 4 stays open for the lawyer). | I (met via ADR-038; LQ-29 item 4 open) |
| | Legal: LQ-29 (proportionality after GitHub's restriction; circumvention reading). If answered against, stop D12 and keep the filtered series `unknown`. | Open (H2) |

---

## 7. Residual risk after all measures

| Risk | Residual (L × S) with I + P + O in place | Residual today (I only) |
|---|---|---|
| R1 | Low–medium (quoted spans remain linkable internally) | **High** |
| R2 | Low | Low (the relevant features are not built yet) |
| R3 | Medium (see LQ-8) | n/a (not built; on hold) |
| R4 | Low | Medium (30-day raw-dump purge and 24-month purge exist; drop-after-parse still open; the purge must be scheduled) |
| R5 | Low | n/a (no deletion-duty source enabled) → becomes **High** if Bluesky is enabled first |
| R6 | Low (api) / Medium (subscription) | Medium |
| R7 | Low | Low–medium |
| R8 | Low | **Medium–high** (encryption at rest available for the bucket but not yet enabled on a host; Postgres volume encryption is an operator duty) |
| R9 | Low | Low |
| R10 | Low | **Medium–high** (tooling exists; no published notice, CB-12) |
| R11 | Low | Low–medium (cache expiry and evidence links done; log filter not in every service) |
| R12 | Medium (outside the controller's control) | Medium |
| R13 | Low | Medium (no backups defined) |
| R14 | Low | Low (nothing is published) |
| R15 | Low–medium (a pseudonymised partial list of recent stars, 16 days by default (ceiling 30; ADR-038), remains internally for repos with open cases; LQ-29 open) | n/a while off: the connector exists with CB-22 and CB-23 implemented and tested, but is off by default (`PIGTAIL_ENABLE_GITHUB_EVENTS` plus the ADR-022 flag). If enabled today: low–medium for R15 itself; the open ADR-022 pre-condition CB-12 (notice) leaves R10 exposure; R5 for this source is covered by the short retention (CB-02 met via ADR-038) |

**Assessment.** With the planned measures in place, no **high** residual risk remains, so prior consultation (GDPR Art. 36(1) / FADP Art. 23(1)) is not required. The lawyer should confirm this (LQ-11).

**Today**, R1 and R8 are high or medium–high and R10 medium–high for the GH Archive capture as built (R4 dropped to medium after CB-01). It runs locally in development only; there is no production capture (ADR-022). This is why the ADR-022 production controls (CB-01, CB-03, CB-04, CB-09, CB-12, CB-16, CB-17, CB-18) are **must-fix before the capture layer runs on the production host** (M1 acceptance on the host), and why person-level sources beyond GH Archive stay off until every ADR-022 pre-condition for them exists: CB-01, CB-02, CB-03, CB-06, CB-08, CB-12 and CB-13. ADR-022 (`ops/DECISIONS.md`) is the single authoritative list. The per-repo GitHub events connector (D12) is held by the same list plus CB-22 and CB-23 (ADR-036); CB-22 and CB-23 are done (M1-T24, ADR-037), so for it the open item is CB-12 (and the rest of CB-03 and CB-06); CB-02 is met for it by ADR-038. Status (ADR-030, ADR-038): CB-01, CB-08 and CB-13 done; CB-03 and CB-06 partly done; CB-02: HN done / GitHub events met via ADR-038 / Bluesky pending; CB-12 open. Of the production controls, CB-01 is done; CB-03, CB-04 and CB-18 are partly done; CB-09, CB-12, CB-16 and CB-17 are open.

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

## 9. Control backlog and status

These are for the orchestrator to add to `ops/BACKLOG.md`. "Blocks" says which processing stays off until the item is done. Done and partly done items say so in the "Suggested owner" column.

| ID | Control | Blocks | Suggested owner |
|---|---|---|---|
| CB-01 | Retention job: purge or aggregate `person_level_24m` evidence, snapshots and derived pseudonymous rows at 24 months. Dry-run report. Tested. | Production capture on the host (M1 acceptance); any person-level source beyond GH Archive (ADR-022) | engineer — **Done 2026-09-25** (`pigtail retention purge`, ADR-030; aggregation is not done: expired rows are deleted) |
| CB-02 | Deletion-sync framework (R1.5): Bluesky Jetstream delete and account events with raw copy dropped in ≤ 48 h; propagation of the HN `deleted` flag; propagation to snapshots, LLM cache, derived rows and the backup tombstone log | Any person-level source beyond GH Archive (ADR-022): Bluesky, HN, V2EX and Discord connectors | engineer — **Partly done:** HN implemented (`pigtail privacy deletion-sync --source hn`, `src/pigtail/privacy/deletion_sync.py`, daily job in `infra/schedule.toml`); GitHub per-repo events met by short retention plus raw drop at parse, no sync source (ADR-038); GH Archive: raw purge ≤ 30 days (CB-04) and replay drops opted-out persons (CB-13); Bluesky pending (no connector yet) |
| CB-03 | Encryption at rest for the snapshot bucket (SSE or an encrypted volume) and the Postgres volume. Documented in the operator guide. | Production host; any person-level source beyond GH Archive (ADR-022; also a TM-06 condition for Bluesky) | engineer — **Partly done 2026-09-25** (SeaweedFS SSE via `S3_SSE_KEK`, `pigtail doctor` check, operator-guide section); open: enabling it on the host and Postgres volume encryption (operator) |
| CB-04 | Minimise GH Archive raw dumps: keep hash and URL, drop bytes after parse or after ≤ 30 days, replay re-fetches and verifies | Production capture on the host | engineer — **Partly done 2026-09-25** (30-day purge, verified re-fetch); open: drop-after-parse, minimal-parse fallback |
| CB-05 | Give `llm_cache` a retention class, `evidence_id` links and a TTL of ≤ 24 months; purge with its source | Tier 2 LLM extraction at scale (M5) | engineer — **Done 2026-09-25** (24-month expiry, `llm_cache_evidence` links, purge with source; ADR-030) |
| CB-06 | Extend the redactor: profile URLs (github.com/<login>, bsky.app/profile/<handle>, news.ycombinator.com/user?id=), `did:` identifiers, bare handles from known author fields. Add tests. | Any person-level source beyond GH Archive (ADR-022); mention capture (A2) being sent to the LLM | engineer — **Partly done 2026-09-25** (profile URLs, DIDs, per-source namespaces); open: gists, avatar URLs, bare handles in author fields |
| CB-07 | Subscription subprocess: set `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1` (or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`). Redact or truncate CLI output in `BackendError` messages. **Done 2026-09-25** (`PRIVACY_ENV` sets all four variables; `BackendError` carries no CLI output). | — (was: subscription-mode coding of person-level text) | engineer |
| CB-08 | Data-subject tooling: `pigtail privacy lookup`, `export`, `suppress`, `erase <platform> <handle>` (as planned; see ADR-030.3 for the built names). Suppression list checked at ingest. Request log without handles. | Any person-level source beyond GH Archive; publication of the notice | engineer — **Done 2026-09-25** as `pigtail privacy request access` or `erasure`, `pigtail privacy requests` and `pigtail privacy optout` (ADR-030.3 renamed the commands); rectification and a separate lookup command are not built (follow-up) |
| CB-09 | Pseudonym-key management runbook: generation, separate backup, rotation (re-pseudonymisation job), never in DB dumps | Production host | engineer + operator |
| CB-10 | Reach bands for person accounts instead of exact counts | Spread graphs / R5.5 | engineer |
| CB-11 | Codebook privacy rules: no Art. 9 or FADP Art. 5(c) attributes, no individual scoring, no cross-platform identity resolution, the public-figure rule | Codebook v0 (M4) | analyst — **Done 2026-09-25** (codebook v0.1.0 §12) |
| CB-12 | Publish the privacy notice (operator site plus a link from the README and the UI), fill in the placeholders, publish the DPIA summary | Any person-level source beyond GH Archive; production host | operator |
| CB-13 | Honour explicit refusals (a Bluesky user-intents opt-out once adopted; objections received) | Any person-level source beyond GH Archive (ADR-022); Bluesky connector (re-check TM-06) | engineer — **Done 2026-09-25** (`pigtail privacy optout`, checked at ingest; ADR-030). Re-check TM-06 when the Bluesky connector is built |
| CB-14 | Output guard: no private individuals named in planner, growth-engine or trend outputs; a public-figure allowlist; a minimum cell size (e.g. k ≥ 10) for public aggregates | Spread graphs (A4), D2 public mode, D3/D4 | engineer |
| CB-15 | Record of processing activities (GDPR Art. 30 / FADP Art. 12) template for operators | H2 / release | compliance |
| CB-16 | Breach-response runbook (GDPR Art. 33/34, FADP Art. 24) | Production host | compliance + operator |
| CB-17 | Backup policy: encrypted, 35-day rotation, deletion tombstones re-applied on restore | Production host | engineer |
| CB-18 | Log hygiene: no raw handles or text in logs or `runs.error`; 12-month log retention | Production host | engineer — **Partly done 2026-09-25** (`RedactingFilter`, `runs.error` scrubbing, 12-month clearing of `runs.error`); open: the filter in all services, log rotation |
| CB-19 | Private UI authentication, role-based access and an audit log of snapshot views | UI (D1) | engineer |
| CB-20 | Public outputs: suppress repos owned by personal accounts and matched losers unless the owner consents or the project is a public-figure project | D2 public mode / M9 | engineer |
| CB-21 | Operator guide section "Your duties as controller": adopt the LIA and DPIA, publish a notice, subscription restrictions, training and telemetry opt-outs, purpose limits | Release (M9); any third-party self-hosting | compliance |
| CB-22 | **Required by ADR-036 (ADR-032, TM-33).** The retention class `person_level_30d` (ceiling 30 days; default 16, ADR-038) for per-repo star and fork events (raw bytes and pseudonymised star and fork rows), purged daily by `pigtail retention purge`, longer values rejected at startup, table registered in `PERSON_TABLES`; after expiry only aggregates remain. Tested. | Per-repo event polling (D12, A1b) | engineer — **Done 2026-09-25** (M1-T24, ADR-037): class in `migrations/0007_github_detection.sql` and `src/pigtail/capture/models.py`; purge in `src/pigtail/privacy/retention.py` (`github_events_days`) and `PERSON_TABLES` (`src/pigtail/privacy/deletion.py`, `repo_event_actor`, ceiling 30 days by event time); `GITHUB_EVENTS_RETENTION_DAYS` default 16, maximum 30 (`src/pigtail/config.py`, ADR-038); daily job in `infra/schedule.toml`. Tests: `test_m1_t24_cb22_repo_event_rows_and_raw_purged_after_30_days`, `test_m1_t24_cb22_retention_setting_capped_at_30_days` (`tests/integration/test_github_detection_m1t24.py`). Aggregation of expired rows is done beforehand (`repo_event_daily_agg`), not by the purge |
| CB-23 | **Required by ADR-036 (ADR-032, TM-33).** Parse minimisation and no-list guard for per-repo events: parse only `WatchEvent` and `ForkEvent` actor pseudonym, repo and time (fork actors are needed for the fork-farm bot features and the PRD §8.1 attention metrics); drop raw bytes after parse; no API, UI view, export or report that lists a repo's stargazers; no cross-repo stargazer graph beyond the tracked set. Tested. | Per-repo event polling (D12, A1b) | engineer — **Done 2026-09-25** (M1-T24, ADR-037): `KEPT_EVENT_TYPES` and `GitHubRepoEventsConnector._parse` (`src/pigtail/connectors/github.py`), `event_type` CHECK (`migrations/0007_github_detection.sql`), `drop_after_parse` in `RepoEventsPoller._ingest` (`src/pigtail/capture/repo_events.py`; hash kept, `deletion_log` tombstone), aggregate-only reads. Tests: `test_m1_t24_repo_events_pseudonymized_minimised_and_bot_filter_applied`, `test_m1_t24_migration_tables_and_constraints` (integration) and the three `test_m1_t24_cb23_*` tests in `tests/unit/test_github_privacy_m1t24.py`. No UI exists yet; the UI and public outputs stay under CB-14/CB-19 |

## Changelog
- 2026-09-25: v0.1 created (M3-T2).
- 2026-09-25 — fixes after verifier M3 round 1: uncommitted M1 controls relabelled "I (M1, pending merge)" (§6 R1, R4, R5); CB-07 marked implemented (§2.3 D9, §2.5, §6 R6 and R11, §9); §7 and the §9 "Blocks" column aligned with ADR-022 and with capture running in local development only.
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — fixes after verifier M3 round 2: CB-04 marked partly implemented (30-day purge + verified re-fetch; drop-after-parse and minimal-parse fallback still planned); stale 'pending merge' conditions removed.
- 2026-09-25 — fixes after verifier M3 round 3: CB-11 marked implemented (codebook v0.1.0 §12); LQ-25 updated for CB-04 partly implemented.
- 2026-09-25 — CB statuses updated after privacy-controls merge (ADR-030): CB-01, CB-05, CB-08, CB-13 marked implemented and CB-03, CB-06, CB-18 partly implemented (§2.2, §2.3 D7 and D9, §4, §5, §6, §7, §9); rectification recorded as not built; §9 renamed "Control backlog and status".
- 2026-09-25 — M3-T7, ADR-032: WP248 criterion 3 mentions per-repo stargazer polling; §2.3 adds D12 (stargazer events for tracked repos, planned, ≤ 30 days) and D13 (star counts, no personal data), and records OpenDigger (TM-32) as not processed and the stargazers API as no longer used; §5 adds R15 (rebuilding stargazer lists against GitHub's 2026-06-30 restriction, LQ-29); §6 and §7 add its measures (all planned: M1-T24, CB-22, CB-23) and residual risk; §9 adds proposed controls CB-22 and CB-23. ADR-022 lists unchanged; an amendment is proposed to the orchestrator.
- 2026-09-25 — fixes after verifier (ADR-036 alignment): D12 lists `ForkEvent` actors alongside `WatchEvent` actors (reason: fork-farm bot/lockstep features and the PRD §8.1 attention metrics) and names the retention class `person_level_30d`; R15 measures and CB-22/CB-23 updated to match; the R15 gate cites ADR-036 (adopted 2026-09-25) instead of a proposed ADR-022 amendment and names `PIGTAIL_ENABLE_GITHUB_EVENTS` (default off); `bot_filter_basis` renamed `bot_filter.basis` (case schema).
- 2026-09-25 — CB-22/23 implemented (M1-T24, ADR-037): §2.3 D12 marked built (off by default) with code and test citations and corrected (no lockstep flag on per-repo events, ADR-037.2; actor rows expire by event time; bot actors stored as NULL); D13 extended to the watch list, GraphQL counts and star-history tables (project-level); new D14 for search result pages (`person_level_24m`, ADR-037.8) and Show HN items (raw dropped at parse), with a minimisation note; detection-v1 cases opening with `bot_filter` pending or unavailable recorded (ADR-037.1); §6 R15 measures marked I with citations, CB-02 for this source recorded as open (no GitHub deletion-sync source; ≤ 30-day retention as mitigation); §7 R15 residual and the ADR-022 status for the connector updated; §9 CB-22 and CB-23 done.
- 2026-09-25 — ADR-038 wording (16-day default; CB-02 per source)
