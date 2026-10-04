"""Find and rank flip opportunities from the latest prices and last hour's volume."""

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from flipwatch.config import EXCLUDED_ITEM_IDS
from flipwatch.models import Confidence, Item, LatestPrice, Opportunity, PriceWindow
from flipwatch.tax import ge_tax


class NoLimitPolicy(StrEnum):
    """What to do with items that have no listed buy limit."""

    SKIP = "skip"
    VOLUME = "volume"


class SortKey(StrEnum):
    PROFIT = "profit"
    MARGIN = "margin"
    ROI = "roi"
    VOLUME = "volume"
    CONFIDENCE = "confidence"


@dataclass(frozen=True)
class ScanSettings:
    freshness_seconds: int = 10 * 60
    # Share of the thinner side's hourly volume a single flipper can expect to fill.
    volume_share: float = 0.10
    min_volume: int = 50
    # A margin of a few coins disappears as soon as someone undercuts by one coin.
    min_margin: int = 10
    min_roi: float = 0.0
    # Potential profit below this is not worth tying up an offer slot for four hours.
    min_profit: int = 500_000
    max_buy_price: int | None = None
    members: bool | None = None
    no_limit_policy: NoLimitPolicy = NoLimitPolicy.SKIP
    # Hourly volume on the thinner side at which liquidity scores full marks.
    liquidity_target: int = 500
    # Distance of the latest prices from the hourly averages at which stability scores zero.
    stability_tolerance: float = 0.10

    def __post_init__(self) -> None:
        if self.freshness_seconds <= 0:
            raise ValueError("freshness_seconds must be positive")
        if not 0 < self.volume_share <= 1:
            raise ValueError("volume_share must be greater than 0 and at most 1")
        if self.liquidity_target <= 0:
            raise ValueError("liquidity_target must be positive")
        if self.stability_tolerance <= 0:
            raise ValueError("stability_tolerance must be positive")


def scan(
    items: Mapping[int, Item],
    latest: Mapping[int, LatestPrice],
    hourly: Mapping[int, PriceWindow],
    settings: ScanSettings,
    now: float,
) -> list[Opportunity]:
    """Return every item that passes the filters, unranked."""
    opportunities = []
    for item_id, price in latest.items():
        item = items.get(item_id)
        window = hourly.get(item_id)
        if item is None or window is None:
            continue
        opportunity = evaluate(item, price, window, settings, now)
        if opportunity is not None:
            opportunities.append(opportunity)
    return opportunities


@dataclass(frozen=True)
class Margin:
    """What one flip of one item makes at the latest prices, after tax."""

    buy_price: int
    sell_price: int
    tax: int
    margin: int
    roi: float


def margin_at(item: Item, price: LatestPrice | None) -> Margin | None:
    """Work out the margin from the latest prices, or None without both sides."""
    if price is None or price.low is None or price.high is None or price.low <= 0:
        return None
    tax = ge_tax(price.high, item.id)
    margin = price.high - price.low - tax
    return Margin(price.low, price.high, tax, margin, margin / price.low)


def evaluate(
    item: Item,
    price: LatestPrice,
    window: PriceWindow,
    settings: ScanSettings,
    now: float,
) -> Opportunity | None:
    """Build an opportunity for one item, or return None if it fails a filter."""
    opportunity, _ = assess(item, price, window, settings, now)
    return opportunity


