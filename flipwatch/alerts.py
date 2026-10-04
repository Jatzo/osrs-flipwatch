"""Raise alerts for flips that meet the alert rules.

The dashboard calls `check_for_alerts` about once a minute while it is open. Alerts are
saved in the database, which also enforces the per-item cooldown.
"""

from collections.abc import Collection, Iterable, Mapping
from typing import Protocol

from flipwatch.config import AlertSettings
from flipwatch.models import Alert, Item, LatestPrice, Opportunity, PriceWindow
from flipwatch.scanner import ScanSettings, assess, rank, scan
from flipwatch.store import Store


class PriceSource(Protocol):
    def mapping(self) -> Mapping[int, Item]: ...

    def latest(self) -> Mapping[int, LatestPrice]: ...

    def one_hour(self) -> Mapping[int, PriceWindow]: ...


def scan_settings(rules: AlertSettings) -> ScanSettings:
    return ScanSettings(
        min_margin=rules.min_margin,
        min_profit=rules.min_profit,
        min_roi=rules.min_roi,
        min_volume=rules.min_volume,
    )


def select_alerts(
    opportunities: Iterable[Opportunity],
    rules: AlertSettings,
    watchlist: Collection[int] | None = None,
) -> list[Opportunity]:
    """Keep the flips confident enough to alert on, most profitable first.

    A watchlist limits alerts to those items. None means every item may alert.
    """
    return [
        o
        for o in rank(opportunities)
        if o.confidence.score >= rules.min_confidence
        and (watchlist is None or o.item.id in watchlist)
    ]


def current_status(
    item: Item,
    price: LatestPrice | None,
    window: PriceWindow | None,
    rules: AlertSettings,
    now: float,
) -> tuple[Opportunity | None, str | None]:
    """Whether an item would alert right now, or the first rule it fails.

    The watchlist and cooldown are left out: this answers whether the flip still holds.
    """
    opportunity, reason = assess(item, price, window, scan_settings(rules), now)
    if opportunity is not None and opportunity.confidence.score < rules.min_confidence:
        return None, f"confidence under {rules.min_confidence}"
    return opportunity, reason


def check_for_alerts(
    client: PriceSource, store: Store, rules: AlertSettings, now: float
) -> list[Alert]:
    """Scan, apply the rules and save new alerts. Returns only the alerts just raised."""
    opportunities = scan(
        client.mapping(), client.latest(), client.one_hour(), scan_settings(rules), now
    )
    watchlist = set(store.watchlist()) if rules.watchlist_only else None
    raised = []
    for opportunity in select_alerts(opportunities, rules, watchlist):
        alert = store.save_alert(
            opportunity, created_at=int(now), cooldown_seconds=rules.cooldown_minutes * 60
        )
        if alert is not None:
            raised.append(alert)
    return raised
