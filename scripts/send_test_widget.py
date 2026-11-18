"""Temporary: send the framed-playbill widget to the bot's owner, to see how Telegram really draws it.

Run by .github/workflows/widget-test.yml. Sends to TELEGRAM_CHAT_ID only (never the extra recipients).
The log of a public repo is public: nothing but Telegram's ok/error answer and sizes is printed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from teatar import sales, widget  # noqa: E402
from teatar.models import Performance  # noqa: E402
from teatar.telegram import chat_ids  # noqa: E402

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
owner = (chat_ids(os.environ.get("TELEGRAM_CHAT_ID")) or [""])[0]  # the first id only: the owner
if not token or not owner:
    sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")

state = json.loads((Path(__file__).resolve().parents[1] / "data" / "state.json").read_text(encoding="utf-8"))
december = sorted(
    (Performance.from_dict(d) for d in state["performances"].values() if d["venue"] == "madlenianum" and d["start"][:7] == "2026-12"),
    key=lambda p: p.start,
)
on_sale = [p for p in december if p.status.buyable]
not_yet = [p for p in december if not p.status.buyable]
site = "https://operatheatremadlenianum.com/repertoar"
tests = [
    ("Tickets for December are out", f"{len(on_sale)} dates · test of the new look", on_sale,
     "🧪 Test · 🎟 <b>Madlenianum</b> · tickets for <b>December</b> are out\n/theatre madlenianum · /new"),
    ("New dates listed, not on sale yet", f"{len(not_yet)} dates · test of the new look", not_yet,
     "🧪 Test · 📋 <b>Madlenianum</b> · new dates listed, tickets not on sale yet\n/theatre madlenianum · /new"),
]

failed = False
with httpx.Client(timeout=40) as client:
    # Widgets are only allowed in some kinds of chat: say which kind this one is (nothing else about it).
    try:
        chat = client.post(f"https://api.telegram.org/bot{token}/getChat", json={"chat_id": owner}).json()
        kind = chat.get("result", {}).get("type") if chat.get("ok") else f"unknown ({chat.get('description')})"
    except Exception as exc:
        kind = f"unknown ({type(exc).__name__})"
    print("the owner's chat is a:", kind, "| id is negative (a group or channel):", owner.startswith("-"))
    me = client.post(f"https://api.telegram.org/bot{token}/getMe").json().get("result", {})
    print("bot:", {k: me.get(k) for k in ("is_bot", "can_join_groups", "supports_inline_queries", "has_main_web_app")})
    for headline, note, perfs, caption in tests:
        if not perfs:
            print(f"skip: no dates for {headline!r}")
            continue
        html, rows = widget.release_widget("Madlenianum", headline, note, list(sales.by_play(perfs).values()), site)
        print(f"{headline!r}: {rows} rows, {len(html.encode())} bytes, {widget.compressed_size(html)} compressed, fits={widget.fits(html, rows)}")
        try:
            reply = client.post(
                f"https://api.telegram.org/bot{token}/sendWidget",
                json={"chat_id": owner, "html": html, "caption": caption, "parse_mode": "HTML"},
            ).json()
        except Exception as exc:  # (the exception text is left out: the URL carries the token)
            print("  request failed:", type(exc).__name__)
            failed = True
            continue
        if reply.get("ok"):
            message = reply.get("result", {})
            print("  sent: message", message.get("message_id"), "| keys in the answer:", sorted(k for k in message if k not in ("chat", "from")))
        else:
            print("  refused:", reply.get("error_code"), reply.get("description"))
            failed = True
sys.exit(1 if failed else 0)
