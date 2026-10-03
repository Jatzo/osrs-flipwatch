import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from flipwatch.models import Item, PriceWindow
from flipwatch.store import SCHEMA_VERSION, Store, StoreError

T0 = 1_791_066_000


def make_item(item_id: int, name: str = "Abyssal whip", buy_limit: int | None = 70) -> Item:
    return Item(
        id=item_id,
        name=name,
        members=True,
        buy_limit=buy_limit,
        value=120_001,
        high_alch=72_000,
        low_alch=48_000,
        icon=f"{name}.png",
    )


def make_window(item_id: int, timestamp: int, avg_high: int | None = 820_000) -> PriceWindow:
    return PriceWindow(
        item_id=item_id,
        timestamp=timestamp,
        avg_high_price=avg_high,
        high_volume=6 if avg_high is not None else 0,
        avg_low_price=810_000,
        low_volume=14,
    )


def snapshot(timestamp: int, *item_ids: int) -> dict[int, PriceWindow]:
    return {item_id: make_window(item_id, timestamp) for item_id in item_ids}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "flipwatch.sqlite3"


@pytest.fixture
def store(db_path: Path) -> Iterator[Store]:
    with Store.open(db_path) as opened:
        yield opened


def test_new_database_gets_the_schema(store: Store, db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        (journal_mode,) = conn.execute("PRAGMA journal_mode").fetchone()

    assert version == SCHEMA_VERSION
    assert {"items", "price_windows", "collected_windows"} <= tables
    assert journal_mode == "wal"


def test_reopening_keeps_data(db_path: Path) -> None:
    with Store.open(db_path) as store:
        store.save_items([make_item(4151)])

    with Store.open(db_path) as store:
        assert store.items()[4151].name == "Abyssal whip"


def test_newer_schema_is_refused(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    with pytest.raises(StoreError, match="newer"):
        Store.open(db_path)


def test_items_round_trip_and_update(store: Store) -> None:
    store.save_items([make_item(4151), make_item(28226, "3rd Age felling axe", buy_limit=None)])
    store.save_items([make_item(4151, buy_limit=80)])

    items = store.items()

    assert items[4151].buy_limit == 80
    assert items[4151].members is True
    assert items[28226].buy_limit is None
    assert len(items) == 2


def test_snapshot_is_stored_once(store: Store) -> None:
    assert store.save_snapshot("5m", T0, snapshot(T0, 4151, 560), collected_at=T0 + 330)
    assert not store.save_snapshot("5m", T0, snapshot(T0, 4151, 560), collected_at=T0 + 630)

    assert store.collected_timestamps("5m", since=0) == {T0}
    assert len(store.windows_for_item(4151, "5m")) == 1


def test_empty_snapshot_is_still_recorded(store: Store) -> None:
    assert store.save_snapshot("5m", T0, {}, collected_at=T0 + 330)

    assert store.collected_timestamps("5m", since=0) == {T0}


def test_windows_round_trip_including_nulls(store: Store) -> None:
    window = make_window(6, T0, avg_high=None)

    store.save_snapshot("5m", T0, {6: window}, collected_at=T0)

    assert store.windows_for_item(6, "5m") == [window]


def test_windows_for_item_are_filtered_and_ordered(store: Store) -> None:
    for offset in (600, 0, 300, 900):
        store.save_snapshot("5m", T0 + offset, snapshot(T0 + offset, 4151, 560), T0 + offset)

    windows = store.windows_for_item(4151, "5m", start=T0 + 300, end=T0 + 900)

    assert [w.timestamp for w in windows] == [T0 + 300, T0 + 600]
    assert all(w.item_id == 4151 for w in windows)
    assert store.windows_for_item(4151, "1h") == []


def test_save_windows_keeps_existing_rows(store: Store) -> None:
    store.save_snapshot("5m", T0, snapshot(T0, 4151), collected_at=T0)

    added = store.save_windows("5m", [make_window(4151, T0, 1), make_window(4151, T0 + 300)])

    assert added == 1
    assert [w.avg_high_price for w in store.windows_for_item(4151, "5m")] == [820_000, 820_000]


def test_seeded_windows_are_not_counted_as_snapshots(store: Store) -> None:
    store.save_windows("5m", [make_window(4151, T0)])

    assert store.collected_timestamps("5m", since=0) == set()


def test_collected_timestamps_since(store: Store) -> None:
    for offset in (0, 300, 600):
        store.save_snapshot("5m", T0 + offset, {}, T0 + offset)

    assert store.collected_timestamps("5m", since=T0 + 300) == {T0 + 300, T0 + 600}


def test_summary_counts_missing_windows(store: Store) -> None:
    for offset in (0, 300, 1200):
        store.save_snapshot("5m", T0 + offset, snapshot(T0 + offset, 4151, 560), T0 + offset)

    summary = store.summary("5m")

    assert summary.snapshot_count == 3
    assert (summary.first_timestamp, summary.last_timestamp) == (T0, T0 + 1200)
    assert summary.missing_count == 2
    assert summary.row_count == 6


def test_summary_of_empty_store(store: Store) -> None:
    summary = store.summary("5m")

    assert summary.snapshot_count == 0
    assert summary.first_timestamp is None
    assert summary.missing_count == 0


def test_delete_before(store: Store) -> None:
    for offset in (0, 300, 600):
        store.save_snapshot("5m", T0 + offset, snapshot(T0 + offset, 4151), T0 + offset)

    removed = store.delete_before(T0 + 600)

    assert removed == 2
    assert [w.timestamp for w in store.windows_for_item(4151, "5m")] == [T0 + 600]
    assert store.collected_timestamps("5m", since=0) == {T0 + 600}


def test_unknown_timestep_is_rejected(store: Store) -> None:
    with pytest.raises(ValueError, match="timestep"):
        store.save_windows("10m", [])
