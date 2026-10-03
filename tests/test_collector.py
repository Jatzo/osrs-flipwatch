import logging
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from flipwatch.api import ApiError
from flipwatch.collector import (
    BACKFILL_WINDOWS,
    COLLECT_DELAY_SECONDS,
    MAX_SEED_ITEMS,
    SECONDS_PER_DAY,
    collect_once,
    last_complete_window,
    prune,
    run_forever,
    seconds_until_next_run,
    seed,
)
from flipwatch.models import Item, PriceWindow
from flipwatch.store import Store

BOUNDARY = 1_791_066_600
NOW = BOUNDARY + COLLECT_DELAY_SECONDS
NEWEST = BOUNDARY - 300
BOND = 13190


def window(item_id: int, timestamp: int) -> PriceWindow:
    return PriceWindow(
        item_id=item_id,
        timestamp=timestamp,
        avg_high_price=200,
        high_volume=1_000,
        avg_low_price=190,
        low_volume=1_000,
    )


class FakeSource:
    """Answers like the prices API, with every window known unless told otherwise.

    Windows in `failing` raise an error the first time they are requested.
    """

    def __init__(self, items: Mapping[int, Item], timeseries: list[PriceWindow]) -> None:
        self.items = items
        self.timeseries_points = timeseries
        self.unpublished: set[int] = set()
        self.failing: set[int] = set()
        self.window_requests: list[int | None] = []

    def mapping(self) -> Mapping[int, Item]:
        return self.items

    def five_minute(self, timestamp: int | None = None) -> Mapping[int, PriceWindow]:
        self.window_requests.append(timestamp)
        if timestamp in self.failing:
            self.failing.remove(timestamp)
            raise ApiError("/5m returned HTTP 503")
        if timestamp in self.unpublished or timestamp is None:
            return {}
        return {item_id: window(item_id, timestamp) for item_id in (560, 4151, BOND)}

    def timeseries(self, item_id: int, timestep: str = "5m") -> list[PriceWindow]:
        return [
            PriceWindow(
                item_id=item_id,
                timestamp=p.timestamp,
                avg_high_price=p.avg_high_price,
                high_volume=p.high_volume,
                avg_low_price=p.avg_low_price,
                low_volume=p.low_volume,
            )
            for p in self.timeseries_points
        ]


@pytest.fixture
def source(mapping_payload: list[dict[str, Any]], timeseries_payload: dict[str, Any]) -> FakeSource:
    items = {r["id"]: Item.from_api(r) for r in mapping_payload}
    points = [PriceWindow.from_api(4151, p["timestamp"], p) for p in timeseries_payload["data"]]
    return FakeSource(items, points)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store.open(tmp_path / "flipwatch.sqlite3") as opened:
        yield opened


class TestSchedule:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [(BOUNDARY, BOUNDARY - 300), (BOUNDARY + 299, BOUNDARY - 300), (BOUNDARY + 300, BOUNDARY)],
    )
    def test_last_complete_window(self, now: float, expected: int) -> None:
        assert last_complete_window(now) == expected

    @pytest.mark.parametrize(
        ("now", "wait"),
        [
            (BOUNDARY, COLLECT_DELAY_SECONDS),
            (BOUNDARY + 10, COLLECT_DELAY_SECONDS - 10),
            (BOUNDARY + COLLECT_DELAY_SECONDS, 300),
            (BOUNDARY + 200.5, 129.5),
        ],
    )
    def test_next_run_is_just_after_a_boundary(self, now: float, wait: float) -> None:
        assert seconds_until_next_run(now) == wait
        assert (now + wait - COLLECT_DELAY_SECONDS) % 300 == 0


class TestCollectOnce:
    def test_first_run_stores_latest_window_and_backfill(
        self, source: FakeSource, store: Store
    ) -> None:
        stored = collect_once(source, store, NOW)

        assert stored[-1] == NEWEST
        assert len(stored) == BACKFILL_WINDOWS + 1
        assert stored == sorted(stored)
        assert store.collected_timestamps("5m", since=0) == set(stored)

    def test_stores_item_metadata(self, source: FakeSource, store: Store) -> None:
        collect_once(source, store, NOW)

        assert store.items()[4151].name == "Abyssal whip"

    def test_bonds_are_not_stored(self, source: FakeSource, store: Store) -> None:
        collect_once(source, store, NOW)

        assert BOND not in store.items()
        assert store.windows_for_item(BOND, "5m") == []
        assert len(store.windows_for_item(560, "5m")) == BACKFILL_WINDOWS + 1

    def test_second_run_only_fetches_the_new_window(self, source: FakeSource, store: Store) -> None:
        collect_once(source, store, NOW)
        source.window_requests.clear()

        stored = collect_once(source, store, NOW + 300)

        assert stored == [NEWEST + 300]
        assert source.window_requests == [NEWEST + 300]

    def test_gap_is_backfilled(self, source: FakeSource, store: Store) -> None:
        collect_once(source, store, NOW)
        source.window_requests.clear()

        stored = collect_once(source, store, NOW + 3 * 300)

        assert stored == [NEWEST + 300, NEWEST + 600, NEWEST + 900]
        assert source.window_requests == stored

    def test_unpublished_window_is_left_for_the_next_run(
        self, source: FakeSource, store: Store
    ) -> None:
        source.unpublished.add(NEWEST)

        stored = collect_once(source, store, NOW)

        assert NEWEST not in stored
        source.unpublished.clear()
        assert collect_once(source, store, NOW) == [NEWEST]

    def test_never_asks_for_an_unaligned_or_bare_window(
        self, source: FakeSource, store: Store
    ) -> None:
        collect_once(source, store, NOW + 123.4)

        assert None not in source.window_requests
        assert all(ts is not None and ts % 300 == 0 for ts in source.window_requests)


