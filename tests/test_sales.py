import json
from datetime import datetime, timedelta

from conftest import START, FakeAdapter, perf

from teatar import backfill, commands, sales
from teatar.config import Config
from teatar.diff import apply
from teatar.models import TZ, Status
from teatar.runner import run
from teatar.state import State


def cfg(**selling) -> Config:
    return Config(venues={"jdp": {"name": "JDP"}}, settings={"selling": selling}, watchlist=[], releases=[], state_path=None)


def check(state, clock, hours, *perfs):
    clock.sleep(hours * 3600)
    apply(state, {"jdp": list(perfs)})


def test_records_on_sale_and_first_sell_out_only(clock):
    s = State()
    check(s, clock, 0, perf(venue="jdp", sid="old", status=Status.SOLD_OUT))  # baseline: history unknown
    check(s, clock, 1, perf(venue="jdp", sid="old", status=Status.SOLD_OUT), perf(venue="jdp", sid="1", days=10))
    on_sale = clock().isoformat(timespec="seconds")
    check(s, clock, 3, perf(venue="jdp", sid="1", days=10, status=Status.LOW))
    check(s, clock, 2, perf(venue="jdp", sid="1", days=10, status=Status.SOLD_OUT))
    sold = clock().isoformat(timespec="seconds")
    check(s, clock, 5, perf(venue="jdp", sid="1", days=10, status=Status.LOW))  # a returned ticket…
    check(s, clock, 5, perf(venue="jdp", sid="1", days=10, status=Status.SOLD_OUT))  # …gone again: not a new sell-out

    r = s.sales["jdp:1"]
    assert r["on_sale"] == on_sale and r["sold_out"] == sold and sales.hours_to_sell_out(r) == 5
    assert s.sales["jdp:old"] == {"venue": "jdp", "title": "Divlje meso", "start": s.sales["jdp:old"]["start"], "sold_out": None}
    assert sales.hours_to_sell_out(s.sales["jdp:old"]) is None


def test_sold_out_before_seen_on_sale_is_instant(clock):
    s = State()
    check(s, clock, 0, perf(venue="jdp", sid="a"))
    check(s, clock, 1, perf(venue="jdp", sid="a"), perf(venue="jdp", sid="new", status=Status.SOLD_OUT), perf(venue="jdp", sid="later", status=Status.NOT_ON_SALE))
    check(s, clock, 1, perf(venue="jdp", sid="a"), perf(venue="jdp", sid="new", status=Status.SOLD_OUT), perf(venue="jdp", sid="later", status=Status.SOLD_OUT))
    assert s.sales["jdp:new"]["instant"] and s.sales["jdp:later"]["instant"]
    assert sales.hours_to_sell_out(s.sales["jdp:new"]) == 0
    assert "jdp:a" in s.sales and "on_sale" not in s.sales["jdp:a"]  # on sale since the baseline: unknown


def test_fast_play_classification():
    s = State()
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=TZ)

    def rec(title, on_h, sold_h, show_days):
        return {"venue": "jdp", "title": title, "start": (t0 + timedelta(days=show_days)).isoformat(),
                "on_sale": t0.isoformat(), **({"sold_out": (t0 + timedelta(hours=sold_h)).isoformat()} if sold_h is not None else {})}

    s.sales = {
        "1": rec("Edip", 0, 6, 20), "2": rec("Edip", 0, 30, 25), "3": rec("Edip", 0, None, 30),
        "4": rec("Gubitnik", 0, 200, 20),  # sold out, slowly
        "5": rec("Last minute", 0, 20, 1.5),  # sold out within 48h, but on the day of the show: not fast
        "6": rec("Tosca", 0, None, 9),
    }
    table = sales.plays(s, cfg())
    edip = table["jdp|edip"]
    assert edip.is_fast and (edip.dates, edip.sold_out) == (3, 2) and edip.speeds == [6, 30]
    assert edip.flag() == "🔥 fast seller: sold out in 6h"
    assert edip.summary() == "sold out 2 of 3 dates · fastest: 6h, typically 18h"
    assert table["jdp|gubitnik"].flag() == "🔴 sells out" and not table["jdp|gubitnik"].is_fast
    assert not table["jdp|last minute"].is_fast
    assert table["jdp|tosca"].flag() == ""
    assert set(sales.fast_plays(s, cfg())) == {"jdp|edip"}
    assert set(sales.fast_plays(s, cfg(fast_hours=4))) == set()  # thresholds come from settings.yaml


