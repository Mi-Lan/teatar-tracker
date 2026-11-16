"""Jugoslovensko dramsko pozorište.

The repertoire page loads months through a WordPress AJAX action (`jdp_repertoire`) that needs a
nonce embedded (base64) in the page. Only the current month is usually published, so a new
month appearing is the ticket-release signal.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date

from bs4 import BeautifulSoup

from ..dates import months_ahead
from ..models import Performance, Status
from ..registry import register
from ..textnorm import month_number
from .base import (
    Adapter,
    at,
    dated_id,
    find_time,
    label_status,
    text_of,
    unique,
    upcoming,
)

PAGE = "https://www.jdp.rs/rs/repertoire-feed/"
AJAX = "https://www.jdp.rs/wp-admin/admin-ajax.php"


def find_nonce(page_html: str) -> str | None:
    for blob in re.findall(r"base64,([A-Za-z0-9+/=]+)", page_html):
        script = base64.b64decode(blob).decode("utf-8", "replace")
        m = re.search(r"action.{0,40}jdp_repertoire", script) and re.search(r"const nonce='([0-9a-f]+)'", script)
        if m:
            return m.group(1)
    return None


def _parse_day(text: str) -> date | None:
    m = re.search(r"(\d{1,2})\.\s*([^\s\d.]+)\s+(\d{4})", text)
    if not m or not month_number(m.group(2)):
        return None
    return date(int(m.group(3)), month_number(m.group(2)), int(m.group(1)))


def parse(html: str, venue: str = "jdp") -> list[Performance]:
    soup = BeautifulSoup(html, "lxml")
    perfs = []
    for block in soup.select("#repertoire-api-list > div"):
        h3 = block.find("h3")
        day = _parse_day(text_of(h3))
        if not day:
            continue
        for art in block.find_all("article"):
            link = art.select_one("h4 a")
            title = text_of(link or art.find("h4"))
            if not title:
                continue
            start = at(day, find_time(text_of(art.select_one("div.items-end"))))
            label = text_of(art.select_one("div.text-gray-500"))
            buy = art.select_one('a[href*="blagajna.jdp.rs"]')
            status = label_status(label) or (Status.ON_SALE if buy else Status.NOT_ON_SALE)

            termin = re.search(r"prostorterminid=(\d+)", buy["href"]) if buy else None
            perfs.append(
                Performance(
                    venue=venue,
                    source_id=dated_id(start, title),
                    title=title,
                    subtitle=text_of(art.select_one("div.text-gray-700 span.font-medium")),
                    start=start,
                    stage=text_of(art.select_one("span.font-semibold")).replace("`", ""),
                    url=link["href"] if link else "",
                    buy_url=buy["href"] if buy else "",
                    status=status,
                    extra={"label": label, **({"prostorterminid": termin.group(1)} if termin else {})},
                )
            )
    return perfs


def parse_ajax(body: str, venue: str = "jdp") -> list[Performance]:
    data = json.loads(body, strict=False)  # the HTML payload contains raw control characters
    if not data.get("success"):
        raise ValueError(f"jdp_repertoire failed: {data.get('data')}")
    return parse(data["data"].get("html", ""), venue)


@register("jdp_wp_ajax")
class JdpAdapter(Adapter):
    method = "api"
    known_options = frozenset({"months_ahead"})

    def fetch(self) -> list[Performance]:
        page = self.http.get(PAGE).text
        nonce = find_nonce(page)
        if not nonce:  # fall back to the server-rendered current listing
            return upcoming(parse(page, self.key))
        perfs: list[Performance] = []
        for year, month in months_ahead(self.options.get("months_ahead", 2)):
            body = self.http.post(
                AJAX,
                data={"action": "jdp_repertoire", "_ajax_nonce": nonce, "year": year, "month": month, "search_for": ""},
            ).text
            perfs += parse_ajax(body, self.key)
        return upcoming(unique(perfs))
