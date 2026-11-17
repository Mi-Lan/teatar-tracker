import json
from datetime import date, datetime
from pathlib import Path

import httpx

from teatar import models
from teatar.adapters import bdp, jdp, kupikartu, madlenianum, narodno, tickets_rs
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
    assert counts[Status.ON_SALE] == 85 and counts[Status.SOLD_OUT] == 1 and counts[Status.NOT_ON_SALE] == 3
    assert {p.start.year for p in perfs} == {2026, 2027}
    nutcracker = by_id["6378"]  # cancelled: no title link, "ОТКАЗАНО" on the image
    assert nutcracker.title == "Крцко Орашчић" and nutcracker.status == Status.CANCELLED


def test_narodno_without_ticket_counts():
    """Since 2026-10-15 the Buy button has no count; "under 10% free" marks a date running low."""
    perfs = narodno.parse(read("narodno_repertoar_2026_11.html"), today=date(2026, 11, 9))
    assert len(perfs) == 69 and all(p.title != "?" for p in perfs)
    counts = {s: sum(p.status == s for p in perfs) for s in Status}
    assert (counts[Status.ON_SALE], counts[Status.LOW], counts[Status.SOLD_OUT], counts[Status.CANCELLED], counts[Status.NOT_ON_SALE]) == (52, 7, 6, 1, 3)
    low = [p for p in perfs if p.status == Status.LOW]
    assert {p.title for p in low} == {"Власт", "Сумњиво лице", "Балкански шпијун", "Крцко Орашчић"}
    assert all(p.buy_url and p.available is None for p in low)


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


def test_kupikartu_box_office():
    base = "https://bilet.pozoristeterazije.com"
    perfs = {p.start.day: p for p in kupikartu.parse_month(read("kupikartu_terazije_2026_09.json"), "terazije", base)}
    assert perfs[25].title == "MAMMA MIA!" and perfs[25].status == Status.SOLD_OUT  # rasprodato = 1
    assert perfs[26].title == "БАЛКАН ЕКСПРЕС" and perfs[26].status == Status.LOW  # rasprodato = 10
    assert perfs[27].status == Status.ON_SALE
    assert perfs[27].start == datetime(2026, 9, 27, 19, 30, tzinfo=TZ)
    assert perfs[27].buy_url == f"{base}/scena.php?prostorterminid=1248"
    assert perfs[16].status == Status.UNKNOWN  # finished
    # an unpublished month is a placeholder row, not a performance
    assert kupikartu.parse_month(read("kupikartu_terazije_2026_10.json"), "terazije", base) == []


class FakeHttp:
    """Answers GET/POST from fixtures and records what was asked for."""

    def __init__(self, get=None, post=None):
        self._get, self._post, self.posts = get, post, []

    def get(self, url, **kw):
        return httpx.Response(200, text=self._get(url, **kw), request=httpx.Request("GET", url))

    def post(self, url, **kw):
        self.posts.append(kw.get("data") or kw.get("json"))
        return httpx.Response(200, text=self._post(url, **kw), request=httpx.Request("POST", url))


def test_month_by_month_sources_ask_for_each_month_once_across_new_year(monkeypatch):
    """JDP and the box offices are read one month per call: this month and `months_ahead` more."""
    models.set_clock(lambda: datetime(2026, 12, 20, 12, 0, tzinfo=TZ))
    monkeypatch.setattr(jdp, "upcoming", lambda perfs: perfs)
    monkeypatch.setattr(kupikartu, "upcoming", lambda perfs: perfs)
    try:
        http = FakeHttp(get=lambda url, **kw: read("jdp_repertoire_feed.html"), post=lambda url, **kw: read("jdp_ajax_2026_09.json"))
        perfs = jdp.JdpAdapter("jdp", http, months_ahead=2).fetch()
        assert [(d["year"], d["month"]) for d in http.posts] == [(2026, 12), (2027, 1), (2027, 2)]
        assert all(d["_ajax_nonce"] == "840e4344fe" for d in http.posts)
        assert len(perfs) == 10  # the same dates in every answer: counted once

        http = FakeHttp(get=lambda url, **kw: "", post=lambda url, **kw: read("kupikartu_terazije_2026_09.json"))
        perfs = kupikartu.KupiKartuAdapter("terazije", http, base_url="https://bilet.pozoristeterazije.com/", months_ahead=1).fetch()
        assert [(d["godina"], d["mesec"]) for d in http.posts] == [(2026, 12), (2027, 1)]
        assert len(perfs) == 6 and perfs[0].buy_url.startswith("https://bilet.pozoristeterazije.com/scena.php?")
    finally:
        models.set_clock(None)


def test_madlenianum_calendar_has_next_months_dates():
    """/repertoar stops at the end of the current month; December was only on the home page's calendar."""
    dec = madlenianum.parse_calendar(read("madlenianum_calendar_2026_12.html"))
    assert [(p.start.strftime("%d.%m. %H:%M"), p.title) for p in madlenianum.merge([], dec)] == [
        ("01.12. 17:00", "KRCKO ORAŠČIĆ"), ("01.12. 19:30", "KRCKO ORAŠČIĆ"),
        ("07.12. 17:00", "KRCKO ORAŠČIĆ"), ("07.12. 19:30", "KRCKO ORAŠČIĆ"),
        ("15.12. 19:30", "JA, EMA"),  # listed twice on the site: one performance
        ("30.12. 20:00", "SVET JE LEP KADA SANJAMO"),
    ]
    assert dec[0].source_id == "krcko-orascic-0-202612011700" and dec[0].url == "https://operatheatremadlenianum.com/krcko-orascic-0"
    assert dec[0].status == Status.UNKNOWN and dec[0].start.tzinfo is TZ


def test_madlenianum_reads_the_listing_and_the_calendar_as_one(monkeypatch):
    models.set_clock(lambda: datetime(2026, 11, 17, 16, 0, tzinfo=TZ))
    asked = []

    def get(url, params=None, **kw):
        asked.append((url.rsplit("/", 1)[1], params))
        if url.endswith("/repertoar"):  # page 2 of the listing is all in the past: the adapter stops there
            return "<html></html>" if params else read("madlenianum_repertoar_2026_11.html")
        return {"2026-11": read("madlenianum_calendar_2026_11.html"), "2026-12": read("madlenianum_calendar_2026_12.html")}.get(params["mini"], "<html></html>")

    try:
        perfs = madlenianum.MadlenianumAdapter("madlenianum", FakeHttp(get=get), months_ahead=2).fetch()
    finally:
        models.set_clock(None)
    assert asked == [("repertoar", None), ("repertoar", {"page": 1}), ("home", {"mini": "2026-11"}), ("home", {"mini": "2026-12"}), ("home", {"mini": "2027-01"})]
    assert len(perfs) == len({p.uid for p in perfs}) == 12  # 6 left in November (on both pages: counted once) + 6 in December
    by_month = {m: [p for p in perfs if p.start.month == m] for m in (11, 12)}
    assert len(by_month[11]) == 6 and len(by_month[12]) == 6
    evita = [p for p in perfs if p.title == "EVITA"]
    assert evita[0].subtitle == "Endrju Lojd Veber"  # the listing's entry wins: only it has the author
    assert [(p.source_id, p.start) for p in evita] == [("evita-202611281930", datetime(2026, 11, 28, 19, 30, tzinfo=TZ))]  # one date on the site
    # the ids the tracker already knows are unchanged, so nothing is announced twice
    assert {p.source_id for p in by_month[11]} >= {"staklena-menazerija-202611242000", "snezana-i-sedam-patuljaka-202611301000"}
