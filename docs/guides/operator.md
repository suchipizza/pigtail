# Operator guide (draft — completed in M9)

## LLM backend (`LLM_BACKEND`, PRD F15)
| Value | What it uses | When |
|---|---|---|
| `subscription` (default) | Your locally installed, official Claude Code CLI (`claude -p`) on **your own** Claude plan | Running pigtail for yourself |
| `api` | Anthropic API with `ANTHROPIC_API_KEY` | Shared or hosted deployments, high-volume runs |

Switching needs a restart and nothing else.

**Scope of the subscription backend (R15.6).** It is only for an operator running pigtail for themselves, on their own Claude plan, within Anthropic's terms for Claude Code. Any deployment that serves other users must use `api`. Read Anthropic's current terms before deploying: https://code.claude.com/docs/en/legal-and-compliance

Subscription setup:
1. Install Claude Code and log in with your plan (`claude`, then `/login`), or on a headless host run `claude setup-token` yourself and export `CLAUDE_CODE_OAUTH_TOKEN` in the host environment. pigtail never reads, stores or transmits this token.
2. Make sure `ANTHROPIC_API_KEY` is **not** set for pigtail's processes. pigtail also strips it from the CLI subprocess, because it would override the subscription.
3. Turn off model training in your Claude account's privacy settings.
4. Check with `uv run pigtail llm smoke` and `uv run pigtail llm status`.

API mode: use zero data retention or a data processing agreement with Anthropic where available (PRD §10).

## Services
`docker compose up -d --wait` starts Postgres and S3-compatible object storage (SeaweedFS). Point `S3_ENDPOINT` at a private bucket in production. Default hosting region: EU or Switzerland.

## Privacy operations
These commands implement the code side of the retention policy and the DPIA controls
(`docs/compliance/retention-policy.md`, `docs/compliance/dpia.md` §9). They need
`DATABASE_URL`; everything that turns a handle into a pseudonym also needs `PSEUDONYM_KEY`.
Every command runs migrations first, writes a `run` record, and logs each deletion as a tombstone
in the append-only `deletion_log` table (hashes and ids only, never content).

### Startup check: `pigtail doctor` (CB-03)
```bash
uv run pigtail doctor            # human-readable; exit 1 on any FAIL
uv run pigtail doctor --strict   # also exit 1 on WARN (use in deploy scripts)
uv run pigtail doctor --json
```
It checks `PSEUDONYM_KEY`, the database and pending migrations, the snapshot bucket's default
encryption (`GetBucketEncryption`), TLS to a remote object store, and prints the retention
settings. `capture scan` logs the same encryption warning when it starts. Two items show as
`MANUAL` because no client can see them: Postgres volume encryption and, with
`SNAPSHOT_BACKEND=local`, the snapshot directory's disk encryption.

### Encryption at rest (CB-03)
Required before production capture (ADR-022).
- **Snapshot bucket.**
  - *Bundled SeaweedFS:* set `S3_SSE_KEK` to 64 hex characters (`openssl rand -hex 32`) in the
    host environment, then run `docker compose up -d objectstore && docker compose run --rm
    objectstore-init`. The init job sets the bucket's default encryption (SSE-S3, AES256). Objects
    are then stored encrypted on the volume (verified with SeaweedFS 4.47). Objects written
    **before** SSE was turned on stay unencrypted: re-capture them or copy them in place. The KEK is
    the only way to read the data. Back it up apart from the data backups and keep it out of git,
    Postgres and the volume. SeaweedFS's `-filer.encryptVolumeData` flag does **not** cover
    S3 uploads, so don't rely on it.
  - *Hosted S3 (production):* use a private bucket with default encryption (SSE-S3 or SSE-KMS)
    and TLS (`https://` endpoint). If your provider doesn't implement `GetBucketEncryption`,
    `doctor` reports `MANUAL`. In that case, confirm encryption in the provider console.
- **Postgres.** Put the `db-data` volume on an encrypted disk or volume: LUKS/dm-crypt on Linux,
  the cloud provider's volume encryption, or FileVault for a local Mac. Postgres has no built-in
  transparent data encryption, and `doctor` cannot check the volume. Record how it is encrypted in
  your deployment notes.
