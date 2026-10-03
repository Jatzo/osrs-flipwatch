"""Read amounts of coins written the way players write them."""

import re

_SUFFIXES = {"": 1, "k": 10**3, "m": 10**6, "b": 10**9}
_AMOUNT = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kmb]?)\s*")


def parse_coins(value: str) -> int:
    """Read an amount such as 50000000, 50m, 250k or 1.5b."""
    match = _AMOUNT.fullmatch(value.lower().replace(",", ""))
    if match is None:
        raise ValueError(f"not an amount of coins: {value}")
    number, suffix = match.groups()
    coins = round(float(number) * _SUFFIXES[suffix])
    if coins <= 0:
        raise ValueError(f"must be greater than zero, got {value}")
    return coins
