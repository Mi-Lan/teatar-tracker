"""Persistent state, committed to the repo by CI after each run (only when it changed)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .models import Performance, now


@dataclass
class State:
    initialized: bool = False
    performances: dict[str, dict] = field(default_factory=dict)  # uid -> Performance.to_dict()
    meta: dict[str, dict] = field(default_factory=dict)  # uid -> {"first_seen", "ever_on_sale"}
    known_titles: dict[str, list[str]] = field(default_factory=dict)  # venue -> normalized titles seen
    watchlist: list[dict] = field(default_factory=list)
    watch_imported: list[str] = field(default_factory=list)  # config/watchlist.yaml queries already imported
    releases: list[dict] = field(default_factory=list)
    notified: dict[str, str] = field(default_factory=dict)  # dedupe key -> when sent
    health: dict[str, dict] = field(default_factory=dict)
    telegram_offset: int = 0
    last_digest: str = ""  # ISO date of the last scheduled report

    # --- persistence -------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1, sort_keys=True) + "\n"

    def save(self, path: Path) -> bool:
        """Write the file only if its content changed. Returns True when written."""
        text = self.dumps()
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True

    # --- helpers -----------------------------------------------------------------------------
    def perfs(self) -> list[Performance]:
        return sorted((Performance.from_dict(d) for d in self.performances.values()), key=lambda p: (p.start, p.venue, p.title))

    def was_notified(self, key: str) -> bool:
        return key in self.notified

    def mark_notified(self, key: str) -> None:
        self.notified[key] = now().isoformat(timespec="seconds")

    def prune(self, keep_days: int = 2) -> None:
        cutoff = now() - timedelta(days=keep_days)
        for uid, d in list(self.performances.items()):
            if datetime.fromisoformat(d["start"]) < cutoff:
                del self.performances[uid]
                self.meta.pop(uid, None)
        old = (now() - timedelta(days=60)).isoformat()
        self.notified = {k: v for k, v in self.notified.items() if v >= old}
        self.releases = [r for r in self.releases if datetime.fromisoformat(r["at"]) > cutoff]
