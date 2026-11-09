"""Sell-out history, and which plays sell out fast.

Every performance that goes on sale gets a record in `state.sales` (uid -> record), kept for
`selling.history_days` after the show, so a play that sold out fast last time is recognised the
next time its dates come out: 🔥 in release messages and lists, /hot and /soldout, and (with
`selling.alert`) a message the moment a fast-selling play gets new dates or goes on sale.

A record: {"venue", "title", "start",
           "on_sale":  when it was first seen on sale (missing when that isn't known),
           "low":      when it was first seen running low,
           "sold_out": when it was first seen sold out (null: sold out, time unknown),
           "instant":  true when it sold out before it was ever seen on sale}
Times are the checks that saw the change, so they are as precise as the gap between checks.
"on_sale" is unknown for dates that were already on sale in a theatre's first scan (or before this
history existed). Statuses flap as tickets are returned (sold out ↔ few left); only the first
sell-out counts. Sales closing on the day of the show is not a sell-out (only a sold-out status is).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

from .models import Performance, Status, now
from .textnorm import normalize

if TYPE_CHECKING:
    from .config import Config
    from .state import State

TRACKED = (Status.ON_SALE, Status.LOW, Status.SOLD_OUT)


def observe(state: State, venue: str, perfs: list[Performance], old: dict[str, dict], stamp: str, baseline: bool) -> None:
    """Update sell-out records from one check of `venue`. `old` is the venue's previous snapshot
    (uid -> Performance dict); `baseline` is true for the venue's very first check."""
    for p in perfs:
        r = state.sales.get(p.uid)
        if r is None:
            if p.status not in TRACKED:
                continue  # not on sale yet: nothing to record
            r = state.sales[p.uid] = _new_record(state, venue, p, old.get(p.uid), stamp, baseline)
        r["title"], r["start"] = p.title, p.start.isoformat()  # dates and spellings can change
        if p.status == Status.LOW and "low" not in r:
            r["low"] = stamp
        if p.status == Status.SOLD_OUT and "sold_out" not in r:
            r["sold_out"] = stamp


def _new_record(state: State, venue: str, p: Performance, prev: dict | None, stamp: str, baseline: bool) -> dict:
    r = {"venue": venue, "title": p.title, "start": p.start.isoformat()}
    meta = state.meta.get(p.uid, {})
    prev_status = Status(prev["status"]) if prev else None
    # Is this the check where it first went on sale (or sold out)? Yes for a date listed just now,
    # or one seen last time without tickets that never had any.
    listed_now = prev is None and not baseline
    opened_now = prev is not None and prev_status not in TRACKED and not meta.get("ever_on_sale")
    if listed_now or opened_now:
        if p.status.buyable:
            r["on_sale"] = stamp
        elif p.status == Status.SOLD_OUT:
            r["instant"] = True  # went on sale and sold out between two checks
        return r
    if meta.get("on_sale_since"):
        r["on_sale"] = meta["on_sale_since"]
    if p.status == Status.SOLD_OUT and prev_status in (None, Status.SOLD_OUT):
        r["sold_out"] = None  # already sold out when first tracked: when is unknown
    if p.status == Status.LOW and prev_status in (None, Status.LOW):
        r["low"] = None
    return r


def import_seed(state: State, path: Path) -> int:
    """Merge history rebuilt from git (data/sales_seed.json, see backfill.py) once. Returns records added."""
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    tag = data.get("generated", "")
    if not tag or state.sales_seed == tag:
        return 0
    added = 0
    for uid, r in data.get("sales", {}).items():
        cur = state.sales.get(uid)
        if cur is None:
            state.sales[uid] = dict(r)
            added += 1
        else:  # already tracked live: fill in only what the live record doesn't know
            for k, v in r.items():
                cur.setdefault(k, v)
    state.sales_seed = tag
    return added


def prune(state: State, days: int) -> None:
    cutoff = (now() - timedelta(days=days)).isoformat()
    state.sales = {u: r for u, r in state.sales.items() if r["start"] >= cutoff}


# --- analysis -------------------------------------------------------------------------------------------


def play_key(venue: str, title: str) -> str:
    return f"{venue}|{normalize(title)}"


