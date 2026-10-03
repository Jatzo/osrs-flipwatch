import pytest

from flipwatch.coins import parse_coins


@pytest.mark.parametrize(
    ("text", "coins"),
    [
        ("50000000", 50_000_000),
        ("50m", 50_000_000),
        ("1.5b", 1_500_000_000),
        ("250K", 250_000),
        ("12,500", 12_500),
        (" 2m ", 2_000_000),
    ],
)
def test_parse_coins(text: str, coins: int) -> None:
    assert parse_coins(text) == coins


@pytest.mark.parametrize("text", ["", "lots", "5x", "-5m", "0", "0.0001k"])
def test_rejects_anything_else(text: str) -> None:
    with pytest.raises(ValueError):
        parse_coins(text)
