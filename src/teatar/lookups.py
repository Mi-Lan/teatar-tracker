"""Custom per-play lookups, configured on a watchlist entry with `lookup: {<method>: <value>, ...}`.

They let a single play be checked directly (API when one exists, scraping otherwise), independent
of the venue adapters. Results are tracked under the virtual venue "custom".
To add a method, write a function taking (http, value, entry) and decorate it with @lookup("name").
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

from .http import Http
from .models import TZ, Performance, Status, now
from .releases import parse_when
from .textnorm import normalize

VENUE = "custom"
LOOKUPS: dict[str, Callable[[Http, str, dict], list[Performance]]] = {}


def lookup(name: str):
    def deco(fn):
        LOOKUPS[name] = fn
        return fn

    return deco


@lookup("sixtix")
def sixtix(http: Http, slug: str, entry: dict) -> list[Performance]:
    """Sixtix event API: sale state and activation (sales start) time for one event."""
    d = http.get(f"https://api.sixtix.com/v1/EventPublicInfo/BySlug/{slug}").json()
    activation = datetime.fromisoformat(d["activationTime"]).replace(tzinfo=TZ) if d.get("activationTime") else None
    on_sale = d.get("isSalesAvailable") and (activation is None or activation <= now())
    return [
        Performance(
            venue=VENUE,
            source_id=f"sixtix-{slug}",
            title=entry.get("query") or d.get("eventName", slug),
            start=datetime.fromisoformat(d["startDate"]).replace(tzinfo=TZ),
            stage=d.get("prefixLabel", ""),
            buy_url=f"https://app.sixtix.com/events/{slug}",
            status=Status.ON_SALE if on_sale else Status.NOT_ON_SALE,
            sales_start=activation if activation and activation > now() else None,
            extra={"lookup": "sixtix"},
        )
    ]


@lookup("page")
def page(http: Http, url: str, entry: dict) -> list[Performance]:
    """Scrape any page: 'on sale' when `pattern` (regex, case-insensitive) appears in it."""
    pattern = entry["lookup"].get("pattern", "kupi|купи|buy")
    found = re.search(pattern, http.get(url).text, re.I) is not None
    start = parse_when(str(entry["date"]))[0] if entry.get("date") else now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return [
        Performance(
            venue=VENUE,
            source_id=f"page-{normalize(entry.get('query', url)).replace(' ', '-')[:40]}",
            title=entry.get("query", url),
            start=start,
            url=url,
            buy_url=url,
            status=Status.ON_SALE if found else Status.NOT_ON_SALE,
            extra={"lookup": "page", "undated": not entry.get("date")},
        )
    ]


def run_all(http: Http, watchlist: list[dict]) -> list[Performance]:
    perfs = []
    for entry in watchlist:
        spec = entry.get("lookup") or {}
        for name, value in spec.items():
            if name in LOOKUPS:
                perfs += LOOKUPS[name](http, value, entry)
    return perfs
