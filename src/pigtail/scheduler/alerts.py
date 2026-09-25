"""Alert rules, sinks and export (M1-T21, M1-T11).

Rules (evaluated on a `HealthReport`):
- `job_stale`: no success for more than `stale_factor` x the job's interval;
- `job_failing`: `consecutive_failures` or more failures in a row;
- `db_down`, `s3_down`: database / snapshot bucket unreachable;
- `disk_high`: `PIGTAIL_DATA_DIR` volume above `disk_percent`;
- `doctor`: any `pigtail doctor` WARN or FAIL;
- `deletion_sla`: deletion-sync re-checks or deletions more than 7 days late (CB-02);
- `login_failures` (CB-30): at least `login_failures` failed UI logins (rate-limited attempts
  included) within `login_window`, from `ui_audit_log`;
- `snapshot_integrity` (CB-30): any snapshot that failed hash verification when the UI served it
  (`snapshot_integrity_failure` audit events) within `integrity_window`.

Sink: `PIGTAIL_DATA_DIR/alerts/` on the host, never in git:
- `ALERTS.md`: human-readable, append-only;
- `alerts.jsonl`: the same events, structured (read by `pigtail alerts export`);
- `state.json`: active alerts, for dedupe and throttling.

Rotation (CB-31): `ALERTS.md` and `alerts.jsonl` are moved together to
`ALERTS.<start>.md` / `alerts.<start>.jsonl` (`<start>` = time of their first event) when either
exceeds `file_max_bytes` or the first event is older than `file_rotate_after` (never longer than
the retention). Archives are deleted once their first event is older than the retention
(`LOG_RETENTION_DAYS`, at most 12 months; CB-18), so no alert line is kept longer than that.

Every line goes through `pigtail.logsafe.scrub` (CB-18), and messages are built from fixed
templates that contain job names, check names, counts and times only.

Throttling: an alert notifies when it starts firing, again at most every `repeat` while it keeps
firing, and once when it resolves. E-mail (optional, stdlib `smtplib`) is sent when `SMTP_URL`
and `ALERT_EMAIL` are set; one message per evaluation that produced events.

`export_summary` writes a sanitized summary for the public repo (`ops/ALERTS.md`): rule, subject,
severity, counts and times only; no message text.
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from pigtail.logsafe import scrub
from pigtail.scheduler.config import AlertConfig, fmt_duration
from pigtail.scheduler.health import HealthReport

log = logging.getLogger("pigtail.scheduler.alerts")

Severity = Literal["warning", "critical"]
EventKind = Literal["firing", "repeat", "resolved"]
LOCAL_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")  # plaintext login allowed
_SAFE = re.compile(r"^[a-z0-9_.:-]{1,64}$")
MD_HEADER = (
    "# pigtail alerts (host-local)\n\n"
    "Written by the pigtail scheduler. Never commit this file; use `pigtail alerts export` for a\n"
    "sanitized summary.\n\n"
)


@dataclass(frozen=True)
class Alert:
    rule: str
    subject: str
    severity: Severity
    message: str

    @property
    def key(self) -> str:
        return f"{self.rule}:{self.subject}"


@dataclass(frozen=True)
class AlertEvent:
    kind: EventKind
    at: datetime
    rule: str
    subject: str
    severity: Severity
    message: str
    count: int = 1

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "at": self.at.isoformat(),
            "rule": self.rule,
            "subject": self.subject,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
        }

    def line(self) -> str:
        return scrub(
            f"- {self.at.isoformat(timespec='seconds')} **{self.kind.upper()}** "
            f"[{self.severity}] {self.rule} `{self.subject}`: {self.message}"
        )


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    return fmt_duration(timedelta(seconds=int(seconds // 60) * 60)) if seconds >= 60 else "<1m"


def evaluate(report: HealthReport, cfg: AlertConfig) -> list[Alert]:
    out: list[Alert] = []
    for j in report.jobs:
        if not j.spec.enabled:
            continue
        name, st = j.spec.name, j.stats
        if j.stale:
            out.append(
                Alert(
                    "job_stale",
                    name,
                    "critical",
                    f"job {name}: last success {_ago(j.since_success_seconds)} ago, more than "
                    f"{cfg.stale_factor:g}x its interval ({fmt_duration(j.spec.every)})",
                )
            )
        if st.consecutive_failures >= cfg.consecutive_failures:
            out.append(
                Alert(
                    "job_failing",
                    name,
                    "critical",
                    f"job {name}: {st.consecutive_failures} consecutive failures "
                    f"({st.failures_24h} in 24 h)",
                )
            )
    checks = {c.name: c for c in report.checks}
    if (c := checks.get("database")) and c.status == "fail":
        out.append(Alert("db_down", "database", "critical", "database unreachable or unset"))
    if (c := checks.get("job_state")) and c.status == "fail":
        out.append(Alert("db_down", "run_log", "critical", "run log (runs table) unavailable"))
    if (c := checks.get("object_store")) and c.status == "fail":
        out.append(Alert("s3_down", "object_store", "critical", "snapshot bucket unreachable"))
    if (c := checks.get("disk")) and c.status == "fail":
        pct = c.value if c.value is not None else 0.0
        out.append(
            Alert(
                "disk_high",
                "data_dir",
                "warning",
                f"PIGTAIL_DATA_DIR volume {pct:.0f}% used (> {cfg.disk_percent:g}%)",
            )
        )
    if (c := checks.get("deletion_sla")) and c.status == "fail":
        out.append(Alert("deletion_sla", "deletion_sync", "critical", f"CB-02 SLA: {c.detail}"))
    if (c := checks.get("ui_login_failures")) and c.status != "ok":
        out.append(Alert("login_failures", "ui", "warning", f"CB-30: {c.detail}"))
    if (c := checks.get("snapshot_integrity")) and c.status == "fail":
        out.append(
            Alert(
                "snapshot_integrity",
                "snapshots",
                "critical",
                f"CB-30: {c.detail}; a stored snapshot no longer matches its hash (check the"
                " snapshot store and ui_audit_log)",
            )
        )
    for d in report.doctor:
        if d.status in ("warn", "fail"):
            sev: Severity = "critical" if d.status == "fail" else "warning"
            out.append(
                Alert("doctor", d.name, sev, f"{d.name} is {d.status.upper()} (run pigtail doctor)")
            )
    return out


# --- notifiers -------------------------------------------------------------------------------
@dataclass(frozen=True)
class SmtpTarget:
    host: str
    port: int
    user: str | None
    password: str | None = field(repr=False)  # CB-29: never in a repr or log
    mode: Literal["plain", "starttls", "ssl"]


def parse_smtp_url(url: str) -> SmtpTarget:
    """`smtp://host[:25]` (STARTTLS if offered), `smtp+starttls://user:pass@host:587`
    (STARTTLS required), `smtps://user:pass@host:465` (implicit TLS)."""
    u = urlparse(url)
    modes: dict[str, tuple[Literal["plain", "starttls", "ssl"], int]] = {
        "smtp": ("plain", 25),
        "smtp+starttls": ("starttls", 587),
        "smtps": ("ssl", 465),
    }
    if u.scheme not in modes or not u.hostname:
        raise ValueError("SMTP_URL must be smtp://, smtp+starttls:// or smtps://host[:port]")
    mode, port = modes[u.scheme]
    return SmtpTarget(
        host=u.hostname,
        port=u.port or port,
        user=unquote(u.username) if u.username else None,
        password=unquote(u.password) if u.password else None,
        mode=mode,
    )


class EmailNotifier:
    def __init__(
        self, smtp_url: str, to: Sequence[str], sender: str | None = None, timeout: float = 15
    ) -> None:
        self.target = parse_smtp_url(smtp_url)
        self.to = [t.strip() for t in to if t.strip()]
        if not self.to:
            raise ValueError("ALERT_EMAIL is empty")
        self.sender = sender or self.to[0]
        self.timeout = timeout

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EmailNotifier | None:
        url, to = env.get("SMTP_URL", "").strip(), env.get("ALERT_EMAIL", "").strip()
        if not url or not to:
            return None
        return cls(url, to.split(","), env.get("ALERT_EMAIL_FROM") or None)

    def __call__(self, events: Sequence[AlertEvent]) -> None:
        firing = sum(1 for e in events if e.kind != "resolved")
        msg = EmailMessage()
        msg["Subject"] = f"[pigtail] {firing} alert(s) firing, {len(events) - firing} resolved"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.to)
        msg.set_content("\n".join(e.line() for e in events) + "\n")
        t = self.target
        ctx = ssl.create_default_context()
        smtp: smtplib.SMTP
        if t.mode == "ssl":
            smtp = smtplib.SMTP_SSL(t.host, t.port, timeout=self.timeout, context=ctx)
        else:
            smtp = smtplib.SMTP(t.host, t.port, timeout=self.timeout)
        with smtp:
            smtp.ehlo()
            tls = t.mode == "ssl"
            if t.mode == "starttls" or (t.mode == "plain" and smtp.has_extn("starttls")):
                smtp.starttls(context=ctx)
                smtp.ehlo()
                tls = True
            if t.user:
                if not tls and t.host not in LOCAL_HOSTS:
                    raise RuntimeError("refusing SMTP login without TLS to a remote host")
                smtp.login(t.user, t.password or "")
            smtp.send_message(msg)


Notifier = Callable[[Sequence[AlertEvent]], None]


# --- manager ---------------------------------------------------------------------------------
ARCHIVE_RE = re.compile(r"^(ALERTS|alerts)\.(\d{8}T\d{6}Z)(?:-(\d+))?\.(md|jsonl)$")
ALERT_FILE_MAX_BYTES = 1_000_000
ALERT_FILE_ROTATE_AFTER = timedelta(days=30)
ALERT_RETENTION_MAX = timedelta(days=365)  # CB-18 / retention-policy: logs at most 12 months


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class AlertManager:
    def __init__(
        self,
        directory: Path,
        repeat: timedelta = timedelta(hours=6),
        notifiers: Sequence[Notifier] = (),
        *,
        max_bytes: int = ALERT_FILE_MAX_BYTES,
        rotate_after: timedelta = ALERT_FILE_ROTATE_AFTER,
        retention: timedelta = ALERT_RETENTION_MAX,
    ) -> None:
        if retention > ALERT_RETENTION_MAX:
            raise ValueError("alert file retention is at most 365 days (CB-18)")
        self.dir = directory
        self.repeat = repeat
        self.notifiers = list(notifiers)
        self.max_bytes = max_bytes
        self.retention = max(retention, timedelta(days=1))
        self.rotate_after = min(rotate_after, self.retention)

    @property
    def md_path(self) -> Path:
        return self.dir / "ALERTS.md"

    @property
    def jsonl_path(self) -> Path:
        return self.dir / "alerts.jsonl"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    # --- rotation (CB-31) ------------------------------------------------------------------
    def _first_event_at(self) -> datetime | None:
        try:
            with self.jsonl_path.open(encoding="utf-8") as f:
                first = f.readline()
            return datetime.fromisoformat(str(json.loads(first)["at"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def archives(self) -> list[tuple[datetime, Path]]:
        """Rotated files with the time of their first event, oldest first."""
        out: list[tuple[datetime, Path]] = []
        try:
            entries = list(self.dir.iterdir())
        except OSError:
            return out
        for p in entries:
            m = ARCHIVE_RE.match(p.name)
            if m and p.is_file():
                at = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                out.append((at, p))
        return sorted(out, key=lambda x: (x[0], x[1].name))

    def rotate(self, now: datetime) -> dict[str, list[str]]:
        """Rotate the live files if too big or too old, then delete archives past retention."""
        rotated: list[str] = []
        live = [p for p in (self.md_path, self.jsonl_path) if p.exists()]
        if live:
            start = self._first_event_at()
            size = max(p.stat().st_size for p in live)
            too_old = start is not None and now - start >= self.rotate_after
            if size >= self.max_bytes or too_old:
                stamp = _stamp(start or now)
                suffix = ""
                n = 0
                while any(
                    (self.dir / f"{stem}.{stamp}{suffix}.{ext}").exists()
                    for stem, ext in (("ALERTS", "md"), ("alerts", "jsonl"))
                ):
                    n += 1
                    suffix = f"-{n}"
                for path, stem, ext in (
                    (self.md_path, "ALERTS", "md"),
                    (self.jsonl_path, "alerts", "jsonl"),
                ):
                    if path.exists():
                        target = self.dir / f"{stem}.{stamp}{suffix}.{ext}"
                        path.replace(target)
                        os.chmod(target, 0o600)
                        rotated.append(target.name)
        deleted: list[str] = []
        cutoff = now - self.retention
        for at, p in self.archives():
            if at < cutoff:
                p.unlink(missing_ok=True)
                deleted.append(p.name)
        return {"rotated": rotated, "deleted": deleted}

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self, state: Mapping[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)

    def process(self, alerts: Iterable[Alert], now: datetime) -> list[AlertEvent]:
        """Dedupe/throttle `alerts` against the stored state; write and send the events."""
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        state = self._load_state()
        events: list[AlertEvent] = []
        current = {a.key: a for a in alerts}
        for key, a in current.items():
            msg = scrub(a.message, limit=300)
            s = state.get(key)
            if s is None:
                state[key] = {
                    "rule": a.rule,
                    "subject": a.subject,
                    "severity": a.severity,
                    "first_seen": now.isoformat(),
                    "last_seen": now.isoformat(),
                    "last_notified": now.isoformat(),
                    "count": 1,
                }
                events.append(AlertEvent("firing", now, a.rule, a.subject, a.severity, msg))
                continue
            s["count"] = int(s.get("count", 0)) + 1
            s["last_seen"] = now.isoformat()
            s["severity"] = a.severity
            last = datetime.fromisoformat(str(s.get("last_notified", s["first_seen"])))
            if now - last >= self.repeat:
                s["last_notified"] = now.isoformat()
                events.append(
                    AlertEvent("repeat", now, a.rule, a.subject, a.severity, msg, s["count"])
                )
        for key in [k for k in state if k not in current]:
            s = state.pop(key)
            events.append(
                AlertEvent(
                    "resolved",
                    now,
                    str(s["rule"]),
                    str(s["subject"]),
                    s.get("severity", "warning"),
                    f"cleared after {s.get('count', 1)} evaluation(s) since {s['first_seen']}",
                    int(s.get("count", 1)),
                )
            )
        self._save_state(state)
        try:
            self.rotate(now)
        except OSError as err:  # never lose an alert over housekeeping
            log.warning("alert file rotation failed: %s", type(err).__name__)
        if events:
            self._write(events)
            for e in events:
                log.warning("alert %s", e.line())
            for n in self.notifiers:
                try:
                    n(events)
                except Exception as err:  # e-mail is best effort; the file sink is authoritative
                    log.warning("alert notifier failed: %s", type(err).__name__)
        return events

    def _write(self, events: Sequence[AlertEvent]) -> None:
        new = not self.md_path.exists()
        with self.md_path.open("a", encoding="utf-8") as f:
            if new:
                f.write(MD_HEADER)
            for e in events:
                f.write(e.line() + "\n")
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e.to_json(), sort_keys=True) + "\n")
        for p in (self.md_path, self.jsonl_path):
            os.chmod(p, 0o600)

    def active(self) -> dict[str, dict[str, Any]]:
        return self._load_state()


# --- export (for an agent session to copy into the public repo) ------------------------------
def _safe(value: Any) -> str:
    v = str(value)
    return v if _SAFE.match(v) else "redacted"


def _ts(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "unknown"


def export_summary(directory: Path, now: datetime, since: timedelta | None = None) -> str:
    """Markdown summary of alert events: rule, subject, severity, counts and times only."""
    mgr = AlertManager(directory)
    cutoff = now - since if since else None
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    lines: list[str] = []
    paths = [p for _, p in mgr.archives() if p.suffix == ".jsonl"] + [mgr.jsonl_path]
    for path in paths:  # CB-31: rotated archives first, then the live file
        try:
            lines += path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
    for raw in lines:
        try:
            e = json.loads(raw)
            at = datetime.fromisoformat(str(e["at"]))
        except (ValueError, KeyError, TypeError):
            continue
        if cutoff and at < cutoff:
            continue
        key = (_safe(e.get("rule")), _safe(e.get("subject")))
        g = groups.setdefault(
            key, {"severity": "warning", "firing": 0, "resolved": 0, "first": at, "last": at}
        )
        if e.get("severity") == "critical":
            g["severity"] = "critical"
        g["resolved" if e.get("kind") == "resolved" else "firing"] += 1
        g["first"], g["last"] = min(g["first"], at), max(g["last"], at)
    active = {(_safe(s.get("rule")), _safe(s.get("subject"))) for s in mgr.active().values()}
    window = f"last {fmt_duration(since)}" if since else "all recorded"
    out = [
        "# Alerts (sanitized export)",
        "",
        f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')} by `pigtail alerts export` from the "
        f"host-local alert log ({window}). Rule, subject, severity, counts and times only; the",
        "full log stays on the host (`PIGTAIL_DATA_DIR/alerts/`).",
        "",
    ]
    if not groups and not active:
        out.append("No alerts in this window.")
        return "\n".join(out) + "\n"
    out += [
        "| rule | subject | severity | state now | notifications | resolved | first | last |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (rule, subject), g in sorted(groups.items()):
        state = "firing" if (rule, subject) in active else "resolved"
        out.append(
            f"| {rule} | {subject} | {g['severity']} | {state} | {g['firing']} | "
            f"{g['resolved']} | {_ts(g['first'].isoformat())} | {_ts(g['last'].isoformat())} |"
        )
    return "\n".join(out) + "\n"
