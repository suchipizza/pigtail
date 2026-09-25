# Runbook: pseudonym key (`PSEUDONYM_KEY`) management and rotation

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.2 · 2026-09-25 · Control **CB-09**; key-change detection **CB-25** and secret hygiene **CB-29** (ADR-045) ([dpia.md](../dpia.md) §9)
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
- **A new key breaks the refusal list.** Opt-outs are keyed hashes, so under another key people and repos that opted out are no longer recognised at ingest, and collection about them would resume.
- **pigtail now detects a key change and fails closed** (CB-25, ADR-045; **I**). The database records a fingerprint of the key on first use (`kfp1_…`, §1). Every command that pseudonymises or matches opt-outs, backup restore and scheduler startup **refuse** (exit 2) when the running key does not match, and `pigtail doctor` reports `pseudonym_key_fingerprint` as FAIL. `pigtail privacy key-fingerprint` shows the status (`ok`, `mismatch` or `unset`; exit 1 on `mismatch`).
- **A mismatch you did not plan is an incident, not a rotation.** Restore the original key from its separate backup (§2 rule 4). **Do not reset the fingerprint.** Only a documented rotation (§4) ends with `pigtail privacy key-fingerprint --reset --confirm-rotation`.
- **Until the re-keying tooling exists (CB-26, CB-27; §6), do not rotate on a schedule.** Rotate only after a compromise of the key (§5), and then follow §4 exactly. This replaces the "every 24 months" rule in [retention-policy.md](../retention-policy.md) §3.5 until CB-26 exists.

---

## 1. What the key is used for (verified in code)

| Use | Where in code | Stored result | Status |
|---|---|---|---|
| Pseudonym of a handle: `"p_" + HMAC-SHA256(key, "<namespace>:<handle, lowercased, without @>")[:16]` | `Pseudonymizer.pseudonym()`, `src/pigtail/pseudonymize.py` | Columns `hn_mention.author`, `upstream_items.author_pseudonym`, `repo_event_actor.actor_pseudonym` (the tables in `PERSON_TABLES`, `src/pigtail/privacy/deletion.py`); refusal-list entries of kind `pseudonym` (`privacy_suppression`) | I |
| GH Archive actors | `connectors/gharchive.py` via `Connector.records()` | Pseudonyms stay **in memory** only (bot and lockstep features); only per-repo counts are stored (`repo_hourly_activity` has no actor column, `migrations/0002_repo_hourly_activity.sql`) | I |
| Keyed repo-name opt-out: `"rk_" + HMAC-SHA256(key, "repo_name:<owner/name>")[:32]` (ADR-042.1) | `repo_name_key()`, `src/pigtail/privacy/suppression.py` | `privacy_suppression` rows of kind `repo_name` | I |
| Redaction before LLM calls: @mentions, profile URLs and DIDs become pseudonyms | `Pseudonymizer.strip_identifiers()`; `build_client()` refuses to start without the key (`src/pigtail/llm/client.py`) | The redacted text is hashed into the LLM cache key (`input_hash = sha256_text(safe_input)`, `LLMClient.cache_key`); outputs in `llm_cache` may quote pseudonyms | I |
| Data-subject requests and opt-outs: the handle given by the requester is pseudonymised with the current key and then discarded | `subject_pseudonym()` (`suppression.py`), `access()` / `erasure()` (`src/pigtail/privacy/requests.py`) | Refusal-list entry (pseudonym); `privacy_requests` stores **no** handle and **no** pseudonym (migration 0003) | I |
| Startup check | `run_checks()`, `src/pigtail/privacy/doctor.py` | `pseudonym_key`: FAIL if unset or shorter than 16 characters, WARN below 32, otherwise OK "value not shown". `pseudonym_key_fingerprint`: OK if the running key matches the recorded fingerprint, FAIL on a mismatch, WARN while none is recorded; doctor never records one | I |
| Key fingerprint (CB-25, ADR-045): `"kfp1_" + HMAC-SHA256(key, "pigtail-key-fingerprint-v1")[:32]` (128 bits). It identifies the key without revealing it and cannot compute pseudonyms. | `Pseudonymizer.fingerprint()`, `src/pigtail/pseudonymize.py`; `verify()`, `reset()`, `src/pigtail/privacy/key_fingerprint.py` | One-row table `pseudonym_key_fingerprint`, recorded on first use; append-only history `pseudonym_key_fingerprint_log` (events `recorded`, `reset`; migration 0012). Never exported (`src/pigtail/export/jsonl.py`). Checked by the opt-out list loader (every collector), the connector base, the `privacy` commands and data-subject request functions, `backup restore` (before anything is replaced; the live fingerprint is kept) and `pigtail scheduler run` at startup. `retention purge` is not gated (deleting is always safe). | I |

