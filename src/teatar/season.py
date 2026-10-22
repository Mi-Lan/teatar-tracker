"""Monthly ticket-release season, and "when did the tickets come out".

Most Belgrade theatres publish next month's programme and open ticket sales between the 20th and
the 27th. At fixed times on each of those days (00:05 and 08:00 by default) the tracker sends one
self-contained status: per theatre, is next month out, and how many dates have tickets.
"🆕" marks what changed since the previous status.

After every scan, `track` also looks for a *release*: a theatre whose next month just got its first
tickets, or any theatre that put out a batch of new dates / opened sales for many dates at once
(`release_season.batch_min`, default 5). Each release is messaged right away with *when* it
happened, pinned down between the previous check that didn't see it and the one that did, and is
kept in state.release_log. `recent` (the /new command) lists what came out in the last days.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from html import escape
from typing import TYPE_CHECKING

from . import format as fmt
from . import releases as rel
from .diff import ONCE, Change, Kind
from .models import Performance, Status, now

if TYPE_CHECKING:
    from .runner import Ctx

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def next_month(d: date) -> tuple[int, int]:
    return (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)


def month_name(key: str, short: bool = False) -> str:
    """'2026-11' -> 'November' (or 'Nov')."""
    name = MONTHS[int(key[5:7]) - 1]
    return name[:3] if short else name


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


def _month_perfs(ctx: Ctx, venue: str, key: str) -> list[Performance]:
    """Performances at `venue` in the month `key` ('2026-11')."""
    return [p for p in ctx.state.perfs() if p.venue == venue and p.start.strftime("%Y-%m") == key]


def _is_out(c: dict) -> bool:
    """Tickets count as out when something is buyable, or the source shows dates without ticket info."""
    return c["buyable"] > 0 or (c["listed"] > 0 and c["unknown"] == c["listed"])


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


# --- release detection ------------------------------------------------------------------------------


def _batches(changes: list[Change]) -> tuple[dict[str, dict[str, Counter]], dict[str, Counter]]:
    """From this scan's fresh new-show/new-date/sales-opened changes: venue -> month -> {new, opened, on_sale},
    and venue -> title -> number of dates."""
    out: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    titles: dict[str, Counter] = defaultdict(Counter)
    for c in changes:
        if c.kind not in ONCE:
            continue
        titles[c.perf.venue][c.perf.title] += 1
        b = out[c.perf.venue][c.perf.start.strftime("%Y-%m")]
        if c.kind == Kind.SALES_OPENED:
            b["opened"] += 1
            b["on_sale"] += 1
        else:
            b["new"] += 1
            b["on_sale"] += c.perf.status.buyable
    return out, titles


def track(ctx: Ctx, scanned_venues: set[str], changes: list[Change] | None = None) -> None:
    """Call after every scan: notices (and messages) the moment a theatre releases tickets.

    A release is next month's first tickets at a theatre, a batch of at least
    `release_season.batch_min` new dates / sales openings in one check, or any new tickets at a
    venue with a known release around now (/release, releases.yaml). The time is bracketed by this
    check and the previous one of that venue (state.scanned).
    """
    s = _season(ctx)
    target = s["month"]
    stamp = now().isoformat(timespec="seconds")
    batch_min = int(ctx.cfg.get("release_season", "batch_min", 5))
    hot = rel.hot_venues(ctx.state, ctx.cfg)
    batches, titles = _batches(changes or [])
    for venue in sorted(scanned_venues & set(ctx.cfg.venues)):
        after = ctx.state.scanned.get(venue)
        c = _counts(_month_perfs(ctx, venue, target))
        prev = s["live"].get(venue)
        out = _is_out(c)
        transition = prev is not None and not prev["out"] and out
        s["live"][venue] = {"out": out}
        ctx.state.scanned[venue] = stamp

        months = batches.get(venue, {})
        size = sum(b["new"] + b["opened"] for b in months.values())
        if transition and target not in months:
            months[target] = Counter()
        if not transition and size < (1 if venue in hot else batch_min):
            continue
        entries = []
        for month in sorted(months):
            b = months[month]
            mc = _counts(_month_perfs(ctx, venue, month))
            entry = {
                "venue": venue, "month": month, "after": after, "by": stamp,
                "new": b["new"], "opened": b["opened"], "on_sale": b["on_sale"],
                "listed": mc["listed"], "buyable": mc["buyable"], "unknown": mc["unknown"],
            }
            ctx.state.release_log.append(entry)
            entries.append(entry)
            if month == target and venue not in s["released"] and _is_out(mc):
                s["released"][venue] = {"after": after, "by": stamp}
        key = f"drop:{venue}:{stamp[:10]}"
        if not ctx.state.was_notified(key):  # one message per theatre per day; later batches are only logged
            ctx.state.mark_notified(key)
            ctx.tg.send(release_message(ctx, venue, entries, titles.get(venue)))


def span(after: str | None, by: str) -> str:
    """'between 12:00 and 17:02' — the checks that bracket a release; with dates when they differ."""
    b = datetime.fromisoformat(by)
    if not after:
        return f"first seen {b:%d.%m. %H:%M}"
    a = datetime.fromisoformat(after)
    if a.date() == b.date():
        return f"between {a:%H:%M} and {b:%H:%M}"
    return f"between {a:%d.%m. %H:%M} and {b:%d.%m. %H:%M}"


def _month_line(e: dict, show_month: bool = True) -> str:
    parts = []
    if e.get("new"):
        parts.append(f"+{e['new']} new date{'s' if e['new'] != 1 else ''}")
    if e.get("opened"):
        parts.append(f"{e['opened']} went on sale")
    head = f"{month_name(e['month'])}: " if show_month else ""
    what = ", ".join(parts) or "tickets out"
    status = f"{e['listed']} listed, {e['buyable']} on sale" if e.get("buyable") or e.get("on_sale") else f"{e['listed']} listed, none on sale yet"
    return f"{head}{what} → {status}"


def titles_line(titles: Counter, limit: int = 6) -> str:
    top = titles.most_common(limit)
    shown = ", ".join(f"{escape(t)}{f' ×{n}' if n > 1 else ''}" for t, n in top)
    more = len(titles) - len(top)
    return f"{shown}{f', +{more} more' if more else ''}"


def release_message(ctx: Ctx, venue: str, entries: list[dict], titles: Counter | None = None) -> str:
    name = escape(ctx.cfg.venue_name(venue))
    on_sale = sum(e["on_sale"] for e in entries)
    target = _season(ctx)["month"]
    if on_sale == 0 and all(e["buyable"] == 0 for e in entries):
        if all(e.get("unknown") == e["listed"] for e in entries):
            head = f"📋 <b>{name}</b> · new dates listed (check tickets on their site)"
        else:
            head = f"📋 <b>{name}</b> · new dates listed, tickets not on sale yet"
    elif len(entries) == 1 and entries[0]["month"] == target:
        head = f"🎟 <b>{name}</b> · tickets for <b>{month_name(target)}</b> are out"
    else:
        head = f"🎟 <b>{name}</b> · new tickets out"
    lines = [head] + [_month_line(e) for e in entries]
    if titles:
        lines.append(titles_line(titles))
    e = entries[0]
    lines.append(f"Appeared {datetime.fromisoformat(e['by']):%d.%m.} {span(e['after'], e['by'])} · /theatre {venue} · /new")
    return "\n".join(lines)


# --- the 20th–27th status -------------------------------------------------------------------------------


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
    mname = month_name(s["month"])
    prev = s.get("reported", {})
    current = {v: _counts(_month_perfs(ctx, v, s["month"])) for v in ctx.cfg.venues}

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
    lines += ["", f"Checks at {times} on the {first}.–{last}. · /new for what came out recently · /month for all dates"]
    if remember:
        s["reported"] = current
    return ["\n".join(lines)]


# --- /new: what came out recently --------------------------------------------------------------------------


def recent(ctx: Ctx, days: int = 7) -> list[str]:
    """What appeared in the last `days` days, newest day first: per theatre, how many dates (by month),
    how many went on sale, when (bracketed by checks where known), and the titles."""
    since = (now() - timedelta(days=days)).isoformat(timespec="seconds")
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"new": Counter(), "opened": Counter(), "on_sale": 0, "titles": Counter(), "uids": set(), "log": []})
    for p in ctx.state.perfs():
        if p.venue not in ctx.cfg.venues or p.start < now() - timedelta(hours=3):
            continue
        m = ctx.state.meta.get(p.uid, {})
        month = p.start.strftime("%Y-%m")
        events = [("new", m.get("first_seen", "")), ("opened", m.get("on_sale_since", ""))]
        for kind, stamp in events:
            if not stamp or stamp < since:
                continue
            g = groups[(stamp[:10], p.venue)]
            g[kind][month] += 1
            if kind == "new":
                g["on_sale"] += p.status.buyable
            if p.uid not in g["uids"]:
                g["uids"].add(p.uid)
                g["titles"][p.title] += 1
            g["last"] = max(g.get("last", ""), stamp)
    for e in ctx.state.release_log:
        if e["by"] >= since:
            groups[(e["by"][:10], e["venue"])]["log"].append(e)

    blocks = []
    for day in sorted({d for d, _ in groups}, reverse=True):
        lines = [f"📅 <b>{fmt.day_label(date.fromisoformat(day))}</b>"]
        for venue in ctx.cfg.venues:
            g = groups.get((day, venue))
            if not g:
                continue
            name = f"<i>{escape(ctx.cfg.venue_name(venue))}</i>"
            parts = []
            if g["new"]:
                total = sum(g["new"].values())
                by_month = ", ".join(f"{month_name(k, short=True)} {n}" for k, n in sorted(g["new"].items()))
                parts.append(f"+{total} date{'s' if total != 1 else ''} ({by_month})" + (f", {g['on_sale']} on sale" if g["on_sale"] else ", not on sale yet"))
            if g["opened"]:
                total = sum(g["opened"].values())
                by_month = ", ".join(f"{month_name(k, short=True)} {n}" for k, n in sorted(g["opened"].items()))
                parts.append(f"{total} went on sale ({by_month})")
            if g["log"]:
                after = min((e["after"] for e in g["log"] if e.get("after")), default=None)
                when = span(after, max(e["by"] for e in g["log"]))
                if not parts:  # the dates themselves are gone from state (e.g. an older month); the log remains
                    parts.append(", ".join(
                        f"{month_name(e['month'])} out: {e['listed']} dates, {e['buyable']} on sale" if "listed" in e else f"{month_name(e['month'])} tickets out"
                        for e in g["log"]
                    ))
            else:
                when = f"at {datetime.fromisoformat(g['last']):%H:%M}" if g.get("last") else ""
            lines.append(f"{name} · {'; '.join(parts)}" + (f" · {when}" if when else ""))
            if g["titles"]:
                lines.append(f"  {titles_line(g['titles'])}")
        blocks.append("\n".join(lines))
    head = f"🆕 <b>New in the last {days} days</b>"
    if not blocks:
        return [f"{head}\n\nNothing new. Try /new 30 for a longer look back."]
    return fmt.pack(blocks, header=f"{head}\n+N = dates that appeared · times are the checks that first saw them")
