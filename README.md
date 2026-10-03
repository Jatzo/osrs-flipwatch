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

Items are skipped when either price is more than 10 minutes old, when the margin after tax is zero or less, or when fewer than 50 trades happened on the thinner side of the market in the last hour. Items with no listed buy limit are skipped too, unless you pass `--no-limit-policy volume`. Old school bonds are always left out, because a bond bought on the Grand Exchange becomes untradeable and costs 10% of its value to make tradeable again.

Quantity is the lower of the buy limit and 10% of last hour's volume on the thinner side. A buy offer fills against people selling instantly and a sell offer fills against people buying instantly, so the quieter side is what limits a flip. Potential profit is margin times quantity.

Large margins on tiny volume are often traps, so each flip has a confidence score from 0 to 100. It is liquidity multiplied by stability. Liquidity rises with the logarithm of hourly volume and reaches full marks at 500 trades an hour. Stability measures how far the latest prices sit from last hour's averages and drops to zero once either side has moved 10%. A one coin difference is ignored so cheap items are not marked down for rounding.

Run `flipwatch scan --help` for every option.

## Data source

Price data comes from the [OSRS Wiki real-time prices API](https://prices.runescape.wiki/).

This project is not affiliated with Jagex.

## Licence

MIT. See [LICENSE](LICENSE).
