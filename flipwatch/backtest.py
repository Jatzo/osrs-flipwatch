"""Strategy interface, conservative fill model and results for backtests.

The engine walks through stored price windows in time order. At each step it fills open
offers against the window that has just closed, adds that window to what the strategy
can see, and then asks the strategy for new offers. A strategy is only ever handed a
`MarketView`, which holds windows that have already closed, so it cannot see the future.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, overload

from flipwatch.config import BUY_LIMIT_WINDOW_SECONDS, GE_OFFER_SLOTS
from flipwatch.models import Item, PriceWindow
from flipwatch.tax import ge_tax


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Offer:
    side: Side
    item_id: int
    price: int
    quantity: int

    def __post_init__(self) -> None:
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError(f"offer price and quantity must be positive: {self}")


@dataclass(frozen=True)
class BacktestSettings:
    starting_capital: int = 50_000_000
    # Share of each later window's volume on the matching side an offer can claim.
    fill_share: float = 0.10
    offer_lifetime_seconds: int = 4 * 60 * 60
    max_open_offers: int = GE_OFFER_SLOTS

    def __post_init__(self) -> None:
        if self.starting_capital <= 0:
            raise ValueError("starting_capital must be positive")
        if not 0 < self.fill_share <= 1:
            raise ValueError("fill_share must be greater than 0 and at most 1")
        if self.offer_lifetime_seconds <= 0:
            raise ValueError("offer_lifetime_seconds must be positive")
        if self.max_open_offers <= 0:
            raise ValueError("max_open_offers must be positive")


class History(Sequence[PriceWindow]):
    """A read-only, fixed length view of one item's closed windows, oldest first."""

    def __init__(self, windows: list[PriceWindow]) -> None:
        self._windows = windows
        self._length = len(windows)

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> PriceWindow: ...

    @overload
    def __getitem__(self, index: slice) -> list[PriceWindow]: ...

    def __getitem__(self, index: int | slice) -> PriceWindow | list[PriceWindow]:
        if isinstance(index, slice):
            return [self._windows[i] for i in range(*index.indices(self._length))]
        if not -self._length <= index < self._length:
            raise IndexError("history index out of range")
        return self._windows[index % self._length]


class MarketView:
    """Everything a strategy may know at time `now`: item metadata and closed windows."""

    def __init__(
        self,
        now: int,
        timestep_seconds: int,
        items: Mapping[int, Item],
        histories: Mapping[int, list[PriceWindow]],
    ) -> None:
        self.now = now
        self.timestep_seconds = timestep_seconds
        self.items = items
        self._histories = histories

    def history(self, item_id: int) -> History:
        return History(self._histories.get(item_id, []))

    def latest(self, item_id: int) -> PriceWindow | None:
        history = self.history(item_id)
        return history[-1] if history else None


@dataclass(frozen=True)
class OpenOfferInfo:
    side: Side
    item_id: int
    price: int
    remaining: int
    placed_at: int


@dataclass(frozen=True)
class PortfolioView:
    """What the strategy owns at time `now`. Coins and items tied up in offers are excluded."""

    cash: int
    holdings: Mapping[int, int]
    open_offers: Sequence[OpenOfferInfo]
    free_slots: int
    buy_limit_remaining: Mapping[int, int]


class Strategy(Protocol):
    name: str

    def decide(self, market: MarketView, portfolio: PortfolioView) -> Sequence[Offer]:
        """Return the offers to place now. Offers that cannot be placed are rejected."""
        ...


@dataclass(frozen=True)
class ItemResult:
    item_id: int
    name: str
    bought: int
    sold: int
    spent: int
    received: int
    realised_profit: int
    offered: int
    filled: int

    @property
    def fill_rate(self) -> float:
        return self.filled / self.offered if self.offered else 0.0


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    start: int
    end: int
    timestep_seconds: int
    settings: BacktestSettings
    realised_profit: int
    profit_per_hour: float
    fill_rate: float
    peak_capital_committed: int
    max_drawdown: int
    max_drawdown_percent: float
    final_cash: int
    # Stock still held at the end, valued at its last average low price after tax.
    # It is reported for information and is not counted as profit.
    held_stock_value: int
    held_stock_cost: int
    offers_placed: int
    offers_rejected: int
    items: list[ItemResult]
    equity_curve: list[tuple[int, int]]


