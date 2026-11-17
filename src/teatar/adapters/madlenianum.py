"""Opera i teatar Madlenianum (own site, Drupal, plus its ticket shop).

Three pages are read, because none is complete on its own:
  /repertoar           productions newest-first, each with all its dates as ISO datetimes, and the
                       author line. It only covers the current month (and the past).
  /home?mini=YYYY-MM   the month calendar from the home page: every performance of that month, title
                       and time only. This is where next month's dates appear on the public site.
  the ticket shop      tickets.operatheatremadlenianum.com/sr, which lists nothing without an account.
                       Signed in, it shows every date on sale with its stage and "KUPI (33)": the
                       tickets left. Dates can be here days before the public site has them.
All three link a production by the same path, so a date found on several is one performance.

The shop is read with MADLENIANUM_EMAIL / MADLENIANUM_PASSWORD (GitHub secrets), on every full check:
one sign-in and the listing page, nothing else. No ticket page is opened, so nothing is ever selected,
reserved or held. Without the secrets the public pages are tracked as before (dates only). If the
sign-in or the page stops working, the dates and counts from the last good check are kept and the
runner warns about "Madlenianum ticket shop" (like a failing site); the public pages carry on.
The 30-second polls around a known release never sign in.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..dates import months_ahead
from ..models import TZ, Performance, Status, now
from ..registry import register
from ..textnorm import month_number, normalize, slug
from .base import Adapter, at, find_time, infer_year, text_of, unique, upcoming

BASE = "https://operatheatremadlenianum.com"
SHOP = "https://tickets.operatheatremadlenianum.com"
SHOP_PAGE = f"{SHOP}/sr"
SHOP_LOGIN = f"{SHOP}/include/action.php"

log = logging.getLogger(__name__)


# --- the public site ---------------------------------------------------------------------------------


def _source_id(path: str, start: datetime) -> str:
    """Production path + start time: the id a date is tracked under, whichever page it was found on."""
    return f"{path}-{start:%Y%m%d%H%M}"


def _performance(venue: str, link, content: str, subtitle: str = "") -> Performance:
    """One date of the production `link` points to; `content` is the date's ISO datetime."""
    start = datetime.fromisoformat(content).astimezone(TZ)
    return Performance(
        venue=venue,
        source_id=_source_id(link["href"].strip("/"), start),
        title=text_of(link),
        subtitle=subtitle,
        start=start,
        url=urljoin(BASE, link["href"]),
        buy_url=SHOP_PAGE,
        status=Status.UNKNOWN,
    )


def parse(html: str, venue: str = "madlenianum") -> list[Performance]:
    """The /repertoar listing."""
    soup = BeautifulSoup(html, "lxml")
    perfs = []
    for row in soup.select(".view-predstave .views-row"):
        link = row.select_one(".views-field-title a")
        if not link:
            continue
        subtitle = text_of(row.select_one(".views-field-field-nadnaslov"))
        for span in row.select(".views-field-field-datum .date-display-single[content]"):
            perfs.append(_performance(venue, link, span["content"], subtitle))
    return perfs


def parse_calendar(html: str, venue: str = "madlenianum") -> list[Performance]:
    """One month of the home page's calendar (/home?mini=YYYY-MM)."""
    soup = BeautifulSoup(html, "lxml")
    perfs = []
    for item in soup.select("td[id^=kalendar_predstava-] li"):
        link = item.select_one(".calendar_tooltips-title-value a[href]")
        span = item.select_one(".date-display-single[content]")
        if link and span and text_of(link):
            perfs.append(_performance(venue, link, span["content"]))
    return perfs


def merge(listed: list[Performance], calendar: list[Performance]) -> list[Performance]:
    """Both public sources as one list, each performance once. The /repertoar entry wins (it has the
    author line); a calendar entry for the same play and time is the same performance even if its link differs."""
    known = {(p.start, p.title_norm) for p in listed}
    return unique([p for p in calendar if (p.start, p.title_norm) not in known] + listed)


# --- the ticket shop ---------------------------------------------------------------------------------


