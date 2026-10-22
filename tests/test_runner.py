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


def test_quiet_week_then_monday_report(make_ctx, clock):
    # START is Tuesday 22.09. 12:00
    a = FakeAdapter("jdp", [perf(venue="jdp")])
    ctx = make_ctx([a], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    assert ctx.state.initialized
    assert any("Tracker is live" in m for m in ctx.tg.sent)

    ctx.tg.sent.clear()
    a.perfs.append(perf(venue="jdp", sid="2", title="Gubitnik", days=6))
    a.perfs.append(perf(venue="jdp", sid="3", title="Gubitnik", days=7))
    for _ in range(24 * 5):  # hourly checks through Sunday: a couple of new dates are found but not messaged
        clock.sleep(3600)
        run(ctx, allow_burst=False)
    assert ctx.tg.sent == []

    while clock().weekday() != 0 or clock().hour < 9:  # Monday 09:xx
        clock.sleep(3600)
        run(ctx, allow_burst=False)
    report = "\n".join(ctx.tg.sent)
    # a self-contained overview until the end of next month, not a list of changes
    assert report.startswith("🎭 <b>Weekly overview · Until 31.10.")
    assert report.count("Gubitnik") == 2  # (the Sunday show before the report is correctly gone)
    assert "New shows" not in report

    ctx.tg.sent.clear()
    clock.sleep(3600)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []  # once a week


def test_watched_plays_are_messaged_immediately(make_ctx, clock):
    a = FakeAdapter("jdp", [perf(venue="jdp", status=Status.NOT_ON_SALE)])
    ctx = make_ctx([a], watchlist=[{"query": "divlje meso"}], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()
    a.perfs = [perf(venue="jdp", status=Status.ON_SALE, available=40)]
    clock.sleep(3600)
    run(ctx, allow_burst=False)
    assert len(ctx.tg.sent) == 1 and "Tickets now on sale" in ctx.tg.sent[0] and "⭐" in ctx.tg.sent[0]


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
    alert = next(m for m in ctx.tg.sent if m.startswith("🎟 <b>JDP</b>"))  # one date is a release at a venue with a known release
    assert "October: +1 new date → 1 listed, 1 on sale\nRičard Drugi\nAppeared 22.09. between 12:40 and 12:40" in alert  # 30-s polls
    assert not any("Release watch" in m for m in ctx.tg.sent)  # no separate list of dates
    # alert went out on the first poll after publication (poll every 30 s)
    assert ctx.state.notified[f"new_show:jdp:oct1"] <= (release_at + timedelta(seconds=60)).isoformat()
    assert not any("No new tickets yet" in m for m in ctx.tg.sent)  # you were alerted; no extra message
    assert clock() >= release_at + timedelta(minutes=45)  # polled until the window closed


def test_release_with_nothing_published_says_so_once(make_ctx, clock):
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")])], releases=[{"venue": "jdp", "at": "2026-09-22 12:40"}])
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()
    clock.sleep(60)
    run(ctx)
    assert sum("No new tickets yet" in m for m in ctx.tg.sent) == 1


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


NARODNO_MONTHLY = {
    "venue": "jdp", "note": "next month's tickets", "day": 23, "time": "00:00", "also_all_day": True,
    "remind": [{"days_before": 2, "at": "09:00"}, {"days_before": 1, "at": "09:00"}],
    "link": "https://www.narodnopozoriste.rs/repertoar",
}


def test_recurring_instances_skip_a_release_that_already_happened():
    now = datetime(2026, 9, 23, 10, 0, tzinfo=TZ)  # September's midnight release is past
    inst = rel.recurring_instances(NARODNO_MONTHLY, now.date(), now)
    assert [(r["at"], r["all_day"]) for r in inst] == [("2026-10-23T00:00:00+02:00", False), ("2026-10-23T00:00:00+02:00", True)]
    assert inst[0]["remind_at"] == ["2026-10-21T09:00:00+02:00", "2026-10-22T09:00:00+02:00"]
    assert inst[0]["id"] != inst[1]["id"]


def test_monthly_reminders_on_21st_and_22nd_then_midnight_burst(make_ctx, clock):
    clock.t = datetime(2026, 10, 20, 12, 0, tzinfo=TZ)

    nov = perf(venue="jdp", sid="nov", title="Tosca")
    nov.start = datetime(2026, 11, 5, 20, 0, tzinfo=TZ)

    def feed():  # tickets for November appear at 00:02 on the 23rd
        items = [perf(venue="jdp", sid="1", days=3)]
        if clock() >= datetime(2026, 10, 23, 0, 2, tzinfo=TZ):
            items.append(nov)
        return items

    ctx = make_ctx([FakeAdapter("jdp", fn=feed)])
    ctx.cfg.recurring = [NARODNO_MONTHLY]
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()

    def reminders():
        return [m for m in ctx.tg.sent if m.startswith("⏰")]

    for when, expected in [
        (datetime(2026, 10, 21, 8, 49, tzinfo=TZ), 0),
        (datetime(2026, 10, 21, 9, 4, tzinfo=TZ), 1),
        (datetime(2026, 10, 21, 9, 19, tzinfo=TZ), 1),  # not repeated
        (datetime(2026, 10, 22, 9, 4, tzinfo=TZ), 2),
    ]:
        clock.t = when
        run(ctx, allow_burst=False)
        assert len(reminders()) == expected, when
    first, second = reminders()
    assert "tomorrow night at midnight (22.→23.10.)" in first
    assert "tonight at midnight (22.→23.10.)" in second
    assert "every 30s from 22.10. 23:50" in second and "08:00–22:00" in second and "repertoire" in second

    ctx.tg.sent.clear()
    clock.t = datetime(2026, 10, 22, 22, 49, tzinfo=TZ)  # a cron run inside the 75-min lookahead
    ctx.started = clock()
    run(ctx)
    alert = next(m for m in ctx.tg.sent if m.startswith("🎟 <b>JDP</b>"))
    assert "tickets for <b>November</b> are out" in alert and "Tosca" in alert
    assert ctx.state.notified["new_show:jdp:nov"] <= "2026-10-23T00:03:00+02:00"

    # the all-day fallback on the 23rd stands down because tickets already came out at midnight
    clock.t = datetime(2026, 10, 23, 7, 4, tzinfo=TZ)
    assert rel.active_windows(ctx.state, ctx.cfg) == []


def test_season_check_each_morning_20th_to_27th(make_ctx, clock):
    clock.t = datetime(2026, 9, 19, 12, 0, tzinfo=TZ)
    oct_show = perf(venue="jdp", sid="oct", title="Sirano", days=0)
    oct_show.start = datetime(2026, 10, 5, 20, 0, tzinfo=TZ)
    feed = [perf(venue="jdp", sid="1", days=0)]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed)), FakeAdapter("bdp", [])])
    run(ctx, allow_burst=False)  # baseline on the 19th
    ctx.tg.sent.clear()

    def season_msgs():
        return [m for m in ctx.tg.sent if m.startswith("🎟 <b>Tickets for")]

    for day, hour in [(19, 10), (20, 8), (20, 9), (20, 15)]:
        clock.t = datetime(2026, 9, day, hour, 7, tzinfo=TZ)
        run(ctx, allow_burst=False)
    assert len(season_msgs()) == 1  # once, on the 20th after 09:00
    assert "Tickets for October" in season_msgs()[0] and "JDP</b>: nothing for October yet" in season_msgs()[0]

    feed.append(oct_show)  # JDP publishes October on the 21st
    clock.t = datetime(2026, 9, 21, 9, 7, tzinfo=TZ)
    run(ctx, allow_burst=False)
    release = [m for m in ctx.tg.sent if "tickets for <b>October</b> are out" in m]
    assert len(release) == 1 and release[0].startswith("🎟 <b>JDP</b>")  # messaged right away…
    assert "Appeared 21.09. between 15:07 and 09:07" not in release[0]  # (previous check was the day before)
    assert "between 20.09. 15:07 and 21.09. 09:07" in release[0]
    latest = season_msgs()[-1]
    assert "JDP</b>: tickets for 1 of 1 dates 🆕" in latest
    assert "BDP</b>: nothing for October yet" in latest

    for day in range(22, 30):
        clock.t = datetime(2026, 9, day, 9, 7, tzinfo=TZ)
        run(ctx, allow_burst=False)
    assert len(season_msgs()) == 8  # 20th … 27th, one per morning
    assert "🆕" not in season_msgs()[-1]  # nothing new after the 21st