def test_history_outlives_the_show_and_is_pruned_later(clock):
    s = State()
    check(s, clock, 0, perf(venue="jdp", sid="a", days=30))
    check(s, clock, 1, perf(venue="jdp", sid="a", days=30), perf(venue="jdp", sid="b", days=3))
    check(s, clock, 2, perf(venue="jdp", sid="a", days=30), perf(venue="jdp", sid="b", days=3, status=Status.SOLD_OUT))
    clock.sleep(10 * 86400)
    s.prune(history_days=180)
    assert "jdp:b" not in s.performances and "jdp:b" in s.sales
    clock.sleep(250 * 86400)
    s.prune(history_days=180)
    assert s.sales == {}


def fast_history(ctx, title="Edip", venue="jdp"):
    """Pretend the tracker saw `title` sell out 5 hours after going on sale, last month."""
    ctx.state.sales["old"] = {"venue": venue, "title": title, "start": (START - timedelta(days=10)).isoformat(),
                              "on_sale": (START - timedelta(days=30)).isoformat(), "sold_out": (START - timedelta(days=30, hours=-5)).isoformat()}


def test_single_new_date_is_messaged_with_its_fast_seller_flag(make_ctx, clock):
    feed = [perf(venue="jdp", sid="1", title="Gubitnik", days=3), perf(venue="jdp", sid="oct", title="Sofka", days=15)]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    fast_history(ctx)
    ctx.tg.sent.clear()
    feed.append(perf(venue="jdp", sid="3", title="EDIP", days=12))
    clock.sleep(900)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent
    assert msg.startswith("🎟 <b>JDP</b> · new tickets out\nOctober: +1 new date → 2 listed, 2 on sale\n")
    assert "🔥 1 of them sells out fast" in msg and "🔥 <a" in msg and "Sofka" not in msg  # only what's new


def test_with_bigger_batches_fast_sellers_still_get_a_message(make_ctx, clock):
    """batch_min above 1: a lone new date isn't messaged, unless its play sells out fast."""
    feed = [perf(venue="jdp", sid="1", title="Gubitnik", days=3), perf(venue="jdp", sid="oct", title="Sofka", days=15)]  # October already out
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))], settings={"release_season": {"days": [1, 1], "batch_min": 5}})
    ctx.cfg.venues["jdp"]["website"] = "https://www.jdp.rs/rs/repertoire-feed/"
    run(ctx, allow_burst=False)
    fast_history(ctx)
    ctx.tg.sent.clear()

    feed.append(perf(venue="jdp", sid="2", title="Gubitnik", days=9))  # one more date of an ordinary play: silent
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []

    feed.append(perf(venue="jdp", sid="3", title="EDIP", days=12))  # one date of a fast seller: messaged
    clock.sleep(900)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent
    assert msg.startswith("🔥 <b>JDP</b> · tickets on sale for a play that sells out fast\n1 date · Appeared 22.09. between 12:15 and 12:30\n\n")
    assert '🔥 <a href="https://example.rs/buy"><b>EDIP</b></a> · fast seller: sold out in 5h · Sun 04.10. 19:30 🟢' in msg
    assert msg.endswith('🌐 <a href="https://www.jdp.rs/rs/repertoire-feed/">jdp.rs</a> · /theatre jdp · /new')

    ctx.tg.sent.clear()
    clock.sleep(900)
    run(ctx, allow_burst=False)
    assert ctx.tg.sent == []  # once


def test_release_message_puts_fast_sellers_first(make_ctx, clock):
    feed = [perf(venue="jdp", sid="1", days=3)]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    fast_history(ctx, "Moj muž")
    ctx.tg.sent.clear()
    for i, title in enumerate(["Gubitnik", "Sofka", "Vatre", "Pučina", "Moj muž"]):
        feed.append(perf(venue="jdp", sid=f"n{i}", title=title, days=20 + i))
    clock.sleep(900)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent  # one release message, no separate fast-seller alert
    assert "🔥 1 of them sells out fast: get tickets early\n\n🔥 <a" in msg
    assert msg.index("Moj muž") < msg.index("Gubitnik")


