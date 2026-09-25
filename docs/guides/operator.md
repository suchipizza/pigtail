# Operator guide (draft — completed in M9)

## Your duties as controller (CB-21)
If you run pigtail, you are the controller of the personal data it collects on your host, not the
pigtail maintainers. Before the first capture, read
[`docs/compliance/controller-duties.md`](../compliance/controller-duties.md). It lists what you must
adopt and do: the LIA and DPIA, a published privacy notice, the record of processing activities,
handling requests with the `pigtail privacy` commands, retention and deletion sync, encryption,
key handling and backups, the breach runbook, the subscription-vs-api scope of the LLM backend
(ADR-008, ADR-023), and EU or Swiss hosting. Related:
- [Record of processing activities](../compliance/ropa.md) (template to fill in)
- [Breach response runbook](../compliance/runbooks/breach.md)
- [`PSEUDONYM_KEY` management and rotation](../compliance/runbooks/key-rotation.md). Don't
  change the key without reading it: opt-outs stop matching.

Set `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` only once those duties are met.

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

## Running unattended (M1-T21)
Long-running jobs run on the host and must not depend on an agent session (WORK_ORDER §2). The
scheduler (`pigtail scheduler run`) is one small process that:
- runs the jobs in `infra/schedule.toml` (override with `PIGTAIL_SCHEDULE`), each in a child
  process with a timeout;
- holds one Postgres advisory lock per job, so runs never overlap, even with two schedulers on one
  database;
- retries failures with exponential backoff (`retry_base` × 2ⁿ, capped at the interval);
- writes a `run` record `scheduler.<job>` for every attempt (the command also writes its own, e.g.
  `capture.hn_ranks`). Those records are the scheduler's only state, so restarts lose nothing. A
  run left `running` by a killed scheduler is closed as failed ("abandoned") and retried;
- serves `/healthz` and `/livez` (port `PIGTAIL_HEALTH_PORT`, default 8787, bound to
  `PIGTAIL_HEALTH_BIND`, default 127.0.0.1) and evaluates alert rules every 5 minutes.

| Job | Command | Every | Notes |
|---|---|---|---|
| `hn_ranks` | `capture hn-ranks --once` | 5 min | project-level, on by default (ADR-031.1) |
| `gharchive_scan` | `capture scan --start <midnight −2 d> --end <now −2 h>` | 1 h | complete days already scanned are skipped; catches up after ≤ 2 days of downtime (ADR-028: limited value, cheap) |
| `gharchive_backfill` | `capture backfill-gharchive --days 7` | 1 h | M1-T19: retries missing GH Archive hours that are due (see below); one query when nothing is due |
| `retention_purge` | `retention purge` | 1 d | CB-01, CB-04, CB-05, CB-18 |
| `deletion_sync` | `privacy deletion-sync` | 1 d | CB-02; needs `PSEUDONYM_KEY` |
| `purge_raw` | `capture purge-raw` | 1 d | CB-04 |
| `hn_mentions` | `capture mentions --repo … --since <opened −14 d>` per live case opened in the last 48 h | 3 h | person-level: **skipped and logged** unless `PIGTAIL_ENABLE_HN=1` *and* `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` (ADR-022) |

**Missing GH Archive hours (M1-T19).** When an hourly dump can't be read at scan time (404: not
published yet; 5xx/429 after the connector's retries; a network error; or a dump that fails to
decompress, whose bytes are then deleted at once), the hour is stored as `missing` with its
reason, attempt count and next retry time (1 h, 2 h, 4 h, … capped at 24 h). Missing hours count
as *unknown* in the baseline, never as zero. `gharchive_backfill` retries the due ones up to
`--days` back (1–30, default 7; `--max-hours` downloads per run, default 48), re-aggregates each
UTC day in which an hour came back, and re-runs detection for the following 47 hours. Older
missing hours are reported as `expired` and stay unknown. By hand:
```bash
uv run pigtail capture backfill-gharchive --days 7
```
The JSON output lists `due`, `tried`, `recovered`, `still_missing` (by reason), `not_due`,
`expired`, `days_rescanned` and `cases_opened`.

