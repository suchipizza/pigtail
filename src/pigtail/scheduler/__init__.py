"""Unattended runtime (M1-T21, health/alerting part of M1-T11; WORK_ORDER §2 "long-running
services must not depend on an agent session being alive").

- `config`:  `infra/schedule.toml` → `ScheduleConfig` (jobs, alert thresholds, health server).
- `state`:   per-job statistics derived from the `runs` table (the persisted scheduler state).
- `locks`:   one Postgres advisory lock per job, so overlapping runs never happen, even across
             several scheduler processes.
- `jobs`:    what each job kind runs (static command, GH Archive window, HN mentions per case).
- `core`:    the loop: due-ness, retry with backoff, one `run` record per attempt.
- `runner`:  each job command runs in a child process with the CB-18 log filter installed.
- `health`:  `pigtail health` and `/healthz` report; `alerts`: rules, sinks, e-mail, export.
"""
