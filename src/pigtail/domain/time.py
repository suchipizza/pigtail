"""TimeRange helpers (spec §6.2). Never invent precision."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {
    m.lower(): i for i, m in enumerate(calendar.month_abbr) if m
}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def day_range(d: date | datetime, precision: str = "day", label: str | None = None) -> dict:
    if isinstance(d, datetime):
        d = d.astimezone(UTC).date()
    start = datetime(d.year, d.month, d.day, tzinfo=UTC)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return {"start": iso(start), "end": iso(end), "precision": precision, "label": label}


def instant(dt: datetime, precision: str = "second", label: str | None = None) -> dict:
    return {"start": iso(dt), "end": iso(dt), "precision": precision, "label": label}


def month_range(year: int, month: int, label: str | None = None) -> dict:
    start = datetime(year, month, 1, tzinfo=UTC)
    last = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last, 23, 59, 59, tzinfo=UTC)
    return {"start": iso(start), "end": iso(end), "precision": "month", "label": label}


def year_range(year: int, label: str | None = None) -> dict:
    return {
        "start": iso(datetime(year, 1, 1, tzinfo=UTC)),
        "end": iso(datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)),
        "precision": "year",
        "label": label,
    }


def unknown_range(label: str | None = None) -> dict:
    return {"start": None, "end": None, "precision": "unknown", "label": label}


_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR = re.compile(r"^(\d{4})$")


def range_from_partial(value: str | None, label: str | None = None) -> dict:
    """Build a TimeRange from 'YYYY', 'YYYY-MM', 'YYYY-MM-DD' or full ISO timestamps.

    Precision follows the input; nothing finer is invented.
    """
    if not value:
        return unknown_range(label)
    v = value.strip()
    if m := _ISO_DAY.match(v):
        try:
            return day_range(date(int(m[1]), int(m[2]), int(m[3])), label=label)
        except ValueError:
            return unknown_range(label)
    if m := _ISO_MONTH.match(v):
        if 1 <= int(m[2]) <= 12:
            return month_range(int(m[1]), int(m[2]), label=label)
        return unknown_range(label)
    if m := _YEAR.match(v):
        return year_range(int(m[1]), label=label)
    dt = parse_dt(v)
    if dt:
        return day_range(dt, label=label)
    return unknown_range(label or v)


def range_start(tr: dict | None) -> datetime | None:
    return parse_dt(tr.get("start")) if tr else None


def human_label(tr: dict | None) -> str:
    if not tr:
        return "Date unknown"
    if tr.get("label"):
        return str(tr["label"])
    start = parse_dt(tr.get("start"))
    end = parse_dt(tr.get("end"))
    p = tr.get("precision")
    if not start:
        return "Date unknown"
    if p == "year":
        return f"{start.year}"
    if p == "month":
        return start.strftime("%b %Y")
    if p == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if p == "range" and end and end.date() != start.date():
        return f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"
    return start.strftime("%b %d, %Y")
