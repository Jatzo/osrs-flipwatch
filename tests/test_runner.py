import json
import re
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

import pytest

from flipwatch.backtest import BacktestSettings
from flipwatch.models import Item, PriceWindow
from flipwatch.runner import BacktestRequest, BacktestRequestError, run_and_save
from flipwatch.store import Store

T0 = 1_791_000_000


def make_item(item_id: int, buy_limit: int | None = 5_000) -> Item:
    return Item(item_id, f"Item {item_id}", True, buy_limit, 100, 60, 40, "")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store.open(tmp_path / "flipwatch.sqlite3") as opened:
        opened.save_items([make_item(1), make_item(2), make_item(3, buy_limit=None)])
        for i in range(24):
            timestamp = T0 + i * 300
            windows = {
                item_id: PriceWindow(item_id, timestamp, 1_200, 1_000 * item_id, 1_000, 1_000)
                for item_id in (1, 2, 3)
            }
            opened.save_snapshot("5m", timestamp, windows, collected_at=timestamp)
        yield opened


def test_defaults_use_the_newest_data_and_most_traded_items(store: Store) -> None:
    saved = run_and_save(store, BacktestRequest(days=1), now=T0 + 10_000)

    assert saved.result.end == T0 + 24 * 300
    assert saved.result.universe == [1, 2]
    assert store.backtest_run(saved.run_id) == saved.result


def test_explicit_items_and_settings(store: Store) -> None:
    request = BacktestRequest(
        strategy="dip",
        item_ids=(1,),
        settings=BacktestSettings(starting_capital=1_000_000),
    )

    saved = run_and_save(store, request, now=T0)

    assert saved.result.strategy == "dip"
    assert saved.result.universe == [1]
    assert saved.result.settings.starting_capital == 1_000_000


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        (BacktestRequest(strategy="martingale"), "unknown strategy"),
        (BacktestRequest(timestep="10m"), "unknown timestep"),
        (BacktestRequest(timestep="1h"), "no 1h data stored yet"),
        (BacktestRequest(start=T0 + 10_000, end=T0), "start must be before the end"),
        (BacktestRequest(item_ids=(3,)), "no stored item with a buy limit for ids [3]"),
        (BacktestRequest(start=T0 - 10_000, end=T0 - 5_000), "no stored data"),
    ],
)
def test_errors(store: Store, request_: BacktestRequest, message: str) -> None:
    with pytest.raises(BacktestRequestError, match=re.escape(message)):
        run_and_save(store, request_, now=T0)


def test_runs_saved_before_the_universe_was_recorded_still_load(store: Store) -> None:
    saved = run_and_save(store, BacktestRequest(), now=T0)
    old = asdict(saved.result)
    del old["universe"]
    with store._conn:
        store._conn.execute(
            "UPDATE backtest_runs SET result = ? WHERE id = ?", (json.dumps(old), saved.run_id)
        )

    loaded = store.backtest_run(saved.run_id)

    assert loaded is not None
    assert loaded.universe == []
