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

from ..models import Performance, Status, now
from ..registry import register
from ..textnorm import month_number, normalize
from .base import Adapter, at, upcoming

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
        day = _parse_day(h3.get_text(" ", strip=True)) if h3 else None
        if not day:
            continue
        for art in block.find_all("article"):
            link = art.select_one("h4 a")
            title = (link or art.find("h4")).get_text(" ", strip=True)
            right = art.select_one("div.items-end")
            right_text = right.get_text(" ", strip=True) if right else ""
            tm = re.search(r"\d{1,2}:\d{2}", right_text)
            start = at(day, tm.group() if tm else None)
            stage_el = art.select_one("span.font-semibold")
            label_el = art.select_one("div.text-gray-500")
            label = label_el.get_text(" ", strip=True) if label_el else ""
            buy = art.select_one('a[href*="blagajna.jdp.rs"]')
            author = art.select_one("div.text-gray-700 span.font-medium")

            lab = normalize(label)
            if "rasprodat" in lab:
                status = Status.SOLD_OUT
            elif "otkazan" in lab:
                status = Status.CANCELLED
            elif "manje od" in lab:
                status = Status.LOW
            elif buy:
                status = Status.ON_SALE
            else:
                status = Status.NOT_ON_SALE

            termin = re.search(r"prostorterminid=(\d+)", buy["href"]) if buy else None
            perfs.append(
                Performance(
                    venue=venue,
                    source_id=f"{start:%Y%m%d%H%M}-{normalize(title).replace(' ', '-')[:40]}",
                    title=title,
                    subtitle=author.get_text(" ", strip=True) if author else "",
                    start=start,
                    stage=stage_el.get_text(" ", strip=True).replace("`", "") if stage_el else "",
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

    def fetch(self) -> list[Performance]:
        page = self.http.get(PAGE).text
        nonce = find_nonce(page)
        if not nonce:  # fall back to the server-rendered current listing
            return upcoming(parse(page, self.key))
        today = now().date()
        perfs: dict[str, Performance] = {}
        for i in range(self.options.get("months_ahead", 2) + 1):
            y, m = divmod(today.month - 1 + i, 12)
            body = self.http.post(
                AJAX,
                data={"action": "jdp_repertoire", "_ajax_nonce": nonce, "year": today.year + y, "month": m + 1, "search_for": ""},
            ).text
            for p in parse_ajax(body, self.key):
                perfs[p.uid] = p
        return upcoming(list(perfs.values()))
