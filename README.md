# OSRS Flipwatch

Finds profitable Grand Exchange flips in Old School RuneScape, backtests flipping strategies against historic prices and alerts you in a dashboard when a good margin opens up.

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

## Backtesting

```
flipwatch backtest
flipwatch backtest --strategy dip --days 3 --capital 20m
flipwatch backtest --items 4151 11802 --timestep 1h --start 2026-09-01
```

A backtest replays stored price history through a strategy and reports realised profit, profit per hour, fill rate, peak capital used, maximum drawdown and a breakdown per item. Each run is saved to the database. By default it trades the 50 items with the most volume in the period, starting with 50,000,000 coins.

Two example strategies are included. `margin` buys at the last window's average low price and sells at its average high when the gap clears 10 coins and 1% after tax. `dip` buys when the average low falls 3% below its mean over the last 24 windows and sells at the mean average high. Writing your own means implementing one method that takes the market and your portfolio and returns the offers to place.

A strategy only ever sees windows that have already closed. The data it is handed simply does not contain anything later, and a test rewrites all prices after a cutoff and fails if any decision before the cutoff changes.

### Fill model assumptions

The stored data holds average prices and volumes for each window, not individual trades, so fills have to be estimated. The model leans towards pessimism:

- An offer can only fill in windows after the one it was placed in.
- A buy fills only in a window whose average low price is at or below the offer price, and a sell only in a window whose average high is at or above it.
- An offer can take at most 10% of the volume on the matching side of each window, shared between all offers on that item. Change this with `--fill-share`.
- Fills happen at the offer price, never better.
- Buy limits apply over a rolling four hours from the first purchase. Items without a listed limit are left out.
- Coins for a buy are set aside when the offer is placed, sales pay the 2% tax, and only stock you hold can be sold. At most 8 offers can be open at once.
- Unfilled offers are cancelled after 4 hours. Change this with `--offer-hours`.
- Stock still held at the end is reported at its last average low price after tax and is not counted as profit.

### Limitations

A backtest is an estimate, not a promise. Averages hide the spread of prices inside a window, so a real offer may fill more or less than the model says. Other flippers compete for the same volume, prices react to your own trades on thin items, and the buy limits come from the wiki rather than the game itself. The model also has no idea about game updates or news that moved prices in the past. Use the results to compare strategies with each other, not to predict what you will earn.

## Dashboard

```
flask --app flipwatch.web run
```

Then open http://127.0.0.1:5000. The dashboard has four pages. Opportunities shows the scan as a table you can filter and sort by clicking any column. Each item has its own page with a chart of average high and low prices, volume underneath, the current margin numbers and, when it is not listed as a flip, the filter it failed. The watchlist keeps items you want to follow, with their numbers whether or not they currently pass the filters. Backtests lists saved runs with their equity curve and per item results, and has a form to start a new run.

Charts use your stored history when it covers the chosen range, and otherwise make a single timeseries request to the wiki for that item, cached for five minutes. The dashboard is meant to run on your own machine. It has no login, so do not expose it to a network you do not trust.

## Alerts

While any dashboard page is open, it checks for new flips once a minute. When a flip meets the alert rules, the Alerts link in the header shows an unread count and a banner appears on whichever page you are on. On the Alerts page you can also turn on desktop notifications, so alerts reach you while the tab is in the background.

An alert needs a margin of at least 10 coins, 1% ROI, 500,000 coins of potential profit, 50 trades an hour on the thinner side and a confidence of 40, all configurable. You can also limit alerts to items on your watchlist. Each item alerts at most once an hour, and the Alerts page lists every alert beside the item's current numbers, so you can see whether the margin still holds. Alerts are only raised while a dashboard tab is open.

## Configuration

Settings come from environment variables or a `.env` file. See `.env.example` for the full list.

`FLIPWATCH_USER_AGENT` is required and should name the project and give a contact. `FLIPWATCH_DB_PATH` sets where the database lives (default `flipwatch.sqlite3`) and `FLIPWATCH_RETENTION_DAYS` sets how long price data is kept (default 90). The `FLIPWATCH_ALERT_` variables set the alert rules and cooldown.

## Data source

Price data comes from the [OSRS Wiki real-time prices API](https://prices.runescape.wiki/).

This project is not affiliated with Jagex.

## Licence

MIT. See [LICENSE](LICENSE).
