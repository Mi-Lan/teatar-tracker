# Theatre tracker 🎭

Watches Belgrade theatre and venue sites, and on Telegram it:
- tells you about **new shows and dates**,
- shows **what's playing, by date and theatre, with availability**,
- catches **ticket releases**: a reminder before, then checks every 30 s around the release time.

It checks hourly on GitHub Actions (free), silently: you only hear from it when it matters. State lives in
`data/state.json`, committed by the workflow.

## Sources

| Venue key | Site | Method | Availability |
|---|---|---|---|
| `narodno` | narodnopozoriste.rs | HTML scrape | **exact tickets left** |
| `bdp` | bdp.rs (Велика сцена only) | Webflow HTML + Sixtix API | on sale / not yet / sold out |
| `jdp` | jdp.rs | WordPress AJAX API | on sale / <10% left / sold out |
| `sava_centar`, `madlenianum_tickets`, `kolarac` | tickets.rs | JSON API | listed = on sale |
| `madlenianum` | operatheatremadlenianum.com | HTML scrape | dates only (shop needs login) |

Configuration is in `config/`:
- `venues.yaml`: which sources to track and their options (BDP stage filter, JDP months ahead…)
- `watchlist.yaml`: plays you care about (⭐, extra alerts: back in stock, running low, sold out)
- `releases.yaml`: known ticket release times, plus **monthly patterns**. Narodno is set up to put next
  month's tickets on sale at midnight between the 22nd and 23rd: reminders at 09:00 on the 21st and 22nd,
  checks every 30 s from 23:50 to 00:45, and every minute on the 23rd if nothing appeared at midnight.
- `settings.yaml`: digest time, reminder times, burst timing

## Telegram commands

Replies come on the next check (within the hour; faster during release bursts).

| Command | |
|---|---|
| `/month` | everything until the end of next month, by date → theatre, with tickets left (tap a title to buy) |
| `/today` `/tomorrow` `/week` | shorter ranges |
| `/overview` | everything announced |
| `/theatre jdp` | one theatre |
| `/search tramvaj`, or just type a title | find a play (Cyrillic/Latin/diacritics don't matter) |
| `/watch divlje meso` · `/unwatch 1` · `/watchlist` | manage watched plays |
| `/release jdp 1.10. 10:00 oktobar` | known release: reminders at −24h and −1h, then polls every 30 s from −10 min to +45 min |
| `/release narodno 2026-10-05` | date only: polls every 60 s from 08:00 to 22:00 that day |
| `/releases` · `/unrelease 1` · `/status` | |

**What it sends you (and nothing else):**
- **Weekly overview, Monday 09:00:** everything on until the end of next month, by date and theatre, with tickets left.
- **Right away:** known ticket releases (reminders, the moment tickets appear), plays on your `/watch` list, and a warning if a site stops responding.

Change the day/time or switch to instant alerts for everything in `config/settings.yaml` (`digest`, `alerts.instant`).

## Setup

1. **Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token. Open your new bot and press **Start**.
2. **Find your chat id:**
   ```sh
   export TELEGRAM_BOT_TOKEN=123:abc
   .venv/bin/teatar whoami          # prints TELEGRAM_CHAT_ID=...
   export TELEGRAM_CHAT_ID=...
   .venv/bin/teatar telegram-test   # you should get a message
   ```
3. **GitHub:** push this repo (public = unlimited free Actions minutes). Under *Settings → Secrets and variables → Actions*, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Then under *Actions → tracker → Run workflow*, start the first run. It saves a baseline and sends you the week ahead.

The bot only answers the chat id in `TELEGRAM_CHAT_ID`.

## Local use

```sh
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/teatar scan                     # live tables for every venue (no state, no messages)
.venv/bin/teatar scan --venue narodno
.venv/bin/teatar run --dry-run            # full run, messages printed instead of sent
.venv/bin/teatar overview                # print the overview to end of next month (--send to send it)
.venv/bin/pytest
```

## Adding sources

- **Another tickets.rs venue:** copy a `tickets_rs` block in `venues.yaml` and set `slug` (the part after `tickets.rs/venue/`).
- **A new site:** add `src/teatar/adapters/<name>.py` with a class decorated `@register("<name>")` that implements `fetch() -> list[Performance]`. Prefer the site's JSON API when it has one, and scrape HTML otherwise. Keep parsing in a pure function and add a fixture test. Then import it in `adapters/__init__.py` and add it to `venues.yaml`.
- **A custom check for one play:** give a watchlist entry a `lookup`, e.g. `{sixtix: <event slug>}` (API) or `{page: <url>, pattern: "Kupi"}` (scrape). See `lookups.py`.
