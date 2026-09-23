"""Beogradsko dramsko pozorište.

Catalog: the Webflow CMS list hidden inside /cir/repertoar (title, date, time, stage, flags).
Sales:   the Sixtix "Main website" iframe API lists every performance currently on sale, with its
         ticket slug. A performance showing up there means sales opened.
"""

from __future__ import annotations

from datetime import date, datetime

from bs4 import BeautifulSoup

from ..models import TZ, Performance, Status
from ..registry import register
from ..textnorm import normalize
from .base import Adapter, at, upcoming

BASE = "https://www.bdp.rs"
SIXTIX_API = "https://api.sixtix.com/v1"
SIXTIX_EVENT_URL = "https://app.sixtix.com/events/{slug}"


def _text(item, cls: str) -> str:
    el = item.select_one(f".{cls}")
    return el.get_text(" ", strip=True) if el else ""


def parse_catalog(html: str, venue: str = "bdp") -> list[Performance]:
    soup = BeautifulSoup(html, "lxml")
    perfs, seen = [], set()
    for item in soup.select(".ec-col-item"):
        title, day = _text(item, "title"), _text(item, "start-date")
        if not title or not day:
            continue
        start = at(date.fromisoformat(day), _text(item, "rep-time") or None)
        key = (start, normalize(title))
        if key in seen:
            continue
        seen.add(key)
        link = item.select_one("a.webflow-link")
        flags = {f: _text(item, f"rep-{f}") == "true" for f in ("ras", "cancel", "gost", "prem")}
        status = Status.NOT_ON_SALE
        if flags["ras"]:
            status = Status.SOLD_OUT
        if flags["cancel"]:
            status = Status.CANCELLED
        perfs.append(
            Performance(
                venue=venue,
                source_id=f"{start:%Y%m%d%H%M}-{normalize(title).replace(' ', '-')[:40]}",
                title=title,
                subtitle="Премијера" if flags["prem"] else "",
                start=start,
                stage=_text(item, "rep-scene"),
                url=BASE + link["href"] if link and link.get("href", "").startswith("/") else "",
                buy_url="",
                status=status,
                extra={"guest": flags["gost"], "guest_place": _text(item, "rep-gost-place"), "site_ticket_link": _text(item, "rep-ticket")},
            )
        )
    return perfs


def apply_sixtix(perfs: list[Performance], iframe: dict) -> None:
    """Mark performances found in the Sixtix on-sale list and attach their ticket slug."""
    by_start: dict[datetime, list[dict]] = {}
    for ev in iframe.get("events", []):
        start = datetime.fromisoformat(ev["startDate"]).replace(tzinfo=TZ)
        by_start.setdefault(start, []).append(ev)

    for p in perfs:
        candidates = by_start.get(p.start, [])
        if len(candidates) > 1:
            words = set(p.title_norm.split())
            candidates = sorted(candidates, key=lambda ev: -len(words & set(normalize(ev["name"]).split())))
            if not words & set(normalize(candidates[0]["name"]).split()):
                candidates = []
        if not candidates:
            continue
        ev = candidates[0]
        p.extra["sixtix"] = ev["slug"]
        p.buy_url = SIXTIX_EVENT_URL.format(slug=ev["slug"])
        if p.status == Status.NOT_ON_SALE:
            p.status = Status.ON_SALE


def stage_filter(perfs: list[Performance], stages: list[str] | None, include_guest: bool) -> list[Performance]:
    wanted = [normalize(s) for s in stages or []]
    out = []
    for p in perfs:
        if p.extra.get("guest") and not include_guest:
            continue
        if wanted and not any(w in normalize(p.stage) for w in wanted):
            continue
        out.append(p)
    return out


@register("bdp_webflow_sixtix")
class BdpAdapter(Adapter):
    method = "hybrid"

    def fetch(self) -> list[Performance]:
        html = self.http.get(f"{BASE}/cir/repertoar").text
        perfs = stage_filter(
            upcoming(parse_catalog(html, self.key)),
            self.options.get("stages"),
            self.options.get("include_guest", False),
        )
        iframe_slug = self.options.get("sixtix_iframe")
        if iframe_slug:
            apply_sixtix(perfs, self.http.get(f"{SIXTIX_API}/iFrame/{iframe_slug}").json())
        return perfs
