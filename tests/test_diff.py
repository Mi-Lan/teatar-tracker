from conftest import perf

from teatar.diff import Kind, apply, compute
from teatar.models import Status
from teatar.state import State


def baseline(*perfs):
    s = State()
    by_venue = {}
    for p in perfs:
        by_venue.setdefault(p.venue, []).append(p)
    apply(s, by_venue)
    return s


def kinds(changes):
    return [(c.kind, c.perf.source_id) for c in changes]


def test_first_scan_of_a_venue_is_silent(clock):
    s = State()
    assert compute(s, {"v": [perf()]}, []) == []


def test_new_show_vs_new_date(clock):
    s = baseline(perf(sid="1", title="Divlje meso"))
    changes = compute(s, {"v": [perf(sid="1"), perf(sid="2", days=9), perf(sid="3", title="Tramvaj zvani želja")]}, [])
    assert kinds(changes) == [(Kind.NEW_DATE, "2"), (Kind.NEW_SHOW, "3")]


def test_cyrillic_title_is_not_a_new_show(clock):
    s = baseline(perf(sid="1", title="DIVLjE MESO"))
    changes = compute(s, {"v": [perf(sid="1", title="DIVLjE MESO"), perf(sid="2", title="Дивље месо")]}, [])
    assert kinds(changes) == [(Kind.NEW_DATE, "2")]


def test_sales_opened_then_back_in_stock(clock):
    s = baseline(perf(status=Status.NOT_ON_SALE))
    changes = compute(s, {"v": [perf(status=Status.ON_SALE, available=300)]}, [])
    assert kinds(changes) == [(Kind.SALES_OPENED, "1")]
    apply(s, {"v": [perf(status=Status.ON_SALE, available=300)]})

    # sells out (Narodno: the Buy button just disappears) …
    apply(s, {"v": [perf(status=Status.NOT_ON_SALE)]})
    # … and comes back: that's a return, not a first release
    changes = compute(s, {"v": [perf(status=Status.ON_SALE, available=2)]}, [{"query": "divlje"}])
    assert kinds(changes) == [(Kind.BACK_IN_STOCK, "1")]
    assert changes[0].watched and changes[0].reportable


def test_watch_only_changes_are_not_reportable_when_unwatched(clock):
    s = baseline(perf(status=Status.ON_SALE, available=50))
    changes = compute(s, {"v": [perf(status=Status.SOLD_OUT)]}, [])
    assert kinds(changes) == [(Kind.SOLD_OUT, "1")]
    assert not changes[0].reportable


def test_low_threshold_for_watched(clock):
    s = baseline(perf(available=25))
    changes = compute(s, {"v": [perf(available=15)]}, [{"query": "Divlje meso", "low_threshold": 20}])
    assert kinds(changes) == [(Kind.LOW, "1")]
    assert compute(s, {"v": [perf(available=15)]}, []) == []  # unwatched: no LOW


def test_watch_entry_venue_filter(clock):
    s = baseline(perf(available=25))
    changes = compute(s, {"v": [perf(available=5)]}, [{"query": "divlje", "venue": "other"}])
    assert changes == []


def test_apply_replaces_only_scanned_venues(clock):
    s = baseline(perf(venue="a", sid="1"), perf(venue="b", sid="1"))
    apply(s, {"a": []})
    assert set(s.performances) == {"b:1"}
