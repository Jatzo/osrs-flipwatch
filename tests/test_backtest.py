import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import pytest

from flipwatch.backtest import (
    BacktestResult,
    BacktestSettings,
    MarketView,
    Offer,
    PortfolioView,
    Side,
    Strategy,
    max_drawdown,
    run_backtest,
)
from flipwatch.models import Item, PriceWindow
from flipwatch.strategies import DipBuy, MarginFlip

STEP = 300
T0 = 1_791_000_000
HOUR = 3600


def make_item(item_id: int = 1, buy_limit: int | None = 10_000) -> Item:
    return Item(
        id=item_id,
        name=f"Item {item_id}",
        members=True,
        buy_limit=buy_limit,
        value=100,
        high_alch=60,
        low_alch=40,
        icon=f"Item {item_id}.png",
    )


def make_window(
    timestamp: int,
    item_id: int = 1,
    low: int | None = 1_000,
    high: int | None = 1_100,
    low_volume: int = 1_000,
    high_volume: int = 1_000,
) -> PriceWindow:
    return PriceWindow(
        item_id=item_id,
        timestamp=timestamp,
        avg_high_price=high,
        high_volume=high_volume if high is not None else 0,
        avg_low_price=low,
        low_volume=low_volume if low is not None else 0,
    )


def steady(count: int, item_id: int = 1, **prices: int) -> list[PriceWindow]:
    return [make_window(T0 + i * STEP, item_id, **prices) for i in range(count)]


@dataclass
class Scripted:
    """Places fixed offers at fixed times and records what it was shown."""

    plan: dict[int, list[Offer]] = field(default_factory=dict)
    name: str = "scripted"
    seen: list[PortfolioView] = field(default_factory=list)

    def decide(self, market: MarketView, portfolio: PortfolioView) -> Sequence[Offer]:
        self.seen.append(portfolio)
        return self.plan.get(market.now, [])


def at(step: int) -> int:
    """The time at which the window `step` has just closed."""
    return T0 + (step + 1) * STEP


def run(
    strategy: Strategy,
    windows: list[PriceWindow],
    items: list[Item] | None = None,
    **settings: int | float,
) -> BacktestResult:
    item_map = {i.id: i for i in (items or [make_item()])}
    end = max(w.timestamp for w in windows) + STEP
    return run_backtest(strategy, windows, item_map, STEP, T0, end, BacktestSettings(**settings))


def buy(quantity: int, price: int = 1_000, item_id: int = 1) -> Offer:
    return Offer(Side.BUY, item_id, price, quantity)


def sell(quantity: int, price: int = 1_100, item_id: int = 1) -> Offer:
    return Offer(Side.SELL, item_id, price, quantity)


class TestFillModel:
    def test_buy_fills_in_the_next_window(self) -> None:
        result = run(Scripted({at(0): [buy(50)]}), steady(2))

        assert result.items[0].bought == 50

    def test_offer_placed_at_the_last_window_never_fills(self) -> None:
        result = run(Scripted({at(1): [buy(50)]}), steady(2))

        assert result.items[0].bought == 0

    def test_buy_needs_average_low_at_or_below_offer(self) -> None:
        windows = [make_window(T0), make_window(T0 + STEP, low=1_001)]
        assert run(Scripted({at(0): [buy(50)]}), windows).items[0].bought == 0

        windows = [make_window(T0), make_window(T0 + STEP, low=1_000)]
        assert run(Scripted({at(0): [buy(50)]}), windows).items[0].bought == 50

    def test_sell_needs_average_high_at_or_above_offer(self) -> None:
        plan = {at(0): [buy(50)], at(1): [sell(50, price=1_101)]}
        assert run(Scripted(plan), steady(3)).items[0].sold == 0

        plan = {at(0): [buy(50)], at(1): [sell(50, price=1_100)]}
        assert run(Scripted(plan), steady(3)).items[0].sold == 50

    def test_missing_side_does_not_fill(self) -> None:
        windows = [make_window(T0), make_window(T0 + STEP, low=None)]

        assert run(Scripted({at(0): [buy(50)]}), windows).items[0].bought == 0

    def test_fills_are_capped_by_share_of_volume(self) -> None:
        strategy = Scripted({at(0): [buy(250)]})

        result = run(strategy, steady(4, low_volume=1_000), fill_share=0.1)

        # 100, 100 then 50 across the three windows after the offer.
        assert result.items[0].bought == 250
        assert [p.holdings.get(1, 0) for p in strategy.seen] == [0, 100, 200, 250]

    def test_offers_on_the_same_item_share_the_volume(self) -> None:
        result = run(Scripted({at(0): [buy(80), buy(80)]}), steady(2), fill_share=0.1)

        assert result.items[0].bought == 100

    def test_rolling_buy_limit(self) -> None:
        windows_per_limit = (4 * HOUR) // STEP
        strategy = Scripted({at(0): [buy(250)]})

        result = run(
            strategy,
            steady(windows_per_limit + 3),
            items=[make_item(buy_limit=100)],
            offer_lifetime_seconds=10 * HOUR,
        )

        held = [p.holdings.get(1, 0) for p in strategy.seen]
        # The first purchase lands when window 1 closes, so the limit resets four hours later.
        assert held[2] == 100
        assert held[windows_per_limit] == 100
        assert held[windows_per_limit + 1] == 200
        assert result.items[0].bought == 200

    def test_sells_pay_tax(self) -> None:
        plan = {at(0): [buy(10, price=1_000)], at(1): [sell(10, price=2_000)]}

        result = run(Scripted(plan), steady(3, high=2_000))

        item = result.items[0]
        assert item.received == 10 * (2_000 - 40)
        assert item.realised_profit == 10 * (2_000 - 40) - 10 * 1_000
        assert result.realised_profit == 9_600


