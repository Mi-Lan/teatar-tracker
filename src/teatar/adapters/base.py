from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import ClassVar

from ..dates import UPCOMING_GRACE
from ..http import Http
from ..models import TZ, Performance, Status, now
from ..textnorm import normalize, slug

log = logging.getLogger(__name__)


class Adapter(ABC):
    """One source of performances.

    `method` documents how the source is read: "api" (JSON endpoints), "html" (scraping), or
    "hybrid" (HTML catalog enriched through an API). Parsing lives in module-level functions
    that take raw text, so they can be tested against saved fixtures without the network.

    `options` are the venue's extra keys in venues.yaml; list the ones the adapter reads in
    `known_options`, so a misspelt key is reported instead of silently doing nothing.
    """

    adapter_name: ClassVar[str] = ""
    method: ClassVar[str] = "html"
    known_options: ClassVar[frozenset[str]] = frozenset()
    # Set uses_previous to be given what the tracker knew before this check (the runner fills in
    # `previous`: uid -> Performance, and `first_seen`: uid -> when the date first appeared).
    uses_previous: ClassVar[bool] = False
    # An adapter can report a partial problem without failing: set `warning` during fetch() and the runner
    # treats it like a failing site named "<venue> <warning_label>" (alert after a few checks, all-clear after).
    warning_label: ClassVar[str] = "extra data"

    def __init__(self, key: str, http: Http, name: str | None = None, **options):
        self.key = key
        self.http = http
        self.name = name or key
        self.options = options
        self.previous: dict[str, Performance] = {}
        self.first_seen: dict[str, str] = {}
        self.warning = ""
        self.notes: dict = {}  # kept in the state between runs, per venue (filled in for uses_previous adapters)
        if unknown := sorted(set(options) - self.known_options):
            log.warning("venues.yaml: %s has unknown option(s) %s for adapter %s (typo?)", key, ", ".join(unknown), self.adapter_name or type(self).__name__)

    @abstractmethod
    def fetch(self) -> list[Performance]:
        """Return every upcoming performance with the best availability info the source offers."""

    def poll(self) -> list[Performance]:
        """Fast re-check used in burst mode around a ticket release. Defaults to a full fetch."""
        return self.fetch()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.key} ({self.method})>"


def upcoming(perfs: list[Performance], grace: timedelta = UPCOMING_GRACE) -> list[Performance]:
    """Drop performances that started more than `grace` ago."""
    cutoff = now() - grace
    return sorted((p for p in perfs if p.start >= cutoff), key=lambda p: (p.start, p.title))


def unique(perfs: list[Performance]) -> list[Performance]:
    """One entry per uid, the last one winning (sources read month by month can repeat a date)."""
    return list({p.uid: p for p in perfs}.values())


def at(d: date, hhmm: str | None) -> datetime:
    """Combine a date and 'HH:MM' (default 00:00) into an aware Belgrade datetime."""
    h, m = (int(x) for x in (hhmm or "0:00").split(":")[:2])
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def infer_year(month: int, day: int, today: date | None = None) -> date:
    """Pick the year for a day/month with no year (repertoires only list the near future): this
    year, unless that is more than two months back (or isn't a date: 29 February), then the next."""
    today = today or now().date()
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today - timedelta(days=60):
            return candidate
    raise ValueError(f"no such date: {day}.{month}. in {today.year} or {today.year + 1}")


_TIME = re.compile(r"\d{1,2}:\d{2}")


def find_time(text: str | None) -> str | None:
    """The first 'HH:MM' in a piece of text, if any."""
    m = _TIME.search(text or "")
    return m.group() if m else None


def text_of(el) -> str:
    """An element's text with whitespace collapsed; '' when the element is missing."""
    return el.get_text(" ", strip=True) if el else ""


def dated_id(start: datetime, title: str) -> str:
    """A stable id for sources that have none: start time + title. It is the key the date is tracked
    under in state, so changing the format would re-announce every date."""
    return f"{start:%Y%m%d%H%M}-{slug(title)}"


def label_status(label: str) -> Status | None:
    """What a site's status label says, in either script: 'Распродато' / 'RASPRODATO' → sold out,
    'Otkazano' → cancelled, 'Manje od 10% slobodnih mesta' → running low. None for anything else."""
    lab = normalize(label)
    if "rasprodat" in lab:  # rasprodat/-a/-o (not "rasprodaja", a sale)
        return Status.SOLD_OUT
    if "otkazan" in lab:
        return Status.CANCELLED
    if "manje od" in lab:
        return Status.LOW
    return None