def hours_to_sell_out(r: dict) -> float | None:
    """Hours from going on sale to selling out, 0 when it sold out between two checks; None if unknown."""
    if r.get("instant"):
        return 0.0
    if r.get("on_sale") and r.get("sold_out"):
        return max(0.0, (datetime.fromisoformat(r["sold_out"]) - datetime.fromisoformat(r["on_sale"])).total_seconds() / 3600)
    return None


@dataclass
class Play:
    venue: str
    title: str
    dates: int = 0  # dates tracked on sale
    sold_out: int = 0  # of those, how many sold out
    speeds: list[float] = field(default_factory=list)  # hours to sell out, where known
    fast: list[float] = field(default_factory=list)  # the speeds that count as fast
    last_sold_out: str = ""  # when the latest sell-out was seen

    @property
    def is_fast(self) -> bool:
        return bool(self.fast)

    @property
    def fastest(self) -> float | None:
        return min(self.speeds) if self.speeds else None

    @property
    def typical(self) -> float | None:
        return median(self.speeds) if self.speeds else None

    @property
    def mark(self) -> str:
        return "🔥" if self.is_fast else "🔴" if self.sold_out else ""

    @property
    def note(self) -> str:
        """'fast seller: sold out in 4h' / 'sells out'."""
        if self.is_fast:
            quickest = min(self.fast)
            return f"fast seller: sold out {'instantly' if quickest == 0 else 'in ' + duration(quickest)}"
        return "sells out" if self.sold_out else ""

    def flag(self) -> str:
        return f"{self.mark} {self.note}".strip()

    def summary(self) -> str:
        """'sold out 4 of 5 dates · fastest 4h, typically 1 day'."""
        parts = [f"sold out {self.sold_out} of {self.dates} date{'s' if self.dates != 1 else ''}"]
        if self.speeds:
            speed = f"fastest: {'instant' if self.fastest == 0 else duration(self.fastest)}"
            if len(self.speeds) > 1 and round(self.typical) != round(self.fastest):
                speed += f", typically {duration(self.typical)}"
            parts.append(speed)
        return " · ".join(parts)


def duration(hours: float) -> str:
    if hours < 1:
        return "under 1h"
    if hours < 48:
        return f"{round(hours)}h"
    return f"{round(hours / 24)} days"


def settings(cfg: Config) -> tuple[float, float]:
    return float(cfg.get("selling", "fast_hours", 48)), float(cfg.get("selling", "min_days_before", 1))


def plays(state: State, cfg: Config) -> dict[str, Play]:
    """Every play with sell-out history, keyed by play_key(venue, title)."""
    fast_hours, min_days = settings(cfg)
    out: dict[str, Play] = {}
    for r in sorted(state.sales.values(), key=lambda r: r["start"]):
        if r["title"].strip() in ("", "?"):  # a listing whose title didn't parse
            continue
        key = play_key(r["venue"], r["title"])
        p = out.get(key) or out.setdefault(key, Play(r["venue"], r["title"]))
        p.title = r["title"]
        p.dates += 1
        if "sold_out" not in r and not r.get("instant"):
            continue
        p.sold_out += 1
        if r.get("sold_out"):
            p.last_sold_out = max(p.last_sold_out, r["sold_out"])
        h = hours_to_sell_out(r)
        if h is None:
            continue
        p.speeds.append(h)
        seen = datetime.fromisoformat(r["sold_out"]) if r.get("sold_out") else None
        lead_ok = seen is None or datetime.fromisoformat(r["start"]) - seen >= timedelta(days=min_days)
        if h <= fast_hours and lead_ok:  # a sell-out on the day of the show says little about demand
            p.fast.append(h)
    return out


def fast_plays(state: State, cfg: Config) -> dict[str, Play]:
    return {k: p for k, p in plays(state, cfg).items() if p.is_fast}


def lookup(table: dict[str, Play], perf: Performance) -> Play | None:
    return table.get(play_key(perf.venue, perf.title))


def by_play(perfs: list[Performance]) -> dict[str, list[Performance]]:
    groups: dict[str, list[Performance]] = defaultdict(list)
    for p in sorted(perfs, key=lambda p: p.start):
        groups[play_key(p.venue, p.title)].append(p)
    return groups