class TestBookkeeping:
    def test_buy_reserves_coins(self) -> None:
        strategy = Scripted({at(0): [buy(30, price=1_000_000)]})

        run(strategy, steady(2, low=2_000_000), starting_capital=50_000_000)

        assert strategy.seen[1].cash == 20_000_000

    def test_unaffordable_buy_is_rejected(self) -> None:
        plan = {at(0): [buy(40, price=1_000_000), buy(20, price=1_000_000)]}

        result = run(Scripted(plan), steady(2), starting_capital=50_000_000)

        assert result.offers_placed == 1
        assert result.offers_rejected == 1

    def test_expired_offer_returns_coins(self) -> None:
        lifetime_steps = HOUR // STEP
        strategy = Scripted({at(0): [buy(10, price=500)]})

        run(strategy, steady(lifetime_steps + 2), offer_lifetime_seconds=HOUR)

        cash = [p.cash for p in strategy.seen]
        # Placed when window 0 closed, so it expires exactly one hour later.
        assert cash[1] == 50_000_000 - 5_000
        assert cash[lifetime_steps - 1] == 50_000_000 - 5_000
        assert cash[lifetime_steps] == 50_000_000

    def test_expired_sell_returns_stock(self) -> None:
        strategy = Scripted({at(0): [buy(10)], at(1): [sell(10, price=5_000)]})

        run(strategy, steady(15), offer_lifetime_seconds=HOUR)

        held = [p.holdings.get(1, 0) for p in strategy.seen]
        assert held[2] == 0
        assert held[13] == 10

    def test_slot_limit(self) -> None:
        plan = {at(0): [buy(1), buy(1, item_id=2), buy(1, item_id=3)]}

        result = run(
            Scripted(plan), steady(2), items=[make_item(i) for i in (1, 2, 3)], max_open_offers=2
        )

        assert (result.offers_placed, result.offers_rejected) == (2, 1)

    def test_cannot_sell_what_is_not_held(self) -> None:
        result = run(Scripted({at(0): [sell(1)]}), steady(2))

        assert result.offers_rejected == 1

    def test_unknown_item_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="not in this backtest"):
            run(Scripted({at(0): [buy(1, item_id=99)]}), steady(2))

    def test_items_without_a_buy_limit_are_left_out(self) -> None:
        seen: list[set[int]] = []

        class Recorder(Scripted):
            def decide(self, market: MarketView, portfolio: PortfolioView) -> Sequence[Offer]:
                seen.append(set(market.items))
                return []

        windows = steady(2) + steady(2, item_id=2)
        run(Recorder(), windows, items=[make_item(1), make_item(2, buy_limit=None)])

        assert seen == [{1}, {1}]

    def test_invalid_offer_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            Offer(Side.BUY, 1, 0, 1)


