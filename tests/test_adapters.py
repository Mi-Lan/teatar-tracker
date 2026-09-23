import json
from datetime import date, datetime
from pathlib import Path

from teatar.adapters import bdp, jdp, madlenianum, narodno, tickets_rs
from teatar.models import TZ, Status

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 22)  # the day the fixtures were captured


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_narodno_agenda_layout():
    perfs = narodno.parse(read("narodno_repertoar.html"), today=date(2026, 9, 23))
    assert len(perfs) == 90
    by_id = {p.source_id: p for p in perfs}
    bozji = by_id["6414"]
    assert bozji.title == "Божји људи" and bozji.stage == "Сцена „Раша Плаовић”"
    assert bozji.start == datetime(2026, 10, 2, 20, 30, tzinfo=TZ)
    assert bozji.status == Status.ON_SALE and bozji.available == 271
    assert bozji.buy_url.endswith("/ulaznice/odabir-ulaznica?p=6414")
    assert bozji.subtitle == "по мотивима прозе Борисава Станковића"
    assert by_id["6343"].status == Status.SOLD_OUT  # explicit РАСПРОДАТО button
    counts = {s: sum(p.status == s for p in perfs) for s in Status}
    assert counts[Status.ON_SALE] == 85 and counts[Status.SOLD_OUT] == 1 and counts[Status.NOT_ON_SALE] == 4
    assert {p.start.year for p in perfs} == {2026, 2027}


def test_narodno_legacy_layout():
    perfs = narodno.parse(read("narodno_repertoar_legacy.html"), today=TODAY)
    assert len(perfs) == 93
    by_id = {p.source_id: p for p in perfs}
    vaskrsle = by_id["6381"]
    assert vaskrsle.title == "Васкрсле сени"
    assert vaskrsle.start == datetime(2026, 10, 3, 19, 0, tzinfo=TZ)
    assert vaskrsle.stage == "Велика сцена"
    assert vaskrsle.status == Status.ON_SALE and vaskrsle.available == 67
    assert vaskrsle.buy_url.endswith("/ulaznice/odabir-ulaznica?p=6381")
    assert "Премијера" in vaskrsle.subtitle
    assert by_id["6414"].status == Status.NOT_ON_SALE and by_id["6414"].available is None
    assert sum(p.status == Status.ON_SALE for p in perfs) == 52
    # December/January entries roll into the right year
    assert all(p.start.year in (2026, 2027) for p in perfs)


def test_bdp_catalog_stage_filter_and_sixtix():
    perfs = bdp.parse_catalog(read("bdp_repertoar.html"))
    upcoming = [p for p in perfs if p.start.date() >= TODAY]
    velika = bdp.stage_filter(upcoming, ["Велика сцена"], include_guest=False)
    assert velika and all("velika" in p.stage.lower() for p in velika)
    assert not any(p.extra["guest"] for p in velika)

    bdp.apply_sixtix(velika, json.loads(read("sixtix_iframe_bdp.json")))
    divlje = next(p for p in velika if p.title == "Дивље месо" and p.start.day == 26)
    assert divlje.status == Status.ON_SALE
    assert divlje.extra["sixtix"] == "e6871527f088"
    assert divlje.buy_url == "https://app.sixtix.com/events/e6871527f088"
    kus = next(p for p in velika if p.title == "Кус петлић" and p.start.month == 12 and p.start.day == 1)
    assert kus.status == Status.NOT_ON_SALE and kus.buy_url == ""


def test_jdp_page_and_ajax_agree():
    page = jdp.parse(read("jdp_repertoire_feed.html"))
    ajax = jdp.parse_ajax(read("jdp_ajax_2026_09.json"))
    assert len(page) == len(ajax) == 10
    assert {p.source_id for p in page} == {p.source_id for p in ajax}  # Cyrillic vs Latin → same ids
    statuses = {}
    for p in ajax:  # first performance of each title
        statuses.setdefault(p.title, p.status)
    assert statuses["Moj muž"] == Status.SOLD_OUT
    assert statuses["Ričard Drugi"] == Status.LOW
    ricard = next(p for p in ajax if p.title == "Ričard Drugi")
    assert ricard.start == datetime(2026, 9, 22, 20, 0, tzinfo=TZ)
    assert ricard.extra["prostorterminid"] == "1178"


def test_jdp_nonce():
    assert jdp.find_nonce(read("jdp_repertoire_feed.html")) == "840e4344fe"


def test_tickets_rs_events():
    data = tickets_rs.unwrap(json.loads(read("tickets_rs_kolarac.json")))["data"]
    perfs = tickets_rs.parse_events(data["Events"], "kolarac")
    assert [p.title for p in perfs] == ["MARIJA GULEGINA", "EKSTRAVAGANCIJE I LUDOSTI", "NIKOLAJ KUZNJECOV"]
    assert perfs[0].start == datetime(2026, 10, 6, 20, 0, tzinfo=TZ)
    assert perfs[0].status == Status.ON_SALE
    assert perfs[0].buy_url == "https://tickets.rs/event/marija_gulegina_25864"


def test_madlenianum_expands_dates():
    perfs = madlenianum.parse(read("madlenianum_repertoar.html"))
    ema = [p for p in perfs if p.title == "JA, EMA"]
    assert [p.start for p in ema] == [
        datetime(2026, 9, 29, 19, 30, tzinfo=TZ),
        datetime(2026, 10, 2, 19, 30, tzinfo=TZ),
        datetime(2026, 10, 10, 19, 30, tzinfo=TZ),
    ]
    assert ema[0].subtitle == "Tatjana Mandić Rigonat"