def test_midnight_check_and_release_time(make_ctx, clock):
    clock.t = datetime(2026, 9, 23, 20, 5, tzinfo=TZ)
    oct_show = perf(venue="jdp", sid="oct", title="Sirano")
    oct_show.start = datetime(2026, 10, 5, 20, 0, tzinfo=TZ)
    feed = [perf(venue="jdp", sid="1", days=3)]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))])
    run(ctx, allow_burst=False)  # baseline
    ctx.tg.sent.clear()

    for hour in (21, 22, 23):  # hourly checks at :05 — nothing for October yet
        clock.t = datetime(2026, 9, 23, hour, 5, tzinfo=TZ)
        run(ctx, allow_burst=False)
    feed.append(oct_show)  # JDP publishes at 23:40
    clock.t = datetime(2026, 9, 24, 0, 5, 30, tzinfo=TZ)
    run(ctx, allow_burst=False)

    midnight = [m for m in ctx.tg.sent if m.startswith("🎟 <b>Tickets for")][-1]
    assert "Thu 24.09. · 00:05 check" in midnight
    assert "JDP</b>: tickets for 1 of 1 dates 🆕 · out 24.09. 00:05 (not yet at 23:05)" in midnight
    assert ctx.state.release_log == [{
        "venue": "jdp", "month": "2026-10", "after": "2026-09-23T23:05:00+02:00", "by": "2026-09-24T00:05:30+02:00",
        "new": 1, "opened": 0, "on_sale": 1, "listed": 1, "buyable": 1, "unknown": 0,
    }]
    release = [m for m in ctx.tg.sent if m.startswith("🎟 <b>JDP</b>")]
    assert len(release) == 1 and "Appeared 24.09. between 23.09. 23:05 and 24.09. 00:05" in release[0]

    clock.t = datetime(2026, 9, 24, 8, 5, tzinfo=TZ)
    run(ctx, allow_burst=False)
    morning = [m for m in ctx.tg.sent if m.startswith("🎟 <b>Tickets for")][-1]
    assert "08:00 check" in morning and "🆕" not in morning  # already reported at midnight
    assert "out 24.09. 00:05 (not yet at 23:05)" in morning  # the release time stays in the status


