# Runbook: personal data breach response

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Control **CB-16** ([dpia.md](../dpia.md) §9)
**Applies to:** every pigtail deployment. The operator is the controller and owns every step. Fill in the `[bracketed]` fields before the deployment collects data.
**Access date:** every URL was accessed on **2026-09-25**.
**Related:** [key-rotation.md](key-rotation.md) · [retention-policy.md](../retention-policy.md) · [ropa.md](../ropa.md) · [controller-duties.md](../controller-duties.md) · [privacy-notice.md](../privacy-notice.md)

**Status legend:** **I** = implemented in code on `main` (file cited). **P** = planned (backlog id). **O** = operator procedure. Every statement about pigtail's behaviour was checked against the code on 2026-09-25.

## Legal texts used
- **GDPR** Art. 4(12) (definition), Art. 33 (notification to the supervisory authority), Art. 34 (communication to the data subject), Art. 32(1). Text: [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj), read via the Publications Office copy (http://publications.europa.eu/resource/celex/32016R0679), because EUR-Lex returned a bot challenge.
- **Swiss FADP** Art. 5(h) (definition) and Art. 24 (notification of data security breaches). Text: [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en), English translation (not legally binding), consolidated version of 1 September 2023.
- **Swiss Data Protection Ordinance (DPO)** Art. 15 (content of the report; documentation). Text: [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/568/en), English translation, consolidated version of 1 September 2023.
- **EDPB Guidelines 9/2022 on personal data breach notification under GDPR**, version 2.0, adopted 28 March 2023: [page](https://www.edpb.europa.eu/documents/guideline/guidelines-92022-on-personal-data-breach-notification-under-gdpr_en), [PDF](https://www.edpb.europa.eu/system/files/2023-04/edpb_guidelines_202209_personal_data_breach_notification_v2.0_en.pdf). Paragraph numbers below refer to that PDF.
- **FDPIC**: breach reporting portal https://databreach.edoeb.admin.ch/report (linked from [the FDPIC's DataBreach page](https://www.edoeb.admin.ch/en/databreach-4)); [FDPIC guidelines on data breaches](https://www.edoeb.admin.ch/en/guidelines-data-breach) (not read in full for this draft).
- **GitHub Docs**, "Removing sensitive data from a repository": https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository

The English FADP translation says "as quickly as possible" in Art. 24(1). This runbook quotes that wording.

---

## 0. One-page checklist

| When | What | Who |
|---|---|---|
| T0 | Someone notices a possible incident (§2). Write down the time. | Anyone |
| T0 + 1 h | Open a breach record (template A, §8). Name the incident lead. Start triage (§3). | [incident lead] |
| As fast as possible | Contain (§4). Stop the leak first, then investigate. | [incident lead] |
| "Aware" (§3.2) | Record the moment you have "a reasonable degree of certainty" that personal data were compromised. The GDPR 72-hour clock starts here. | [incident lead] |
| Aware + 48 h at the latest | Risk assessment (§5) and notification decision (§6). | [controller] |
| Aware + 72 h at the latest | GDPR: notify the competent supervisory authority unless the breach is unlikely to result in a risk (Art. 33(1)). FADP: notify the FDPIC "as quickly as possible" if high risk is likely (Art. 24(1)). | [controller] |
| Without undue delay | GDPR high risk: inform the data subjects, or publish a notice if individual contact is disproportionate (Art. 34). FADP: inform them if needed for their protection or if the FDPIC asks (Art. 24(4)). | [controller] |
| Always | Document the breach, including non-notified ones (GDPR Art. 33(5); DPO Art. 15(4), kept at least 2 years). | [controller] |
| After | Review: DPIA review trigger ([dpia.md](../dpia.md) §8), lessons learned, backlog items. | [controller] |

Roles to fill in: incident lead **[name, phone]**; controller decision-maker **[name]**; privacy contact **[e-mail]**; lawyer **[name, contact]**; hosting provider support **[contact]**.

---

## 1. What counts as a breach

- **GDPR Art. 4(12):** "a breach of security leading to the accidental or unlawful destruction, loss, alteration, unauthorised disclosure of, or access to, personal data transmitted, stored or otherwise processed".
- **FADP Art. 5(h):** "a breach of security that leads to the accidental or unlawful loss, deletion, destruction or modification or unauthorised disclosure or access to personal data".
- The EDPB groups breaches as **confidentiality** ("unauthorised or accidental disclosure of, or access to"), **integrity** ("unauthorised or accidental alteration") and **availability** ("accidental or unauthorised loss of access to, or destruction of") breaches (Guidelines 9/2022 ¶17).

### 1.1 pigtail's personal data (see [ropa.md](../ropa.md))
- **Raw snapshots** (bucket or `PIGTAIL_DATA_DIR/snapshots`): handles, names, text, and in GH Archive dumps possibly commit e-mails. **Directly identifying.**
- **Pseudonymous rows** in Postgres (`hn_mention`, `upstream_items`, `repo_event_actor`) and the refusal list (`privacy_suppression`). Identifying for anyone who also has `PSEUDONYM_KEY` (handles are public, so a dictionary reverses them).
- **`PSEUDONYM_KEY`**: not personal data by itself, but it is the "additional information" of GDPR Art. 4(5).
- **LLM cache** (`llm.sqlite3`): coded outputs and verbatim quoted spans.
- **Access-request files** (`PIGTAIL_DATA_DIR/requests/*.json`) and **person-level JSONL exports**: a person's data in one file.
- **UI audit log**: operator-staff events, keyed network hash, no IP address.
- **Backups** of all of the above (planned, CB-17).

### 1.2 Typical pigtail scenarios
| # | Scenario | Type |
|---|---|---|
| S1 | A snapshot, raw record, handle, pseudonym list or `.env` is committed to the **public** repo (or to a fork, gist, issue or CI log) | Confidentiality |
| S2 | `PSEUDONYM_KEY` leaks (with or without data) | Confidentiality (potential) |
| S3 | Host, database, bucket or secrets-manager compromise; stolen object-store or database credentials | Confidentiality / integrity |
| S4 | A backup, person-level export or access-request file is exposed (wrong bucket policy, sent to the wrong person) | Confidentiality |
| S5 | Loss: bucket deleted, `S3_SSE_KEK` lost (encrypted snapshots unreadable), `PSEUDONYM_KEY` lost (refusal list stops matching) | Availability |
| S6 | UI credentials compromised; unexplained `login_success` or `snapshot_view` rows in `ui_audit_log` | Confidentiality |
| S7 | Unredacted person-level text sent to the LLM provider outside the documented set-up (for example subscription mode with training on) | Confidentiality (assess case by case) |
| S8 | Opted-out data collected again (for example after a key change, [key-rotation.md](key-rotation.md) §3), or erased data restored from a backup without re-applying deletions | Not always a security breach under Art. 4(12), but a compliance incident: record it in the same register and assess with the lawyer |

---

## 2. Detection sources

| Source | What it catches | Status |
|---|---|---|
| **CI private-data scan** (`scripts/private_data_scan.py`, job "Private-data scan" in `.github/workflows/ci.yml`; also a pre-commit hook in `.pre-commit-config.yaml`) | Secrets and tokens (Anthropic, GitHub, AWS, Google, Slack, private keys, credential URLs), snapshot or raw-data paths and formats (`data/`, `snapshots/`, `raw/`, `.jsonl.gz`, `.sqlite3`, `.parquet`, `.env` …), personal e-mail addresses, unlisted fixtures, files over 1 MB. It does **not** detect pseudonyms or bare handles in prose (DPIA R7 residual). | I |
| **gitleaks** on the pushed commit range (`.github/workflows/ci.yml`) | Secrets in pushed commits | I |
| **Host alerts** (`PIGTAIL_DATA_DIR/alerts/`, optional e-mail; `src/pigtail/scheduler/alerts.py`) | `db_down`, `s3_down` (availability); `doctor` (any `pigtail doctor` WARN or FAIL, for example the bucket losing default encryption or `PSEUDONYM_KEY` unset); `disk_high`; `job_failing`/`job_stale`; `deletion_sla`; `scheduler_dead` from the external liveness check | I |
| **`pigtail doctor`** (`src/pigtail/privacy/doctor.py`) | Key presence and length, database and migrations, bucket encryption, TLS to a remote object store, source flags, unkeyed name opt-outs | I |
| **Snapshot hash check** when the UI opens a snapshot (`GET /api/snapshots/{hash}`, `src/pigtail/api/app.py`) | Altered raw bytes: refused with 500 and audited as `snapshot_integrity_failure` | I |
| **UI audit log** (`ui_audit_log`) | `login_failure`, `login_rate_limited`, unexpected `login_success` or `snapshot_view` | I (logged). **No alert** is raised from it; the operator must read it (SQL). **P CB-30** |
| **Key change** | An accidental `PSEUDONYM_KEY` change is **not detected** today | **P CB-25** |
| **Provider notices** | Hosting, object-storage, e-mail or Anthropic security notices; GitHub notifications about the repo | O |
| **Reports from people** | Messages to the privacy contact (privacy notice), GitHub issues, security reports | O |

**Operator routine (O):** read `ALERTS.md` daily; run `pigtail doctor --strict` after every deploy; review `SELECT at, event, evidence_id FROM ui_audit_log ORDER BY at DESC LIMIT 200` weekly; check the CI result of every push.

---

## 3. Triage

### 3.1 First questions (write the answers into template A)
1. What happened, when, and how was it found?
2. Which stores are involved (§1.1)? Raw snapshots, pseudonymous rows, the key, the cache, exports, backups?
3. Is it still happening? Who could have accessed the data, and for how long?
4. Roughly how many records and how many people? Which platforms (GitHub, HN …)? Which retention classes?
5. Is the data readable? Encrypted with a key that did not leak? Pseudonymised with a key that did not leak?

### 3.2 When are you "aware"?
The EDPB: "a controller should be regarded as having become 'aware' when that controller has a reasonable degree of certainty that a security incident has occurred that has led to personal data being compromised" (Guidelines 9/2022 ¶31). A "short period of investigation" is allowed first, but it "should begin as soon as possible" (¶34). Record the awareness time in template A. The 72 hours count from then, including weekends.

---

## 4. Containment

Stop the exposure first. Preserve evidence (logs, audit rows, the offending commit hash) in private storage, never in the repo.

### 4.1 General steps
- Revoke access: rotate the credentials involved (§4.3); end all UI sessions with `DELETE FROM ui_sessions;` (sessions are server-side; `check_session()` in `src/pigtail/api/auth.py` finds no row and refuses).
- Stop the affected jobs: `docker compose stop app ui` or `systemctl stop pigtail-scheduler`.
- Close the hole (bucket policy, firewall, exposed port). Postgres, the object store, the health port and the UI bind to `127.0.0.1` in `docker-compose.yml`; check that no one changed that.
- For availability breaches (S5): restore from backup, then re-apply deletions (`pigtail privacy optout purge`, `pigtail retention purge`; retention-policy §5). Backups are not built yet (**P CB-17**).

### 4.2 Personal data in the public repository (S1)
The repo is public, so assume the content was copied the moment it was pushed.
1. **Do not** repeat the content in a fix commit, a commit message, an issue or a PR description.
2. **Rotate first.** If a secret leaked, revoke or rotate it before anything else. GitHub's guide says this is the first step, and that once a secret is rotated, rewriting history "may not be necessary" for that secret. Personal data cannot be "rotated", so for snapshots, handles or pseudonym lists continue.
3. **Remove from the current tree** and push, so the CI scan passes again.
4. **Rewrite history** with `git filter-repo` (GitHub names version 2.47 or later with `--sensitive-data-removal`) and force-push. Side effects named by GitHub: all later commit hashes change, signatures become invalid, closed PR diffs can become inaccessible, and old clones can re-introduce the data on the next push. Tell every collaborator to re-clone. Coordinate this with the owner: it rewrites the public history of a project repo.
5. **Contact GitHub Support** after the force-push. Per the same guide, Support can dereference or delete affected pull requests, run garbage collection and remove cached views, and helps only where the risk "can't be mitigated by rotating affected credentials".
6. **Forks and clones** cannot be cleaned by you. Ask fork owners to delete the data or the fork. Other copies (mirrors, archives, caches) may exist; their number is unknown and must be recorded as such in the assessment.
7. Treat the exposure as a disclosure to the public for the risk assessment (§5), whatever the clean-up achieves.
8. Add a scan rule if the pattern was not caught (for example a new file type) (backlog, engineer).

### 4.3 Rotate keys and credentials
| Secret | How | Notes |
|---|---|---|
| `PSEUDONYM_KEY` | [key-rotation.md](key-rotation.md) §4 | Rotation breaks refusal-list matching unless done exactly as described. Read §3 of that runbook first. |
| `PIGTAIL_OPERATOR_PASSWORD_HASH` | `pigtail ui hash-password`, set the new hash, restart the UI, `DELETE FROM ui_sessions;` | Changes the audit-log client key too (ADR-034.2). |
| `POSTGRES_PASSWORD` / `DATABASE_URL` | Change the role password in Postgres, update the env, restart | — |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Rotate in the object store, update the env | — |
| `S3_SSE_KEK` (bundled SeaweedFS) | Procedure **unknown**: pigtail has no re-encryption tooling, and SeaweedFS KEK rotation was not verified | Do not change it without a tested plan: objects encrypted under the old KEK become unreadable. Add to CB-17 work. |
| `GITHUB_TOKEN` | Revoke in GitHub settings, create a new token | — |
| `ANTHROPIC_API_KEY` (api mode) | Revoke in the Anthropic Console, create a new key | pigtail never stores or logs Claude credentials. |
| `CLAUDE_CODE_OAUTH_TOKEN` (subscription mode) | Log out and re-authenticate with the official CLI; revoke the old token in your Claude account | pigtail never reads or stores it. |
| `SMTP_URL` credentials | Change the mailbox password | — |

---

## 5. Risk assessment

Assess the risk **to the people in the data**, not to the operator. Consider (EDPB Guidelines 9/2022 section IV): type of breach; nature, sensitivity and volume of the data; how easily people can be identified; severity and permanence of the consequences; special characteristics of the people; number of people affected.

| Factor | pigtail specifics |
|---|---|
| Origin | All person-level data comes from public platforms. This lowers, but does not remove, the risk: the combination (who starred what, who posted what, when, across repos) is not public in one place. |
| Identifiability | Raw snapshots: direct (handles, names, possibly e-mails in old GH Archive dumps). Pseudonymous rows: identifying only together with `PSEUDONYM_KEY`. EDPB ¶77: keyed-hash data is treated as protected only if the key "was not compromised". |
| Sensitivity | No special-category data is sought, but free text may contain some incidentally (LQ-9). |
| Volume | GH Archive dumps hold every public event of each hour (≤ 30 days kept by default); HN mentions only for open cases; per-repo events ≤ 16 days by default. Count affected snapshots with `evidence` and `deletion_state`. |
| Encryption | Snapshot bucket: SSE if `S3_SSE_KEK` or provider encryption is on (`pigtail doctor`). Postgres and `PIGTAIL_DATA_DIR`: only if the volume is encrypted (operator; `doctor` reports MANUAL). Encryption helps only if the key did not leak with the data (GDPR Art. 34(3)(a)). |

**Outcome levels:**
- **No risk likely:** document only (§7).
- **Risk:** GDPR notification to the supervisory authority (§6.1).
- **High risk:** also GDPR Art. 34 communication (§6.3) and FADP notification to the FDPIC (§6.2).

Examples (draft; the lawyer should confirm):
| Case | Likely level |
|---|---|
| Encrypted bucket, attacker without KEK or credentials; `doctor` shows encryption on | No risk likely (document) |
| `PSEUDONYM_KEY` alone in a private log, no data exposed, key rotated | No risk likely (document; LQ-31) |
| Pseudonymous rows **and** the key exposed | Risk; high if mentions or text are included |
| Raw snapshots with handles and text pushed to the public repo | Risk at least; assess high (public, permanent copies, cross-repo profiles possible) |
| Access-request file sent to the wrong person | High for that person |
| Snapshots lost with no backup (availability) | Risk usually low for data subjects (public origin), but document; refusal list loss is higher (objections stop working) |

---

## 6. Notification

### 6.1 GDPR: supervisory authority (Art. 33)
- **Deadline:** "without undue delay and, where feasible, not later than 72 hours after having become aware of it", unless the breach "is unlikely to result in a risk to the rights and freedoms of natural persons". A late notification must give "reasons for the delay" (Art. 33(1)).
- **Content** (Art. 33(3)): (a) nature of the breach, categories and approximate number of data subjects and records; (b) name and contact of the DPO or other contact point; (c) likely consequences; (d) measures taken or proposed. Information may be given "in phases without undue further delay" (Art. 33(4)).
- **Which authority:** the one competent under Art. 55 (the lead authority for cross-border processing by an EU-established controller). For a controller **not established in the EU** that is subject to the GDPR under Art. 3(2), the mere presence of a representative "does not trigger the one-stop-shop system", so "the breach will need to be notified to every supervisory authority for which affected data subjects reside in their Member State" (EDPB Guidelines 9/2022 ¶73). pigtail's data subjects are spread worldwide; how to handle this is LQ-31. List of EU authorities: [EDPB members](https://www.edpb.europa.eu/about-edpb/about-edpb/members_en).
- **Processors** (hosting, Anthropic in api mode) must notify the controller "without undue delay" (Art. 33(2)). Check your contracts for the contact route.

### 6.2 FADP: FDPIC (Art. 24)
- **Threshold and deadline:** notify the FDPIC "of any breach of data security that is likely to lead to a high risk to the data subject's personality or fundamental rights as quickly as possible" (Art. 24(1)). There is no fixed hour limit.
- **Content** (Art. 24(2)): at minimum "the nature of the breach of data security, its consequences and the measures taken or planned". DPO Art. 15(1) lists: (a) the form of breach; (b) time and duration, if possible; (c) categories and approximate amount of personal data, if possible; (d) categories and approximate number of data subjects, if possible; (e) consequences, including risks; (f) measures taken or planned; (g) name and contact details of a contact person. Missing details follow "as quickly as possible" (DPO Art. 15(2)).
- **How:** the FDPIC portal https://databreach.edoeb.admin.ch/report.
- **Processors** notify the controller "as quickly as possible" (Art. 24(3)).
- A notification "may only be used against the person required to notify in criminal proceedings with that person's consent" (Art. 24(6)).

### 6.3 Data subjects
- **GDPR Art. 34(1):** when the breach "is likely to result in a high risk", communicate it to the data subjects "without undue delay", in "clear and plain language", with the information in Art. 33(3)(b)–(d) (Art. 34(2)).
- **Not required** (Art. 34(3)) if (a) the data were unintelligible to unauthorised persons, "such as encryption"; (b) later measures make the high risk no longer likely; or (c) it "would involve disproportionate effort", in which case "there shall instead be a public communication or similar measure".
- **FADP Art. 24(4)–(5):** inform the data subject "if this is required for their protection or if the FDPIC so requests"; information may be limited, delayed or dropped where it "is impossible or requires disproportionate effort", or where "a public announcement" gives it equally. DPO Art. 15(3): give the data subject the details in Art. 15(1)(a) and (e)–(g) "in simple and comprehensible language".
- **pigtail reality:** pigtail stores no e-mail or contact details for data subjects, only platform handles (in raw snapshots) and pseudonyms. Individual contact would mean messaging people on the platforms, which may itself conflict with platform terms and "no auto-posting". A **public communication** (template D, on the page where the privacy notice is published, and linked from the README of the deployment) is therefore the expected route; the lawyer should confirm (LQ-31).

---

## 7. Documentation (every breach)

- GDPR Art. 33(5): "The controller shall document any personal data breaches, comprising the facts relating to the personal data breach, its effects and the remedial action taken."
- DPO Art. 15(4): "The controller must document the breaches." The documentation contains "a summary of the circumstances of the incidents, their effects and the measures taken" and is kept "for a minimum of two years" from the report.
- EDPB Guidelines 9/2022: controllers are "encouraged to establish an internal register of breaches, regardless of whether they are required to notify or not"; record the reasoning for decisions, and "if a breach is not notified, a justification for that decision should be documented" (¶121–125).

**Where:** a private breach register kept by the controller (for example an encrypted document outside the repo). **Never** in `ops/`, issues or anything else in the public repo; a public post-mortem may only contain what the public communication contains. Keep it at least 2 years (DPO Art. 15(4)); pigtail's policy default is **5 years** after closure unless the lawyer advises otherwise. The register holds no personal data about the data subjects beyond counts and categories.

---

## 8. Templates

### A. Internal breach record
```
Breach id:            BR-[YYYY]-[nn]
Opened by / at:       [name] / [UTC time]
Detected by:          [source from §2]
Aware at (§3.2):      [UTC time]           72 h deadline: [UTC time]
Scenario (§1.2):      [S1–S8 / other]
Type:                 [confidentiality / integrity / availability]
Stores involved:      [raw snapshots / pseudonymous rows / key / LLM cache / export / backup / UI audit]
Platforms / sources:  [GitHub, GH Archive, HN …]
Data subjects:        [categories], approx. [n]    Records: approx. [n]
Data readable?        [encrypted? key leaked? pseudonym key leaked?]
Timeline:             [start – end of exposure]
Containment:          [steps, times]
Keys rotated:         [list, times]
Risk level (§5):      [none likely / risk / high]   Reasoning: [...]
GDPR SA notified?     [yes, which, when, reference / no, why]
FDPIC notified?       [yes, when, reference / no, why]
Data subjects told?   [individual / public communication / no, why]
Follow-up actions:    [backlog ids]
Closed at / by:       [UTC time] / [name]
```

### B. Notification to a GDPR supervisory authority (Art. 33(3))
Use the authority's own form where it has one; this is the content.
```
Controller: [operator legal name, address, country]; representative: [if any]
Contact point: [name, e-mail, phone]
Nature of the breach: [what happened, type, when it started, when found, when aware]
Categories of data subjects: [e.g. GitHub users whose public events appeared in hourly archives]
Approximate number of data subjects: [n or range; "unknown, investigation ongoing"]
Categories of personal data: [handles, public post text, pseudonyms, …]
Approximate number of records: [n]
Likely consequences: [...]
Measures taken or proposed: [containment, key rotation, history rewrite, public notice]
Cross-border: [Member States of affected people, if known]
Phased notification: [yes/no; what follows and when]
Reason for any delay beyond 72 h: [...]
```

### C. Report to the FDPIC (FADP Art. 24(2); DPO Art. 15(1))
Submit through https://databreach.edoeb.admin.ch/report; keep the confirmation.
```
Form of breach: [...]
Time and duration: [...]
Categories and approximate amount of personal data: [...]
Categories and approximate number of data subjects: [...]
Consequences, including risks, for the data subjects: [...]
Measures taken or planned (remedy and mitigation): [...]
Contact person: [name, e-mail, phone]
```

### D. Public communication to data subjects (GDPR Art. 34(2), (3)(c); FADP Art. 24(5)(c); DPO Art. 15(3))
```
Security incident at [deployment name], [date]

What happened: On [date] we found that [plain description]. It affected [what data]
about [which people, e.g. "people whose public GitHub activity was recorded between X and Y"].

What this may mean for you: [likely consequences, plain language].

What we have done: [containment, deletion, key rotation, clean-up requests].

What you can do: [e.g. nothing is required / check …]. To ask whether your data was
affected, or to object or ask for erasure, write to [privacy contact] with the
platform and your username. We reply within one month.

Contact: [privacy contact]. You can also complain to a data protection authority
(see our privacy notice: [link]).
```

---

## 9. After the incident
- Run the DPIA review (a breach is a review trigger, [dpia.md](../dpia.md) §8) and update [ropa.md](../ropa.md) if the processing changes.
- Add engineering follow-ups to the backlog (for example a new scan pattern).
- If an agent session copies anything to `ops/`, it may copy only the sanitized status (id, date, "closed"), never details.

## Tooling gaps found while writing this runbook
- **CB-30:** no alert from the UI audit log (repeated `login_failure`/`login_rate_limited`, any `snapshot_integrity_failure`, logins at unusual times).
- **CB-25:** an accidental `PSEUDONYM_KEY` change is not detected ([key-rotation.md](key-rotation.md) §6).
- **CB-17** (existing): no backups, so availability breaches cannot be repaired from pigtail tooling yet.
- **CB-31:** the host alert files (`ALERTS.md`, `alerts.jsonl`) grow without rotation (`_write()` appends; no pruning in `src/pigtail/scheduler/alerts.py`). They hold no data-subject data by design, but need a 12-month rotation like other logs (CB-18).

## Open questions for the lawyer
- **LQ-31** ([legal-review-questions.md](../legal-review-questions.md)): which supervisory authority or authorities must be notified when the data subjects are worldwide and the controller may not be established in the EU (EDPB ¶73); whether a key-only leak is a notifiable breach; whether a public communication is the right Art. 34(3)(c) route when pigtail holds no contact details.

## Changelog
- 2026-09-25: v0.1 created (CB-16). Statements about pigtail checked against `main`; CB-30 and CB-31 proposed; LQ-31 raised.
