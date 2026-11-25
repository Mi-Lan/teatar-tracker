"""Minimal Telegram Bot API client (sendMessage + getUpdates polling)."""

from __future__ import annotations

import logging
import re
import time
from html import unescape

import httpx

log = logging.getLogger(__name__)

RETRIES = 3  # per API call, for network errors, 5xx replies and rate limits
MAX_TEXT = 4096  # Telegram's limit on one message's text
MAX_CAPTION = 1024  # …and on the text under a photo
_TAG = re.compile(r"<[^>]+>")


def plain(html: str) -> str:
    """The text a Telegram HTML message shows: tags removed, entities decoded."""
    return unescape(_TAG.sub("", html))


def chat_ids(*values: str | int | None) -> list[str]:
    """Chat ids from one or more values, each a single id or a comma/space-separated list; in order, no repeats."""
    out: list[str] = []
    for v in values:
        for part in re.split(r"[,\s]+", str(v or "").strip()):
            if part and part not in out:
                out.append(part)
    return out


class TelegramError(RuntimeError):
    """The Bot API answered with an error. `status` is the HTTP status (400 = Telegram rejected the request)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def unsendable(self) -> bool:
        """Telegram won't take this text as it is (broken markup, too long): retrying the same thing can't work."""
        text = str(self).lower()
        return self.status == 400 and any(w in text for w in ("parse", "entit", "too long"))


class Messenger:
    """What the tracker needs from a chat: `chat_id` is the owner, `recipients` everyone who gets notifications."""

    chat_id: str = ""
    recipients: tuple[str, ...] | list[str] = ()

    def send(self, text: str, chat_id: str | None = None, banner: str = "") -> None:
        """`banner`: the path of a picture to put at the top of the message (the text becomes its caption,
        so it must be within MAX_CAPTION; otherwise, or if the picture can't be sent, the text goes alone)."""
        raise NotImplementedError

    def send_all(self, messages: list[str], chat_id: str | None = None, banner: str = "") -> None:
        """Send the messages in order; the banner goes on the first one only."""
        for i, m in enumerate(messages):
            self.send(m, chat_id, banner if i == 0 else "")

    def updates(self, offset: int) -> list[dict]:
        return []


class Telegram(Messenger):
    """Every notification goes to all `recipients` (TELEGRAM_CHAT_ID plus TELEGRAM_EXTRA_CHAT_IDS[_2]);
    any of them can send commands, and the reply goes to whoever asked."""

    def __init__(self, token: str, chat_id: str | int, extra: str | list | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.recipients = chat_ids(chat_id, *(extra if isinstance(extra, list) else [extra]))
        self.chat_id = self.recipients[0]  # the owner
        self.client = httpx.Client(timeout=40)
        self._last_send = 0.0

    def _call(self, method: str, _files: dict | None = None, **params) -> dict:
        """One Bot API call. Network errors, 5xx replies and rate limits are retried; anything else raises.
        `_files`: files to upload with it (then the parameters go as form fields)."""
        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                if _files:
                    resp = self.client.post(f"{self.base}/{method}", data=params, files=_files)
                else:
                    resp = self.client.post(f"{self.base}/{method}", json=params)
            except httpx.TransportError as exc:
                if last:
                    raise
                # (the exception's text is left out of the log: the request URL carries the bot token)
                log.warning("Telegram %s: %s, retrying", method, type(exc).__name__)
                time.sleep(2**attempt)
                continue
            try:
                data = resp.json()
            except ValueError:  # an HTML error page from a gateway
                data = {}
            if data.get("ok"):
                return data["result"]
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry_after:
                wait = retry_after + 1
            elif resp.status_code >= 500:
                wait = 2**attempt
            else:
                raise TelegramError(f"Telegram {method} failed: {data.get('description') or f'HTTP {resp.status_code}'}", resp.status_code)
            if not last:
                log.warning("Telegram %s: HTTP %s, retrying in %ss", method, resp.status_code, wait)
                time.sleep(wait)
        raise TelegramError(f"Telegram {method}: too many retries")

    def send(self, text: str, chat_id: str | None = None, banner: str = "") -> None:
        """Send to `chat_id`, or to every recipient when it's None. A recipient who can't be reached
        (e.g. blocked the bot) is logged and skipped; only a failure for the owner is an error."""
        for chat in [str(chat_id)] if chat_id is not None else self.recipients:
            try:
                self._send_one(text, chat, banner)
            except (RuntimeError, httpx.HTTPError):
                if chat == self.chat_id:
                    raise
                log.warning("could not message chat %s", chat, exc_info=True)

    def _send_one(self, text: str, chat_id: str, banner: str = "") -> None:
        wait = self._last_send + 1.1 - time.monotonic()  # stay under 1 msg/s per chat
        if wait > 0:
            time.sleep(wait)
        if banner and self._send_photo(text, chat_id, banner):
            self._last_send = time.monotonic()
            return
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
        try:
            self._call("sendMessage", **params)
        except TelegramError as exc:
            if not exc.unsendable:
                raise
            # The same message would be rejected on every later run too, and block everything behind it:
            # send what it says as plain text instead, in pieces if it's too long.
            log.warning("message rejected (%s); sending as plain text", exc)
            params.pop("parse_mode")
            body = plain(text)
            for i in range(0, len(body), MAX_TEXT):
                self._call("sendMessage", **params | {"text": body[i : i + MAX_TEXT]})
        self._last_send = time.monotonic()

    def _send_photo(self, text: str, chat_id: str, banner: str) -> bool:
        """Send `banner` as a photo with `text` as its caption: the picture fills the top of the bubble.
        False when that can't be done (text too long for a caption, no such file, Telegram refuses it):
        the caller then sends the text alone, so a picture never costs you the message."""
        if len(plain(text)) > MAX_CAPTION:
            return False
        try:
            with open(banner, "rb") as f:
                photo = (banner.rsplit("/", 1)[-1], f.read(), "image/jpeg")
            self._call("sendPhoto", _files={"photo": photo}, chat_id=chat_id, caption=text, parse_mode="HTML")
        except OSError as exc:
            log.warning("banner %s not readable (%s); sending the text alone", banner.rsplit("/", 1)[-1], type(exc).__name__)
            return False
        except TelegramError as exc:
            if exc.status != 400:
                raise
            log.warning("banner not sent (%s); sending the text alone", exc)
            return False
        return True

    def updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])


class DryRun(Messenger):
    """Stand-in used when no bot token is configured: prints messages instead of sending."""

    chat_id = "dry-run"
    recipients = ("dry-run",)

    def send(self, text: str, chat_id: str | None = None, banner: str = "") -> None:
        shown = unescape(re.sub(r"<(?!/?(b|i|u)>)[^>]+>", "", text))
        print(f"\n──── telegram message ({len(text)} chars){' · banner ' + banner.rsplit('/', 1)[-1] if banner else ''} ────\n{shown}")
