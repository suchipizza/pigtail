# Runbook: pseudonym key (`PSEUDONYM_KEY`) management and rotation

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Control **CB-09** ([dpia.md](../dpia.md) §9)
**Applies to:** every pigtail deployment. The operator (the controller) runs it.
**Access date:** every URL was accessed on **2026-09-25**.
**Related:** [retention-policy.md](../retention-policy.md) §3 · [breach.md](breach.md) · [controller-duties.md](../controller-duties.md) · `docs/guides/operator.md` "Privacy operations"

**Status legend:** **I** = implemented in code on `main` (file cited). **P** = planned (backlog id). **O** = operator procedure. Every statement about pigtail's behaviour below was checked against the code on 2026-09-25.

**Legal anchors:**
- GDPR Art. 4(5): pseudonymisation requires that the "additional information is kept separately and is subject to technical and organisational measures". Art. 32(1)(a) lists "the pseudonymisation and encryption of personal data" among security measures. Text: [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj), read via the Publications Office copy (http://publications.europa.eu/resource/celex/32016R0679), because EUR-Lex returned a bot challenge.
- FADP Art. 8(1): "a level of data security appropriate to the risk". Text: [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en), English translation, consolidated version of 1 September 2023.
- EDPB Guidelines 9/2022 on personal data breach notification, version 2.0 (adopted 28 March 2023), ¶77: a keyed hash is treated as unintelligible only if "the key used to hash the data was not compromised in any breach" ([PDF](https://www.edpb.europa.eu/system/files/2023-04/edpb_guidelines_202209_personal_data_breach_notification_v2.0_en.pdf)).
- EDPB Guidelines 01/2025 on Pseudonymisation (adopted 16 January 2025, public consultation), [PDF](https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf). Already cited in the DPIA; not re-read for this runbook.

---

## 0. Summary for the operator

- `PSEUDONYM_KEY` turns every handle into a pseudonym (`p_` + 16 hex of HMAC-SHA256) and every repo-name opt-out into a keyed hash (`rk_…`). Whoever holds the key and the database can re-identify any pseudonym by hashing candidate handles, because handles are public.
- **Changing the key silently breaks the refusal list.** People and repos that opted out are no longer recognised at ingest, and collection about them resumes. pigtail does not detect a key change today (§4.1).
- **Until the rotation tooling exists (CB-25, CB-26, CB-27; §6), do not rotate on a schedule.** Rotate only after a compromise of the key (§5), and then follow §4 exactly. This replaces the "every 24 months" rule in [retention-policy.md](../retention-policy.md) §3.5 until CB-26 exists.

---

## 1. What the key is used for (verified in code)

| Use | Where in code | Stored result | Status |
|---|---|---|---|
| Pseudonym of a handle: `"p_" + HMAC-SHA256(key, "<namespace>:<handle, lowercased, without @>")[:16]` | `Pseudonymizer.pseudonym()`, `src/pigtail/pseudonymize.py` | Columns `hn_mention.author`, `upstream_items.author_pseudonym`, `repo_event_actor.actor_pseudonym` (the tables in `PERSON_TABLES`, `src/pigtail/privacy/deletion.py`); refusal-list entries of kind `pseudonym` (`privacy_suppression`) | I |
| GH Archive actors | `connectors/gharchive.py` via `Connector.records()` | Pseudonyms stay **in memory** only (bot and lockstep features); only per-repo counts are stored (`repo_hourly_activity` has no actor column, `migrations/0002_repo_hourly_activity.sql`) | I |
| Keyed repo-name opt-out: `"rk_" + HMAC-SHA256(key, "repo_name:<owner/name>")[:32]` (ADR-042.1) | `repo_name_key()`, `src/pigtail/privacy/suppression.py` | `privacy_suppression` rows of kind `repo_name` | I |
| Redaction before LLM calls: @mentions, profile URLs and DIDs become pseudonyms | `Pseudonymizer.strip_identifiers()`; `build_client()` refuses to start without the key (`src/pigtail/llm/client.py`) | The redacted text is hashed into the LLM cache key (`input_hash = sha256_text(safe_input)`, `LLMClient.cache_key`); outputs in `llm_cache` may quote pseudonyms | I |
| Data-subject requests and opt-outs: the handle given by the requester is pseudonymised with the current key and then discarded | `subject_pseudonym()` (`suppression.py`), `access()` / `erasure()` (`src/pigtail/privacy/requests.py`) | Refusal-list entry (pseudonym); `privacy_requests` stores **no** handle and **no** pseudonym (migration 0003) | I |
| Startup check | `run_checks()`, `src/pigtail/privacy/doctor.py` | `pseudonym_key`: FAIL if unset or shorter than 16 characters, WARN below 32, otherwise OK "value not shown" | I |

**Not derived from `PSEUDONYM_KEY`** (so a rotation does not touch them):
- **UI sessions and audit log (ADR-034).** Session rows store an unkeyed SHA-256 of the cookie token (`token_hash()`, `src/pigtail/api/auth.py`). The audit log's `client` column is an HMAC of the truncated client network whose key is derived from `PIGTAIL_OPERATOR_PASSWORD_HASH` (`client_key()`), so it changes when the **operator password** changes, not with `PSEUDONYM_KEY`. After a password change, rate-limit history per network starts again; this is harmless.
- **Snapshot content hashes** (SHA-256 of the raw bytes) and `deletion_log` tombstones (hashes and ids only, migration 0003).
- **SeaweedFS encryption** (`S3_SSE_KEK`) and all other credentials. They are separate secrets with their own rotation (see [breach.md](breach.md) §4.3).

---

## 2. Where the key lives

| Deployment | Location | Status |
|---|---|---|
| Docker Compose | `.env` next to `docker-compose.yml`, loaded by the `app` and `ui` services (`env_file: .env`). `.env` is refused by the private-data scan (`FORBIDDEN_PATH` in `scripts/private_data_scan.py`), so it cannot be committed. | I (scan) / O (file mode 0600, owner only) |
| systemd | `/etc/pigtail/pigtail.env`, mode 0600, root-owned, loaded with `EnvironmentFile=` (`infra/systemd/pigtail-scheduler.service`) | O |
| Secrets manager | Any manager that injects environment variables. pigtail reads only the environment (`Settings.from_env()`, `src/pigtail/config.py`). | O |

Rules (**O** unless marked):
1. **Generate** on the host: `openssl rand -hex 32` (64 hex characters, 256 bits). The code accepts 16 characters or more (`Pseudonymizer.__init__`), and `pigtail doctor` warns below 32 (**I**).
2. **Never** put the key in git, Postgres, the snapshot bucket, `PIGTAIL_DATA_DIR`, logs, tickets, chat or e-mail. pigtail does not write it anywhere: `doctor` prints "set (value not shown)", and no code path logs the `Settings` object (checked 2026-09-25). One weakness: `Settings.pseudonym_key` is a plain dataclass field, so `repr(settings)` would show it, whereas `s3_secret_key` is declared with `repr=False` (`src/pigtail/config.py`). Fixing this is part of **CB-29**.
3. **Least privilege.** Only processes that pseudonymise need it: capture, the scheduler, `privacy` commands and the LLM client. The UI does not use it (no reference in `src/pigtail/api/`), but under Compose the `ui` service loads the whole `.env`. Until per-service environments exist (**P, CB-29**), accept this, or give the `ui` service its own env file without `PSEUDONYM_KEY`.
4. **Separate backup.** Keep an encrypted copy of the key **apart from the data backups**: a password manager or a sealed offline copy held by the controller, not on the backup target. Record where it is (not the value) in your deployment notes. A data backup and the key must never be in the same place, or the backup is no longer pseudonymised in any meaningful sense (GDPR Art. 4(5), "kept separately").
5. **Test the backup** at least once a year: compare the fingerprint `printf %s "$PSEUDONYM_KEY" | sha256sum | cut -c1-12` of the live key and of the backup copy. Never compare or print the key itself.
6. **Record the fingerprint** (the 12 characters above, never the key) in your deployment notes. It is the only way to tell later whether the key changed, until CB-25 stores it.

---

## 3. What a rotation (or an accidental change) breaks

pigtail stores **no key id** next to its pseudonyms (no `key_id` or fingerprint column in any migration, checked 2026-09-25). A new key therefore produces different pseudonyms, and nothing marks the old ones as old.

| Affected | Effect of a new key | Severity | Mitigation today | Tooling needed |
|---|---|---|---|---|
| **Refusal list: pseudonym entries** (`privacy_suppression`, kind `pseudonym`; CB-13) | People who objected or asked for erasure are **no longer matched at ingest**: their data is collected again. The handles are not stored (ADR-030.4), so the entries cannot be recomputed from the database. | **Critical** (breaks GDPR Art. 21 and FADP Art. 30(2)(b) honouring) | Keep the old key (§4 step 2) and, before switching, map each entry to its handle **only where the handle appears in retained raw snapshots** (manual, operator-written script). Entries that cannot be mapped must be recorded and re-requested from the data subject if a contact exists (pigtail stores none). | **CB-26** re-pseudonymisation migration; **CB-27** dual-key matching |
| **Refusal list: keyed repo-name entries** (`rk_…`, ADR-042.1) | Orphaned: opted-out repos not in `repos` are collected again (HN mentions, watch list, Show HN screen). Entries by **repo id** (`<host>:<id>`) are unaffected. | High | Re-add each name with `pigtail privacy optout add --repo owner/name` after the switch (operator guide "Opt-outs"). The names are not stored; the source is the owner's original request. `pigtail privacy optout rekey` does **not** help: it converts only legacy unkeyed `rn_` entries (`rekey_unkeyed_names()`, `requests.py`). | CB-26 |
| **Pseudonymous person-level rows** (`hn_mention.author`, `upstream_items.author_pseudonym`, `repo_event_actor.actor_pseudonym`) | One person gets two pseudonyms (old and new). Access and erasure requests compute only the new pseudonym, so **rows under the old pseudonym are missed** until they expire (24 months; `repo_event_actor` 16 days by default). Star de-duplication within a case window double-counts across the switch. | High (erasure incomplete) | During the transition, answer each access or erasure request twice: once with the new key and once with the old key (§4 step 7). There is no command option for this today. Or wait for expiry. | CB-26; CB-27 |
| **Raw snapshots** | Not affected: they hold handles, not pseudonyms. Access and erasure re-parse retained snapshots with the current key (`scan_snapshots()`, `requests.py`), so they keep working. | None | — | — |
| **LLM cache** (`llm_cache`, SQLite under `PIGTAIL_DATA_DIR`) | Keys are hashes of redacted input, which contains pseudonyms, so the same input gets a **new cache key**: cache misses and repeated LLM calls (cost). Cached outputs may quote old pseudonyms that an erasure computed with the new key will not find (`find_containing`). | Medium | Delete the cache rows at the switch: `sqlite3 "$PIGTAIL_DATA_DIR/llm.sqlite3" 'DELETE FROM llm_cache; DELETE FROM llm_cache_evidence;'` (keeps the usage ledger). There is no pigtail command for this. Today only the smoke test calls the LLM, with `use_cache=False` (`cmd_llm_smoke`, `src/pigtail/cli.py`), so the cache is normally empty. | **CB-28** cache-clear command |
| **JSONL exports with `--include-person-level`** and **backups** | Contain old pseudonyms. Once the old key is destroyed they are unlinkable with the database. | Low | Delete old person-level exports; let backups expire (35-day rotation, planned CB-17). | — |
| **UI sessions and audit log** | Not affected (§1). | None | — | — |
| **Aggregates, project-level data, content hashes** | Not affected. | None | — | — |

---

## 4. Rotation procedure (manual, until CB-25 to CB-28 exist)

Use only after a compromise (§5) or when the lawyer or controller decides it is necessary. Plan a maintenance window. Nobody may run capture or `privacy` commands during it.

1. **Record the decision.** Open a breach record ([breach.md](breach.md) §7) if the reason is a compromise; otherwise note the reason in your deployment notes. Note the old key's fingerprint (§2 rule 5).
2. **Keep the old key, sealed.** Copy it to the separate key backup, marked "old, rotation of <date>". It is needed for steps 5 and 7 and must not be destroyed until step 9.
3. **Stop every writer.** `docker compose stop app ui`, or `systemctl stop pigtail-scheduler` and any cron entries (`pigtail alerts check`, `pigtail health --liveness-file …`, `retention purge`). Check that no `pigtail` process is running.
4. **Back up** the database and `PIGTAIL_DATA_DIR` (encrypted, stored apart from both keys).
5. **Map the refusal list** (the critical step; no tooling, **P CB-26**):
   - Export the entries: `pigtail privacy optout list`.
   - For each `pseudonym` entry, find the handle only if it appears in retained raw snapshots: parse the retained snapshots of that platform with the **old** key and keep the handles whose pseudonym is on the list. This has to be a local, operator-written script today; it must write nothing to git and nothing to logs. Then add each found handle with the **new** key (`pigtail privacy optout add --platform <p> --handle -`, run with the new key in the environment).
   - Record how many entries could not be mapped. They stay on the list (harmless) but no longer match anything. If you still have the original request (for example the e-mail), re-add the handle from it. If not, record the gap in the breach or change record; it is an unresolved objection.
   - For each `repo_name` (`rk_`) entry, re-add the name from the owner's original request with `optout add --repo owner/name` under the new key.
   - Entries of kind `repo` (by id) need nothing.
6. **Clear the LLM cache** (§3) so that no old pseudonyms stay in cached outputs.
7. **Decide about existing pseudonymous rows.** Either delete them now (they expire anyway: `repo_event_actor` after 16 days by default, the rest after 24 months), or keep them and answer every access and erasure request with both keys until they expire. With no tooling for the second option, **deleting is the safer choice** where the analysis allows it. Deleting needs SQL today; there is no command (**P CB-26**). Write a note in the change record, since the `deletion_log` has no reason code for a rotation.
8. **Switch.** Generate the new key (`openssl rand -hex 32`), put it in `.env` or `/etc/pigtail/pigtail.env` (mode 0600), back it up separately (§2 rule 4), record its fingerprint, and start the services. Run `pigtail doctor --strict`: `pseudonym_key` must be OK, and `optout_name_keys` must not report unkeyed entries.
9. **Destroy the old key** once no request needs it any more: when the old-pseudonym rows have been deleted or have expired (step 7) and the backups taken under the old key have rotated out. Record the date.
10. **Update** the deployment notes (new fingerprint, date), and, if the rotation followed a breach, close the breach record.

---

## 5. Compromise response

"Compromise" means the key may have been seen by someone not authorised: found in git, a log, a ticket or a backup stored with the data; a leaked `.env`; a host or secrets-manager intrusion; a departing person who had access.

1. **Treat it as a possible personal data breach** and open the breach runbook ([breach.md](breach.md)). Start the 72-hour clock assessment there (GDPR Art. 33(1)).
2. **Assess what else leaked.** The key alone reveals nothing: the pseudonyms live only in the private database and backups. The risk is high only if the attacker also has (or may get) the database, a backup or a person-level export, because handles are public and a dictionary of candidate handles reverses the pseudonyms. EDPB Guidelines 9/2022 ¶77 treat keyed-hash data as protected only if the key "was not compromised in any breach". A key leak together with data is therefore **not** covered by the Art. 34(3)(a) "unintelligible" exception.
3. **Contain.** Remove the key from wherever it leaked. For git, follow [breach.md](breach.md) §4.2 (the repo is public). Rotate the key (§4), keeping the old key sealed only as long as §4 needs it.
4. **Rotate the other secrets** that were stored with it (the same `.env` usually holds `DATABASE_URL`, `S3_*`, `S3_SSE_KEK`, `GITHUB_TOKEN`, `SMTP_URL`, `PIGTAIL_OPERATOR_PASSWORD_HASH`): see [breach.md](breach.md) §4.3.
5. **Document** it in the breach register, even if no notification is needed (GDPR Art. 33(5); FADP ordinance Art. 15(4)).

**Loss of the key** (no backup): new pseudonyms cannot be linked to old ones; the refusal list stops matching (§3). This is an availability breach of the refusal list. Follow §4 steps 5–8 as far as possible (the old-key mapping in step 5 is impossible) and record it in the breach register.

---

## 6. Tooling: what exists and what is missing

| Need | Exists? | Backlog |
|---|---|---|
| Minimum length and presence check; `doctor` check | Yes (`Pseudonymizer.__init__`, `doctor.py`) | — |
| Key never logged or printed | Yes (`doctor` hides the value; CB-18 scrubber on logs) | — |
| **Detect an accidental key change**: store a key fingerprint (for example an HMAC of a fixed label, never the key) in the database at first use; `doctor` FAILs and capture refuses to start when the running key does not match | **No** | **CB-25** |
| **Re-pseudonymisation migration** `pigtail privacy rekey-pseudonyms --old-key-env OLD_PSEUDONYM_KEY` (dry run first): maps refusal-list pseudonyms and `rk_` name keys through retained raw snapshots and local data, rewrites or deletes pseudonymous rows, clears the LLM cache, reports what could not be mapped, writes a `deletion_log`/change record | **No** | **CB-26** |
| **Dual-key matching** during a transition: ingest and access/erasure also compute the old-key pseudonym while an old key is configured, so objections keep working and old rows are found | **No** | **CB-27** |
| **LLM cache clear** command (all rows, keeps the usage ledger) | **No** (SQL only) | **CB-28** |
| Secret hygiene: the UI does not receive `PSEUDONYM_KEY` (per-service environment), and `Settings.pseudonym_key` gets `repr=False` | **No** (`ui` loads the full `.env` in `docker-compose.yml`; the field has no `repr=False`) | **CB-29** |
| Key escrow / backup | Operator procedure (§2) | O |

Until CB-25 to CB-27 exist, the key must not be rotated on a schedule (§0), and [retention-policy.md](../retention-policy.md) §3.5 points here.

## Open questions for the lawyer
- **LQ-30** ([legal-review-questions.md](../legal-review-questions.md)): if a rotation leaves refusal-list entries that can no longer be matched (§3), what must the controller do to keep honouring those objections, given that pigtail stores neither handles nor contact details of the requesters?

## Changelog
- 2026-09-25: v0.1 created (CB-09). Every statement about pigtail's behaviour was checked against `main`; CB-25 to CB-29 proposed.