class TestRunForever:
    def test_failed_window_is_retried_next_run(
        self, source: FakeSource, store: Store, caplog: pytest.LogCaptureFixture
    ) -> None:
        source.failing.add(NEWEST - 300)
        times = iter([NOW, NOW, NOW + 300, NOW + 300])
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt

        with caplog.at_level(logging.WARNING), pytest.raises(KeyboardInterrupt):
            run_forever(source, store, 90, clock=lambda: next(times), sleep=sleep)

        assert "Could not fetch window" in caplog.text
        assert sleeps == [300, 300]
        collected = store.collected_timestamps("5m", since=0)
        assert {NEWEST - 300, NEWEST, NEWEST + 300} <= collected

    def test_keeps_going_when_the_mapping_fails(
        self, source: FakeSource, store: Store, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = 0

        def flaky_mapping() -> Mapping[int, Item]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ApiError("/mapping returned HTTP 503")
            return source.items

        source.mapping = flaky_mapping
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt

        with caplog.at_level(logging.WARNING), pytest.raises(KeyboardInterrupt):
            run_forever(source, store, 90, clock=lambda: NOW, sleep=sleep)

        assert "Collection failed" in caplog.text
        assert NEWEST in store.collected_timestamps("5m", since=0)

    def test_prunes_old_data_each_run(self, source: FakeSource, store: Store) -> None:
        old = NEWEST - 91 * SECONDS_PER_DAY
        store.save_snapshot("5m", old, {560: window(560, old)}, collected_at=old)

        def stop(_: float) -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run_forever(source, store, 90, clock=lambda: NOW, sleep=stop)

        assert old not in store.collected_timestamps("5m", since=0)


class TestPrune:
    def test_removes_data_past_retention(self, store: Store) -> None:
        cutoff = NOW - 90 * SECONDS_PER_DAY
        aligned = cutoff - cutoff % 300
        for ts in (aligned - 300, aligned + 300, aligned + 600):
            store.save_snapshot("5m", ts, {560: window(560, ts)}, collected_at=ts)

        removed = prune(store, NOW, retention_days=90)

        assert removed == 1
        assert [w.timestamp for w in store.windows_for_item(560, "5m")] == [
            aligned + 300,
            aligned + 600,
        ]

    def test_zero_days_keeps_everything(self, store: Store) -> None:
        store.save_snapshot("5m", 0, {560: window(560, 0)}, collected_at=0)

        assert prune(store, NOW, retention_days=0) == 0
        assert len(store.windows_for_item(560, "5m")) == 1


class TestSeed:
    def test_stores_timeseries_for_requested_items(self, source: FakeSource, store: Store) -> None:
        added = seed(source, store, [4151], "5m")

        windows = store.windows_for_item(4151, "5m")
        assert added == len(windows) == 12
        assert store.items()[4151].name == "Abyssal whip"
        assert store.collected_timestamps("5m", since=0) == set()

    def test_seeding_twice_adds_nothing(self, source: FakeSource, store: Store) -> None:
        seed(source, store, [4151], "1h")

        assert seed(source, store, [4151], "1h") == 0
        assert len(store.windows_for_item(4151, "1h")) == 12

    def test_refuses_too_many_items(self, source: FakeSource, store: Store) -> None:
        with pytest.raises(ValueError, match=f"at most {MAX_SEED_ITEMS}"):
            seed(source, store, list(range(MAX_SEED_ITEMS + 1)), "5m")

    def test_refuses_excluded_items(self, source: FakeSource, store: Store) -> None:
        with pytest.raises(ValueError, match="excluded"):
            seed(source, store, [4151, BOND], "5m")

    def test_refuses_unknown_items(self, source: FakeSource, store: Store) -> None:
        with pytest.raises(ValueError, match="unknown item ids: \\[999999\\]"):
            seed(source, store, [4151, 999_999], "5m")
