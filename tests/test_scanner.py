from dataclasses import replace
from typing import Any

import pytest

from flipwatch.models import Item, LatestPrice, Opportunity, PriceWindow
from flipwatch.scanner import (
    Margin,
    NoLimitPolicy,
    ScanSettings,
    SortKey,
    assess,
    confidence,
    evaluate,
    margin_at,
    rank,
    realistic_quantity,
    scan,
)
from flipwatch.tax import ge_tax

NOW = 1_791_066_600
FRESH = NOW - 60
DEFAULTS = ScanSettings()
# The default margin and profit floors get their own tests. Everywhere else they are
# switched off so the examples can use small, easy to check numbers.
SETTINGS = replace(DEFAULTS, min_margin=0, min_profit=0)


def make_item(item_id: int = 1, buy_limit: int | None = 1_000, members: bool = True) -> Item:
    return Item(
        id=item_id,
        name=f"Item {item_id}",
        members=members,
        buy_limit=buy_limit,
        value=100,
        high_alch=60,
        low_alch=40,
        icon=f"Item {item_id}.png",
    )


def make_price(
    item_id: int = 1,
    high: int | None = 1_100,
    low: int | None = 1_000,
    high_time: int | None = FRESH,
    low_time: int | None = FRESH,
) -> LatestPrice:
    return LatestPrice(item_id=item_id, high=high, high_time=high_time, low=low, low_time=low_time)


def make_window(
    item_id: int = 1,
    avg_high: int | None = 1_100,
    avg_low: int | None = 1_000,
    high_volume: int = 5_000,
    low_volume: int = 5_000,
) -> PriceWindow:
    return PriceWindow(
        item_id=item_id,
        timestamp=NOW - 3_600,
        avg_high_price=avg_high,
        high_volume=high_volume,
        avg_low_price=avg_low,
        low_volume=low_volume,
    )


def evaluate_one(
    item: Item | None = None,
    price: LatestPrice | None = None,
    window: PriceWindow | None = None,
    settings: ScanSettings = SETTINGS,
) -> Opportunity | None:
    return evaluate(
        item or make_item(), price or make_price(), window or make_window(), settings, NOW
    )


class TestMetrics:
    def test_margin_roi_and_profit(self) -> None:
        opportunity = evaluate_one()

        assert opportunity is not None
        assert opportunity.tax == 22
        assert opportunity.margin == 1_100 - 1_000 - 22
        assert opportunity.roi == pytest.approx(78 / 1_000)
        assert opportunity.quantity == 500
        assert opportunity.potential_profit == 78 * 500

    def test_exempt_item_pays_no_tax(self) -> None:
        lobster = make_item(379)

        opportunity = evaluate_one(lobster, make_price(379), make_window(379))

        assert opportunity is not None
        assert opportunity.tax == 0
        assert opportunity.margin == 100

    def test_tax_is_capped_on_expensive_items(self) -> None:
        price = make_price(high=1_000_000_000, low=900_000_000)
        window = make_window(avg_high=1_000_000_000, avg_low=900_000_000)

        opportunity = evaluate_one(price=price, window=window)

        assert opportunity is not None
        assert opportunity.tax == 5_000_000
        assert opportunity.margin == 95_000_000


