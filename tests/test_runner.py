from datetime import date, datetime, timedelta

from conftest import START, FakeAdapter, perf

from teatar import releases as rel
from teatar.models import TZ, Status
from teatar.runner import run


def test_parse_when():
    today = date(2026, 9, 22)
    assert rel.parse_when("2026-10-01 10:00", today) == (datetime(2026, 10, 1, 10, 0, tzinfo=TZ), False)
    assert rel.parse_when("1.10. 10:00", today) == (datetime(2026, 10, 1, 10, 0, tzinfo=TZ), False)
    assert rel.parse_when("05.01.2027", today) == (datetime(2027, 1, 5, 0, 0, tzinfo=TZ), True)
    assert rel.parse_when("3.2.", today)[0].year == 2027  # past day/month rolls to next year


def test_first_run_is_baseline_then_alerts(make_ctx, clock):
    a = FakeAdapter("jdp", [perf(venue="jdp")])
    ctx = make_ctx([a])
    run(ctx, allow_burst=False)
    assert ctx.state.initialized
    assert any("Tracker is live" in m for m in ctx.tg.sent)
    assert not any("Theatre update" in m for m in ctx.tg.sent)

    ctx.tg.sent.clear()
    a.perfs.append(perf(venue="jdp", sid="2", title="Gubitnik", days=12))
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert len(ctx.tg.sent) == 1 and "New shows" in ctx.tg.sent[0] and "Gubitnik" in ctx.tg.sent[0]

    ctx.tg.sent.clear()
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []  # nothing changed, nothing sent


def test_failing_venue_keeps_data_and_warns(make_ctx, clock):
    good = FakeAdapter("jdp", [perf(venue="jdp", days=5)])
    ctx = make_ctx([good])
    run(ctx, allow_burst=False)

    def boom():
        raise ConnectionError("site down")

    good.fn = boom
    for _ in range(3):
        clock.sleep(900)
        run(ctx, allow_burst=False)
    assert "jdp:1" in ctx.state.performances  # old data kept
    assert sum("failed 3 checks" in m for m in ctx.tg.sent) == 1

    good.fn = None
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert any("working again" in m for m in ctx.tg.sent)


def test_release_burst_catches_tickets_within_a_minute(make_ctx, clock):
    release_at = START + timedelta(minutes=40)

    def jdp_feed():
        items = [perf(venue="jdp", sid="1", days=3)]
        if clock() >= release_at + timedelta(seconds=20):  # site publishes October 20 s late
            items.append(perf(venue="jdp", sid="oct1", title="Ričard Drugi", days=15))
        return items

    a = FakeAdapter("jdp", fn=jdp_feed)
    ctx = make_ctx([a], releases=[{"venue": "jdp", "at": "2026-09-22 12:40", "note": "oktobar"}])
    run(ctx, allow_burst=False)  # baseline run, 40 min before the release
    assert sum("Ticket release in 1h" in m for m in ctx.tg.sent) == 1
    ctx.tg.sent.clear()

    clock.sleep(60)
    run(ctx)  # 39 min before release: inside the 75-min lookahead → burst

    assert not any("Ticket release in" in m for m in ctx.tg.sent)  # reminder not repeated
    alert = next(m for m in ctx.tg.sent if "Release watch" in m)
    assert "Ričard Drugi" in alert
    # alert went out on the first poll after publication (poll every 30 s)
    assert ctx.state.notified[f"new_show:jdp:oct1"] <= (release_at + timedelta(seconds=60)).isoformat()
    assert any("Release window closed" in m for m in ctx.tg.sent)
    assert clock() >= release_at + timedelta(minutes=45)  # polled until the window closed


def test_telegram_commands(make_ctx, clock):
    a = FakeAdapter("jdp", [perf(venue="jdp", title="Ričard Drugi", status=Status.LOW), perf(venue="jdp", sid="2", title="Gubitnik", days=1)])
    ctx = make_ctx([a])
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()

    ctx.tg.say("/watch ричард")
    ctx.tg.say("gubitnik")
    ctx.tg.say("/release jdp 1.10. 10:00 oktobar")
    ctx.tg.say("/tomorrow")
    clock.sleep(900)
    run(ctx, allow_burst=False)

    assert ctx.state.watchlist == [{"query": "ричард"}]
    assert ctx.state.telegram_offset == 5
    assert ctx.state.releases[0]["venue"] == "jdp" and ctx.state.releases[0]["note"] == "oktobar"
    out = "\n".join(ctx.tg.sent)
    assert "Watching" in out and "Gubitnik" in out and "Release added" in out and "Tomorrow" in out

    ctx.tg.sent.clear()
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []  # commands are not answered twice
