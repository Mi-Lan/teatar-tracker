"""One tracker run: scan → detect changes → notify → answer commands → (burst around releases) → save."""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx

from . import commands, lookups, sales, season
from . import diff as diffmod
from . import format as fmt
from . import releases as rel
from .adapters.base import Adapter
from .config import Config
from .dates import WEEKDAYS, parse_time, until_end_of_next_month
from .http import Http
from .models import Performance, now
from .state import State
from .telegram import Messenger, TelegramError

log = logging.getLogger(__name__)

UPDATE = "🔔 <b>Theatre update</b>"


@dataclass
class Ctx:
    cfg: Config
    state: State
    http: Http
    tg: Messenger  # telegram.Telegram, or telegram.DryRun to print instead
    adapters: dict[str, Adapter]
    started: datetime = field(default_factory=now)
    sleep: Callable[[float], None] = _time.sleep  # swapped out in tests

    def names(self) -> dict[str, str]:
        """Display names: venue key -> theatre, plus the names health alerts use for adapter warnings."""
        names = {k: self.cfg.venue_name(k) for k in self.cfg.venues} | {lookups.VENUE: "Custom lookups"}
        return names | {_warning_key(k): f"{names.get(k, k)} {a.warning_label}" for k, a in self.adapters.items()}

    @property
    def every_change(self) -> bool:
        """alerts.instant: all — every new show, date and sale is messaged as soon as it's found."""
        return self.cfg.flag("alerts", "instant") == "all"

    def save(self) -> bool:
        return self.state.save(self.cfg.state_path)


def _warning_key(venue: str) -> str:
    """The health entry for a venue's partial problems (Adapter.warning), next to the venue's own."""
    return f"{venue}:warning"


# --- scanning & health ---------------------------------------------------------------------------


def _health_fail(ctx: Ctx, key: str, error: str) -> None:
    h = ctx.state.health.setdefault(key, {})
    h["fails"] = h.get("fails", 0) + 1
    h["last_error"] = error[:300]
    limit = ctx.cfg.get("health", "failures_before_alert")
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


def scan(ctx: Ctx, keys: set[str] | None = None, include_custom: bool = True, quick: bool = False) -> dict[str, list[Performance]]:
    """Fetch venues (all, or `keys`). A failing venue is left out, so its previous data is kept.
    `quick` uses each adapter's fast poll() (burst mode) instead of the full fetch()."""
    results = {}
    for key, adapter in ctx.adapters.items():
        if keys is not None and key not in keys:
            continue
        t0 = _time.monotonic()
        stored = ctx.state.venue_dicts(key)
        if adapter.uses_previous:
            adapter.previous = {u: Performance.from_dict(d) for u, d in stored.items()}
            adapter.first_seen = {u: ctx.state.meta.get(u, {}).get("first_seen", "") for u in stored}
            adapter.notes = dict(ctx.state.adapters.get(key, {}))
        try:
            perfs = adapter.poll() if quick else adapter.fetch()
        except Exception as exc:  # one broken site must not stop the others
            log.exception("%s failed", key)
            _health_fail(ctx, key, f"{type(exc).__name__}: {exc}")
            continue
        finally:
            if adapter.uses_previous and adapter.notes:
                ctx.state.adapters[key] = adapter.notes
        tomorrow = (now() + timedelta(days=1)).isoformat()
        if not perfs and any(d["start"] > tomorrow for d in stored.values()):  # it had dates ahead: not a quiet week
            _health_fail(ctx, key, "returned 0 performances")
            continue
        _health_ok(ctx, key)
        if not quick:  # an adapter can report a partial problem (e.g. Narodno's seat counts) without failing
            if adapter.warning:
                _health_fail(ctx, _warning_key(key), adapter.warning)
            else:
                _health_ok(ctx, _warning_key(key))
        results[key] = perfs
        log.info("%-20s %4d performances (%.1fs)", key, len(perfs), _time.monotonic() - t0)
    wanted = keys is None or lookups.VENUE in keys  # (a lookup's own sales start is a release at venue "custom")
    if include_custom and wanted and any(w.get("lookup") for w in ctx.state.watchlist):
        try:
            results[lookups.VENUE] = lookups.run_all(ctx.http, ctx.state.watchlist)
            _health_ok(ctx, lookups.VENUE)
        except Exception as exc:
            log.exception("custom lookups failed")
            _health_fail(ctx, lookups.VENUE, f"{type(exc).__name__}: {exc}")
    return results


