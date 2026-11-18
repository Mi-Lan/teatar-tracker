"""Temporary: send one sample release message per theatre look to the bot's owner, to see the banners and
text styles in the real Telegram apps.

Run by .github/workflows/banner-test.yml. Sends to TELEGRAM_CHAT_ID only (never the extra recipients).
The messages are built by the tracker's own code from data/state.json; only the header says "Test".
The log of a public repo is public: nothing but counts and Telegram's ok/error answer is printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from teatar import config, sales, season  # noqa: E402
from teatar.dates import month_key  # noqa: E402
from teatar.diff import Change, Kind  # noqa: E402
from teatar.models import now  # noqa: E402
from teatar.runner import Ctx  # noqa: E402
from teatar.state import State  # noqa: E402
from teatar.telegram import Telegram, chat_ids  # noqa: E402

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
owner = (chat_ids(os.environ.get("TELEGRAM_CHAT_ID")) or [""])[0]  # the first id only: the owner
if not token or not owner:
    sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")

cfg = config.load(ROOT)
# the banners of this very commit (they are not on main yet)
cfg.settings.setdefault("messages", {})["banner_url"] = (
    f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/{os.environ['GITHUB_SHA']}/assets/banners/{{name}}.jpg"
)
state = State.load(cfg.state_path)
tg = Telegram(token, owner)  # no extra recipients
ctx = Ctx(cfg=cfg, state=state, http=None, tg=tg, adapters={})
fast = sales.fast_plays(state, cfg)

venues = [v for v in cfg.venues if v != "madlenianum_tickets"]  # (same look as madlenianum)
failed = 0
for i, venue in enumerate(venues, 1):
    upcoming = sorted((p for p in state.catalog() if p.venue == venue and p.start > now()), key=lambda p: p.start)
    plays = sorted(sales.by_play(upcoming).values(), key=lambda ps: (-len(ps), ps[0].start))[:3]
    perfs = [p for ps in plays for p in ps[:4]]
    if not perfs:
        print(f"{venue}: nothing upcoming, skipped")
        continue
    entries = []
    for month in sorted({month_key(p.start) for p in perfs}):
        counts = season._counts(season._month_perfs(ctx, venue, month))
        new = sum(month_key(p.start) == month for p in perfs)
        entries.append({"venue": venue, "month": month, "after": None, "by": now().isoformat(timespec="seconds"), "new": new, "opened": 0,
                        "on_sale": sum(p.status.buyable for p in perfs if month_key(p.start) == month),
                        "listed": counts["listed"], "buyable": counts["buyable"], "unknown": counts["unknown"]})
    messages = season.release_message(ctx, venue, entries, [Change(Kind.NEW_DATE, p) for p in perfs], fast)
    messages[0] = f"🧪 <b>Test {i}/{len(venues)}</b>: sample of the new look, not a real release\n" + messages[0]
    try:
        tg.send_all(messages, banner=cfg.banner_url(venue))
        print(f"{venue}: sent {len(messages)} message(s), {len(perfs)} dates, banner {cfg.venue_look(venue).banner}")
    except Exception as exc:  # (only the kind and Telegram's own description: no addresses)
        failed += 1
        print(f"{venue}: FAILED {type(exc).__name__}: {str(exc)[:200] if type(exc).__name__ == 'TelegramError' else ''}")
sys.exit(1 if failed else 0)
