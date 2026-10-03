# OSRS Flipwatch

Finds profitable Grand Exchange flips in Old School RuneScape, backtests flipping strategies against historic prices and sends Discord alerts when a good margin opens up.

This project is in early development.

## Quick start

Python 3.12 or later is required.

```
python -m venv .venv
pip install -e ".[dev]"
cp .env.example .env
pytest
```

Set `FLIPWATCH_USER_AGENT` in `.env` before talking to the API. The OSRS Wiki blocks requests that do not identify the project and give a contact.

## Finding flips

```
flipwatch scan
flipwatch scan --sort roi --min-roi 2 --max-price 5000000
flipwatch scan --f2p --top 50
```

The scan compares the latest instant buy and instant sell price for every item. The idea is to place a buy offer at the instant sell price and a sell offer at the instant buy price, so the margin is the gap between them minus the 2% Grand Exchange tax, which is capped at 5,000,000 coins per item and waived for a short list of exempt items.

Items are skipped when either price is more than 10 minutes old, when the margin after tax is under 10 coins, when fewer than 50 trades happened on the thinner side of the market in the last hour, or when the potential profit is under 500,000 coins. A margin of a few coins vanishes the moment someone undercuts by one, and a small profit is not worth tying up an offer slot for four hours. Pass `--min-margin 0 --min-profit 0` to see everything. Items with no listed buy limit are skipped too, unless you pass `--no-limit-policy volume`. Old school bonds are always left out, because a bond bought on the Grand Exchange becomes untradeable and costs 10% of its value to make tradeable again.

Quantity is the lower of the buy limit and 10% of last hour's volume on the thinner side. A buy offer fills against people selling instantly and a sell offer fills against people buying instantly, so the quieter side is what limits a flip. Potential profit is margin times quantity.

Large margins on tiny volume are often traps, so each flip has a confidence score from 0 to 100. It is liquidity multiplied by stability. Liquidity rises with the logarithm of hourly volume and reaches full marks at 500 trades an hour. Stability measures how far the latest prices sit from last hour's averages and drops to zero once either side has moved 10%. A one coin difference is ignored so cheap items are not marked down for rounding.

Run `flipwatch scan --help` for every option.

## Collecting price history

Backtests need history, so the collector stores the five minute averages for every traded item in a local SQLite database.

```
flipwatch collect
flipwatch collect --once
flipwatch seed 4151 11802 --timestep 1h
flipwatch status
```

`flipwatch collect` runs until you stop it with Ctrl+C. It wakes 30 seconds after each five minute boundary, stores the window that has just closed and checks the last hour for gaps, fetching any window it missed. Use `--once` to run a single pass from cron or Task Scheduler instead. A window that the API has not published yet is left for the next run rather than stored as empty, so `flipwatch status` can report real gaps.

`flipwatch seed` fills in recent history for a few items from the timeseries endpoint, up to 365 windows each. It is limited to 10 items per call to keep requests to the wiki light.

Storing the whole market takes roughly 30 MB a day, so about 3 GB at the default retention of 90 days. Older data is deleted on each run. Change this with `FLIPWATCH_RETENTION_DAYS`, or set it to 0 to keep everything.

## Configuration

Settings come from environment variables or a `.env` file. See `.env.example` for the full list.

`FLIPWATCH_USER_AGENT` is required and should name the project and give a contact. `FLIPWATCH_DB_PATH` sets where the database lives (default `flipwatch.sqlite3`) and `FLIPWATCH_RETENTION_DAYS` sets how long price data is kept (default 90).

## Data source

Price data comes from the [OSRS Wiki real-time prices API](https://prices.runescape.wiki/).

This project is not affiliated with Jagex.

## Licence

MIT. See [LICENSE](LICENSE).
