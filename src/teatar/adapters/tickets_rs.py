"""tickets.rs: one JSON RPC endpoint (`/web/api/data/agnosticget`) serves every venue page.

Any tickets.rs venue can be tracked by adding its slug (the part after /venue/) in venues.yaml.
"""

from __future__ import annotations

import json
import re
from datetime import date

from ..models import Performance, Status
from ..registry import register
from ..textnorm import normalize
from .base import Adapter, at, upcoming

API = "https://tickets.rs/web/api/data/agnosticget"
SITE = "https://tickets.rs"
# Public session values baked into https://tickets.rs/assets/sales_app/app_config.js
SESSION = {"UserName": "tickets.rs", "WebSession": "", "SessionCode": "12C1E0F33673E440A77C47D316944EB8", "UserType": 2, "Lang": "SR"}
EVENT_LIST_WIDGET = 25  # the "event-list" widget on venue pages


def unwrap(payload: dict) -> dict:
    d = payload.get("d", payload)
    return json.loads(d) if isinstance(d, str) else d


def parse_events(events: list[dict], venue: str) -> list[Performance]:
    perfs = []
    for ev in events:
        if ev.get("Type") != "event" or not ev.get("StartDate"):
            continue
        tm = re.search(r"\d{1,2}:\d{2}", ev.get("DateTime", ""))
        status_text = normalize(ev.get("EventStatus") or "")
        if "rasprod" in status_text:
            status = Status.SOLD_OUT
        elif "otkaz" in status_text:
            status = Status.CANCELLED
        elif "uskoro" in status_text or "najav" in status_text:
            status = Status.NOT_ON_SALE
        else:
            status = Status.ON_SALE  # listed with a price on the ticket shop = sellable
        price = ev.get("Price")
        perfs.append(
            Performance(
                venue=venue,
                source_id=str(ev["Id"]),
                title=ev.get("Title", "").strip(),
                start=at(date.fromisoformat(ev["StartDate"]), tm.group() if tm else None),
                stage=ev.get("Venue", "").partition(" - ")[2],  # "Sava Centar - Plava dvorana" → hall only
                url=f"{SITE}/{ev['Slug']}",
                buy_url=f"{SITE}/{ev['Slug']}",
                status=status,
                price=f"od {price} RSD" if price else "",
                extra={"event_status": ev.get("EventStatus") or "", "end_date": ev.get("EndDate")},
            )
        )
    return perfs


@register("tickets_rs")
class TicketsRsAdapter(Adapter):
    method = "api"

    def __init__(self, key, http, slug: str, **options):
        super().__init__(key, http, **options)
        self.slug = slug

    def _call(self, sproc: str, **params) -> dict:
        body = {"JSONParams": {**SESSION, "Slug": "", "LayoutType": "", **params}, "sproc": sproc}
        data = unwrap(self.http.post(API, json=body).json())
        if data.get("RetMessage") != "OK":
            raise RuntimeError(f"tickets.rs {sproc}: {data.get('RetMessage')}")
        return data["data"]

    def fetch(self) -> list[Performance]:
        events, page = [], 1
        while True:
            data = self._call("web__Get_EventList", Slug=self.slug, LayoutType="venue", Search={}, WidgetID=EVENT_LIST_WIDGET, CurrentPage=page)
            events += data.get("Events") or []
            if not data.get("Events") or len(events) >= data.get("TotalItems", 0) or page >= 20:
                break
            page += 1
        return upcoming(parse_events(events, self.key))