class TestFilters:
    def test_excluded_item_is_skipped(self) -> None:
        bond = make_item(13190)

        assert evaluate_one(bond, make_price(13190), make_window(13190)) is None

    def test_negative_margin_after_tax_is_skipped(self) -> None:
        assert evaluate_one(price=make_price(high=1_019, low=1_000)) is None

    def test_zero_margin_after_tax_is_skipped(self) -> None:
        assert evaluate_one(price=make_price(high=1_020, low=1_000)) is None

    def test_one_coin_margin_after_tax_is_kept(self) -> None:
        assert evaluate_one(price=make_price(high=1_020, low=999)) is not None

    def test_inverted_prices_are_skipped(self) -> None:
        assert evaluate_one(price=make_price(high=900, low=1_000)) is None

    @pytest.mark.parametrize("side", ["high_time", "low_time"])
    def test_stale_side_is_skipped(self, side: str) -> None:
        stale = replace(make_price(), **{side: NOW - DEFAULTS.freshness_seconds - 1})

        assert evaluate_one(price=stale) is None

    def test_price_at_the_freshness_limit_is_kept(self) -> None:
        edge = NOW - DEFAULTS.freshness_seconds

        assert evaluate_one(price=make_price(high_time=edge, low_time=edge)) is not None

    @pytest.mark.parametrize("side", ["high", "low"])
    def test_missing_side_is_skipped(self, side: str) -> None:
        missing = replace(make_price(), **{side: None, f"{side}_time": None})

        assert evaluate_one(price=missing) is None

    def test_thin_side_below_minimum_volume_is_skipped(self) -> None:
        window = make_window(high_volume=10_000, low_volume=DEFAULTS.min_volume - 1)

        assert evaluate_one(window=window) is None

    def test_min_margin(self) -> None:
        assert evaluate_one(settings=replace(SETTINGS, min_margin=78)) is not None
        assert evaluate_one(settings=replace(SETTINGS, min_margin=79)) is None

    def test_min_roi(self) -> None:
        assert evaluate_one(settings=replace(SETTINGS, min_roi=0.078)) is not None
        assert evaluate_one(settings=replace(SETTINGS, min_roi=0.079)) is None

    def test_min_profit(self) -> None:
        assert evaluate_one(settings=replace(SETTINGS, min_profit=39_000)) is not None
        assert evaluate_one(settings=replace(SETTINGS, min_profit=39_001)) is None

    def test_default_margin_floor_is_10_coins(self) -> None:
        settings = replace(DEFAULTS, min_profit=0)

        assert evaluate_one(price=make_price(high=1_030), settings=settings) is not None
        assert evaluate_one(price=make_price(high=1_029), settings=settings) is None

    def test_default_profit_floor_is_500k(self) -> None:
        big_seller = make_item(buy_limit=10_000)
        busy_window = make_window(high_volume=100_000, low_volume=100_000)

        assert evaluate_one(settings=DEFAULTS) is None
        assert evaluate_one(big_seller, window=busy_window, settings=DEFAULTS) is not None

    def test_max_buy_price(self) -> None:
        assert evaluate_one(settings=replace(SETTINGS, max_buy_price=1_000)) is not None
        assert evaluate_one(settings=replace(SETTINGS, max_buy_price=999)) is None

    @pytest.mark.parametrize(
        ("members_filter", "kept"), [(None, True), (True, True), (False, False)]
    )
    def test_members_filter(self, members_filter: bool | None, kept: bool) -> None:
        result = evaluate_one(
            make_item(members=True), settings=replace(SETTINGS, members=members_filter)
        )

        assert (result is not None) is kept

    def test_quantity_that_rounds_to_zero_is_skipped(self) -> None:
        settings = replace(SETTINGS, min_volume=0)
        window = make_window(high_volume=9, low_volume=9)

        assert evaluate_one(window=window, settings=settings) is None


class TestQuantity:
    def test_limited_by_buy_limit(self) -> None:
        assert realistic_quantity(100, 5_000, DEFAULTS) == 100

    def test_limited_by_volume_share(self) -> None:
        assert realistic_quantity(1_000, 5_000, DEFAULTS) == 500

    def test_volume_share_rounds_down(self) -> None:
        assert realistic_quantity(1_000, 59, DEFAULTS) == 5

    def test_no_limit_skipped_by_default(self) -> None:
        assert realistic_quantity(None, 5_000, DEFAULTS) is None
        assert evaluate_one(make_item(buy_limit=None)) is None

    def test_no_limit_capped_by_volume_when_configured(self) -> None:
        settings = replace(SETTINGS, no_limit_policy=NoLimitPolicy.VOLUME)

        assert realistic_quantity(None, 5_000, settings) == 500
        opportunity = evaluate_one(make_item(buy_limit=None), settings=settings)
        assert opportunity is not None
        assert opportunity.quantity == 500

    def test_uses_thinner_side_of_the_market(self) -> None:
        opportunity = evaluate_one(window=make_window(high_volume=8_000, low_volume=2_000))

        assert opportunity is not None
        assert opportunity.tradeable_volume == 2_000
        assert opportunity.quantity == 200


