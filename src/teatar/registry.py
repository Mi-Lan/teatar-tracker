"""Adapter registry. Each adapter module registers itself with @register("<name>")."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.base import Adapter
    from .http import Http

ADAPTERS: dict[str, type[Adapter]] = {}


def register(name: str):
    def deco(cls):
        cls.adapter_name = name
        ADAPTERS[name] = cls
        return cls

    return deco


def build_adapters(venues: dict[str, dict], http: Http, only: list[str] | None = None) -> list[Adapter]:
    """Instantiate one adapter per enabled venue in config/venues.yaml."""
    from . import adapters  # noqa: F401  (imports all adapter modules so they register)

    built = []
    for key, cfg in venues.items():
        cfg = dict(cfg)
        if only and key not in only:
            continue
        if not cfg.pop("enabled", True):
            continue
        name = cfg.pop("adapter")
        if name not in ADAPTERS:
            raise KeyError(f"venue {key!r}: unknown adapter {name!r} (known: {sorted(ADAPTERS)})")
        built.append(ADAPTERS[name](key=key, http=http, **cfg))
    return built
