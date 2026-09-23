"""Narodno pozorište u Beogradu: server-rendered HTML, exact ticket counts on the Buy button.

Two layouts are understood: the agenda redesign (article.np-agenda-item, since 2026-09-23) and
the older list (div.repertoarwide-entry). Whichever the page contains is parsed.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..models import Performance, Status
from ..registry import register
from ..textnorm import month_number
from .base import Adapter, at, infer_year, upcoming

BASE = "https://www.narodnopozoriste.rs"


def _txt(el: Tag | None) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _agenda_fields(e: Tag) -> dict:
    date_el = e.select_one(".np-agenda-date")
    day = _txt(date_el.find("strong")) if date_el else ""
    month = _txt(date_el.find("span")).split("·")[0] if date_el else ""
    desc = _txt(e.select_one(".np-agenda-description")).strip("· ").strip()
    return {
        "day": day, "month": month, "time": _txt(e.select_one(".np-agenda-time")),
        "stage": _txt(e.select_one(".np-agenda-stage")), "link": e.select_one("h2 a"),
        "subtitle": " · ".join(x for x in [_txt(e.select_one(".np-writer")), desc] if x),
        "actions": e.select_one(".np-agenda-actions"),
    }


def _legacy_fields(e: Tag) -> dict:
    date_el = e.select_one(".repertoarwide-entry-date")
    day = "".join(date_el.find_all(string=True, recursive=False)) if date_el else ""
    meta = _txt(e.select_one(".repertoarwide-meta"))
    em, note = e.select_one(".entry-title em"), e.select_one(".entry-title span.small")
    return {
        "day": day, "month": _txt(date_el.select_one(".mesec")) if date_el else "", "time": meta,
        "stage": meta.split("·", 1)[1].strip() if "·" in meta else "", "link": e.select_one(".entry-title h4 a"),
        "subtitle": " · ".join(x for x in [_txt(em), _txt(note)] if x), "actions": e,
    }


def parse(html: str, venue: str = "narodno", today: date | None = None) -> list[Performance]:
    soup = BeautifulSoup(html, "lxml")
    entries = [(e, _agenda_fields) for e in soup.select("article.np-agenda-item[id^=repid]")]
    entries = entries or [(e, _legacy_fields) for e in soup.select("div.repertoarwide-entry[id^=repid]")]
    perfs = []
    for entry, fields in entries:
        f = fields(entry)
        day, month = re.search(r"\d{1,2}", f["day"]), month_number(f["month"].strip())
        if not (day and month):
            continue
        rep_id = entry["id"].removeprefix("repid")
        time_m = re.search(r"\d{1,2}:\d{2}", f["time"])
        link = f["link"]
        perf = Performance(
            venue=venue,
            source_id=rep_id,
            title=_txt(link) or "?",
            subtitle=f["subtitle"],
            start=at(infer_year(month, int(day.group()), today), time_m.group() if time_m else None),
            stage=f["stage"],
            url=urljoin(BASE, link["href"]) if link else "",
            status=Status.NOT_ON_SALE,
            extra={"repid": rep_id},
        )
        actions = f["actions"]
        buy = actions.select_one('a[href*="odabir-ulaznica"]') if actions else None
        blob = _txt(actions).lower()
        if buy:
            count = re.search(r"\d+", buy.get("title", "") or buy.get_text())
            perf.buy_url = urljoin(BASE, buy["href"])
            perf.available = int(count.group()) if count else None
            perf.status = Status.SOLD_OUT if perf.available == 0 else Status.ON_SALE
        elif "распродат" in blob or "rasprodat" in blob or (actions and actions.select_one(".np-ticket-button--sold")):
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
        perfs = parse(html, self.key)
        if not perfs:  # the repertoire is never empty: treat it as a layout change
            raise RuntimeError("no performances parsed from /repertoar (page layout changed?)")
        return upcoming(perfs)
