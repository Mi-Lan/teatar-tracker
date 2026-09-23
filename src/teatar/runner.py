"""One tracker run: scan → detect changes → notify → answer commands → (burst around releases) → save."""

from __future__ import annotations

import calendar
import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import commands, lookups
from . import diff as diffmod
from . import format as fmt
from . import releases as rel
from .adapters.base import Adapter
from .config import Config
from .http import Http
from .models import Performance, now
from .state import State

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    cfg: Config
    state: State
    http: Http
    tg: object  # telegram.Telegram or telegram.DryRun
    adapters: dict[str, Adapter]
    started: datetime = field(default_factory=now)
    sleep: callable = _time.sleep  # swapped out in tests

    def names(self) -> dict[str, str]:
        return {k: self.cfg.venue_name(k) for k in self.cfg.venues} | {lookups.VENUE: "Custom lookups"}

    def save(self) -> bool:
        return self.state.save(self.cfg.state_path)


# --- scanning & health ---------------------------------------------------------------------------


def _health_fail(ctx: Ctx, key: str, error: str) -> None:
    h = ctx.state.health.setdefault(key, {})
    h["fails"] = h.get("fails", 0) + 1
    h["last_error"] = error[:300]
    limit = ctx.cfg.get("health", "failures_before_alert", 3)
    if h["fails"] >= limit and not h.get("alerted"):
        h["alerted"] = True
        ctx.tg.send(
            f"⚠️ <b>{ctx.names().get(key, key)}</b> failed {h['fails']} checks in a row "
            f"(site layout or API may have changed).\n<code>{fmt.escape(h['last_error'])}</code>"
        )


def _health_ok(ctx: Ctx, key: str) -> None:
    h = ctx.state.health.pop(key, None)
    if h and h.get("alerted"):
        ctx.tg.send(f"✅ <b>{ctx.names().get(key, key)}</b> is working again.")


def scan(ctx: Ctx, keys: set[str] | None = None, include_custom: bool = True) -> dict[str, list[Performance]]:
    """Fetch venues (all, or `keys`). A failing venue is left out, so its previous data is kept."""
    results = {}
    for key, adapter in ctx.adapters.items():
        if keys is not None and key not in keys:
            continue
        t0 = _time.monotonic()
        try:
            perfs = adapter.fetch()
        except Exception as exc:  # one broken site must not stop the others
            log.exception("%s failed", key)
            _health_fail(ctx, key, f"{type(exc).__name__}: {exc}")
            continue
        expected = any(d["venue"] == key and d["start"] > (now() + timedelta(days=1)).isoformat() for d in ctx.state.performances.values())
        if not perfs and expected:
            _health_fail(ctx, key, "returned 0 performances")
            continue
        _health_ok(ctx, key)
        results[key] = perfs
        log.info("%-20s %4d performances (%.1fs)", key, len(perfs), _time.monotonic() - t0)
    if include_custom and keys is None and any(w.get("lookup") for w in ctx.state.watchlist):
        try:
            results[lookups.VENUE] = lookups.run_all(ctx.http, ctx.state.watchlist)
            _health_ok(ctx, lookups.VENUE)
        except Exception as exc:
            log.exception("custom lookups failed")
            _health_fail(ctx, lookups.VENUE, f"{type(exc).__name__}: {exc}")
    return results


def process(ctx: Ctx, results: dict[str, list[Performance]], header: str = "🔔 <b>Theatre update</b>") -> list[diffmod.Change]:
    low = ctx.cfg.get("alerts", "low_threshold", 20)
    changes = diffmod.compute(ctx.state, results, ctx.state.watchlist, low)
    diffmod.apply(ctx.state, results)
    fresh = [c for c in changes if c.reportable and not (c.kind in diffmod.ONCE and ctx.state.was_notified(c.key))]
    if fresh:
        ctx.tg.send_all(fmt.alerts(fresh, ctx.names(), header))
        for c in fresh:
            if c.kind in diffmod.ONCE:
                ctx.state.mark_notified(c.key)
    for venue in {c.perf.venue for c in fresh if c.kind in diffmod.ONCE}:
        rel.mark_released(ctx.state, ctx.cfg, venue)
    log.info("%d change(s), %d notified", len(changes), len(fresh))
    return fresh


# --- config import, reminders, digest --------------------------------------------------------------


def import_watchlist(ctx: Ctx) -> None:
    """Add entries from config/watchlist.yaml once (removing them via /unwatch sticks)."""
    for entry in ctx.cfg.watchlist:
        tag = f"{entry['query']}@{entry.get('venue', '')}"
        if tag not in ctx.state.watch_imported:
            ctx.state.watchlist.append(entry)
            ctx.state.watch_imported.append(tag)


def send_reminders(ctx: Ctx) -> None:
    for r, label in rel.due_reminders(ctx.state, ctx.cfg):
        w = rel.window(r, ctx.cfg)
        at = datetime.fromisoformat(r["at"])
        phrase = fmt.when_phrase(at, now()) if "remind_at" in r else f"in {label}"
        lines = [
            f"⏰ <b>Ticket release {phrase}</b>",
            fmt.release_line(r, ctx.names()),
            f"I'll check every {w.poll_seconds}s from {w.start_poll:%d.%m. %H:%M} and message you the moment tickets appear.",
        ]
        # a companion all-day window on the same date (recurring releases with also_all_day)
        for other in ctx.state.releases:
            if other is not r and other.get("all_day") and other["venue"] == r["venue"] and other["at"][:10] == r["at"][:10]:
                ow = rel.window(other, ctx.cfg)
                lines.append(f"If they're not out by then, I keep checking every {ow.poll_seconds}s on {ow.start_poll:%d.%m.} {ow.start_poll:%H:%M}–{ow.end:%H:%M}.")
        if r.get("link"):
            lines.append(f'<a href="{fmt.escape(r["link"], quote=True)}">Open the repertoire</a>')
        ctx.tg.send("\n".join(lines))


