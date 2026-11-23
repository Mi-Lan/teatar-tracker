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
Gently, so the site doesn't take it for an attack: one ticket page every `page_seconds` (3), at most one
login per `min_gap_minutes` (60), and after a failed count none for `retry_hours` (3). Redirects on
ticket pages are followed one at a time (at most 3), never in a loop.
Each count is also kept in the date's sell-out history (sales.py), so how fast seats go is on record.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..dates import parse_time
from ..models import Performance, Status, now
from ..registry import register
from ..textnorm import month_number, normalize
from .base import Adapter, at, find_time, infer_year, text_of, upcoming

BASE = "https://www.narodnopozoriste.rs"
LOGIN_PAGE = f"{BASE}/nalog/prijavi-se"
API = f"{BASE}/api/ulaznice"

log = logging.getLogger(__name__)


class RedirectLoop(Exception):
    """A ticket page that keeps redirecting (not to the login page)."""


def _agenda_fields(e: Tag) -> dict:
    date_el = e.select_one(".np-agenda-date")
    day = text_of(date_el.find("strong")) if date_el else ""
    month = text_of(date_el.find("span")).split("·")[0] if date_el else ""
    desc = text_of(e.select_one(".np-agenda-description")).strip("· ").strip()
    return {
        "day": day, "month": month, "time": text_of(e.select_one(".np-agenda-time")),
        "stage": text_of(e.select_one(".np-agenda-stage")), "link": e.select_one("h2 a"), "heading": e.select_one("h2"),
        "subtitle": " · ".join(x for x in [text_of(e.select_one(".np-writer")), desc] if x),
        "actions": e.select_one(".np-agenda-actions"),
        # cancelled dates lose their links and get a label on the image, outside the actions
        "cancelled": "is-cancelled" in (e.get("class") or []) or "otkazan" in normalize(text_of(e.select_one(".np-agenda-status"))),
    }