class TestConfidence:
    def test_full_marks_for_liquid_stable_item(self) -> None:
        result = confidence(make_price(), make_window(), DEFAULTS)

        assert result.score == 100
        assert result.liquidity == 1.0
        assert result.stability == 1.0

    def test_rises_with_volume(self) -> None:
        scores = [
            confidence(make_price(), make_window(high_volume=v, low_volume=v), DEFAULTS).score
            for v in (1, 10, 100, 500)
        ]

        assert scores == sorted(scores)
        assert scores[0] < scores[-1] == 100

    def test_falls_as_prices_drift_from_average(self) -> None:
        window = make_window(avg_high=1_100, avg_low=1_000)
        scores = [
            confidence(make_price(high=1_100, low=low), window, DEFAULTS).score
            for low in (1_000, 1_030, 1_060, 1_090)
        ]

        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 100

    def test_zero_when_drift_reaches_tolerance(self) -> None:
        window = make_window(avg_low=1_000)

        result = confidence(make_price(low=1_101), window, DEFAULTS)

        assert result.stability == 0.0
        assert result.score == 0

    def test_one_coin_gap_on_a_cheap_item_is_not_drift(self) -> None:
        window = make_window(avg_high=3, avg_low=2)

        result = confidence(make_price(high=4, low=3), window, DEFAULTS)

        assert result.stability == 1.0

    def test_zero_when_an_average_is_missing(self) -> None:
        window = make_window(avg_low=None, low_volume=0)

        assert confidence(make_price(), window, DEFAULTS).score == 0

    @pytest.mark.parametrize("volume", [0, 1, 50, 499, 500, 10_000_000])
    @pytest.mark.parametrize("low", [1, 500, 1_000, 1_500, 10_000])
    def test_score_stays_between_0_and_100(self, volume: int, low: int) -> None:
        window = make_window(high_volume=volume, low_volume=volume)

        result = confidence(make_price(low=low), window, DEFAULTS)

        assert 0 <= result.score <= 100


class TestRank:
    @pytest.fixture
    def opportunities(self) -> list[Any]:
        cheap_liquid = evaluate_one(
            make_item(1), make_price(1, high=110, low=100), make_window(1, 110, 100)
        )
        pricey_thin = evaluate_one(
            make_item(2, buy_limit=10),
            make_price(2, high=60_000, low=50_000),
            make_window(2, 60_000, 50_000, high_volume=60, low_volume=60),
        )
        middle = evaluate_one(make_item(3))
        return [cheap_liquid, pricey_thin, middle]

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            (SortKey.PROFIT, [2, 3, 1]),
            (SortKey.MARGIN, [2, 3, 1]),
            (SortKey.ROI, [2, 1, 3]),
            (SortKey.VOLUME, [3, 1, 2]),
            (SortKey.CONFIDENCE, [3, 1, 2]),
        ],
    )
    def test_sorts_best_first(
        self, opportunities: list[Any], key: SortKey, expected: list[int]
    ) -> None:
        assert [o.item.id for o in rank(opportunities, key)] == expected

    def test_ties_fall_back_to_profit(self, opportunities: list[Any]) -> None:
        cheap_liquid, _, middle = opportunities

        ranked = rank([cheap_liquid, middle], SortKey.CONFIDENCE)

        assert cheap_liquid.confidence.score == middle.confidence.score
        assert [o.item.id for o in ranked] == [3, 1]


def test_settings_are_validated() -> None:
    with pytest.raises(ValueError, match="volume_share"):
        ScanSettings(volume_share=0)
    with pytest.raises(ValueError, match="freshness_seconds"):
        ScanSettings(freshness_seconds=0)


