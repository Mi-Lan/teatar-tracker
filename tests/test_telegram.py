from conftest import FakeAdapter, perf

from teatar import commands, models
from teatar.telegram import Telegram, TelegramError, chat_ids


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


def test_a_banner_goes_out_as_a_photo_with_the_message_as_its_caption(monkeypatch, tmp_path):
    monkeypatch.setattr("teatar.telegram.time.sleep", lambda s: None)
    banner = tmp_path / "madlenianum.jpg"
    banner.write_bytes(b"\xff\xd8 a picture")

    class Calls(Telegram):
        def __init__(self, refuse_photos=False):
            super().__init__("token", "111")
            self.seen, self._last_send, self.refuse_photos = [], -10.0, refuse_photos

        def _call(self, method, _files=None, **params):
            if method == "sendPhoto" and self.refuse_photos:
                raise TelegramError("Telegram sendPhoto failed: Bad Request: wrong file", 400)
            self.seen.append((method, _files, params))
            return {}

    tg = Calls()
    tg.send_all(["<b>first</b>", "second"], banner=str(banner))
    (method, files, params), (method2, files2, params2) = tg.seen
    assert method == "sendPhoto" and files["photo"] == ("madlenianum.jpg", b"\xff\xd8 a picture", "image/jpeg")
    assert params == {"chat_id": "111", "caption": "<b>first</b>", "parse_mode": "HTML"}
    assert method2 == "sendMessage" and files2 is None and params2["text"] == "second"  # only the first message carries the picture
    assert params2["link_preview_options"] == {"is_disabled": True}

    # a picture never costs you the message: too long for a caption, a missing file or a refusal -> the text alone
    for tg, text, path in [(Calls(), "x" * 1025, str(banner)), (Calls(), "short", str(tmp_path / "gone.jpg")), (Calls(refuse_photos=True), "short", str(banner))]:
        tg.send(text, banner=path)
        assert [(m, p["text"]) for m, _, p in tg.seen] == [("sendMessage", text)]
