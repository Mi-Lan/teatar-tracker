"""Monthly ticket-release season.

Most Belgrade theatres publish next month's programme and open ticket sales between the 20th and
the 27th. At fixed times on each of those days (00:05 and 08:00 by default) the tracker sends one
self-contained status: per theatre, is next month out, and how many dates have tickets.
"🆕" marks what changed since the previous status.

It also records *when* each theatre released: the first check that saw next month's tickets, and
the last check before it that didn't. Checks are hourly (every 30 s around known releases), so
that pins the release time down to that interval. Past releases are kept in state.release_log.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape
from typing import TYPE_CHECKING

from .models import Performance, Status, now

if TYPE_CHECKING:
    from .runner import Ctx

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def next_month(d: date) -> tuple[int, int]:
    return (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)


def season_days(ctx: Ctx) -> tuple[int, int]:
    first, last = ctx.cfg.get("release_season", "days", [20, 27])
    return int(first), int(last)


def slots(ctx: Ctx) -> list[time]:
    return sorted(time.fromisoformat(t) for t in ctx.cfg.get("release_season", "times", ["00:05", "08:00"]))


def due(ctx: Ctx) -> time | None:
    """The status slot to send now, if any. Only the latest due slot of the day is sent."""
    t = now()
    first, last = season_days(ctx)
    if not first <= t.day <= last:
        return None
    passed = [s for s in slots(ctx) if t.time() >= s]
    if not passed:
        return None
    keys = [f"season:{t.date().isoformat()}:{s:%H:%M}" for s in passed]
    if keys[-1] in ctx.state.notified:
        return None
    for k in keys:
        ctx.state.mark_notified(k)
    return passed[-1]


def _counts(perfs: list[Performance]) -> dict:
    return {
        "listed": len(perfs),
        "buyable": sum(p.status.buyable for p in perfs),
        "sold_out": sum(p.status == Status.SOLD_OUT for p in perfs),
        "not_yet": sum(p.status == Status.NOT_ON_SALE for p in perfs),
        "unknown": sum(p.status == Status.UNKNOWN for p in perfs),
    }


def _month_perfs(ctx: Ctx, venue: str, year: int, month: int) -> list[Performance]:
    return [p for p in ctx.state.perfs() if p.venue == venue and p.start.year == year and p.start.month == month]


def _season(ctx: Ctx) -> dict:
    """This month's season record, reset when the target month changes."""
    year, month = next_month(now().date())
    key = f"{year}-{month:02d}"
    if ctx.state.season.get("month") != key:
        ctx.state.season = {"month": key}
    for field in ("reported", "live", "released"):
        ctx.state.season.setdefault(field, {})
    ctx.state.season.pop("venues", None)  # pre-release-tracking format
    return ctx.state.season


def track(ctx: Ctx, scanned_venues: set[str]) -> None:
    """Call after every scan: notices the moment a theatre's next month gets tickets."""
    s = _season(ctx)
    year, month = next_month(now().date())
    first, last = season_days(ctx)
    in_window = first - 1 <= now().day <= last
    stamp = now().isoformat(timespec="seconds")
    for venue in scanned_venues & set(ctx.cfg.venues):
        c = _counts(_month_perfs(ctx, venue, year, month))
        prev = s["live"].get(venue)
        released = c["buyable"] > 0 or (c["listed"] > 0 and c["unknown"] == c["listed"])
        if prev is not None and not prev["out"] and released and venue not in s["released"]:
            s["released"][venue] = {"after": prev.get("checked"), "by": stamp}
            ctx.state.release_log.append({"venue": venue, "month": s["month"], "after": prev.get("checked"), "by": stamp})
        entry = {"out": released}
        # remember when we last saw "not out" (only in the window, to keep state changes rare)
        if not released and in_window:
            entry["checked"] = stamp
        elif prev and prev.get("checked"):
            entry["checked"] = prev["checked"]
        s["live"][venue] = entry


def _when_released(r: dict) -> str:
    by = datetime.fromisoformat(r["by"])
    text = f"out {by:%d.%m. %H:%M}"
    if r.get("after"):
        after = datetime.fromisoformat(r["after"])
        text += f" (not yet at {after:%H:%M})" if after.date() == by.date() or by - after < timedelta(hours=12) else f" (not yet on {after:%d.%m. %H:%M})"
    return text


def report(ctx: Ctx, remember: bool = True, slot: time | None = None) -> list[str]:
    """The status message. `remember` stores these numbers so the next status can mark what's new."""
    s = _season(ctx)
    today = now().date()
    year, month = next_month(today)
    mname = MONTHS[month - 1]
    prev = s.get("reported", {})
    current = {v: _counts(_month_perfs(ctx, v, year, month)) for v in ctx.cfg.venues}

    when = f" · {slot:%H:%M} check" if slot else ""
    lines = [f"🎟 <b>Tickets for {mname}</b> · {DAYS[today.weekday()]} {today:%d.%m.}{when}"]
    for venue, c in current.items():
        name = f"<b>{escape(ctx.cfg.venue_name(venue))}</b>"
        p = prev.get(venue)
        new = ""
        if p is not None:
            if c["buyable"] > 0 and p["buyable"] == 0:
                new = " 🆕"
            elif c["listed"] > p["listed"]:
                new = f" 🆕 +{c['listed'] - p['listed']}"
        rel = f" · {_when_released(s['released'][venue])}" if venue in s.get("released", {}) else ""
        if c["listed"] == 0:
            lines.append(f"⏳ {name}: nothing for {mname} yet")
        elif c["buyable"] == 0 and c["sold_out"] == 0 and c["unknown"] == c["listed"]:
            lines.append(f"📋 {name}: {c['listed']} dates listed (check tickets on their site){new}{rel}")
        elif c["buyable"] == 0 and c["sold_out"] == 0:
            lines.append(f"📋 {name}: programme out ({c['listed']} dates), tickets not on sale yet{new}{rel}")
        else:
            extra = []
            if c["sold_out"]:
                extra.append(f"{c['sold_out']} sold out")
            if c["not_yet"]:
                extra.append(f"{c['not_yet']} not on sale yet")
            tail = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"✅ {name}: tickets for {c['buyable']} of {c['listed']} dates{tail}{new}{rel}")
    first, last = season_days(ctx)
    times = " and ".join(f"{t:%H:%M}" for t in slots(ctx))
    lines += ["", f"Checks at {times} on the {first}.–{last}. · /month for all dates · /theatre <i>name</i> for one theatre"]
    if remember:
        s["reported"] = current
    return ["\n".join(lines)]