@dataclass
class ShopEntry:
    """One card on the shop's listing."""

    path: str  # the production's path on the public site ("evita"): what ties the card to the public pages
    start: datetime
    title: str  # "EVITA" (the card says "EVITA - I REPRIZA")
    label: str  # "I REPRIZA", "PREMIJERA", or ""
    genre: str  # "MJUZIKL"
    stage: str  # "Velika scena"
    status: Status
    available: int | None  # tickets left, from "KUPI (162)"
    buy_url: str
    shop_id: str = ""


_LABEL = re.compile(r"(?i)premijer|repriz|obnov|gostovanj")


def _split_title(heading: str, genre: str) -> tuple[str, str]:
    """'EVITA - I REPRIZA' -> ('EVITA', 'I REPRIZA'); 'KRCKO ORAŠČIĆ- BALET' -> ('KRCKO ORAŠČIĆ', '') when
    the tail only repeats the genre. A dash inside a real title is left alone."""
    head, dash, tail = heading.rpartition("-")
    head, tail = head.strip(), tail.strip()
    if dash and head and tail:
        if _LABEL.search(tail):
            return head, tail
        if normalize(tail) == normalize(genre):
            return head, ""
    return heading.strip(), ""


def parse_shop(html: str, today: date | None = None) -> list[ShopEntry]:
    """The shop's listing as shown to a signed-in visitor. An empty list when the page has no cards
    (which is also what the sign-in page looks like)."""
    soup = BeautifulSoup(html, "lxml")
    entries = []
    for card in soup.select("div.events_entry_new"):
        day = re.search(r"\d{1,2}", text_of(card.select_one(".blackdate-day")))
        month = month_number(text_of(card.select_one(".blackdate-month")))
        heading = card.select_one(".events_entry_title")
        if not (day and month and heading):
            continue
        info = text_of(heading.find_parent("div").find("p")) if heading.find_parent("div") else ""  # "19:30 Velika scena"
        try:
            start = at(infer_year(month, int(day.group()), today), find_time(info))
        except ValueError:  # a date that doesn't exist: skip the card, keep the rest
            continue
        genre = text_of(heading.find("span"))
        title, label = _split_title(" ".join("".join(heading.find_all(string=True, recursive=False)).split()), genre)
        link = card.select_one("a.repA[href]")
        buy = card.select_one('a[href*="odabir-ulaznica"]')
        reserve = card.select_one('a[href*="rezervacija-ulaznica"]')
        count = re.search(r"\((\d+)\)", text_of(buy))
        available = int(count.group(1)) if count else None
        if buy:
            status = Status.SOLD_OUT if available == 0 else Status.ON_SALE
        elif reserve:  # nothing to buy online, but seats can still be reserved
            status = Status.ON_SALE
        else:
            closed = normalize(" ".join(b.get("title", "") for b in card.select(".entry_buttons button")))
            status = Status.SOLD_OUT if "nema slobodnih" in closed else Status.NOT_ON_SALE
        target = buy or reserve
        shop_id = re.search(r"[?&]id=(\d+)", target["href"]) if target else None
        entries.append(
            ShopEntry(
                path=(urlsplit(link["href"]).path.strip("/") if link else "") or slug(title),
                start=start,
                title=title,
                label=label,
                genre=genre,
                stage=re.sub(r"^\s*\d{1,2}:\d{2}\s*", "", info).strip(),
                status=status,
                available=available,
                buy_url=urljoin(SHOP_PAGE, target["href"]) if target else SHOP_PAGE,
                shop_id=shop_id.group(1) if shop_id else "",
            )
        )
    return entries


def _path(p: Performance) -> str:
    return urlsplit(p.url).path.strip("/")


def apply_shop(public: list[Performance], entries: list[ShopEntry], venue: str, read_at: str) -> list[Performance]:
    """The public performances with what the shop says about them, plus the dates only the shop has.
    A date the shop doesn't list isn't on sale there yet."""
    by_uid = {p.uid: p for p in public}
    titles = {_path(p): p.title for p in public}  # the public site's spelling of each production
    for p in public:
        p.status = Status.NOT_ON_SALE
    out = list(public)
    for e in entries:
        p = by_uid.get(f"{venue}:{_source_id(e.path, e.start)}")
        if p is None:  # the same play at the same time under another link is still the same performance
            words = set(normalize(e.title).split())
            p = next((q for q in public if q.start == e.start and "shop_id" not in q.extra and words & set(q.title_norm.split())), None)
        if p is None:
            p = Performance(
                venue=venue, source_id=_source_id(e.path, e.start), title=titles.get(e.path) or e.title, start=e.start,
                url=f"{BASE}/{e.path}", extra={"shop_only": True},
            )
            by_uid[p.uid] = p
            out.append(p)
        p.status, p.available, p.stage, p.buy_url = e.status, e.available, e.stage, e.buy_url
        p.extra |= {"shop_id": e.shop_id, "genre": e.genre} | ({"label": e.label} if e.label else {})
        if e.available is not None:
            p.extra["counted_at"] = read_at
    return out