@dataclass
class _OpenOffer:
    offer: Offer
    placed_at: int
    filled: int = 0

    @property
    def remaining(self) -> int:
        return self.offer.quantity - self.filled


@dataclass
class _ItemLedger:
    bought: int = 0
    sold: int = 0
    spent: int = 0
    received: int = 0
    cost_of_sold: int = 0
    offered: int = 0
    filled: int = 0


@dataclass
class _BuyLimit:
    window_start: int | None = None
    bought: int = 0

    def remaining(self, limit: int, now: int) -> int:
        if self.window_start is None or now >= self.window_start + BUY_LIMIT_WINDOW_SECONDS:
            return limit
        return max(0, limit - self.bought)

    def record(self, quantity: int, now: int) -> None:
        if self.window_start is None or now >= self.window_start + BUY_LIMIT_WINDOW_SECONDS:
            self.window_start = now
            self.bought = 0
        self.bought += quantity


@dataclass
class _Portfolio:
    """Coins, stock and open offers, with the bookkeeping the fill model needs."""

    settings: BacktestSettings
    items: Mapping[int, Item]
    cash: int = 0
    reserved_cash: int = 0
    holdings: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    reserved_holdings: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    holding_cost: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    open_offers: list[_OpenOffer] = field(default_factory=list)
    buy_limits: dict[int, _BuyLimit] = field(default_factory=lambda: defaultdict(_BuyLimit))
    ledgers: dict[int, _ItemLedger] = field(default_factory=lambda: defaultdict(_ItemLedger))
    offers_placed: int = 0
    offers_rejected: int = 0

    def place(self, offer: Offer, now: int) -> bool:
        if offer.item_id not in self.items:
            raise ValueError(f"item {offer.item_id} is not in this backtest")
        if len(self.open_offers) >= self.settings.max_open_offers:
            return self._reject()
        if offer.side is Side.BUY:
            cost = offer.price * offer.quantity
            if cost > self.cash - self.reserved_cash:
                return self._reject()
            self.reserved_cash += cost
        else:
            free = self.holdings[offer.item_id] - self.reserved_holdings[offer.item_id]
            if offer.quantity > free:
                return self._reject()
            self.reserved_holdings[offer.item_id] += offer.quantity
        self.open_offers.append(_OpenOffer(offer, placed_at=now))
        self.ledgers[offer.item_id].offered += offer.quantity
        self.offers_placed += 1
        return True

    def fill(self, windows: Mapping[int, PriceWindow], window_start: int, now: int) -> None:
        """Fill open offers against one closed window, oldest offers first."""
        claimed: dict[tuple[int, Side], int] = defaultdict(int)
        for open_offer in self.open_offers:
            offer = open_offer.offer
            window = windows.get(offer.item_id)
            # An offer only trades in windows that started after it was placed.
            if window is None or window_start < open_offer.placed_at:
                continue
            available = _fillable(offer, window, self.settings.fill_share)
            available -= claimed[(offer.item_id, offer.side)]
            if offer.side is Side.BUY:
                limit = self._buy_limit(offer.item_id, now)
                available = min(available, limit)
            quantity = min(open_offer.remaining, available)
            if quantity <= 0:
                continue
            claimed[(offer.item_id, offer.side)] += quantity
            self._apply_fill(open_offer, quantity, now)
        self.open_offers = [o for o in self.open_offers if o.remaining > 0]

    def expire(self, now: int) -> None:
        """Cancel offers that have reached their lifetime, returning coins and stock."""
        lifetime = self.settings.offer_lifetime_seconds
        keep = []
        for open_offer in self.open_offers:
            if now - open_offer.placed_at < lifetime:
                keep.append(open_offer)
            else:
                self._release(open_offer)
        self.open_offers = keep

    def cancel_all(self) -> None:
        for open_offer in self.open_offers:
            self._release(open_offer)
        self.open_offers = []

    def view(self, now: int) -> PortfolioView:
        return PortfolioView(
            cash=self.cash - self.reserved_cash,
            holdings={
                item_id: free
                for item_id, held in self.holdings.items()
                if (free := held - self.reserved_holdings[item_id]) > 0
            },
            open_offers=[
                OpenOfferInfo(
                    o.offer.side, o.offer.item_id, o.offer.price, o.remaining, o.placed_at
                )
                for o in self.open_offers
            ],
            free_slots=self.settings.max_open_offers - len(self.open_offers),
            buy_limit_remaining={item_id: self._buy_limit(item_id, now) for item_id in self.items},
        )

    def committed_capital(self) -> int:
        return self.reserved_cash + sum(self.holding_cost.values())

    def _buy_limit(self, item_id: int, now: int) -> int:
        limit = self.items[item_id].buy_limit
        if limit is None:
            raise ValueError(f"item {item_id} has no buy limit and cannot be backtested")
        return self.buy_limits[item_id].remaining(limit, now)

    def _apply_fill(self, open_offer: _OpenOffer, quantity: int, now: int) -> None:
        offer = open_offer.offer
        ledger = self.ledgers[offer.item_id]
        open_offer.filled += quantity
        ledger.filled += quantity
        if offer.side is Side.BUY:
            cost = offer.price * quantity
            self.cash -= cost
            self.reserved_cash -= cost
            self.holdings[offer.item_id] += quantity
            self.holding_cost[offer.item_id] += cost
            self.buy_limits[offer.item_id].record(quantity, now)
            ledger.bought += quantity
            ledger.spent += cost
        else:
            proceeds = (offer.price - ge_tax(offer.price, offer.item_id)) * quantity
            held = self.holdings[offer.item_id]
            cost = self.holding_cost[offer.item_id] * quantity // held
            self.cash += proceeds
            self.holdings[offer.item_id] -= quantity
            self.reserved_holdings[offer.item_id] -= quantity
            self.holding_cost[offer.item_id] -= cost
            ledger.sold += quantity
            ledger.received += proceeds
            ledger.cost_of_sold += cost

    def _release(self, open_offer: _OpenOffer) -> None:
        offer = open_offer.offer
        if offer.side is Side.BUY:
            self.reserved_cash -= offer.price * open_offer.remaining
        else:
            self.reserved_holdings[offer.item_id] -= open_offer.remaining

    def _reject(self) -> bool:
        self.offers_rejected += 1
        return False


