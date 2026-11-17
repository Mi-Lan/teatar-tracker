"""Opera i teatar Madlenianum (own site, Drupal). Catalog only: its ticket shop requires a login.

/repertoar lists productions newest-first, each with all its dates as ISO datetimes.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import TZ, Performance, Status, now
from ..registry import register
from .base import Adapter, text_of, upcoming

BASE = "https://operatheatremadlenianum.com"
SHOP = "https://tickets.operatheatremadlenianum.com/sr"


def parse(html: str, venue: str = "madlenianum") -> list[Performance]:
    soup = BeautifulSoup(html, "lxml")
    perfs = []
    for row in soup.select(".view-predstave .views-row"):
        link = row.select_one(".views-field-title a")
        if not link:
            continue
        slug = link["href"].strip("/")
        over = row.select_one(".views-field-field-nadnaslov")
        for span in row.select(".views-field-field-datum .date-display-single[content]"):
            start = datetime.fromisoformat(span["content"]).astimezone(TZ)
            perfs.append(
                Performance(
                    venue=venue,
                    source_id=f"{slug}-{start:%Y%m%d%H%M}",
                    title=text_of(link),
                    subtitle=text_of(over),
                    start=start,
                    url=urljoin(BASE, link["href"]),
                    buy_url=SHOP,
                    status=Status.UNKNOWN,
                )
            )
    return perfs


@register("madlenianum_html")
class MadlenianumAdapter(Adapter):
    method = "html"
    known_options = frozenset({"max_pages"})

    def fetch(self) -> list[Performance]:
        perfs = []
        for page in range(self.options.get("max_pages", 3)):
            batch = parse(self.http.get(f"{BASE}/repertoar", params={"page": page} if page else None).text, self.key)
            perfs += batch
            # Newest-first listing: once a page has nothing in the future, older pages won't either.
            if not any(p.start >= now() for p in batch):
                break
        return upcoming(perfs)