def test_batch_release_is_messaged_once_with_its_time_window(make_ctx, clock):
    """A theatre that puts out many dates at once (any month) is a release: one message, bracketed by checks."""
    clock.t = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)
    feed = [perf(venue="narodno", sid="1", days=3)]
    ctx = make_ctx([FakeAdapter("narodno", fn=lambda: list(feed)), FakeAdapter("sava", [perf(venue="sava")])], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()

    for hour in (13, 14):
        clock.t = datetime(2026, 10, 5, hour, 2, tzinfo=TZ)
        run(ctx, allow_burst=False)
    for i in range(6):  # 6 December dates appear, two of them not on sale yet
        p = perf(venue="narodno", sid=f"dec{i}", title=f"Play {i}", status=Status.NOT_ON_SALE if i < 2 else Status.ON_SALE)
        p.start = datetime(2026, 12, 1 + i, 19, 30, tzinfo=TZ)
        feed.append(p)
    p = perf(venue="narodno", sid="jan", title="Play 9", status=Status.ON_SALE)  # plus one for January
    p.start = datetime(2027, 1, 3, 19, 30, tzinfo=TZ)
    feed.append(p)
    clock.t = datetime(2026, 10, 5, 17, 2, 42, tzinfo=TZ)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == [
        "🎟 <b>NARODNO</b> · new tickets out\n"
        "December: +6 new dates → 6 listed, 4 on sale\n"
        "January: +1 new date → 1 listed, 1 on sale\n"
        "Play 0, Play 1, Play 2, Play 3, Play 4, Play 5, +1 more\n"
        "Appeared 05.10. between 14:02 and 17:02 · /theatre narodno · /new"
    ]
    assert [(e["month"], e["new"], e["on_sale"]) for e in ctx.state.release_log] == [("2026-12", 6, 4), ("2027-01", 1, 1)]

    ctx.tg.sent.clear()
    feed[1].status = feed[2].status = Status.ON_SALE  # the two remaining dates go on sale later that day: logged, not messaged again
    feed.append(perf(venue="narodno", sid="one", title="Single", days=20))  # one more date alone is not a release
    clock.t = datetime(2026, 10, 5, 21, 15, tzinfo=TZ)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []
    assert ctx.state.meta["narodno:dec0"]["on_sale_since"] == "2026-10-05T21:15:00+02:00"

    clock.t = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
    ctx.tg.say("/new")
    run(ctx, allow_burst=False)
    (reply,) = ctx.tg.sent
    assert reply.startswith("🆕 <b>New in the last 7 days</b>")
    assert "📅 <b>Mon 05.10.</b>\n<i>NARODNO</i> · +8 dates (Oct 1, Dec 6, Jan 1), 8 on sale; 2 went on sale (Dec 2) · between 14:02 and 17:02\n  Single, Play 0, Play 1, Play 2, Play 3, Play 4, +2 more" in reply
    assert "SAVA" not in reply  # nothing new there

    clock.t = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
    ctx.tg.say("/new 1")
    run(ctx, allow_burst=False)
    assert "Nothing new" in ctx.tg.sent[-1]


def test_quiet_config_only_messages_releases(make_ctx, clock):
    """config/settings.yaml: no weekly overview, no 20th–27th status, no reminders, no "nothing yet" note."""
    clock.t = datetime(2026, 10, 20, 12, 0, tzinfo=TZ)
    feed = [perf(venue="jdp", sid="1", days=3)]
    settings = {"digest": {"every": "off"}, "release_season": {"times": []}, "releases": {"remind_before": [], "report_misses": False}}
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))], settings=settings, releases=[{"venue": "jdp", "at": "2026-10-22 10:00"}])
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()

    t = clock()
    while t < datetime(2026, 10, 28, 12, 0, tzinfo=TZ):  # a week of checks across a Monday and the 20th–27th, with a release that brings nothing
        t += timedelta(hours=1)
        clock.t = t
        ctx.started = t
        run(ctx)
    assert ctx.tg.sent == []

    p = perf(venue="jdp", sid="nov", title="Tosca")
    p.start = datetime(2026, 11, 5, 20, 0, tzinfo=TZ)
    feed.append(p)
    clock.t = datetime(2026, 10, 28, 13, 0, tzinfo=TZ)
    run(ctx, allow_burst=False)
    assert len(ctx.tg.sent) == 1 and ctx.tg.sent[0].startswith("🎟 <b>JDP</b> · tickets for <b>November</b> are out")
