# Theatre tracker 🎭

Watches Belgrade theatre and venue sites, and on Telegram it:
- messages you **the moment a theatre releases tickets**: how many dates, which titles, and **when** they appeared,
- around a **known release time** checks every 30 s, so the message comes within a minute,
- lists **every play and date** in that message, each date linked to its tickets, plus a link to the theatre's site,
- remembers **which plays sell out and how fast**, flags them 🔥 the next time their tickets come out, and messages
  you as soon as a fast seller gets new dates (`/hot` lists them),
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
| `narodno` | narodnopozoriste.rs | HTML scrape (+ ticket pages with a login) | **exact seats left** with a Narodno account (`NARODNO_EMAIL` / `NARODNO_PASSWORD`), else on sale / <10% left / sold out |
| `bdp` | bdp.rs (Велика сцена only) | Webflow HTML + Sixtix API | on sale / not yet / sold out |
| `jdp` | jdp.rs | WordPress AJAX API | on sale / <10% left / sold out |
| `sava_centar`, `madlenianum_tickets`, `kolarac` | tickets.rs | JSON API | listed = on sale |
| `madlenianum` | operatheatremadlenianum.com + its ticket shop | HTML scrape (repertoire list + month calendar; the shop's listing with a login) | **tickets left** and shop-only dates with a shop account (`MADLENIANUM_EMAIL` / `MADLENIANUM_PASSWORD`), else dates only |
| `terazije` | pozoristeterazije.com box office | "Kupi kartu" JSON API | on sale / <10% left / sold out / not yet |

Configuration is in `config/`:
- `venues.yaml`: which sources to track and their options (BDP stage filter, JDP months ahead…)
- `watchlist.yaml`: plays you care about (⭐, extra alerts: back in stock, running low, sold out)
- `releases.yaml`: known ticket release times, plus **monthly patterns**. Narodno is set up to put next
  month's tickets on sale at midnight between the 22nd and 23rd: checks every 30 s from 23:50 to 00:45, and
  every minute on the 23rd if nothing appeared at midnight (reminders beforehand can be added with `remind`).
- `venues.yaml` `website:`: the theatre's page linked under each release message.
- `venues.yaml` `look:`: how each theatre's release messages look. Every theatre has its own banner picture at
  the top of the message (its logo in its own colours, from `assets/banners/`) and its own text style: italic or
  bold titles, capitals, a marker in the theatre's colour, an ornament line. A play's dates sit in a quote block
  under its title. `settings.yaml` `messages.banners: false` turns the pictures off.
- `settings.yaml` `selling:`: what counts as a fast seller (default: sold out within 48 h of going on sale, at
  least a day before the show), and whether fast sellers' new dates are messaged right away.
- `settings.yaml`: what is sent on a schedule (`digest`, `release_season.times`: both off by default),
  reminders and burst timing, and `release_season.batch_min`: how many new dates (or sales openings) one check
  needs before it is messaged (default 1: every new date)

Every setting is optional and falls back to the value shipped in `settings.yaml` (one table, `DEFAULTS` in
`src/teatar/config.py`). Mistakes in `config/` are caught when a run starts, before anything is scanned or sent:
a venue without an adapter, a bad time or weekday, an entry missing its `query` or `venue` stop the run with a
message naming the file and the field; a setting or venue option the tracker doesn't know (usually a typo) is
reported in the run's log. Write times in quotes (`"08:00"`).

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
| `/theatres` | every theatre tracked, linked to its repertoire page |
| `/search tramvaj`, or just type a title | find a play (Cyrillic/Latin/diacritics don't matter); shows 🔥/🔴 if it sells out |
| `/hot` | plays that sell out fast: how many dates sold out, fastest and typical time, and their next dates (tap to buy); then plays that sell out more slowly |
| `/soldout` | upcoming dates that are sold out now, and how long each took to sell out |
| `/watch divlje meso` · `/unwatch 1` · `/watchlist` | manage watched plays |
| `/release jdp 1.10. 10:00 oktobar` | known release: reminders at −24h and −1h, then polls every 30 s from −10 min to +45 min |
| `/release narodno 2026-10-05` | date only: polls every 60 s from 08:00 to 22:00 that day |
| `/releases` · `/unrelease 1` · `/status` | |

**What it sends you (and nothing else):**
- **The moment new dates come out or go on sale**, at any theatre, even a single date (`batch_min`, default 1).
  One message per theatre per check, listing **only what's new since the previous check**: how many dates per
  month (and how many that month now has), when they appeared, bracketed by the previous check that didn't see
  them and the one that did (e.g. "between 12:00 and 14:00"), then **every new play and date** (title → the play's
  page, each date → its tickets, with availability), plays that sell out fast first with 🔥, and a 🌐 link to the
  theatre's site. A date is only ever reported once. (Over Sep–Nov that was about 6 messages a week.)
- **New dates for a play that sells out fast** (🔥), even a single one, as soon as they're seen (`selling.alert`).
- Plays on your `/watch` list (back in stock, running low, sold out), and a warning if a site stops responding.

The banner goes out as a photo with the message as its caption. Telegram keeps a caption to 1,024 characters, so a
long release shows the banner with the first plays and continues in a second message. If a picture can't be sent,
the text goes out on its own.

Optional, off by default in `config/settings.yaml`: a weekly or daily overview (`digest.every`), a "tickets for
next month?" status per theatre on the 20th–27th (`release_season.times`), reminders before known releases
(`releases.remind_before`, `remind` in `releases.yaml`), a note when a release window passes with nothing
(`releases.report_misses`), and instant alerts for every new date (`alerts.instant: all`).

## Sell-outs and fast sellers

Every check records, per performance, when it went on sale, ran low and **first** sold out (later "sold out ↔ few
left" flapping from returned tickets is ignored), and keeps that for `selling.history_days` after the show
(`state.sales`, see `src/teatar/sales.py`). A play is a **fast seller** 🔥 when one of its dates sold out within
`selling.fast_hours` of going on sale (default 48 h), or was already sold out the first time it was seen, at least
`selling.min_days_before` before the show. 🔴 marks plays that sell out more slowly.

Where sell-outs can be seen: Narodno, JDP and Terazije (sold out / under 10% left), Madlenianum with a shop account,
BDP (sold-out flag), and the tickets.rs venues ("rasprodato"). Without a shop account, Madlenianum's own site shows no availability. Sales
that close on the day of the show are not counted as sell-outs. Times are only as precise as the gap between checks.

History before this feature was rebuilt from the git history of `data/state.json`:
`teatar backfill-sales` replays every committed snapshot and writes `data/sales_seed.json`, which the next run
imports once. It needs the full history (`git fetch --unshallow` in a shallow clone).

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

4. **More people (optional).** Each person opens the bot and presses **Start**; `teatar whoami` (or the bot's
   `getUpdates`) shows their chat id. Add a secret `TELEGRAM_EXTRA_CHAT_IDS` with their ids, comma-separated.
   They get every notification you get, and can use the commands (replies go to whoever asked). Check with
   *Actions → tracker → Run workflow → test*: everyone gets a message listing the theatres with links.

5. **Narodno seat counts (optional).** Narodno shows how many seats are left only to logged-in users. Add secrets
   `NARODNO_EMAIL` and `NARODNO_PASSWORD` for a narodnopozoriste.rs account. The tracker logs in only when a count
   is due (`seat_counts` in `venues.yaml`): once a day at the first check after 09:00, for every on-sale date, and
   for new dates at the first check 90 minutes or more after they appeared. It reads their ticket pages with GET
   only: no seat is ever selected or held. Other checks don't log in and keep the last counts, shown as `~145` once
   they're a few hours old; on sale / under 10% / sold out still update from the public listing every check. Each
   count is also kept in the date's sell-out history. If the login stops working, you get the usual "failed 3
   checks" warning for "Narodno pozorište seat counts", and everything else keeps working.

6. **Madlenianum's ticket shop (optional).** Their shop lists performances only to signed-in visitors, and it can
   have a date (and its tickets) days before the theatre's public site does. Add a secret `MADLENIANUM_PASSWORD`
   for an account on tickets.operatheatremadlenianum.com, and `MADLENIANUM_EMAIL` unless the account uses the same
   email as `NARODNO_EMAIL`. Every regular check then signs in once and reads the shop's listing page: dates only
   the shop has are tracked like any other, and every date shows how many tickets are left. It opens no ticket or
   reservation page, so nothing is ever selected or held. A date on the public site that the shop doesn't list yet
   counts as "not on sale yet", so you hear when its sale opens. If the sign-in stops working you get the usual
   "failed 3 checks" warning for "Madlenianum ticket shop"; the last dates and counts are kept (shown as `~142`
   once they're a few hours old) and the public pages keep working.

The bot only answers the chat ids in `TELEGRAM_CHAT_ID` and `TELEGRAM_EXTRA_CHAT_IDS`. Keep chat ids in secrets,
not in the repo: it's public.

## Local use

```sh
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/teatar scan                     # live tables for every venue (no state, no messages)
.venv/bin/teatar scan --venue narodno
.venv/bin/teatar run --dry-run            # full run, messages printed instead of sent
.venv/bin/teatar overview                # print the overview to end of next month (--send to send it)
.venv/bin/teatar backfill-sales          # rebuild sell-out history from git into data/sales_seed.json
.venv/bin/pytest
```

The tests also run on GitHub for every push that touches code or config (`.github/workflows/tests.yml`).

## If something goes wrong

- **A site changes or goes down:** its last data is kept, the other theatres carry on, and after 3 failed checks
  you get a warning (and an all-clear when it works again).
- **A run fails halfway** (Telegram unreachable, a timeout): state is saved after each stage that sends messages,
  and written to a temporary file first, so the next run neither repeats what was already sent nor finds half a
  file. A failed run shows up red under *Actions*, and GitHub emails you about it.
- **A command the bot can't handle:** you get a "something went wrong" reply, and the check carries on.
- **`data/state.json` is damaged:** the run stops rather than starting over (which would re-announce everything).
  Restore the last good version: `git checkout <commit> -- data/state.json`.

## Adding sources

- **Another tickets.rs venue:** copy a `tickets_rs` block in `venues.yaml` and set `slug` (the part after `tickets.rs/venue/`).
- **A new site:** add `src/teatar/adapters/<name>.py` with a class decorated `@register("<name>")` that implements `fetch() -> list[Performance]`. Prefer the site's JSON API when it has one, and scrape HTML otherwise. Keep parsing in a pure function and add a fixture test. Then import it in `adapters/__init__.py` and add it to `venues.yaml`.
  `adapters/base.py` has the pieces every adapter needs, so use them instead of writing them again: `upcoming` (drop past dates), `unique` (sources read month by month), `at` / `infer_year` / `find_time` (dates and times), `text_of` (an element's text), `label_status` (sold out / cancelled / running low from a site's label, Cyrillic or Latin), `dated_id` (a stable id for sources without one), and `dates.months_ahead` (which months to ask for). List the venue options your adapter reads in `known_options`. To report a partial problem without failing the venue, set `self.warning` (see Narodno's seat counts).
- **A banner for a new theatre:** add its design to `assets/banners/src/build.py` (and its logo to `logos/` there),
  run `python3 assets/banners/src/build.py && node assets/banners/src/render.js` (needs Playwright), and name the
  new file in the venue's `look: {banner: …}`. Without a `look`, a theatre's messages are plain: bold titles, no picture.
- **A custom check for one play:** give a watchlist entry a `lookup`, e.g. `{sixtix: <event slug>}` (API) or `{page: <url>, pattern: "Kupi"}` (scrape). See `lookups.py`.
