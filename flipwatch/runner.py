"""Load stored history, run a backtest and save the result.

Shared by the command line and the dashboard so both run backtests the same way.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from flipwatch.api import TIMESTEP_SECONDS
from flipwatch.backtest import BacktestResult, BacktestSettings, Strategy, run_backtest
from flipwatch.store import Store
from flipwatch.strategies import DipBuy, MarginFlip

SECONDS_PER_DAY = 24 * 60 * 60

STRATEGIES: dict[str, Callable[[], Strategy]] = {"margin": MarginFlip, "dip": DipBuy}


class BacktestRequestError(Exception):
    """Raised when a backtest cannot run with the stored data, with a message for the user."""


@dataclass(frozen=True)
class BacktestRequest:
    strategy: str = "margin"
    timestep: str = "5m"
    # Without an explicit start, the most recent `days` of data before `end` are used.
    days: int = 7
    start: int | None = None
    # Without an explicit end, the backtest runs up to the newest stored window.
    end: int | None = None
    # Without explicit items, the `top` most traded items in the period are used.
    item_ids: tuple[int, ...] = ()
    top: int = 50
    settings: BacktestSettings = field(default_factory=BacktestSettings)


@dataclass(frozen=True)
class SavedRun:
    run_id: int
    result: BacktestResult


def run_and_save(store: Store, request: BacktestRequest, now: float) -> SavedRun:
    if request.strategy not in STRATEGIES:
        raise BacktestRequestError(f"unknown strategy {request.strategy!r}")
    if request.timestep not in TIMESTEP_SECONDS:
        raise BacktestRequestError(f"unknown timestep {request.timestep!r}")

    end = request.end or store.latest_window_end(request.timestep)
    if end is None:
        raise BacktestRequestError(f"no {request.timestep} data stored yet")
    start = request.start or end - request.days * SECONDS_PER_DAY
    if start >= end:
        raise BacktestRequestError("the start must be before the end")

    items = store.items()
    item_ids = list(request.item_ids) or store.top_items_by_volume(
        request.timestep, start, end, request.top
    )
    unusable = [i for i in item_ids if i not in items or items[i].buy_limit is None]
    if unusable:
        raise BacktestRequestError(f"no stored item with a buy limit for ids {unusable}")
    windows = store.windows_between(request.timestep, start, end, item_ids)
    if not windows:
        raise BacktestRequestError("no stored data for those items in that period")

    result = run_backtest(
        STRATEGIES[request.strategy](),
        windows,
        {i: items[i] for i in item_ids},
        TIMESTEP_SECONDS[request.timestep],
        start,
        end,
        request.settings,
    )
    return SavedRun(store.save_backtest(result, created_at=int(now)), result)