def _fillable(offer: Offer, window: PriceWindow, fill_share: float) -> int:
    """How many items the window could fill, before other offers and buy limits."""
    if offer.side is Side.BUY:
        price, volume = window.avg_low_price, window.low_volume
        trades_at_offer = price is not None and price <= offer.price
    else:
        price, volume = window.avg_high_price, window.high_volume
        trades_at_offer = price is not None and price >= offer.price
    return math.floor(volume * fill_share) if trades_at_offer else 0


def run_backtest(
    strategy: Strategy,
    windows: Iterable[PriceWindow],
    items: Mapping[int, Item],
    timestep_seconds: int,
    start: int,
    end: int,
    settings: BacktestSettings | None = None,
) -> BacktestResult:
    """Run `strategy` over the windows that start at or after `start` and close by `end`."""
    settings = settings or BacktestSettings()
    tradeable = {item_id: item for item_id, item in items.items() if item.buy_limit is not None}
    by_time: dict[int, dict[int, PriceWindow]] = defaultdict(dict)
    for window in windows:
        if window.item_id in tradeable and start <= window.timestamp <= end - timestep_seconds:
            by_time[window.timestamp][window.item_id] = window

    portfolio = _Portfolio(settings, tradeable, cash=settings.starting_capital)
    histories: dict[int, list[PriceWindow]] = defaultdict(list)
    last_low: dict[int, int] = {}
    equity_curve: list[tuple[int, int]] = []
    peak_committed = 0

    for window_start in sorted(by_time):
        now = window_start + timestep_seconds
        closed = by_time[window_start]
        portfolio.fill(closed, window_start, now)
        portfolio.expire(now)
        for item_id, window in closed.items():
            histories[item_id].append(window)
            if window.avg_low_price is not None:
                last_low[item_id] = window.avg_low_price

        equity_curve.append((now, portfolio.cash + _stock_value(portfolio.holdings, last_low)))
        peak_committed = max(peak_committed, portfolio.committed_capital())

        market = MarketView(now, timestep_seconds, tradeable, histories)
        for offer in strategy.decide(market, portfolio.view(now)):
            portfolio.place(offer, now)
        peak_committed = max(peak_committed, portfolio.committed_capital())

    portfolio.cancel_all()
    drawdown, drawdown_percent = max_drawdown(equity_curve, settings.starting_capital)
    return BacktestResult(
        strategy=strategy.name,
        start=start,
        end=end,
        timestep_seconds=timestep_seconds,
        settings=settings,
        realised_profit=sum(_realised(ledger) for ledger in portfolio.ledgers.values()),
        profit_per_hour=_per_hour(portfolio.ledgers, start, end),
        fill_rate=_fill_rate(portfolio.ledgers.values()),
        peak_capital_committed=peak_committed,
        max_drawdown=drawdown,
        max_drawdown_percent=drawdown_percent,
        final_cash=portfolio.cash,
        held_stock_value=_stock_value(portfolio.holdings, last_low),
        held_stock_cost=sum(portfolio.holding_cost.values()),
        offers_placed=portfolio.offers_placed,
        offers_rejected=portfolio.offers_rejected,
        items=_item_results(portfolio.ledgers, tradeable),
        equity_curve=equity_curve,
    )


