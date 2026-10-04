"""Template filters for formatting coins, percentages and times."""

from datetime import UTC, datetime

from flask import Flask


def coins(value: int | float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def percent(value: float | None, places: int = 1) -> str:
    return "-" if value is None else f"{value:.{places}%}"


def utc_time(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp, UTC).strftime("%d %b %Y %H:%M UTC")


def register(app: Flask) -> None:
    app.add_template_filter(coins)
    app.add_template_filter(percent)
    app.add_template_filter(utc_time)
