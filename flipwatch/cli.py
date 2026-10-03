"""Command line entry point."""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from flipwatch import collector
from flipwatch.api import TIMESTEP_SECONDS, ApiError, PricesClient
from flipwatch.config import ConfigError, Settings, load_settings
from flipwatch.models import Opportunity
from flipwatch.scanner import NoLimitPolicy, ScanSettings, SortKey, rank, scan
from flipwatch.store import Store, StoreError

ClientFactory = Callable[[Settings], PricesClient]
Clock = Callable[[], float]

MAX_NAME_WIDTH = 30

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    """What a command needs from the outside world, so tests can swap it out."""

    settings: Settings
    client_factory: ClientFactory
    clock: Clock


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = PricesClient,
    clock: Clock = time.time,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = Runtime(load_settings(), client_factory, clock)
        return args.run(args, runtime)
    except (ConfigError, ApiError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flipwatch", description="Find Grand Exchange flips in Old School RuneScape."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _add_scan_command(commands)
    _add_collect_command(commands)
    _add_seed_command(commands)
    _add_status_command(commands)
    return parser


def _add_scan_command(commands: argparse._SubParsersAction) -> None:
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


def _add_collect_command(commands: argparse._SubParsersAction) -> None:
    collect_parser = commands.add_parser(
        "collect",
        help="store five minute price snapshots for backtesting",
        description="Store five minute price snapshots every five minutes until stopped. "
        "Each run also fills gaps from the last hour.",
    )
    collect_parser.add_argument(
        "--once",
        action="store_true",
        help="collect once and exit, for running from cron or Task Scheduler",
    )
    collect_parser.set_defaults(run=run_collect)


def _add_seed_command(commands: argparse._SubParsersAction) -> None:
    seed_parser = commands.add_parser(
        "seed",
        help="store recent history for a few items",
        description="Store up to 365 recent windows for each item from the timeseries "
        f"endpoint. At most {collector.MAX_SEED_ITEMS} items at a time.",
    )
    seed_parser.add_argument("item_ids", nargs="+", type=_positive_int, metavar="ITEM_ID")
    seed_parser.add_argument(
        "--timestep",
        choices=list(TIMESTEP_SECONDS),
        default="5m",
        help="window size to fetch (default 5m)",
    )
    seed_parser.set_defaults(run=run_seed)


def _add_status_command(commands: argparse._SubParsersAction) -> None:
    status_parser = commands.add_parser(
        "status",
        help="show what the database holds",
        description="Show how much price data is stored and whether any windows are missing.",
    )
    status_parser.set_defaults(run=run_status)


def run_scan(args: argparse.Namespace, runtime: Runtime) -> int:
    settings = ScanSettings(
        min_volume=args.min_volume,
        min_margin=args.min_margin,
        min_profit=args.min_profit,
        min_roi=args.min_roi / 100,
        max_buy_price=args.max_price,
        members=args.members,
        no_limit_policy=args.no_limit_policy,
    )
    with runtime.client_factory(runtime.settings) as client:
        found = scan(
            client.mapping(), client.latest(), client.one_hour(), settings, runtime.clock()
        )
    opportunities = rank(found, args.sort)
    if not opportunities:
        print("No flips match the current filters.")
        return 0

    shown = opportunities[: args.top]
    print(format_table(shown))
    print()
    noun = "flip" if len(opportunities) == 1 else "flips"
    print(f"Showing {len(shown)} of {len(opportunities)} {noun}, ranked by {args.sort}.")
    print("Conf is liquidity multiplied by stability, each scored out of 100.")
    return 0


def run_collect(args: argparse.Namespace, runtime: Runtime) -> int:
    _configure_logging()
    settings = runtime.settings
    with runtime.client_factory(settings) as client, Store.open(settings.db_path) as store:
        if args.once:
            now = runtime.clock()
            collector.collect_once(client, store, now)
            collector.prune(store, now, settings.retention_days)
            return 0
        log.info("Collecting into %s. Press Ctrl+C to stop.", settings.db_path)
        try:
            collector.run_forever(client, store, settings.retention_days, clock=runtime.clock)
        except KeyboardInterrupt:
            log.info("Stopped")
    return 0


def run_seed(args: argparse.Namespace, runtime: Runtime) -> int:
    _configure_logging()
    settings = runtime.settings
    with runtime.client_factory(settings) as client, Store.open(settings.db_path) as store:
        try:
            added = collector.seed(client, store, args.item_ids, args.timestep)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    count = len(args.item_ids)
    noun = "item" if count == 1 else "items"
    print(f"Added {added:,} new {args.timestep} rows for {count} {noun}.")
    return 0


def run_status(args: argparse.Namespace, runtime: Runtime) -> int:
    db_path = runtime.settings.db_path
    if not Path(db_path).exists():
        print(f"No database at {db_path} yet. Run flipwatch collect to start one.")
        return 1

    with Store.open(db_path) as store:
        summaries = [store.summary(timestep) for timestep in TIMESTEP_SECONDS]

    print(f"Database: {db_path}")
    snapshots = summaries[0]
    if snapshots.first_timestamp is None or snapshots.last_timestamp is None:
        print("Five minute snapshots: none yet")
    else:
        first = collector.format_timestamp(snapshots.first_timestamp)
        last = collector.format_timestamp(snapshots.last_timestamp)
        print(f"Five minute snapshots: {snapshots.snapshot_count:,}, from {first} to {last}")
        print(f"Missing windows: {snapshots.missing_count:,}")
    for summary in summaries:
        if summary.row_count:
            print(f"Stored {summary.timestep} rows: {summary.row_count:,}")
    return 0


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request at INFO, which would double the output of each run.
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
