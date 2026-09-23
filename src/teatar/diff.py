"""Compare the previous snapshot with a fresh scan and describe what changed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import Performance, Status, now
from .state import State
from .textnorm import matches


class Kind(StrEnum):
    NEW_SHOW = "new_show"  # a title never seen before at this venue
    NEW_DATE = "new_date"  # another performance of a known title
    SALES_OPENED = "sales_opened"  # first time this performance became buyable
    BACK_IN_STOCK = "back_in_stock"  # buyable again after selling out / closing
    SOLD_OUT = "sold_out"
    LOW = "low"
    CANCELLED = "cancelled"


# Changes reported for every venue; the rest only for watched plays.
ALWAYS = {Kind.NEW_SHOW, Kind.NEW_DATE, Kind.SALES_OPENED}
# Changes that are reported once per performance, ever.
ONCE = {Kind.NEW_SHOW, Kind.NEW_DATE, Kind.SALES_OPENED}


@dataclass
class Change:
    kind: Kind
    perf: Performance
    old: Performance | None = None
    watched: bool = False

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.perf.uid}"

    @property
    def reportable(self) -> bool:
        return self.kind in ALWAYS or self.watched


def watch_entry(p: Performance, watchlist: list[dict]) -> dict | None:
    for w in watchlist:
        if w.get("venue") and w["venue"] != p.venue:
            continue
        if matches(w["query"], p.title):
            return w
    return None


def compute(state: State, scanned: dict[str, list[Performance]], watchlist: list[dict], low_threshold: int = 20) -> list[Change]:
    """Changes between state and fresh results. Venues seen for the first time are a silent baseline."""
    changes = []
    for venue, perfs in scanned.items():
        if venue not in state.known_titles:
            continue
        known = set(state.known_titles[venue])
        for p in perfs:
            w = watch_entry(p, watchlist)
            watched = w is not None
            old_d = state.performances.get(p.uid)
            if old_d is None:
                changes.append(Change(Kind.NEW_SHOW if p.title_norm not in known else Kind.NEW_DATE, p, None, watched))
                continue
            o = Performance.from_dict(old_d)
            ever = state.meta.get(p.uid, {}).get("ever_on_sale", False)
            if p.status.buyable and not o.status.buyable:
                kind = Kind.BACK_IN_STOCK if (o.status == Status.SOLD_OUT or ever) else Kind.SALES_OPENED
                changes.append(Change(kind, p, o, watched))
            elif o.status.buyable and p.status in (Status.SOLD_OUT, Status.NOT_ON_SALE) and p.start > now():
                changes.append(Change(Kind.SOLD_OUT, p, o, watched))
            elif p.status == Status.CANCELLED and o.status != Status.CANCELLED:
                changes.append(Change(Kind.CANCELLED, p, o, watched))
            elif watched and p.status.buyable:
                limit = int(w.get("low_threshold", low_threshold))
                became_low = p.status == Status.LOW and o.status != Status.LOW
                dropped = p.available is not None and o.available is not None and p.available < limit <= o.available
                if became_low or dropped:
                    changes.append(Change(Kind.LOW, p, o, watched))
    return changes


def apply(state: State, scanned: dict[str, list[Performance]]) -> None:
    """Replace each scanned venue's performances in state and update bookkeeping."""
    stamp = now().isoformat(timespec="seconds")
    for venue, perfs in scanned.items():
        for uid in [u for u, d in state.performances.items() if d["venue"] == venue]:
            del state.performances[uid]
        titles = set(state.known_titles.get(venue, []))
        for p in perfs:
            state.performances[p.uid] = p.to_dict()
            meta = state.meta.setdefault(p.uid, {"first_seen": stamp, "ever_on_sale": False})
            meta["ever_on_sale"] = meta["ever_on_sale"] or p.status.buyable
            titles.add(p.title_norm)
        state.known_titles[venue] = sorted(titles)
