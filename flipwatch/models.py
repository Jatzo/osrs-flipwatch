"""Dataclasses for item metadata, price data and flip opportunities.

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


@dataclass(frozen=True)
class Confidence:
    """How far to trust a margin, from 0 to 100.

    The score is liquidity multiplied by stability, so weakness in either one pulls it down.
    """

    score: int
    liquidity: float
    stability: float


@dataclass(frozen=True)
class Opportunity:
    """A possible flip: buy near the instant sell price, sell near the instant buy price."""

    item: Item
    buy_price: int
    sell_price: int
    tax: int
    margin: int
    roi: float
    # Hourly volumes. A buy offer fills against instant sellers (the low side) and a
    # sell offer fills against instant buyers (the high side).
    low_volume: int
    high_volume: int
    quantity: int
    potential_profit: int
    confidence: Confidence

    @property
    def tradeable_volume(self) -> int:
        """The thinner side of the market, which limits how much can be flipped."""
        return min(self.low_volume, self.high_volume)


@dataclass(frozen=True)
class Alert:
    """A flip that met the alert rules, with its numbers at the moment it was raised."""

    id: int
    item_id: int
    item_name: str
    created_at: int
    buy_price: int
    sell_price: int
    margin: int
    roi: float
    potential_profit: int
    confidence: int
    read: bool
