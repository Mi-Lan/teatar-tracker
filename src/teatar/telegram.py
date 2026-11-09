"""Minimal Telegram Bot API client (sendMessage + getUpdates polling)."""

from __future__ import annotations

import logging
import re
import time
from html import unescape

import httpx

log = logging.getLogger(__name__)


def chat_ids(*values: str | int | None) -> list[str]:
    """Chat ids from one or more values, each a single id or a comma/space-separated list; in order, no repeats."""
    out: list[str] = []
    for v in values:
        for part in re.split(r"[,\s]+", str(v or "").strip()):
            if part and part not in out:
                out.append(part)
    return out


class Telegram:
    """Every notification goes to all `recipients` (TELEGRAM_CHAT_ID plus TELEGRAM_EXTRA_CHAT_IDS);
    any of them can send commands, and the reply goes to whoever asked."""

    def __init__(self, token: str, chat_id: str | int, extra: str | list | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.recipients = chat_ids(chat_id, *(extra if isinstance(extra, list) else [extra]))
        self.chat_id = self.recipients[0]  # the owner
        self.client = httpx.Client(timeout=40)
        self._last_send = 0.0

    def _call(self, method: str, **params) -> dict:
        for attempt in range(4):
            resp = self.client.post(f"{self.base}/{method}", json=params)
            data = resp.json()
            if data.get("ok"):
                return data["result"]
            retry = (data.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry:
                time.sleep(retry + 1)
                continue
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        raise RuntimeError(f"Telegram {method}: too many retries")

    def send(self, text: str, chat_id: str | None = None) -> None:
        """Send to `chat_id`, or to every recipient when it's None. A recipient who can't be reached
        (e.g. blocked the bot) is logged and skipped; only a failure for the owner is an error."""
        for chat in [str(chat_id)] if chat_id is not None else self.recipients:
            try:
                self._send_one(text, chat)
            except (RuntimeError, httpx.HTTPError):
                if chat == self.chat_id:
                    raise
                log.warning("could not message chat %s", chat, exc_info=True)

    def _send_one(self, text: str, chat_id: str) -> None:
        wait = self._last_send + 1.1 - time.monotonic()  # stay under 1 msg/s per chat
        if wait > 0:
            time.sleep(wait)
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            self._call("sendMessage", **params)
        except RuntimeError as exc:
            if "parse" not in str(exc).lower() and "entities" not in str(exc).lower():
                raise
            log.warning("HTML rejected (%s); sending as plain text", exc)
            params.pop("parse_mode")
            params["text"] = unescape(re.sub(r"<[^>]+>", "", text))
            self._call("sendMessage", **params)
        self._last_send = time.monotonic()

    def send_all(self, messages: list[str], chat_id: str | None = None) -> None:
        for m in messages:
            self.send(m, chat_id)

    def updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])


class DryRun:
    """Stand-in used when no bot token is configured: prints messages instead of sending."""

    chat_id = "dry-run"
    recipients = ["dry-run"]

    def send(self, text: str, chat_id: str | None = None) -> None:
        plain = unescape(re.sub(r"<(?!/?(b|i|u)>)[^>]+>", "", text))
        print(f"\n──── telegram message ({len(text)} chars) ────\n{plain}")

    def send_all(self, messages: list[str], chat_id: str | None = None) -> None:
        for m in messages:
            self.send(m, chat_id)

    def updates(self, offset: int) -> list[dict]:
        return []
