"""How a release message lays out plays and dates."""

from datetime import datetime

from conftest import FakeAdapter, perf

from teatar import format as fmt
from teatar.models import TZ, Status
from teatar.runner import run

SHOP = "https://shop.example/t?id="


def show(title, day, hhmm, status=Status.ON_SALE, left=None, month=12, sid=None):
    p = perf(venue="madl", sid=sid or f"{title}-{month}-{day}-{hhmm}", title=title, status=status, available=left)
    h, m = map(int, hhmm.split(":"))
    p.start = datetime(2026, month, day, h, m, tzinfo=TZ)
    p.url, p.buy_url = f"https://site.example/{title.lower()}", f"{SHOP}{day}{h}"
    return p


def test_a_play_with_several_dates_gets_one_line_per_day():
    krcko = [show("KRCKO", 1, "17:00", left=174), show("KRCKO", 1, "19:30", left=151), show("KRCKO", 7, "17:00", left=184), show("KRCKO", 7, "19:30", left=186)]
    assert fmt.play_block(krcko) == (
        '<a href="https://site.example/krcko"><b>KRCKO</b></a>\n'
        f'   Tue 01.12. · <a href="{SHOP}117">17:00</a> 🟢 174 · <a href="{SHOP}119">19:30</a> 🟢 151\n'
        f'   Mon 07.12. · <a href="{SHOP}717">17:00</a> 🟢 184 · <a href="{SHOP}719">19:30</a> 🟢 186'
    )
    # one date: the same shape, below the title
    assert fmt.play_block([show("EVITA", 2, "19:30", left=160)]) == (
        f'<a href="https://site.example/evita"><b>EVITA</b></a>\n   Wed 02.12. · <a href="{SHOP}219">19:30</a> 🟢 160'
    )


def test_the_same_status_for_every_show_that_day_is_said_once():
    later = [show("KRCKO", d, t, Status.NOT_ON_SALE) for d in (16, 29) for t in ("17:00", "19:30")]
    assert fmt.play_block(later).split("\n")[1:] == ["   Wed 16.12. · 17:00 · 19:30 ⏳ not on sale", "   Tue 29.12. · 17:00 · 19:30 ⏳ not on sale"]
    same_count = [show("KRCKO", 7, "17:00", left=186), show("KRCKO", 7, "19:30", left=186)]
    assert "17:00</a> 🟢 186 · " in fmt.play_block(same_count)  # a number belongs to its own show, even when two are equal
    mixed = [show("KRCKO", 7, "17:00", Status.SOLD_OUT), show("KRCKO", 7, "19:30", left=12), show("KRCKO", 9, "20:00", Status.UNKNOWN)]
    assert fmt.play_block(mixed).split("\n")[1:] == [f'   Mon 07.12. · 17:00 🔴 sold out · <a href="{SHOP}719">19:30</a> 🟢 12', "   Wed 09.12. · 20:00"]


def test_new_dates_that_are_not_on_sale_are_not_called_new_tickets(make_ctx, clock):
    clock.t = datetime(2026, 11, 18, 10, 7, tzinfo=TZ)
    feed = [show("KRCKO", 1, "17:00", left=174), show("EVITA", 2, "19:30", left=160)]
    ctx = make_ctx([FakeAdapter("madl", fn=lambda: list(feed))], settings={"release_season": {"days": [1, 1]}})
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()

    feed += [show("KRCKO", d, t, Status.NOT_ON_SALE) for d in (16, 29) for t in ("17:00", "19:30")]
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent
    assert msg == (
        "📋 <b>MADL</b> · new dates listed, tickets not on sale yet\n"  # (December's older dates are on sale; these four aren't)
        "December: +4 new dates → 6 listed, 2 on sale\n"
        "Appeared 18.11. between 10:07 and 12:07\n"
        "\n"
        '<a href="https://site.example/krcko"><b>KRCKO</b></a>\n'
        "   Wed 16.12. · 17:00 · 19:30 ⏳ not on sale\n"
        "   Tue 29.12. · 17:00 · 19:30 ⏳ not on sale\n"
        "\n"
        "/theatre madl · /new"  # no numbers in the message: no key for them
    )

    ctx.tg.sent.clear()
    feed += [show("JA, EMA", 15, "19:30", left=140)]
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent
    assert msg.startswith("🎟 <b>MADL</b> · new tickets out\n") and msg.endswith("🟢 140\n\n🟢 number = tickets left\n/theatre madl · /new")


