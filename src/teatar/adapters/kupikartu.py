"""'Kupi kartu' online box office (1 Click Soft), used by several Belgrade theatres:
bilet.pozoristeterazije.com, blagajna.jdp.rs, bilet.bdp.rs, …

The box office loads its repertoire as JSON from `qrydata.php` (q=1, one month per call).
Field meanings, from the box office's own JavaScript:
  status      '1' on sale · '0' not on sale yet · '3' finished · '' placeholder (month is empty)
  status2     '3' cancelled
  rasprodato  1 sold out · 10 fewer than 10% of seats left · 0 available
  prodaja     1 = tickets sold by another vendor
Add a theatre with `adapter: kupikartu_pos` and its box-office `base_url` in venues.yaml.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..models import TZ, Performance, Status, now
from ..registry import register
from .base import Adapter, upcoming


def parse_month(body: str, venue: str, base_url: str, site_url: str = "") -> list[Performance]:
    data = json.loads(body)
    perfs = []
    for v in data.get("resp") or []:
        if not v.get("status") or not v.get("prostorterminid") or not v.get("datum_vreme_od"):
            continue  # placeholder row for a month with nothing published
        start = datetime.fromisoformat(v["datum_vreme_od"]).replace(tzinfo=TZ)
        if v.get("status2") == "3":
            status = Status.CANCELLED
        elif v["status"] == "1" and v.get("prodaja") != "1":
            status = {1: Status.SOLD_OUT, 10: Status.LOW}.get(int(v.get("rasprodato") or 0), Status.ON_SALE)
        elif v["status"] == "0":
            status = Status.NOT_ON_SALE
        else:
            status = Status.UNKNOWN  # finished, or sold by another vendor
        ptid = v["prostorterminid"]
        perfs.append(
            Performance(
                venue=venue,
                source_id=str(ptid),
                title=(v.get("predstava_cyr") or v.get("predstava") or "?").strip(),
                subtitle=(v.get("reditelj_cyr") or v.get("reditelj") or "").strip(),
                start=start,
                stage=(v.get("scena_cyr") or v.get("scena") or "").replace("`", "").strip(),
                url=v.get("url") or site_url,
                buy_url=f"{base_url}/scena.php?prostorterminid={ptid}",
                status=status,
                extra={"prostorterminid": ptid, "box_office_only": v.get("online_prodaja") not in ("1", 1, None)},
            )
        )
    return perfs


@register("kupikartu_pos")
class KupiKartuAdapter(Adapter):
    method = "api"

    def __init__(self, key, http, base_url: str, site_url: str = "", **options):
        super().__init__(key, http, **options)
        self.base_url = base_url.rstrip("/")
        self.site_url = site_url

    def fetch(self) -> list[Performance]:
        self.http.get(f"{self.base_url}/repertoar.php")  # session cookie
        today = now().date()
        perfs: dict[str, Performance] = {}
        for i in range(self.options.get("months_ahead", 2) + 1):
            y, m = divmod(today.month - 1 + i, 12)
            body = self.http.post(
                f"{self.base_url}/qrydata.php",
                data={"q": 1, "godina": today.year + y, "mesec": m + 1, "dan": 1, "sdsp": "", "trazi": "",
                      "user_tipid": "", "sap": "", "ss": "", "tip_prikaza": 0, "program": "0"},
            ).text
            for p in parse_month(body, self.key, self.base_url, self.site_url):
                perfs[p.uid] = p
        return upcoming(list(perfs.values()))
