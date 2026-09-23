from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


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

    def venue_name(self, key: str) -> str:
        return (self.venues.get(key) or {}).get("name", key)

    def get(self, section: str, key: str, default=None):
        return (self.settings.get(section) or {}).get(key, default)


def _yaml(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def load(root: Path = ROOT) -> Config:
    cfg_dir = root / "config"
    return Config(
        venues=_yaml(cfg_dir / "venues.yaml"),
        settings=_yaml(cfg_dir / "settings.yaml"),
        watchlist=_yaml(cfg_dir / "watchlist.yaml").get("watch") or [],
        releases=_yaml(cfg_dir / "releases.yaml").get("releases") or [],
        state_path=root / "data" / "state.json",
    )
