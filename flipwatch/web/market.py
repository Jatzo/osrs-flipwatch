"""Gather market data for dashboard pages: item search, prices and chart history."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from flipwatch.api import TIMESTEP_SECONDS, ApiError, PricesClient
from flipwatch.models import Item, PriceWindow
from flipwatch.store import Store

CHART_RANGES = {"6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600}
MAX_SEARCH_RESULTS = 50
# Stored history may start this share of the range late and still be used for the chart.
COVERAGE_SLACK = 0.1


@dataclass(frozen=True)
class ItemHistory:
    windows: list[PriceWindow]
    source: str
    error: str | None = None


def find_items(items: Mapping[int, Item], query: str) -> list[Item]:
    """Match an item id, an exact name, or failing that every name containing the query."""
    query = query.strip()
    if not query:
        return []
    if query.isdigit():
        item = items.get(int(query))
        return [item] if item else []
    lowered = query.lower()
    exact = [item for item in items.values() if item.name.lower() == lowered]
    if exact:
        return exact
    partial = [item for item in items.values() if lowered in item.name.lower()]
    return sorted(partial, key=lambda item: (len(item.name), item.name))[:MAX_SEARCH_RESULTS]


def item_history(
    store: Store, client: PricesClient, item_id: int, range_seconds: int, now: float
) -> ItemHistory:
    """Use stored windows when they cover the range, otherwise one timeseries request."""
    start = int(now - range_seconds)
    # 365 five minute points cover about 30 hours, so longer ranges need hourly points.
    timestep = "5m" if range_seconds <= 24 * 3600 else "1h"
    for stored_timestep in dict.fromkeys(["5m", timestep]):
        stored = store.windows_for_item(item_id, stored_timestep, start=start)
        if _covers(stored, start, now, range_seconds, TIMESTEP_SECONDS[stored_timestep]):
            return ItemHistory(stored, f"stored {stored_timestep} windows")
    try:
        points = client.timeseries(item_id, timestep)
    except ApiError as exc:
        return ItemHistory([], "the OSRS Wiki", error=str(exc))
    recent = [w for w in points if w.timestamp >= start]
    return ItemHistory(recent, f"the OSRS Wiki {timestep} timeseries")


def _covers(
    windows: Sequence[PriceWindow], start: int, now: float, range_seconds: int, step: int
) -> bool:
    """True when stored windows span most of the range at both ends.

    A collector that stopped hours ago leaves history that starts on time but ends early,
    which would hide the most recent prices, so the end is checked as well as the start.
    """
    if not windows:
        return False
    slack = range_seconds * COVERAGE_SLACK
    starts_on_time = windows[0].timestamp - start <= slack
    ends_recently = now - (windows[-1].timestamp + step) <= slack
    return starts_on_time and ends_recently


def price_chart(windows: Sequence[PriceWindow]) -> dict[str, Any]:
    return {
        "labels": _labels(w.timestamp for w in windows),
        "high": [w.avg_high_price for w in windows],
        "low": [w.avg_low_price for w in windows],
        "highVolume": [w.high_volume for w in windows],
        "lowVolume": [w.low_volume for w in windows],
    }


def equity_chart(points: Sequence[tuple[int, int]]) -> dict[str, Any]:
    return {
        "labels": _labels(timestamp for timestamp, _ in points),
        "equity": [equity for _, equity in points],
    }


def _labels(timestamps: Iterable[int]) -> list[str]:
    return [datetime.fromtimestamp(t, UTC).strftime("%d %b %H:%M") for t in timestamps]
