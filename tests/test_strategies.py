from collections.abc import Mapping, Sequence

from flipwatch.backtest import MarketView, Offer, OpenOfferInfo, PortfolioView, Side
from flipwatch.models import Item, PriceWindow
from flipwatch.strategies import DipBuy, MarginFlip, items_in_play, sell_holdings, spread_buys

STEP = 300
NOW = 1_791_000_000


def make_item(item_id: int, buy_limit: int = 10_000) -> Item:
    return Item(
        id=item_id,
        name=f"Item {item_id}",
        members=True,
        buy_limit=buy_limit,
        value=100,
        high_alch=60,
        low_alch=40,
        icon="",
    )


def window(
    item_id: int, steps_ago: int = 1, low: int | None = 1_000, high: int | None = 1_100,
    volume: int = 1_000,
) -> PriceWindow:  # fmt: skip
    return PriceWindow(
        item_id=item_id,
        timestamp=NOW - steps_ago * STEP,
        avg_high_price=high,
        high_volume=volume,
        avg_low_price=low,
        low_volume=volume,
    )


def market(histories: Mapping[int, list[PriceWindow]]) -> MarketView:
    items = {item_id: make_item(item_id) for item_id in histories}
    return MarketView(
        NOW, STEP, items, {k: sorted(v, key=lambda w: w.timestamp) for k, v in histories.items()}
    )


def portfolio(
    cash: int = 10_000_000,
    holdings: Mapping[int, int] | None = None,
    open_offers: Sequence[OpenOfferInfo] = (),
    free_slots: int = 8,
    limits: Mapping[int, int] | None = None,
) -> PortfolioView:
    return PortfolioView(
        cash=cash,
        holdings=holdings or {},
        open_offers=list(open_offers),
        free_slots=free_slots,
        buy_limit_remaining=limits or {i: 10_000 for i in range(1, 10)},
    )


def sides(offers: Sequence[Offer]) -> list[tuple[Side, int, int]]:
    return [(o.side, o.item_id, o.price) for o in offers]


class TestMarginFlip:
    def test_buys_at_average_low_when_margin_clears_the_floors(self) -> None:
        offers = MarginFlip().decide(market({1: [window(1)]}), portfolio())

        assert sides(offers) == [(Side.BUY, 1, 1_000)]

    def test_skips_margin_below_floor_after_tax(self) -> None:
        # 1,030 - 1,000 - 20 tax leaves 10, under a floor of 11.
        offers = MarginFlip(min_margin=11, min_roi=0).decide(
            market({1: [window(1, high=1_030)]}), portfolio()
        )

        assert offers == []

    def test_skips_thin_markets(self) -> None:
        offers = MarginFlip(min_volume=50).decide(market({1: [window(1, volume=49)]}), portfolio())

        assert offers == []

    def test_skips_items_without_a_fresh_window(self) -> None:
        offers = MarginFlip().decide(market({1: [window(1, steps_ago=2)]}), portfolio())

        assert offers == []

    def test_prefers_the_best_margin_weighted_by_volume(self) -> None:
        histories = {
            1: [window(1, high=1_100, volume=100)],
            2: [window(2, high=1_100, volume=5_000)],
            # Margins after tax: 78, 78 and 470. Scores: 7,800, 390,000 and 470,000.
            3: [window(3, high=1_500, volume=1_000)],
        }

        offers = MarginFlip().decide(market(histories), portfolio(free_slots=2))

        assert [o.item_id for o in offers] == [3, 2]

    def test_sells_held_stock_at_average_high(self) -> None:
        offers = MarginFlip().decide(market({1: [window(1)]}), portfolio(holdings={1: 40}))

        assert sides(offers) == [(Side.SELL, 1, 1_100)]
        assert offers[0].quantity == 40

    def test_does_not_buy_an_item_with_an_open_offer(self) -> None:
        open_buy = OpenOfferInfo(Side.BUY, 1, 1_000, 10, NOW - STEP)

        offers = MarginFlip().decide(market({1: [window(1)]}), portfolio(open_offers=[open_buy]))

        assert offers == []


class TestDipBuy:
    def history(self, item_id: int, lows: list[int], high: int = 1_100) -> list[PriceWindow]:
        return [
            window(item_id, steps_ago=len(lows) - i, low=low, high=high)
            for i, low in enumerate(lows)
        ]

    def test_buys_a_dip_below_the_recent_mean(self) -> None:
        lows = [1_000] * 5 + [940]

        offers = DipBuy(lookback=6, dip=0.03).decide(
            market({1: self.history(1, lows)}), portfolio()
        )

        assert sides(offers) == [(Side.BUY, 1, 940)]

    def test_ignores_a_small_dip(self) -> None:
        lows = [1_000] * 5 + [990]

        offers = DipBuy(lookback=6, dip=0.03).decide(
            market({1: self.history(1, lows)}), portfolio()
        )

        assert offers == []

    def test_waits_for_a_full_lookback(self) -> None:
        lows = [1_000] * 3 + [500]

        offers = DipBuy(lookback=6, dip=0.03).decide(
            market({1: self.history(1, lows)}), portfolio()
        )

        assert offers == []

    def test_sells_at_mean_average_high(self) -> None:
        history = self.history(1, [1_000] * 6, high=1_200)

        offers = DipBuy(lookback=6).decide(market({1: history}), portfolio(holdings={1: 5}))

        assert sides(offers) == [(Side.SELL, 1, 1_200)]


class TestHelpers:
    def test_spread_buys_splits_coins_across_free_slots(self) -> None:
        offers = spread_buys([(1, 1_000), (2, 2_000)], portfolio(cash=100_000, free_slots=2), [])

        assert [(o.item_id, o.quantity) for o in offers] == [(1, 50), (2, 25)]

    def test_spread_buys_respects_buy_limit(self) -> None:
        offers = spread_buys([(1, 10)], portfolio(cash=100_000, limits={1: 7}), [])

        assert offers[0].quantity == 7

    def test_spread_buys_leaves_room_for_sells(self) -> None:
        sell = Offer(Side.SELL, 3, 100, 1)

        offers = spread_buys([(1, 10), (2, 10)], portfolio(free_slots=2), [sell])

        assert [o.item_id for o in offers] == [1]

    def test_sell_holdings_stops_at_free_slots(self) -> None:
        view = market({1: [window(1)], 2: [window(2)]})

        offers = sell_holdings(view, portfolio(holdings={1: 1, 2: 1}, free_slots=1),
                               lambda m, i: 1_000)  # fmt: skip

        assert len(offers) == 1

    def test_items_in_play(self) -> None:
        open_buy = OpenOfferInfo(Side.BUY, 1, 1_000, 10, NOW)
        view = portfolio(holdings={2: 1}, open_offers=[open_buy])

        assert items_in_play(view, [Offer(Side.SELL, 3, 10, 1)]) == {1, 2, 3}
