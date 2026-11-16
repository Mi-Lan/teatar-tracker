"""Adapter registry. Each adapter module registers itself with @register("<name>")."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import ConfigError

if TYPE_CHECKING:
    from .adapters.base import Adapter
    from .http import Http

ADAPTERS: dict[str, type[Adapter]] = {}
# venues.yaml keys that aren't passed to the adapter: `enabled` switches the venue off, `website` is the
# link shown in messages (Config.venue_website).
NOT_FOR_ADAPTER = ("enabled", "website")


def register(name: str):
    def deco(cls):
        cls.adapter_name = name
        ADAPTERS[name] = cls
        return cls

    return deco


def build_adapters(venues: dict[str, dict], http: Http, only: list[str] | None = None) -> list[Adapter]:
    """Instantiate one adapter per enabled venue in config/venues.yaml."""
    from . import adapters  # noqa: F401  (imports all adapter modules so they register)

    if unknown := sorted(set(only or []) - set(venues)):
        raise ConfigError(f"unknown venue {', '.join(unknown)} (known: {', '.join(venues)})")
    built = []
    for key, cfg in venues.items():
        if only and key not in only:
            continue
        if not isinstance(cfg, dict) or not cfg.get("adapter"):
            raise ConfigError(f"venues.yaml: venue {key!r} needs an `adapter:`")
        if not cfg.get("enabled", True):
            continue
        name = cfg["adapter"]
        if name not in ADAPTERS:
            raise ConfigError(f"venues.yaml: venue {key!r} has unknown adapter {name!r} (known: {', '.join(sorted(ADAPTERS))})")
        options = {k: v for k, v in cfg.items() if k != "adapter" and k not in NOT_FOR_ADAPTER}
        try:
            built.append(ADAPTERS[name](key=key, http=http, **options))
        except TypeError as exc:  # a required option (e.g. tickets_rs `slug`) is missing
            raise ConfigError(f"venues.yaml: venue {key!r} ({name}): {exc}") from exc
    return built
