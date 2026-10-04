"""Read dashboard form input into scan and backtest settings.

Bad values fall back to the default and are reported back, so a typo in a filter shows
a message instead of an error page.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from flipwatch.backtest import BacktestSettings
from flipwatch.coins import parse_coins
from flipwatch.runner import STRATEGIES, BacktestRequest
from flipwatch.scanner import NoLimitPolicy, ScanSettings

MEMBERSHIP = {"any": None, "members": True, "f2p": False}


@dataclass
class Form:
    """Raw values to show back in the form, and any problems found reading them."""

    values: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def number(self, args: Mapping[str, str], name: str, label: str, default: float) -> float:
        raw = args.get(name, "").strip().replace(",", "")
        self.values[name] = raw
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            value = -1
        if value < 0:
            self.errors.append(f"{label} must be a number of zero or more, so it was ignored.")
            return default
        return value

    def choice(self, args: Mapping[str, str], name: str, options: list[str], default: str) -> str:
        raw = args.get(name, default)
        value = raw if raw in options else default
        self.values[name] = value
        return value


def scan_settings(args: Mapping[str, str]) -> tuple[ScanSettings, Form]:
    defaults = ScanSettings()
    form = Form()
    min_margin = form.number(args, "min_margin", "Minimum margin", defaults.min_margin)
    min_profit = form.number(args, "min_profit", "Minimum profit", defaults.min_profit)
    min_roi = form.number(args, "min_roi", "Minimum ROI", defaults.min_roi * 100)
    min_volume = form.number(args, "min_volume", "Minimum volume", defaults.min_volume)
    max_price = form.number(args, "max_price", "Maximum buy price", 0)
    membership = form.choice(args, "membership", list(MEMBERSHIP), "any")
    no_limit = form.choice(args, "no_limit", [p.value for p in NoLimitPolicy], "skip")
    settings = ScanSettings(
        min_margin=int(min_margin),
        min_profit=int(min_profit),
        min_roi=min_roi / 100,
        min_volume=int(min_volume),
        max_buy_price=int(max_price) or None,
        members=MEMBERSHIP[membership],
        no_limit_policy=NoLimitPolicy(no_limit),
    )
    return settings, form


def backtest_request(args: Mapping[str, str]) -> tuple[BacktestRequest, Form]:
    defaults = BacktestSettings()
    form = Form()
    strategy = form.choice(args, "strategy", list(STRATEGIES), "margin")
    timestep = form.choice(args, "timestep", ["5m", "1h"], "5m")
    days = form.number(args, "days", "Days", 7)
    top = form.number(args, "top", "Top items", 50)
    fill_share = form.number(args, "fill_share", "Fill share", defaults.fill_share * 100)
    offer_hours = form.number(
        args, "offer_hours", "Offer hours", defaults.offer_lifetime_seconds / 3600
    )
    capital = _capital(args, form, defaults.starting_capital)
    item_ids = _item_ids(args, form)
    start = _date(args, form, "start", "Start date")

    if days < 1 or top < 1 or not 0 < fill_share <= 100 or offer_hours <= 0:
        form.errors.append(
            "Days and top items must be at least 1, fill share between 0 and 100 "
            "and offer hours above 0."
        )
        days, top, fill_share = 7, 50, defaults.fill_share * 100
        offer_hours = defaults.offer_lifetime_seconds / 3600

    request = BacktestRequest(
        strategy=strategy,
        timestep=timestep,
        days=int(days),
        start=start,
        item_ids=item_ids,
        top=int(top),
        settings=BacktestSettings(
            starting_capital=capital,
            fill_share=fill_share / 100,
            offer_lifetime_seconds=round(offer_hours * 3600),
        ),
    )
    return request, form


def _capital(args: Mapping[str, str], form: Form, default: int) -> int:
    raw = args.get("capital", "").strip()
    form.values["capital"] = raw
    if not raw:
        return default
    try:
        return parse_coins(raw)
    except ValueError:
        form.errors.append("Starting capital should look like 50m, 1.5b or 250000.")
        return default


def _item_ids(args: Mapping[str, str], form: Form) -> tuple[int, ...]:
    raw = args.get("items", "").strip()
    form.values["items"] = raw
    parts = raw.replace(",", " ").split()
    if not all(part.isdigit() for part in parts):
        form.errors.append("Items should be item ids separated by spaces or commas.")
        return ()
    return tuple(int(part) for part in parts)


def _date(args: Mapping[str, str], form: Form, name: str, label: str) -> int | None:
    raw = args.get(name, "").strip()
    form.values[name] = raw
    if not raw:
        return None
    try:
        return int(datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    except ValueError:
        form.errors.append(f"{label} should look like 2026-10-01.")
        return None
