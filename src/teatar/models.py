from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from .textnorm import normalize

TZ = ZoneInfo("Europe/Belgrade")


_clock = None  # tests can install a fake clock with set_clock()


def now() -> datetime:
    return _clock() if _clock else datetime.now(TZ)


def set_clock(fn) -> None:
    global _clock
    _clock = fn


class Status(StrEnum):
    NOT_ON_SALE = "not_on_sale"
    ON_SALE = "on_sale"
    LOW = "low"
    SOLD_OUT = "sold_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def buyable(self) -> bool:
        return self in (Status.ON_SALE, Status.LOW)


@dataclass
class Performance:
    venue: str  # venue key from config/venues.yaml, e.g. "narodno"
    source_id: str  # id that is stable within the venue
    title: str
    start: datetime  # timezone-aware, Europe/Belgrade
    stage: str = ""
    subtitle: str = ""
    url: str = ""
    buy_url: str = ""
    status: Status = Status.UNKNOWN
    available: int | None = None  # exact number of tickets left, when the source exposes it
    sales_start: datetime | None = None
    price: str = ""
    extra: dict = field(default_factory=dict)  # adapter-specific lookup data

    @property
    def uid(self) -> str:
        return f"{self.venue}:{self.source_id}"

    @property
    def title_norm(self) -> str:
        return normalize(self.title)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["sales_start"] = self.sales_start.isoformat() if self.sales_start else None
        d["status"] = str(self.status)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Performance:
        d = dict(d)
        d["start"] = datetime.fromisoformat(d["start"])
        d["sales_start"] = datetime.fromisoformat(d["sales_start"]) if d.get("sales_start") else None
        d["status"] = Status(d.get("status", "unknown"))
        return cls(**d)