def process(ctx: Ctx, results: dict[str, list[Performance]], header: str = UPDATE) -> list[diffmod.Change]:
    """Detect changes. New dates and sales openings are messaged by season.track (watched plays get ⭐
    there); other changes to watched plays (sold out, running low, back in stock, cancelled) are messaged
    here right away, as is every change when alerts.instant is "all"."""
    low = ctx.cfg.get("alerts", "low_threshold")
    changes = diffmod.compute(ctx.state, results, ctx.state.watchlist, low)
    diffmod.apply(ctx.state, results)
    fresh = [c for c in changes if c.reportable and not (c.kind in diffmod.ONCE and ctx.state.was_notified(c.key))]
    send_all = ctx.every_change
    # season.track only covers real venues: new dates from custom lookups (venue "custom") are sent here
    instant = [c for c in fresh if send_all or (c.watched and (c.kind not in diffmod.ONCE or c.perf.venue not in ctx.cfg.venues))]
    if instant:
        ctx.tg.send_all(fmt.alerts(instant, ctx.names(), header))
    for c in fresh:
        if c.kind in diffmod.ONCE:
            ctx.state.mark_notified(c.key)
    for venue in {c.perf.venue for c in fresh if c.kind in diffmod.ONCE}:
        rel.mark_released(ctx.state, ctx.cfg, venue)
    log.info("%d change(s), %d sent now", len(changes), len(instant))
    return fresh


def check(ctx: Ctx, keys: set[str] | None = None, quick: bool = False, header: str = UPDATE) -> dict[str, list[Performance]]:
    """One check of the venues (all, or `keys`): scan, detect and message what changed, note releases."""
    results = scan(ctx, keys=keys, quick=quick)
    fresh = process(ctx, results, header)
    season.track(ctx, set(results), fresh)
    return results


def pending_updates(ctx: Ctx) -> list[dict]:
    """Messages waiting for the bot. When Telegram can't be reached the check still runs, and they are
    answered next time; a rejected request (a wrong token, say) still stops the run, so it gets noticed."""
    try:
        return ctx.tg.updates(ctx.state.telegram_offset)
    except (httpx.TransportError, TelegramError) as exc:
        if isinstance(exc, TelegramError) and 400 <= exc.status < 500:
            raise
        log.warning("Telegram unreachable (%s): commands will be answered on the next check", type(exc).__name__)
        return []


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
    """The scheduled report (digest.every: weekly or daily): due once the latest scheduled slot has passed."""
    every = ctx.cfg.flag("digest", "every")
    if every == "off":
        return False
    weekly = every == "weekly"
    t = now()
    slot = t.date()
    if weekly:
        weekday = WEEKDAYS.index(ctx.cfg.flag("digest", "weekday", *WEEKDAYS))
        slot -= timedelta(days=(t.weekday() - weekday) % 7)
    if t < datetime.combine(slot, parse_time(ctx.cfg.get("digest", "time")), t.tzinfo):
        slot -= timedelta(days=7 if weekly else 1)
    return ctx.state.last_digest < slot.isoformat()


def send_report(ctx: Ctx) -> None:
    """The scheduled report: a self-contained overview until the end of next month."""
    until, title = digest_range(ctx)
    upcoming = [r for r in ctx.state.releases if not r.get("all_day")][:2]
    note = "\n".join(f"⏰ Next ticket release: {fmt.release_line(r, ctx.names())}" for r in upcoming)
    kind = "Weekly" if ctx.cfg.flag("digest", "every") == "weekly" else "Daily"
    send_overview(ctx, until, f"{kind} overview · {title}", note)


