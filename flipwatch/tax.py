"""Grand Exchange tax rules.

The seller pays the tax, per item, out of the sale price. The buyer pays nothing.
"""

from flipwatch.config import GE_TAX_CAP, GE_TAX_EXEMPT_ITEM_IDS, GE_TAX_RATE_PERCENT


def ge_tax(price: int, item_id: int | None = None) -> int:
    """Return the tax in coins on selling one item at `price`."""
    if price < 0:
        raise ValueError(f"price cannot be negative, got {price}")
    if item_id is not None and item_id in GE_TAX_EXEMPT_ITEM_IDS:
        return 0
    return min(price * GE_TAX_RATE_PERCENT // 100, GE_TAX_CAP)


def net_sale_proceeds(price: int, item_id: int | None = None) -> int:
    """Return what the seller receives for one item sold at `price`."""
    return price - ge_tax(price, item_id)
