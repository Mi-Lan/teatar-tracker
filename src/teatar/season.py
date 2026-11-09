"""Monthly ticket-release season, and "when did the tickets come out".

Most Belgrade theatres publish next month's programme and open ticket sales between the 20th and
the 27th. At fixed times on each of those days (00:05 and 08:00 by default) the tracker sends one
self-contained status: per theatre, is next month out, and how many dates have tickets.
"🆕" marks what changed since the previous status.

After every scan, `track` also looks for a *release*: a theatre whose next month just got its first
tickets, or any theatre that put out a batch of new dates / opened sales for many dates at once
(`release_season.batch_min`, default 1: any new date). Each release is messaged right away with *when* it
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
from . import sales
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


def _batches(changes: list[Change]) -> tuple[dict[str, dict[str, Counter]], dict[str, list[Change]]]:
    """From this scan's fresh new-show/new-date/sales-opened changes: venue -> month -> {new, opened, on_sale},
    and venue -> those changes."""
    out: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    found: dict[str, list[Change]] = defaultdict(list)
    for c in changes:
        if c.kind not in ONCE:
            continue
        found[c.perf.venue].append(c)
        b = out[c.perf.venue][c.perf.start.strftime("%Y-%m")]
        if c.kind == Kind.SALES_OPENED:
            b["opened"] += 1
            b["on_sale"] += 1
        else:
            b["new"] += 1
            b["on_sale"] += c.perf.status.buyable
    return out, found


def track(ctx: Ctx, scanned_venues: set[str], changes: list[Change] | None = None) -> None:
    """Call after every scan: notices (and messages) the moment a theatre releases tickets.

    A release is next month's first tickets at a theatre, a batch of at least
    `release_season.batch_min` new dates / sales openings in one check (default 1), or any new tickets at a
    venue with a known release around now (/release, releases.yaml). The time is bracketed by this
    check and the previous one of that venue (state.scanned).

    With a `batch_min` above 1, smaller batches still get a message when they include a play you
    /watch or one that sells out fast (`selling.alert`).
    """
    s = _season(ctx)
    target = s["month"]
    stamp = now().isoformat(timespec="seconds")
    batch_min = int(ctx.cfg.get("release_season", "batch_min", 1))
    hot = rel.hot_venues(ctx.state, ctx.cfg)
    batches, found = _batches(changes or [])
    fast = sales.fast_plays(ctx.state, ctx.cfg)
    send_all = ctx.cfg.get("alerts", "instant", "releases_and_watchlist") == "all"  # then process() sent every change
    fast_alerts = ctx.cfg.get("selling", "alert", True)
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
        if transition or size >= (1 if venue in hot else batch_min):
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
            # one message per theatre per check, listing just what this check found (each date is only ever
            # reported once, so a release that trickles in over several checks never repeats a date)
            ctx.tg.send_all(release_message(ctx, venue, entries, found.get(venue, []), fast, first=transition))
            continue
        if send_all:
            continue
        follow = [ch for ch in found.get(venue, []) if ch.watched or (fast_alerts and sales.lookup(fast, ch.perf))]
        if follow:
            ctx.tg.send_all(fast_message(ctx, venue, follow, after, stamp, fast))


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
    if e.get("buyable") or e.get("on_sale"):
        status = f"{e['listed']} listed, {e['buyable']} on sale"
    elif e.get("listed") and e.get("unknown") == e["listed"]:
        status = f"{e['listed']} listed"  # the source doesn't say whether tickets are on sale
    else:
        status = f"{e['listed']} listed, none on sale yet"
    return f"{head}{what} → {status}"


def titles_line(titles: Counter, limit: int = 6, links: dict[str, str] | None = None, marks: dict[str, str] | None = None) -> str:
    """'Edip ×2, Gubitnik, +3 more', titles linked to `links[title]`, prefixed with `marks[title]` (🔥)."""
    links, marks = links or {}, marks or {}
    top = titles.most_common(limit)
    shown = ", ".join(f"{marks[t] + ' ' if t in marks else ''}{fmt.a(links.get(t, ''), escape(t))}{f' ×{n}' if n > 1 else ''}" for t, n in top)
    more = len(titles) - len(top)
    return f"{shown}{f', +{more} more' if more else ''}"


def appeared(after: str | None, by: str) -> str:
    """'Appeared 05.10. between 14:02 and 17:02' (or 'First seen 27.10. 17:44' with no earlier check)."""
    if not after:
        return f"First seen {datetime.fromisoformat(by):%d.%m. %H:%M}"
    return f"Appeared {datetime.fromisoformat(by):%d.%m.} {span(after, by)}"


def plays_list(perfs: list[Performance], fast: dict[str, sales.Play], watched: set[str] | None = None) -> list[str]:
    """One block per play (see fmt.play_block): ⭐ plays you watch and 🔥 plays that sell out fast first,
    then by first date."""
    watched = watched or set()
    groups = sales.by_play(perfs)
    multi_stage = len({p.stage for p in perfs if p.stage}) > 1

    def flagged(ps):
        return any(p.uid in watched for p in ps) or sales.lookup(fast, ps[0]) is not None

    blocks = []
    for ps in sorted(groups.values(), key=lambda ps: (not flagged(ps), ps[0].start)):
        play = sales.lookup(fast, ps[0])
        mark = ("⭐" if any(p.uid in watched for p in ps) else "") + (play.mark if play else "")
        blocks.append(fmt.play_block(ps, mark, play.note if play else "", multi_stage))
    return blocks


def _footer(ctx: Ctx, venue: str) -> str:
    parts = [fmt.site_link(ctx.cfg.venue_website(venue)), f"/theatre {venue}", "/new"]
    return " · ".join(p for p in parts if p)


def release_message(
    ctx: Ctx, venue: str, entries: list[dict], found: list[Change] | None = None, fast: dict | None = None, first: bool = False,
) -> list[str]:
    """The release message: what came out (per month), when, every new play and date (each linked to
    its tickets), ⭐ on plays you watch, 🔥 on plays that sell out fast, and a link to the theatre's site.
    `first`: next month's first tickets at this theatre ("tickets for December are out")."""
    found = found or []
    perfs = [ch.perf for ch in found]
    watched = {ch.perf.uid for ch in found if ch.watched}
    fast = fast if fast is not None else sales.fast_plays(ctx.state, ctx.cfg)
    name = escape(ctx.cfg.venue_name(venue))
    on_sale = sum(e["on_sale"] for e in entries)
    target = _season(ctx)["month"]
    if on_sale == 0 and all(e["buyable"] == 0 for e in entries):
        if all(e.get("unknown") == e["listed"] for e in entries):
            head = f"📋 <b>{name}</b> · new dates listed (check tickets on their site)"
        else:
            head = f"📋 <b>{name}</b> · new dates listed, tickets not on sale yet"
    elif first:
        head = f"🎟 <b>{name}</b> · tickets for <b>{month_name(target)}</b> are out"
    else:
        head = f"🎟 <b>{name}</b> · new tickets out"
    e = entries[0]
    lines = [head] + [_month_line(x) for x in entries] + [appeared(e["after"], e["by"])]
    hot = sum(1 for ps in sales.by_play(perfs).values() if sales.lookup(fast, ps[0]))
    if hot:
        lines.append(f"🔥 {hot} of them sell{'s' if hot == 1 else ''} out fast: get tickets early")
    blocks = plays_list(perfs, fast, watched)
    if blocks:
        blocks[0] = "\n" + blocks[0]
    return fmt.pack(blocks + ["\n" + _footer(ctx, venue)], header="\n".join(lines), joiner="\n", cont=f"{head.split(' · ')[0]} · continued")


