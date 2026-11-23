from datetime import datetime, timedelta
from pathlib import Path

import pytest

from teatar import models
from teatar.adapters.base import Adapter
from teatar.config import Config
from teatar.models import TZ, Performance, Status
from teatar.runner import Ctx
from teatar.state import State
from teatar.telegram import Messenger

START = datetime(2026, 9, 22, 12, 0, tzinfo=TZ)

# The shipped settings are quiet (no digest, no status, no reminders: config.DEFAULTS). Tests run with
# everything switched on unless they say otherwise, so those features are exercised.
LOUD = {
    "digest": {"every": "weekly", "time": "09:00"},
    "release_season": {"times": ["00:05", "08:00"]},
    "releases": {"remind_before": ["24h", "1h"], "report_misses": True, "lookahead_minutes": 75},
}


class FakeClock:
    def __init__(self, t: datetime = START):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, seconds: float):
        self.t += timedelta(seconds=seconds)


class FakeTg(Messenger):
    chat_id = "42"

    def __init__(self):
        self.recipients = ["42"]
        self.sent: list[str] = []
        self.banners: list[str] = []
        self.inbox: list[dict] = []

    def send(self, text, chat_id=None, banner=""):
        self.sent.append(text)
        if banner:
            self.banners.append(banner)

    def updates(self, offset):
        pending = [u for u in self.inbox if u["update_id"] >= offset]
        return pending

    def say(self, text: str):
        uid = len(self.inbox) + 1
        self.inbox.append({"update_id": uid, "message": {"chat": {"id": 42}, "text": text, "date": models.now().timestamp()}})


class FakeAdapter(Adapter):
    """Returns whatever `self.perfs` (or `self.fn()`) produces."""

    def __init__(self, key, perfs=None, fn=None):
        super().__init__(key, http=None, name=key)
        self.perfs, self.fn, self.calls = perfs or [], fn, 0

    def fetch(self):
        self.calls += 1
        return self.fn() if self.fn else list(self.perfs)


def perf(venue="v", sid="1", title="Divlje meso", days=5, status=Status.ON_SALE, available=None) -> Performance:
    return Performance(
        venue=venue, source_id=sid, title=title, start=START.replace(hour=19, minute=30) + timedelta(days=days),
        status=status, available=available, buy_url="https://example.rs/buy",
    )


@pytest.fixture
def clock():
    c = FakeClock()
    models.set_clock(c)
    yield c
    models.set_clock(None)


@pytest.fixture
def make_ctx(tmp_path: Path, clock):
    def _make(adapters: list[Adapter], settings: dict | None = None, releases=None, watchlist=None) -> Ctx:
        merged = {section: {**LOUD.get(section, {}), **(settings or {}).get(section, {})} for section in {*LOUD, *(settings or {})}}
        cfg = Config(
            venues={a.key: {"adapter": "fake", "name": a.key.upper()} for a in adapters},
            settings=merged,
            watchlist=watchlist or [],
            releases=releases or [],
            state_path=tmp_path / "state.json",
        )
        return Ctx(cfg=cfg, state=State(), http=None, tg=FakeTg(), adapters={a.key: a for a in adapters}, started=clock(), sleep=clock.sleep)

    return _make


@pytest.fixture(autouse=True)
def _no_waiting_between_ticket_pages(monkeypatch):
    from teatar.adapters import narodno

    monkeypatch.setattr(narodno.NarodnoAdapter, "sleep", staticmethod(lambda s: None))
