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
        self.listing = LISTING

    def unpublish(self, rep_id: str | None) -> None:
        """Serve the repertoire without one date (None: all of them again)."""
        if rep_id is None:
            self.listing = LISTING
            return
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(LISTING, "lxml")
        soup.select_one(f"#repid{rep_id}").decompose()
        self.listing = str(soup)

    def get(self, url, **kw):
        self.requests.append(url)
        if url.endswith("/repertoar"):
            return Resp(url, self.listing)
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
    narodno.apply_seats(p, 300, 20, "2026-11-09T09:07:00+01:00")
    assert (p.available, p.status, p.extra["counted_at"]) == (20, Status.LOW, "2026-11-09T09:07:00+01:00")
    narodno.apply_seats(p, 300, 0, "2026-11-09T09:07:00+01:00")
    assert p.status == Status.SOLD_OUT


class Checks:
    """Successive checks of one Narodno adapter, feeding each result back as `previous` like the runner does."""

    def __init__(self, site):
        self.site, self.prev = site, {}
        # dates already known from earlier days (as on the first run after this change ships)
        self.first_seen = {p.uid: "2026-11-01T12:00:00+01:00" for p in narodno.parse(LISTING, today=date(2026, 11, 9))}

    def at(self, hhmm, day=10):
        h, m = map(int, hhmm.split(":"))
        models.set_clock(lambda: models.datetime(2026, 11, day, h, m, tzinfo=TZ))
        self.site.requests.clear()
        self.site.posts.clear()
        self.site.logged_in = False
        a = narodno.NarodnoAdapter("narodno", self.site)
        a.previous, a.first_seen = dict(self.prev), dict(self.first_seen)
        perfs = a.fetch()
        for p in perfs:
            self.first_seen.setdefault(p.uid, models.now().isoformat())
        self.prev = {p.uid: p for p in perfs}
        return perfs, sum("odabir" in u for u in self.site.requests), len(self.site.posts)


def test_logs_in_once_a_day_and_after_a_release(today, creds):
    site = FakeSite()
    checks = Checks(site)
    new_id = next(p.source_id for p in narodno.parse(LISTING, today=date(2026, 11, 9)) if p.status.buyable and p.buy_url)
    site.unpublish(new_id)  # one date isn't published yet
    checks.first_seen.pop(f"narodno:{new_id}")

    perfs, pages, logins = checks.at("08:07")  # first check with this feature: every known on-sale date is due…
    assert logins == 1 and pages == 58
    perfs, pages, logins = checks.at("08:47")  # …then nothing until the daily count
    assert (pages, logins) == (0, 0)
    assert all(p.available == 145 and p.extra["counted_at"].startswith("2026-11-10T08:07") for p in perfs if p.status.buyable)
    perfs, pages, logins = checks.at("09:07")  # the first check after 09:00: the daily count, every on-sale date
    assert logins == 1 and pages == 58
    perfs, pages, logins = checks.at("11:07")  # same day: no login
    assert (pages, logins) == (0, 0)

    site.unpublish(None)  # the new date is published: messaged from the listing at once, not counted yet
    perfs, pages, logins = checks.at("13:07")
    assert (pages, logins) == (0, 0)
    perfs, pages, logins = checks.at("14:07")  # only an hour later: still waiting
    assert (pages, logins) == (0, 0)
    perfs, pages, logins = checks.at("15:07")  # 2 hours after it appeared: just that date is counted
    assert (pages, logins) == (1, 1)
    perfs, pages, logins = checks.at("17:07")
    assert (pages, logins) == (0, 0)
    perfs, pages, logins = checks.at("09:07", day=11)  # next morning: the daily count again
    assert logins == 1 and pages == 59


def test_failed_daily_count_is_retried_at_the_next_check(today, creds):
    site = FakeSite()
    checks = Checks(site)
    checks.at("08:07")  # counted
    site.password = "changed"  # the daily count fails…
    perfs, pages, logins = checks.at("09:07")
    assert logins == 1 and pages == 0
    site.password = "secret"
    perfs, pages, logins = checks.at("11:07")  # …and is tried again
    assert logins == 1 and pages == 59


def test_carried_over_counts_are_marked_old(today, creds):
    from teatar import format as fmt

    checks = Checks(FakeSite())
    checks.at("09:07")
    perfs, _, _ = checks.at("10:07")
    vlast = next(p for p in perfs if p.title == "Власт")
    assert fmt.short_badge(vlast) == "🟡 145"  # counted an hour ago
    perfs, _, _ = checks.at("13:07")
    vlast = next(p for p in perfs if p.title == "Власт")
    assert fmt.short_badge(vlast) == "🟡 ~145" and fmt.badge(vlast) == "🟡 ~145 left"
    assert "(~N: at the last daily count)" in fmt.overview(perfs, {}, "Narodno")[0]


def test_counts_are_kept_in_the_sell_out_history(make_ctx, clock, creds):
    clock.t = models.datetime(2026, 11, 9, 9, 30, tzinfo=TZ)
    site = FakeSite()
    ctx = make_ctx([narodno.NarodnoAdapter("narodno", site)])
    run(ctx, allow_burst=False)  # baseline check (the daily count is due)
    clock.t = models.datetime(2026, 11, 10, 9, 15, tzinfo=TZ)
    run(ctx, allow_burst=False)  # next day's count
    clock.t = models.datetime(2026, 11, 10, 11, 15, tzinfo=TZ)
    run(ctx, allow_burst=False)  # no login: the history doesn't repeat the carried-over count
    r = next(r for r in ctx.state.sales.values() if r["title"] == "Власт")
    assert [c[1] for c in r["counts"]] == [145, 145] and r["seats"] == 323
    assert [c[0][:13] for c in r["counts"]] == ["2026-11-09T09", "2026-11-10T09"]
