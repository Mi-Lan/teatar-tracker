"""Telegram (HTML parse mode) message formatting."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
import re
from html import escape, unescape

from .diff import Change, Kind
from .models import Performance, Status

LIMIT = 3900  # Telegram max is 4096 visible characters (after HTML parsing); leave room
MAX_ENTITIES = 90  # keep well under Telegram's per-message formatting-entity limit
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def day_label(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d:%d.%m.}"


def when(p: Performance) -> str:
    return f"{DAYS[p.start.weekday()]} {p.start:%d.%m. %H:%M}"


def badge(p: Performance) -> str:
    s = p.status
    if s == Status.ON_SALE:
        return f"🟢 {p.available} left" if p.available is not None else "🟢 on sale"
    if s == Status.LOW:
        return f"🟡 {p.available} left" if p.available is not None else "🟡 few left"
    if s == Status.SOLD_OUT:
        return "🔴 sold out"
    if s == Status.NOT_ON_SALE:
        return f"⏳ sale from {p.sales_start:%d.%m. %H:%M}" if p.sales_start else "⏳ not on sale yet"
    if s == Status.CANCELLED:
        return "❌ cancelled"
    return "❔ see site"


def link(p: Performance) -> str:
    if p.status.buyable and p.buy_url:
        return f' · <a href="{escape(p.buy_url, quote=True)}">buy</a>'
    if p.url:
        return f' · <a href="{escape(p.url, quote=True)}">info</a>'
    return ""


def short_badge(p: Performance) -> str:
    s = p.status
    if s in (Status.ON_SALE, Status.LOW) and p.available is not None:
        return f"{'🟢' if s == Status.ON_SALE else '🟡'} {p.available}"
    return {
        Status.ON_SALE: "🟢", Status.LOW: "🟡 few left", Status.SOLD_OUT: "🔴 sold out",
        Status.NOT_ON_SALE: f"⏳ from {p.sales_start:%d.%m.}" if p.sales_start else "⏳ not on sale",
        Status.CANCELLED: "❌ cancelled", Status.UNKNOWN: "❔",
    }[s]


def titled(p: Performance) -> str:
    """The title, linked to the ticket page when buyable (else the info page)."""
    href = p.buy_url if (p.status.buyable and p.buy_url) else p.url or p.buy_url
    t = escape(p.title)
    return f'<a href="{escape(href, quote=True)}">{t}</a>' if href else f"<b>{t}</b>"


def perf_line(p: Performance, show_date: bool = False, star: bool = False, show_stage: bool = True) -> str:
    """Compact line: '20:30 Божји људи · Раша Плаовић 🟢 271' (title links to tickets)."""
    head = when(p) if show_date else f"{p.start:%H:%M}"
    stage = f" · {escape(p.stage)}" if p.stage and show_stage else ""
    return f"{'⭐' if star else ''}{head} {titled(p)}{stage} {short_badge(p)}"


def visible_len(html: str) -> int:
    return len(unescape(re.sub(r"<[^>]+>", "", html)))


def entities(html: str) -> int:
    return len(re.findall(r"<(?:a|b|i|u|code)\b", html))


def pack(blocks: list[str], header: str = "") -> list[str]:
    """Join blocks into as few messages as possible under Telegram's size and entity limits."""
    messages, cur = [], header

    def fits(text: str) -> bool:
        return visible_len(text) <= LIMIT and entities(text) <= MAX_ENTITIES

    for block in blocks:
        pieces = [block] if fits(block) else block.split("\n")
        for piece in pieces:
            sep = "\n\n" if cur and piece is block else "\n"
            candidate = f"{cur}{sep}{piece}" if cur else piece
            if cur and not fits(candidate):
                messages.append(cur)
                cur = piece
            else:
                cur = candidate
    if cur:
        messages.append(cur)
    return messages


def overview(perfs: list[Performance], venue_names: dict[str, str], title: str, watched: set[str] | None = None) -> list[str]:
    """Performances grouped by date, then theatre."""
    watched = watched or set()
    if not perfs:
        return [f"🎭 <b>{escape(title)}</b>\n\nNothing found."]
    by_day: dict[date, dict[str, list[Performance]]] = defaultdict(lambda: defaultdict(list))
    stages: dict[str, set[str]] = defaultdict(set)
    for p in sorted(perfs, key=lambda p: (p.start, p.venue, p.title)):
        by_day[p.start.date()][p.venue].append(p)
        stages[p.venue].add(p.stage)
    blocks = []
    for d, venues in by_day.items():
        lines = [f"📅 <b>{day_label(d)}</b>"]
        for v, ps in venues.items():
            lines.append(f"<i>{escape(venue_names.get(v, v))}</i>")
            # a venue that only uses one stage (e.g. BDP filtered to Velika scena) needn't repeat it
            lines += [perf_line(p, star=p.uid in watched, show_stage=len(stages[v]) > 1) for p in ps]
        blocks.append("\n".join(lines))
    head = f"🎭 <b>{escape(title)}</b> · {len(perfs)} performances\n🟢 N = tickets left · tap a title to buy"
    return pack(blocks, header=head)