- **Local snapshots and the LLM cache** (`PIGTAIL_DATA_DIR`) also hold person-level data. Keep
  that directory on an encrypted disk.
- **Backups** must be encrypted too (CB-17, planned).

### Retention purge (CB-01, CB-04, CB-05, CB-18)
```bash
uv run pigtail retention purge --dry-run   # report only; still writes a run record
uv run pigtail retention purge             # run daily (cron or systemd timer)
```
What it does, in order:
1. Drops raw GH Archive dumps after `GHARCHIVE_RAW_RETENTION_DAYS` (default 30).
2. Drops the raw bytes of every `person_level_24m` evidence record after
   `PERSON_LEVEL_RETENTION_DAYS` (default and maximum 730, counted from `fetched_at`). The evidence
   row keeps its hash, URL, source, fetch time and terms basis, and moves to
   `deletion_state = raw_dropped`. A blob shared with a `project_level` record or with a younger
   capture is kept and reported as `blocked_shared`.
3. Deletes pseudonymous person-level rows older than the cutoff (tables registered in
   `pigtail.privacy.deletion.PERSON_TABLES`: `hn_mention`, `upstream_items`; M5 tables add to it).
4. Deletes LLM cache rows linked to the evidence dropped in step 2, and every cache row older than
   `LLM_CACHE_RETENTION_DAYS` (default and maximum 730). The LLM usage ledger follows the same
   period. Expired cache rows are never served, even before a purge runs.
5. Clears `runs.error` text older than `LOG_RETENTION_DAYS` (default and maximum 365).

Project-level and aggregate data are never touched. Longer periods than the policy allows are
rejected at startup. Container and system logs need their own 12-month rotation, for example
journald `MaxRetentionSec=1year` or logrotate.

### Deletion sync (CB-02, R1.5)
```bash
uv run pigtail privacy deletion-sync --dry-run          # report only; still writes a run record
uv run pigtail privacy deletion-sync --source hn        # run daily (cron or systemd timer)
```
Capture jobs register every upstream item whose content sits in a person-level snapshot
(`upstream_items`). The sync re-checks items that are due and, for each item that is gone upstream
(HN: `deleted`, `dead`, or `null` from the Firebase API):
1. drops the raw bytes of every snapshot that holds it (a search page holds many items, so the
   whole page goes) and moves all evidence with those hashes to `deletion_state =
   deleted_upstream` (hash, URL, fetch time and terms basis stay);
2. deletes its parsed person-level rows (`hn_mention`) and the LLM cache rows derived from the
   evidence;
3. writes tombstones (reason `deleted_upstream`) to `deletion_log`.

It also re-applies deletions to evidence captured after an item was found gone (a stale search
index, or a backup restore). Schedule per source (retention-policy.md §4): HN items linked to an
open case are re-checked daily, others monthly; action within 7 days of detection. The report
lists `overdue_before_run` (re-checks more than 7 days late) and `detected_not_acted`. Checks store
nothing and run even when HN collection is switched off. Bluesky (≤ 48 h, push/tombstone based)
will plug into the same job before it may be enabled.

### Hacker News sources (M1-T4, M1-T14)
- **Rank poller** (`hn_ranks`, enabled by default; `PIGTAIL_CONNECTOR_HN_RANKS_ENABLED=false`
  turns it off). Project-level only: story ids, ranks, urls, titles, scores, comment counts. It
  keeps no usernames (the item's `by` is dropped, and item raw JSON is deleted right after
  parsing), so it does not need the ADR-022 flag. Rank history can't be backfilled, so run it
  continuously:
  ```bash
  uv run pigtail capture hn-ranks --once                          # one poll
  uv run pigtail capture hn-ranks --loop --interval-minutes 5     # long-running (systemd service)
  ```
  The interval can't be under 1 minute (TM-04). Ranks 1–30 are the front page.