**Not derived from `PSEUDONYM_KEY`** (so a rotation does not touch them):
- **UI sessions and audit log (ADR-034).** Session rows store an unkeyed SHA-256 of the cookie token (`token_hash()`, `src/pigtail/api/auth.py`). The audit log's `client` column is an HMAC of the truncated client network whose key is derived from `PIGTAIL_OPERATOR_PASSWORD_HASH` (`client_key()`), so it changes when the **operator password** changes, not with `PSEUDONYM_KEY`. After a password change, rate-limit history per network starts again; this is harmless.
- **Snapshot content hashes** (SHA-256 of the raw bytes) and `deletion_log` tombstones (hashes and ids only, migration 0003).
- **SeaweedFS encryption** (`S3_SSE_KEK`) and all other credentials. They are separate secrets with their own rotation (see [breach.md](breach.md) §4.3).

---

## 2. Where the key lives

| Deployment | Location | Status |
|---|---|---|
| Docker Compose | `.env` next to `docker-compose.yml`, loaded only by the `scheduler` service (`env_file`) and by one-off `docker compose run --rm scheduler pigtail …` commands. The `ui` service has no `env_file`: it gets an explicit list of variables (database URL, snapshot-store settings, operator login, retention periods) and **not** `PSEUDONYM_KEY`, tokens, SMTP or API keys (CB-29, ADR-045; `docker-compose.yml`). `.env` is refused by the private-data scan (`FORBIDDEN_PATH` in `scripts/private_data_scan.py`), so it cannot be committed. | I (scan, `ui` environment) / O (file mode 0600, owner only) |
| systemd | `/etc/pigtail/pigtail.env`, mode 0600, root-owned, loaded with `EnvironmentFile=` (`infra/systemd/pigtail-scheduler.service`) | O |
| Secrets manager | Any manager that injects environment variables. pigtail reads only the environment (`Settings.from_env()`, `src/pigtail/config.py`). | O |

Rules (**O** unless marked):
1. **Generate** on the host: `openssl rand -hex 32` (64 hex characters, 256 bits). The code accepts 16 characters or more (`Pseudonymizer.__init__`), and `pigtail doctor` warns below 32 (**I**).
2. **Never** put the key in git, Postgres, the snapshot bucket, `PIGTAIL_DATA_DIR`, logs, tickets, chat or e-mail. pigtail does not write it anywhere: `doctor` prints "set (value not shown)", `privacy key-fingerprint` prints only fingerprints, and `repr(Settings)` masks every secret field (`pseudonym_key`, `database_url`, `s3_access_key`, `s3_secret_key`) as `'***'` (`SECRET_FIELDS`, `src/pigtail/config.py`; **I, CB-29**, ADR-045). The database stores only the fingerprint (§1), never the key.
3. **Least privilege.** Only processes that pseudonymise need it: capture, the scheduler, `privacy` commands and the LLM client. The UI does not use it (no reference in `src/pigtail/api/`) and under Compose does not receive it (**I, CB-29**: explicit `environment:` list, no `env_file`). If you add variables to the `ui` service, never add `PSEUDONYM_KEY`. On other deployments (systemd, a secrets manager), give the UI process its own environment without the key (**O**).
4. **Separate backup.** Keep an encrypted copy of the key **apart from the data backups**: a password manager or a sealed offline copy held by the controller, not on the backup target. Record where it is (not the value) in your deployment notes. A data backup and the key must never be in the same place, or the backup is no longer pseudonymised in any meaningful sense (GDPR Art. 4(5), "kept separately").
5. **Test the backup** at least once a year: the backup copy must give the same `kfp1_` fingerprint as the live key. Either run `pigtail privacy key-fingerprint` with the backup copy as `PSEUDONYM_KEY` in the environment (it only reads; `status` must be `ok`), or compute it offline without putting the key on a command line: `python3 -c 'import hmac,hashlib,os;print("kfp1_"+hmac.new(os.environ["PSEUDONYM_KEY"].encode(),b"pigtail-key-fingerprint-v1",hashlib.sha256).hexdigest()[:32])'` (same scheme as `Pseudonymizer.fingerprint()`). Never compare or print the key itself.
6. **Record the fingerprint** in your deployment notes: the `running_key_fingerprint` value printed by `pigtail privacy key-fingerprint` (scheme `kfp1_` + 32 hex of HMAC-SHA256(key, `pigtail-key-fingerprint-v1`), never the key). The database stores the same value (CB-25), but a copy outside the database tells you which key a backup or a rebuilt host needs. Version 0.1 of this runbook used a different value (the first 12 hex of an unkeyed SHA-256 of the key); it is not comparable with `kfp1_`, so replace it in your notes.

