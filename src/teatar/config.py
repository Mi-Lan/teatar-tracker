from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml

from .dates import WEEKDAYS, parse_time

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

# Every setting the code reads, with the value used when config/settings.yaml leaves it out. This is the
# one place defaults live (Config.get has no per-call fallback), and it matches the shipped settings.yaml:
# deleting a line there changes nothing. tests/test_config.py checks the two against each other.
DEFAULTS: dict[str, dict] = {
    "digest": {"every": "off", "weekday": "monday", "time": "08:00", "until": "next_month", "days": 7},
    "release_season": {"days": [20, 27], "times": [], "batch_min": 1},
    "alerts": {"instant": "releases_and_watchlist", "low_threshold": 20},
    "selling": {"fast_hours": 48, "min_days_before": 1, "alert": True, "history_days": 180},
    "releases": {
        "remind_before": [],
        "report_misses": False,
        "lookahead_minutes": 150,
        "start_before_minutes": 10,
        "stop_after_minutes": 45,
        "poll_seconds": 30,
        "full_scan_minutes": 10,
        "all_day_window": ["08:00", "22:00"],
        "all_day_poll_seconds": 60,
        "max_run_minutes": 200,
    },
    "health": {"failures_before_alert": 3},
}


class ConfigError(ValueError):
    """A problem in config/*.yaml, worded for whoever edits those files."""


def parse_duration(text: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([dhm])\s*", str(text))
    if not m:
        raise ValueError(f"bad duration {text!r} (use e.g. 24h, 90m, 2d)")
    n, unit = int(m.group(1)), m.group(2)
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]


@dataclass
class Config:
    venues: dict
    settings: dict
    watchlist: list[dict]
    releases: list[dict]
    state_path: Path
    recurring: list[dict] = field(default_factory=list)

    def venue_name(self, key: str) -> str:
        return (self.venues.get(key) or {}).get("name", key)

    def venue_website(self, key: str) -> str:
        """The theatre's own repertoire page: `website` in venues.yaml, else a tickets.rs venue page."""
        v = self.venues.get(key) or {}
        if v.get("website"):
            return v["website"]
        if v.get("adapter") == "tickets_rs" and v.get("slug"):
            return f"https://tickets.rs/venue/{v['slug']}"
        return v.get("site_url", "")

    def get(self, section: str, key: str):
        """A setting from settings.yaml, or its default from DEFAULTS (a key in neither is a bug)."""
        value = (self.settings.get(section) or {}).get(key)
        return DEFAULTS[section][key] if value is None else value

    def flag(self, section: str, key: str, *choices: str) -> str:
        """A setting that is one of a few words, lowercased. YAML reads a bare `off`/`no` as false,
        so anything off-like comes back as "off"."""
        value = str(self.get(section, key)).strip().lower()
        if value in ("off", "false", "no", "none", "never"):
            return "off"
        if choices and value not in choices:
            raise ConfigError(f"{section}.{key}: {value!r} is not one of {', '.join(choices)}")
        return value


def _yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a mapping (key: value), not {type(data).__name__}")
    return data


def _list(data: dict, key: str, path: str) -> list[dict]:
    items = data.get(key) or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ConfigError(f"{path} `{key}:` must be a list of entries (- key: value)")
    return items


def unknown_settings(settings: dict) -> list[str]:
    """Names in settings.yaml the code doesn't read (usually a typo, which would silently do nothing)."""
    out = []
    for section, values in settings.items():
        if section not in DEFAULTS:
            out.append(str(section))
        elif isinstance(values, dict):
            out += [f"{section}.{k}" for k in values if k not in DEFAULTS[section]]
    return out


def check_venues(venues: dict) -> None:
    for key, v in venues.items():
        if not isinstance(v, dict) or not v.get("adapter"):
            raise ConfigError(f"venues.yaml: venue {key!r} needs an `adapter:` (see the list at the top of the file)")


def check_entries(cfg: Config) -> None:
    """Fields the run would otherwise trip over halfway through, after messages have gone out."""
    for w in cfg.watchlist:
        if not str(w.get("query") or "").strip():
            raise ConfigError("watchlist.yaml: every `watch:` entry needs a `query:`")
    for r in cfg.releases:
        if not r.get("venue") or not r.get("at"):
            raise ConfigError("releases.yaml: every `releases:` entry needs `venue:` and `at:`")
    for r in cfg.recurring:
        if not r.get("venue") or not r.get("day"):
            raise ConfigError("releases.yaml: every `recurring:` entry needs `venue:` and `day:`")


def check_settings(cfg: Config) -> None:
    """Read every setting that has a fixed form once, so a bad value stops the run before it starts."""
    try:
        cfg.flag("digest", "every", "weekly", "daily")
        cfg.flag("digest", "weekday", *WEEKDAYS)
        cfg.flag("digest", "until", "next_month", "days")
        cfg.flag("alerts", "instant", "releases_and_watchlist", "all")
        for value in [cfg.get("digest", "time"), *cfg.get("release_season", "times"), *cfg.get("releases", "all_day_window")]:
            parse_time(value)
        for value in cfg.get("releases", "remind_before"):
            parse_duration(value)
        first, last = cfg.get("release_season", "days")
        start, end = cfg.get("releases", "all_day_window")
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"settings.yaml: {exc}") from exc


def load(root: Path = ROOT) -> Config:
    cfg_dir = root / "config"
    releases = _yaml(cfg_dir / "releases.yaml")
    cfg = Config(
        venues=_yaml(cfg_dir / "venues.yaml"),
        settings=_yaml(cfg_dir / "settings.yaml"),
        watchlist=_list(_yaml(cfg_dir / "watchlist.yaml"), "watch", "watchlist.yaml"),
        releases=_list(releases, "releases", "releases.yaml"),
        state_path=root / "data" / "state.json",
        recurring=_list(releases, "recurring", "releases.yaml"),
    )
    check_venues(cfg.venues)
    check_entries(cfg)
    check_settings(cfg)
    for name in unknown_settings(cfg.settings):
        log.warning("settings.yaml: unknown setting %r is ignored (typo?)", name)
    return cfg
