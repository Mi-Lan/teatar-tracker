"""Persistent state, committed to the repo by CI after each run (only when it changed)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .dates import UPCOMING_GRACE
from .models import CUSTOM_VENUE, Performance, now

KEEP_DAYS = 2  # performances (and known releases) stay this long after their time
NOTIFIED_DAYS = 60  # how long "already told you" keys, and the bookkeeping of dates no longer listed, are kept
LOG_DAYS = 180  # how long the release log is kept (/new looks back 90 days at most)


@dataclass
class State:
    initialized: bool = False
    performances: dict[str, dict] = field(default_factory=dict)  # uid -> Performance.to_dict()
    meta: dict[str, dict] = field(default_factory=dict)  # uid -> {"first_seen", "ever_on_sale", "on_sale_since"}
    known_titles: dict[str, list[str]] = field(default_factory=dict)  # venue -> normalized titles seen
    watchlist: list[dict] = field(default_factory=list)
    watch_imported: list[str] = field(default_factory=list)  # config/watchlist.yaml queries already imported
    releases: list[dict] = field(default_factory=list)
    notified: dict[str, str] = field(default_factory=dict)  # dedupe key -> when sent
    health: dict[str, dict] = field(default_factory=dict)
    telegram_offset: int = 0
    last_digest: str = ""  # ISO date of the last scheduled report
    season: dict = field(default_factory=dict)  # next-month status for the 20th–27th checks (see season.py)
    release_log: list[dict] = field(default_factory=list)  # when each theatre released each month (see season.py)
    scanned: dict[str, str] = field(default_factory=dict)  # venue -> time of its last successful check
    sales: dict[str, dict] = field(default_factory=dict)  # uid -> sell-out record, kept after the show (see sales.py)
    sales_seed: str = ""  # which data/sales_seed.json was imported (see sales.import_seed)

    # --- persistence -------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Starting over would re-announce every date, so stop instead; the file is in git.
            raise ValueError(f"{path} is not valid JSON ({exc}); restore it with `git checkout <commit> -- {path.name}`") from exc
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})  # fields from other versions are dropped

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1, sort_keys=True) + "\n"

    def save(self, path: Path) -> bool:
        """Write the file only if its content changed. Returns True when written.
        Written to a temporary file and moved into place, so a run killed mid-write (a timeout, a
        cancelled job) can't leave half a file for CI to commit."""
        text = self.dumps()
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True

    # --- helpers -----------------------------------------------------------------------------
    def perfs(self) -> list[Performance]:
        return sorted((Performance.from_dict(d) for d in self.performances.values()), key=lambda p: (p.start, p.venue, p.title))

    def catalog(self) -> list[Performance]:
        """What lists and overviews show: upcoming performances at the theatres (no custom lookups)."""
        cutoff = now() - UPCOMING_GRACE
        return [p for p in self.perfs() if p.venue != CUSTOM_VENUE and p.start >= cutoff]

    def venue_dicts(self, venue: str) -> dict[str, dict]:
        """uid -> stored performance, for one venue."""
        return {u: d for u, d in self.performances.items() if d["venue"] == venue}

    def was_notified(self, key: str) -> bool:
        return key in self.notified

    def mark_notified(self, key: str) -> None:
        self.notified[key] = now().isoformat(timespec="seconds")

    def prune(self, keep_days: int = KEEP_DAYS, history_days: int = 180) -> None:
        from . import sales

        sales.prune(self, history_days)
        t = now()
        cutoff = t - timedelta(days=keep_days)
        for uid, d in list(self.performances.items()):
            if datetime.fromisoformat(d["start"]) < cutoff:
                del self.performances[uid]
                self.meta.pop(uid, None)
        old = (t - timedelta(days=NOTIFIED_DAYS)).isoformat()
        self.notified = {k: v for k, v in self.notified.items() if v >= old}
        # A date that drops off a site keeps its bookkeeping for a while, in case it comes back; after
        # that it goes too (most leave by simply being over, and nothing else would ever remove them).
        self.meta = {u: m for u, m in self.meta.items() if u in self.performances or m.get("first_seen", "") >= old}
        self.releases = [r for r in self.releases if datetime.fromisoformat(r["at"]) > cutoff]
        log_cutoff = (t - timedelta(days=LOG_DAYS)).isoformat()
        self.release_log = [e for e in self.release_log if e.get("by", "") >= log_cutoff]
