from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import ClassVar

from ..http import Http
from ..models import TZ, Performance, now


class Adapter(ABC):
    """One source of performances.

    `method` documents how the source is read: "api" (JSON endpoints), "html" (scraping), or
    "hybrid" (HTML catalog enriched through an API). Parsing lives in module-level functions
    that take raw text, so they can be tested against saved fixtures without the network.
    """

    adapter_name: ClassVar[str] = ""
    method: ClassVar[str] = "html"

    def __init__(self, key: str, http: Http, name: str | None = None, **options):
        self.key = key
        self.http = http
        self.name = name or key
        self.options = options

    @abstractmethod
    def fetch(self) -> list[Performance]:
        """Return every upcoming performance with the best availability info the source offers."""

    def poll(self) -> list[Performance]:
        """Fast re-check used in burst mode around a ticket release. Defaults to a full fetch."""
        return self.fetch()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.key} ({self.method})>"


def upcoming(perfs: list[Performance], grace: timedelta = timedelta(hours=3)) -> list[Performance]:
    """Drop performances that started more than `grace` ago."""
    cutoff = now() - grace
    return sorted((p for p in perfs if p.start >= cutoff), key=lambda p: (p.start, p.title))


def at(d: date, hhmm: str | None) -> datetime:
    """Combine a date and 'HH:MM' (default 00:00) into an aware Belgrade datetime."""
    h, m = (int(x) for x in (hhmm or "0:00").split(":")[:2])
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def infer_year(month: int, day: int, today: date | None = None) -> date:
    """Pick the year for a day/month with no year (repertoires only list the near future)."""
    today = today or now().date()
    candidate = date(today.year, month, day)
    if candidate < today - timedelta(days=60):
        candidate = date(today.year + 1, month, day)
    return candidate
