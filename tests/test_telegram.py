from conftest import FakeAdapter, perf

from teatar import commands, models
from teatar.telegram import Telegram, chat_ids


class Recorder(Telegram):
    """Telegram with the HTTP call replaced: records (chat, text), fails for chats in `blocked`."""

    def __init__(self, *args, blocked=(), **kwargs):
        super().__init__("token", *args, **kwargs)
        self.calls, self.blocked, self._last_send = [], set(blocked), -10.0

    def _call(self, method, **params):
        if params.get("chat_id") in self.blocked:
            raise RuntimeError("Telegram sendMessage failed: Forbidden: bot was blocked by the user")
        self.calls.append((params.get("chat_id"), params.get("text")))
        return {}


def test_chat_ids_parsing():
    assert chat_ids("111", "222, 333 111", None, "") == ["111", "222", "333"]


def test_every_notification_goes_to_every_recipient(monkeypatch):
    monkeypatch.setattr("teatar.telegram.time.sleep", lambda s: None)
    tg = Recorder("111", "222,333", blocked={"333"})
    assert tg.chat_id == "111" and tg.recipients == ["111", "222", "333"]
    tg.send_all(["one", "two"])
    assert tg.calls == [("111", "one"), ("222", "one"), ("111", "two"), ("222", "two")]  # 333 blocked the bot: skipped
    tg.calls.clear()
    tg.send("just you", "222")
    assert tg.calls == [("222", "just you")]


def test_owner_failure_still_raises(monkeypatch):
    monkeypatch.setattr("teatar.telegram.time.sleep", lambda s: None)
    tg = Recorder("111", "222", blocked={"111"})
    try:
        tg.send("hi")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the owner's failure to raise")


def test_any_recipient_can_use_commands_and_gets_the_reply(make_ctx, clock):
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")])])
    ctx.tg.recipients = ["42", "77"]
    for uid, chat in [(1, 77), (2, 99)]:  # 99 isn't a recipient
        ctx.tg.inbox.append({"update_id": uid, "message": {"chat": {"id": chat}, "text": "/theatres", "date": models.now().timestamp()}})
    replies = []
    ctx.tg.send_all = lambda messages, chat_id=None: replies.append((chat_id, messages))
    assert commands.handle(ctx, ctx.tg.updates(0)) == 1
    (chat, (text,)), = replies
    assert chat == "77" and "🎭 <b>Theatres I watch</b>" in text and "/theatre jdp" in text


def test_theatres_lists_links(make_ctx, clock):
    ctx = make_ctx([FakeAdapter("jdp", [perf(venue="jdp")]), FakeAdapter("sava", [])])
    ctx.cfg.venues["jdp"]["website"] = "https://www.jdp.rs/rs/repertoire-feed/"
    ctx.cfg.venues["sava"].update(adapter="tickets_rs", slug="sava_centar_3")
    ctx.state.performances = {p.uid: p.to_dict() for p in [perf(venue="jdp")]}
    (text,) = commands.cmd_theatres(ctx, "")
    assert '• <a href="https://www.jdp.rs/rs/repertoire-feed/"><b>JDP</b></a> · jdp.rs · 1 upcoming date · /theatre jdp' in text
    assert '<a href="https://tickets.rs/venue/sava_centar_3"><b>SAVA</b></a> · tickets.rs · 0 upcoming dates' in text
