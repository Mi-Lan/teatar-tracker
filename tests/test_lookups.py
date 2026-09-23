import json
from datetime import datetime
from pathlib import Path

import httpx
import respx

from teatar import lookups
from teatar.http import Http
from teatar.models import TZ, Status

FIX = Path(__file__).parent / "fixtures"


@respx.mock
def test_sixtix_lookup(clock):
    respx.get("https://api.sixtix.com/v1/EventPublicInfo/BySlug/e6871527f088").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "sixtix_event.json").read_text()))
    )
    entry = {"query": "Divlje meso", "lookup": {"sixtix": "e6871527f088"}}
    [p] = lookups.run_all(Http(min_interval=0), [entry])
    assert p.venue == "custom" and p.title == "Divlje meso"
    assert p.start == datetime(2026, 9, 26, 19, 30, tzinfo=TZ)
    assert p.status == Status.ON_SALE
    assert p.buy_url == "https://app.sixtix.com/events/e6871527f088"


@respx.mock
def test_page_lookup(clock):
    respx.get("https://example.rs/show").mock(return_value=httpx.Response(200, text="<a>Kupi kartu</a>"))
    entry = {"query": "Nešto", "date": "2026-10-10 20:00", "lookup": {"page": "https://example.rs/show", "pattern": "kupi kartu"}}
    [p] = lookups.run_all(Http(min_interval=0), [entry])
    assert p.status == Status.ON_SALE and p.start == datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