def digest_range(ctx: Ctx) -> tuple[date, str]:
    """Last day covered by the scheduled overview, and its title."""
    if ctx.cfg.flag("digest", "until") == "next_month":
        return until_end_of_next_month()
    days = int(ctx.cfg.get("digest", "days"))
    return now().date() + timedelta(days=days - 1), f"Next {days} days"


def send_overview(ctx: Ctx, until: date | None, title: str, note: str = "") -> None:
    """Send everything from now through `until` (inclusive; None = everything announced)."""
    perfs = [p for p in ctx.state.catalog() if until is None or p.start.date() <= until]
    ctx.tg.send_all(commands.overview(ctx, perfs, title, note))


# --- burst mode --------------------------------------------------------------------------------------


def burst(ctx: Ctx, windows: list[rel.Window]) -> None:
    """Poll release venues rapidly until the windows close (or the run's time budget ends)."""
    budget = ctx.started + timedelta(minutes=ctx.cfg.get("releases", "max_run_minutes"))
    end = min(max(w.end for w in windows), budget)
    full_every = timedelta(minutes=ctx.cfg.get("releases", "full_scan_minutes"))
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
            commands.handle(ctx, pending_updates(ctx))
            continue
        check(ctx, keys={w.release["venue"] for w in polling}, quick=True, header="🚨 <b>Release watch</b>")
        if now() - last_full >= full_every:
            check(ctx)
            last_full = now()
        commands.handle(ctx, pending_updates(ctx))
        ctx.save()
        ctx.sleep(min(w.poll_seconds for w in polling))

    for w in windows:
        key = f"burst-done:{w.release['id']}"
        if now() >= w.end and not ctx.state.was_notified(key):
            ctx.state.mark_notified(key)
            if rel.released_key(w.release) in ctx.state.notified:
                continue  # tickets came out and you were already alerted
            if not ctx.cfg.get("releases", "report_misses"):
                continue
            later = [
                o for o in ctx.state.releases
                if o is not w.release and o["venue"] == w.release["venue"] and rel.window(o, ctx.cfg).end > now()
                and o["at"][:10] == w.release["at"][:10]
            ]
            follow = (
                f"I'll keep checking {rel.window(later[0], ctx.cfg).start_poll:%d.%m. %H:%M}–{rel.window(later[0], ctx.cfg).end:%H:%M}."
                if later else "They may come later; I'll message you when they do."
            )
            ctx.tg.send(f"🕛 No new tickets yet: {fmt.release_line(w.release, ctx.names())}\n{follow}")


# --- one run -------------------------------------------------------------------------------------------


def run(ctx: Ctx, allow_burst: bool = True) -> None:
    first = not ctx.state.initialized
    import_watchlist(ctx)
    if n := sales.import_seed(ctx.state, ctx.cfg.state_path.parent / "sales_seed.json"):
        log.info("imported %d sell-out records from sales_seed.json", n)
    updates = pending_updates(ctx)

    # State is saved after each stage that sends messages. If a later stage fails, what has already been
    # announced (or answered) is on record and isn't sent again by every following run; a stage that
    # fails midway isn't saved, so it is simply redone next time.
    results = check(ctx)
    ctx.save()
    rel.sync(ctx.state, ctx.cfg, [p for ps in results.values() for p in ps])
    send_reminders(ctx)
    commands.handle(ctx, updates, skip_start=first)
    ctx.save()

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
        send_report(ctx)
    if not first and (slot := season.due(ctx)):
        ctx.tg.send_all(season.report(ctx, slot=slot))

    _finish(ctx)
    if allow_burst and (windows := rel.active_windows(ctx.state, ctx.cfg)):
        burst(ctx, windows)
        _finish(ctx)


def _finish(ctx: Ctx) -> None:
    ctx.state.prune(history_days=int(ctx.cfg.get("selling", "history_days")))
    ctx.save()
