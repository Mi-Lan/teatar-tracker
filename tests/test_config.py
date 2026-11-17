"""config/*.yaml as shipped, and the settings table the code reads them through."""

import logging
from datetime import time

import pytest
import yaml

from teatar import config
from teatar.config import DEFAULTS, Config, ConfigError
from teatar.dates import (
    add_months,
    day_in_month,
    end_of_next_month,
    month_key,
    months_ahead,
    parse_time,
)
from teatar.registry import build_adapters


def cfg_with(settings: dict) -> Config:
    return Config(venues={}, settings=settings, watchlist=[], releases=[], state_path=None)


def test_defaults_are_the_shipped_settings():
    """Deleting a line from settings.yaml must change nothing, and every setting must be documented there."""
    shipped = yaml.safe_load((config.ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert set(shipped) == set(DEFAULTS)
    for section, values in DEFAULTS.items():
        assert set(shipped[section]) == set(values), section
        for key, default in values.items():
            value = shipped[section][key]
            assert ("off" if value is False and default == "off" else value) == default, f"{section}.{key}"
    assert config.unknown_settings(shipped) == []


def test_settings_fall_back_to_defaults_and_typos_are_noticed():
    cfg = cfg_with({"digest": {"every": "daily", "tiem": "07:00"}, "releases": None, "digset": {}})
    assert cfg.get("digest", "every") == "daily" and cfg.get("digest", "time") == "08:00"
    assert cfg.get("releases", "poll_seconds") == 30  # an empty section is fine
    assert config.unknown_settings(cfg.settings) == ["digest.tiem", "digset"]
    with pytest.raises(KeyError):
        cfg.get("digest", "no_such_setting")  # a name the code made up: fail in tests, not silently in production


def test_flags_read_yaml_booleans_as_off():
    assert cfg_with({"digest": {"every": False}}).flag("digest", "every", "weekly", "daily") == "off"  # `every: off`
    assert cfg_with({"digest": {"every": "Weekly"}}).flag("digest", "every", "weekly", "daily") == "weekly"
    with pytest.raises(ConfigError, match="digest.every"):
        cfg_with({"digest": {"every": "monthly"}}).flag("digest", "every", "weekly", "daily")
    with pytest.raises(ConfigError, match="digest.every"):
        config.check_settings(cfg_with({"digest": {"every": "monthly"}}))
    with pytest.raises(ConfigError, match="bad time"):
        config.check_settings(cfg_with({"release_season": {"times": ["8 o'clock"]}}))


def test_times_survive_yaml():
    assert parse_time("08:00") == time(8, 0) == parse_time(time(8, 0))
    assert yaml.safe_load("t: 8:05")["t"] == 485  # an unquoted time is a base-60 number to YAML…
    assert parse_time(485) == time(8, 5)  # …and is still read as 08:05
    for bad in ("25:00", "soon", 1440, True, None):
        with pytest.raises(ValueError):
            parse_time(bad)


def test_month_arithmetic():
    from datetime import date

    assert add_months(date(2026, 11, 16), 2) == (2027, 1)
    assert months_ahead(2, date(2026, 12, 31)) == [(2026, 12), (2027, 1), (2027, 2)]
    assert day_in_month(2027, 2, 31) == date(2027, 2, 28)
    assert end_of_next_month(date(2026, 12, 5)) == date(2027, 1, 31)
    assert month_key(date(2027, 1, 3)) == "2027-01"


def test_shipped_config_loads_and_every_venue_builds(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = config.load()
        adapters = build_adapters(cfg.venues, http=None)
    assert {a.key for a in adapters} == {k for k, v in cfg.venues.items() if v.get("enabled", True)}
    assert caplog.records == []  # no unknown settings, no unknown venue options


def test_venue_mistakes_are_explained(caplog):
    with pytest.raises(ConfigError, match="unknown adapter 'tickets'"):
        build_adapters({"x": {"adapter": "tickets"}}, http=None)
    with pytest.raises(ConfigError, match="needs an `adapter:`"):
        build_adapters({"x": {"name": "X"}}, http=None)
    with pytest.raises(ConfigError, match="slug"):
        build_adapters({"x": {"adapter": "tickets_rs"}}, http=None)  # a required option is missing
    with pytest.raises(ConfigError, match="unknown venue nardno"):
        build_adapters({"narodno": {"adapter": "narodno_html"}}, http=None, only=["nardno"])
    with caplog.at_level(logging.WARNING):
        (a,) = build_adapters({"jdp": {"adapter": "jdp_wp_ajax", "website": "https://x", "month_ahead": 3}}, http=None)
    assert a.options == {"month_ahead": 3} and "unknown option(s) month_ahead" in caplog.text
    assert build_adapters({"jdp": {"adapter": "jdp_wp_ajax", "enabled": False}}, http=None) == []


def test_broken_yaml_is_a_config_error(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "venues.yaml").write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="venues.yaml must be a mapping"):
        config.load(tmp_path)
    (tmp_path / "config" / "venues.yaml").write_text("jdp:\n  name: JDP\n")
    with pytest.raises(ConfigError, match="needs an `adapter:`"):
        config.load(tmp_path)
    (tmp_path / "config" / "venues.yaml").write_text("jdp:\n  adapter: jdp_wp_ajax\n")
    (tmp_path / "config" / "watchlist.yaml").write_text("watch:\n  - venue: jdp\n")
    with pytest.raises(ConfigError, match="needs a `query:`"):
        config.load(tmp_path)
