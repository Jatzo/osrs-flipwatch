import pytest

from flipwatch.models import Confidence, Item, Opportunity
from flipwatch.planner import ROUND_HOURS, PlannedFlip, PlanSettings, make_plan


def flip(
    item_id: int, buy_price: int, margin: int, quantity: int, confidence: int = 80
) -> Opportunity:
    return Opportunity(
        item=Item(item_id, f"Item {item_id}", True, quantity, 1, 1, 1, ""),
        buy_price=buy_price,
        sell_price=buy_price + margin + 1,
        tax=1,
        margin=margin,
        roi=margin / buy_price,
        low_volume=10_000,
        high_volume=10_000,
        quantity=quantity,
        potential_profit=margin * quantity,
        confidence=Confidence(score=confidence, liquidity=1.0, stability=confidence / 100),
    )


def picked(plan_flips: list[PlannedFlip]) -> list[tuple[int, int]]:
    return [(f.opportunity.item.id, f.quantity) for f in plan_flips]


def test_takes_the_most_profitable_flips_that_fit() -> None:
    flips = [flip(1, 1_000, 50, 100), flip(2, 1_000, 30, 100), flip(3, 1_000, 40, 100)]

    plan = make_plan(flips, PlanSettings(capital=1_000_000, slots=2))

    assert picked(plan.flips) == [(1, 100), (3, 100)]
    assert plan.profit == 50 * 100 + 40 * 100
    assert plan.cash_used == 200_000
    assert plan.cash_left == 800_000


def test_shrinks_the_last_flip_to_the_cash_left() -> None:
    flips = [flip(1, 10_000, 500, 100), flip(2, 1_000, 10, 1_000)]

    plan = make_plan(flips, PlanSettings(capital=650_000, slots=8))

    # The first flip needs 1,000,000 for its full quantity, so it gets 65 instead.
    assert picked(plan.flips) == [(1, 65)]
    assert plan.cash_used <= 650_000


def test_cheap_liquid_flip_beats_an_expensive_one_when_cash_is_short() -> None:
    expensive = flip(1, 5_000_000, 200_000, 8)
    cheap = flip(2, 1_000, 40, 10_000)

    plan = make_plan([expensive, cheap], PlanSettings(capital=4_000_000, slots=1))

    # The expensive item cannot be afforded even once, so the slot goes to the cheap one.
    assert picked(plan.flips) == [(2, 4_000)]


def test_uses_the_rest_of_the_cash_on_later_slots() -> None:
    flips = [flip(1, 1_000_000, 100_000, 3), flip(2, 1_000, 20, 50_000)]

    plan = make_plan(flips, PlanSettings(capital=3_500_000, slots=2))

    assert picked(plan.flips) == [(1, 3), (2, 500)]
    assert plan.cash_left == 0


def test_leaves_out_low_confidence_flips() -> None:
    flips = [flip(1, 1_000, 900, 100, confidence=39), flip(2, 1_000, 10, 100, confidence=40)]

    plan = make_plan(flips, PlanSettings(min_confidence=40))

    assert picked(plan.flips) == [(2, 100)]


def test_respects_the_slot_count() -> None:
    flips = [flip(i, 100, 10, 10) for i in range(1, 12)]

    assert len(make_plan(flips, PlanSettings(slots=3)).flips) == 3
    assert len(make_plan(flips, PlanSettings()).flips) == 8


def test_each_item_takes_one_slot_at_most() -> None:
    plan = make_plan([flip(1, 100, 10, 10)], PlanSettings())

    assert picked(plan.flips) == [(1, 10)]


def test_empty_when_nothing_is_affordable_or_available() -> None:
    assert make_plan([], PlanSettings()).flips == []
    assert make_plan([flip(1, 2_000_000, 1, 5)], PlanSettings(capital=1_000_000)).flips == []


def test_profit_per_hour_spreads_a_round_over_the_buy_limit_window() -> None:
    plan = make_plan([flip(1, 1_000, 100, 400)], PlanSettings())

    assert ROUND_HOURS == 4
    assert plan.profit_per_hour == pytest.approx(40_000 / 4)


@pytest.mark.parametrize(
    "kwargs", [{"capital": 0}, {"slots": 0}, {"min_confidence": 101}, {"min_confidence": -1}]
)
def test_settings_are_validated(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        PlanSettings(**kwargs)
