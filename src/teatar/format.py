"""Telegram (HTML parse mode) message formatting."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from html import escape
from urllib.parse import urlparse

from .config import PLAIN, Look
from .diff import Change, Kind
from .models import Performance, Status, now
from .telegram import plain

LIMIT = 3900  # Telegram max is 4096 visible characters (after HTML parsing); leave room
MAX_ENTITIES = 90  # keep well under Telegram's per-message formatting-entity limit
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def plural(n: int, word: str) -> str:
    """'1 date', '3 dates'."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def month_name(key: str, short: bool = False) -> str:
    """'2026-11' -> 'November' (or 'Nov')."""
    name = MONTHS[int(key[5:7]) - 1]
    return name[:3] if short else name


def day_label(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d:%d.%m.}"


def when(p: Performance) -> str:
    return f"{DAYS[p.start.weekday()]} {p.start:%d.%m. %H:%M}"


STALE = timedelta(hours=3)


def stale(p: Performance) -> bool:
    """A seat count carried over from an earlier check (Narodno counts about once a day)."""
    counted = p.extra.get("counted_at")
    return bool(counted) and now() - datetime.fromisoformat(counted) > STALE


def left(p: Performance) -> str:
    """Seats left: '145', or '~145' when the count is a few hours old."""
    return f"~{p.available}" if stale(p) else str(p.available)


def badge(p: Performance) -> str:
    s = p.status
    if s == Status.ON_SALE:
        return f"🟢 {left(p)} left" if p.available is not None else "🟢 on sale"
    if s == Status.LOW:
        return f"🟡 {left(p)} left" if p.available is not None else "🟡 few left"
    if s == Status.SOLD_OUT:
        return "🔴 sold out"
    if s == Status.NOT_ON_SALE:
        return f"⏳ sale from {p.sales_start:%d.%m. %H:%M}" if p.sales_start else "⏳ not on sale yet"
    if s == Status.CANCELLED:
        return "❌ cancelled"
    return "❔ see site"


def href(url: str) -> str:
    """A URL ready for an href attribute (some sites put spaces in their links)."""
    return escape(url.strip().replace(" ", "%20"), quote=True)


def a(url: str, text: str) -> str:
    """`text` (already escaped) linked to `url`, or plain when there is no URL."""
    return f'<a href="{href(url)}">{text}</a>' if url else text


def link(p: Performance) -> str:
    if p.status.buyable and p.buy_url:
        return f" · {a(p.buy_url, 'buy')}"
    if p.url:
        return f" · {a(p.url, 'info')}"
    return ""


def domain(url: str) -> str:
    """'https://www.jdp.rs/rs/…' -> 'jdp.rs'."""
    return urlparse(url).netloc.removeprefix("www.") or url


def site_link(url: str) -> str:
    """'🌐 jdp.rs' linked to the theatre's repertoire page."""
    if not url:
        return ""
    return f"🌐 {a(url, escape(domain(url)))}"


def short_badge(p: Performance) -> str:
    s = p.status
    if s in (Status.ON_SALE, Status.LOW) and p.available is not None:
        return f"{'🟢' if s == Status.ON_SALE else '🟡'} {left(p)}"
    return {
        Status.ON_SALE: "🟢", Status.LOW: "🟡 few left", Status.SOLD_OUT: "🔴 sold out",
        Status.NOT_ON_SALE: f"⏳ from {p.sales_start:%d.%m.}" if p.sales_start else "⏳ not on sale",
        Status.CANCELLED: "❌ cancelled", Status.UNKNOWN: "❔",
    }[s]


def titled(p: Performance) -> str:
    """The title, linked to the ticket page when buyable (else the info page)."""
    url = p.buy_url if (p.status.buyable and p.buy_url) else p.url or p.buy_url
    t = escape(p.title)
    return a(url, t) if url else f"<b>{t}</b>"


def perf_line(p: Performance, show_date: bool = False, star: bool = False, show_stage: bool = True, fire: bool = False) -> str:
    """Compact line: '20:30 Божји људи · Раша Плаовић 🟢 271' (title links to tickets).
    ⭐ marks a watched play, 🔥 one that sells out fast."""
    head = when(p) if show_date else f"{p.start:%H:%M}"
    stage = f" · {escape(p.stage)}" if p.stage and show_stage else ""
    return f"{'⭐' if star else ''}{'🔥' if fire else ''}{head} {titled(p)}{stage} {short_badge(p)}"


