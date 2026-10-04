"""Flask dashboard for opportunities, item charts, the watchlist and backtests.

Run it with `flask --app flipwatch.web run`. It is meant for your own machine and has no
login, so do not expose it to a network you do not trust.
"""

import time
from collections.abc import Callable

from flask import Flask

from flipwatch.api import PricesClient
from flipwatch.config import Settings, load_settings, require_user_agent
from flipwatch.web import context, filters, routes


def create_app(
    settings: Settings | None = None,
    *,
    client_factory: Callable[[Settings], PricesClient] = PricesClient,
    clock: Callable[[], float] = time.time,
) -> Flask:
    settings = settings or load_settings()
    require_user_agent(settings)
    app = Flask(__name__)
    context.install(app, context.Dashboard(settings, client_factory(settings), clock))
    filters.register(app)
    app.register_blueprint(routes.bp)
    return app