- **Mention capture** (`hn_algolia`, `hn_firebase`): person-level (usernames are pseudonymized,
  comment text stays in private snapshots). **Disabled by default** (`PIGTAIL_ENABLE_HN=0`).
  Setting `PIGTAIL_ENABLE_HN=1` fails with an error unless `PIGTAIL_ADR022_PERSON_SOURCES_OK=1`
  is also set. **Set that flag only after every ADR-022 precondition for person-level sources is
  in place on your deployment:** CB-01 (retention purge scheduled), CB-02 (deletion sync
  scheduled), CB-03 (encryption at rest), CB-06 (LLM redaction), CB-08 (request handling),
  CB-12 (published privacy notice) and CB-13 (opt-outs), per `ops/DECISIONS.md` ADR-022. Use by
  commercial operators is also pending legal question LQ-6.
  ```bash
  uv run pigtail capture mentions --repo owner/name [--since 2026-09-01] [--loose] [--no-items]
  ```
  Evidence attaches to the repo's newest open case if it has one. `--loose` also keeps hits that
  only contain the repo name (noisy for common words).

### Opt-outs (CB-13)
```bash
uv run pigtail privacy optout add --platform github --handle -       # reads the handle from stdin
uv run pigtail privacy optout add --platform github --repo-id 123456 # a project owner opts out
uv run pigtail privacy optout add --platform github --repo owner/name
uv run pigtail privacy optout list
uv run pigtail privacy optout remove --platform github --handle -
uv run pigtail privacy optout purge     # re-apply the whole list, e.g. after a backup restore
```
- **Handles are never stored.** A handle is pseudonymized at once with `PSEUDONYM_KEY` in the
  platform's namespace, which is the same pseudonym the connectors store. The database rejects
  anything that isn't a pseudonym. `--handle X` also works, but `--handle -` (or leaving the flag
  out) reads the handle from stdin and keeps it out of your shell history.
- **Ingest:** `capture scan` loads the list, and connectors drop matching records before
  aggregation (counted as `<source>.suppressed` in the run record). Opted-out repos are dropped
  by repo id.
- **Existing data:** `add` purges at once unless you pass `--no-purge`.
  - For a person, this drops the raw snapshots that contain their records (whole snapshots;
    replay re-downloads them and drops the person at ingest), person-level rows, and LLM cache
    rows derived from those snapshots or mentioning the pseudonym.
  - For a repo, it deletes the repo's hourly aggregates, cases, linked evidence (raw bytes
    first) and the `repos` row.
  - Opt-outs are logged in the request log as `objection`.

### Data-subject requests (CB-08)
```bash
uv run pigtail privacy request access  --platform github --handle -   # export
uv run pigtail privacy request erasure --platform github --handle -   # erase + opt out
uv run pigtail privacy requests                                       # request log
```
- **Access** writes `PIGTAIL_DATA_DIR/requests/<request id>.json` (mode 0600; override with
  `--out DIR`). It contains every record keyed by the requester's pseudonym:
  - parsed records in retained raw snapshots of that platform's sources;
  - person-level rows;
  - LLM cache rows that mention the pseudonym. Rows from LLM calls made without a source
    namespace are listed separately; review them before sending.

  Evidence whose raw bytes were already dropped has no person-level content left to search, and
  the export says how many such records exist. Access requires proof of account control
  (retention policy §5); check it before running the command. Send the file through a secure
  channel, then delete it.
- **Erasure** adds the pseudonym to the opt-out list, then purges as described under Opt-outs.
  Project-level aggregates without pseudonyms are kept (retention policy §5).
- **The request log** (`privacy_requests`) stores the request id, type, platform, received and
  completed times, outcome and counts. It never stores the handle or the pseudonym. The
  statutory deadline is one month (GDPR Art. 12(3)).
- Scanning raw snapshots reads every retained snapshot of the platform. With the 30-day GH
  Archive retention that is up to about 720 hourly dumps, so expect minutes to hours.

### Log hygiene (CB-18)
`pigtail capture scan` installs `pigtail.logsafe.RedactingFilter` on its logging (other commands and services don't yet: CB-18 follow-up). The filter replaces
handles, e-mails, profile URLs and DIDs with placeholders and truncates long payloads. `runs.error`
goes through the same scrubber. When you add a service, call `pigtail.logsafe.configure_logging()`
or `install()` on its handlers.
