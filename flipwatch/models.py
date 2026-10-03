"""Dataclasses for item metadata and price data from the OSRS Wiki prices API.

Prices are whole coins and times are Unix timestamps in seconds, as the API returns them.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    members: bool
    # The API leaves these fields out for some items, so None means "not listed".
    buy_limit: int | None
    value: int | None
    high_alch: int | None
    low_alch: int | None
    icon: str

    @classmethod
    def from_api(cls, record: Mapping[str, Any]) -> "Item":
        return cls(
            id=record["id"],
            name=record["name"],
            members=record["members"],
            buy_limit=record.get("limit"),
            value=record.get("value"),
            high_alch=record.get("highalch"),
            low_alch=record.get("lowalch"),
            icon=record["icon"],
        )


@dataclass(frozen=True)
class LatestPrice:
    """The most recent instant buy (high) and instant sell (low) for an item.

    Either side is None when the item has never traded that way.
    """

    item_id: int
    high: int | None
    high_time: int | None
    low: int | None
    low_time: int | None

    @classmethod
    def from_api(cls, item_id: int, record: Mapping[str, Any]) -> "LatestPrice":
        return cls(
            item_id=item_id,
            high=record["high"],
            high_time=record["highTime"],
            low=record["low"],
            low_time=record["lowTime"],
        )


@dataclass(frozen=True)
class PriceWindow:
    """Average prices and traded volume for one item over a fixed window.

    `timestamp` is the start of the window. An average price is None when nothing
    traded on that side, in which case its volume is zero.
    """

    item_id: int
    timestamp: int
    avg_high_price: int | None
    high_volume: int
    avg_low_price: int | None
    low_volume: int

    @property
    def total_volume(self) -> int:
        return self.high_volume + self.low_volume

    @classmethod
    def from_api(cls, item_id: int, timestamp: int, record: Mapping[str, Any]) -> "PriceWindow":
        return cls(
            item_id=item_id,
            timestamp=timestamp,
            avg_high_price=record["avgHighPrice"],
            high_volume=record["highPriceVolume"],
            avg_low_price=record["avgLowPrice"],
            low_volume=record["lowPriceVolume"],
        )