def _legacy_fields(e: Tag) -> dict:
    date_el = e.select_one(".repertoarwide-entry-date")
    day = "".join(date_el.find_all(string=True, recursive=False)) if date_el else ""
    meta = text_of(e.select_one(".repertoarwide-meta"))
    em, note = e.select_one(".entry-title em"), e.select_one(".entry-title span.small")
    return {
        "day": day, "month": text_of(date_el.select_one(".mesec")) if date_el else "", "time": meta,
        "stage": meta.split("·", 1)[1].strip() if "·" in meta else "", "link": e.select_one(".entry-title h4 a"),
        "heading": e.select_one(".entry-title h4"),
        "subtitle": " · ".join(x for x in [text_of(em), text_of(note)] if x), "actions": e, "cancelled": False,
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
        try:
            start = at(infer_year(month, int(day.group()), today), find_time(f["time"]))
        except ValueError:  # a date that doesn't exist: skip the entry, keep the rest of the listing
            log.warning("narodno: unreadable date %r %r for repid %s", f["day"], f["month"], rep_id)
            continue
        link = f["link"]
        perf = Performance(
            venue=venue,
            source_id=rep_id,
            title=text_of(link) or text_of(f["heading"]) or "?",
            subtitle=f["subtitle"],
            start=start,
            stage=f["stage"],
            url=urljoin(BASE, link["href"]) if link else "",
            status=Status.NOT_ON_SALE,
            extra={"repid": rep_id},
        )
        actions = f["actions"]
        buy = actions.select_one('a[href*="odabir-ulaznica"]') if actions else None
        blob = normalize(text_of(actions))  # Cyrillic or Latin, any case
        if buy:
            count = re.search(r"\d+", buy.get("title", "") or buy.get_text())
            perf.buy_url = urljoin(BASE, buy["href"])
            perf.available = int(count.group()) if count else None
            low = actions.select_one(".np-ticket-low-stock") or "manje od" in blob
            perf.status = Status.SOLD_OUT if perf.available == 0 else Status.LOW if low else Status.ON_SALE
        elif "rasprodat" in blob or (actions and actions.select_one(".np-ticket-button--sold")):
            perf.status = Status.SOLD_OUT
        if f["cancelled"] or "otkazan" in blob:
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
    known_options = frozenset({"seat_counts"})
    uses_previous = True
    warning_label = "seat counts"  # `warning` is set when they couldn't be read; the listing is still good

    sleep = staticmethod(time.sleep)  # between ticket pages (tests replace it)

    def fetch(self) -> list[Performance]:
        perfs = self.poll()
        creds = os.environ.get("NARODNO_EMAIL") and os.environ.get("NARODNO_PASSWORD")
        self.warning = self.notes.get("seats_error", "") if creds else ""  # until a count works again
        due = self.counts_due(perfs)
        if due and creds:
            if paused := self._paused():
                log.info("narodno seat counts: %s", paused)
                return perfs
            self.notes["seats_tried"] = now().isoformat(timespec="seconds")
            try:
                self._seat_counts(due)
            except Exception as exc:  # the listing (and the carried-over counts) are still good
                log.warning("narodno seat counts failed: %s", exc)
                self.warning = f"seat counts: {type(exc).__name__}: {exc}"
                self.notes["seats_error"] = self.warning
            else:
                self.warning = ""
                self.notes.pop("seats_error", None)
        return perfs

    def _paused(self) -> str:
        """Why seat counts wait this time ("" when they don't): not long after the last login, and for
        a few hours after a failed count."""
        opts = self.options.get("seat_counts") or {}
        tried = self.notes.get("seats_tried")
        if not tried:
            return ""
        since = now() - datetime.fromisoformat(tried)
        if self.notes.get("seats_error") and since < timedelta(hours=float(opts.get("retry_hours", 3))):
            return f"last attempt failed {int(since.total_seconds() // 60)} min ago, waiting before logging in again"
        if since < timedelta(minutes=float(opts.get("min_gap_minutes", 60))):
            return f"logged in {int(since.total_seconds() // 60)} min ago, waiting before logging in again"
        return ""

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
        daily = datetime.combine(t.date(), parse_time(opts.get("daily_at", "09:00")), t.tzinfo)
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

    MAX_HOPS = 3

    def _ticket_page(self, url: str):
        """A ticket page's response, or None when it sends us to the login page. Redirects are followed
        one at a time, at most MAX_HOPS: a page that keeps redirecting raises instead of looping."""
        for _ in range(self.MAX_HOPS + 1):
            resp = self.http.get(url, follow_redirects=False)
            if not resp.is_redirect:
                return None if "prijavi-se" in str(resp.url) else resp
            url = urljoin(str(resp.url), resp.headers["location"])
            if "prijavi-se" in url:
                return None
        raise RedirectLoop(url)

    def _seat_counts(self, perfs: list[Performance]) -> None:
        self._login()
        relogged, counted_any, skipped, in_a_row = False, False, [], 0
        page_seconds = float((self.options.get("seat_counts") or {}).get("page_seconds", 3))
        for i, p in enumerate(perfs):
            if i:
                self.sleep(page_seconds)
            try:
                resp = self._ticket_page(p.buy_url)
                if resp is None:  # session dropped: log in again, once per count
                    if relogged:
                        raise RuntimeError("still sent to the login page after logging in")
                    self._login()
                    relogged = True
                    resp = self._ticket_page(p.buy_url)
                    if resp is None:
                        raise RuntimeError("still sent to the login page after logging in")
            except RedirectLoop:  # this date's page: skip it, but stop if they all do (blocked?)
                skipped.append(p.uid)
                in_a_row += 1
                if in_a_row >= 3:
                    raise RuntimeError(f"ticket pages keep redirecting ({', '.join(skipped)}); stopped counting") from None
                continue
            in_a_row = 0
            if counted := count_seats(resp.text):
                apply_seats(p, *counted, now().isoformat(timespec="seconds"))
                counted_any = True
        if skipped:
            log.info("narodno: ticket pages that keep redirecting, not counted: %s", ", ".join(skipped))
        if perfs and not counted_any:  # otherwise every check would log in again and quietly count nothing
            raise RuntimeError("no seat map found on any ticket page (page layout changed?)")