def result_from_dict(data: Mapping[str, Any]) -> BacktestResult:
    """Rebuild a result saved with `dataclasses.asdict`."""
    fields = dict(data)
    fields["settings"] = BacktestSettings(**fields["settings"])
    fields["items"] = [ItemResult(**item) for item in fields["items"]]
    fields["equity_curve"] = [(t, equity) for t, equity in fields["equity_curve"]]
    return BacktestResult(**fields)


def max_drawdown(
    equity_curve: Sequence[tuple[int, int]], starting_capital: int
) -> tuple[int, float]:
    """Return the largest fall from a peak in coins, and as a share of that peak."""
    peak = starting_capital
    worst, worst_share = 0, 0.0
    for _, equity in equity_curve:
        peak = max(peak, equity)
        fall = peak - equity
        if fall > worst:
            worst, worst_share = fall, fall / peak
    return worst, worst_share


def _realised(ledger: _ItemLedger) -> int:
    return ledger.received - ledger.cost_of_sold


def _per_hour(ledgers: Mapping[int, _ItemLedger], start: int, end: int) -> float:
    hours = (end - start) / 3600
    return sum(_realised(ledger) for ledger in ledgers.values()) / hours if hours > 0 else 0.0


def _fill_rate(ledgers: Iterable[_ItemLedger]) -> float:
    offered = filled = 0
    for ledger in ledgers:
        offered += ledger.offered
        filled += ledger.filled
    return filled / offered if offered else 0.0


def _item_results(
    ledgers: Mapping[int, _ItemLedger], items: Mapping[int, Item]
) -> list[ItemResult]:
    results = [
        ItemResult(
            item_id=item_id,
            name=items[item_id].name,
            bought=ledger.bought,
            sold=ledger.sold,
            spent=ledger.spent,
            received=ledger.received,
            realised_profit=_realised(ledger),
            offered=ledger.offered,
            filled=ledger.filled,
        )
        for item_id, ledger in ledgers.items()
    ]
    return sorted(results, key=lambda r: (-r.realised_profit, r.name))


def _stock_value(holdings: Mapping[int, int], last_low: Mapping[int, int]) -> int:
    """Value held stock at what it could be sold for instantly, after tax."""
    total = 0
    for item_id, quantity in holdings.items():
        price = last_low.get(item_id)
        if price is not None and quantity:
            total += (price - ge_tax(price, item_id)) * quantity
    return total