def fast_message(ctx: Ctx, venue: str, found: list[Change], after: str | None, by: str, fast: dict) -> list[str]:
    """New dates, or tickets going on sale, for plays you watch or that sell out fast, when the check's
    batch was too small for a release message (release_season.batch_min above 1)."""
    perfs = [ch.perf for ch in found]
    watched = {ch.perf.uid for ch in found if ch.watched}
    name = escape(ctx.cfg.venue_name(venue))
    what = "tickets on sale" if any(p.status.buyable for p in perfs) else "new dates (not on sale yet)"
    plural = len(sales.by_play(perfs)) > 1
    if all(p.uid in watched for p in perfs):
        icon, whom = "⭐", "plays you watch" if plural else "a play you watch"
    elif not watched:
        icon, whom = "🔥", "plays that sell out fast" if plural else "a play that sells out fast"
    else:
        icon, whom = "⭐", "plays you follow"
    n = len(perfs)
    lines = [f"{icon} <b>{name}</b> · {what} for {whom}", f"{n} date{'s' if n != 1 else ''} · {appeared(after, by)}"]
    blocks = plays_list(perfs, fast, watched)
    blocks[0] = "\n" + blocks[0]
    return fmt.pack(blocks + ["\n" + _footer(ctx, venue)], header="\n".join(lines), joiner="\n", cont=f"{icon} <b>{name}</b> · continued")


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
    fast = sales.fast_plays(ctx.state, ctx.cfg)
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"new": Counter(), "opened": Counter(), "on_sale": 0, "titles": Counter(), "links": {}, "marks": {}, "uids": set(), "log": []}
    )
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
                g["links"].setdefault(p.title, p.url or p.buy_url)
                if sales.lookup(fast, p):
                    g["marks"][p.title] = "🔥"
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
                lines.append(f"  {titles_line(g['titles'], 8, g['links'], g['marks'])}")
        blocks.append("\n".join(lines))
    head = f"🆕 <b>New in the last {days} days</b>"
    if not blocks:
        return [f"{head}\n\nNothing new. Try /new 30 for a longer look back."]
    legend = "+N = dates that appeared · times are the checks that first saw them" + (" · 🔥 sells out fast (/hot)" if fast else "")
    return fmt.pack(blocks, header=f"{head}\n{legend}")
