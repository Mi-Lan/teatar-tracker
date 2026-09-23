"""Known ticket-release times: reminders beforehand and burst-polling windows around them.

A release is {"id", "venue", "at" (ISO), "all_day" (bool), "note", "source": "manual"|"auto"}.
Timed releases get a short burst (default T-10m … T+45m, every 30 s). All-day releases (only the
date is known) get a long window (default 08:00–22:00, every 60 s) that successive runs continue.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Config, parse_duration
from .models import TZ, Performance, now
from .state import State


def release_id(venue: str, at: datetime) -> str:
    return hashlib.sha1(f"{venue}|{at.isoformat()}".encode()).hexdigest()[:8]


def make_release(venue: str, at: datetime, all_day: bool = False, note: str = "", source: str = "manual") -> dict:
    return {"id": release_id(venue, at), "venue": venue, "at": at.isoformat(), "all_day": all_day, "note": note, "source": source}


def parse_when(text: str, today: date | None = None) -> tuple[datetime, bool]:
    """Parse '2026-10-01 10:00', '01.10.2026 10:00', '1.10. 10:00' or a date alone (→ all-day)."""
    today = today or now().date()
    text = text.strip()
    tm = re.search(r"(\d{1,2}):(\d{2})\s*$", text)
    date_part = text[: tm.start()].strip() if tm else text
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_part):
        d = date(int(m[1]), int(m[2]), int(m[3]))
    elif m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?\.?", date_part):
        d = date(int(m[3]) if m[3] else today.year, int(m[2]), int(m[1]))
        if not m[3] and d < today:
            d = d.replace(year=d.year + 1)
    else:
        raise ValueError(f"can't read date {date_part!r}; use 2026-10-01 10:00 or 1.10. 10:00")
    if tm:
        return datetime.combine(d, time(int(tm[1]), int(tm[2])), TZ), False
    return datetime.combine(d, time(0, 0), TZ), True


def sync(state: State, cfg: Config, perfs: list[Performance]) -> list[dict]:
    """Import releases from config and from performances with a future sales start. Returns new ones."""
    added = []
    existing = {r["id"] for r in state.releases}
    candidates = []
    for r in cfg.releases:
        at, all_day = parse_when(str(r["at"]))
        candidates.append(make_release(r["venue"], at, all_day, r.get("note", ""), "config"))
    for p in perfs:
        if p.sales_start and p.sales_start > now():
            candidates.append(make_release(p.venue, p.sales_start, False, f"{p.title} {p.start:%d.%m.}", "auto"))
    for r in candidates:
        removed = f"unrelease:{r['id']}" in state.notified
        if r["id"] not in existing and not removed and window(r, cfg).end > now():
            state.releases.append(r)
            existing.add(r["id"])
            added.append(r)
    state.releases.sort(key=lambda r: r["at"])
    return added


@dataclass
class Window:
    release: dict
    start_poll: datetime
    end: datetime
    poll_seconds: int


def window(release: dict, cfg: Config) -> Window:
    at = datetime.fromisoformat(release["at"])
    if release.get("all_day"):
        start_s, end_s = cfg.get("releases", "all_day_window", ["08:00", "22:00"])
        start = datetime.combine(at.date(), time.fromisoformat(start_s), TZ)
        end = datetime.combine(at.date(), time.fromisoformat(end_s), TZ)
        return Window(release, start, end, int(cfg.get("releases", "all_day_poll_seconds", 60)))
    return Window(
        release,
        at - timedelta(minutes=cfg.get("releases", "start_before_minutes", 10)),
        at + timedelta(minutes=cfg.get("releases", "stop_after_minutes", 45)),
        int(cfg.get("releases", "poll_seconds", 30)),
    )


def active_windows(state: State, cfg: Config, at: datetime | None = None) -> list[Window]:
    """Releases whose burst window is open now or opens within the lookahead."""
    at = at or now()
    lookahead = timedelta(minutes=cfg.get("releases", "lookahead_minutes", 75))
    out = []
    for r in state.releases:
        w = window(r, cfg)
        if w.start_poll - lookahead <= at < w.end:
            out.append(w)
    return out


def due_reminders(state: State, cfg: Config, at: datetime | None = None) -> list[tuple[dict, str]]:
    """(release, label) pairs to send now. Only the most imminent due reminder is sent per release."""
    at = at or now()
    labels = sorted(cfg.get("releases", "remind_before", ["24h", "1h"]), key=parse_duration)
    out = []
    for r in state.releases:
        t = window(r, cfg).start_poll if r.get("all_day") else datetime.fromisoformat(r["at"])
        if at >= t:
            continue
        due = [lab for lab in labels if at >= t - parse_duration(lab)]
        if not due:
            continue
        keys = [f"remind:{r['id']}:{lab}" for lab in due]
        if all(k in state.notified for k in keys):
            continue
        for k in keys:
            state.mark_notified(k)
        out.append((r, due[0]))
    return out