A job whose connector is disabled (`PIGTAIL_CONNECTOR_<NAME>_ENABLED=false`) is skipped, not
failed: its run record has `counts.skipped = 1` and the reason in `config.skipped`. Set
`enabled = false` in the schedule to drop a job entirely. `pigtail scheduler plan` shows each
job's next due time and what it would run now; `pigtail scheduler run --once` runs whatever is due
once and exits (for cron-only hosts or debugging).

### With Docker Compose
```bash
cp .env.example .env            # set PSEUDONYM_KEY, S3_*, SNAPSHOT_BACKEND=s3 for production, SMTP_URL/ALERT_EMAIL
docker compose up -d --wait db objectstore && docker compose run --rm objectstore-init
docker compose up -d --build scheduler
curl -s http://127.0.0.1:8787/healthz | python3 -m json.tool
docker compose exec scheduler pigtail health
docker compose logs -f scheduler
```
The `scheduler` service uses the repo's `Dockerfile` (python:3.12-slim + uv, locked
dependencies, non-root uid 10001, read-only root filesystem, all capabilities dropped,
`restart: unless-stopped`). Its `PIGTAIL_DATA_DIR` is the `app-data` volume (`/data`): local
snapshots (if `SNAPSHOT_BACKEND=local`), the LLM cache and `alerts/`. It reads `.env`, but
`DATABASE_URL` and `S3_ENDPOINT` point at the compose services (`db`, `objectstore`); set
`COMPOSE_DATABASE_URL` / `COMPOSE_S3_ENDPOINT` to use external ones. Build with
`--build-arg PIGTAIL_CODE_COMMIT=$(git rev-parse HEAD)` (or export `PIGTAIL_CODE_COMMIT` before
`docker compose build`) so run records carry the code commit. `docker compose stop` gives
running jobs 2 minutes to finish.

