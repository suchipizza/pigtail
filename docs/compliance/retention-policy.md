# Retention and deletion policy

**Status:** draft for legal review (gate H2). This is not legal advice.
**Version:** 0.1 · 2026-09-25 · Task M3-T2
**Applies to:** every pigtail deployment. The operator is responsible for enforcing it. Code support is tracked as CB-xx items in [dpia.md](dpia.md) §9.
**Legal anchors:**
- GDPR Art. 5(1)(e) storage limitation and Art. 17 erasure ([EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj));
- FADP Art. 6(4): data "shall be destroyed or anonymised as soon as they are no longer required" ([fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en));
- the platform terms in [terms-memos.md](terms-memos.md).

All URLs were accessed on 2026-09-25.

**Status legend:** **I** = implemented in code on `main` (file cited). **P** = planned (backlog id). **O** = operator procedure.

---

## 1. Retention classes

The class is recorded on every evidence record: `evidence.retention_class` in `src/pigtail/capture/models.py` and `migrations/0001_capture_v0.sql` (**I**).

| Class | What it covers | Retention | At the end of the period | Status |
|---|---|---|---|---|
| `person_level_24m` | Raw snapshots and parsed records that contain or derive from data about identifiable people: GH Archive hourly dumps, GitHub search result pages (their items embed owner objects; ADR-037.8; raw bytes dropped right after parsing, CB-24), posts, comments, pseudonymised actor rows, spread-graph nodes and edges, account reach | **24 months from `fetched_at`** (from the capture time, not the content's date) | **Aggregate or delete.** Raw bytes are deleted. Pseudonymised rows are deleted or rolled up into counts with no pseudonym. The evidence record keeps `content_hash`, `url`, `source`, `fetched_at`, `terms_basis` and the coded facts that carry no person identifier, and `deletion_state` is set to `raw_dropped`. | Field: I. Job: **I (CB-01)**: `pigtail retention purge` (`src/pigtail/privacy/retention.py`), run daily by the operator (**O**). Periods above 730 days are rejected at startup (`src/pigtail/config.py`). Expired pseudonymised rows are deleted; roll-up into counts is not built. |
| `project_level` | Data about repos and packages: stars, downloads, releases, dependents, pricing pages, Wayback captures of project pages | **No time limit** (PRD §10) | Kept. **Exception:** `owner/repo` names of repos owned by personal accounts are personal data. They are kept internally but never published without consent or the public-figure rule (CB-20, LQ-7). | I (policy) |
| `derived_aggregate` | Counts and statistics with no person identifier (e.g. `repo_hourly_activity`) | No time limit | Kept. Must stay non-identifying (cell size rule for public outputs, CB-14). | I (`migrations/0002_repo_hourly_activity.sql`) |
| `person_level_30d` | Per-repo GitHub event actor rows (`repo_event_actor`: pseudonymised `WatchEvent` and `ForkEvent` actors, repo, time; bots stored without an actor) and the raw per-repo events snapshots (TM-33, DPIA D12, ADR-036) | **Raw events: dropped right after parsing** (hash and URL kept, `raw_dropped`, tombstone; CB-23); a raw copy that survives parsing is dropped **16 days by default (ceiling 30; ADR-038) from `fetched_at`**. **Actor rows: 16 days by default (ceiling 30; ADR-038) from the event's `created_at`**. Both follow `GITHUB_EVENTS_RETENTION_DAYS` (default 16, maximum 30). The class name `person_level_30d` names the 30-day ceiling; the operative default is 16 days, the time needed to de-duplicate stars within a case window (48-h detection window plus the 14-day case cooldown), since the lockstep rule is not applied to this source (ADR-037.2). | Delete the actor rows; only aggregates without pseudonyms remain (`repo_event_daily_agg`: per repo and day, distinct non-bot stars and forks, bot stars and forks; per case: `bot_filter` counts, `coverage_ratio`, `confirmed`). No lockstep flag is derived from these rows (ADR-037.2). Periods above 30 days rejected at startup; table registered in `PERSON_TABLES`, so erasure reaches it. | **I (CB-22)**, M1-T24: class in `migrations/0007_github_detection.sql`; purge in `src/pigtail/privacy/retention.py` (`github_events_days`) and `src/pigtail/privacy/deletion.py` (`PersonTable("repo_event_actor", …, retention_days=30, retention_class="person_level_30d")`, where the purge applies the shorter of that ceiling and `github_events_days`); default 16 and cap 30 in `src/pigtail/config.py` (`GITHUB_EVENTS_DEFAULT_DAYS`, `GITHUB_EVENTS_MAX_DAYS`); drop at parse in `src/pigtail/capture/repo_events.py`; daily `retention_purge` job in `infra/schedule.toml`. Tested: `test_m1_t24_cb22_repo_event_rows_and_raw_purged_after_30_days`, `test_m1_t24_cb22_retention_setting_capped_at_30_days`, `test_m1_t24_repo_events_pseudonymized_minimised_and_bot_filter_applied` (`tests/integration/test_github_detection_m1t24.py`). The connector stays off (`PIGTAIL_ENABLE_GITHUB_EVENTS=0` plus the ADR-022 flag) until the remaining ADR-022 pre-conditions exist (CB-12 open; CB-03, CB-06 partly done). CB-02 is met for this source by the short retention plus the raw drop at parse (ADR-038; §4). |

**Why 24 months:** outcomes are scored up to T+365 (R3.1). The universe covers a trailing 24 months (R4.1). Matched losers are selected after outcomes are known. See [lia.md](lia.md) §3. A shorter period is allowed per source when the terms require it (§4).

**Minimisation ahead of the 24-month limit.** GH Archive hourly dumps are the largest pool of raw person-level data, and only six fields are used. CB-04, **partly implemented** (`src/pigtail/capture/retention.py`, ADR-027.4):
- **I:** raw bytes are purged after `GHARCHIVE_RAW_RETENTION_DAYS` (default and maximum 30; higher values are refused at startup, CB-32, ADR-045) by `purge_raw()`, which runs after every `capture scan` and via `pigtail capture purge-raw`; the evidence record keeps the hash and URL and is marked `raw_dropped`;
- **I:** replay and forced re-scans re-download the dump from `data.gharchive.org` and refuse it if the hash differs;
- **P:** dropping the bytes immediately after parsing (instead of after ≤ 30 days);
- **P:** a fallback to a stored minimal parse if the upstream copy disappears (today `replay()` raises `NotFound`).

---

## 2. Store-by-store schedule

| Store | Content | Retention | Deletion mechanism | Status |
|---|---|---|---|---|
| Snapshot store (S3 bucket or `PIGTAIL_DATA_DIR/snapshots`; `src/pigtail/capture/snapshots.py`) | Raw bytes plus `.meta.json` sidecar, content-addressed | Per the class of the **longest-living** evidence record that references the hash | Delete the object and its sidecar only when no live evidence record still needs the raw bytes. Content addressing means one blob can back several evidence records. | Store: I. Retention: **I (CB-01)**: a hash is dropped only when no present record still needs it; shared blobs are reported as `blocked_shared`. `SnapshotStore.delete()` removes the bytes and keeps the `.meta.json` sidecar; `purge_raw()` and the retention purge use it (CB-04, CB-01). Encryption at rest: SeaweedFS SSE-S3 when `S3_SSE_KEK` is set; checked by `pigtail doctor` (**I**, CB-03 partly); enabling it is **O**. |
| Postgres `evidence` | Metadata, hash, terms basis | Kept for as long as its coded facts are used. After the raw copy is dropped, `deletion_state` is `raw_dropped` or `deleted_upstream`. | Update the state; delete the row if it is still person-level after aggregation | Field: I (`migrations/0001_capture_v0.sql`). Job: I for the state change (`raw_dropped` on retention and erasure, CB-01, CB-08); a repo opt-out deletes the repo's evidence rows (CB-13). Postgres volume encryption: **O** (CB-03) |
| Postgres case, actor and edge tables (M5) | Pseudonymised records | 24 months | Delete, or roll up into aggregates | Mechanism: I (tables must register in `PERSON_TABLES`, `src/pigtail/privacy/deletion.py`, ADR-030.5; retention and erasure then reach them). Tables: P (M5) |
| Postgres `repo_hourly_activity`, `gharchive_hours` | Aggregates, scan log | No limit | — | I (`migrations/0002_repo_hourly_activity.sql`) |
| Postgres `repo_event_actor` (M1-T24) | Pseudonymised per-repo star and fork actors (D12) | **16 days by default (ceiling 30; ADR-038)** by event time | Deleted by `pigtail retention purge`; erasure by pseudonym | I (CB-22; see §1) |
| Postgres `repo_event_daily_agg`, `repo_event_poll` (M1-T24) | Daily star and fork counts per repo; one row per events poll (status, pages, overflow). No pseudonyms. | No limit | — | I (`migrations/0007_github_detection.sql`) |
| Postgres `watchlist`, `repo_count_snapshot`, `github_graphql_batch`, `repo_star_daily`, `star_history_fetch`, `detection_agreement`, `hn_show_screen`, `github_budget_ledger`, `github_http_cache` (M1-T24) | Project-level: watch list (`owner/name`, owner **type**), hourly GraphQL counts, star-history daily net counts, detection agreement, Show HN item ids with repo names, budget and ETag state. No login or actor columns (asserted in `test_m1_t24_migration_tables_and_constraints`). | No limit (`project_level`); `owner/name` of personal accounts as in §1 | Watch-list rows are deactivated (stale, over the cap, missing or private), not deleted. A repo opt-out **deletes** its rows in every repo-keyed table, the watch list included (`REPO_TABLES`, `src/pigtail/privacy/deletion.py`; CB-13c, ADR-044.1), because a plain-text row would show which repos opted out; `enforce_policy()` (`src/pigtail/capture/github_watch.py`) also skips opted-out repos before every count run | I (`migrations/0007_github_detection.sql`; CB-13c: `tests/integration/test_repo_purge_cb13c.py`) |
| Snapshots of GitHub search pages (M1-T24) | Raw `search/repositories` pages with owner objects (DPIA D14a) | **Dropped right after parsing** (classed `person_level_24m`, ADR-037.8, as a ceiling) | `drop_after_parse` in `SearchSweeper._page` (`src/pigtail/capture/github_screens.py`): hash and URL kept, evidence `raw_dropped`, tombstone in `deletion_log`; unparseable pages dropped at once (CB-23b) | **I (CB-24**, ADR-040.5; `tests/integration/test_github_raw_drop_cb24_cb23b.py`) |
| `llm_cache` (`src/pigtail/llm/store.py`) | Coded outputs, including **verbatim quoted spans** (R7.1) and pseudonyms. Keyed on a hash of the redacted input. | **24 months, and never longer than the evidence it was derived from** | TTL plus purge when the source evidence is deleted or dropped, via `evidence_id` links (`llm_cache_evidence`). | **I (CB-05)**: rows carry `retention_class`, expire after `LLM_CACHE_RETENTION_DAYS` (default and maximum 730) and are never served once expired; `purge_expired()` and `purge_for_evidence()` run in `pigtail retention purge` and on erasure (`src/pigtail/llm/store.py`). |
| `llm_usage`, `llm_pause_log` | Ledger: backend, job, model, token counts. No content. | 24 months (cost audit) | Delete | I (`purge_ledger()` in `pigtail retention purge`) |
| Anthropic (LLM provider) | Redacted inputs and outputs | Outside pigtail's control. See §6. | — | — |
| Logs (application, `runs.error`, container logs) | Must hold no personal data or content | **12 months** | Log rotation; `runs.error` cleared by the purge | **I (CB-18)**: `pigtail.logsafe.RedactingFilter` redacts handles, e-mails, profile URLs and DIDs and truncates payloads; it is installed by every CLI command (`main()` in `src/pigtail/cli.py`, which also scrubs uncaught tracebacks), by every scheduled job before job code runs (`src/pigtail/scheduler/child.py`) and by the scheduler and UI processes (ADR-040.6; `tests/unit/test_logsafe_cb18b.py`). `runs.error` is scrubbed on write (`src/pigtail/capture/runs.py`) and cleared after `LOG_RETENTION_DAYS` (max 365). UI audit rows (`ui_audit_log`) older than `LOG_RETENTION_DAYS` and expired UI sessions (`ui_sessions`) are deleted by the daily `pigtail retention purge` (**I, CB-33**, ADR-045; `src/pigtail/privacy/retention.py`, `infra/schedule.toml`), not only at each login. **O**: container and system log rotation on the host. Host alert files (`ALERTS.md`, `alerts.jsonl`) rotate at 1 MB or 30 days and rotated files are deleted once their first event is older than `LOG_RETENTION_DAYS` (**I, CB-31**, ADR-046.5; `src/pigtail/scheduler/alerts.py`). `BackendError` no longer echoes CLI output, only the exit code, `subtype` and `api_error_status` (`src/pigtail/llm/subscription.py`; tested in `tests/unit/test_subscription_backend.py`): **I (CB-07)**. |
| Local Claude Code transcripts (subscription mode) | None | — | The CLI runs with `--no-session-persistence` in a temporary empty directory (`subscription.py`) | I |
| Git repository (public) | Code and docs only. **Never** personal data. | Permanent (public) | Prevented by the CI private-data scan (`scripts/private_data_scan.py`, ADR-011) | I |
| Backups | Database dumps (`pg_dump`, encrypted to `BACKUP_RECIPIENT` with age, gpg as fallback) and bucket replicas (snapshot bytes are not in the dump; they rely on bucket replicas with the same 35-day expiry) | **35-day rolling** | `pigtail backup prune` deletes files older than 35 days (`--days` may only shorten it). Deletions are re-applied after a restore (§5). | **I (CB-17**, ADR-044.2–3; `src/pigtail/privacy/backup.py`; `tests/integration/test_backup_cb17.py`): unencrypted output and output inside a git working tree are refused. **Not in backups: the LLM cache** (`llm_cache`, `llm_usage`, `llm_pause_log`, SQLite under `PIGTAIL_DATA_DIR`, `src/pigtail/llm/store.py`); after a host loss it starts empty, and it must not be copied back from any file-level host backup without re-applying the retention purge. **Partly I (CB-17b**, ADR-046.6): `pigtail doctor` checks `BACKUP_RECIPIENT` and the age of the newest backup (warn after 2 days, fail after 7), and optional scheduler jobs `backup_create` and `backup_prune` ship disabled (`infra/schedule.toml`; they need `pg_dump` 16 and age or gpg on the host running the scheduler). **P (CB-17b remainder):** continuous off-host shipping of `deletion_log` and the opt-out list (until then, deletions made after the last backup are lost if the live database is lost too, and the operator re-enters them from the request records); a backup container (the app image has neither `pg_dump` 16 nor age). Backups taken before the last key rotation or fingerprint reset cannot be restored (CB-26, CB-35; §3.5). **O:** schedule backups, keep the age identity apart from the data and from `PSEUDONYM_KEY`, store copies off the host in the EU or CH. |
| Manual entries (TM-29, TM-31) | Organisation-level facts, citations | `project_level` | — | I (policy) |

---

## 3. Pseudonym key management

The key (`PSEUDONYM_KEY`) is the "additional information" of GDPR Art. 4(5). Whoever holds it can re-identify any pseudonym by hashing candidate handles.

1. **Generation:** on the host, with at least 256 bits of entropy (`openssl rand -hex 32`, as in H1). The code enforces a minimum length of 16 characters (`src/pigtail/pseudonymize.py`) (**I**). Recommended minimum: 32 bytes.
2. **Storage:** in the host environment or a secrets manager only. Never in git, Postgres, the snapshot bucket, logs or data backups (**O**; CLAUDE.md).
3. **Backup:** a separate encrypted backup, kept apart from the data backups (**O**, H1). If the key is lost, pseudonym continuity is lost. Old records then have to be re-pseudonymised from the raw data, which is only possible while the raw data is retained.
4. **Access:** the operator only. Access requests from data subjects (Art. 15 / FADP Art. 25) are handled by the operator computing the pseudonym, not by giving the key out.
5. **Rotation:** follow [runbooks/key-rotation.md](runbooks/key-rotation.md) (CB-09). A new key stops the refusal list from matching unless everything is re-derived, because opt-outs are stored only as keyed pseudonyms and keyed name hashes. pigtail detects a key change and fails closed (**I, CB-25**, ADR-045). **Rotation uses `pigtail privacy rekey`** (**I, CB-26**, ADR-046.1; runbook §4.1): one transaction that re-derives every stored pseudonym and opt-out under the new key, refuses while other database sessions are connected, **refuses if any opt-out cannot be mapped** (no override), deletes unmappable person-level rows only on request (`--drop-unmapped` or `--purge-person-level`, tombstones with reason `key_rotation`), clears the LLM cache and switches the key fingerprint last. No dual-key window is needed (CB-27 not needed, ADR-046.2). **Scheduled rotation is allowed again** (ADR-046.3): rotate **every 24 months** (aligned with the 24-month retention of person-level data), and also after a compromise (item 6) or when a person with access to the key leaves. A scheduled rotation that would lose an opt-out is postponed, never forced. The bare `pigtail privacy key-fingerprint --reset --confirm-rotation` is only the manual fallback when the old key is lost (runbook §4.2). `pigtail backup restore` refuses backups taken before the last `rekey` or `reset` (**I, CB-26, CB-35**, ADR-046.4), so a fresh backup is taken right after every rotation; older ones expire with the 35-day backup rotation, after which the old key is destroyed.
6. **Compromise:** treat it as a possible personal data breach (§7) and rotate with `pigtail privacy rekey` following the runbook's §4.1 and §5.
7. **Deletion:** when a deployment is decommissioned, destroy the key after the data. Once both are gone, any pseudonyms that remain in aggregates are unlinkable.

---

## 4. Deletion sync (R1.5), per source

The field `evidence.deletion_state` (`present | deleted_upstream | raw_dropped`) exists in `migrations/0001_capture_v0.sql` (**I**). CB-02 status per source: **HN** implemented (`pigtail privacy deletion-sync --source hn`, `src/pigtail/privacy/deletion_sync.py`, daily `deletion_sync` job in `infra/schedule.toml`); **GitHub per-repo events** met by short retention plus the raw drop at parse, no sync source (ADR-038); **GH Archive** raw dumps purged after ≤ 30 days (CB-04) and replay drops opted-out persons at ingest (CB-13); **Bluesky** pending (no connector yet; `BLUESKY_POLICY` is defined, the push source is not). **No source with a deletion duty may be enabled before its sync job exists and has been tested.**

On an upstream deletion, pigtail:
1. marks the evidence `deleted_upstream`;
2. deletes the raw bytes (unless another live evidence record needs the same blob, which cannot happen for a deleted item);
3. deletes the parsed person-level rows and the `llm_cache` rows derived from it;
4. keeps the hash plus coded facts with no person identifier (R1.5), **unless the source's terms forbid even that**;
5. writes a tombstone (hash and source id, no content) so the deletion is re-applied after any backup restore. The append-only `deletion_log` table exists and accepts `deleted_upstream` as a reason (`migrations/0003_privacy_operations.sql`, **I**); the HN sync job writes it (CB-02); the Bluesky sync job is P.

| Source (memo) | Duty (as stated in the memo) | pigtail rule | Window | Status |
|---|---|---|---|---|
| **Bluesky / AT Protocol** (TM-06) | "All services must have a method for deleting content a user has requested to be deleted." Account deletions are notified to other services (ToS). | Consume Jetstream delete and account (deactivate or delete) events. Delete the raw copy and the person-level rows. Keep the hash plus non-identifying coded facts. For a deleted account, apply this to all of that DID's content. Re-check if the User Intents proposal is adopted (CB-13). | **≤ 48 h** (our own default; the guidelines set no number) | P (CB-02). Bluesky stays off until then. |
| **HN Firebase / Algolia** (TM-03, TM-04, TM-30) | No duty found. A `deleted` flag exists. | Courtesy: propagate `deleted` and `dead` on re-check. Same handling as Bluesky. | Re-check open cases daily and closed cases monthly; act ≤ 7 days after detection | **I (CB-02)**: `pigtail privacy deletion-sync --source hn` (`HN_POLICY`) |
| **GitHub API / GH Archive** (TM-01, TM-02) | No deletion duty stated (§H). The GitHub Privacy Statement applies (AUP §7). | No sync (GH Archive is an immutable archive). Raw dumps are purged after ≤ 30 days (CB-04); a replay re-downloads the dump and drops persons on the refusal list at ingest (CB-13). Answer erasure and objection requests individually (§5). The 24-month limit applies to pseudonymised rows. | — | Policy; raw purge I (CB-04) |
| **GitHub per-repo Events API** (TM-33, `github_events`) | No deletion duty stated (TM-02, §H). pigtail consumes no deletion signal for un-stars or deleted accounts from this API. | No deletion-sync source (`pigtail privacy deletion-sync` knows only `hn`). CB-02 is met instead by the raw pages dropped at parse and the actor rows expiring after 16 days by default (ceiling 30), so an upstream un-star or deletion is reflected at the latest then (ADR-038). Erasure and objection requests reach `repo_event_actor` through `PERSON_TABLES` (§5). A sync source is added only if GitHub or H2 requires one (LQ-29 item 4 stays open). | 16 days by default (ceiling 30) (expiry) | **I** (CB-22 expiry; CB-02 met via ADR-038). Connector off by default. |
| **V2EX** (TM-19) | None found | As for HN, on re-check. Off by default until Q8 is answered. | ≤ 7 days | P |
| **Discord invite counts** (TM-27) | Delete API Data if Discord, the user or the server owner asks, and when access ends (Developer Terms §5.b) | Only counts are stored. Delete on request, and delete everything if API access ends. | On request, ≤ 7 days | P (connector not built) |
| **Reddit** (TM-05, **GAP**) | Deleted content must be removed. "Even if … de-identified or anonymized" retention violates the terms. 48 h recommended. | If ever enabled under an operator agreement: delete **everything**, including hash and coded facts, unless the agreement allows otherwise (Q2 / LQ-3). | ≤ 48 h | Not applicable (gap) |
| **X** (TM-15, **GAP**) | Delete or modify within 24 h of a written request; keep offline content in sync | If ever enabled: 24 h sync | ≤ 24 h | Not applicable (gap) |
| **YouTube** (TM-14, **GAP**) | 30-day storage limit | Not applicable | — | Gap |
| **Wayback** (TM-13) | No person-level data may be captured | Project-level pages only; no sync needed | — | Policy |
| **Registries, deps.dev, PyPI, npm, crates.io, Homebrew, Docker Hub** (TM-07 to TM-12) | Aggregate or project data | The crates.io `users` table is dropped at ingest (TM-09) | — | Per connector |

---

## 5. Requests from data subjects (erasure and objection)

- **Channel:** the contact address in [privacy-notice.md](privacy-notice.md).
- **Identification:** the person gives their platform and handle. Proving account control is needed only for access requests, not for objection or erasure: honouring a false objection harms no one.
- **Action (CB-08, CB-13; I: `src/pigtail/privacy/requests.py`, `suppression.py`; commands in `docs/guides/operator.md` "Privacy operations"):**
  1. compute the pseudonym in the platform's namespace; the handle is never stored;
  2. add it to the suppression list (`privacy_suppression`, pseudonyms only), so the person is dropped at ingest from then on;
  3. purge their raw and parsed person-level data and derived cache rows. Raw snapshots containing the person are dropped **whole**, because a content-addressed blob cannot be edited; replay re-downloads the snapshot and drops the person at ingest (ADR-030.1);
  4. keep project-level aggregates, which contain no pseudonym;
  5. write a tombstone to `deletion_log`, and log the request in `privacy_requests` without handle or pseudonym.
- **Unparseable snapshots (CB-34, I, ADR-045):** a retained snapshot that fails to parse no longer aborts an access, erasure or opt-out purge. It is skipped, counted and logged without content; access lists it in the export; erasure and opt-out purges drop its raw bytes if any of its evidence is person-level (tombstone in `deletion_log`), because pigtail cannot prove the person is not in it.
- **Key check (CB-25, I, ADR-045):** every request and opt-out command verifies `PSEUDONYM_KEY` against the database's key fingerprint first and refuses under a changed key, so a request is never answered with pseudonyms that cannot match.
- **Access:** `pigtail privacy request access` exports everything keyed by the pseudonym to a 0600 JSON file; the operator checks account control first and sends it through a secure channel (**I**, CB-08).
- **Rectification:** not built (ADR-030.3). Handled manually meanwhile; raw snapshots are not corrected (DPIA §4).
- **Project owners:** a repo can be opted out by id (`pigtail privacy optout add --repo-id`) or by name; name opt-outs are stored only as keyed hashes (`rk_` + HMAC-SHA256 with `PSEUDONYM_KEY`, CB-13b, ADR-042.1; legacy unkeyed rows are still matched and `pigtail doctor` warns until `pigtail privacy optout rekey` converts them). The purge deletes or unlinks the repo's rows in every repo-keyed table registered in `REPO_TABLES` (aggregates, detection tables, watch list, HN links, cases and linked evidence); a schema test fails if a repo-keyed column is unregistered (**I**, CB-13, CB-13c, ADR-044.1). Snapshots shared with other repos are kept.
- **Deadline:** without undue delay and within one month (GDPR Art. 12(3)).
- **Objection:** honoured without asking for "grounds". This is a conservative default: GDPR Art. 21(1) would let the controller show "compelling legitimate grounds", and we choose not to rely on that.
- **Backups:** purged data is not restored. `pigtail backup restore` carries the live `deletion_log`, opt-out list and request log over, restores in one transaction, replays every tombstone, re-applies the whole opt-out list and runs the retention purge (**I**, CB-17, ADR-044.3). Before anything is replaced it checks the running key against the live database's fingerprint and refuses on a mismatch; the live fingerprint is kept (**I**, CB-25, ADR-045). It also refuses a backup taken before the last key rotation (`privacy rekey`) or bare fingerprint reset, whose pseudonyms and opt-outs use the old key (**I**, CB-26, CB-35, ADR-046.4). If the live database is lost too, deletions made after the last backup are lost: the command warns, and the operator re-enters them from the request records (follow-up: ship `deletion_log` and opt-outs off the host continuously, P).

---

## 6. LLM processing: what leaves pigtail and how long it lives

| | What pigtail sends | What pigtail keeps | What Anthropic keeps (per Anthropic's published policy, 2026-09-25) |
|---|---|---|---|
| **Both backends** | Redacted text: e-mails become `[email]`, phones `[phone]`, GitHub, Bluesky and HN profile URLs `[profile:<platform>:<pseudonym>]`, `did:` identifiers `[did:<pseudonym>]`, and @mentions pseudonyms in the source's namespace (`src/pigtail/pseudonymize.py`, `src/pigtail/llm/client.py`, ADR-006; CB-06 partly implemented: gists, avatar URLs and bare handles in author fields are not yet redacted). Plus the versioned prompt and the output schema. | The input **hash** only (not the input), plus the output in `llm_cache` (see §2) | — |
| **`api`** | as above | as above | Deleted "within 30 days of receipt or generation" by default. **Zero retention** only under a ZDR agreement per organisation. Flagged content up to 2 years even under ZDR. No training (Commercial Terms). Sources: [privacy center](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data), [API retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention). |
| **`subscription`** | as above | as above; no local transcripts (`--no-session-persistence`) | Training setting off: 30 days. On: up to 5 years. Flagged: 2 years (scores 7 years). Feedback: 5 years. **No ZDR** for Free, Pro or Max. Source: [privacy center](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data), [Claude Code data usage](https://code.claude.com/docs/en/data-usage). |

**Conservative defaults:**
- **PRD §10's "zero-retention LLM processing" is met only in `api` mode with a ZDR agreement.** Without ZDR, api mode is "30-day retention under a DPA as processor". Subscription mode can never be zero-retention.
- The operator must turn off training (H1).
- `subscription` is used only for the owner's own non-commercial use (ADR-008, LQ-2).
- Telemetry, error-report and feedback opt-outs are set in the CLI environment: `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_FEEDBACK_COMMAND` (`PRIVACY_ENV`, `src/pigtail/llm/subscription.py`; **I**, CB-07).
- The `/feedback` command is never used on product sessions.

---

## 7. Breach handling (summary)

Security breaches involving person-level data or the pseudonym key are handled under [runbooks/breach.md](runbooks/breach.md) (CB-16, documentation done). It covers:
- notification to the supervisory authority where required, "not later than 72 hours" where feasible (GDPR Art. 33(1));
- notification to the FDPIC "as quickly as possible" when high risk is likely (FADP Art. 24(1));
- key rotation (§3).

---

## 8. Review

Review this policy when a source is added, when a platform's terms change, at H2, and at least every 12 months.

## Changelog
- 2026-09-25: v0.1 created (M3-T2).
- 2026-09-25 — fixes after verifier M3 round 1: uncommitted M1 controls relabelled "I (M1, pending merge)" (§1, §2 snapshot store, evidence and `repo_hourly_activity` rows, §4); CB-07 marked implemented (§2 Logs row, §6).
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — fixes after verifier M3 round 2: CB-04 marked partly implemented (30-day purge + verified re-fetch; drop-after-parse and minimal-parse fallback still planned); stale 'pending merge' conditions removed.
- 2026-09-25 — CB statuses updated after privacy-controls merge (ADR-030): CB-01, CB-05, CB-08, CB-13 implemented and CB-03, CB-06, CB-18 partly implemented (§1, §2, §4, §5, §6); rectification recorded as not built.
- 2026-09-25 — fixes after verifier (ADR-036 alignment): §1 adds the planned class `person_level_30d` (P, CB-22): per-repo event actor rows ≤ 30 days, raw events snapshots dropped at parse.
- 2026-09-25 — CB-22/23 implemented (M1-T24, ADR-037): §1 `person_level_30d` marked I with code and test citations, actor rows now dated by event time (not `fetched_at`), aggregates named (`repo_event_daily_agg`, case `bot_filter` counts; no lockstep, ADR-037.2); `person_level_24m` covers GitHub search pages (ADR-037.8); §2 adds the M1-T24 tables and the search snapshots; §4 adds the per-repo Events API (no deletion duty, no sync source, ≤ 30-day expiry as mitigation, CB-02 applicability open).
- 2026-09-25 — ADR-038 wording (16-day default; CB-02 per source)
- 2026-09-25 — §3 rotation and compromise now point to runbooks/key-rotation.md (CB-09); scheduled rotation suspended until CB-25 to CB-27 exist; §7 points to runbooks/breach.md (CB-16).
- 2026-09-25 — status sync (M3-T11): §1 and §2 GitHub search pages dropped at parse (CB-24); §2 Logs row: filter on every CLI command and scheduled job, CB-18 implemented, host log rotation an operator duty; §2 watch list deleted on repo opt-out (CB-13c); §2 Backups row implemented (CB-17) with follow-ups, and the LLM cache recorded as not in backups; §5 project-owner opt-outs by keyed name (CB-13b) and the `REPO_TABLES` purge (CB-13c), restore re-applies deletions (CB-17).
- 2026-09-25 — ADR-045 (CB-25/29/32/33/34): §1 `GHARCHIVE_RAW_RETENTION_DAYS` capped at 30 (CB-32); §2 Logs row: UI audit rows and expired sessions purged by the daily retention job (CB-33); §3.5 key-change detection implemented (CB-25), scheduled rotation still suspended until CB-26 and CB-27 exist; §5 unparseable snapshots (CB-34), key check on requests and on restore (CB-25).
- 2026-09-25 — ADR-046 (CB-17b/26/27/28/31/35): §3.5 rotation with `pigtail privacy rekey` (CB-26); CB-27 not needed; scheduled rotation allowed again, every 24 months plus after a compromise or a departure (ADR-046.3); bare reset only as the fallback; restore refuses backups taken before the last `rekey` or `reset` (CB-35); §3.6 points to the runbook's §4.1; §2 Logs row: alert-file rotation implemented (CB-31); §2 Backups row: doctor backup checks and optional scheduler jobs (CB-17b, partial); §5 restore guard.