def test_watched_fast_seller_is_not_messaged_twice(make_ctx, clock):
    for batch_min in (1, 5):
        feed = [perf(venue="jdp", sid="1", days=3), perf(venue="jdp", sid="oct", title="Sofka", days=15)]
        settings = {"release_season": {"days": [1, 1], "batch_min": batch_min}}
        ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))], watchlist=[{"query": "edip"}], settings=settings)
        run(ctx, allow_burst=False)
        fast_history(ctx)
        ctx.tg.sent.clear()
        feed.append(perf(venue="jdp", sid="2", title="Edip", days=12))
        clock.sleep(900)
        run(ctx, allow_burst=False)
        (msg,) = ctx.tg.sent  # one message, no separate watchlist alert
        assert "⭐🔥 <a" in msg and "New shows" not in msg, batch_min
        if batch_min == 5:
            assert msg.startswith("⭐ <b>JDP</b> · tickets on sale for a play you watch")


def test_hot_soldout_and_flags_in_lists(make_ctx, clock):
    feed = [perf(venue="jdp", sid="1", title="Edip", days=3), perf(venue="jdp", sid="2", title="Gubitnik", days=4)]
    ctx = make_ctx([FakeAdapter("jdp", fn=lambda: list(feed))])
    assert "No sell-outs recorded yet" in commands.cmd_hot(ctx, "")[0]
    run(ctx, allow_burst=False)
    fast_history(ctx)
    feed[0] = perf(venue="jdp", sid="1", title="Edip", days=3, status=Status.SOLD_OUT)
    clock.sleep(900)
    run(ctx, allow_burst=False)

    hot = "\n".join(commands.cmd_hot(ctx, ""))
    assert "🔥 <b>Plays that sell out fast</b>\nSold out within 2 days of tickets going on sale" in hot
    assert '<i>JDP</i>\n🔥 <a href="https://example.rs/buy"><b>Edip</b></a> · sold out 2 of 2 dates · fastest: 5h · Fri 25.09. 19:30 🔴 sold out' in hot
    soldout = commands.cmd_soldout(ctx, "")[0]
    assert "🔴 <b>Sold out</b> · 1 upcoming dates" in soldout and "19:30 Edip · <i>JDP</i>" in soldout
    week = "\n".join(commands.cmd_week(ctx, ""))
    assert "🔥Fri 25.09." not in week and "🔥19:30" in week and "🔥 sells out fast" in week
    assert "🔥 fast seller: sold out in 5h" in commands.cmd_search(ctx, "edip")[0]


def test_seed_from_git_history_is_imported_once(make_ctx, clock, tmp_path):
    def snap(status, at):
        p = perf(venue="jdp", sid="1", days=20, status=status)
        return {"performances": {p.uid: p.to_dict()}, "meta": {p.uid: {"first_seen": at}}}

    t = [(START + timedelta(hours=h)).isoformat() for h in range(4)]
    base = snap(Status.ON_SALE, t[0])
    base["performances"]["jdp:0"] = perf(venue="jdp", sid="0", days=2).to_dict()
    later = [snap(Status.NOT_ON_SALE, t[1]), snap(Status.ON_SALE, t[1]), snap(Status.SOLD_OUT, t[1])]
    for s in later:
        s["performances"]["jdp:0"] = base["performances"]["jdp:0"]
    snaps = [("c0", t[0], {"performances": {"jdp:0": base["performances"]["jdp:0"]}})] + [(f"c{i + 1}", t[i + 1], s) for i, s in enumerate(later)]
    records = backfill.replay(snaps)
    assert records["jdp:1"]["on_sale"] == t[2] and records["jdp:1"]["sold_out"] == t[3]

    seed = tmp_path / "sales_seed.json"
    seed.write_text(json.dumps({"generated": "x", "sales": records}))
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")])])
    assert sales.import_seed(ctx.state, seed) == 2 and ctx.state.sales_seed == "x"
    ctx.state.sales.pop("jdp:1")
    assert sales.import_seed(ctx.state, seed) == 0  # once