### With systemd (no Docker)
`infra/systemd/pigtail-scheduler.service` runs the same command from a checkout in
`/opt/pigtail` (`uv sync --locked --no-dev`), as user `pigtail`, with its environment in
`/etc/pigtail/pigtail.env` (mode 0600, root-owned) and `PIGTAIL_DATA_DIR=/var/lib/pigtail`. It
runs `pigtail db migrate` before starting, restarts on failure and is sandboxed
(`ProtectSystem=strict`, `NoNewPrivileges`, no capabilities). Install steps are in the unit
file's header. Check it with `systemctl status pigtail-scheduler`, `journalctl -u
pigtail-scheduler` and `curl -s 127.0.0.1:8787/healthz`. Set journald retention to 12 months
or less (`MaxRetentionSec=1year`, CB-18).

### Health
```bash
uv run pigtail health            # per job: last success, lag, failures; DB, S3, disk, deletion SLA, doctor
uv run pigtail health --json     # same as /healthz's body
uv run pigtail health --history 7d
```
- A job is **stale** when it has had no success for 3× its interval; that and 3 consecutive
  failures mark it FAIL. `pigtail health` exits 1 when anything is FAIL.
- `/healthz` returns the same JSON. Its HTTP status is 503 only when the scheduler loop has stopped
  ticking or the database is unreachable, because restarting the container fixes neither job
  failures nor doctor warnings. Those show in the body (`"status": "fail"`) and raise alerts.
  `/livez` only checks the loop.

**M1 acceptance: 7 consecutive days of scans.** `pigtail health --history 7d` prints one line
per UTC day: whether it is a complete scan day, GH Archive hours covered (out of 24, counting
hours GH Archive itself is missing), HN rank polls (288 expected at 5 minutes), and scheduler
runs per job (succeeded/failed/skipped). The last line gives the number of consecutive complete
scan days, today excluded. A day is complete when all 24 hours were scanned and at least one
scheduled `gharchive_scan` run succeeded that day. The criterion is met when that number is
≥ 7; `--json` gives the same data for the verifier.

### External liveness check (M1-T26)
A dead scheduler can't send its own alerts, and `/healthz` answers only while the process runs.
So the scheduler writes a heartbeat at every tick (every 15 s) to
`PIGTAIL_DATA_DIR/liveness.json`. To use another path, set `PIGTAIL_LIVENESS_FILE` or pass
`pigtail scheduler run --liveness-file PATH`. The heartbeat holds only times, a tick count and
the process id. A **separate** process, on its own schedule, checks it:
```bash
uv run pigtail health --liveness-file /var/lib/pigtail/liveness.json --max-age 5m --alert
```
- Exit 0 while the heartbeat is fresh. Exit 1 when it is older than `--max-age` (default 5m), or
  missing or unreadable.
- `--alert` raises a critical `scheduler_dead` alert through the normal sink (host files and
  e-mail when `SMTP_URL`/`ALERT_EMAIL` are set). The alert repeats at most hourly and resolves
  once the heartbeat is fresh again. Its state lives in `PIGTAIL_DATA_DIR/alerts/liveness/`, so
  it never touches the scheduler's own alert state.

**systemd (same host):** install `infra/systemd/pigtail-liveness.service` and
`pigtail-liveness.timer`, then run `sudo systemctl enable --now pigtail-liveness.timer`. It runs
every 5 minutes.

**cron (same host):**
```cron
*/5 * * * * cd /opt/pigtail && PIGTAIL_DATA_DIR=/var/lib/pigtail .venv/bin/pigtail health --liveness-file /var/lib/pigtail/liveness.json --alert >/dev/null
```

**Docker Compose:** the file is in the `app-data` volume. On the host, run
`docker compose exec -T scheduler cat /data/liveness.json | pigtail health --liveness-file - --alert`.
Note that this also fails (and alerts) when the container is down.

**From a second host** (recommended: it also catches a dead host, disk or network). Install
pigtail on the second host with `uv sync --locked --no-dev`. It needs no database, only
`PIGTAIL_DATA_DIR` and the SMTP variables. Give it key-only SSH access to a read-only account on
the scheduler host that can read the heartbeat file, then run it every 5 minutes (a timer, or
cron):
```bash
ssh -o BatchMode=yes -o ConnectTimeout=20 pigtail-ro@scheduler-host cat /var/lib/pigtail/liveness.json \
  | pigtail health --liveness-file - --max-age 5m --alert
```
`--liveness-file -` reads the heartbeat from stdin. If SSH fails, nothing arrives on stdin, so
the check fails and the alert fires. The commented `ExecStart` in
`pigtail-liveness.service` is the same thing as a systemd unit. Use a `--max-age` a few minutes
longer than the check interval, so that one slow SSH connection doesn't page you.

### Alerts
Every 5 minutes the scheduler evaluates these rules (thresholds under `[alerts]` in the schedule):

| Rule | Fires when |
|---|---|
| `job_stale` | no success for more than 3× the job's interval |
| `job_failing` | 3 or more consecutive failures |
| `db_down` | database unreachable, or the run log can't be read |
| `s3_down` | snapshot bucket unreachable (`SNAPSHOT_BACKEND=s3`) |
| `disk_high` | `PIGTAIL_DATA_DIR` volume more than 80% full |
| `doctor` | any `pigtail doctor` check at WARN or FAIL |
| `deletion_sla` | deletion-sync re-checks, or detected deletions, more than 7 days overdue (CB-02) |
| `scheduler_dead` | the heartbeat is stale; raised by the **external** liveness check, not by the scheduler (M1-T26) |

Alerts go to `PIGTAIL_DATA_DIR/alerts/` on the host: `ALERTS.md` (readable, append-only),
`alerts.jsonl` (structured) and `state.json` (dedupe). The files are mode 0600 and are **never
committed**. Messages hold job names, check names, counts and times only, and are passed through
the CB-18 scrubber anyway. An alert is written when it starts firing, again at most every 6 hours
while it keeps firing (`repeat`), and once when it resolves. If `SMTP_URL` and `ALERT_EMAIL` are
set, each batch is also e-mailed:
- `SMTP_URL`: `smtp://host[:25]` (STARTTLS if offered), `smtp+starttls://user:pass@host:587`, or
  `smtps://user:pass@host:465`. Login without TLS is refused except to localhost.
