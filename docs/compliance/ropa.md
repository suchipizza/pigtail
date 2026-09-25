# Record of processing activities (RoPA)

**Status:** draft template for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Control **CB-15** ([dpia.md](dpia.md) §9)
**Access date:** every URL was accessed on **2026-09-25**.
**Related:** [lia.md](lia.md) · [dpia.md](dpia.md) · [retention-policy.md](retention-policy.md) · [privacy-notice.md](privacy-notice.md) · [controller-duties.md](controller-duties.md) · [runbooks/breach.md](runbooks/breach.md) · [runbooks/key-rotation.md](runbooks/key-rotation.md)

**How to use it.** This is the record for **one deployment**. The operator of that deployment is the controller (LQ-14). Copy this file to your private deployment notes (not to a public fork if you add real names of staff or providers you want to keep private), fill in every `[bracketed]` field, delete activities you have switched off, and review it at least every 12 months and whenever a source is enabled. The defaults below describe pigtail as shipped on `main` on 2026-09-25; each statement about pigtail's behaviour was checked against the code (file cited). **I** = implemented, **P** = planned (backlog id), **O** = operator action, **Off** = built but off by default.

## Legal basis for keeping this record
- **GDPR Art. 30(1):** each controller "shall maintain a record of processing activities under its responsibility" with (a) controller identity and contacts, (b) purposes, (c) categories of data subjects and personal data, (d) categories of recipients "including recipients in third countries", (e) transfers to third countries and safeguards, (f) "where possible, the envisaged time limits for erasure", (g) "where possible, a general description of the technical and organisational security measures referred to in Article 32(1)". It must be in writing, including electronic form (Art. 30(3)), and made available to the supervisory authority on request (Art. 30(4)). Text: [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj), read via the Publications Office copy (http://publications.europa.eu/resource/celex/32016R0679).
- **GDPR Art. 30(5) exemption** (fewer than 250 employees) does not apply where the processing "is not occasional". pigtail's capture runs continuously on a schedule (`infra/schedule.toml`), so **the exemption should be assumed not to apply** (to confirm at H2).
- **FADP Art. 12(1)–(2):** the controller keeps a record with at least (a) the identity of the controller, (b) the purpose, (c) categories of data subjects and of personal data, (d) categories of recipients, (e) "if possible, the retention period … or the criteria for determining this period", (f) "if possible, a general description of the measures taken to guarantee data security under Article 8", (g) for disclosure abroad, "details of the State concerned and the guarantees under Article 16 paragraph 2". Text: [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en), English translation, consolidated version of 1 September 2023.
- **FADP exemption:** Data Protection Ordinance Art. 24 exempts undertakings with fewer than 250 employees "and natural persons" unless "a large volume of sensitive personal data is being processed" or "high-risk profiling is being carried out" ([fedlex, DPO](https://www.fedlex.admin.ch/eli/cc/2022/568/en), consolidated version of 1 September 2023). A small Swiss operator may therefore be exempt under the FADP but still bound by the GDPR if it applies (LQ-13). **Default: keep the record anyway.**

---

## Part 1 · Controller

| Field | Value |
|---|---|
| Controller (GDPR Art. 30(1)(a); FADP Art. 12(2)(a)) | **[operator legal name]**, [address], [country] |
| Contact for data protection | **[privacy contact e-mail or form]** (same as the published privacy notice) |
| Representative in the EU (GDPR Art. 27) | **[name, address]** or "none — reason: [...]" (LQ-13) |
| Representative in Switzerland (FADP Art. 14) | **[name, address]** or "not required — reason: [...]" |
| Data protection officer | **[name, contact]** or "none designated" |
| Joint controllers | None assumed. The pigtail maintainers publish code only and receive no operator data (no telemetry in pigtail) (LQ-14). |
| Deployment | Host **[provider, region]**; code commit **[sha]**; `LLM_BACKEND` **[subscription / api]** |
| Record owner / last review | **[name]** / **[date]** |

## Part 2 · Common recipients, transfers and measures

### 2.1 Recipients (categories)
| Recipient | Role | Activities | Location | Notes |
|---|---|---|---|---|
| **[Hosting provider]** (VM, disks) | Processor (GDPR Art. 28; FADP Art. 9) | All | **[EU / CH]** (PRD §10 default) | Contract with processor terms required (O) |
| **[Object-storage provider]** (if not the bundled SeaweedFS on the same host) | Processor | Snapshots (A1, A1b, A1c, A2, A2b) | **[EU / CH]** | Private bucket, default encryption, TLS (`pigtail doctor`) |
| **[Backup target]** | Processor | A10 | **[EU / CH]** | Receives age- or gpg-encrypted backup files only (CB-17, I); the operator stores them off the host |
| **[E-mail provider]** (`SMTP_URL`, optional) | Processor | A9 alerts | **[...]** | Alerts hold no data-subject data (§A9) |
| **Anthropic, `api` backend** | Processor under the Commercial Terms, which incorporate the DPA ("Customer is the controller and Anthropic is Customer's processor"; DPIA §2.5) | A3 | United States | Default deletion within 30 days; ZDR only by agreement (retention policy §6). Contracting Anthropic entity: **[from your agreement]** |
| **Anthropic, `subscription` backend** | **Unclear** (LQ-1): Consumer Terms, counterparty for EEA and Swiss consumers Anthropic Ireland, Limited; no DPA | A3 | Ireland, onward to the United States | Owner's own non-commercial use only (ADR-008, ADR-023). Training must be off (H1). No ZDR. |
| **Public** | Recipient of published outputs | A7 (not active) | Worldwide | Aggregates only; blocked until H2 and H4 (LIA §7) |

Sources (GitHub, GH Archive, Hacker News) are **not** recipients: pigtail sends them only API requests for public repos, items and search terms (repo names, never a person's handle searched alone; ADR-031.4).

### 2.2 Transfers abroad (GDPR Art. 30(1)(e); FADP Art. 12(2)(g), Art. 16)
| Transfer | Country | Safeguard | Status |
|---|---|---|---|
| Anthropic (`api`) | United States | DPA with EU SCCs (Modules Two and Three), UK Addendum, Swiss addendum (DPIA §2.5). The US is adequate for organisations certified under the Data Privacy Framework (EU Commission; Swiss Federal Council decision in force 15 September 2024). **Whether Anthropic is certified: unknown, not checked.** | O: confirm |
| Anthropic (`subscription`) | Ireland, then United States | Anthropic's own safeguards per its Privacy Policy (SCCs and adequacy decisions); no DPA with the operator (LQ-1) | Open (H2) |
| Hosting, storage, backups, e-mail | **[country]** | None needed if EU/EEA or CH and the controller is in the EU or CH; otherwise **[safeguard]** | O |

### 2.3 Security measures (GDPR Art. 32(1); FADP Art. 8) — general description
Details, status and evidence: [dpia.md](dpia.md) §6 (measures per risk) and §9 (controls CB-01…). Summary:
- **Pseudonymisation at ingest** with keyed HMAC-SHA256 (`src/pigtail/pseudonymize.py`, `connectors/base.py`) (I); key held apart from data ([key-rotation runbook](runbooks/key-rotation.md), CB-09) (O).
- **Minimisation:** raw bytes dropped right after parsing for HN items, per-repo events and GitHub search pages (`drop_after_parse`, `src/pigtail/privacy/deletion.py`; CB-23, CB-24) (I); GH Archive raw dumps purged after 30 days (CB-04) (I).
- **Retention limits** enforced by `pigtail retention purge`, periods above policy rejected at startup (`src/pigtail/config.py`) (CB-01, CB-22) (I).
- **Encryption at rest:** bucket SSE via `S3_SSE_KEK` or provider encryption, checked by `pigtail doctor` (I, partly); Postgres and `PIGTAIL_DATA_DIR` volume encryption (O) (CB-03).
- **Access control:** services bound to `127.0.0.1` (`docker-compose.yml`); private UI behind an argon2id password, server-side sessions, audit log without IP addresses (CB-19, ADR-034; `src/pigtail/api/auth.py`) (I); role-based access not built, one operator account (P CB-19).
- **Redaction before LLM calls** (CB-06, partly) and CLI telemetry opt-outs (CB-07) (I).
- **Log hygiene:** redacting log filter in every CLI command and scheduled job (ADR-040.6); `runs.error` scrubbed and cleared after 12 months (CB-18) (I); container and system log rotation on the host (O); alert-file rotation (P CB-31).
- **Public-repo protection:** CI private-data scan and gitleaks (`scripts/private_data_scan.py`, `.github/workflows/ci.yml`) (I).
- **Rights and refusals:** access, erasure and opt-out tooling (CB-08, CB-13), keyed repo-name opt-outs (CB-13b, ADR-042.1) and a repo purge covering every repo-keyed table (CB-13c, ADR-044.1) (I); deletion sync for HN (CB-02) (I).
- **Monitoring:** host alerts and `pigtail doctor` (ADR-033) (I).
- **Incident response:** [breach runbook](runbooks/breach.md) (CB-16) (documentation done).
- **Backups:** encrypted to `BACKUP_RECIPIENT` (age, gpg fallback), 35-day pruning, deletions re-applied on restore (`pigtail backup create|restore|prune`; CB-17, ADR-044.2–3) (I); scheduling and off-host storage (O); continuous off-host shipping of `deletion_log` and opt-outs, and a backup container or scheduler job (P, CB-17 follow-ups). The LLM cache is not backed up.

---

## Part 3 · Activities

Legal basis is not a RoPA field, but is given for convenience: all activities on data subjects' data rely on legitimate interests (GDPR Art. 6(1)(f); FADP Art. 31(1) overriding private interest) as assessed in [lia.md](lia.md).

### A1 · GH Archive velocity scan
| Field | Value |
|---|---|
| Purpose | Detect star and fork bursts per repo from public GitHub event archives (R1.1), with bot and lockstep filtering, for research on open-source growth ([lia.md](lia.md) A1) |
| Data subjects | Every GitHub account that appears in a public event in the scanned hours (star, fork, push, issue, PR, comment…); people named or quoted in event payloads |
| Personal data | Raw hourly dumps: login, account id, avatar URL, event type, time, repo, payload text (issue/PR/comment bodies; older dumps may contain commit author names and e-mails; DPIA D1–D2). Parsed actors: pseudonyms **in memory only**. Stored results: per-repo hourly counts with no actor column (`repo_hourly_activity`, `migrations/0002_repo_hourly_activity.sql`) |
| Source | GH Archive (`data.gharchive.org`), TM-01 |
| Recipients | Hosting and storage processors (§2.1) |
| Transfers | None beyond hosting (§2.2) |
| Retention | Raw dumps: **30 days** by default (`GHARCHIVE_RAW_RETENTION_DAYS`, purge after every scan and daily `purge_raw` job) then `raw_dropped` with hash and URL kept (CB-04, I). Note: this setting has no upper cap in `src/pigtail/config.py` (unlike the other retention settings); keep it ≤ 30 (**P CB-32**). Evidence class `person_level_24m` (24-month ceiling). Aggregates: unlimited. |
| Security | §2.3; bot logins dropped before hashing; refusal list applied at ingest (CB-13) |
| Status | I. Local development only; no production capture until the ADR-022 production controls exist |

### A1b · Per-repo GitHub events (stargazer events for tracked repos)
| Field | Value |
|---|---|
| Purpose | Confirm or reject bot-driven star bursts for repos with an open case (bot-filter confirmation; ADR-032.2, ADR-036) |
| Data subjects | Accounts that starred or forked a repo with a live `velocity` case (and, only with `--prethreshold`, not scheduled, watch-list repos above the pre-threshold); actors of the repo's other recent events inside the raw response |
| Personal data | Kept: pseudonymised `WatchEvent`/`ForkEvent` actor, repo, time (`repo_event_actor`; CHECK allows only pseudonyms). Raw response (all recent repo events with actors and payloads) dropped right after parsing |
| Source | GitHub REST per-repo Events API (TM-33 under TM-02) |
| Recipients | Hosting and storage processors |
| Transfers | None beyond hosting |
| Retention | Actor rows and any surviving raw copy: **16 days** by default, ceiling 30 (`GITHUB_EVENTS_RETENTION_DAYS`; class `person_level_30d`; ADR-038). Aggregates without pseudonyms (`repo_event_daily_agg`): unlimited |
| Security | §2.3; never builds or exports a stargazer list (CB-23 tests); `repo_event_actor` never exported (ADR-042.4) |
| Status | **Off** by default (`PIGTAIL_ENABLE_GITHUB_EVENTS` plus the ADR-022 flag; scheduler job `enabled = false`). Pending LQ-29 |

### A1c · GitHub project-level detection (watch list, search sweeps, star history)
| Field | Value |
|---|---|
| Purpose | Find and track fast-growing repos (hourly counts, star-history series, watch-list nomination; ADR-032, ADR-037) |
| Data subjects | Owners of repos that are personal accounts (their login is part of `owner/name`); owners embedded in search result pages |
| Personal data | `owner/name` of repos (project-level, but personal data when the owner is a person; retention policy §1); raw `search/repositories` pages embed owner objects (login, id, avatar and profile URLs). Parsed: repo id, name, counts, dates, owner **type** only |
| Source | GitHub GraphQL, Search API, star-history endpoint (TM-02, TM-33) |
| Recipients | Hosting and storage processors |
| Transfers | None beyond hosting |
| Retention | Search pages: raw bytes dropped right after parsing (`SearchSweeper`, `src/pigtail/capture/github_screens.py`, CB-24; hash and URL kept). GraphQL and star-history snapshots and tables: `project_level`, unlimited |
| Security | §2.3; no login or actor columns in the detection tables (tested, DPIA D13); personal-account repos never named in public outputs (ADR-022, CB-20) |
| Status | I. Makes no live call until `GITHUB_TOKEN` is set |

### A2a · Hacker News rank poller
| Field | Value |
|---|---|
| Purpose | Record front-page rank history of stories (it cannot be backfilled; ADR-031.1) |
| Data subjects | HN users who submitted front-page stories (`by` field in the raw item); people named in story titles |
| Personal data | Raw item JSON with `by`: dropped right after parsing (hash kept, `raw_dropped`). Stored: item id, type, URL, title, score, comment count, time, rank history (`hn_story`, `migrations/0004_hn_ranks.sql`; no author column). Top-story id lists (project-level) |
| Source | HN Firebase API (TM-04) |
| Recipients | Hosting and storage processors |
| Transfers | None beyond hosting |
| Retention | Raw items: none (dropped at parse). `hn_story`: project-level, unlimited; title and URL cleared if the story is deleted upstream (ADR-040.3). Evidence class of raw items: `person_level_24m` (`connectors/hn_ranks.py`) |
| Security | §2.3; deletion sync tracks rank-poller stories (CB-02, I) |
| Status | I, **on by default** (ADR-031.1); runs every 5 minutes (`infra/schedule.toml`) |

### A2b · Hacker News mentions and comments
| Field | Value |
|---|---|
| Purpose | Capture discussions that mention a repo with an open case, to reconstruct how attention spread (R1.2) |
| Data subjects | Authors of HN stories and comments that mention the repo; people named in them |
| Personal data | Raw snapshots of search results and items (author handle, text, time, parent ids, points). Parsed: `hn_mention` (author **pseudonym**, item id and type, time, story/parent ids, points, comment count, title and URL for stories), `upstream_items` (item id, author pseudonym, deletion state; `migrations/0005_hn_mentions_deletion_sync.sql`) |
| Source | HN Algolia and Firebase APIs (TM-03, TM-04, TM-30) |
| Recipients | Hosting and storage processors; Anthropic once LLM coding of mentions starts (A3) |
| Transfers | None beyond hosting (until A3) |
| Retention | `person_level_24m`: 24 months from capture, then raw bytes and rows purged (CB-01). Upstream deletions acted on within 7 days (daily check for open cases, monthly otherwise; CB-02, `pigtail privacy deletion-sync --source hn`) |
| Security | §2.3; the owner alone is never searched (ADR-031.4); JSON API never returns HN authors (operator guide "Web app") |
| Status | **Off** by default: `PIGTAIL_ENABLE_HN=1` plus `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` required (ADR-031.2); held until every ADR-022 precondition exists (CB-12 open) |

### A3 · LLM coding
| Field | Value |
|---|---|
| Purpose | Extract structured, cited facts (event, asset and edge types) from snapshot text (F7, F15) |
| Data subjects | People whose posts, comments or event text appear in coded snapshots |
| Personal data | Sent: redacted text (e-mails, phones, profile URLs, DIDs and @mentions replaced; gists, avatar URLs and bare author handles not yet; CB-06 partly). Kept: input **hash** and outputs in `llm_cache` (coded fields, verbatim quoted spans, provenance) |
| Source | A1, A2b snapshots |
| Recipients | **Anthropic** (§2.1), under `api` or `subscription` |
| Transfers | United States (§2.2) |
| Retention | pigtail: `llm_cache` ≤ 24 months and never longer than its evidence (CB-05); usage ledger 24 months. Anthropic: see retention policy §6 (30 days by default in `api`; in `subscription` 30 days with training off, longer if flagged or training on) |
| Security | §2.3; closed output schemas; subscription CLI without tools, session persistence or telemetry (CB-07) |
| Status | **Planned (M5)**. Built: `LLMClient` and the cache; today only `pigtail llm smoke` calls a model, with a fixed test input and no cache (`cmd_llm_smoke`, `src/pigtail/cli.py`) |

### A7 · Publication of aggregate findings
Not active. Blocked until H2 and H4 ([lia.md](lia.md) §7). No personal data by design (CB-14, CB-20 planned). Kept here so that the record is complete when it starts.

### A8 · Private UI (D1) access and audit log
| Field | Value |
|---|---|
| Purpose | Secure operator access to the private evidence UI; detect brute force; account for snapshot views (R13.3, CB-19; ADR-034) |
| Data subjects | The operator and any staff given the password |
| Personal data | `ui_sessions`: SHA-256 of the session token, times. `ui_audit_log`: time, event (login success/failure/rate-limited, logout, snapshot view/gone/missing/integrity failure), route, HTTP status, evidence id, content hash, 16-char session-hash prefix, keyed hash of the truncated client network (IPv4 /24, IPv6 /48). **No IP address** (`migrations/0006_ui_auth_audit.sql`) |
| Source | The UI itself |
| Recipients | None beyond hosting |
| Transfers | None beyond hosting |
| Retention | Sessions: 12 h absolute, 120 min idle. Audit rows: `LOG_RETENTION_DAYS` (default and maximum 365), deleted **at each login** (`create_session()`, `src/pigtail/api/auth.py`). If nobody logs in, old rows are not deleted; `pigtail retention purge` does not cover this table (**P CB-33**). uvicorn access logs off by default |
| Security | argon2id password hash; HttpOnly SameSite=Strict cookie; rate limits; read-only database pool (ADR-034.5) |
| Status | I (CB-19 login and audit log; role-based access P) |

### A9 · Alerts, run records and logs
| Field | Value |
|---|---|
| Purpose | Operate the system: job health, failures, doctor checks, deletion-sync SLA (ADR-033) |
| Data subjects | Operator staff (alert e-mail recipients). Data subjects of A1–A2b only by accident (the scrubber removes handles, e-mails, profile URLs and DIDs) |
| Personal data | Alert files on the host (`PIGTAIL_DATA_DIR/alerts/ALERTS.md`, `alerts.jsonl`, `state.json`, mode 0600): rule, subject, severity, counts, times, fixed-template messages (`src/pigtail/scheduler/alerts.py`). Alert e-mails: recipient addresses. `runs` records: job, counts, scrubbed error text. Container and system logs |
| Source | pigtail |
| Recipients | **[E-mail provider]** if `SMTP_URL` is set. The public repo receives only `pigtail alerts export` summaries without message text |
| Transfers | **[...]** (e-mail provider location) |
| Retention | `runs.error`: cleared after `LOG_RETENTION_DAYS` (max 365) by the purge (I). Alert files: **no rotation today** (append-only; **P CB-31**). Container and system logs: 12 months by operator rotation (O) |
| Security | CB-18 scrubbing of every alert line and log record; files 0600 in a 0700 directory |
| Status | I (rotation gaps as noted) |

### A10 · Backups and exports
| Field | Value |
|---|---|
| Purpose | Restore after loss (GDPR Art. 32(1)(c)); portable project data (PRD §7) |
| Data subjects | Everyone in A1–A9 |
| Personal data | Encrypted database dumps (every Postgres table: pseudonymous rows, refusal list, request log, UI audit log) and bucket replicas of snapshots; not the LLM cache (SQLite under `PIGTAIL_DATA_DIR`); JSONL exports (project-level by default; person-level tables only with `--include-person-level`, never `repo_event_actor`, UI sessions or the audit log; ADR-042.4) |
| Source | pigtail stores |
| Recipients | **[Backup target]** (processor) |
| Transfers | **[...]** |
| Retention | Backups: 35-day rolling (`pigtail backup prune`, I CB-17; the operator schedules it, O). Person-level exports: delete when done (operator guide "JSONL export") (O) |
| Security | Backups encrypted as one stream to `BACKUP_RECIPIENT`; unencrypted output and output inside a git working tree refused; files 0600; restore re-applies every deletion (ADR-044.2–3; `src/pigtail/privacy/backup.py`) (I). Backups kept apart from `PSEUDONYM_KEY`, `S3_SSE_KEK` and the age identity (key-rotation runbook §2) (O); exports refused inside any git working tree for person-level data, files 0600 (I) |
| Status | Backups I (CB-17; follow-ups P: continuous off-host shipping of `deletion_log` and opt-outs, backup container or scheduler job). Exports I |

### A11 · Data-subject requests and the refusal list
| Field | Value |
|---|---|
| Purpose | Handle access, erasure and objection requests and keep honouring them (GDPR Art. 15, 17, 21; FADP Art. 25, 30(2)(b)) |
| Data subjects | Requesters; project owners who opt a repo out |
| Personal data | `privacy_suppression`: pseudonyms, repo ids, keyed repo-name hashes (never handles or names). `privacy_requests`: id, type, platform, times, outcome, counts (no handle, no pseudonym). Access export files (`PIGTAIL_DATA_DIR/requests/*.json`, 0600). The request correspondence itself (e-mails) lives outside pigtail |
| Source | The requester |
| Recipients | The requester (access export, sent through a secure channel) |
| Transfers | None |
| Retention | Refusal list: as long as the deployment runs (needed to keep honouring the objection). Request log: not purged by pigtail; it contains no identifier of the person. Access files: delete after sending (O). Correspondence: **[operator policy]** |
| Security | Handle pseudonymised at once and discarded (ADR-030.4); database CHECKs reject raw handles and names |
| Status | I (rectification not built, ADR-030.3) |

---

## Changelog
- 2026-09-25: v0.1 created (CB-15). Activities A1, A1b, A1c, A2a, A2b, A3, A7, A8, A9, A10, A11; each checked against `main`. Gaps recorded as CB-31 (alert-file rotation), CB-32 (cap on `GHARCHIVE_RAW_RETENTION_DAYS`) and CB-33 (UI audit-log purge without logins).
- 2026-09-25 — status sync (M3-T11): §2.1 backup target and §2.3 measures updated (CB-17 backups I with follow-ups; CB-18 I with host rotation O; CB-13b/CB-13c; CB-19 role-based access P); A8 status notes role-based access open; A10 backups implemented, LLM cache not in backups.
