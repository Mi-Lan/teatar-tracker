"""Things that used to stop the tracker, repeat messages or grow without bound."""

import json
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx
from conftest import START, FakeAdapter, perf

from teatar import commands, lookups, runner
from teatar import releases as rel
from teatar.adapters import narodno
from teatar.adapters.base import dated_id, find_time, infer_year, label_status
from teatar.models import TZ, Performance, Status
from teatar.runner import run
from teatar.state import State
from teatar.telegram import Telegram, TelegramError

API = "https://api.telegram.org/bottoken"
NO_STATUS = {"release_season": {"days": [1, 1]}}  # START is the 22nd: keep the 20th–27th status out of these tests


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("teatar.telegram.time.sleep", lambda s: None)


# --- state -------------------------------------------------------------------------------------------------


def test_state_is_written_whole_or_not_at_all(tmp_path, monkeypatch, clock):
    path = tmp_path / "state.json"
    st = State(initialized=True)
    assert st.save(path) and not st.save(path)  # unchanged: not rewritten
    before = path.read_text()

    st.last_digest = "2026-09-22"
    monkeypatch.setattr("teatar.state.os.replace", lambda a, b: (_ for _ in ()).throw(OSError("killed mid-save")))
    with pytest.raises(OSError):
        st.save(path)
    assert path.read_text() == before and json.loads(before)["initialized"] is True  # the old file is intact

    path.write_text('{"initialized": tr')  # a truncated file must stop the run, not start a fresh baseline
    with pytest.raises(ValueError, match="not valid JSON"):
        State.load(path)


def test_state_from_another_version_still_loads(tmp_path):
    d = perf().to_dict() | {"a_field_from_the_future": 1}
    assert Performance.from_dict(d) == perf()
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"initialized": True, "performances": {"v:1": d}, "something_new": {}}))
    assert State.load(path).perfs() == [perf()]


def test_bookkeeping_of_dates_no_longer_listed_is_dropped(clock):
    st = State()
    st.performances = {"v:1": perf().to_dict()}
    ago = lambda days: (START - timedelta(days=days)).isoformat(timespec="seconds")  # noqa: E731
    st.meta = {
        "v:1": {"first_seen": ago(200), "ever_on_sale": True},  # still listed: kept however old
        "v:gone-lately": {"first_seen": ago(10), "ever_on_sale": True},  # may come back: kept for now
        "v:gone-long-ago": {"first_seen": ago(61), "ever_on_sale": True},
    }
    st.release_log = [{"venue": "v", "month": "2026-03", "by": ago(181)}, {"venue": "v", "month": "2026-09", "by": ago(3)}]
    st.prune()
    assert set(st.meta) == {"v:1", "v:gone-lately"}
    assert [e["month"] for e in st.release_log] == ["2026-09"]


# --- a run that fails halfway ----------------------------------------------------------------------------


def test_a_failure_later_in_the_run_does_not_repeat_the_release_message(make_ctx, clock, monkeypatch):
    feed = [perf(venue="jdp")]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))], settings=NO_STATUS)
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()

    feed.append(perf(venue="jdp", sid="2", title="Gubitnik", days=9))
    clock.sleep(3600)
    with monkeypatch.context() as m:
        m.setattr(runner, "send_reminders", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            run(ctx, allow_burst=False)
    assert len(ctx.tg.sent) == 1 and "Gubitnik" in ctx.tg.sent[0]  # the release went out before the failure

    ctx.state = State.load(ctx.cfg.state_path)  # the next run starts from what was saved
    ctx.tg.sent.clear()
    clock.sleep(3600)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []  # …and doesn't announce it again


def test_a_command_that_fails_is_answered_and_not_retried_forever(make_ctx, clock, monkeypatch):
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")])], settings=NO_STATUS)
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()
    monkeypatch.setitem(commands.COMMANDS, "/week", lambda ctx, arg: 1 / 0)
    for text in ["/week", "/overview 99999999999999", "/new 99999999999999", "/unwatch 99999999999999", "/unrelease ٣", "/today"]:
        ctx.tg.say(text)
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert len(ctx.tg.sent) == 6 and ctx.state.telegram_offset == 7  # every message answered, none left to redeliver
    assert "Something went wrong answering “/week”" in ctx.tg.sent[0]
    assert "🎭 <b>Everything announced</b>" in ctx.tg.sent[1] and "Today" in ctx.tg.sent[5]


def test_telegram_being_down_does_not_stop_the_check(make_ctx, clock):
    feed = [perf(venue="jdp")]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))])
    run(ctx, allow_burst=False)
    ctx.tg.say("/today")

    ctx.tg.updates = lambda offset: (_ for _ in ()).throw(httpx.ConnectError("no route"))
    clock.sleep(900)
    run(ctx, allow_burst=False)  # nothing to say, so the check completes and its results are saved
    assert State.load(ctx.cfg.state_path).scanned["jdp"] == clock().isoformat(timespec="seconds")

    ctx.tg.updates = lambda offset: (_ for _ in ()).throw(TelegramError("Telegram getUpdates failed: Unauthorized", 401))
    with pytest.raises(TelegramError):  # a wrong token is not "down": it must fail loudly
        run(ctx, allow_burst=False)