- `ALERT_EMAIL`: a comma-separated list of recipients.
- `ALERT_EMAIL_FROM`: optional sender (default: the first recipient).

E-mail is best effort: the file is always written first. On a host without the scheduler, run
`pigtail alerts check` from cron.

**Copying alerts into the repo.** `ops/ALERTS.md` is public. An agent session copies only a
sanitized summary there:
```bash
uv run pigtail alerts export --to ops/ALERTS.md --since 7d
```
The export keeps rule, subject (job or check name; anything else becomes `redacted`),
severity, whether the alert is still firing, counts, and first and last times. It has no message
text. Review the diff before committing.

## Web app (D1 preview)
The Forensics Explorer preview (M1-T12): `/cases` and `/cases/:id` with the **Timeline** and
**Evidence** tabs on captured data. Everything is labelled **uncoded preview**: events are raw
captures; burst/launch labels, triggers and mechanisms arrive with coding (M5). One process serves
the read-only API (R14.2) and the built UI.

**Private by default (R13.3, DPIA CB-19).** The app refuses to start without an operator password
hash, every `/api` route needs a session, and there is no public API explorer.

1. Create the password hash (the password itself is never stored):
   ```bash
   uv run pigtail ui hash-password          # prompts twice; prints an argon2id hash
   export PIGTAIL_OPERATOR_PASSWORD_HASH='$argon2id$v=19$…'   # single quotes: the hash contains `$`
   ```
   In `.env`, also single-quote it so Compose doesn't interpolate the `$`.
2. Build the UI once (Node 22 and pnpm; `corepack enable` provides pnpm), then serve:
   ```bash
   pnpm --dir ui install --frozen-lockfile && pnpm --dir ui build
   uv run pigtail ui serve --host 127.0.0.1 --port 8080   # runs migrations, then serves
   ```
   Or with Docker: `docker compose up -d --build ui` (image `infra/ui/Dockerfile`; published on
   `127.0.0.1:8080` only; reads local snapshots from the `app-data` volume, read-only).
3. Open http://127.0.0.1:8080 and log in.

Settings (environment): `PIGTAIL_UI_SESSION_HOURS` (absolute session lifetime, default 12),
`PIGTAIL_UI_IDLE_MINUTES` (default 120), `PIGTAIL_UI_SECURE_COOKIE` (`auto` by default: the
cookie is `Secure` unless the app is reached on a loopback host; set `1` behind a TLS proxy),
`PIGTAIL_UI_DIST` (built UI directory). Database and snapshot settings are the capture ones
(`DATABASE_URL`, `SNAPSHOT_BACKEND`, `S3_*`, `PIGTAIL_DATA_DIR`).

**Remote access.** Keep the bind on loopback. To reach it from elsewhere, use an SSH tunnel
(`ssh -L 8080:127.0.0.1:8080 host`) or a TLS reverse proxy; with a proxy, start with
`--proxy-headers --forwarded-allow-ips <proxy ip>` so login rate limiting sees real clients, and
set `PIGTAIL_UI_SECURE_COOKIE=1`. Never expose it on a public address without TLS.

**What is logged (CB-19).** `ui_audit_log` records login success, failure and rate-limit events,
logouts, and every snapshot view (time, route, HTTP status, evidence id, content hash, a 16-char
session-hash prefix). It stores **no IP address**: the client is a keyed hash of the truncated
address (IPv4 /24, IPv6 /48), used only to rate-limit logins (5 failures per client network and
30 overall per 15 minutes) and to spot brute force. The key derives from the password hash, so it
rotates with the password. Rows older than `LOG_RETENTION_DAYS` (max 365) are deleted at each
login. uvicorn access logs, which would contain IPs, are off unless `--access-log` is given.
Read the log with SQL, e.g. `SELECT at, event, evidence_id FROM ui_audit_log ORDER BY at DESC`.

