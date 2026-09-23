"""Minimal Telegram Bot API client (sendMessage + getUpdates polling)."""

from __future__ import annotations

import logging
import re
import time
from html import unescape

import httpx

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, chat_id: str | int):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
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
        wait = self._last_send + 1.1 - time.monotonic()  # stay under 1 msg/s per chat
        if wait > 0:
            time.sleep(wait)
        params = {"chat_id": chat_id or self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
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

    def send(self, text: str, chat_id: str | None = None) -> None:
        plain = unescape(re.sub(r"<(?!/?(b|i|u)>)[^>]+>", "", text))
        print(f"\n──── telegram message ({len(text)} chars) ────\n{plain}")

    def send_all(self, messages: list[str], chat_id: str | None = None) -> None:
        for m in messages:
            self.send(m, chat_id)

    def updates(self, offset: int) -> list[dict]:
        return []
