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
| `person_level_24m` | Raw snapshots and parsed records that contain or derive from data about identifiable people: GH Archive hourly dumps, posts, comments, pseudonymised actor rows, spread-graph nodes and edges, account reach | **24 months from `fetched_at`** (from the capture time, not the content's date) | **Aggregate or delete.** Raw bytes are deleted. Pseudonymised rows are deleted or rolled up into counts with no pseudonym. The evidence record keeps `content_hash`, `url`, `source`, `fetched_at`, `terms_basis` and the coded facts that carry no person identifier, and `deletion_state` is set to `raw_dropped`. | Field: I. Job: **P (CB-01)** |
| `project_level` | Data about repos and packages: stars, downloads, releases, dependents, pricing pages, Wayback captures of project pages | **No time limit** (PRD §10) | Kept. **Exception:** `owner/repo` names of repos owned by personal accounts are personal data. They are kept internally but never published without consent or the public-figure rule (CB-20, LQ-7). | I (policy) |
| `derived_aggregate` | Counts and statistics with no person identifier (e.g. `repo_hourly_activity`) | No time limit | Kept. Must stay non-identifying (cell size rule for public outputs, CB-14). | I (`migrations/0002_repo_hourly_activity.sql`) |

**Why 24 months:** outcomes are scored up to T+365 (R3.1). The universe covers a trailing 24 months (R4.1). Matched losers are selected after outcomes are known. See [lia.md](lia.md) §3. A shorter period is allowed per source when the terms require it (§4).

**Minimisation ahead of the 24-month limit.** GH Archive hourly dumps are the largest pool of raw person-level data, and only six fields are used. CB-04, **partly implemented** (`src/pigtail/capture/retention.py`, ADR-027.4):
- **I:** raw bytes are purged after `GHARCHIVE_RAW_RETENTION_DAYS` (default 30) by `purge_raw()`, which runs after every `capture scan` and via `pigtail capture purge-raw`; the evidence record keeps the hash and URL and is marked `raw_dropped`;
- **I:** replay and forced re-scans re-download the dump from `data.gharchive.org` and refuse it if the hash differs;
- **P:** dropping the bytes immediately after parsing (instead of after ≤ 30 days);
- **P:** a fallback to a stored minimal parse if the upstream copy disappears (today `replay()` raises `NotFound`).

---

## 2. Store-by-store schedule

| Store | Content | Retention | Deletion mechanism | Status |
|---|---|---|---|---|
| Snapshot store (S3 bucket or `PIGTAIL_DATA_DIR/snapshots`; `src/pigtail/capture/snapshots.py`) | Raw bytes plus `.meta.json` sidecar, content-addressed | Per the class of the **longest-living** evidence record that references the hash | Delete the object and its sidecar only when no live evidence record still needs the raw bytes. Content addressing means one blob can back several evidence records. | Store: I. Retention: **P (CB-01)**. `SnapshotStore.delete()` removes the bytes and keeps the `.meta.json` sidecar; `purge_raw()` uses it (CB-04). |
| Postgres `evidence` | Metadata, hash, terms basis | Kept for as long as its coded facts are used. After the raw copy is dropped, `deletion_state` is `raw_dropped` or `deleted_upstream`. | Update the state; delete the row if it is still person-level after aggregation | Field: I (`migrations/0001_capture_v0.sql`). Job: P |
| Postgres case, actor and edge tables (M5) | Pseudonymised records | 24 months | Delete, or roll up into aggregates | P |
| Postgres `repo_hourly_activity`, `gharchive_hours` | Aggregates, scan log | No limit | — | I (`migrations/0002_repo_hourly_activity.sql`) |
| `llm_cache` (`src/pigtail/llm/store.py`) | Coded outputs, including **verbatim quoted spans** (R7.1) and pseudonyms. Keyed on a hash of the redacted input. | **24 months, and never longer than the evidence it was derived from** | TTL plus purge when the source evidence is deleted or dropped. Needs `evidence_id` links on each cache row. | **P (CB-05)**. Today there is no TTL and no link. |
| `llm_usage`, `llm_pause_log` | Ledger: backend, job, model, token counts. No content. | 24 months (cost audit) | Delete | P |
| Anthropic (LLM provider) | Redacted inputs and outputs | Outside pigtail's control. See §6. | — | — |
| Logs (application, `runs.error`, container logs) | Must hold no personal data or content | **12 months** | Log rotation | P (CB-18). `BackendError` no longer echoes CLI output, only the exit code, `subtype` and `api_error_status` (`src/pigtail/llm/subscription.py`; tested in `tests/unit/test_subscription_backend.py`): **I (CB-07)**. |
| Local Claude Code transcripts (subscription mode) | None | — | The CLI runs with `--no-session-persistence` in a temporary empty directory (`subscription.py`) | I |
| Git repository (public) | Code and docs only. **Never** personal data. | Permanent (public) | Prevented by the CI private-data scan (`scripts/private_data_scan.py`, ADR-011) | I |
| Backups | Database dumps and bucket replicas | **35-day rolling** | Expiry. Deletions are re-applied after a restore (§5). | P (CB-17) |
| Manual entries (TM-29, TM-31) | Organisation-level facts, citations | `project_level` | — | I (policy) |

---

## 3. Pseudonym key management

The key (`PSEUDONYM_KEY`) is the "additional information" of GDPR Art. 4(5). Whoever holds it can re-identify any pseudonym by hashing candidate handles.

1. **Generation:** on the host, with at least 256 bits of entropy (`openssl rand -hex 32`, as in H1). The code enforces a minimum length of 16 characters (`src/pigtail/pseudonymize.py`) (**I**). Recommended minimum: 32 bytes.
2. **Storage:** in the host environment or a secrets manager only. Never in git, Postgres, the snapshot bucket, logs or data backups (**O**; CLAUDE.md).
3. **Backup:** a separate encrypted backup, kept apart from the data backups (**O**, H1). If the key is lost, pseudonym continuity is lost. Old records then have to be re-pseudonymised from the raw data, which is only possible while the raw data is retained.
4. **Access:** the operator only. Access requests from data subjects (Art. 15 / FADP Art. 25) are handled by the operator computing the pseudonym, not by giving the key out.
5. **Rotation:** after a suspected compromise, or every 24 months (aligned with the retention period). Rotation re-pseudonymises the retained records with the new key, and then the old key is destroyed. This needs a key id stored with the pseudonyms (**P, CB-09**).
6. **Compromise:** treat it as a personal data breach (§7) and rotate at once.
7. **Deletion:** when a deployment is decommissioned, destroy the key after the data. Once both are gone, any pseudonyms that remain in aggregates are unlinkable.

---

## 4. Deletion sync (R1.5), per source

The field `evidence.deletion_state` (`present | deleted_upstream | raw_dropped`) exists in `migrations/0001_capture_v0.sql` (**I**). The sync jobs are **P (CB-02)**. **No source with a deletion duty may be enabled before its sync job exists and has been tested.**

On an upstream deletion, pigtail:
1. marks the evidence `deleted_upstream`;
2. deletes the raw bytes (unless another live evidence record needs the same blob, which cannot happen for a deleted item);
3. deletes the parsed person-level rows and the `llm_cache` rows derived from it;
4. keeps the hash plus coded facts with no person identifier (R1.5), **unless the source's terms forbid even that**;
5. writes a tombstone (hash and source id, no content) so the deletion is re-applied after any backup restore.

| Source (memo) | Duty (as stated in the memo) | pigtail rule | Window | Status |
|---|---|---|---|---|
| **Bluesky / AT Protocol** (TM-06) | "All services must have a method for deleting content a user has requested to be deleted." Account deletions are notified to other services (ToS). | Consume Jetstream delete and account (deactivate or delete) events. Delete the raw copy and the person-level rows. Keep the hash plus non-identifying coded facts. For a deleted account, apply this to all of that DID's content. Re-check if the User Intents proposal is adopted (CB-13). | **≤ 48 h** (our own default; the guidelines set no number) | P (CB-02). Bluesky stays off until then. |
| **HN Firebase / Algolia** (TM-03, TM-04, TM-30) | No duty found. A `deleted` flag exists. | Courtesy: propagate `deleted` and `dead` on re-check. Same handling as Bluesky. | Re-check open cases daily and closed cases monthly; act ≤ 7 days after detection | P |
| **GitHub API / GH Archive** (TM-01, TM-02) | No deletion duty stated (§H). The GitHub Privacy Statement applies (AUP §7). | No sync (GH Archive is an immutable archive). Answer erasure and objection requests individually (§5). The 24-month limit applies. | — | Policy |
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
- **Action (CB-08):**
  1. compute the pseudonym;
  2. add it to the suppression list, so the person is dropped at ingest from then on;
  3. purge their raw and parsed person-level data and derived cache rows;
  4. keep project-level aggregates, which contain no pseudonym;
  5. write a tombstone.
- **Deadline:** without undue delay and within one month (GDPR Art. 12(3)).
- **Objection:** honoured without asking for "grounds". This is a conservative default: GDPR Art. 21(1) would let the controller show "compelling legitimate grounds", and we choose not to rely on that.
- **Backups:** purged data is not restored. The tombstone log is re-applied after any restore (CB-17).

---

## 6. LLM processing: what leaves pigtail and how long it lives

| | What pigtail sends | What pigtail keeps | What Anthropic keeps (per Anthropic's published policy, 2026-09-25) |
|---|---|---|---|
| **Both backends** | Redacted text: e-mails become `[email]`, phones `[phone]`, and @mentions become pseudonyms (`src/pigtail/llm/client.py`, ADR-006). Plus the versioned prompt and the output schema. | The input **hash** only (not the input), plus the output in `llm_cache` (see §2) | — |
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

Security breaches involving person-level data or the pseudonym key are handled under the runbook CB-16 (**P**). It covers:
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
