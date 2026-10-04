"""Dashboard pages."""

from typing import Any

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from flipwatch.alerts import check_for_alerts, current_status
from flipwatch.api import ApiError
from flipwatch.config import EXCLUDED_ITEM_IDS
from flipwatch.models import Alert
from flipwatch.runner import STRATEGIES, BacktestRequestError, run_and_save
from flipwatch.scanner import ScanSettings, assess, margin_at, rank, scan
from flipwatch.web import forms, market
from flipwatch.web.context import dashboard, store

bp = Blueprint("dashboard", __name__)

MAX_OPPORTUNITY_ROWS = 200

Page = str | Response | tuple[str | Response, int]


@bp.app_context_processor
def alert_status() -> dict[str, int]:
    """The unread count for the header badge, and where the alert check should resume."""
    return {
        "unread_alerts": store().unread_alert_count(),
        "latest_alert_id": store().latest_alert_id(),
    }


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
        summary=margin_at(found, latest),
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


@bp.get("/alerts")
def alerts() -> Page:
    app = dashboard()
    history = store().alerts()
    rows: list[dict[str, Any]] = [{"alert": alert} for alert in history]
    api_error = None
    try:
        items, latest, hourly = app.client.mapping(), app.client.latest(), app.client.one_hour()
    except ApiError as exc:
        api_error = str(exc)
    else:
        now = app.clock()
        for row in rows:
            item_id = row["alert"].item_id
            found = items.get(item_id)
            if found is None:
                continue
            price = latest.get(item_id)
            opportunity, reason = current_status(
                found, price, hourly.get(item_id), app.settings.alerts, now
            )
            row.update(summary=margin_at(found, price), opportunity=opportunity, reason=reason)
    return render_template("alerts.html", rows=rows, rules=app.settings.alerts, api_error=api_error)


@bp.post("/alerts/read")
def mark_alerts_read() -> Page:
    store().mark_alerts_read()
    return redirect(url_for("dashboard.alerts"))


@bp.post("/alerts/check")
def check_alerts() -> Page:
    """Raise any new alerts, then return every alert after the one the page last saw."""
    app = dashboard()
    since = request.args.get("since", type=int, default=0)
    error = None
    try:
        check_for_alerts(app.client, store(), app.settings.alerts, app.clock())
    except ApiError as exc:
        error = str(exc)
    new = store().alerts_after(since)
    body = {
        "alerts": [_alert_json(alert) for alert in new],
        "unread": store().unread_alert_count(),
        "latestId": new[-1].id if new else since,
        "error": error,
    }
    return jsonify(body), 502 if error else 200


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
                        "summary": margin_at(found, price),
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


def _alert_json(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "itemName": alert.item_name,
        "margin": alert.margin,
        "potentialProfit": alert.potential_profit,
        "confidence": alert.confidence,
        "url": url_for("dashboard.item", item_id=alert.item_id),
    }


def _safe_next(default: str) -> str:
    """Only follow a `next` value that stays on this site."""
    target = request.form.get("next", "")
    # Browsers read a backslash as a slash, so a leading slash and backslash leaves the site.
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return default
