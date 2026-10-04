"""Shared state for the dashboard: settings, one price client and a store per request."""

from collections.abc import Callable
from dataclasses import dataclass

from flask import Flask, current_app, g

from flipwatch.api import PricesClient
from flipwatch.config import Settings
from flipwatch.store import Store

_KEY = "flipwatch"


@dataclass(frozen=True)
class Dashboard:
    settings: Settings
    # One client for the whole app, so its caches are shared between requests.
    client: PricesClient
    clock: Callable[[], float]


def install(app: Flask, dashboard: Dashboard) -> None:
    app.extensions[_KEY] = dashboard
    app.teardown_appcontext(_close_store)


def dashboard() -> Dashboard:
    return current_app.extensions[_KEY]


def store() -> Store:
    """Open the database for this request on first use."""
    if "store" not in g:
        g.store = Store.open(dashboard().settings.db_path)
    return g.store


def _close_store(_: BaseException | None) -> None:
    opened = g.pop("store", None)
    if opened is not None:
        opened.close()