def assess(
    item: Item,
    price: LatestPrice | None,
    window: PriceWindow | None,
    settings: ScanSettings,
    now: float,
) -> tuple[Opportunity | None, str | None]:
    """Return the opportunity for one item, or None and the first filter it fails."""
    if item.id in EXCLUDED_ITEM_IDS:
        return None, "excluded from tracking"
    if settings.members is not None and item.members != settings.members:
        return None, "members item" if item.members else "free to play item"
    gap = margin_at(item, price)
    if price is None or gap is None:
        return None, "no recent instant buy and sell prices"
    if not _is_fresh(price, settings.freshness_seconds, now):
        return None, f"prices are older than {settings.freshness_seconds // 60} minutes"
    if settings.max_buy_price is not None and gap.buy_price > settings.max_buy_price:
        return None, f"buy price above {settings.max_buy_price:,} gp"
    if gap.margin <= 0:
        return None, "no margin after tax"
    if gap.margin < settings.min_margin:
        return None, f"margin under {settings.min_margin:,} gp"
    if gap.roi < settings.min_roi:
        return None, f"ROI under {settings.min_roi:.1%}"

    if window is None:
        return None, "no trades in the last hour"
    tradeable_volume = min(window.low_volume, window.high_volume)
    if tradeable_volume < settings.min_volume:
        return None, f"fewer than {settings.min_volume:,} trades an hour on the thinner side"

    quantity = realistic_quantity(item.buy_limit, tradeable_volume, settings)
    if quantity is None:
        return None, "no listed buy limit"
    if quantity == 0:
        return None, "too little volume to trade any"
    if gap.margin * quantity < settings.min_profit:
        return None, f"potential profit under {settings.min_profit:,} gp"

    opportunity = Opportunity(
        item=item,
        buy_price=gap.buy_price,
        sell_price=gap.sell_price,
        tax=gap.tax,
        margin=gap.margin,
        roi=gap.roi,
        low_volume=window.low_volume,
        high_volume=window.high_volume,
        quantity=quantity,
        potential_profit=gap.margin * quantity,
        confidence=confidence(price, window, settings),
    )
    return opportunity, None


def realistic_quantity(
    buy_limit: int | None, tradeable_volume: int, settings: ScanSettings
) -> int | None:
    """Return how many items one flipper can expect to trade, or None to skip the item."""
    by_volume = math.floor(tradeable_volume * settings.volume_share)
    if buy_limit is None:
        return by_volume if settings.no_limit_policy is NoLimitPolicy.VOLUME else None
    return min(buy_limit, by_volume)


def confidence(price: LatestPrice, window: PriceWindow, settings: ScanSettings) -> Confidence:
    """Score how far to trust a margin, rewarding volume and prices close to their average.

    Liquidity grows with the logarithm of the thinner side's hourly volume, so every
    tenfold increase adds the same amount, and it reaches 1 at the liquidity target.
    Stability is 1 when the latest prices are within a coin of last hour's averages and
    falls to 0 when either side has moved by the stability tolerance. A huge margin on a
    handful of trades, or one created by a sudden spike, scores low.
    """
    tradeable_volume = min(window.low_volume, window.high_volume)
    liquidity = min(
        1.0, math.log10(1 + tradeable_volume) / math.log10(1 + settings.liquidity_target)
    )

    drift = _largest_drift(price, window)
    stability = 0.0 if drift is None else max(0.0, 1 - drift / settings.stability_tolerance)

    return Confidence(
        score=round(100 * liquidity * stability),
        liquidity=liquidity,
        stability=stability,
    )


_SORT_KEYS: dict[SortKey, Callable[[Opportunity], float]] = {
    SortKey.PROFIT: lambda o: o.potential_profit,
    SortKey.MARGIN: lambda o: o.margin,
    SortKey.ROI: lambda o: o.roi,
    SortKey.VOLUME: lambda o: o.tradeable_volume,
    SortKey.CONFIDENCE: lambda o: o.confidence.score,
}


def rank(opportunities: Iterable[Opportunity], key: SortKey = SortKey.PROFIT) -> list[Opportunity]:
    """Sort best first. Ties fall back to potential profit, then name, so output is stable."""
    primary = _SORT_KEYS[key]
    return sorted(
        opportunities,
        key=lambda o: (-primary(o), -o.potential_profit, o.item.name),
    )


def _is_fresh(price: LatestPrice, freshness_seconds: int, now: float) -> bool:
    return all(
        traded_at is not None and now - traded_at <= freshness_seconds
        for traded_at in (price.high_time, price.low_time)
    )


def _largest_drift(price: LatestPrice, window: PriceWindow) -> float | None:
    pairs = ((price.high, window.avg_high_price), (price.low, window.avg_low_price))
    drifts = []
    for latest_price, average in pairs:
        if latest_price is None or not average:
            return None
        # Prices move in whole coins, so a one coin gap on a 3 gp item is rounding, not
        # a 33% swing. Ignoring one coin keeps cheap items from scoring as unstable.
        gap = max(0, abs(latest_price - average) - 1)
        drifts.append(gap / average)
    return max(drifts)