**What the pages show.**
- `/cases`: filters (status, opened date range), sort by recency or 48 h velocity, and a "Live
  now" strip of open cases by velocity (a placeholder until triggers are coded).
- `/cases/:id`: detection metrics with the 48 hourly buckets and GH Archive dumps behind them, the
  **coverage caveat** (ADR-028: GH Archive under-captures stars, so counts are a lower bound;
  unscanned hours are unknown, not zero), and two tabs:
  - **Timeline**: time-aligned lanes for GitHub stars (raw vs bot/lockstep-filtered), forks,
    HN front-page rank (best rank per bucket; shaded band = ranks 1–30) with mention markers,
    and captured evidence. Zoom with the range buttons or by dragging across the chart; buckets
    switch between hours and days. Every point opens its evidence (a daily bucket lists its
    hourly dumps). A table view is available.
  - **Evidence**: every evidence record behind the case (case- and repo-linked items, HN stories,
    mentions and rank polls, GH Archive hours with repo activity), sortable by capture time,
    source, reliability, retention class and state, with a one-click **Open snapshot**.
- `/evidence/:id`: the record, its retention rule and due date, and what references it.

**Snapshots.** "Open snapshot" streams the raw bytes after re-checking their SHA-256; a mismatch
is refused (500) and audited. Snapshots open in a new tab under a sandboxing CSP (no scripts, no
external requests); gzip dumps download. Raw snapshots can contain handles and text: they are the
private evidence itself, so treat the screen and any downloads accordingly. When the bytes are
gone the API answers **410** with the reason: `raw_dropped` (retention; hash, URL and fetch time
are kept, replay can re-fetch) or `deleted_upstream` (deletion sync, CB-02). JSON responses never
contain handles: HN authors are not returned, and titles and URLs pass the identifier scrubber
(`@handle` → `@[handle]`, `github.com/<login>` → `[profile:github]`); titles of stories deleted
upstream are hidden.

## JSONL export (M1-T20, PRD §7)
```bash
uv run pigtail export jsonl --out /srv/pigtail-export                  # project-level tables
uv run pigtail export jsonl --out /srv/pigtail-export --tables repos cases evidence
uv run pigtail export jsonl --out /srv/private/export --include-person-level
```
- The export writes one `<table>.jsonl` per table plus `manifest.json` (format, database
  migration, code commit, row count and SHA-256 per file). Each line is one record with sorted
  keys and a `schema_version`. Rows are ordered by primary key, times are UTC, and one
  read-only snapshot transaction is used. The same database state therefore gives identical
  files, which diff and merge cleanly.
- **Never inside the pigtail repository** (it is public). Such a path is refused with or without
  flags.
- **Person-level tables** (`hn_mention`, `upstream_items`, `evidence_upstream_items`, the refusal
  list) are exported only with `--include-person-level`, and only to a directory outside **any**
  git working tree. `repo_event_actor`, UI sessions and the UI audit log are never exported.
  Files are mode 0600 in a 0700 directory. Treat a person-level export like the database:
  private, encrypted storage, covered by the retention policy. Delete it when you're done.
- A table the export doesn't know stops it. Every new migration must classify its tables in
  `pigtail.export.jsonl.TABLE_LEVELS`.

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

It also reports which sources are switched on (M1-T23):
- `adr022_person_sources`: `OK` while `PIGTAIL_ADR022_PERSON_SOURCES_OK` is unset (person-level
  sources held); `WARN` when it is `1`, as a reminder that only you can confirm every ADR-022
  precondition (for example the published notice, CB-12);