def digest_due(ctx: Ctx) -> bool:
    at = time.fromisoformat(ctx.cfg.get("digest", "time", "08:00"))
    t = now()
    return t.time() >= at and ctx.state.last_digest != t.date().isoformat()


def end_of_next_month(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, calendar.monthrange(year, month)[1])


def digest_range(ctx: Ctx) -> tuple[date, str]:
    """Last day covered by the scheduled overview, and its title."""
    today = now().date()
    if ctx.cfg.get("digest", "until", "next_month") == "next_month":
        end = end_of_next_month(today)
        return end, f"Until {end:%d.%m.} (end of next month)"
    days = int(ctx.cfg.get("digest", "days", 7))
    return today + timedelta(days=days - 1), f"Next {days} days"


def send_overview(ctx: Ctx, until: date | None, title: str) -> None:
    """Send everything from now through `until` (inclusive; None = everything announced)."""
    perfs = [p for p in ctx.state.perfs() if p.venue != lookups.VENUE and p.start >= now() - timedelta(hours=3)]
    if until is not None:
        perfs = [p for p in perfs if p.start.date() <= until]
    watched = {p.uid for p in perfs if diffmod.watch_entry(p, ctx.state.watchlist)}
    ctx.tg.send_all(fmt.overview(perfs, ctx.names(), title, watched))


# --- burst mode --------------------------------------------------------------------------------------


def burst(ctx: Ctx, windows: list[rel.Window]) -> None:
    """Poll release venues rapidly until the windows close (or the run's time budget ends)."""
    budget = ctx.started + timedelta(minutes=ctx.cfg.get("releases", "max_run_minutes", 200))
    end = min(max(w.end for w in windows), budget)
    full_every = timedelta(minutes=ctx.cfg.get("releases", "full_scan_minutes", 10))
    last_full = now()
    log.info("burst mode until %s for %s", end, [(w.release["venue"], w.release["at"]) for w in windows])
    while (t := now()) < end:
        windows = [w for w in windows if not (w.release.get("all_day") and rel.released_key(w.release) in ctx.state.notified)]
        if not windows:
            break
        polling = [w for w in windows if w.start_poll <= t < w.end]
        if not polling:
            nxt = min((w.start_poll for w in windows if w.start_poll > t), default=end)
            ctx.sleep(max(1.0, min((nxt - t).total_seconds(), 60)))
            commands.handle(ctx, ctx.tg.updates(ctx.state.telegram_offset))
            continue
        venues = {w.release["venue"] for w in polling}
        process(ctx, scan(ctx, keys=venues, include_custom=False), header="🚨 <b>Release watch</b>")
        if now() - last_full >= full_every:
            process(ctx, scan(ctx))
            last_full = now()
        commands.handle(ctx, ctx.tg.updates(ctx.state.telegram_offset))
        ctx.save()
        ctx.sleep(min(w.poll_seconds for w in polling))

    for w in windows:
        key = f"burst-done:{w.release['id']}"
        if now() >= w.end and not ctx.state.was_notified(key):
            ctx.state.mark_notified(key)
            venue = w.release["venue"]
            on_sale = [p for p in ctx.state.perfs() if p.venue == venue and p.status.buyable]
            ctx.tg.send(
                f"🏁 Release window closed: {fmt.release_line(w.release, ctx.names())}\n"
                f"{len(on_sale)} performance(s) at {ctx.names().get(venue, venue)} are on sale now. /theatre {venue}"
            )


# --- one run -------------------------------------------------------------------------------------------


def run(ctx: Ctx, allow_burst: bool = True) -> None:
    first = not ctx.state.initialized
    import_watchlist(ctx)
    updates = ctx.tg.updates(ctx.state.telegram_offset)

    results = scan(ctx)
    process(ctx, results)
    rel.sync(ctx.state, ctx.cfg, [p for ps in results.values() for p in ps])
    send_reminders(ctx)
    commands.handle(ctx, updates, skip_start=first)

    if first:
        ctx.state.initialized = True
        ctx.state.last_digest = now().date().isoformat()
        n = len(ctx.state.performances)
        ctx.tg.send(
            f"✅ <b>Tracker is live.</b> Found {n} upcoming performances across {len(results)} sources. "
            f"Here's what's on until the end of next month; send /overview for everything.\n\n" + commands.HELP
        )
        send_overview(ctx, *digest_range(ctx))
    elif digest_due(ctx):
        ctx.state.last_digest = now().date().isoformat()
        send_overview(ctx, *digest_range(ctx))

    ctx.state.prune()
    ctx.save()
    if allow_burst and (windows := rel.active_windows(ctx.state, ctx.cfg)):
        burst(ctx, windows)
        ctx.state.prune()
        ctx.save()