def test_a_lookup_with_its_own_release_time_is_polled_in_the_burst(make_ctx, clock, monkeypatch):
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")])])
    ctx.state.watchlist = [{"query": "Tosca", "lookup": {"sixtix": "abc"}}]
    monkeypatch.setattr(lookups, "run_all", lambda http, wl: [perf(venue=lookups.VENUE, sid="sixtix-abc", title="Tosca")])
    assert set(runner.scan(ctx, keys={lookups.VENUE}, quick=True)) == {lookups.VENUE}
    assert set(runner.scan(ctx, keys={"jdp"}, quick=True)) == {"jdp"}
    assert set(runner.scan(ctx)) == {"jdp", lookups.VENUE}
    assert set(runner.scan(ctx, include_custom=False)) == {"jdp"}


def test_a_bad_entry_in_releases_yaml_is_skipped(make_ctx, clock):
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")])], releases=[{"venue": "jdp", "at": "31.2. 10:00"}, {"venue": "jdp", "at": "2026-10-01 10:00:00"}])
    ctx.cfg.recurring = [{"venue": "jdp", "day": "the 23rd"}]
    run(ctx, allow_burst=False)
    assert [r["at"] for r in ctx.state.releases] == ["2026-10-01T10:00:00+02:00"]  # (seconds are accepted)
    assert rel.parse_when("1.10.2026 10:00:00") == (datetime(2026, 10, 1, 10, 0, tzinfo=TZ), False)


# --- Telegram ------------------------------------------------------------------------------------------------


@respx.mock
def test_telegram_retries_what_can_pass_and_raises_what_cannot(no_sleep):
    tg = Telegram("token", "1")
    ok = httpx.Response(200, json={"ok": True, "result": {}})
    route = respx.post(f"{API}/sendMessage").mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(502, text="<html>Bad Gateway</html>"),
                     httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 1}}), ok]
    )
    tg.send("hello")
    assert route.call_count == 4

    respx.post(f"{API}/getUpdates").mock(return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(TelegramError, match="Unauthorized") as err:
        tg.updates(0)
    assert err.value.status == 401

    respx.post(f"{API}/getMe").mock(return_value=httpx.Response(502, text="down"))
    with pytest.raises(TelegramError, match="too many retries"):
        tg._call("getMe")


@respx.mock
def test_a_message_telegram_rejects_is_sent_as_plain_text(no_sleep):
    """Otherwise the same message would fail on every later run, and nothing behind it would ever be sent."""
    tg = Telegram("token", "1")
    sent = []

    def api(request):
        body = json.loads(request.content)
        if body.get("parse_mode"):
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities: unclosed tag"})
        sent.append(body["text"])
        return httpx.Response(200, json={"ok": True, "result": {}})

    respx.post(f"{API}/sendMessage").mock(side_effect=api)
    tg.send("<b>Tom &amp; Jerry" + "!" * 5000)
    assert "".join(sent) == "Tom & Jerry" + "!" * 5000 and [len(t) for t in sent] == [4096, 915]

    respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"}))
    with pytest.raises(TelegramError, match="chat not found"):
        tg.send("hi")  # not something plain text would fix


# --- parsing -------------------------------------------------------------------------------------------------


def test_year_inference_and_29_february():
    assert infer_year(1, 5, date(2026, 12, 20)) == date(2027, 1, 5)
    assert infer_year(11, 30, date(2026, 12, 20)) == date(2026, 11, 30)  # last month's date is still this year
    assert infer_year(2, 29, date(2027, 12, 20)) == date(2028, 2, 29)  # used to raise: 2027 has no 29 February
    with pytest.raises(ValueError):
        infer_year(2, 30, date(2027, 12, 20))


def test_shared_parsing_helpers():
    assert label_status("РАСПРОДАТО") == label_status("Rasprodat") == Status.SOLD_OUT
    assert label_status("Otkazano") == Status.CANCELLED and label_status("Мање од 10% слободних места") == Status.LOW
    assert label_status("") is None and label_status("Kupi kartu") is None
    assert find_time("уторак · 19:30 · Велика сцена") == "19:30" and find_time(None) is None
    # ids are what dates are tracked under: this format must never change
    assert dated_id(datetime(2026, 10, 3, 20, 0, tzinfo=TZ), "Дивље месо — премијера!") == "202610032000-divlje-meso-premijera"


def test_ticket_pages_without_a_seat_map_are_reported(monkeypatch, clock):
    from test_narodno_seats import FakeSite

    monkeypatch.setenv("NARODNO_EMAIL", "someone@example.com")
    monkeypatch.setenv("NARODNO_PASSWORD", "secret")
    clock.t = datetime(2026, 11, 9, 22, 0, tzinfo=TZ)
    monkeypatch.setattr(narodno, "count_seats", lambda html: None)  # the ticket page's layout changed
    a = narodno.NarodnoAdapter("narodno", FakeSite())
    perfs = a.fetch()
    assert perfs and "no seat map found" in a.warning  # the listing is kept, the problem is surfaced
