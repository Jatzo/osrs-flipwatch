"""Command line entry point."""

import argparse
import sys
import time
from collections.abc import Callable, Sequence

from flipwatch.api import ApiError, PricesClient
from flipwatch.config import ConfigError, Settings, load_settings
from flipwatch.models import Opportunity
from flipwatch.scanner import NoLimitPolicy, ScanSettings, SortKey, rank, scan

ClientFactory = Callable[[Settings], PricesClient]
Clock = Callable[[], float]

MAX_NAME_WIDTH = 30


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = PricesClient,
    clock: Clock = time.time,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        with client_factory(settings) as client:
            return args.run(args, client, clock())
    except (ConfigError, ApiError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flipwatch", description="Find Grand Exchange flips in Old School RuneScape."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    defaults = ScanSettings()
    scan_parser = commands.add_parser(
        "scan",
        help="list the best flips available right now",
        description="List the best flips available right now, ranked by potential profit.",
    )
    scan_parser.add_argument(
        "--top", type=_positive_int, default=20, help="number of rows to show (default 20)"
    )
    scan_parser.add_argument(
        "--sort",
        type=SortKey,
        choices=list(SortKey),
        default=SortKey.PROFIT,
        help="column to rank by (default profit)",
    )
    scan_parser.add_argument(
        "--min-volume",
        type=_non_negative_int,
        default=defaults.min_volume,
        help="minimum hourly trades on the thinner side of the market "
        f"(default {defaults.min_volume})",
    )
    scan_parser.add_argument(
        "--min-margin",
        type=_non_negative_int,
        default=defaults.min_margin,
        help=f"minimum margin per item after tax, in coins (default {defaults.min_margin})",
    )
    scan_parser.add_argument(
        "--min-profit",
        type=_non_negative_int,
        default=defaults.min_profit,
        help="minimum potential profit per buy limit window, in coins "
        f"(default {defaults.min_profit:,})",
    )
    scan_parser.add_argument(
        "--min-roi",
        type=_non_negative_float,
        default=0.0,
        metavar="PERCENT",
        help="minimum return on the buy price, as a percentage",
    )
    scan_parser.add_argument(
        "--max-price",
        type=_positive_int,
        default=None,
        help="highest buy price to consider, in coins",
    )
    membership = scan_parser.add_mutually_exclusive_group()
    membership.add_argument(
        "--members", dest="members", action="store_const", const=True, help="members items only"
    )
    membership.add_argument(
        "--f2p", dest="members", action="store_const", const=False, help="free to play items only"
    )
    scan_parser.add_argument(
        "--no-limit-policy",
        type=NoLimitPolicy,
        choices=list(NoLimitPolicy),
        default=defaults.no_limit_policy,
        help="skip items with no listed buy limit, or cap them by volume alone (default skip)",
    )
    scan_parser.set_defaults(run=run_scan)
    return parser


def run_scan(args: argparse.Namespace, client: PricesClient, now: float) -> int:
    settings = ScanSettings(
        min_volume=args.min_volume,
        min_margin=args.min_margin,
        min_profit=args.min_profit,
        min_roi=args.min_roi / 100,
        max_buy_price=args.max_price,
        members=args.members,
        no_limit_policy=args.no_limit_policy,
    )
    opportunities = rank(
        scan(client.mapping(), client.latest(), client.one_hour(), settings, now), args.sort
    )
    if not opportunities:
        print("No flips match the current filters.")
        return 0

    shown = opportunities[: args.top]
    print(format_table(shown))
    print()
    print(f"Showing {len(shown)} of {len(opportunities)} flips, ranked by {args.sort}.")
    print("Conf is liquidity multiplied by stability, each scored out of 100.")
    return 0


_COLUMNS: list[tuple[str, Callable[[Opportunity], str]]] = [
    ("Item", lambda o: _truncate(o.item.name, MAX_NAME_WIDTH)),
    ("Buy", lambda o: f"{o.buy_price:,}"),
    ("Sell", lambda o: f"{o.sell_price:,}"),
    ("Margin", lambda o: f"{o.margin:,}"),
    ("ROI", lambda o: f"{o.roi:.1%}"),
    ("Limit", lambda o: "none" if o.item.buy_limit is None else f"{o.item.buy_limit:,}"),
    ("Vol/h", lambda o: f"{o.tradeable_volume:,}"),
    ("Qty", lambda o: f"{o.quantity:,}"),
    ("Profit", lambda o: f"{o.potential_profit:,}"),
    ("Conf", lambda o: str(o.confidence.score)),
    ("Liq", lambda o: str(round(100 * o.confidence.liquidity))),
    ("Stab", lambda o: str(round(100 * o.confidence.stability))),
]


def format_table(opportunities: Sequence[Opportunity]) -> str:
    """Lay out opportunities as an aligned text table. Text is left aligned, numbers right."""
    headers = [header for header, _ in _COLUMNS]
    rows = [[cell(o) for _, cell in _COLUMNS] for o in opportunities]
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        first, *rest = cells
        parts = [first.ljust(widths[0])]
        parts += [value.rjust(width) for value, width in zip(rest, widths[1:], strict=True)]
        return "  ".join(parts).rstrip()

    rule = "  ".join("-" * width for width in widths)
    return "\n".join([line(headers), rule, *(line(row) for row in rows)])


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than zero, got {value}")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"cannot be negative, got {value}")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"cannot be negative, got {value}")
    return number
