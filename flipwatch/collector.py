"""Scheduled job that stores five minute price snapshots for backtesting."""

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol

from flipwatch.api import TIMESTEP_SECONDS, ApiError
from flipwatch.config import EXCLUDED_ITEM_IDS, SECONDS_PER_DAY
from flipwatch.models import Item, PriceWindow
from flipwatch.store import Store

log = logging.getLogger(__name__)

TIMESTEP = "5m"
WINDOW_SECONDS = TIMESTEP_SECONDS[TIMESTEP]
# How many past windows each run checks for gaps, one request per missing window.
BACKFILL_WINDOWS = 12
# The API publishes a window shortly after it closes, so wait a little past the boundary.
COLLECT_DELAY_SECONDS = 30
# Seeding uses one request per item, so keep it to a handful as the wiki asks.
MAX_SEED_ITEMS = 10


class PriceSource(Protocol):
    def mapping(self) -> Mapping[int, Item]: ...

    def five_minute(self, timestamp: int | None = None) -> Mapping[int, PriceWindow]: ...

    def timeseries(self, item_id: int, timestep: str = "5m") -> list[PriceWindow]: ...


def last_complete_window(now: float) -> int:
    """Return the start of the most recent five minute window that has fully closed."""
    return math.floor(now / WINDOW_SECONDS) * WINDOW_SECONDS - WINDOW_SECONDS


def seconds_until_next_run(now: float) -> float:
    """Return how long to wait until the next window has closed and been published."""
    next_run = (
        math.floor((now - COLLECT_DELAY_SECONDS) / WINDOW_SECONDS) * WINDOW_SECONDS
        + WINDOW_SECONDS
        + COLLECT_DELAY_SECONDS
    )
    return next_run - now


def collect_once(
    client: PriceSource,
    store: Store,
    now: float,
    backfill_windows: int = BACKFILL_WINDOWS,
) -> list[int]:
    """Store the latest window and fill any recent gaps. Returns the windows stored."""
    store.save_items(item for item in client.mapping().values() if item.id not in EXCLUDED_ITEM_IDS)

    newest = last_complete_window(now)
    wanted = [newest - i * WINDOW_SECONDS for i in range(backfill_windows, -1, -1)]
    already_stored = store.collected_timestamps(TIMESTEP, since=wanted[0])

    stored = []
    for timestamp in wanted:
        if timestamp in already_stored:
            continue
        try:
            windows = _without_excluded(client.five_minute(timestamp=timestamp))
        except ApiError as exc:
            log.warning("Could not fetch window %s: %s", format_timestamp(timestamp), exc)
            continue
        # An empty answer means the window is not published yet. Recording it would
        # look like a market with no trades, so leave it for the next run to retry.
        if not windows:
            log.info("Window %s is not available yet", format_timestamp(timestamp))
            continue
        store.save_snapshot(TIMESTEP, timestamp, windows, collected_at=int(now))
        log.info("Stored window %s for %d items", format_timestamp(timestamp), len(windows))
        stored.append(timestamp)
    return stored


def prune(store: Store, now: float, retention_days: int) -> int:
    """Delete data older than the retention period. Zero days keeps everything."""
    if retention_days == 0:
        return 0
    removed = store.delete_before(int(now) - retention_days * SECONDS_PER_DAY)
    if removed:
        log.info("Removed %d rows older than %d days", removed, retention_days)
    return removed


def run_forever(
    client: PriceSource,
    store: Store,
    retention_days: int,
    *,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Collect every five minutes until interrupted.

    Windows that fail to download are retried on later runs while they are still inside
    the backfill period.
    """
    while True:
        now = clock()
        try:
            collect_once(client, store, now)
        except ApiError as exc:
            log.warning("Collection failed, will retry next window: %s", exc)
        prune(store, now, retention_days)
        sleep(seconds_until_next_run(clock()))


def seed(client: PriceSource, store: Store, item_ids: Sequence[int], timestep: str) -> int:
    """Store recent history for a few items from the timeseries endpoint.

    Returns the number of new rows. Existing rows are kept.
    """
    if len(item_ids) > MAX_SEED_ITEMS:
        raise ValueError(f"seed at most {MAX_SEED_ITEMS} items at a time, got {len(item_ids)}")
    excluded = sorted(set(item_ids) & EXCLUDED_ITEM_IDS)
    if excluded:
        raise ValueError(f"these items are excluded from tracking: {excluded}")

    items = client.mapping()
    unknown = [item_id for item_id in item_ids if item_id not in items]
    if unknown:
        raise ValueError(f"unknown item ids: {unknown}")

    store.save_items(items[item_id] for item_id in item_ids)
    added = 0
    for item_id in item_ids:
        points = client.timeseries(item_id, timestep)
        added += store.save_windows(timestep, points)
        log.info("Seeded %s with %d %s windows", items[item_id].name, len(points), timestep)
    return added


def format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _without_excluded(windows: Mapping[int, PriceWindow]) -> dict[int, PriceWindow]:
    return {item_id: w for item_id, w in windows.items() if item_id not in EXCLUDED_ITEM_IDS}
