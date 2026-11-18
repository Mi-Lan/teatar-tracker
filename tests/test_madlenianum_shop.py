"""Madlenianum's ticket shop: dates and tickets left that only a signed-in visitor sees (no network: a fake site)."""

from datetime import date, datetime
from pathlib import Path

import pytest

from teatar import models
from teatar.adapters import madlenianum
from teatar.models import TZ, Status
from teatar.runner import run
from teatar.state import State

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 17, 18, 0, tzinfo=TZ)  # when the fixtures were captured
SIGN_IN = '<html><body><form id="loginForm"><input name="email"><input name="pass" type="password"></form></body></html>'


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class Resp:
    def __init__(self, text="", data=None):
        self.text, self._data = text, data

    def json(self):
        return self._data


class FakeSite:
    """operatheatremadlenianum.com (public) and its ticket shop (needs the sign-in)."""

    def __init__(self, password="secret"):
        self.password, self.signed_in, self.requests, self.posts = password, False, [], []
        self.shop = read("madlenianum_shop_2026_11.html")
        self.calendar = {"2026-11": read("madlenianum_calendar_2026_11.html"), "2026-12": read("madlenianum_calendar_2026_12.html")}

    def get(self, url, params=None, **kw):
        self.requests.append(url)
        if url == madlenianum.SHOP_PAGE:
            return Resp(self.shop if self.signed_in else SIGN_IN)
        assert not url.startswith(madlenianum.SHOP), url  # only the listing is ever opened: never a ticket or reservation page
        if url.endswith("/repertoar"):
            return Resp("<html></html>" if params else read("madlenianum_repertoar_2026_11.html"))
        if url.endswith("/home"):
            return Resp(self.calendar.get(params["mini"], "<html></html>"))
        raise AssertionError(url)

    def post(self, url, data=None, **kw):
        assert url == madlenianum.SHOP_LOGIN and data["fn"] == "acc_login"
        self.posts.append(data)
        self.signed_in = data["pass"] == self.password
        return Resp(data={"status": "ok", "error": ""} if self.signed_in else {"status": "error", "error": "Pogrešna lozinka"})

    def shop_requests(self) -> int:
        return sum(u.startswith(madlenianum.SHOP) for u in self.requests) + len(self.posts)


@pytest.fixture
def clock_now():
    models.set_clock(lambda: NOW)
    yield
    models.set_clock(None)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("MADLENIANUM_EMAIL", "Someone@Example.com")
    monkeypatch.setenv("MADLENIANUM_PASSWORD", "secret")


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.delenv("MADLENIANUM_EMAIL", raising=False)
    monkeypatch.delenv("MADLENIANUM_PASSWORD", raising=False)


def adapter(site, **options):
    return madlenianum.MadlenianumAdapter("madlenianum", site, **options)


def test_the_shop_listing():
    entries = madlenianum.parse_shop(read("madlenianum_shop_2026_11.html"), today=date(2026, 11, 17))
    assert len(entries) == 13
    evita = [(e.start, e.title, e.label, e.available, e.buy_url) for e in entries if e.path == "evita"]
    assert evita == [
        (datetime(2026, 11, 28, 19, 30, tzinfo=TZ), "EVITA", "PREMIJERA", 33, "https://tickets.operatheatremadlenianum.com/sr/odabir-ulaznica?id=1169"),
        (datetime(2026, 12, 2, 19, 30, tzinfo=TZ), "EVITA", "I REPRIZA", 162, "https://tickets.operatheatremadlenianum.com/sr/odabir-ulaznica?id=1170"),
    ]
    by_day = {(e.start.day, e.start.month, e.start.hour): e for e in entries}
    assert by_day[(1, 12, 17)].title == "KRCKO ORAŠČIĆ" and by_day[(1, 12, 17)].genre == "BALET"  # "KRCKO ORAŠČIĆ- BALET": the tail is the genre
    assert by_day[(15, 12, 19)].title == "JA, EMA" and by_day[(15, 12, 19)].stage == "Velika scena"
    assert by_day[(23, 11, 20)].path == "kazanova-protiv-don-zuana" and by_day[(23, 11, 20)].stage == "Mala scena"  # (an http:// link)
    sold_out, reserve_only = by_day[(22, 11, 20)], by_day[(30, 12, 20)]
    assert sold_out.status == Status.SOLD_OUT and sold_out.available is None  # "Nema slobodnih ulaznica", reservations closed
    assert reserve_only.status == Status.ON_SALE and reserve_only.available is None and "rezervacija-ulaznica?id=1172" in reserve_only.buy_url
    assert madlenianum.parse_shop(SIGN_IN) == []
    assert madlenianum._split_title("ROMEO - JULIJA", "BALET") == ("ROMEO - JULIJA", "")  # a dash in a real title stays


