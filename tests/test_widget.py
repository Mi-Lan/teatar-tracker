"""The experimental framed-playbill widget (HTML sent with sendWidget)."""

from datetime import datetime

from conftest import perf

from teatar import widget
from teatar.models import TZ, Status


def show(title, day, hhmm, status=Status.ON_SALE, left=None):
    p = perf(venue="madl", sid=f"{title}-{day}-{hhmm}", title=title, status=status, available=left)
    h, m = map(int, hhmm.split(":"))
    p.start = datetime(2026, 12, day, h, m, tzinfo=TZ)
    p.url, p.buy_url = f"https://site.example/{title.lower()}", f"https://shop.example/t?id={day}{h}"
    return p


def test_playbill_lists_plays_with_a_line_per_day_and_links():
    krcko = [show("Krcko <&> Oraščić", 1, "17:00", left=174), show("Krcko <&> Oraščić", 1, "19:30", left=146)]
    later = [show("Evita", d, t, Status.NOT_ON_SALE) for d in (16, 29) for t in ("17:00", "19:30")]
    html, rows = widget.release_widget("Madlenianum", "Tickets for December are out", "2 new dates", [krcko, later], "https://site.example/repertoar")
    assert rows == 2 + 3  # a title and one day line; a title and two day lines
    assert "<h1>Madlenianum</h1>" in html and "Krcko &lt;&amp;&gt; Oraščić</a>" in html  # titles are escaped
    assert '<span class=d>Tue 01.12.</span><span><a href="https://shop.example/t?id=117">17:00</a><i></i>174</span>' in html
    assert "<span class=d>Wed 16.12.</span><span>17:00 · 19:30 <span class=q>not on sale yet</span></span>" in html  # said once
    assert '<i></i>tickets left · <a href="https://site.example/repertoar">site.example</a>' in html
    # nothing a widget may not contain: no scripts, no outside resources, only https links
    assert "<script" not in html and "url(" not in html and "http://" not in html and "@import" not in html


def test_only_what_fits_goes_out_as_a_widget():
    one = [show("Evita", 2, "19:30", left=152)]
    html, rows = widget.release_widget("Madlenianum", "Tickets are out", "1 new date", [one])
    assert widget.fits(html, rows) and widget.compressed_size(html) < 1200  # (the limit is 4096)
    big = [[show(f"Play {i}", 1 + i % 28, "19:30", left=10)] for i in range(40)]
    html, rows = widget.release_widget("Narodno", "Tickets are out", "40 new dates", big)
    assert rows == 80 and not widget.fits(html, rows)  # taller than the 600 px a widget gets: send the text instead