---

## 3. What a rotation (or an accidental change) breaks

pigtail stores **no key id** next to its pseudonyms (no `key_id` column in any migration, checked 2026-09-25). Since CB-25 (ADR-045) it stores one key fingerprint for the whole database (§1), so a **changed key is detected and refused** (fail closed) instead of silently breaking the refusal list. It does not re-key anything. The effects below happen once a new key is accepted with `--reset` (§4 step 9); that is why §4 builds the old-key mapping first and re-adds the opt-outs right after the reset.

| Affected | Effect of a new key | Severity | Mitigation today | Tooling needed |
|---|---|---|---|---|
| **Refusal list: pseudonym entries** (`privacy_suppression`, kind `pseudonym`; CB-13) | People who objected or asked for erasure are **no longer matched at ingest**: their data is collected again. The handles are not stored (ADR-030.4), so the entries cannot be recomputed from the database. | **Critical** (breaks GDPR Art. 21 and FADP Art. 30(2)(b) honouring) | Keep the old key (§4 step 2) and, before switching, map each entry to its handle **only where the handle appears in retained raw snapshots** (manual, operator-written script). Entries that cannot be mapped must be recorded and re-requested from the data subject if a contact exists (pigtail stores none). | **CB-26** re-pseudonymisation migration; **CB-27** dual-key matching |
| **Refusal list: keyed repo-name entries** (`rk_…`, ADR-042.1) | Orphaned: opted-out repos not in `repos` are collected again (HN mentions, watch list, Show HN screen). Entries by **repo id** (`<host>:<id>`) are unaffected. | High | Re-add each name with `pigtail privacy optout add --repo owner/name` after the switch (operator guide "Opt-outs"). The names are not stored; the source is the owner's original request. `pigtail privacy optout rekey` does **not** help: it converts only legacy unkeyed `rn_` entries (`rekey_unkeyed_names()`, `requests.py`). | CB-26 |
| **Pseudonymous person-level rows** (`hn_mention.author`, `upstream_items.author_pseudonym`, `repo_event_actor.actor_pseudonym`) | One person gets two pseudonyms (old and new). Access and erasure requests compute only the new pseudonym, so **rows under the old pseudonym are missed** until they expire (24 months; `repo_event_actor` 16 days by default). Star de-duplication within a case window double-counts across the switch. | High (erasure incomplete) | During the transition, answer each access or erasure request twice: once with the new key and once with the old key (§4 step 7). There is no command option for this today. Or wait for expiry. | CB-26; CB-27 |
| **Raw snapshots** | Not affected: they hold handles, not pseudonyms. Access and erasure re-parse retained snapshots with the current key (`scan_snapshots()`, `requests.py`), so they keep working. | None | — | — |
| **LLM cache** (`llm_cache`, SQLite under `PIGTAIL_DATA_DIR`) | Keys are hashes of redacted input, which contains pseudonyms, so the same input gets a **new cache key**: cache misses and repeated LLM calls (cost). Cached outputs may quote old pseudonyms that an erasure computed with the new key will not find (`find_containing`). | Medium | Delete the cache rows at the switch: `sqlite3 "$PIGTAIL_DATA_DIR/llm.sqlite3" 'DELETE FROM llm_cache; DELETE FROM llm_cache_evidence;'` (keeps the usage ledger). There is no pigtail command for this. Today only the smoke test calls the LLM, with `use_cache=False` (`cmd_llm_smoke`, `src/pigtail/cli.py`), so the cache is normally empty. | **CB-28** cache-clear command |
| **JSONL exports with `--include-person-level`** and **backups** | Contain old pseudonyms. Once the old key is destroyed they are unlinkable with the database. Restoring a pre-rotation backup keeps the live (new) fingerprint (`backup restore` checks the running key against the live database first and refuses on a mismatch), but brings back the old-key rows that §4 step 7 deleted by SQL, since that deletion writes no tombstone. | Low | Delete old person-level exports; let backups expire (35-day rotation, `pigtail backup prune`, CB-17). After restoring a pre-rotation backup, repeat §4 step 7. | — |
| **UI sessions and audit log** | Not affected (§1). | None | — | — |
| **Aggregates, project-level data, content hashes** | Not affected. | None | — | — |