def test_signed_in_check_adds_the_shops_dates_and_tickets_left(clock_now, creds):
    site = FakeSite()
    perfs = adapter(site).fetch()
    assert site.posts == [{"fn": "acc_login", "language": "sr", "email": "someone@example.com", "pass": "secret"}]
    assert site.shop_requests() == 3  # the page for its cookie, the sign-in, the listing: nothing else
    assert len(perfs) == len({p.uid for p in perfs}) == 13  # 12 on the public site + the one only the shop has

    evita = {p.start.day: p for p in perfs if p.title == "EVITA"}
    assert sorted(evita) == [2, 28]
    repeat = evita[2]  # Wed 2 December: in the shop, not (yet) on the public site
    assert repeat.source_id == "evita-202612021930" and repeat.status == Status.ON_SALE and repeat.available == 162
    assert repeat.extra["shop_only"] and repeat.extra["label"] == "I REPRIZA" and repeat.url == "https://operatheatremadlenianum.com/evita"
    premiere = evita[28]  # known from the public site: same id as before, now with tickets left
    assert premiere.source_id == "evita-202611281930" and premiere.subtitle == "Endrju Lojd Veber"
    assert (premiere.status, premiere.available, premiere.stage) == (Status.ON_SALE, 33, "Velika scena")
    assert premiere.buy_url.endswith("/sr/odabir-ulaznica?id=1169") and "shop_only" not in premiere.extra

    celulit = next(p for p in perfs if p.start.day == 22)
    assert celulit.title == "TVOJE TAJNO ORUŽJE U BORBI PROTIV NEUKROTIVOG CELULITA" and celulit.status == Status.SOLD_OUT  # the public title is kept
    assert all(p.extra.get("counted_at") == NOW.isoformat(timespec="seconds") for p in perfs if p.available is not None)


def test_a_date_the_shop_does_not_list_is_not_on_sale_yet(clock_now, creds):
    site = FakeSite()
    site.shop = site.shop.replace('href="/sr/odabir-ulaznica?id=1168">KUPI (142)', 'href="/x">').replace("rezervacija-ulaznica?id=1168", "x")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(site.shop, "lxml")
    next(c for c in soup.select("div.events_entry_new") if "JA, EMA" in c.get_text()).decompose()
    site.shop = str(soup)
    ema = next(p for p in adapter(site).fetch() if p.title == "JA, EMA")
    assert ema.status == Status.NOT_ON_SALE and ema.available is None  # on the public calendar only


def test_without_an_account_and_in_quick_polls_the_shop_is_left_alone(clock_now, no_creds, monkeypatch):
    site = FakeSite()
    perfs = adapter(site).fetch()
    assert site.shop_requests() == 0 and len(perfs) == 12 and {p.status for p in perfs} == {Status.UNKNOWN}  # as before

    monkeypatch.setenv("MADLENIANUM_EMAIL", "someone@example.com")
    monkeypatch.setenv("MADLENIANUM_PASSWORD", "secret")
    a = adapter(site)
    a.poll()
    assert site.shop_requests() == 0  # the 30-second polls never sign in
    assert len(adapter(site, shop=False).fetch()) == 12 and site.shop_requests() == 0  # switched off in venues.yaml


def test_when_the_sign_in_fails_the_last_answers_are_kept_and_it_warns(clock_now, creds):
    site = FakeSite()
    good = adapter(site).fetch()

    site.password = "changed"
    a = adapter(site)
    a.previous = {p.uid: p for p in good}
    perfs = a.fetch()
    assert a.warning == "ticket shop: RuntimeError: sign-in failed: Pogrešna lozinka"
    assert {p.uid: (p.status, p.available, p.buy_url) for p in perfs} == {p.uid: (p.status, p.available, p.buy_url) for p in good}
    assert any(p.extra.get("shop_only") and p.start.day == 2 for p in perfs)  # 2 December is still there

    site.password, site.shop = "secret", "<html><body>redesigned</body></html>"
    b = adapter(site)
    b.fetch()
    assert "no performances found on the shop page" in b.warning