- `hn_sources`: the rank poller and the person-level HN connectors (`hn_firebase`,
  `hn_algolia`). `WARN` if the rank poller is off (rank history can't be backfilled); `FAIL` if a
  person-level connector is on without the ADR-022 flag (it would refuse to start);
- `github_events`: per-repo GitHub events. `FAIL` if on without the ADR-022 flag, `WARN` if on
  without `GITHUB_TOKEN`; otherwise it shows the retention (`GITHUB_EVENTS_RETENTION_DAYS`).

Flag values and tokens are never printed.

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
   evidence, and clears the title and url of front-page stories stored by the rank poller
   (`hn_story`; M1-T23). The rank history and the story's repo link stay, so front-page minutes
   remain computable; a later poll never refills a cleared title;
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
  The interval can't be under 1 minute (TM-04). Ranks 1–30 are the front page. Every stored
  story is registered for deletion sync (no author is stored), so stories deleted upstream lose
  their title and url (M1-T23).
- **Front-page minutes** (M1-T22, `att.hn_frontpage_minutes`), read-only (no writes, no run
  record; the database session is read-only):
  ```bash
  uv run pigtail report hn-frontpage --repo owner/name [--since 2026-09-20T00] [--until …]
  ```
  Stories are matched by their URL (`github.com/owner/name`). Each poll's ranks count until the
  next poll; a gap between polls longer than 2 × the interval (`--interval-minutes`, default 5;
  `--gap-factor`, default 2) is not counted and is listed under `gaps` / `uncovered_minutes` (per
  story: the gaps that began while it was on the front page). Time before the first poll is
  `before_polling_minutes`. `quality` is `verified` when the window is fully covered, `estimated`
  (a lower bound) when not, and `unknown` (`minutes: null`) when nothing in the window was
  polled. Opted-out repos are refused.
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

### GitHub token and budgets (M1-T24, ADR-032)
Breakout detection uses the GitHub API: hourly star counts for a watch list, Search sweeps,
the star-history endpoint and, optionally, per-repo events for open cases.

**The token.** Create one fine-grained personal access token on your own GitHub account with
access to *public repositories only* and no extra permissions, and put it in the host's `.env`
as `GITHUB_TOKEN=…` (never in git). Use one token only: GitHub's terms forbid sharing or pooling
tokens to exceed rate limits (TM-02), so don't add a second token or a GitHub App to raise them
(ADR-032.4). The token is sent only as an `Authorization` header and is never logged.
**Without `GITHUB_TOKEN` pigtail makes no GitHub API call**: the `capture github` commands exit 2,
and the scheduler skips the `gh_*` jobs with the logged reason `missing_env:GITHUB_TOKEN` (not a
failure, no alert).

**Budgets.** One token has three separate buckets. pigtail caps each at 70 % per UTC hour by
default (validation plan M7) and stops hard (no request sent) when a cap is reached; the job
records `budget_stop` and resumes on its next run.

| Bucket | GitHub limit | Default cap | Override (per hour) | Used by |
|---|---|---|---|---|
| core | 5,000 requests/h | 3,500 | `GITHUB_BUDGET_CORE_PER_HOUR` | star history, per-repo events |
| graphql | 5,000 points/h | 3,500 | `GITHUB_BUDGET_GRAPHQL_PER_HOUR` | watch-list counts (100 repos ≈ 1 point) |
| search | 30 requests/min | 1,260 (21/min) | `GITHUB_BUDGET_SEARCH_PER_HOUR` | Search sweeps |

- The hourly spend is shared by all pigtail processes through the `github_budget_ledger` table.
- `GITHUB_BUDGET_RESERVE_FRACTION` (default 0.30): stop when GitHub reports less than this share
  of a bucket left and the reset is more than 2 minutes away. This also leaves room for anything
  else you run with the same account.
- Each job run has its own cap too (`--max-points`, `--max-requests`; defaults: counts 700
  points, search 400, star history 400, detect-v1 200, repo events 1,600 core requests).
- Rate-limit answers are honoured: `Retry-After`, `X-RateLimit-Reset`, at least 60 s (doubling)
  for secondary limits; requests are serial; ETag `304` answers cost nothing; per-repo events are
  never polled faster than GitHub's `X-Poll-Interval`.
- Expected steady state (replan §6.2, a 50,000-repo watch list): core ≈ 3,055/h (61 %), GraphQL
  ≈ 700/h (14 %), search ≈ 210/h (12 %). Check actual use with
  `uv run pigtail capture github budget --hours 24`.

**Jobs** (`infra/schedule.toml`; all skip until `GITHUB_TOKEN` is set):

| Job | Every | Command |
|---|---|---|
| `gh_watchlist_counts` | 1 h | `capture github watchlist-counts` (watch-list cap `--cap`, default 50,000) |
| `gh_search_sweep` | 6 h | `capture github search-sweep --kind all` |
| `gh_hn_screen` | 1 h | `capture github hn-screen` (HN + Show HN URLs, GH Archive nominations) |
| `gh_star_history_confirm` | 1 h | `capture github star-history --candidates` |
| `gh_detect_v1` | 1 h | `capture github detect-v1` |
| `gh_settle_lag` | 1 h | `capture github settle-lag` (K2 re-fetches; see below) |
| `gh_repo_events` | 15 min | `capture github repo-events` (**off**; see below) |

Add a repo by hand with `uv run pigtail capture github watch-add --repo owner/name`. The GH
Archive `gharchive_scan` job keeps running as the velocity-v0 control; `detect-v1` reports how
often both agree (`agreement_30d`).

**Per-repo events (person-level).** `repo-events` reads `WatchEvent`/`ForkEvent` actors for
repos with an open case, to confirm the bot filter. It is off by default. To turn it on, meet
every ADR-022 precondition (see "Hacker News sources" above; ADR-036), then set
`PIGTAIL_ENABLE_GITHUB_EVENTS=1` and `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` and change
`enabled = true` on `gh_repo_events`. Actors are pseudonymized at ingest; raw event pages are
deleted right after parsing (a page that fails to parse is deleted at once too, CB-23b, and
counted as `repo_events.parse_failed` in the run record); the pseudonymous rows are kept at most
30 days (`GITHUB_EVENTS_RETENTION_DAYS`, default 16, maximum 30; ADR-038) and deleted by
`pigtail retention purge`; only daily aggregates stay (CB-22, CB-23). pigtail never builds or exports a list of a repo's
stargazers.

**settle_lag re-fetches (K2, M4-T4).** The threshold calibration needs to know how much a
star-history day still changes after it ends (pre-registration
`docs/preregistration/2026-09-25-threshold-calibration.md` §2.1 K2). Each day, `gh_settle_lag`
enrols the previous endpoint day for up to 100 repos (`--max-repos`). It then re-fetches that
day at +1, +3, +7, +14 and +21 days after the day ended. Every version is kept in the
append-only `star_history_settle_obs` table. An item fetched more than 24 h late is marked
`missed`.
- Only repos whose cases are **all** in the calibration split are enrolled. Held-out repos never
  are, nor repos without a case or on the refusal list. Pending items of a repo that later opts
  out, or gets a held-out case, are dropped.
- Cost: about one core request per enrolled repo per day (≤ 200 per run).
- This job only collects. The settled-share statistic is computed once, by the calibration.

**Search pages (CB-24).** Search result pages embed owner objects, so they are snapshotted as
person-level and their raw bytes are deleted right after parsing (hash and URL kept, tombstone in
`deletion_log`). Only repo ids, names, counts, dates and the owner *type* (User/Organization) are
kept.

**Day boundaries.** Star-history days are GitHub's own day labels, which are not UTC days
(probably US Pacific; to be confirmed around the DST change on 2026-11-01). Stored rows carry a
`day_boundary_tz` note.

**First runs once the token exists** (validation plan in `docs/research/detection-replan.md` §8):
```bash
uv run pigtail capture github search-sweep --kind new --max-requests 60   # seed the watch list
uv run pigtail capture github hn-screen
uv run pigtail capture github watchlist-counts --max-points 50             # M3: cost per batch
uv run pigtail capture github budget --hours 1                              # M7: ledger
uv run pigtail capture github star-history --candidates --max-requests 50
uv run pigtail capture github detect-v1                                    # needs ≥ 2 count runs
```

### Opt-outs (CB-13)
```bash
uv run pigtail privacy optout add --platform github --handle -       # reads the handle from stdin
uv run pigtail privacy optout add --platform github --repo-id 123456 # a project owner opts out
uv run pigtail privacy optout add --platform github --repo owner/name   # also if not in the DB
uv run pigtail privacy optout list
uv run pigtail privacy optout remove --platform github --handle -
uv run pigtail privacy optout purge     # re-apply the whole list, e.g. after a backup restore
uv run pigtail privacy optout rekey     # CB-13b: convert pre-0009 unkeyed name entries
```
- **Handles are never stored.** A handle is pseudonymized at once with `PSEUDONYM_KEY` in the
  platform's namespace, which is the same pseudonym the connectors store. The database rejects
  anything that isn't a pseudonym. `--handle X` also works, but `--handle -` (or leaving the flag
  out) reads the handle from stdin and keeps it out of your shell history.
