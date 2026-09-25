# Your duties as controller of a pigtail deployment

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Control **CB-21** ([dpia.md](dpia.md) §9)
**Audience:** anyone who runs pigtail: the owner's own deployment and any self-hoster.
**Access date:** every URL was accessed on **2026-09-25**.
**Legal texts:** GDPR ([EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj), read via the Publications Office copy http://publications.europa.eu/resource/celex/32016R0679); Swiss FADP ([fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en), English translation, consolidated version of 1 September 2023). Statements about pigtail's behaviour were checked against `main` on 2026-09-25; **P** marks what is planned, not built.

---

## 1. You are the controller

pigtail collects public activity about people (GitHub accounts, Hacker News authors) and stores it on **your** host. Whoever runs a deployment "determines the purposes and means of the processing" (GDPR Art. 4(7)); the FADP uses the same test (Art. 5(j)). So:

- **You** are the controller for your deployment, and you must "be able to demonstrate compliance" (GDPR Art. 5(2), accountability).
- The pigtail maintainers publish code, defaults and documentation. pigtail has no telemetry and sends the maintainers nothing, so they never see your data. Whether they could nonetheless be joint controllers is open (LQ-14).
- The documents in `docs/compliance/` are written for the owner's deployment. They are a starting point, not your compliance: **adopt or adapt each one under your own name**.

Checklist (tick in your private deployment notes):

| # | Duty | What to do | Where | Before |
|---|---|---|---|---|
| 1 | LIA | Review [lia.md](lia.md), adapt the purposes to yours, record your balancing result and sign it | GDPR Art. 6(1)(f); FADP Art. 31(1) | First capture |
| 2 | DPIA | Review [dpia.md](dpia.md) against your set-up (sources enabled, backend, hosting), sign §8, re-run on each trigger in §8 | GDPR Art. 35; FADP Art. 22 | First capture |
| 3 | Privacy notice | Fill in [privacy-notice.md](privacy-notice.md) and publish it at a stable public URL; link it from your deployment's README and UI | GDPR Art. 14; FADP Art. 19 | Any person-level source beyond GH Archive (ADR-022, CB-12) |
| 4 | Record of processing | Fill in [ropa.md](ropa.md) and keep it current | GDPR Art. 30; FADP Art. 12 | First capture |
| 5 | Requests | Handle access, erasure and objection requests with the CLI (§3) | GDPR Art. 12, 15, 17, 21; FADP Art. 25, 30(2)(b) | Notice published |
| 6 | Retention | Schedule the purge; don't raise limits (§4) | GDPR Art. 5(1)(e); FADP Art. 6(4) | First capture |
| 7 | Deletion sync | Keep the HN sync job running; watch `deletion_sla` alerts (§4) | [retention-policy.md](retention-policy.md) §4 | Enabling HN mentions |
| 8 | Security | Encrypt volumes and the bucket, protect keys, back up safely (§5) | GDPR Art. 32; FADP Art. 8 | First capture |
| 9 | Breaches | Name an incident lead, fill in [runbooks/breach.md](runbooks/breach.md) | GDPR Art. 33–34; FADP Art. 24 | First capture |
| 10 | LLM backend | Pick the backend that fits your use (§6) | ADR-008, ADR-023 | First LLM coding |
| 11 | Hosting | EU or Switzerland; processor contracts (§7) | GDPR Art. 28; FADP Art. 9 | First capture |
| 12 | Purpose limits | Research on projects, not people (§8) | GDPR Art. 5(1)(b) | Always |

`pigtail doctor --strict` checks the technical parts it can see (key, database, bucket encryption, source flags). It cannot check the legal ones; the ADR-022 flag `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` always shows as WARN for that reason (`src/pigtail/privacy/doctor.py`). **Only set that flag when items 1–9 are done.**

---

## 2. Documents to adopt

- **LIA and DPIA.** The owner's versions conclude that a DPIA is required (WP248 criteria for systematic monitoring, large scale and combined datasets) and that legitimate interest holds only with the safeguards listed. If you enable fewer sources, your risk is lower; if you add sources or features (for example spread graphs, which are on hold, LQ-8), you must extend both first.
- **Privacy notice.** pigtail does not collect data from the people concerned, so the notice is how you meet the information duty (GDPR Art. 14; FADP Art. 19). The disproportionate-effort question is LQ-10. Keep the notice's retention periods in step with your settings.
- **RoPA.** Assume the GDPR Art. 30(5) small-organisation exemption does not apply, because capture is not occasional. The FADP has an exemption for small undertakings and natural persons (Data Protection Ordinance Art. 24); keep a record anyway ([ropa.md](ropa.md) "Legal basis").

---

## 3. Handling requests (built: CB-08, CB-13)

The operator guide section "Privacy operations" has the commands. In short:

```bash
uv run pigtail privacy request access  --platform github --handle -   # export to a 0600 JSON file
uv run pigtail privacy request erasure --platform github --handle -   # erase + opt out
uv run pigtail privacy optout add --platform github --handle -        # objection
uv run pigtail privacy optout add --platform github --repo owner/name # a project owner opts out
uv run pigtail privacy requests                                       # request log (no handles)
```

- **Deadlines:** GDPR "within one month of receipt", extendable by two months with notice (Art. 12(3)); FADP access "in general … within 30 days" (Art. 25). Use 30 days as your target.
- **Identity:** check account control for access requests only (retention policy §5). Objections and erasure need no proof.
- **Objections are honoured without asking for grounds** (conservative default).
- **Not built:** rectification and a separate lookup command (ADR-030.3). Handle rectification by hand and record it.
- **Key dependency:** requests and opt-outs are computed with `PSEUDONYM_KEY`. Never change the key casually: opt-outs stop matching ([runbooks/key-rotation.md](runbooks/key-rotation.md) §3).

---

## 4. Retention and deletion sync (built: CB-01, CB-02 for HN, CB-17 restore, CB-22)

- Keep the scheduler's `retention_purge` and `purge_raw` jobs (daily, `infra/schedule.toml`) running, or run `pigtail retention purge` daily from cron.
- The limits are hard caps: person-level 24 months, per-repo events 16 days by default (ceiling 30), LLM cache 24 months, run error text and UI audit log 12 months, raw GH Archive dumps 30 days (CB-32); higher values are refused at startup (`src/pigtail/config.py`). The daily retention purge also deletes UI audit rows past `LOG_RETENTION_DAYS` and expired UI sessions (CB-33).
- With HN mentions enabled, keep the daily `deletion_sync` job on. A `deletion_sla` alert means an upstream deletion is more than 7 days overdue: act on it.
- Restore only with `pigtail backup restore`: it carries the live `deletion_log`, opt-out list and request log over, replays every tombstone, re-applies the opt-out list and runs the retention purge (CB-17, ADR-044.3; retention policy §5). If you restore any other way, run `pigtail privacy optout purge`, then `pigtail retention purge`. If the live database was lost too, deletions made after the last backup are lost: re-enter them from your request records.
- pigtail's own log records go through the redacting filter on every command and scheduled job (CB-18, ADR-040.6). Rotate container and system logs at 12 months yourself (journald or logrotate). The host alert files are not rotated by pigtail yet (**P CB-31**); prune them yourself.

---

## 5. Security, keys and backups

- **Encryption at rest** (CB-03): enable bucket encryption (`S3_SSE_KEK` for the bundled SeaweedFS, or provider SSE); put the Postgres volume and `PIGTAIL_DATA_DIR` on encrypted disks. `doctor` reports what it can and marks the rest MANUAL.
- **`PSEUDONYM_KEY`**: generate, store and back up exactly as in [runbooks/key-rotation.md](runbooks/key-rotation.md) §2. Keep it apart from data backups, and record its `kfp1_` fingerprint (`running_key_fingerprint` from `pigtail privacy key-fingerprint`) in your deployment notes. pigtail refuses to run under a different key (CB-25): if `pigtail doctor` reports `pseudonym_key_fingerprint` FAIL and you did not rotate on purpose, restore the original key; never reset. Do not rotate on a schedule until the re-keying tooling exists (CB-26, CB-27); after a compromise, follow the runbook's §4 exactly. Under Compose the `ui` service does not receive the key (CB-29); keep it that way if you edit `docker-compose.yml`.
- **Backups** (CB-17, built: `pigtail backup create --out DIR`, `pigtail backup prune --dir DIR`, `pigtail backup restore --in FILE --yes`; `src/pigtail/privacy/backup.py`, ADR-044). Set `BACKUP_RECIPIENT` (an age public key; gpg as fallback); output is always encrypted and refused inside a git working tree. **Your part:** install `pg_dump` 16 and age on the host (the app image has neither), schedule create and prune yourself (no scheduler job yet), keep the age identity (`BACKUP_IDENTITY`) apart from the backups and from `PSEUDONYM_KEY`, store copies off the host in the EU or Switzerland, and give the bucket replicas the same 35-day expiry (snapshot bytes are not in the dump). The LLM cache (`PIGTAIL_DATA_DIR`) is not backed up. Until `deletion_log` and opt-outs are shipped off the host continuously (CB-17 follow-up, **P**), keep your request records so you can re-enter deletions. Record it in the RoPA (A10).
- **Private UI:** keep it on loopback; use an SSH tunnel or a TLS reverse proxy; use a strong password (`pigtail ui hash-password`). There is one operator account and no role-based access (CB-19 remainder, **P**): everyone with the password sees everything, so give it only to staff who need full access. Review `ui_audit_log` weekly; there is no automatic alert from it yet (**P CB-30**).
- **Public repo:** never commit data, `.env` files, exports or alert files. The CI private-data scan and gitleaks catch many mistakes, not all (they do not detect pseudonyms or bare handles in prose).
- **Breaches:** follow [runbooks/breach.md](runbooks/breach.md). GDPR: notify the supervisory authority within 72 hours of becoming aware unless no risk is likely (Art. 33(1)). FADP: notify the FDPIC "as quickly as possible" if high risk is likely (Art. 24(1)). Document every breach.

---

## 6. LLM backend: subscription vs api (ADR-008, ADR-023)

| | `subscription` (default) | `api` |
|---|---|---|
| Who may use it | **Only an operator running pigtail for themselves, on their own Claude plan, through the unmodified official `claude` CLI** (ADR-008, from https://code.claude.com/docs/en/legal-and-compliance). The owner's use is further limited to her own non-commercial research, with no bulk person-level coding until LQ-1 and LQ-2 are answered (ADR-023). | Any deployment, and **required** for any deployment that serves other users (ADR-008) |
| Anthropic's role | Unclear; Consumer Terms, no DPA (LQ-1). Consumer Terms for EEA/Swiss users contain a no-commercial-or-business-use sentence (LQ-2) | Processor under the Commercial Terms and DPA |
| Retention at Anthropic | 30 days with training off; no ZDR | 30 days by default; ZDR by agreement |
| Your duties | Turn off model training in your Claude account (H1). Never use `/feedback` on product sessions. pigtail sets the CLI's telemetry, error-reporting and feedback opt-outs itself (CB-07) | Accept the DPA (part of the Commercial Terms); request ZDR where eligible; record Anthropic as a processor in your RoPA |

- pigtail redacts identifiers before every call on both backends (CB-06, partly: gists, avatar URLs and bare author handles are not redacted yet).
- **If you run pigtail for a company, a client or other users, set `LLM_BACKEND=api`.** Switching needs a restart and nothing else.
- Today no product job sends captured data to a model; only `pigtail llm smoke` calls it with a fixed test input. LLM coding arrives with M5. Re-read this section then.

---

## 7. Hosting in the EU or Switzerland

- PRD §10 sets the default region to the EU or Switzerland. Choose a host and object store there, and sign their processor terms (GDPR Art. 28; FADP Art. 9, which also requires you to "satisfy" yourself that the processor "is able to guarantee data security").
- Anthropic processes in the United States in both modes; record the transfer and its safeguard in the RoPA (§2.2). Whether Anthropic is certified under the Data Privacy Framework was not checked (unknown).
- If you are established outside the EU and Switzerland, you may need representatives there (GDPR Art. 27; FADP Art. 14). Ask your lawyer (LQ-13).

---

## 8. Purpose limits (what not to do)

- pigtail studies how **projects** grow. Do not use it to look up, score or profile individuals, and do not add person search to the UI (DPIA R12).
- Do not name private individuals, matched losers or repos owned by personal accounts in any public output (ADR-022; CB-14 and CB-20 planned).
- Do not enable a source that the terms memos mark as a gap, and do not enable person-level sources before the ADR-022 preconditions exist in **your** deployment.
- Account-level spread graphs are on hold (LQ-8).

---

## 9. Review

Review this checklist, your LIA, DPIA, RoPA and notice when you enable a source, change `LLM_BACKEND`, move hosts, after any breach, when H2 answers arrive, and at least every 12 months.

## Changelog
- 2026-09-25: v0.1 created (CB-21). Linked from `docs/guides/operator.md` ("Your duties as controller").
- 2026-09-25 — status sync (M3-T11): §4 restore re-applies deletions (CB-17) and the log filter runs on every command and job (CB-18); §5 backups built (CB-17, ADR-044) with the operator's remaining duties and follow-ups; §5 private UI has no role-based access (CB-19).
- 2026-09-25 — ADR-045 (CB-25/29/32/33/34): §4 GH Archive raw retention capped at 30 days (CB-32) and UI audit rows and sessions purged daily (CB-33); §5 key fingerprint, fail-closed check and "restore, don't reset" (CB-25), `ui` service without the key (CB-29).