def state_from_the_public_site(ctx, site):
    """What the tracker knew before it could read the shop: every date, status unknown."""
    run(ctx, allow_burst=False)
    assert site.shop_requests() == 0 and {d["status"] for d in ctx.state.performances.values()} == {"unknown"}
    ctx.tg.sent.clear()


def test_first_signed_in_check_announces_only_what_is_new(make_ctx, clock, no_creds, monkeypatch):
    clock.t = NOW
    site = FakeSite()
    ctx = make_ctx([adapter(site)], settings={"release_season": {"days": [1, 1]}})
    ctx.cfg.venues["madlenianum"]["name"] = "Madlenianum"
    state_from_the_public_site(ctx, site)

    monkeypatch.setenv("MADLENIANUM_EMAIL", "someone@example.com")
    monkeypatch.setenv("MADLENIANUM_PASSWORD", "secret")
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent  # one message: the date only the shop has. The 12 known dates gaining counts is not news.
    assert msg.startswith("🎟 <b>Madlenianum</b> · new tickets out\nDecember: +1 new date → 7 listed, 7 on sale\n")
    assert '<a href="https://operatheatremadlenianum.com/evita"><b>EVITA</b></a>\n   Wed 02.12. · <a href="https://tickets.operatheatremadlenianum.com/sr/odabir-ulaznica?id=1170">19:30</a> 🟢 162' in msg
    assert "KRCKO" not in msg and "went on sale" not in msg

    st = ctx.state
    assert st.performances["madlenianum:evita-202611281930"]["available"] == 33
    # when the known dates went on sale isn't known, so none of them can later look like a fast sell-out
    assert "on_sale" not in st.sales["madlenianum:evita-202611281930"] and "on_sale_since" not in st.meta["madlenianum:evita-202611281930"]
    assert st.sales["madlenianum:evita-202612021930"]["on_sale"] == clock().isoformat(timespec="seconds")  # first seen now
    assert st.sales["madlenianum:evita-202612021930"]["counts"] == [[clock().isoformat(timespec="seconds"), 162]]

    ctx.tg.sent.clear()
    for _ in range(3):  # later checks (22:00, 00:00, 02:00): same numbers, nothing to say…
        clock.sleep(7200)
        run(ctx, allow_burst=False)
    assert ctx.tg.sent == []
    counts = st.sales["madlenianum:evita-202612021930"]["counts"]
    assert [(c[0][:10], c[1]) for c in counts] == [("2026-11-17", 162), ("2026-11-18", 162)]  # …and one point a day, not one per check

    site.shop = site.shop.replace("KUPI (162)", "KUPI (150)")
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    assert [c[1] for c in st.sales["madlenianum:evita-202612021930"]["counts"]] == [162, 162, 150]  # a change is recorded at once


def test_tickets_going_on_sale_in_the_shop_later_is_announced(make_ctx, clock, creds):
    clock.t = NOW
    site = FakeSite()
    from bs4 import BeautifulSoup

    full = site.shop
    soup = BeautifulSoup(full, "lxml")
    next(c for c in soup.select("div.events_entry_new") if "JA, EMA" in c.get_text()).decompose()
    site.shop = str(soup)  # Ja, Ema on 15 December is announced on the public site, but not in the shop yet
    ctx = make_ctx([adapter(site)], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    assert ctx.state.performances["madlenianum:ja-ema-202612151930"]["status"] == "not_on_sale"
    ctx.tg.sent.clear()

    site.shop = full
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent
    assert "December: 1 went on sale" in msg and "JA, EMA" in msg and "🟢 142" in msg
    assert ctx.state.sales["madlenianum:ja-ema-202612151930"]["on_sale"] == clock().isoformat(timespec="seconds")


def test_shop_trouble_is_reported_like_a_failing_site(make_ctx, clock, creds):
    clock.t = NOW
    site = FakeSite()
    ctx = make_ctx([adapter(site)], settings={"release_season": {"days": [1, 1]}})
    ctx.cfg.venues["madlenianum"]["name"] = "Madlenianum"
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()
    site.password = "changed"
    for _ in range(3):
        clock.sleep(7200)
        run(ctx, allow_burst=False)
    (alert,) = ctx.tg.sent  # no "sold out"/"new date" noise: the last answers were kept
    assert alert.startswith("⚠️ <b>Madlenianum ticket shop</b> failed 3 checks in a row") and "Pogrešna lozinka" in alert
    assert State.load(ctx.cfg.state_path).performances["madlenianum:evita-202612021930"]["available"] == 162
    site.password = "secret"
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent[-1] == "✅ <b>Madlenianum ticket shop</b> is working again."
