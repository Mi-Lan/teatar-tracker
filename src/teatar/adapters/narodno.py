"""Narodno pozorište u Beogradu: server-rendered HTML.

Two layouts are understood: the agenda redesign (article.np-agenda-item, since 2026-09-23) and
the older list (div.repertoarwide-entry). Whichever the page contains is parsed.

Availability: a "Купи" button = on sale; "Мање од 10% слободних места" next to it = running low;
"РАСПРОДАТО" = sold out; "ОТКАЗАНО" = cancelled. Until 2026-10-15 the Buy button also carried the
exact number of tickets left ("Купи [ 271 ]"), which is still read when present.

Exact counts now: each date's ticket page (/ulaznice/odabir-ulaznica?p=<id>) is server-rendered with
every seat as <button class="np-seat …">, sold ones carrying `prodato`. It needs a login, so with
NARODNO_EMAIL / NARODNO_PASSWORD set (GitHub secrets) the tracker logs in only when a count is due
(`seat_counts` in venues.yaml) and reads ticket pages with GET only: nothing is selected or held.
  - once a day, at the first check after `daily_at` (09:00): every on-sale date;
  - after a release: dates first seen at least `after_release_minutes` (90) ago and never counted,
    i.e. at the next regular check (the release message itself goes out at once, from the listing).
Other checks don't log in; the last counts are carried over (shown as ~N once they're a few hours old).
Each count is also kept in the date's sell-out history (sales.py), so how fast seats go is on record.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..models import Performance, Status, now
from ..registry import register
from ..textnorm import month_number
from .base import Adapter, at, infer_year, upcoming

BASE = "https://www.narodnopozoriste.rs"
LOGIN_PAGE = f"{BASE}/nalog/prijavi-se"
API = f"{BASE}/api/ulaznice"

log = logging.getLogger(__name__)


def _txt(el: Tag | None) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _agenda_fields(e: Tag) -> dict:
    date_el = e.select_one(".np-agenda-date")
    day = _txt(date_el.find("strong")) if date_el else ""
    month = _txt(date_el.find("span")).split("·")[0] if date_el else ""
    desc = _txt(e.select_one(".np-agenda-description")).strip("· ").strip()
    return {
        "day": day, "month": month, "time": _txt(e.select_one(".np-agenda-time")),
        "stage": _txt(e.select_one(".np-agenda-stage")), "link": e.select_one("h2 a"), "heading": e.select_one("h2"),
        "subtitle": " · ".join(x for x in [_txt(e.select_one(".np-writer")), desc] if x),
        "actions": e.select_one(".np-agenda-actions"),
        # cancelled dates lose their links and get a label on the image, outside the actions
        "cancelled": "is-cancelled" in (e.get("class") or []) or "отказан" in _txt(e.select_one(".np-agenda-status")).lower(),
    }


def _legacy_fields(e: Tag) -> dict:
    date_el = e.select_one(".repertoarwide-entry-date")
    day = "".join(date_el.find_all(string=True, recursive=False)) if date_el else ""
    meta = _txt(e.select_one(".repertoarwide-meta"))
    em, note = e.select_one(".entry-title em"), e.select_one(".entry-title span.small")
    return {
        "day": day, "month": _txt(date_el.select_one(".mesec")) if date_el else "", "time": meta,
        "stage": meta.split("·", 1)[1].strip() if "·" in meta else "", "link": e.select_one(".entry-title h4 a"),
        "heading": e.select_one(".entry-title h4"),
        "subtitle": " · ".join(x for x in [_txt(em), _txt(note)] if x), "actions": e, "cancelled": False,
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
            title=_txt(link) or _txt(f["heading"]) or "?",
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
            low = (actions.select_one(".np-ticket-low-stock") if actions else None) or "мање од" in blob
            perf.status = Status.SOLD_OUT if perf.available == 0 else Status.LOW if low else Status.ON_SALE
        elif "распродат" in blob or "rasprodat" in blob or (actions and actions.select_one(".np-ticket-button--sold")):
            perf.status = Status.SOLD_OUT
        if f["cancelled"] or "отказан" in blob or "otkazan" in blob:
            perf.status = Status.CANCELLED
        perfs.append(perf)
    return perfs


def count_seats(html: str) -> tuple[int, int] | None:
    """(seats, free) on a ticket page; None when it has no seat map (e.g. the login page)."""
    seats = BeautifulSoup(html, "lxml").select("button.np-seat")
    if not seats:
        return None
    free = sum(1 for b in seats if "prodato" not in (b.get("class") or []) and not b.has_attr("disabled"))
    return len(seats), free


def apply_seats(p: Performance, seats: int, free: int, counted_at: str) -> None:
    p.available = free
    p.extra["seats"] = seats
    p.extra["counted_at"] = counted_at
    if free == 0:
        p.status = Status.SOLD_OUT
    elif p.status == Status.ON_SALE and free < seats / 10:
        p.status = Status.LOW


@register("narodno_html")
class NarodnoAdapter(Adapter):
    method = "html"
    uses_previous = True
    warning = ""  # set when seat counts couldn't be read; the runner reports it like a failing site

    def fetch(self) -> list[Performance]:
        perfs = self.poll()
        self.warning = ""
        due = self.counts_due(perfs)
        if due and os.environ.get("NARODNO_EMAIL") and os.environ.get("NARODNO_PASSWORD"):
            try:
                self._seat_counts(due)
            except Exception as exc:  # the listing (and the carried-over counts) are still good
                log.warning("narodno seat counts failed: %s", exc)
                self.warning = f"seat counts: {type(exc).__name__}: {exc}"
        return perfs

    def poll(self) -> list[Performance]:
        """The repertoire listing, with the last seat counts carried over (no login): every regular check,
        and the 30-second polls around a release."""
        html = self.http.get(f"{BASE}/repertoar").text
        perfs = parse(html, self.key)
        if not perfs:  # the repertoire is never empty: treat it as a layout change
            raise RuntimeError("no performances parsed from /repertoar (page layout changed?)")
        perfs = upcoming(perfs)
        for p in perfs:
            o = self.previous.get(p.uid)
            if o and o.extra.get("counted_at") and o.available is not None and p.available is None and p.status.buyable:
                apply_seats(p, o.extra["seats"], o.available, o.extra["counted_at"])
        return perfs

    def counts_due(self, perfs: list[Performance]) -> list[Performance]:
        """On-sale dates to count now: all of them once a day, else new ones since the last release."""
        opts = self.options.get("seat_counts") or {}
        t = now()
        daily = datetime.combine(t.date(), time.fromisoformat(str(opts.get("daily_at", "09:00"))), t.tzinfo)
        after = timedelta(minutes=int(opts.get("after_release_minutes", 90)))
        buyable = [p for p in perfs if p.status.buyable and p.buy_url]
        last = max((o.extra.get("counted_at", "") for o in self.previous.values()), default="")
        if t >= daily and last < daily.isoformat():
            return buyable
        return [
            p for p in buyable
            if "counted_at" not in p.extra and (seen := self.first_seen.get(p.uid)) and t - datetime.fromisoformat(seen) >= after
        ]

    def _login(self) -> None:
        page = BeautifulSoup(self.http.get(LOGIN_PAGE).text, "lxml")
        form = page.select_one('form[data-account-form="login"]')
        if form is None:
            raise RuntimeError("login form not found")
        data = {i["name"]: i.get("value", "") for i in form.select("input[type=hidden][name]")}
        data |= {"email": os.environ["NARODNO_EMAIL"], "pass": os.environ["NARODNO_PASSWORD"]}
        reply = self.http.post(API, json=data, headers={"Accept": "application/json"}).json()
        if reply.get("error"):
            raise RuntimeError(f"login failed: {reply.get('errorMessage') or reply.get('message') or reply.get('error')}")

    def _seat_counts(self, perfs: list[Performance]) -> None:
        self._login()
        for p in perfs:
            resp = self.http.get(p.buy_url)
            if "prijavi-se" in str(resp.url):  # session dropped: log in again, once
                self._login()
                resp = self.http.get(p.buy_url)
                if "prijavi-se" in str(resp.url):
                    raise RuntimeError("still sent to the login page after logging in")
            if counted := count_seats(resp.text):
                apply_seats(p, *counted, now().isoformat(timespec="seconds"))