- **Ingest:** `capture scan` loads the list, and connectors drop matching records before
  aggregation (counted as `<source>.suppressed` in the run record). Opted-out repos are dropped
  by repo id and, for HN data, the watch list and the Show HN screen, also by normalized
  `owner/name` (M1-T23), so an opt-out reaches repos pigtail doesn't track yet. The name is
  stored only as a **keyed** hash (`rk_…`: HMAC-SHA256 with `PSEUDONYM_KEY`, CB-13b), so the list
  can't be reversed with a dictionary of public repo names; `optout list` never shows the name.
  Matching names needs `PSEUDONYM_KEY`: a capture that finds keyed name entries but no key stops
  (`MissingNameKey`) instead of ingesting opted-out repos. **Changing `PSEUDONYM_KEY`** orphans
  these keys as it does pseudonyms, so re-add the names after a key rotation.
- **Entries from before migration 0009** were unkeyed SHA-256 hashes (`rn_…`). SQL can't convert
  them, because the name isn't stored. Dropping them would resume collecting repos whose owners
  objected, so 0009 keeps them as `repo_name_unkeyed` and ingest still honours them. The migration
  logs a warning with their count, and `pigtail doctor` (and therefore an alert) warns
  (`optout_name_keys`) while any are left. To clear them:
  1. Run `pigtail privacy optout rekey`. It converts every entry whose name is found in local data
     (HN rows, the watch list, `repos`).
  2. Re-add each remaining name with `optout add --repo owner/name`. This writes the keyed entry
     and deletes the unkeyed one. The source is the owner's original request, never the hash.
