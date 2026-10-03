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

## Data source

Price data comes from the [OSRS Wiki real-time prices API](https://prices.runescape.wiki/).

This project is not affiliated with Jagex.

## Licence

MIT. See [LICENSE](LICENSE).
