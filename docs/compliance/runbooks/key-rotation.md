# Runbook: pseudonym key (`PSEUDONYM_KEY`) management and rotation

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.3 · 2026-09-25 · Control **CB-09**; key-change detection **CB-25** and secret hygiene **CB-29** (ADR-045); rotation by re-derivation **CB-26**, LLM cache clear **CB-28** and restore guard **CB-35** (ADR-046) ([dpia.md](../dpia.md) §9)
**Applies to:** every pigtail deployment. The operator (the controller) runs it.
**Access date:** every URL was accessed on **2026-09-25**.
**Related:** [retention-policy.md](../retention-policy.md) §3 · [breach.md](breach.md) · [controller-duties.md](../controller-duties.md) · `docs/guides/operator.md` "Privacy operations" ("Rotating the key: `privacy rekey`")

**Status legend:** **I** = implemented in code on `main` (file cited). **P** = planned (backlog id). **O** = operator procedure. Every statement about pigtail's behaviour below was checked against the code on 2026-09-25.

**Legal anchors:**
- GDPR Art. 4(5): pseudonymisation requires that the "additional information is kept separately and is subject to technical and organisational measures". Art. 32(1)(a) lists "the pseudonymisation and encryption of personal data" among security measures. Text: [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj), read via the Publications Office copy (http://publications.europa.eu/resource/celex/32016R0679), because EUR-Lex returned a bot challenge.
- FADP Art. 8(1): "a level of data security appropriate to the risk". Text: [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en), English translation, consolidated version of 1 September 2023.
- EDPB Guidelines 9/2022 on personal data breach notification, version 2.0 (adopted 28 March 2023), ¶77: a keyed hash is treated as unintelligible only if "the key used to hash the data was not compromised in any breach" ([PDF](https://www.edpb.europa.eu/system/files/2023-04/edpb_guidelines_202209_personal_data_breach_notification_v2.0_en.pdf)).
- EDPB Guidelines 01/2025 on Pseudonymisation (adopted 16 January 2025, public consultation), [PDF](https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf). Already cited in the DPIA; not re-read for this runbook.

---

## 0. Summary for the operator

- `PSEUDONYM_KEY` turns every handle into a pseudonym (`p_` + 16 hex of HMAC-SHA256) and every repo-name opt-out into a keyed hash (`rk_…`). Whoever holds the key and the database can re-identify any pseudonym by hashing candidate handles, because handles are public.
- **A new key breaks the refusal list unless everything is re-derived.** Opt-outs are keyed hashes, so under another key people and repos that opted out are no longer recognised at ingest, and collection about them would resume.
- **pigtail detects a key change and fails closed** (CB-25, ADR-045; **I**). The database records a fingerprint of the key on first use (`kfp1_…`, §1). Every command that pseudonymises or matches opt-outs, backup restore and scheduler startup **refuse** (exit 2) when the running key does not match, and `pigtail doctor` reports `pseudonym_key_fingerprint` as FAIL. `pigtail privacy key-fingerprint` shows the status (`ok`, `mismatch` or `unset`; exit 1 on `mismatch`).
- **A mismatch you did not plan is an incident, not a rotation.** Restore the original key from its separate backup (§2 rule 4). **Do not reset the fingerprint and do not run `rekey`.**
- **Rotate with `pigtail privacy rekey`** (CB-26, ADR-046.1; **I**; §4.1). It re-derives every stored pseudonym and opt-out under the new key in **one transaction**, clears the LLM cache and switches the fingerprint as its last write. It refuses (and changes nothing) while other sessions are connected to the database, and whenever an opt-out cannot be mapped; no flag overrides that. The manual procedure (§4.2) is only a fallback for when `rekey` cannot run, chiefly when the old key is lost.
- **Scheduled rotation is allowed again** (ADR-046.3, reversing ADR-043's "only after a compromise"). Suggested cadence: every **24 months**, plus after a compromise (§5) and when a person who had access to the key leaves (§4.3; [retention-policy.md](../retention-policy.md) §3.5).
- **After a rotation, older backups cannot be restored over the database.** `pigtail backup restore` refuses a backup taken before the last `rekey` (CB-26) or the last bare `key-fingerprint --reset` (CB-35) (§4.4). Take a fresh backup right after every rotation.

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
| Key fingerprint (CB-25, ADR-045): `"kfp1_" + HMAC-SHA256(key, "pigtail-key-fingerprint-v1")[:32]` (128 bits). It identifies the key without revealing it and cannot compute pseudonyms. | `Pseudonymizer.fingerprint()`, `src/pigtail/pseudonymize.py`; `verify()`, `reset()`, `src/pigtail/privacy/key_fingerprint.py` | One-row table `pseudonym_key_fingerprint`, recorded on first use; append-only history `pseudonym_key_fingerprint_log` (events `recorded`, `reset` (migration 0012) and `rekey` (migration 0013)). Never exported (`src/pigtail/export/jsonl.py`). Checked by the opt-out list loader (every collector), the connector base, the `privacy` commands and data-subject request functions, `backup restore` (before anything is replaced; the live fingerprint is kept; backups taken before the last `rekey` or `reset` are refused, §4.4) and `pigtail scheduler run` at startup. `retention purge` is not gated (deleting is always safe). | I |

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

pigtail stores **no key id** next to its pseudonyms (no `key_id` column in any migration, checked 2026-09-25). It stores one key fingerprint for the whole database (CB-25, §1), so a **changed key is detected and refused** (fail closed) instead of silently breaking the refusal list. The effects below happen only once a new key is accepted: by `pigtail privacy rekey`, which handles each of them in the same transaction (§4.1), or by a bare `key-fingerprint --reset` in the manual fallback (§4.2), which handles none of them by itself.

| Affected | Effect of a new key | Severity | With `privacy rekey` (§4.1, **I**) | Manual fallback (§4.2) |
|---|---|---|---|---|
| **Refusal list: pseudonym entries** (`privacy_suppression`, kind `pseudonym`; CB-13) | People who objected or asked for erasure are **no longer matched at ingest**: their data is collected again. The handles are not stored (ADR-030.4), so the entries cannot be recomputed from the database alone. | **Critical** (breaks GDPR Art. 21 and FADP Art. 30(2)(b) honouring) | Each entry is mapped old → new through a handle from the operator's handles file or from retained raw snapshots, keeping platform, reason, request id and date. **If any entry cannot be mapped, `rekey` refuses and changes nothing; there is no override** (ADR-046.1). The refusal lists each unmapped entry's kind, platform, request id and date, never the pseudonym. | Re-add each handle from the original requests with `privacy optout add` after the reset. Entries with no recoverable handle are an unresolved objection (LQ-30). |
| **Refusal list: keyed repo-name entries** (`rk_…`, ADR-042.1) | Orphaned: opted-out repos not in `repos` are collected again (HN mentions, watch list, Show HN screen). Entries by **repo id** (`<host>:<id>`) and legacy unkeyed `rn_` entries do not use the key and are unaffected. | High | Mapped through `repo <owner/name>` lines in the handles file or names pigtail still holds (HN rows, story links, Show HN screen, watch list, `repos`). Unmappable entries make it refuse, as above. | Re-add each name with `pigtail privacy optout add --repo owner/name` after the reset. The source is the owner's original request. |
| **Pseudonymous person-level rows** (`hn_mention.author`, `upstream_items.author_pseudonym`, `repo_event_actor.actor_pseudonym`; `PERSON_TABLES`) | One person gets two pseudonyms (old and new). Access and erasure compute only the new pseudonym, so **rows under the old pseudonym are missed** until they expire (24 months; `repo_event_actor` 16 days by default). | High (erasure incomplete) | Rewritten to the new pseudonym where a handle is found (handles file or retained snapshots). Rows that cannot be mapped make it refuse unless `--drop-unmapped` (those rows are deleted; for `upstream_items` only the author is cleared, so deletion sync keeps tracking the item) or `--purge-person-level` (every person-level row, mapped or not). Deletions write `deletion_log` tombstones with reason `key_rotation` (migration 0013). A final check refuses if any old pseudonym is left. | Delete the rows by SQL (no command; no tombstone), or answer every access and erasure request with both keys until the rows expire. |
| **Raw snapshots** | Not affected: they hold handles, not pseudonyms. Access and erasure re-parse retained snapshots with the current key (`scan_snapshots()`, `requests.py`). | None | Used read-only as a mapping source (`--no-snapshot-scan` skips them). | — |
| **LLM cache** (`llm_cache`, SQLite under `PIGTAIL_DATA_DIR`) | Keys are hashes of redacted input, which contains pseudonyms, so the same input gets a new cache key (cache misses, repeated cost). Cached outputs may quote old pseudonyms that an erasure under the new key would not find. | Medium | Cleared (all cache rows and evidence links; the usage ledger is kept), logged as a `cache_purged` tombstone. `--dry-run` only counts. The cache is SQLite, outside the Postgres transaction: it is cleared just before the fingerprint switch, so if the transaction then fails the cache is simply empty. | `pigtail llm cache clear --all --yes` (CB-28, **I**). |
| **JSONL exports with `--include-person-level`** | Contain old pseudonyms; unlinkable once the old key is destroyed. | Low | Not reached (outside the database): delete them. | Delete them. |
| **Backups** taken before the rotation | Contain the old-key pseudonyms and refusal list. Restoring one would bring back entries that no longer match under the live key. | High if restored | `pigtail backup restore` refuses a backup taken before the last `rekey` (§4.4). They expire with `backup prune` (35 days). | `backup restore` refuses a backup taken before the last `reset` (CB-35, §4.4). |
| **Request log, deletion log, UI sessions and audit log, runs** | Hold no pseudonym (ADR-030.4, ADR-034.2; §1). | None | — | — |
| **Aggregates, project-level data, content hashes** | Not affected. | None | — | — |

No dual-key matching window is needed (CB-27 not needed, ADR-046.2): `rekey` switches everything atomically, and every process started afterwards is checked against the new fingerprint (CB-25), so no process can run with the old key after the commit.

---

## 4. Rotation procedure

Use it for a scheduled rotation (§4.3), after a compromise (§5), or when the lawyer or controller decides it is necessary. Plan a maintenance window. Nobody may run capture, `privacy` commands or the UI during it. Under Docker Compose, run one-off commands as `docker compose run --rm scheduler pigtail …`: that container has the `.env`, the snapshot-store settings and the `app-data` volume (`PIGTAIL_DATA_DIR=/data`, where the LLM cache lives). On any deployment, run `rekey` with the **same** `PIGTAIL_DATA_DIR` as the scheduler, or it clears the wrong LLM cache.

### 4.1 Primary procedure: `pigtail privacy rekey` (CB-26, ADR-046.1; **I**, `src/pigtail/privacy/rekey.py`)

```
pigtail privacy rekey --old-key-env OLD_PSEUDONYM_KEY \
  [--handles-file PATH] [--drop-unmapped | --purge-person-level] [--no-snapshot-scan] \
  (--dry-run | --confirm-rotation)
```

What the command does (verified in code on 2026-09-25):
- The **new** key is `PSEUDONYM_KEY`. The **old** key is read from the environment variable *named* by `--old-key-env` (never an argument or a file; the name must be an upper-case variable name other than `PSEUDONYM_KEY`). A key pasted in place of the name is refused without being echoed. Old and new key must differ, and the old key must match the stored fingerprint (else `KeyFingerprintMismatch`, exit 2). A database already on the new key is refused.
- `--handles-file` is the operator's old-key mapping: one entry per line, `github <handle>`, `hn <handle>`, `bluesky <handle>`, `v2ex <handle>` or `repo <owner/name>`, `#` for comments. It is **refused inside any git working tree** and **unless it is readable by its owner only** (mode 0600, `chmod 600`). Errors cite line numbers, never content. It only maps existing entries; nothing in it is added to the refusal list.
- Neither `--dry-run` nor `--confirm-rotation`: refused (usage error). `--dry-run` runs everything and rolls back, leaving the LLM cache alone. `--confirm-rotation` commits.
- **One transaction.** It refuses to start while **any other client session is connected to the database** (`pg_stat_activity`), then locks the refusal list, the fingerprint table and every `PERSON_TABLES` table against writes (`SHARE ROW EXCLUSIVE`, 10 s lock timeout), builds the mapping, refuses before any write if an opt-out (or, without `--drop-unmapped`/`--purge-person-level`, a person-level row) cannot be mapped, rewrites or deletes rows, clears the LLM cache and **switches the fingerprint as the last write** (event `rekey` in `pseudonym_key_fingerprint_log`, migration 0013). Any refusal or error rolls everything back.
- Output: JSON counts only (opt-outs mapped and unmapped, rows mapped or deleted per table, snapshots scanned, cache rows deleted) with a `privacy.rekey` run record. Neither contains a key, a handle, a repo name or the file's path. Exit 2 means it refused and nothing changed.

Steps:
1. **Record the decision** (scheduled rotation, compromise, departure). For a compromise, open a breach record ([breach.md](breach.md) §7). Run `pigtail privacy key-fingerprint`: `status` must be `ok`; note `running_key_fingerprint` (the old key's `kfp1_…`, §2 rule 6).
2. **Keep the old key, sealed.** Copy it to the separate key backup, marked "old, rotation of <date>". It is needed for the rotation and must not be destroyed until step 12.
3. **Prepare the handles file** from the original opt-out and erasure requests (the refusal list itself stores no handle). `pigtail privacy optout list` (no key needed) shows the entries; the dry run in step 7 lists every entry it cannot map with its platform, request id and date. Keep the file **outside any git working tree**, `chmod 600`, on encrypted storage, and never in logs, tickets or chat. Under Compose, bind-mount its directory read-only into the one-off container and make the file owned by the container user (uid 10001) while keeping mode 0600.
4. **Stop every writer and close every session.** `docker compose stop scheduler ui` (or `systemctl stop pigtail-scheduler`, the UI service and cron entries such as `pigtail alerts check`, `health --liveness-file`, `retention purge`, `backup create`). Close any `psql` sessions. `rekey` refuses while other sessions are connected, because a collector that loaded the refusal list under the old key could otherwise write old pseudonyms after the commit.
5. **Back up** the database (`pigtail backup create`, still under the old key; encrypted, stored apart from both keys). This is a precaution only: once the rotation commits, `backup restore` refuses this backup over the rotated database (§4.4).
6. **Set the keys.** Generate the new key (`openssl rand -hex 32`), put it in `.env` or `/etc/pigtail/pigtail.env` (mode 0600) as `PSEUDONYM_KEY`, and back it up separately (§2 rule 4). Put the old key only into the current shell, without echo or history: `read -rs OLD_PSEUDONYM_KEY && export OLD_PSEUDONYM_KEY`. From now on every other key-using command refuses (CB-25): expected until step 8.
7. **Dry run** until it reports no refusal:
   ```bash
   pigtail privacy rekey --old-key-env OLD_PSEUDONYM_KEY --handles-file /secure/rotation/handles.txt --dry-run
   # Compose:
   docker compose run --rm -e OLD_PSEUDONYM_KEY -v /secure/rotation:/rotation:ro scheduler \
     pigtail privacy rekey --old-key-env OLD_PSEUDONYM_KEY --handles-file /rotation/handles.txt --dry-run
   ```
   - **Unmapped opt-outs** (`counts.optouts_unmapped` > 0, listed in `unmapped_optouts`): find the original request by `request_id` and date, add its handle or repo name to the file, and re-run. **An opt-out that cannot be mapped always refuses and must never be dropped.** If a handle cannot be recovered, do not rotate: keep the current key and record why (a scheduled rotation can wait; after a compromise see LQ-30 and §5).
   - **Unmapped person-level rows** (`counts.person_pseudonyms_unmapped` > 0): choose `--drop-unmapped` (delete only those rows) or `--purge-person-level` (delete every person-level row; they expire anyway, `repo_event_actor` after 16 days by default, the rest after 24 months). Deleting is the safer side and is always allowed.
   - `--no-snapshot-scan` skips re-parsing retained snapshots (faster with many GH Archive dumps) but then more rows are unmapped.
8. **Rotate:** the same command with `--confirm-rotation` instead of `--dry-run` (plus the flag chosen in step 7). Check the output: `committed: true`, `old_fingerprint` is the value from step 1, `new_fingerprint` the new key's. Put the `run_id` in the change or breach record. `pigtail privacy key-fingerprint` now shows `status: ok` and a `rekey` event.
9. **Clean up at once.** `unset OLD_PSEUDONYM_KEY`; destroy the handles file (and any copy); delete JSONL exports made with `--include-person-level` before the rotation.
10. **Back up again** (`pigtail backup create`, under the new key): it is now the oldest backup that can be restored (§4.4).
11. **Check, then start.** `pigtail doctor --strict`: `pseudonym_key` and `pseudonym_key_fingerprint` must be OK. Start the services.
12. **Destroy the old key** once the backups taken under it have been pruned (35 days, `pigtail backup prune`). Record the date. (After `rekey`, no old-key pseudonym is left in the database or the LLM cache, so no request needs the old key.)
13. **Update** the deployment notes: new `kfp1_` fingerprint, date, `run_id`; close the breach record if there was one.

### 4.2 Fallback: manual procedure with a bare fingerprint reset (only when `rekey` cannot run)

Use this **only** when `pigtail privacy rekey` cannot be used, chiefly when the **old key is lost** (§5, "Loss of the key"): `rekey` needs the old key to verify the stored fingerprint and to map anything. A bare reset re-derives nothing (§3, last column).

**Order matters (CB-25).** As soon as the new key is in the environment, every key-using command refuses (exit 2) until the fingerprint is reset. So: collect handles and names first, switch the key, reset the fingerprint **with the new key**, then re-add the opt-outs **under the new key**.

1. Record the decision and open a breach record if applicable (as §4.1 step 1).
2. **Stop every writer** (as §4.1 step 4) and **back up** (as §4.1 step 5).
3. **Collect the old-key mapping**: the handles and repo names of every opt-out from the original requests (`pigtail privacy optout list` shows the entries; entries by repo id need nothing). If the old key is still available, prefer `rekey` (§4.1). Record every entry whose handle cannot be recovered: it stays on the list but will no longer match (unresolved objection, LQ-30).
4. **Clear the LLM cache:** `pigtail llm cache clear --all --yes` (CB-28), so no old pseudonyms stay in cached outputs.
5. **Existing pseudonymous rows**: delete them now by SQL (no command; no `deletion_log` tombstone, so note it in the change record), or wait for them to expire. Access and erasure requests will miss them meanwhile. Deleting is the safer choice.
6. **Switch the key** (as §4.1 step 6, without the old key). Do not start the services.
7. **Reset the fingerprint, with the new key in the environment:** `pigtail privacy key-fingerprint --reset --confirm-rotation` (`--reset` alone is refused). It writes a `runs` record (`privacy.key_fingerprint_reset`) and a `reset` event with both fingerprints to the append-only `pseudonym_key_fingerprint_log`. Put the `run_id` in the record.
8. **Re-add the opt-outs under the new key:** each handle with `pigtail privacy optout add --platform <p> --handle -` (handle on stdin), each repo name with `pigtail privacy optout add --repo owner/name`. Then destroy the mapping.
9. **Back up again** (as §4.1 step 10): `backup restore` now refuses backups taken before the reset (CB-35, §4.4).
10. **Check, then start** (as §4.1 step 11), delete old person-level exports, and update the deployment notes.

**Mismatch without a planned rotation** (someone edited `.env`, a secrets manager served another value, a host was rebuilt from the wrong copy): stop, find the original key in the separate backup (its `kfp1_` fingerprint must equal the stored `fingerprint` shown by `pigtail privacy key-fingerprint`), put it back and re-run `pigtail doctor`. **Do not reset and do not run `rekey`.** A reset under the wrong key would make every opt-out stop matching. If the original key is lost, follow "Loss of the key" (§5).

### 4.3 Scheduled rotation (ADR-046.3)

Scheduled rotation is allowed again now that `rekey` exists (ADR-046.3 reverses ADR-043's "only after a compromise"). Suggested cadence, as in [retention-policy.md](../retention-policy.md) §3.5:
- **every 24 months**, aligned with the 24-month retention of person-level data, so that no retained pseudonymous record outlives two keys;
- **and** after a compromise (§5) or when a person who had access to the key leaves or changes role.

A scheduled rotation uses §4.1 only, never the fallback. It depends on the handles of every opt-out being recoverable from the original requests: keep those requests (outside pigtail, with the controller's request records) for as long as the opt-out stands. Whether keeping them for this purpose is proportionate is open for the lawyer (LQ-30 question 2). If the dry run shows an opt-out that cannot be mapped, **postpone the rotation** and keep the current key rather than lose the objection.

### 4.4 Restoring backups after a rotation (CB-26, CB-35; **I**)

`pigtail backup restore` reads the live database's fingerprint history before anything is replaced and **refuses a backup taken before the most recent `rekey` or `reset` event** (`check_rotation()`, `src/pigtail/privacy/backup.py`; ADR-046.4). Such a backup's pseudonyms and refusal list are keyed with the old key and would not match under the live key. A backup whose creation time is unknown is refused too. The first-use `recorded` event does not count. The message names the event (`pigtail privacy rekey` or `pigtail privacy key-fingerprint --reset`) and its time; restore a backup taken after it. This is why §4.1 step 10 and §4.2 step 9 take a fresh backup right after the rotation. Separately, the running key must still match the live fingerprint (CB-25, exit 2 on a mismatch) and the live fingerprint is kept after the restore.

---

## 5. Compromise response

"Compromise" means the key may have been seen by someone not authorised: found in git, a log, a ticket or a backup stored with the data; a leaked `.env`; a host or secrets-manager intrusion; a departing person who had access.

1. **Treat it as a possible personal data breach** and open the breach runbook ([breach.md](breach.md)). Start the 72-hour clock assessment there (GDPR Art. 33(1)).
2. **Assess what else leaked.** The key alone reveals nothing: the pseudonyms live only in the private database and backups. The risk is high only if the attacker also has (or may get) the database, a backup or a person-level export, because handles are public and a dictionary of candidate handles reverses the pseudonyms. EDPB Guidelines 9/2022 ¶77 treat keyed-hash data as protected only if the key "was not compromised in any breach". A key leak together with data is therefore **not** covered by the Art. 34(3)(a) "unintelligible" exception.
3. **Contain.** Remove the key from wherever it leaked. For git, follow [breach.md](breach.md) §4.2 (the repo is public). Rotate the key with `pigtail privacy rekey` (§4.1), keeping the old key sealed only as long as §4.1 step 12 needs it. Rotate even if the database may have leaked: after the rotation the leaked key no longer matches new pseudonyms, and old-key pseudonyms are gone from the database and the LLM cache.
4. **Rotate the other secrets** that were stored with it (the same `.env` usually holds `DATABASE_URL`, `S3_*`, `S3_SSE_KEK`, `GITHUB_TOKEN`, `SMTP_URL`, `PIGTAIL_OPERATOR_PASSWORD_HASH`): see [breach.md](breach.md) §4.3.
5. **Document** it in the breach register, even if no notification is needed (GDPR Art. 33(5); FADP ordinance Art. 15(4)).

**Loss of the key** (no backup): new pseudonyms cannot be linked to old ones; the refusal list stops matching (§3), and pigtail refuses to run under any new key until the fingerprint is reset (CB-25). This is an availability breach of the refusal list. `rekey` cannot run without the old key, so follow the manual fallback (§4.2): only handles and names from the original requests can be re-added (step 8), and existing pseudonymous rows can only be deleted or left to expire (step 5). Record it in the breach register. After the reset, backups taken under the lost key cannot be restored (CB-35, §4.4).

---

## 6. Tooling: what exists and what is missing

| Need | Exists? | Backlog |
|---|---|---|
| Minimum length and presence check; `doctor` check | Yes (`Pseudonymizer.__init__`, `doctor.py`) | — |
| Key never logged or printed | Yes (`doctor` hides the value; `repr(Settings)` masks it, CB-29; CB-18 scrubber on logs; `rekey` reads the old key only from a named variable and never echoes it) | — |
| **Detect an accidental key change**: key fingerprint (`kfp1_`) stored at first use; every key-using path, backup restore and scheduler startup refuse on a mismatch (exit 2); `doctor` FAILs; `pigtail privacy key-fingerprint` shows status and history; `--reset --confirm-rotation` accepts a new key (fallback, §4.2) | **Yes** (`src/pigtail/privacy/key_fingerprint.py`, migration 0012; ADR-045) | CB-25 done |
| **Re-pseudonymisation**: `pigtail privacy rekey --old-key-env … [--handles-file …] [--drop-unmapped \| --purge-person-level] [--no-snapshot-scan] (--dry-run \| --confirm-rotation)` — one transaction, refuses while other sessions are connected, refuses on any unmappable opt-out, clears the LLM cache, switches the fingerprint last (§4.1) | **Yes** (`src/pigtail/privacy/rekey.py`, migration 0013; `tests/integration/test_rekey_cb26.py`; ADR-046.1) | CB-26 done |
| **Dual-key matching** during a transition | **Not needed**: the rotation is atomic and CB-25 refuses any process with the wrong key, so there is never a mixed-key period (ADR-046.2) | CB-27 closed |
| **LLM cache clear** command (all rows or older than N days, keeps the usage ledger) | **Yes** (`pigtail llm cache clear`, `src/pigtail/llm/store.py`; ADR-046.7) | CB-28 done |
| **Restore refuses pre-rotation backups** (last `rekey` or bare `reset`) | **Yes** (`check_rotation()`, `src/pigtail/privacy/backup.py`; ADR-046.4) | CB-26, CB-35 done |
| Secret hygiene: the UI does not receive `PSEUDONYM_KEY` (per-service environment), and secrets are masked in `repr(Settings)` | **Yes** (`ui` gets an explicit `environment:` list without the key, tokens, SMTP or API keys, `docker-compose.yml`; `SECRET_FIELDS` masked as `'***'`, `src/pigtail/config.py`; ADR-045) | CB-29 done |
| Key escrow / backup | Operator procedure (§2) | O |
| Keeping the original opt-out requests (handles) so every opt-out can be mapped at the next rotation | Operator procedure (§4.3); pigtail stores no handles by design (ADR-030.4) | O |

## Open questions for the lawyer
- **LQ-30** ([legal-review-questions.md](../legal-review-questions.md)): if a rotation leaves refusal-list entries that can no longer be matched (§3), what must the controller do to keep honouring those objections, given that pigtail stores neither handles nor contact details of the requesters? Since ADR-046 this arises only in the manual fallback (§4.2, old key lost): `privacy rekey` refuses to rotate while any opt-out is unmappable.

## Changelog
- 2026-09-25: v0.1 created (CB-09). Every statement about pigtail's behaviour was checked against `main`; CB-25 to CB-29 proposed.
- 2026-09-25 — ADR-045 (CB-25/29/32/33/34): v0.2. §0 and §3: a key change is now detected and refused (fail closed); a mismatch without a planned rotation means restoring the original key, never a reset. §1: fingerprint row and doctor check added. §2: the `ui` service no longer receives `PSEUDONYM_KEY` (explicit environment list); `repr(Settings)` masks secrets; the deployment notes record `running_key_fingerprint` (`kfp1_` scheme, replaces the v0.1 SHA-256 prefix). §3: backup restore checks the key before anything is replaced. §4 reordered: old-key mapping, key switch, `pigtail privacy key-fingerprint --reset --confirm-rotation` with the new key, then opt-outs re-added under the new key; Compose service name corrected to `scheduler`. §6: CB-25 and CB-29 done.
- 2026-09-25 — ADR-046 (CB-26/27/28/35): v0.3. §0: `pigtail privacy rekey` is the rotation procedure; scheduled rotation allowed again (ADR-046.3), every 24 months plus after a compromise or a departure; restore refuses backups taken before the last `rekey` or `reset`. §1: `rekey` event (migration 0013). §3: per-effect handling by `rekey` versus the manual fallback; CB-27 not needed (ADR-046.2). §4 restructured: §4.1 `rekey` procedure (flags and behaviour checked in code: atomic, refuses while other sessions are connected, unmappable opt-outs always refuse, handles file outside git with mode 0600, LLM cache cleared, fingerprint switched last); §4.2 the manual procedure kept only as a fallback (old key lost), with `pigtail llm cache clear` (CB-28); §4.3 scheduled rotation; §4.4 restore guard (CB-26, CB-35). §5: compromise rotation uses `rekey`; key loss uses the fallback. §6: CB-26, CB-28, CB-35 done; CB-27 closed as not needed. LQ-30 scope narrowed.
