"""Opera i teatar Madlenianum (own site, Drupal). Catalog only: its ticket shop requires a login.

Two pages are read, because neither is complete on its own:
  /repertoar           productions newest-first, each with all its dates as ISO datetimes, and the
                       author line. It only covers the current month (and the past).
  /home?mini=YYYY-MM   the month calendar from the home page: every performance of that month, title
                       and time only. This is where next month's dates appear first.
Both link a production by the same path, so a date found on both is one performance.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..dates import months_ahead
from ..models import TZ, Performance, Status, now
from ..registry import register
from .base import Adapter, text_of, unique, upcoming

BASE = "https://operatheatremadlenianum.com"
SHOP = "https://tickets.operatheatremadlenianum.com/sr"


def _performance(venue: str, link, content: str, subtitle: str = "") -> Performance:
    """One date of the production `link` points to; `content` is the date's ISO datetime."""
    start = datetime.fromisoformat(content).astimezone(TZ)
    return Performance(
        venue=venue,
        source_id=f"{link['href'].strip('/')}-{start:%Y%m%d%H%M}",
        title=text_of(link),
        subtitle=subtitle,
        start=start,
        url=urljoin(BASE, link["href"]),
        buy_url=SHOP,
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
    """Both sources as one list, each performance once. The /repertoar entry wins (it has the author
    line); a calendar entry for the same play and time is the same performance even if its link differs."""
    known = {(p.start, p.title_norm) for p in listed}
    return unique([p for p in calendar if (p.start, p.title_norm) not in known] + listed)


@register("madlenianum_html")
class MadlenianumAdapter(Adapter):
    method = "html"
    known_options = frozenset({"max_pages", "months_ahead"})

    def fetch(self) -> list[Performance]:
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
        return upcoming(merge(listed, calendar))
