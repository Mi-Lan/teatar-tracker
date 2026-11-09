"""Narodno exact seat counts from the logged-in ticket pages (no network: a fake site)."""

from datetime import date
from pathlib import Path

import pytest
from conftest import FakeAdapter

from teatar import models
from teatar.adapters import narodno
from teatar.models import TZ, Status
from teatar.runner import run

FIX = Path(__file__).parent / "fixtures"
SEATS = (FIX / "narodno_seats_6454.html").read_text(encoding="utf-8")
LISTING = (FIX / "narodno_repertoar_2026_11.html").read_text(encoding="utf-8")
LOGIN = '<form data-account-form="login"><input type="hidden" name="fn" value="login"><input type="hidden" name="redirect" value=""><input name="email"><input name="pass" type="password"></form>'


class Resp:
    def __init__(self, url, text="", data=None):
        self.url, self.text, self._data = url, text, data

    def json(self):
        return self._data


class FakeSite:
    """narodnopozoriste.rs: the repertoire, a login, and ticket pages that need the login."""

    def __init__(self, password="secret", drop_session_after=None):
        self.password, self.logged_in, self.requests, self.posts = password, False, [], []
        self.drop_session_after = drop_session_after

    def get(self, url, **kw):
        self.requests.append(url)
        if url.endswith("/repertoar"):
            return Resp(url, LISTING)
        if url.endswith("/nalog/prijavi-se"):
            return Resp(url, LOGIN)
        if "odabir-ulaznica" in url:
            if self.drop_session_after is not None and sum("odabir" in u for u in self.requests) > self.drop_session_after:
                self.logged_in, self.drop_session_after = False, None
            if not self.logged_in:
                return Resp(narodno.LOGIN_PAGE + "?redirect=x", LOGIN)
            return Resp(url, SEATS)
        raise AssertionError(url)

    def post(self, url, json=None, **kw):
        assert url == narodno.API and json["fn"] == "login"  # never ticket_click: that would hold seats
        self.posts.append(json)
        self.logged_in = json["pass"] == self.password
        return Resp(url, data={"error": 0 if self.logged_in else 1, "errorMessage": "" if self.logged_in else "Pogrešna lozinka"})


@pytest.fixture
def today(monkeypatch):
    models.set_clock(lambda: models.datetime(2026, 11, 9, 22, 0, tzinfo=TZ))
    monkeypatch.setattr(narodno, "upcoming", lambda perfs: perfs)
    yield
    models.set_clock(None)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("NARODNO_EMAIL", "someone@example.com")
    monkeypatch.setenv("NARODNO_PASSWORD", "secret")


def adapter(site):
    return narodno.NarodnoAdapter("narodno", site)


def test_count_seats_from_the_ticket_page():
    assert narodno.count_seats(SEATS) == (323, 145)  # 178 sold ("prodato")
    assert narodno.count_seats(LOGIN) is None


def test_full_check_logs_in_and_counts_every_on_sale_date(today, creds):
    site = FakeSite()
    perfs = adapter(site).fetch()
    buyable = [p for p in perfs if p.buy_url and p.status.buyable]
    assert len(site.posts) == 1 and site.posts[0]["email"] == "someone@example.com"
    assert sum("odabir" in u for u in site.requests) == len(buyable) == 59
    assert all(p.available == 145 and p.extra["seats"] == 323 for p in buyable)
    assert {p.status for p in perfs if p.title == "Власт"} == {Status.LOW}  # "under 10%" on the listing stays


def test_quick_poll_and_no_credentials_skip_seat_pages(today, monkeypatch, creds):
    site = FakeSite()
    adapter(site).poll()
    monkeypatch.delenv("NARODNO_PASSWORD")
    a = adapter(site)
    perfs = a.fetch()
    assert not any("odabir" in u for u in site.requests) and site.posts == [] and a.warning == ""
    assert all(p.available is None for p in perfs)


def test_session_dropped_midway_logs_in_again(today, creds):
    site = FakeSite(drop_session_after=10)
    a = adapter(site)
    perfs = a.fetch()
    assert len(site.posts) == 2 and a.warning == ""
    assert all(p.available == 145 for p in perfs if p.buy_url and p.status.buyable)


def test_wrong_password_keeps_the_listing_and_warns(today, creds):
    site = FakeSite(password="changed")
    a = adapter(site)
    perfs = a.fetch()
    assert a.warning == "seat counts: RuntimeError: login failed: Pogrešna lozinka"
    assert len(perfs) == 69 and all(p.available is None for p in perfs)


def test_seat_count_problems_are_reported_like_a_failing_site(make_ctx, clock):
    from conftest import perf

    a = FakeAdapter("narodno", [perf(venue="narodno")])
    ctx = make_ctx([a])
    run(ctx, allow_burst=False)
    a.warning = "seat counts: RuntimeError: login failed: Pogrešna lozinka"
    for _ in range(3):
        clock.sleep(7200)
        run(ctx, allow_burst=False)
    (alert,) = [m for m in ctx.tg.sent if "failed 3 checks" in m]
    assert alert.startswith("⚠️ <b>NARODNO seat counts</b> failed 3 checks in a row") and "Pogrešna lozinka" in alert
    a.warning = ""
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    assert "✅ <b>NARODNO seat counts</b> is working again." in ctx.tg.sent


def test_apply_seats_marks_sold_out_and_low():
    p = narodno.parse(LISTING, today=date(2026, 11, 9))[0]
    p.status = Status.ON_SALE
    narodno.apply_seats(p, 300, 20)
    assert (p.available, p.status) == (20, Status.LOW)
    narodno.apply_seats(p, 300, 0)
    assert p.status == Status.SOLD_OUT
