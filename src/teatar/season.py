"""Monthly ticket-release season.

Most Belgrade theatres publish next month's programme and open ticket sales between the 20th and
the 27th. On each of those mornings the tracker sends one self-contained status: per theatre, is
next month out, and how many shows have tickets. "🆕" marks what changed since the previous day.
"""

from __future__ import annotations

from datetime import date, datetime, time
from html import escape
from typing import TYPE_CHECKING

from . import lookups
from .models import Status, now

if TYPE_CHECKING:
    from .runner import Ctx

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def next_month(d: date) -> tuple[int, int]:
    return (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)


def season_days(ctx: Ctx) -> tuple[int, int]:
    first, last = ctx.cfg.get("release_season", "days", [20, 27])
    return int(first), int(last)


def due(ctx: Ctx) -> bool:
    t = now()
    first, last = season_days(ctx)
    at = time.fromisoformat(ctx.cfg.get("release_season", "time", "09:00"))
    return first <= t.day <= last and t.time() >= at and f"season:{t.date().isoformat()}" not in ctx.state.notified


def counts(ctx: Ctx, year: int, month: int) -> dict[str, dict]:
    out = {}
    perfs = [p for p in ctx.state.perfs() if p.start.year == year and p.start.month == month]
    for venue in ctx.cfg.venues:
        ps = [p for p in perfs if p.venue == venue]
        out[venue] = {
            "listed": len(ps),
            "buyable": sum(p.status.buyable for p in ps),
            "sold_out": sum(p.status == Status.SOLD_OUT for p in ps),
            "not_yet": sum(p.status == Status.NOT_ON_SALE for p in ps),
            "unknown": sum(p.status == Status.UNKNOWN for p in ps),
        }
    return out


def report(ctx: Ctx, remember: bool = True) -> list[str]:
    """The status message. `remember` stores today's numbers so tomorrow can mark what's new."""
    today = now().date()
    year, month = next_month(today)
    key = f"{year}-{month:02d}"
    current = counts(ctx, year, month)
    prev_all = ctx.state.season if ctx.state.season.get("month") == key else {}
    prev = prev_all.get("venues", {})
    mname = MONTHS[month - 1]

    lines = [f"🎟 <b>Tickets for {mname}</b> · {DAYS[today.weekday()]} {today:%d.%m.}"]
    for venue, c in current.items():
        name = f"<b>{escape(ctx.cfg.venue_name(venue))}</b>"
        p = prev.get(venue)
        new = ""
        if p is not None:
            if c["buyable"] > 0 and p["buyable"] == 0:
                new = " 🆕"
            elif c["listed"] > p["listed"]:
                new = f" 🆕 +{c['listed'] - p['listed']}"
        if c["listed"] == 0:
            lines.append(f"⏳ {name}: nothing for {mname} yet")
        elif c["buyable"] == 0 and c["sold_out"] == 0 and c["unknown"] == c["listed"]:
            lines.append(f"📋 {name}: {c['listed']} dates listed (check tickets on their site){new}")
        elif c["buyable"] == 0 and c["sold_out"] == 0:
            lines.append(f"📋 {name}: programme out ({c['listed']} dates), tickets not on sale yet{new}")
        else:
            extra = []
            if c["sold_out"]:
                extra.append(f"{c['sold_out']} sold out")
            if c["not_yet"]:
                extra.append(f"{c['not_yet']} not on sale yet")
            tail = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"✅ {name}: tickets for {c['buyable']} of {c['listed']} dates{tail}{new}")
    first, last = season_days(ctx)
    lines += ["", f"I check every morning {first}.–{last}. · /month for all dates · /theatre <i>name</i> for one theatre"]
    if remember:
        ctx.state.season = {"month": key, "venues": current}
    return ["\n".join(lines)]