- **`--repo owner/name`:** if the repo is in the database, it is opted out by id *and* name. If
  not, it is opted out by name only; when it later enters the database, `optout purge` also adds
  its id.
- **Existing data:** `add` purges at once unless you pass `--no-purge`.
  - For a person, this drops the raw snapshots that contain their records (whole snapshots;
    replay re-downloads them and drops the person at ingest), person-level rows, and LLM cache
    rows derived from those snapshots or mentioning the pseudonym.
  - For a repo, it deletes the repo's hourly aggregates, cases, linked evidence (raw bytes
    first) and the `repos` row. By name it also deletes HN mention rows and their snapshots,
    clears the title, url and repo link of rank-poller stories (the rank history keeps only the
    item id), clears Show HN screen links and deactivates the watch-list entry (`opted_out`).
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
**Every `pigtail` command** installs `pigtail.logsafe.RedactingFilter` on the root log handlers
before it runs (CB-18b, in `pigtail.cli.main`), whether you run it by hand or the scheduler starts
it (scheduled jobs run through `python -m pigtail.scheduler.child`, which installs it before any
job code runs; the scheduler scrubs captured job output again). Python warnings go through the
same filter, and the traceback of an uncaught error is scrubbed before it reaches stderr. The
filter replaces handles, e-mails, profile URLs and DIDs with placeholders and truncates long
payloads. Pages that fail to parse are counted in run records by source and exception type only
(`<source>.parse_failed.<Type>`), never with their content. `runs.error` goes through the same
scrubber. When you add a service, call `pigtail.logsafe.configure_logging()` or `install()` on
its handlers.