def by_title(perfs: list[Performance], venue_names: dict[str, str], title: str) -> list[str]:
    """Performances grouped by play (used for search results and the watchlist)."""
    groups: dict[tuple[str, str], list[Performance]] = defaultdict(list)
    for p in sorted(perfs, key=lambda p: p.start):
        groups[(p.venue, p.title_norm)].append(p)
    blocks = []
    for (venue, _), ps in groups.items():
        lines = [f"<b>{escape(ps[0].title)}</b> — <u>{escape(venue_names.get(venue, venue))}</u>"]
        lines += [f"  {when(p)}{f' <i>({escape(p.stage)})</i>' if p.stage else ''} — {badge(p)}{link(p)}" for p in ps]
        blocks.append("\n".join(lines))
    return pack(blocks, header=f"🔎 <b>{escape(title)}</b>") if blocks else [f"🔎 <b>{escape(title)}</b>\n\nNothing found."]


SECTIONS = [
    (Kind.SALES_OPENED, "🎟 <b>Tickets now on sale</b>"),
    (Kind.NEW_SHOW, "🆕 <b>New shows</b>"),
    (Kind.NEW_DATE, "📅 <b>New dates</b>"),
    (Kind.BACK_IN_STOCK, "🔁 <b>Tickets available again</b>"),
    (Kind.LOW, "🟡 <b>Running low</b>"),
    (Kind.SOLD_OUT, "🔴 <b>Sold out / sales closed</b>"),
    (Kind.CANCELLED, "❌ <b>Cancelled</b>"),
]


def alerts(changes: list[Change], venue_names: dict[str, str], header: str = "🔔 <b>Theatre update</b>") -> list[str]:
    blocks = []
    for kind, heading in SECTIONS:
        items = [c for c in changes if c.kind == kind]
        if not items:
            continue
        lines = [heading]
        by_venue: dict[str, list[Change]] = defaultdict(list)
        for c in sorted(items, key=lambda c: (not c.watched, c.perf.start)):
            by_venue[c.perf.venue].append(c)
        for v, cs in by_venue.items():
            lines.append(f"<u>{escape(venue_names.get(v, v))}</u>")
            lines += [perf_line(c.perf, show_date=True, star=c.watched) for c in cs]
        blocks.append("\n".join(lines))
    return pack(blocks, header=header) if blocks else []


def release_line(r: dict, venue_names: dict[str, str]) -> str:
    at = datetime.fromisoformat(r["at"])
    when_s = f"{day_label(at.date())} (all day)" if r.get("all_day") else f"{day_label(at.date())} {at:%H:%M}"
    note = f" — {escape(r['note'])}" if r.get("note") else ""
    return f"<b>{escape(venue_names.get(r['venue'], r['venue']))}</b> · {when_s}{note}"


def when_phrase(at: datetime, ref: datetime) -> str:
    """Human wording for a release time relative to `ref`, e.g. 'tonight at midnight (22.→23.10.)'."""
    days = (at.date() - ref.date()).days
    if at.time() == time(0) and days in (1, 2):  # midnight: name the night it falls in
        night = "tonight" if days == 1 else "tomorrow night"
        return f"{night} at midnight ({at.date() - timedelta(days=1):%d.}→{at:%d.%m.})"
    hm = f"{at:%H:%M}"
    if days == 0:
        return f"today at {hm}"
    if days == 1:
        return f"tomorrow at {hm}"
    return f"on {day_label(at.date())} at {hm}"


def weekly_summary(items: list[tuple[Kind, Performance]], releases: list[dict], venue_names: dict[str, str], title: str = "Weekly theatre report") -> list[str]:
    """What changed since the last report: sales opened, new shows (one line per play), new dates (counts)."""
    def counts(ps: list[Performance]) -> str:
        c: dict[str, int] = defaultdict(int)
        for p in ps:
            c[venue_names.get(p.venue, p.venue)] += 1
        return " · ".join(f"{escape(v)} {n}" for v, n in sorted(c.items(), key=lambda x: -x[1]))

    lines = [f"📰 <b>{escape(title)}</b>"]
    opened = [p for k, p in items if k == Kind.SALES_OPENED]
    shows = [p for k, p in items if k == Kind.NEW_SHOW]
    dates = [p for k, p in items if k == Kind.NEW_DATE]
    if not items:
        lines.append("Nothing new since the last report.")
    if opened:
        lines += ["", f"🎟 <b>Tickets went on sale</b> ({len(opened)}): {counts(opened)}"]
    if shows:
        plays: dict[tuple[str, str], list[Performance]] = defaultdict(list)
        for p in sorted(shows, key=lambda p: p.start):
            plays[(p.venue, p.title_norm)].append(p)
        lines += ["", f"🆕 <b>New shows</b> ({len(plays)})"]
        for i, ((venue, _), ps) in enumerate(plays.items()):
            if i == 30:
                lines.append(f"…and {len(plays) - 30} more")
                break
            more = f" (+{len(ps) - 1} dates)" if len(ps) > 1 else ""
            lines.append(f"{titled(ps[0])} · <i>{escape(venue_names.get(venue, venue))}</i> · {when(ps[0])}{more} {short_badge(ps[0])}")
    if dates:
        lines += ["", f"📅 <b>New dates</b> ({len(dates)}): {counts(dates)}"]
    if releases:
        lines += ["", "⏰ <b>Coming up</b>"] + [release_line(r, venue_names) for r in releases[:3]]
    lines += ["", "Everything until the end of next month is below · /month any time"]
    return pack(["\n".join(lines)])
