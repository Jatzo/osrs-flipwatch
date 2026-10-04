"""Dashboard pages."""

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from flipwatch.api import ApiError
from flipwatch.config import EXCLUDED_ITEM_IDS
from flipwatch.runner import STRATEGIES, BacktestRequestError, run_and_save
from flipwatch.scanner import ScanSettings, assess, rank, scan
from flipwatch.web import forms, market
from flipwatch.web.context import dashboard, store

bp = Blueprint("dashboard", __name__)

MAX_OPPORTUNITY_ROWS = 200

Page = str | tuple[str, int] | Response


@bp.get("/")
def opportunities() -> Page:
    settings, form = forms.scan_settings(request.args)
    app = dashboard()
    try:
        found = scan(
            app.client.mapping(), app.client.latest(), app.client.one_hour(), settings, app.clock()
        )
    except ApiError as exc:
        return render_template("opportunities.html", form=form, api_error=str(exc)), 502
    ranked = rank(found)
    return render_template(
        "opportunities.html",
        form=form,
        opportunities=ranked[:MAX_OPPORTUNITY_ROWS],
        total=len(ranked),
        watched=set(store().watchlist()),
    )


@bp.get("/item/<int:item_id>")
def item(item_id: int) -> Page:
    app = dashboard()
    chart_range = request.args.get("range", "24h")
    if chart_range not in market.CHART_RANGES:
        chart_range = "24h"
    try:
        items = app.client.mapping()
        latest = app.client.latest().get(item_id)
        hourly = app.client.one_hour().get(item_id)
    except ApiError as exc:
        return render_template("error.html", message=str(exc)), 502
    found = items.get(item_id)
    if found is None:
        abort(404)

    now = app.clock()
    opportunity, reason = assess(found, latest, hourly, ScanSettings(), now)
    history = market.item_history(
        store(), app.client, item_id, market.CHART_RANGES[chart_range], now
    )
    return render_template(
        "item.html",
        item=found,
        latest=latest,
        summary=market.price_summary(found, latest),
        opportunity=opportunity,
        reason=reason,
        history=history,
        chart=market.price_chart(history.windows),
        chart_range=chart_range,
        ranges=list(market.CHART_RANGES),
        watched=item_id in store().watchlist(),
    )


@bp.get("/search")
def search() -> Page:
    query = request.args.get("q", "")
    try:
        matches = market.find_items(dashboard().client.mapping(), query)
    except ApiError as exc:
        return render_template("error.html", message=str(exc)), 502
    if len(matches) == 1:
        return redirect(url_for("dashboard.item", item_id=matches[0].id))
    return render_template("search.html", query=query, matches=matches)


@bp.get("/watchlist")
def watchlist() -> Page:
    return _render_watchlist()


@bp.post("/watchlist")
def watchlist_add() -> Page:
    query = request.form.get("item", "")
    try:
        matches = market.find_items(dashboard().client.mapping(), query)
    except ApiError as exc:
        return _render_watchlist(api_error=str(exc), status=502)
    if len(matches) != 1:
        problem = "No item matches" if not matches else f"{len(matches)} items match"
        return _render_watchlist(error=f"{problem} {query!r}. Try the exact name or id.")
    return _watch(matches[0].id, url_for("dashboard.watchlist"))


@bp.post("/watchlist/<int:item_id>/add")
def watch_item(item_id: int) -> Page:
    return _watch(item_id, url_for("dashboard.item", item_id=item_id))


@bp.post("/watchlist/<int:item_id>/remove")
def unwatch_item(item_id: int) -> Page:
    store().unwatch(item_id)
    return redirect(_safe_next(url_for("dashboard.watchlist")))


@bp.get("/backtests")
def backtests() -> Page:
    _, form = forms.backtest_request({})
    return _render_backtests(form)


@bp.post("/backtests")
def start_backtest() -> Page:
    backtest, form = forms.backtest_request(request.form)
    if form.errors:
        return _render_backtests(form, status=400)
    try:
        saved = run_and_save(store(), backtest, dashboard().clock())
    except BacktestRequestError as exc:
        form.errors.append(f"The backtest could not run: {exc}.")
        return _render_backtests(form, status=400)
    return redirect(url_for("dashboard.backtest", run_id=saved.run_id))


@bp.get("/backtests/<int:run_id>")
def backtest(run_id: int) -> Page:
    result = store().backtest_run(run_id)
    if result is None:
        abort(404)
    return render_template(
        "backtest.html",
        run_id=run_id,
        result=result,
        traded=[item for item in result.items if item.filled],
        chart=market.equity_chart(result.equity_curve),
    )


@bp.app_errorhandler(404)
def not_found(_: Exception) -> Page:
    return render_template("error.html", message="There is nothing at this address."), 404


def _render_watchlist(
    error: str | None = None, api_error: str | None = None, status: int = 200
) -> Page:
    app = dashboard()
    ids = store().watchlist()
    rows = []
    if api_error is None:
        try:
            items, latest, hourly = (
                app.client.mapping(),
                app.client.latest(),
                app.client.one_hour(),
            )
        except ApiError as exc:
            api_error = str(exc)
        else:
            now = app.clock()
            for item_id in ids:
                found = items.get(item_id)
                if found is None:
                    continue
                price = latest.get(item_id)
                opportunity, reason = assess(found, price, hourly.get(item_id), ScanSettings(), now)
                rows.append(
                    {
                        "item": found,
                        "summary": market.price_summary(found, price),
                        "opportunity": opportunity,
                        "reason": reason,
                    }
                )
    page = render_template("watchlist.html", rows=rows, error=error, api_error=api_error)
    return page, 502 if api_error else status


def _watch(item_id: int, default_next: str) -> Page:
    if item_id in EXCLUDED_ITEM_IDS:
        return _render_watchlist(error="That item is excluded from tracking.", status=400)
    store().watch(item_id, added_at=int(dashboard().clock()))
    return redirect(_safe_next(default_next))


def _render_backtests(form: forms.Form, status: int = 200) -> Page:
    page = render_template(
        "backtests.html", runs=store().backtest_runs(), form=form, strategies=list(STRATEGIES)
    )
    return page, status


def _safe_next(default: str) -> str:
    """Only follow a `next` value that stays on this site."""
    target = request.form.get("next", "")
    # Browsers read a backslash as a slash, so a leading slash and backslash leaves the site.
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return default