def play_block(perfs: list[Performance], mark: str = "", note: str = "", show_stage: bool = False, look: Look = PLAIN) -> str:
    """One play and its dates, for release messages: the title, then one line per day below it (also when
    there is only one date, so every play reads the same way), in the theatre's look:

        🔥 Слика Доријана Греја · fast seller: sold out in 4h      (title → the play's page)
           Mon 02.11. · 20:30 🟢 12                                (each time → its tickets)
           Tue 01.12. · 17:00 🟢 174 · 19:30 🟢 151                (two shows on one day)
           Wed 16.12. · 17:00 · 19:30 ⏳ not on sale               (…with the same status: said once)

    `look` sets the title (bold or italic, capitals, a marker before it) and whether the day lines are
    indented or sit in a quote block.
    """
    first = perfs[0]
    title_url = first.url or first.buy_url
    name = escape(first.title.upper() if look.upper else first.title)
    head = a(title_url, f"<i>{name}</i>" if look.titles == "italic" else f"<b>{name}</b>")
    stages = {p.stage for p in perfs if p.stage}
    if show_stage and stages:
        head += f" <i>({escape(' / '.join(sorted(stages)))})</i>"
    if before := " ".join(x for x in (look.marker, mark) if x):
        head = f"{before} {head}"
    if note:
        head += f" · {escape(note)}"

    def linked(p: Performance, label: str) -> str:
        return a(p.buy_url, label) if p.status.buyable and p.buy_url and p.buy_url != title_url else label

    def badge_of(p: Performance) -> str:
        return "" if p.status == Status.UNKNOWN else short_badge(p)

    by_day: dict[date, list[Performance]] = defaultdict(list)
    for p in sorted(perfs, key=lambda p: p.start):
        by_day[p.start.date()].append(p)
    lines = []
    for day, ps in by_day.items():
        badges = [badge_of(p) for p in ps]
        # the same status for every show that day is said once, unless it carries a number (each show has its own)
        shared = len(ps) > 1 and len(set(badges)) == 1 and not any(p.status.buyable and p.available is not None for p in ps)
        times = [linked(p, f"{p.start:%H:%M}") + ("" if shared or not b else f" {b}") for p, b in zip(ps, badges, strict=True)]
        lines.append(f"{day_label(day)} · " + " · ".join(times) + (f" {badges[0]}" if shared and badges[0] else ""))
    if look.dates == "quote":
        return f"{head}\n<blockquote>" + "\n".join(lines) + "</blockquote>"
    return "\n   ".join([head, *lines])


def counts_note(perfs: list[Performance]) -> str:
    """A one-line key for messages that show bare numbers after 🟢/🟡; '' when none does."""
    if not any(p.status.buyable and p.available is not None for p in perfs):
        return ""
    return "🟢 number = tickets left" + (" (~: counted a few hours ago)" if any(stale(p) and p.available is not None for p in perfs) else "")


def visible_len(html: str) -> int:
    return len(plain(html))


def entities(html: str) -> int:
    return len(re.findall(r"<(?:a|b|i|u|code|blockquote)\b", html))


def pack(blocks: list[str], header: str = "", joiner: str = "\n\n", cont: str = "") -> list[str]:
    """Join blocks into as few messages as possible under Telegram's size and entity limits.
    Blocks are separated by `joiner`; a message after the first starts with `cont` (if given)."""
    messages, cur = [], header

    def fits(text: str) -> bool:
        return visible_len(text) <= LIMIT and entities(text) <= MAX_ENTITIES

    for block in blocks:
        if not fits(block):  # split line by line: a quote block can't span two messages, so its lines go out plain
            block = block.replace("<blockquote>", "").replace("</blockquote>", "")
        pieces = [block] if fits(block) else block.split("\n")
        for piece in pieces:
            sep = joiner if cur and piece is block else "\n"
            candidate = f"{cur}{sep}{piece}" if cur else piece
            if cur and not fits(candidate):
                messages.append(cur)
                cur = f"{cont}\n{piece.lstrip(chr(10))}" if cont else piece
            else:
                cur = candidate
    if cur:
        messages.append(cur)
    return messages


def overview(
    perfs: list[Performance], venue_names: dict[str, str], title: str, watched: set[str] | None = None, note: str = "",
    fast: set[str] | None = None,
) -> list[str]:
    """Performances grouped by date, then theatre. ⭐ = `watched` uids, 🔥 = `fast` uids (plays that sell out fast)."""
    watched, fast = watched or set(), fast or set()
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
            lines += [perf_line(p, star=p.uid in watched, show_stage=len(stages[v]) > 1, fire=p.uid in fast) for p in ps]
        blocks.append("\n".join(lines))
    head = (
        f"🎭 <b>{escape(title)}</b> · {plural(len(perfs), 'performance')}\n🟢 N = tickets left"
        + (" (~N: at the last daily count)" if any(stale(p) and p.available is not None for p in perfs) else "")
        + " · tap a title to buy" + (" · 🔥 sells out fast" if fast else "")
    )
    if note:
        head += "\n" + note
    return pack(blocks, header=head)


def by_title(perfs: list[Performance], venue_names: dict[str, str], title: str, notes: dict[str, str] | None = None) -> list[str]:
    """Performances grouped by play (used for search results and the watchlist).
    `notes`: uid of any of a play's dates -> a note for the play (e.g. its fast-seller flag)."""
    notes = notes or {}
    groups: dict[tuple[str, str], list[Performance]] = defaultdict(list)
    for p in sorted(perfs, key=lambda p: p.start):
        groups[(p.venue, p.title_norm)].append(p)
    blocks = []
    for (venue, _), ps in groups.items():
        note = next((notes[p.uid] for p in ps if p.uid in notes), "")
        lines = [f"<b>{escape(ps[0].title)}</b> — <u>{escape(venue_names.get(venue, venue))}</u>" + (f" · {escape(note)}" if note else "")]
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