def test_scan_over_real_fixtures(
    mapping_payload: list[dict[str, Any]],
    latest_payload: dict[str, Any],
    one_hour_payload: dict[str, Any],
) -> None:
    items = {r["id"]: Item.from_api(r) for r in mapping_payload}
    latest = {int(k): LatestPrice.from_api(int(k), v) for k, v in latest_payload["data"].items()}
    hourly = {
        int(k): PriceWindow.from_api(int(k), one_hour_payload["timestamp"], v)
        for k, v in one_hour_payload["data"].items()
    }

    ranked = rank(scan(items, latest, hourly, DEFAULTS, NOW))

    # The bond is excluded, the whip loses money after tax, the cannon base trades too
    # thinly, the 3rd Age items are stale and have no hourly volume, item 2660 is not in
    # the mapping, and the death rune and feather margins are too small to be worth it.
    assert [o.item.name for o in ranked] == ["Tormented synapse", "Noxious halberd"]

    synapse, halberd = ranked
    assert (synapse.margin, synapse.quantity, synapse.potential_profit) == (1_222_531, 5, 6_112_655)
    assert synapse.tax == ge_tax(synapse.sell_price)
    assert (halberd.margin, halberd.quantity, halberd.potential_profit) == (158_046, 5, 790_230)
    # The synapse has the bigger margin but trades thinly and has moved from its average.
    assert synapse.confidence.score < halberd.confidence.score

    without_floors = rank(scan(items, latest, hourly, SETTINGS, NOW))
    names = [o.item.name for o in without_floors]
    assert names == ["Tormented synapse", "Noxious halberd", "Death rune", "Feather"]
    death_rune, feather = without_floors[2:]
    assert (death_rune.tax, death_rune.margin, death_rune.quantity) == (3, 2, 25_000)
    assert (feather.tax, feather.margin, feather.confidence.score) == (0, 1, 100)


class TestSkipReasons:
    def reason(
        self,
        item: Item | None = None,
        price: LatestPrice | None = None,
        window: PriceWindow | None = None,
        settings: ScanSettings = SETTINGS,
        no_window: bool = False,
        no_price: bool = False,
    ) -> str | None:
        opportunity, reason = assess(
            item or make_item(),
            None if no_price else price or make_price(),
            None if no_window else window or make_window(),
            settings,
            NOW,
        )
        assert (opportunity is None) == (reason is not None)
        return reason

    def test_passing_item_has_no_reason(self) -> None:
        assert self.reason() is None

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"item": make_item(13190)}, "excluded from tracking"),
            ({"settings": replace(SETTINGS, members=False)}, "members item"),
            ({"no_price": True}, "no recent instant buy and sell prices"),
            (
                {"price": make_price(low=None, low_time=None)},
                "no recent instant buy and sell prices",
            ),
            ({"price": make_price(high_time=NOW - 601)}, "prices are older than 10 minutes"),
            ({"settings": replace(SETTINGS, max_buy_price=999)}, "buy price above 999 gp"),
            ({"price": make_price(high=1_020)}, "no margin after tax"),
            ({"settings": replace(SETTINGS, min_margin=79)}, "margin under 79 gp"),
            ({"settings": replace(SETTINGS, min_roi=0.1)}, "ROI under 10.0%"),
            ({"no_window": True}, "no trades in the last hour"),
            (
                {"window": make_window(low_volume=49)},
                "fewer than 50 trades an hour on the thinner side",
            ),
            ({"item": make_item(buy_limit=None)}, "no listed buy limit"),
            (
                {
                    "window": make_window(low_volume=9, high_volume=9),
                    "settings": replace(SETTINGS, min_volume=0),
                },
                "too little volume to trade any",
            ),
            ({"settings": DEFAULTS}, "potential profit under 500,000 gp"),
        ],
    )
    def test_reason_names_the_first_failed_filter(
        self, kwargs: dict[str, Any], expected: str
    ) -> None:
        assert self.reason(**kwargs) == expected

    def test_free_to_play_filter_names_the_item_type(self) -> None:
        reason = self.reason(make_item(members=False), settings=replace(SETTINGS, members=True))

        assert reason == "free to play item"


class TestMarginAt:
    def test_margin_after_tax(self) -> None:
        assert margin_at(make_item(), make_price()) == Margin(1_000, 1_100, 22, 78, 0.078)

    @pytest.mark.parametrize(
        "price", [None, make_price(low=None), make_price(high=None), make_price(low=0)]
    )
    def test_needs_both_sides(self, price: LatestPrice | None) -> None:
        assert margin_at(make_item(), price) is None
