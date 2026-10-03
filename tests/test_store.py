import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from flipwatch.backtest import BacktestResult, BacktestSettings, ItemResult
from flipwatch.models import Item, PriceWindow
from flipwatch.store import _SCHEMA_V1, SCHEMA_VERSION, BacktestRunSummary, Store, StoreError

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
    assert {"items", "price_windows", "collected_windows", "backtest_runs", "watchlist"} <= tables
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


def test_version_1_database_is_upgraded_in_place(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.execute("INSERT INTO items VALUES (4151, 'Abyssal whip', 1, 70, 1, 1, 1, '')")

    with Store.open(db_path) as store:
        assert store.items()[4151].name == "Abyssal whip"
        assert store.backtest_runs() == []

    with sqlite3.connect(db_path) as conn:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION == 3


def test_windows_between(store: Store) -> None:
    for offset in (0, 300, 600):
        store.save_snapshot("5m", T0 + offset, snapshot(T0 + offset, 4151, 560, 2), T0)

    windows = store.windows_between("5m", T0 + 300, T0 + 900, [4151, 560])

    assert [(w.timestamp, w.item_id) for w in windows] == [
        (T0 + 300, 560),
        (T0 + 300, 4151),
        (T0 + 600, 560),
        (T0 + 600, 4151),
    ]
    assert store.windows_between("5m", T0, T0 + 900, []) == []


def test_top_items_by_volume_uses_thinner_side_and_needs_a_buy_limit(store: Store) -> None:
    store.save_items(
        [make_item(1), make_item(2), make_item(3), make_item(4, "No limit", buy_limit=None)]
    )
    windows = {
        1: replace(make_window(1, T0), high_volume=10, low_volume=1_000),
        2: replace(make_window(2, T0), high_volume=500, low_volume=500),
        3: replace(make_window(3, T0), high_volume=100, low_volume=100),
        4: replace(make_window(4, T0), high_volume=9_999, low_volume=9_999),
    }
    store.save_snapshot("5m", T0, windows, T0)

    assert store.top_items_by_volume("5m", T0, T0 + 300, limit=2) == [2, 3]
    assert store.top_items_by_volume("5m", T0 + 300, T0 + 600, limit=2) == []


def test_latest_window_end(store: Store) -> None:
    assert store.latest_window_end("1h") is None

    store.save_windows("1h", [make_window(4151, T0), make_window(4151, T0 + 3_600)])

    assert store.latest_window_end("1h") == T0 + 7_200


def test_backtest_runs_round_trip(store: Store) -> None:
    result = BacktestResult(
        strategy="margin",
        start=T0,
        end=T0 + 3_600,
        timestep_seconds=300,
        settings=BacktestSettings(starting_capital=1_000_000),
        realised_profit=1_234,
        profit_per_hour=1_234.0,
        fill_rate=0.5,
        peak_capital_committed=10_000,
        max_drawdown=100,
        max_drawdown_percent=0.01,
        final_cash=1_001_234,
        held_stock_value=0,
        held_stock_cost=0,
        offers_placed=4,
        offers_rejected=1,
        items=[ItemResult(4151, "Abyssal whip", 10, 10, 8_000_000, 8_001_234, 1_234, 20, 20)],
        equity_curve=[(T0 + 300, 1_000_000), (T0 + 600, 1_001_234)],
    )

    first = store.save_backtest(result, created_at=T0 + 4_000)
    second = store.save_backtest(replace(result, strategy="dip"), created_at=T0 + 5_000)

    assert store.backtest_run(first) == result
    assert [run.strategy for run in store.backtest_runs()] == ["dip", "margin"]
    assert store.backtest_runs()[1] == BacktestRunSummary(
        first, T0 + 4_000, "margin", T0, T0 + 3_600, 1_234
    )
    assert second != first
    assert store.backtest_run(999) is None


def test_watchlist(store: Store) -> None:
    assert store.watchlist() == []

    assert store.watch(4151, added_at=T0 + 10)
    assert store.watch(560, added_at=T0 + 20)
    assert not store.watch(4151, added_at=T0 + 30)
    assert store.watchlist() == [4151, 560]

    assert store.unwatch(4151)
    assert not store.unwatch(4151)
    assert store.watchlist() == [560]
