"""Narodno pozorište u Beogradu: server-rendered HTML, exact ticket counts on the Buy button."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Performance, Status
from ..registry import register
from ..textnorm import month_number
from .base import Adapter, at, infer_year, upcoming

BASE = "https://www.narodnopozoriste.rs"


def parse(html: str, venue: str = "narodno", today: date | None = None) -> list[Performance]:
    soup = BeautifulSoup(html, "lxml")
    perfs = []
    for entry in soup.select("div.repertoarwide-entry[id^=repid]"):
        rep_id = entry["id"].removeprefix("repid")
        date_el = entry.select_one(".repertoarwide-entry-date")
        month_el = date_el.select_one(".mesec") if date_el else None
        day = re.search(r"\d{1,2}", "".join(t for t in date_el.find_all(string=True, recursive=False))) if date_el else None
        if not (day and month_el and month_number(month_el.get_text())):
            continue
        d = infer_year(month_number(month_el.get_text()), int(day.group()), today)

        link = entry.select_one(".entry-title h4 a")
        title = link.get_text(strip=True) if link else "?"
        em = entry.select_one(".entry-title em")
        note = entry.select_one(".entry-title span.small")
        subtitle = " · ".join(x for x in [em.get_text(strip=True) if em else "", note.get_text(strip=True) if note else ""] if x)

        meta = entry.select_one(".repertoarwide-meta")
        meta_text = meta.get_text(" ", strip=True) if meta else ""
        time_m = re.search(r"\d{1,2}:\d{2}", meta_text)
        stage = meta_text.split("·", 1)[1].strip() if "·" in meta_text else ""

        perf = Performance(
            venue=venue,
            source_id=rep_id,
            title=title,
            subtitle=subtitle,
            start=at(d, time_m.group() if time_m else None),
            stage=stage,
            url=urljoin(BASE, link["href"]) if link else "",
            status=Status.NOT_ON_SALE,
            extra={"repid": rep_id},
        )
        buy = entry.select_one('a[href*="odabir-ulaznica"]')
        blob = entry.get_text(" ", strip=True).lower()
        if buy:
            count = re.search(r"\d+", buy.get("title", "") or buy.get_text())
            perf.buy_url = urljoin(BASE, buy["href"])
            perf.available = int(count.group()) if count else None
            perf.status = Status.SOLD_OUT if perf.available == 0 else Status.ON_SALE
        elif "распродат" in blob or "rasprodat" in blob:
            perf.status = Status.SOLD_OUT
        if "отказан" in blob or "otkazan" in blob:
            perf.status = Status.CANCELLED
        perfs.append(perf)
    return perfs


@register("narodno_html")
class NarodnoAdapter(Adapter):
    method = "html"

    def fetch(self) -> list[Performance]:
        html = self.http.get(f"{BASE}/repertoar").text
        return upcoming(parse(html, self.key))
