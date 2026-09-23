from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import config, format as fmt
from .http import Http
from .models import Status
from .registry import build_adapters
from .runner import Ctx, run, scan
from .state import State
from .telegram import DryRun, Telegram

STATUS_STYLE = {
    Status.ON_SALE: "green", Status.LOW: "yellow", Status.SOLD_OUT: "red",
    Status.NOT_ON_SALE: "cyan", Status.CANCELLED: "magenta", Status.UNKNOWN: "dim",
}


def load_dotenv(path: Path = config.ROOT / ".env") -> None:
    """Read KEY=value lines from .env (gitignored) without overriding real environment variables."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip().removeprefix("export "), value.strip().strip("'\""))


def make_tg(dry_run: bool):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if dry_run or not token or not chat:
        if not dry_run:
            logging.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set: printing messages instead of sending")
        return DryRun()
    return Telegram(token, chat)


def make_ctx(args, tg) -> Ctx:
    cfg = config.load()
    if getattr(args, "state", None):
        cfg.state_path = Path(args.state)
    http = Http()
    adapters = {a.key: a for a in build_adapters(cfg.venues, http, only=getattr(args, "venue", None))}
    return Ctx(cfg=cfg, state=State.load(cfg.state_path), http=http, tg=tg, adapters=adapters)


def cmd_scan(args) -> None:
    """Live scan without touching state; prints a table per venue."""
    ctx = make_ctx(args, DryRun())
    console = Console()
    for key, perfs in scan(ctx, include_custom=False).items():
        table = Table(title=f"{ctx.cfg.venue_name(key)} — {len(perfs)} performances", title_justify="left")
        for col in ("When", "Title", "Stage", "Status", "Left", "Link"):
            table.add_column(col, overflow="fold")
        for p in perfs[: args.limit or None]:
            table.add_row(
                fmt.when(p), p.title, p.stage, f"[{STATUS_STYLE[p.status]}]{p.status}[/]",
                "" if p.available is None else str(p.available), p.buy_url or p.url,
            )
        console.print(table)


def cmd_run(args) -> None:
    ctx = make_ctx(args, make_tg(args.dry_run))
    run(ctx, allow_burst=not args.no_burst)


def cmd_overview(args) -> None:
    """Scan now and send (or print) the overview: to the end of next month, N days, or everything."""
    from datetime import timedelta

    from .diff import apply
    from .models import now
    from .runner import end_of_next_month, send_overview

    ctx = make_ctx(args, make_tg(not args.send))
    apply(ctx.state, scan(ctx))  # in-memory only, not saved
    today = now().date()
    if args.all:
        send_overview(ctx, None, "Everything announced")
    elif args.days:
        send_overview(ctx, today + timedelta(days=args.days - 1), f"Next {args.days} days")
    else:
        end = end_of_next_month(today)
        send_overview(ctx, end, f"Until {end:%d.%m.} (end of next month)")


def cmd_telegram_test(args) -> None:
    tg = make_tg(False)
    if isinstance(tg, DryRun):
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first (see README).")
    tg.send("✅ <b>Theatre tracker</b> can reach you. Send /help to see what I can do.")
    print("sent")


def cmd_whoami(args) -> None:
    """Print chats that recently messaged the bot, to find your TELEGRAM_CHAT_ID."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN first.")
    chats = {}
    for u in Telegram(token, "0").updates(0):
        chat = (u.get("message") or {}).get("chat") or {}
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title")
    if not chats:
        print("No messages yet: open your bot in Telegram, press Start (or send any message), then run this again.")
    for cid, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({name})")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    parser = argparse.ArgumentParser(prog="teatar", description="Belgrade theatre tracker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="live scan, print tables (no state, no messages)")
    p.add_argument("--venue", action="append", help="only this venue key (repeatable)")
    p.add_argument("--limit", type=int, default=0, help="rows per venue (0 = all)")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("run", help="one tracker run (what CI executes)")
    p.add_argument("--dry-run", action="store_true", help="print Telegram messages instead of sending")
    p.add_argument("--no-burst", action="store_true", help="skip release burst polling")
    p.add_argument("--state", help="state file path (default data/state.json)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("overview", help="scan and show the overview")
    p.add_argument("--days", type=int, default=0, help="days ahead (default: until the end of next month)")
    p.add_argument("--all", action="store_true", help="everything announced")
    p.add_argument("--send", action="store_true", help="send to Telegram instead of printing")
    p.set_defaults(fn=cmd_overview)

    sub.add_parser("telegram-test", help="send a test message").set_defaults(fn=cmd_telegram_test)
    sub.add_parser("whoami", help="find your Telegram chat id").set_defaults(fn=cmd_whoami)

    args = parser.parse_args(argv)
    args.fn(args)