# --- each theatre's own look ------------------------------------------------------------------------------


def test_a_look_styles_titles_and_dates():
    from teatar.config import Look

    krcko = [show("Krcko", 1, "17:00", left=174), show("Krcko", 1, "19:30", left=146), show("Krcko", 7, "17:00", left=181)]
    classic = fmt.play_block(krcko, look=Look(titles="italic", marker="✦", dates="quote"))
    assert classic == (
        '✦ <a href="https://site.example/krcko"><i>Krcko</i></a>\n'
        f'<blockquote>Tue 01.12. · <a href="{SHOP}117">17:00</a> 🟢 174 · <a href="{SHOP}119">19:30</a> 🟢 146\n'
        f'Mon 07.12. · <a href="{SHOP}717">17:00</a> 🟢 181</blockquote>'
    )
    modern = fmt.play_block([show("Evita", 2, "19:30", left=152)], mark="🔥", note="fast seller: sold out in 4h", look=Look(upper=True, marker="🟥"))
    assert modern == f'🟥 🔥 <a href="https://site.example/evita"><b>EVITA</b></a> · fast seller: sold out in 4h\n   Wed 02.12. · <a href="{SHOP}219">19:30</a> 🟢 152'


def test_release_message_uses_the_theatres_look_and_banner(make_ctx, clock):
    clock.t = datetime(2026, 11, 18, 10, 7, tzinfo=TZ)
    feed = [show("Krcko", 1, "17:00", left=174)]
    ctx = make_ctx([FakeAdapter("madl", fn=lambda: list(feed))], settings={"release_season": {"days": [1, 1]}})
    ctx.cfg.venues["madl"]["look"] = {"banner": "madlenianum", "titles": "italic", "marker": "✦", "dates": "quote", "rule": "· · · ✦ · · ·"}
    run(ctx, allow_burst=False)
    ctx.tg.sent.clear()
    ctx.tg.banners.clear()

    feed.append(show("Evita", 2, "19:30", left=152))
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    (msg,) = ctx.tg.sent
    assert msg == (
        "🎟 <b>MADL</b> · new tickets out\n"
        "December: +1 new date → 2 listed, 2 on sale\n"
        "Appeared 18.11. between 10:07 and 12:07\n"
        "· · · ✦ · · ·\n"
        '✦ <a href="https://site.example/evita"><i>Evita</i></a>\n'
        f'<blockquote>Wed 02.12. · <a href="{SHOP}219">19:30</a> 🟢 152</blockquote>\n'
        "\n"
        "🟢 number = tickets left\n"
        "/theatre madl · /new"
    )
    assert ctx.tg.banners == ["https://raw.githubusercontent.com/Mi-Lan/teatar-tracker/main/assets/banners/madlenianum.jpg"]

    ctx.cfg.settings["messages"] = {"banners": False}  # switched off in settings.yaml: the same text, no picture
    ctx.tg.banners.clear()
    feed.append(show("Ja, Ema", 15, "19:30", left=140))
    clock.sleep(7200)
    run(ctx, allow_burst=False)
    assert ctx.tg.banners == [] and "<i>Ja, Ema</i>" in ctx.tg.sent[-1]


def test_a_long_release_is_split_without_breaking_a_quote_block(monkeypatch):
    from teatar.config import Look

    look = Look(titles="italic", dates="quote")
    blocks = [fmt.play_block([show(f"Play {i}", 1 + d, "19:30", left=5) for d in range(20)], look=look) for i in range(12)]
    messages = fmt.pack(blocks, header="🎟 <b>Narodno</b> · new tickets out", joiner="\n")
    assert len(messages) > 1
    for m in messages:  # every message is valid on its own: each quote block opens and closes inside it
        assert m.count("<blockquote>") == m.count("</blockquote>") and fmt.entities(m) <= fmt.MAX_ENTITIES
    monkeypatch.setattr(fmt, "MAX_ENTITIES", 12)  # a single play too big for one message: its lines go out plain
    messages = fmt.pack(blocks[:1], header="head", joiner="\n")
    assert len(messages) > 1 and not any("blockquote" in m for m in messages)