class TestResults:
    def test_unsold_stock_is_reported_but_not_counted_as_profit(self) -> None:
        result = run(Scripted({at(0): [buy(10, price=1_000)]}), steady(3, low=900))

        assert result.realised_profit == 0
        assert result.held_stock_cost == 10_000
        assert result.held_stock_value == 10 * (900 - 18)
        assert result.final_cash == 50_000_000 - 10_000

    def test_fill_rate(self) -> None:
        strategy = Scripted({at(0): [buy(100)]})

        result = run(strategy, steady(3, low_volume=500), offer_lifetime_seconds=STEP)

        # One window fills 50 of the 100 offered before the offer expires.
        assert result.items[0].bought == 50
        assert result.items[0].fill_rate == 0.5
        assert result.fill_rate == 0.5

    def test_profit_per_hour_uses_the_whole_period(self) -> None:
        plan = {at(0): [buy(10)], at(1): [sell(10)]}

        result = run(Scripted(plan), steady(12))

        assert result.realised_profit == 10 * (1_100 - 22 - 1_000)
        assert result.profit_per_hour == pytest.approx(780 / 1.0)

    def test_equity_curve_has_a_point_per_window(self) -> None:
        result = run(Scripted(), steady(5))

        assert [t for t, _ in result.equity_curve] == [at(i) for i in range(5)]
        assert all(equity == 50_000_000 for _, equity in result.equity_curve)

    @pytest.mark.parametrize(
        ("equity", "expected"),
        [
            ([100, 120, 90, 130, 110], (30, 0.25)),
            ([100, 110, 120], (0, 0.0)),
            ([80, 90, 70], (30, 0.3)),
        ],
    )
    def test_max_drawdown(self, equity: list[int], expected: tuple[int, float]) -> None:
        curve = [(i, value) for i, value in enumerate(equity)]

        drawdown, share = max_drawdown(curve, starting_capital=100)

        assert (drawdown, share) == (expected[0], pytest.approx(expected[1]))

    def test_settings_are_validated(self) -> None:
        with pytest.raises(ValueError, match="fill_share"):
            BacktestSettings(fill_share=0)
        with pytest.raises(ValueError, match="starting_capital"):
            BacktestSettings(starting_capital=0)


def random_market(seed: int, items: int = 3, windows: int = 200) -> list[PriceWindow]:
    rng = random.Random(seed)
    result = []
    for item_id in range(1, items + 1):
        price = rng.randint(1_000, 50_000)
        for i in range(windows):
            price = max(100, int(price * rng.uniform(0.95, 1.05)))
            spread = int(price * rng.uniform(0.01, 0.08))
            result.append(
                make_window(
                    T0 + i * STEP,
                    item_id,
                    low=price,
                    high=price + spread,
                    low_volume=rng.randint(0, 3_000),
                    high_volume=rng.randint(0, 3_000),
                )
            )
    return result


@dataclass
class Recording:
    """Wraps a strategy and records each decision with the time it was made."""

    inner: MarginFlip | DipBuy
    decisions: list[tuple[int, list[Offer]]] = field(default_factory=list)
    check: Callable[[MarketView], None] | None = None

    @property
    def name(self) -> str:
        return self.inner.name

    def decide(self, market: MarketView, portfolio: PortfolioView) -> list[Offer]:
        if self.check:
            self.check(market)
        offers = self.inner.decide(market, portfolio)
        self.decisions.append((market.now, list(offers)))
        return offers


class TestNoLookahead:
    def test_strategy_only_sees_closed_windows(self) -> None:
        def check(market: MarketView) -> None:
            for item_id in market.items:
                history = market.history(item_id)
                assert all(w.timestamp + STEP <= market.now for w in history)
                assert all(
                    history[i].timestamp < history[i + 1].timestamp for i in range(len(history) - 1)
                )

        items = [make_item(i) for i in (1, 2, 3)]
        run(Recording(MarginFlip(), check=check), random_market(1), items=items)

    def test_history_cannot_be_read_past_its_end(self) -> None:
        def check(market: MarketView) -> None:
            history = market.history(1)
            with pytest.raises(IndexError):
                history[len(history)]
            assert history[-1:] == [history[len(history) - 1]] if history else True

        run(Recording(MarginFlip(), check=check), steady(5))

    @pytest.mark.parametrize("strategy", [MarginFlip(), DipBuy(lookback=6, dip=0.02)])
    def test_changing_the_future_does_not_change_past_decisions(
        self, strategy: MarginFlip | DipBuy
    ) -> None:
        market = random_market(7)
        cutoff = T0 + 120 * STEP
        # Wild prices from the cutoff on. If any of this leaked into earlier decisions,
        # those decisions would change.
        rewritten = [
            replace(w, avg_low_price=1, avg_high_price=10_000_000, low_volume=1, high_volume=1)
            if w.timestamp >= cutoff
            else w
            for w in market
        ]
        items = [make_item(i) for i in (1, 2, 3)]

        original = Recording(strategy)
        changed = Recording(strategy)
        run(original, market, items=items)
        run(changed, rewritten, items=items)

        before_cutoff = [(t, o) for t, o in original.decisions if t <= cutoff]
        assert any(offers for _, offers in before_cutoff)
        assert [(t, o) for t, o in changed.decisions if t <= cutoff] == before_cutoff
        assert changed.decisions != original.decisions
