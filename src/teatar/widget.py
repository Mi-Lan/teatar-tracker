"""A release message as a Telegram HTML widget (Bot API 10.4, sendWidget): a playbill in a wooden frame.

Experimental. A widget is one small self-contained HTML document drawn inside the chat bubble. What it
may contain is fixed by Telegram's widget profile 1 (github.com/TelegramMessenger/widget-sandbox,
docs/widget-runtime-profile.md); the parts that shape this file:
  - at most 4096 bytes after zlib level 9 (and 32 KB before), or the send is refused;
  - no network, so no images or fonts from outside: the wood is CSS gradients, the type is the
    device's own `serif`;
  - the bubble is 200–600 px wide and at most 600 px tall, and nothing scrolls: what doesn't fit is cut
    off. A long release must go out as text instead (see `fits`);
  - `<a href="https://…">` works, and the app asks the user to confirm the address before opening it;
  - private chats only, for now.
Colours are fixed rather than taken from the chat's theme: the frame is the point.
"""

from __future__ import annotations

import zlib
from collections import defaultdict
from datetime import date
from html import escape

from . import format as fmt
from .models import Performance, Status

MAX_COMPRESSED = 4096
MAX_ROWS = 17  # titles + day lines that fit under the 600 px height limit at a phone's text size

CSS = (
    "body{margin:0;padding:0;font-family:serif;color:#3b2a1a}"
    # the frame: planks (a vertical grain over a darker-to-lighter board)
    ".w{padding:11px;background:repeating-linear-gradient(91deg,rgba(0,0,0,0) 0 5px,rgba(0,0,0,.13) 5px 6px,"
    "rgba(0,0,0,0) 6px 14px,rgba(255,255,255,.07) 14px 15px),linear-gradient(#80502b,#5b3418 46%,#734524 54%,#4d2a12)}"
    # the sheet: parchment with a gilt double rule
    ".c{background:linear-gradient(#faf2de,#eedcb6);border:1px solid #8a6a2a;border-radius:4px;"
    "box-shadow:inset 0 0 0 3px #faf2de,inset 0 0 0 4px #c7a252,0 1px 5px rgba(0,0,0,.55);padding:15px 14px 11px}"
    "h1{font-size:1.2em;letter-spacing:.16em;text-align:center;margin:0;font-weight:700;text-transform:uppercase}"
    ".o{display:flex;align-items:center;gap:8px;color:#a67c2e;margin:2px 14% 3px}"
    ".o:before,.o:after{content:'';flex:1;border-top:1px solid #c7a252}"
    ".s{text-align:center;font-style:italic;margin:0;font-size:1.05em}"
    ".m{text-align:center;font-size:.78em;color:#7a6548;margin:2px 0 4px}"
    ".t{font-style:italic;font-size:1.13em;margin:10px 0 3px}.t a{color:#6b1d1d;text-decoration:none}"
    ".r{display:flex;flex-wrap:wrap;gap:0 12px;font-size:.93em;padding:1px 0 1px 9px;border-left:2px solid #c7a252;"
    "font-variant-numeric:tabular-nums}"
    ".d{min-width:5.3em;color:#5a4630}.r a{color:#3b2a1a;font-weight:700;border-bottom:1px dotted #8a6a2a;text-decoration:none}"
    ".r i{display:inline-block;width:.58em;height:.58em;border-radius:50%;margin:0 4px 0 5px;background:#3d9a48}"
    ".r i.l{background:#d7931e}.r i.x{background:#b23a2b}.q{color:#7a6548;font-style:italic}"
    ".f{margin-top:11px;padding-top:6px;border-top:1px solid #dcc590;font-size:.78em;color:#7a6548;text-align:center}"
    ".f a{color:#6b1d1d;text-decoration:none}.f i{display:inline-block;width:.58em;height:.58em;border-radius:50%;"
    "background:#3d9a48;margin-right:4px}"
)


