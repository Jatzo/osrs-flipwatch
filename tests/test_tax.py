import pytest

from flipwatch.config import GE_TAX_CAP, GE_TAX_EXEMPT_ITEM_IDS
from flipwatch.tax import ge_tax

ABYSSAL_WHIP = 4151
OLD_SCHOOL_BOND = 13190

# The price at which 2% first reaches the 5,000,000 gp cap.
CAP_THRESHOLD = 250_000_000


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (0, 0),
        (1, 0),
        (49, 0),
        (50, 1),
        (99, 1),
        (100, 2),
        (818_095, 16_361),
    ],
)
def test_tax_is_two_percent_rounded_down(price: int, expected: int) -> None:
    assert ge_tax(price, ABYSSAL_WHIP) == expected


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (CAP_THRESHOLD - 50, GE_TAX_CAP - 1),
        (CAP_THRESHOLD, GE_TAX_CAP),
        (CAP_THRESHOLD + 50, GE_TAX_CAP),
        (9_199_000_000, GE_TAX_CAP),
    ],
)
def test_tax_is_capped_per_item(price: int, expected: int) -> None:
    assert ge_tax(price) == expected


def test_exempt_item_pays_no_tax() -> None:
    assert ge_tax(11_396_421, OLD_SCHOOL_BOND) == 0


def test_every_exempt_item_pays_no_tax() -> None:
    assert all(ge_tax(1_000_000, item_id) == 0 for item_id in GE_TAX_EXEMPT_ITEM_IDS)


def test_unknown_item_is_taxed() -> None:
    assert ge_tax(1_000) == 20


def test_negative_price_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        ge_tax(-1)


def test_selling_at_a_multiple_of_50_nets_the_same_as_one_coin_less() -> None:
    # The wiki notes that undercutting a price that is an exact multiple of 50 is free.
    assert 50 - ge_tax(50) == 49 - ge_tax(49) == 49
    assert 100 - ge_tax(100) == 99 - ge_tax(99) == 98
