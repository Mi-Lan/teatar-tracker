"""Telegram bot commands. Messages are fetched with getUpdates on every run (and during bursts)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from html import escape
from typing import TYPE_CHECKING

from . import format as fmt
from . import releases as rel
from .diff import watch_entry
from .models import Performance, now
from .textnorm import matches, normalize

if TYPE_CHECKING:
    from .runner import Ctx

log = logging.getLogger(__name__)

HELP = """🎭 <b>Theatre tracker</b>
Replies arrive on the next check (≤15 min, faster around ticket releases).

<b>Browse</b>
/month — everything until the end of next month
/today · /tomorrow · /week — shorter ranges
/overview — everything that's announced
/theatre <i>name</i> — one theatre, e.g. <code>/theatre jdp</code>
/search <i>title</i> — or just type a title

<b>Watchlist</b> (⭐ in lists, extra alerts)
/watch <i>title</i> — e.g. <code>/watch divlje meso</code>
/unwatch <i>number or title</i>
/watchlist

<b>Ticket releases</b>
/release <i>theatre date [time] [note]</i>
   <code>/release jdp 1.10. 10:00 oktobar</code>
   <code>/release narodno 2026-10-05</code> (date only = checks all day)
/releases · /unrelease <i>number</i>

/status — sources and last results"""


def _catalog(ctx: Ctx) -> list[Performance]:
    return [p for p in ctx.state.perfs() if p.venue != "custom" and p.start >= now() - timedelta(hours=3)]


def _names(ctx: Ctx) -> dict[str, str]:
    return {k: ctx.cfg.venue_name(k) for k in ctx.cfg.venues} | {"custom": "Custom lookups"}


def _watched_uids(ctx: Ctx, perfs: list[Performance]) -> set[str]:
    return {p.uid for p in perfs if watch_entry(p, ctx.state.watchlist)}


def resolve_venue(ctx: Ctx, text: str) -> list[str]:
    q = normalize(text)
    if q.replace(" ", "_") in ctx.cfg.venues:
        return [q.replace(" ", "_")]
    return [k for k in ctx.cfg.venues if q and (q in normalize(k) or q in normalize(ctx.cfg.venue_name(k)))]


def _days(ctx: Ctx, start_offset: int, days: int, title: str) -> list[str]:
    today = now().date()
    lo, hi = today + timedelta(days=start_offset), today + timedelta(days=start_offset + days)
    perfs = [p for p in _catalog(ctx) if lo <= p.start.date() < hi]
    return fmt.overview(perfs, _names(ctx), title, _watched_uids(ctx, perfs))


def cmd_help(ctx, arg):
    return [HELP]


def cmd_today(ctx, arg):
    return _days(ctx, 0, 1, "Today")


def cmd_tomorrow(ctx, arg):
    return _days(ctx, 1, 1, "Tomorrow")


def cmd_week(ctx, arg):
    return _days(ctx, 0, 7, "Next 7 days")


def cmd_month(ctx, arg):
    from .runner import end_of_next_month

    today = now().date()
    end = end_of_next_month(today)
    return _days(ctx, 0, (end - today).days + 1, f"Until {end:%d.%m.} (end of next month)")


def cmd_overview(ctx, arg):
    days = int(arg) if arg.strip().isdigit() else 3650
    return _days(ctx, 0, days, "Everything announced" if days == 3650 else f"Next {days} days")


def cmd_theatre(ctx, arg):
    keys = resolve_venue(ctx, arg)
    if len(keys) != 1:
        options = ", ".join(f"<code>{k}</code>" for k in (keys or ctx.cfg.venues))
        return [f"Which theatre? {options}"]
    perfs = [p for p in _catalog(ctx) if p.venue == keys[0]]
    return fmt.overview(perfs, _names(ctx), ctx.cfg.venue_name(keys[0]), _watched_uids(ctx, perfs))


def cmd_search(ctx, arg):
    if not arg.strip():
        return ["Usage: /search <i>title</i>"]
    perfs = [p for p in _catalog(ctx) if matches(arg, p.title)]
    return fmt.by_title(perfs, _names(ctx), f"“{arg.strip()}”")


def cmd_watch(ctx, arg):
    query, _, venue = arg.partition("@")
    query, venue = query.strip(), venue.strip()
    if not query:
        return ["Usage: /watch <i>title</i> (optionally <code>@venue</code>)"]
    entry = {"query": query}
    if venue:
        keys = resolve_venue(ctx, venue)
        if len(keys) != 1:
            return [f"Unknown theatre {escape(venue)!s}. Try one of: " + ", ".join(ctx.cfg.venues)]
        entry["venue"] = keys[0]
    if any(normalize(w["query"]) == normalize(query) and w.get("venue") == entry.get("venue") for w in ctx.state.watchlist):
        return [f"Already watching “{escape(query)}”."]
    ctx.state.watchlist.append(entry)
    hits = [p for p in _catalog(ctx) if watch_entry(p, [entry])]
    msg = f"⭐ Watching “{escape(query)}”. {len(hits)} upcoming performance(s) match right now."
    return [msg] + (fmt.by_title(hits, _names(ctx), "Current matches") if hits else [])


def cmd_unwatch(ctx, arg):
    wl = ctx.state.watchlist
    idx = int(arg) - 1 if arg.strip().isdigit() else next((i for i, w in enumerate(wl) if normalize(w["query"]) == normalize(arg)), -1)
    if not 0 <= idx < len(wl):
        return ["Not found. See /watchlist for numbers."]
    removed = wl.pop(idx)
    return [f"Stopped watching “{escape(removed['query'])}”."]


def cmd_watchlist(ctx, arg):
    wl = ctx.state.watchlist
    if not wl:
        return ["Your watchlist is empty. Add plays with /watch <i>title</i>."]
    catalog = _catalog(ctx)
    lines = ["⭐ <b>Watchlist</b>"]
    for i, w in enumerate(wl, 1):
        hits = [p for p in catalog if watch_entry(p, [w])]
        where = f" @{w['venue']}" if w.get("venue") else ""
        nxt = f" — next {fmt.when(hits[0])} {fmt.badge(hits[0])}" if hits else " — no dates announced"
        lines.append(f"{i}. <b>{escape(w['query'])}</b>{where} · {len(hits)} date(s){nxt}")
    return ["\n".join(lines)]


def cmd_release(ctx, arg):
    parts = arg.split()
    if len(parts) < 2:
        return ["Usage: <code>/release jdp 1.10. 10:00 note</code> (time optional)"]
    keys = resolve_venue(ctx, parts[0])
    if len(keys) != 1:
        return [f"Which theatre? " + ", ".join(f"<code>{k}</code>" for k in (keys or ctx.cfg.venues))]
    has_time = len(parts) > 2 and ":" in parts[2]
    when_text = " ".join(parts[1:3] if has_time else parts[1:2])
    note = " ".join(parts[3:] if has_time else parts[2:])
    try:
        at, all_day = rel.parse_when(when_text)
    except ValueError as exc:
        return [escape(str(exc))]
    r = rel.make_release(keys[0], at, all_day, note)
    if rel.window(r, ctx.cfg).end <= now():
        return ["That time has already passed."]
    if any(x["id"] == r["id"] for x in ctx.state.releases):
        return ["That release is already on the list."]
    ctx.state.notified.pop(f"unrelease:{r['id']}", None)
    ctx.state.releases.append(r)
    ctx.state.releases.sort(key=lambda x: x["at"])
    w = rel.window(r, ctx.cfg)
    plan = f"I'll check every {w.poll_seconds}s from {w.start_poll:%d.%m. %H:%M} to {w.end:%H:%M}"
    return [f"🗓 Release added: {fmt.release_line(r, _names(ctx))}\n{plan}, with reminders before."]


def cmd_releases(ctx, arg):
    if not ctx.state.releases:
        return ["No known ticket releases. Add one with /release."]
    lines = ["🗓 <b>Known ticket releases</b>"]
    lines += [f"{i}. {fmt.release_line(r, _names(ctx))}{' (auto)' if r.get('source') == 'auto' else ' (monthly)' if r.get('source') == 'recurring' else ''}" for i, r in enumerate(ctx.state.releases, 1)]
    return ["\n".join(lines)]


def cmd_unrelease(ctx, arg):
    if not arg.strip().isdigit() or not 0 < int(arg) <= len(ctx.state.releases):
        return ["Usage: /unrelease <i>number</i> (see /releases)"]
    r = ctx.state.releases.pop(int(arg) - 1)
    ctx.state.mark_notified(f"unrelease:{r['id']}")
    return [f"Removed: {fmt.release_line(r, _names(ctx))}"]


def cmd_status(ctx, arg):
    catalog = _catalog(ctx)
    lines = ["🩺 <b>Sources</b>"]
    for key in ctx.cfg.venues:
        n = sum(p.venue == key for p in catalog)
        h = ctx.state.health.get(key, {})
        ok = "⚠️ failing" if h.get("fails") else "✅"
        err = f" — {escape(h.get('last_error', ''))[:80]}" if h.get("fails") else ""
        lines.append(f"{ok} <b>{escape(ctx.cfg.venue_name(key))}</b>: {n} upcoming{err}")
    lines.append(f"\n⭐ {len(ctx.state.watchlist)} watched · 🗓 {len(ctx.state.releases)} releases")
    return ["\n".join(lines)]


COMMANDS = {
    "/start": cmd_help, "/help": cmd_help,
    "/today": cmd_today, "/danas": cmd_today,
    "/tomorrow": cmd_tomorrow, "/sutra": cmd_tomorrow,
    "/week": cmd_week, "/nedelja": cmd_week,
    "/month": cmd_month, "/mesec": cmd_month,
    "/overview": cmd_overview, "/all": cmd_overview, "/pregled": cmd_overview,
    "/theatre": cmd_theatre, "/theater": cmd_theatre, "/pozoriste": cmd_theatre,
    "/search": cmd_search, "/trazi": cmd_search,
    "/watch": cmd_watch, "/prati": cmd_watch,
    "/unwatch": cmd_unwatch,
    "/watchlist": cmd_watchlist,
    "/release": cmd_release,
    "/releases": cmd_releases,
    "/unrelease": cmd_unrelease,
    "/status": cmd_status,
}


def handle(ctx: Ctx, updates: list[dict], skip_start: bool = False) -> int:
    """Answer every pending message from the configured chat. Returns how many were handled.

    `skip_start` drops /start on the very first run, whose welcome message already has the help.
    """
    handled = 0
    for u in updates:
        ctx.state.telegram_offset = max(ctx.state.telegram_offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if chat != str(ctx.tg.chat_id):
            log.warning("ignoring message from unknown chat %s", chat)
            continue
        if not text or (skip_start and text.split("@")[0] == "/start"):
            continue
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            fn = COMMANDS.get(cmd.split("@")[0].lower())
            reply = fn(ctx, arg.strip()) if fn else [f"Unknown command {escape(cmd)}. Send /help."]
        else:
            reply = cmd_search(ctx, text)
        age = now() - datetime.fromtimestamp(msg.get("date", now().timestamp()), tz=now().tzinfo)
        if age > timedelta(minutes=3):
            reply[0] = f"<i>(re: “{escape(text[:60])}”)</i>\n" + reply[0]
        ctx.tg.send_all(reply, chat)
        handled += 1
    return handled
