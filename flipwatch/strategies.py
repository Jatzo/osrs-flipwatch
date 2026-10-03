"""Example strategies for the backtester."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from flipwatch.backtest import MarketView, Offer, PortfolioView, Side
from flipwatch.models import PriceWindow
from flipwatch.tax import ge_tax

PriceRule = Callable[[MarketView, int], int | None]


@dataclass(frozen=True)
class MarginFlip:
    """Buy at the last window's average low and sell at its average high.

    The backtest counterpart of `flipwatch scan`: it only buys when the gap between the
    averages clears the margin and ROI floors after tax, preferring the widest margin
    weighted by volume.
    """

    min_margin: int = 10
    min_roi: float = 0.01
    min_volume: int = 10
    name: str = "margin"

    def decide(self, market: MarketView, portfolio: PortfolioView) -> list[Offer]:
        sells = sell_holdings(market, portfolio, _latest_high)
        busy = items_in_play(portfolio, sells)
        ranked = sorted(
            (
                (score, item_id, window.avg_low_price)
                for item_id in market.items
                if item_id not in busy
                and (window := _just_closed(market, item_id)) is not None
                and window.avg_low_price is not None
                and (score := self._score(item_id, window)) is not None
            ),
            reverse=True,
        )
        buys = spread_buys([(item_id, price) for _, item_id, price in ranked], portfolio, sells)
        return sells + buys

    def _score(self, item_id: int, window: PriceWindow) -> int | None:
        low, high = window.avg_low_price, window.avg_high_price
        thinner_side = min(window.low_volume, window.high_volume)
        if low is None or high is None or thinner_side < self.min_volume:
            return None
        margin = high - low - ge_tax(high, item_id)
        if margin < self.min_margin or margin / low < self.min_roi:
            return None
        return margin * thinner_side


@dataclass(frozen=True)
class DipBuy:
    """Buy when the average low falls well below its recent mean, then sell at the mean.

    A simple mean reversion strategy, there to show the interface handles more than
    margin flipping. The sell target is the mean average high over the same lookback.
    """

    lookback: int = 24
    dip: float = 0.03
    name: str = "dip"

    def decide(self, market: MarketView, portfolio: PortfolioView) -> list[Offer]:
        sells = sell_holdings(market, portfolio, self._target)
        busy = items_in_play(portfolio, sells)
        dips = []
        for item_id in market.items:
            window = _just_closed(market, item_id)
            if item_id in busy or window is None or window.avg_low_price is None:
                continue
            recent = market.history(item_id)[-self.lookback :]
            mean_low = _mean(w.avg_low_price for w in recent)
            if len(recent) < self.lookback or mean_low is None:
                continue
            depth = 1 - window.avg_low_price / mean_low
            if depth >= self.dip:
                dips.append((depth, item_id, window.avg_low_price))
        dips.sort(reverse=True)
        buys = spread_buys([(item_id, price) for _, item_id, price in dips], portfolio, sells)
        return sells + buys

    def _target(self, market: MarketView, item_id: int) -> int | None:
        recent = market.history(item_id)[-self.lookback :]
        mean_high = _mean(w.avg_high_price for w in recent)
        return round(mean_high) if mean_high is not None else None


def sell_holdings(market: MarketView, portfolio: PortfolioView, price: PriceRule) -> list[Offer]:
    """Offer every free item for sale at the price the rule gives, while slots last."""
    offers = []
    for item_id, quantity in sorted(portfolio.holdings.items()):
        if len(offers) >= portfolio.free_slots:
            break
        target = price(market, item_id)
        if target is not None and target > 0:
            offers.append(Offer(Side.SELL, item_id, target, quantity))
    return offers


def items_in_play(portfolio: PortfolioView, new_offers: Iterable[Offer]) -> set[int]:
    """Items with an open offer, unsold stock or a new offer, which should not be bought."""
    return (
        {o.item_id for o in portfolio.open_offers}
        | set(portfolio.holdings)
        | {o.item_id for o in new_offers}
    )


def spread_buys(
    candidates: Sequence[tuple[int, int]], portfolio: PortfolioView, sells: Sequence[Offer]
) -> list[Offer]:
    """Buy the best candidates first, splitting free coins evenly across free slots."""
    slots = portfolio.free_slots - len(sells)
    if slots <= 0:
        return []
    budget = portfolio.cash // slots
    offers = []
    for item_id, price in candidates:
        if len(offers) >= slots:
            break
        quantity = min(portfolio.buy_limit_remaining.get(item_id, 0), budget // price)
        if quantity > 0:
            offers.append(Offer(Side.BUY, item_id, price, quantity))
    return offers


def _latest_high(market: MarketView, item_id: int) -> int | None:
    window = market.latest(item_id)
    return window.avg_high_price if window is not None else None


def _just_closed(market: MarketView, item_id: int) -> PriceWindow | None:
    """The item's latest window, but only if it is the one that has just closed."""
    window = market.latest(item_id)
    if window is None or window.timestamp != market.now - market.timestep_seconds:
        return None
    return window


def _mean(values: Iterable[int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None