---

## 4. Rotation procedure (manual, until CB-26 to CB-28 exist)

Use only after a compromise (§5) or when the lawyer or controller decides it is necessary. Plan a maintenance window. Nobody may run capture or `privacy` commands during it.

**Order matters (CB-25).** As soon as the new key is in the environment, every command that uses the key refuses (exit 2) until the fingerprint is reset. So: build the old-key mapping **under the old key** (step 5), switch the key (step 8), reset the fingerprint **with the new key** (step 9), and only then re-add the opt-outs **under the new key** (step 10). A reset before step 5 loses the old-key mapping; re-adding before step 9 is refused. Under Compose, run one-off commands as `docker compose run --rm scheduler pigtail …` (the scheduler service holds the `.env`).

1. **Record the decision.** Open a breach record ([breach.md](breach.md) §7) if the reason is a compromise; otherwise note the reason in your deployment notes. Run `pigtail privacy key-fingerprint`: `status` must be `ok`; note `running_key_fingerprint` (the old key's `kfp1_…`, §2 rule 6).
2. **Keep the old key, sealed.** Copy it to the separate key backup, marked "old, rotation of <date>". It is needed for steps 5 and 7 and must not be destroyed until step 12.
3. **Stop every writer.** `docker compose stop scheduler ui`, or `systemctl stop pigtail-scheduler` and any cron entries (`pigtail alerts check`, `pigtail health --liveness-file …`, `retention purge`, `backup create`). Check that no `pigtail` process is running.
4. **Back up** the database and `PIGTAIL_DATA_DIR` (`pigtail backup create`, still under the old key; encrypted, stored apart from both keys).
5. **Build the old-key mapping** (the critical step; no tooling, **P CB-26**). The **old** key stays in the environment; add nothing yet.
   - Export the entries: `pigtail privacy optout list` (it does not need the key).
   - For each `pseudonym` entry, find the handle only if it appears in retained raw snapshots: parse the retained snapshots of that platform with the **old** key and keep the handles whose pseudonym is on the list. This has to be a local, operator-written script today. Write the mapping (platform, handle) to one encrypted file outside any git working tree, never to logs, and destroy it after step 10.
   - Record how many entries could not be mapped. They stay on the list (harmless) but will no longer match anything. If you still have the original request (for example the e-mail), take the handle from it. If not, record the gap in the breach or change record; it is an unresolved objection (LQ-30).
   - For each `repo_name` (`rk_`) entry, collect the name from the owner's original request (names are not stored).
   - Entries of kind `repo` (by id) need nothing.
6. **Clear the LLM cache** (§3) so that no old pseudonyms stay in cached outputs.
7. **Decide about existing pseudonymous rows.** Either delete them now (they expire anyway: `repo_event_actor` after 16 days by default, the rest after 24 months), or keep them and answer every access and erasure request with both keys until they expire. With no tooling for the second option, **deleting is the safer choice** where the analysis allows it. Deleting needs SQL today; there is no command (**P CB-26**). Write a note in the change record, since the `deletion_log` has no reason code for a rotation.
8. **Switch the key.** Generate the new key (`openssl rand -hex 32`), put it in `.env` or `/etc/pigtail/pigtail.env` (mode 0600) and back it up separately (§2 rule 4). **Do not start the services yet.** From now on `pigtail doctor` reports `pseudonym_key_fingerprint` FAIL and every key-using command, and scheduler startup, refuse: this is expected until step 9.
9. **Reset the fingerprint, with the new key in the environment:** `pigtail privacy key-fingerprint --reset --confirm-rotation` (`--reset` alone is refused). Check the output: `old_fingerprint` is the value noted in step 1 and `fingerprint` is the new key's. The command writes a `runs` record (`privacy.key_fingerprint_reset`) and a `reset` event with both fingerprints to the append-only `pseudonym_key_fingerprint_log`. Put the `run_id` in the change or breach record.
10. **Re-add the opt-outs under the new key:** each mapped handle with `pigtail privacy optout add --platform <p> --handle -` (handle on stdin), and each repo name with `pigtail privacy optout add --repo owner/name`. Then destroy the mapping file from step 5.
11. **Check, then start.** `pigtail doctor --strict`: `pseudonym_key` and `pseudonym_key_fingerprint` must be OK, and `optout_name_keys` must not report unkeyed entries. `pigtail privacy key-fingerprint` must show `status: ok`. Start the services.
12. **Destroy the old key** once no request needs it any more: when the old-pseudonym rows have been deleted or have expired (step 7) and the backups taken under the old key have rotated out (35 days). Record the date.
13. **Update** the deployment notes (new `kfp1_` fingerprint, date, reset `run_id`), and, if the rotation followed a breach, close the breach record.

**Mismatch without a planned rotation** (someone edited `.env`, a secrets manager served another value, a host was rebuilt from the wrong copy): stop, find the original key in the separate backup (its `kfp1_` fingerprint must equal the stored `fingerprint` shown by `pigtail privacy key-fingerprint`), put it back and re-run `pigtail doctor`. **Do not reset.** A reset under the wrong key would make every opt-out stop matching. If the original key is lost, follow "Loss of the key" (§5).

---

## 5. Compromise response

"Compromise" means the key may have been seen by someone not authorised: found in git, a log, a ticket or a backup stored with the data; a leaked `.env`; a host or secrets-manager intrusion; a departing person who had access.

1. **Treat it as a possible personal data breach** and open the breach runbook ([breach.md](breach.md)). Start the 72-hour clock assessment there (GDPR Art. 33(1)).
2. **Assess what else leaked.** The key alone reveals nothing: the pseudonyms live only in the private database and backups. The risk is high only if the attacker also has (or may get) the database, a backup or a person-level export, because handles are public and a dictionary of candidate handles reverses the pseudonyms. EDPB Guidelines 9/2022 ¶77 treat keyed-hash data as protected only if the key "was not compromised in any breach". A key leak together with data is therefore **not** covered by the Art. 34(3)(a) "unintelligible" exception.
3. **Contain.** Remove the key from wherever it leaked. For git, follow [breach.md](breach.md) §4.2 (the repo is public). Rotate the key (§4, including the fingerprint reset in step 9), keeping the old key sealed only as long as §4 needs it.
4. **Rotate the other secrets** that were stored with it (the same `.env` usually holds `DATABASE_URL`, `S3_*`, `S3_SSE_KEK`, `GITHUB_TOKEN`, `SMTP_URL`, `PIGTAIL_OPERATOR_PASSWORD_HASH`): see [breach.md](breach.md) §4.3.
5. **Document** it in the breach register, even if no notification is needed (GDPR Art. 33(5); FADP ordinance Art. 15(4)).

**Loss of the key** (no backup): new pseudonyms cannot be linked to old ones; the refusal list stops matching (§3), and pigtail refuses to run under any new key until the fingerprint is reset (CB-25). This is an availability breach of the refusal list. Follow §4 steps 5–13 as far as possible (the old-key mapping in step 5 is impossible; only names and handles from original requests can be re-added in step 10) and record it in the breach register.

---

## 6. Tooling: what exists and what is missing

| Need | Exists? | Backlog |
|---|---|---|
| Minimum length and presence check; `doctor` check | Yes (`Pseudonymizer.__init__`, `doctor.py`) | — |
| Key never logged or printed | Yes (`doctor` hides the value; `repr(Settings)` masks it, CB-29; CB-18 scrubber on logs) | — |
| **Detect an accidental key change**: key fingerprint (`kfp1_`, HMAC of a fixed label, never the key) stored at first use; every key-using path, backup restore and scheduler startup refuse on a mismatch (exit 2); `doctor` FAILs; `pigtail privacy key-fingerprint` shows status and history; `--reset --confirm-rotation` accepts a new key after §4 | **Yes** (`src/pigtail/privacy/key_fingerprint.py`, migration 0012; ADR-045) | CB-25 done |
| **Re-pseudonymisation migration** `pigtail privacy rekey-pseudonyms --old-key-env OLD_PSEUDONYM_KEY` (dry run first): maps refusal-list pseudonyms and `rk_` name keys through retained raw snapshots and local data, rewrites or deletes pseudonymous rows, clears the LLM cache, reports what could not be mapped, writes a `deletion_log`/change record | **No** | **CB-26** |
| **Dual-key matching** during a transition: ingest and access/erasure also compute the old-key pseudonym while an old key is configured, so objections keep working and old rows are found | **No** | **CB-27** |
| **LLM cache clear** command (all rows, keeps the usage ledger) | **No** (SQL only) | **CB-28** |
| Secret hygiene: the UI does not receive `PSEUDONYM_KEY` (per-service environment), and secrets are masked in `repr(Settings)` | **Yes** (`ui` gets an explicit `environment:` list without the key, tokens, SMTP or API keys, `docker-compose.yml`; `SECRET_FIELDS` masked as `'***'`, `src/pigtail/config.py`; ADR-045) | CB-29 done |
| Key escrow / backup | Operator procedure (§2) | O |

Until CB-26 and CB-27 exist, the key must not be rotated on a schedule (§0), and [retention-policy.md](../retention-policy.md) §3.5 points here. CB-25 makes an unplanned change safe (pigtail stops instead of collecting opted-out people again); it does not make a rotation cheap.

## Open questions for the lawyer
- **LQ-30** ([legal-review-questions.md](../legal-review-questions.md)): if a rotation leaves refusal-list entries that can no longer be matched (§3), what must the controller do to keep honouring those objections, given that pigtail stores neither handles nor contact details of the requesters?

## Changelog
- 2026-09-25: v0.1 created (CB-09). Every statement about pigtail's behaviour was checked against `main`; CB-25 to CB-29 proposed.
- 2026-09-25 — ADR-045 (CB-25/29/32/33/34): v0.2. §0 and §3: a key change is now detected and refused (fail closed); a mismatch without a planned rotation means restoring the original key, never a reset. §1: fingerprint row and doctor check added. §2: the `ui` service no longer receives `PSEUDONYM_KEY` (explicit environment list); `repr(Settings)` masks secrets; the deployment notes record `running_key_fingerprint` (`kfp1_` scheme, replaces the v0.1 SHA-256 prefix). §3: backup restore checks the key before anything is replaced. §4 reordered: old-key mapping, key switch, `pigtail privacy key-fingerprint --reset --confirm-rotation` with the new key, then opt-outs re-added under the new key; Compose service name corrected to `scheduler`. §6: CB-25 and CB-29 done.