def _time(p: Performance, title_url: str) -> str:
    """A show's time, linked to its tickets when they can be bought."""
    label = f"{p.start:%H:%M}"
    return f'<a href="{fmt.href(p.buy_url)}">{label}</a>' if p.status.buyable and p.buy_url and p.buy_url != title_url else label


def _status(p: Performance) -> str:
    """What is left: a coloured dot and the count, or a word."""
    if p.status.buyable:
        count = fmt.left(p) if p.available is not None else ("few left" if p.status == Status.LOW else "")
        return f'<i{" class=l" if p.status == Status.LOW else ""}></i>{count}'
    return {
        Status.SOLD_OUT: " <i class=x></i>sold out", Status.CANCELLED: " <i class=x></i>cancelled",
        Status.NOT_ON_SALE: " <span class=q>not on sale yet</span>",
    }.get(p.status, "")


def _play(perfs: list[Performance], mark: str = "") -> tuple[str, int]:
    """The block for one play (title, then a line per day) and how many rows it takes."""
    first = perfs[0]
    title_url = first.url or first.buy_url
    title = escape(first.title)
    title = f'<a href="{fmt.href(title_url)}">{title}</a>' if title_url.startswith("https://") else title
    by_day: dict[date, list[Performance]] = defaultdict(list)
    for p in sorted(perfs, key=lambda p: p.start):
        by_day[p.start.date()].append(p)
    rows = []
    for day, ps in by_day.items():
        statuses = [_status(p) for p in ps]
        # the same status for every show that day is said once, unless it carries a number (each show has its own)
        shared = len(ps) > 1 and len(set(statuses)) == 1 and not any(p.status.buyable and p.available is not None for p in ps)
        if shared:
            cells = f"<span>{' · '.join(_time(p, title_url) for p in ps)}{statuses[0]}</span>"
        else:
            cells = "".join(f"<span>{_time(p, title_url)}{st}</span>" for p, st in zip(ps, statuses, strict=True))
        rows.append(f"<div class=r><span class=d>{fmt.day_label(day)}</span>{cells}</div>")
    return f'<p class=t>{mark + " " if mark else ""}{title}</p>' + "".join(rows), 1 + len(rows)


def release_widget(
    theatre: str, headline: str, note: str, plays: list[list[Performance]], site_url: str = "", marks: dict[str, str] | None = None,
) -> tuple[str, int]:
    """The widget's HTML and the number of rows it shows. `plays`: each play's performances, in the order
    to show them; `marks`: uid of any of a play's dates -> "⭐" / "🔥" before its title."""
    marks = marks or {}
    blocks, rows = [], 0
    for perfs in plays:
        block, n = _play(perfs, next((marks[p.uid] for p in perfs if p.uid in marks), ""))
        blocks.append(block)
        rows += n
    counted = any(p.status.buyable and p.available is not None for ps in plays for p in ps)
    foot = [x for x in ["<i></i>tickets left" if counted else "", f'<a href="{fmt.href(site_url)}">{escape(fmt.domain(site_url))}</a>' if site_url.startswith("https://") else ""] if x]
    html = (
        f"<style>{CSS}</style><div class=w><div class=c><h1>{escape(theatre)}</h1><div class=o>❦</div>"
        f"<p class=s>{escape(headline)}</p><p class=m>{escape(note)}</p>{''.join(blocks)}"
        + (f"<div class=f>{' · '.join(foot)}</div>" if foot else "")
        + "</div></div>"
    )
    return html, rows


def compressed_size(html: str) -> int:
    """The size Telegram's server checks: zlib at level 9."""
    return len(zlib.compress(html.encode("utf-8"), 9))


def fits(html: str, rows: int) -> bool:
    """Can this go out as a widget? Otherwise send the text message."""
    return compressed_size(html) <= MAX_COMPRESSED and len(html.encode("utf-8")) <= 32768 and rows <= MAX_ROWS
