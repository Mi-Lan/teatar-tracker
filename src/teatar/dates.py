"""Calendar helpers shared by the adapters, release windows and messages (everything is Europe/Belgrade)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from .models import now

UPCOMING_GRACE = timedelta(hours=3)  # a performance stays "upcoming" this long after it starts
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def month_key(d: date | datetime) -> str:
    """'2026-11': the key months are stored and grouped under."""
    return f"{d.year:04d}-{d.month:02d}"


def add_months(d: date, n: int) -> tuple[int, int]:
    """(year, month) of the month `n` months after the one `d` is in."""
    years, month = divmod(d.month - 1 + n, 12)
    return d.year + years, month + 1


def months_ahead(count: int, today: date | None = None) -> list[tuple[int, int]]:
    """(year, month) for this month and the `count` months after it."""
    today = today or now().date()
    return [add_months(today, i) for i in range(int(count) + 1)]


def day_in_month(year: int, month: int, day: int) -> date:
    """That day of the month, or the month's last day when it's shorter (the 31st in April → the 30th)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def end_of_next_month(d: date) -> date:
    year, month = add_months(d, 1)
    return day_in_month(year, month, 31)


def until_end_of_next_month(today: date | None = None) -> tuple[date, str]:
    """The last day the default overview covers, and its title."""
    end = end_of_next_month(today or now().date())
    return end, f"Until {end:%d.%m.} (end of next month)"


def parse_time(value) -> time:
    """'08:00' → time(8, 0). Also takes what YAML makes of an unquoted time: `8:00` loads as the
    number 480 (base 60), so a number is read as minutes since midnight."""
    if isinstance(value, time):
        return value
    if isinstance(value, bool):
        raise ValueError(f"bad time {value!r} (use e.g. \"08:00\")")
    if isinstance(value, int):
        if not 0 <= value < 24 * 60:
            raise ValueError(f"bad time {value!r} (use e.g. \"08:00\")")
        return time(*divmod(value, 60))
    try:
        return time.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"bad time {value!r} (use e.g. \"08:00\")") from None