def carry_over(public: list[Performance], previous: dict[str, Performance]) -> list[Performance]:
    """When the shop isn't read this time: keep what it said last time (status, tickets left, stage, link),
    and the dates only it had."""
    for p in public:
        o = previous.get(p.uid)
        if o is not None and o.status != Status.UNKNOWN:
            p.status, p.available, p.stage, p.buy_url = o.status, o.available, o.stage or p.stage, o.buy_url or p.buy_url
            p.extra |= {k: v for k, v in o.extra.items() if k != "shop_only"}
    seen = {p.uid for p in public}
    return public + [o for o in previous.values() if o.extra.get("shop_only") and o.uid not in seen]


@register("madlenianum_html")
class MadlenianumAdapter(Adapter):
    method = "html"
    known_options = frozenset({"max_pages", "months_ahead", "shop"})
    uses_previous = True
    warning_label = "ticket shop"  # `warning` is set when the shop couldn't be read; the public pages are still good

    def fetch(self) -> list[Performance]:
        public = self._public()
        self.warning = ""
        if not self._shop_wanted():
            return upcoming(public)
        try:
            perfs = apply_shop(public, self._shop_entries(public), self.key, now().isoformat(timespec="seconds"))
        except Exception as exc:  # the public pages (and what the shop said last time) are still good
            log.warning("madlenianum ticket shop failed: %s", exc)
            self.warning = f"ticket shop: {type(exc).__name__}: {exc}"
            perfs = carry_over(public, self.previous)
        return upcoming(perfs)

    def poll(self) -> list[Performance]:
        """The public pages with the shop's last answers carried over (no sign-in): the 30-second polls
        around a release."""
        return upcoming(carry_over(self._public(), self.previous))

    def _public(self) -> list[Performance]:
        listed: list[Performance] = []
        for page in range(self.options.get("max_pages", 3)):
            batch = parse(self.http.get(f"{BASE}/repertoar", params={"page": page} if page else None).text, self.key)
            listed += batch
            # Newest-first listing: once a page has nothing in the future, older pages won't either.
            if not any(p.start >= now() for p in batch):
                break
        calendar: list[Performance] = []
        for year, month in months_ahead(self.options.get("months_ahead", 2)):
            calendar += parse_calendar(self.http.get(f"{BASE}/home", params={"mini": f"{year}-{month:02d}"}).text, self.key)
        return merge(listed, calendar)

    def _shop_wanted(self) -> bool:
        return bool(self.options.get("shop", True) and os.environ.get("MADLENIANUM_EMAIL") and os.environ.get("MADLENIANUM_PASSWORD"))

    def _shop_entries(self, public: list[Performance]) -> list[ShopEntry]:
        self.http.get(SHOP_PAGE)  # the session cookie the sign-in is tied to
        reply = self.http.post(
            SHOP_LOGIN,
            data={"fn": "acc_login", "language": "sr", "email": os.environ["MADLENIANUM_EMAIL"].strip().lower(), "pass": os.environ["MADLENIANUM_PASSWORD"]},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": SHOP_PAGE},
        ).json()
        if reply.get("status") != "ok":
            raise RuntimeError(f"sign-in failed: {reply.get('error') or reply.get('status') or 'no reason given'}")
        html = self.http.get(SHOP_PAGE).text
        if 'id="loginForm"' in html:
            raise RuntimeError("still shown the sign-in form after signing in")
        entries = parse_shop(html)
        if not entries and any(p.start >= now() for p in public):  # the theatre has dates ahead: an empty shop is a changed page
            raise RuntimeError("no performances found on the shop page (page layout changed?)")
        return entries
