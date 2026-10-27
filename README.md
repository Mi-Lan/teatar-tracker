# Theatre tracker 🎭

Watches Belgrade theatre and venue sites, and on Telegram it:
- messages you **the moment a theatre releases tickets**: how many dates, which titles, and **when** they appeared,
- around a **known release time** checks every 30 s, so the message comes within a minute,
- on demand shows **what came out recently** (`/new`) and **what's playing, by date and theatre, with availability**.

By default that release message is the only thing it sends (plus plays you `/watch`); the weekly overview,
the 20th–27th status, reminders and "nothing yet" notes are off in `config/settings.yaml` and can be turned on.

It checks every 15 minutes on GitHub Actions (free), silently: you only hear from it when it matters. GitHub
delays and skips scheduled runs freely (an hourly schedule ran only every 3–6 hours in practice), so the gap
between checks varies; that gap is how precisely "when did the tickets come out" can be answered. State lives
in `data/state.json`, committed by the workflow.

## Sources

| Venue key | Site | Method | Availability |
|---|---|---|---|
| `narodno` | narodnopozoriste.rs | HTML scrape | **exact tickets left** |
| `bdp` | bdp.rs (Велика сцена only) | Webflow HTML + Sixtix API | on sale / not yet / sold out |
| `jdp` | jdp.rs | WordPress AJAX API | on sale / <10% left / sold out |
| `sava_centar`, `madlenianum_tickets`, `kolarac` | tickets.rs | JSON API | listed = on sale |
| `madlenianum` | operatheatremadlenianum.com | HTML scrape | dates only (shop needs login) |
| `terazije` | pozoristeterazije.com box office | "Kupi kartu" JSON API | on sale / <10% left / sold out / not yet |

Configuration is in `config/`:
- `venues.yaml`: which sources to track and their options (BDP stage filter, JDP months ahead…)
- `watchlist.yaml`: plays you care about (⭐, extra alerts: back in stock, running low, sold out)
- `releases.yaml`: known ticket release times, plus **monthly patterns**. Narodno is set up to put next
  month's tickets on sale at midnight between the 22nd and 23rd: checks every 30 s from 23:50 to 00:45, and
  every minute on the 23rd if nothing appeared at midnight (reminders beforehand can be added with `remind`).
- `settings.yaml`: what is sent on a schedule (`digest`, `release_season.times`: both off by default),
  reminders and burst timing, and `release_season.batch_min`: how many new dates (or sales openings) in one
  check count as a release worth a message (default 5; around a known release, any new ticket is)

## Telegram commands

Replies come on the next check (within the hour; faster during release bursts).

| Command | |
|---|---|
| `/new`, `/new 30` | what came out in the last 7 (or 30) days and when: per day and theatre, how many dates appeared (by month), how many went on sale, the titles, and the two checks the release fell between |
| `/month` | everything until the end of next month, by date → theatre, with tickets left (tap a title to buy) |
| `/tickets` | is next month out yet, per theatre, and when each released |
| `/today` `/tomorrow` `/week` | shorter ranges |
| `/overview` | everything announced |
| `/theatre jdp` | one theatre |
| `/search tramvaj`, or just type a title | find a play (Cyrillic/Latin/diacritics don't matter) |
| `/watch divlje meso` · `/unwatch 1` · `/watchlist` | manage watched plays |
| `/release jdp 1.10. 10:00 oktobar` | known release: reminders at −24h and −1h, then polls every 30 s from −10 min to +45 min |
| `/release narodno 2026-10-05` | date only: polls every 60 s from 08:00 to 22:00 that day |
| `/releases` · `/unrelease 1` · `/status` | |

**What it sends you (and nothing else):**
- **The moment tickets come out:** when a theatre's next month gets its first tickets, when any theatre puts out a
  batch of new dates / opens sales for many dates in one check (`batch_min`, default 5), or when any new ticket
  appears at a theatre with a known release around now. One message per theatre per day: how many dates per
  month, how many on sale, the titles, and when they appeared, bracketed by the previous check that didn't see
  them and the one that did (e.g. "between 12:00 and 17:02"). Single new dates elsewhere are not messaged; they
  show up in `/new`.
- Plays on your `/watch` list (back in stock, running low, sold out), and a warning if a site stops responding.

Optional, off by default in `config/settings.yaml`: a weekly or daily overview (`digest.every`), a "tickets for
next month?" status per theatre on the 20th–27th (`release_season.times`), reminders before known releases
(`releases.remind_before`, `remind` in `releases.yaml`), a note when a release window passes with nothing
(`releases.report_misses`), and instant alerts for every new date (`alerts.instant: all`).

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
