from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from flipwatch.api import ApiError
from flipwatch.config import AlertSettings, ConfigError, Settings
from flipwatch.models import Item, PriceWindow
from flipwatch.store import Store
from flipwatch.web import create_app
from flipwatch.web.forms import backtest_request, scan_settings
from flipwatch.web.market import find_items, price_summary
from tests.fakes import FakeClient

NOW = 1_791_066_600
WHIP = 4151


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "flipwatch.sqlite3"


@pytest.fixture
def app(fake_client: FakeClient, db_path: Path) -> Flask:
    settings = Settings(
        user_agent="osrs-flipwatch-tests (example.test)",
        db_path=str(db_path),
        # No ROI or confidence floor, so both fixture flips can raise alerts.
        alerts=AlertSettings(min_roi=0, min_confidence=0),
    )
    return create_app(settings, client_factory=lambda _: fake_client, clock=lambda: NOW)


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    with app.test_client() as test_client:
        yield test_client


def text(response: TestResponse) -> str:
    return response.get_data(as_text=True)


class TestOpportunities:
    def test_lists_flips_with_default_filters(self, client: FlaskClient) -> None:
        response = client.get("/")

        page = text(response)
        assert response.status_code == 200
        assert "Tormented synapse" in page
        assert "Noxious halberd" in page
        assert "Feather" not in page
        assert "Showing 2 of 2 flips" in page

    def test_filters_come_from_the_query_string(self, client: FlaskClient) -> None:
        page = text(client.get("/?min_margin=0&min_profit=0&membership=f2p"))

        assert "Feather" in page
        assert "Death rune" in page
        assert "Tormented synapse" not in page

    def test_bad_filter_value_is_reported_and_ignored(self, client: FlaskClient) -> None:
        page = text(client.get("/?min_margin=lots"))

        assert "Minimum margin must be a number of zero or more" in page
        assert "Tormented synapse" in page

    def test_watched_items_are_tagged(self, client: FlaskClient) -> None:
        client.post("/watchlist", data={"item": "Noxious halberd"})

        page = text(client.get("/"))

        assert page.count('class="tag">watched') == 1

    def test_api_failure_shows_a_message(
        self, client: FlaskClient, fake_client: FakeClient
    ) -> None:
        fake_client._error = ApiError("/mapping returned HTTP 503")

        response = client.get("/")

        assert response.status_code == 502
        assert "/mapping returned HTTP 503" in text(response)


class TestItem:
    def test_shows_numbers_and_why_it_is_not_a_flip(self, client: FlaskClient) -> None:
        response = client.get(f"/item/{WHIP}")

        page = text(response)
        assert response.status_code == 200
        assert "Abyssal whip" in page
        assert "808,365" in page
        assert "Not listed as a flip right now: no margin after tax." in page
        assert f"https://prices.runescape.wiki/osrs/item/{WHIP}" in page

    def test_shows_opportunity_for_a_flip(self, client: FlaskClient) -> None:
        page = text(client.get("/item/29796"))

        assert "790,230" in page
        assert "Not listed as a flip" not in page

    def save_whip_windows(self, db_path: Path, timestep: str, *timestamps: int) -> None:
        with Store.open(db_path) as store:
            windows = [PriceWindow(WHIP, t, 820_000, 6, 810_000, 14) for t in timestamps]
            store.save_windows(timestep, windows)

    def test_uses_stored_history_that_covers_the_range(
        self, client: FlaskClient, fake_client: FakeClient, db_path: Path
    ) -> None:
        self.save_whip_windows(db_path, "5m", NOW - 24 * 3600 + 300, NOW - 600)

        page = text(client.get(f"/item/{WHIP}"))

        assert "stored 5m windows" in page
        assert '"high": [820000, 820000]' in page
        assert fake_client.timeseries_requests == []

    def test_short_stored_history_is_not_stretched_over_the_range(
        self, client: FlaskClient, fake_client: FakeClient, db_path: Path
    ) -> None:
        self.save_whip_windows(db_path, "5m", NOW - 3600, NOW - 600)

        page = text(client.get(f"/item/{WHIP}"))

        assert "the OSRS Wiki 5m timeseries" in page
        assert fake_client.timeseries_requests == [(WHIP, "5m")]

    def test_stored_history_that_stopped_early_is_not_used(
        self, client: FlaskClient, fake_client: FakeClient, db_path: Path
    ) -> None:
        # Starts on time but the collector stopped six hours ago.
        self.save_whip_windows(db_path, "5m", NOW - 24 * 3600 + 300, NOW - 6 * 3600)

        page = text(client.get(f"/item/{WHIP}"))

        assert "the OSRS Wiki 5m timeseries" in page
        assert fake_client.timeseries_requests == [(WHIP, "5m")]

    def test_long_range_can_use_stored_hourly_history(
        self, client: FlaskClient, fake_client: FakeClient, db_path: Path
    ) -> None:
        self.save_whip_windows(db_path, "1h", NOW - 7 * 24 * 3600, NOW - 3600)

        page = text(client.get(f"/item/{WHIP}?range=7d"))

        assert "stored 1h windows" in page
        assert fake_client.timeseries_requests == []

    @pytest.mark.parametrize(("range_", "timestep"), [("6h", "5m"), ("24h", "5m"), ("7d", "1h")])
    def test_falls_back_to_one_timeseries_request(
        self, client: FlaskClient, fake_client: FakeClient, range_: str, timestep: str
    ) -> None:
        page = text(client.get(f"/item/{WHIP}?range={range_}"))

        assert f"the OSRS Wiki {timestep} timeseries" in page
        assert fake_client.timeseries_requests == [(WHIP, timestep)]

    def test_unknown_range_uses_the_default(self, client: FlaskClient) -> None:
        page = text(client.get(f"/item/{WHIP}?range=forever"))

        assert 'aria-current="page">24h' in page

    def test_unknown_item_is_not_found(self, client: FlaskClient) -> None:
        response = client.get("/item/999999")

        assert response.status_code == 404
        assert "There is nothing at this address." in text(response)


class TestSearch:
    @pytest.mark.parametrize("query", ["Abyssal whip", "abyssal WHIP", "whip", "4151"])
    def test_single_match_goes_to_the_item(self, client: FlaskClient, query: str) -> None:
        response = client.get("/search", query_string={"q": query})

        assert response.status_code == 302
        assert response.headers["Location"] == f"/item/{WHIP}"

    def test_several_matches_are_listed(self, client: FlaskClient) -> None:
        page = text(client.get("/search?q=3rd age"))

        assert "3rd Age pickaxe" in page
        assert "3rd Age felling axe" in page

    def test_no_match(self, client: FlaskClient) -> None:
        assert "No items match" in text(client.get("/search?q=dragonfire"))


class TestWatchlist:
    def test_add_by_name_and_remove(self, client: FlaskClient) -> None:
        added = client.post("/watchlist", data={"item": "abyssal whip"})
        assert added.status_code == 302

        page = text(client.get("/watchlist"))
        assert "Abyssal whip" in page
        assert "no margin after tax" in page

        client.post(f"/watchlist/{WHIP}/remove")
        assert "Nothing on the watchlist yet" in text(client.get("/watchlist"))

    def test_flip_is_marked(self, client: FlaskClient) -> None:
        client.post("/watchlist", data={"item": "29796"})

        assert 'class="tag good">flip' in text(client.get("/watchlist"))

    def test_ambiguous_name_is_refused(self, client: FlaskClient) -> None:
        page = text(client.post("/watchlist", data={"item": "3rd Age"}))

        assert "2 items match &#39;3rd Age&#39;" in page

    def test_excluded_item_is_refused(self, client: FlaskClient) -> None:
        response = client.post("/watchlist", data={"item": "Old school bond"})

        assert response.status_code == 400
        assert "excluded from tracking" in text(response)

    def test_add_from_item_page_returns_there(self, client: FlaskClient) -> None:
        response = client.post(f"/watchlist/{WHIP}/add", data={"next": f"/item/{WHIP}"})

        assert response.headers["Location"] == f"/item/{WHIP}"
        assert "Remove from watchlist" in text(client.get(f"/item/{WHIP}"))

    @pytest.mark.parametrize("target", ["//example.com", "https://example.com", "/\\example.com"])
    def test_next_cannot_leave_the_site(self, client: FlaskClient, target: str) -> None:
        response = client.post(f"/watchlist/{WHIP}/add", data={"next": target})

        assert response.headers["Location"] == f"/item/{WHIP}"


@pytest.fixture
def market_db(db_path: Path) -> Path:
    """A day of steady five minute windows for two items."""
    start = NOW - 24 * 3600
    with Store.open(db_path) as store:
        store.save_items(
            [Item(i, f"Steady item {i}", True, 5_000, 100, 60, 40, "") for i in (1, 2)]
        )
        for step in range(24 * 12):
            timestamp = start + step * 300
            windows = {i: PriceWindow(i, timestamp, 1_200, 1_000, 1_000, 1_000) for i in (1, 2)}
            store.save_snapshot("5m", timestamp, windows, collected_at=timestamp)
    return db_path


class TestBacktests:
    def test_empty_list(self, client: FlaskClient) -> None:
        page = text(client.get("/backtests"))

        assert "No backtests yet." in page
        assert '<option value="dip"' in page

    def test_run_from_the_form_then_view_it(self, client: FlaskClient, market_db: Path) -> None:
        response = client.post(
            "/backtests", data={"strategy": "margin", "days": "1", "capital": "5m"}
        )

        assert response.status_code == 302
        assert response.headers["Location"] == "/backtests/1"

        page = text(client.get("/backtests/1"))
        assert "Backtest 1: margin" in page
        assert "5,000,000 starting capital" in page
        assert "Steady item 1" in page
        assert 'id="equity-data"' in page
        assert "/backtests/1" in text(client.get("/backtests"))

    def test_bad_form_input_is_reported(self, client: FlaskClient, market_db: Path) -> None:
        response = client.post("/backtests", data={"capital": "loads", "items": "whip"})

        page = text(response)
        assert response.status_code == 400
        assert "Starting capital should look like 50m" in page
        assert "Items should be item ids" in page

    def test_run_without_data_is_reported(self, client: FlaskClient) -> None:
        response = client.post("/backtests", data={"strategy": "margin"})

        assert response.status_code == 400
        assert "The backtest could not run: no 5m data stored yet." in text(response)

    def test_missing_run_is_not_found(self, client: FlaskClient) -> None:
        assert client.get("/backtests/99").status_code == 404


class TestForms:
    def test_scan_settings_from_query(self) -> None:
        settings, form = scan_settings(
            {"min_margin": "25", "min_profit": "1,000,000", "min_roi": "2.5", "max_price": "0",
             "membership": "members", "no_limit": "volume"}
        )  # fmt: skip

        assert form.errors == []
        assert (settings.min_margin, settings.min_profit) == (25, 1_000_000)
        assert settings.min_roi == pytest.approx(0.025)
        assert settings.max_buy_price is None
        assert settings.members is True
        assert settings.no_limit_policy == "volume"

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e999"])
    def test_numbers_that_are_not_finite_are_rejected(self, value: str) -> None:
        settings, form = scan_settings({"min_margin": value, "min_profit": value})

        assert (settings.min_margin, settings.min_profit) == (10, 500_000)
        assert len(form.errors) == 2

        _, form = backtest_request({"days": value})
        assert form.errors

    def test_non_finite_filter_does_not_break_the_page(self, client: FlaskClient) -> None:
        response = client.get("/?min_margin=nan&max_price=inf")

        assert response.status_code == 200
        assert "Minimum margin must be a number of zero or more" in text(response)

    def test_unknown_choices_fall_back_quietly(self) -> None:
        settings, form = scan_settings({"membership": "ironman", "no_limit": "guess"})

        assert settings.members is None
        assert settings.no_limit_policy == "skip"
        assert form.errors == []

    def test_backtest_request_from_form(self) -> None:
        request, form = backtest_request(
            {"strategy": "dip", "timestep": "1h", "days": "3", "items": "4151, 11802",
             "capital": "1.5b", "fill_share": "5", "offer_hours": "2", "start": "2026-10-01"}
        )  # fmt: skip

        assert form.errors == []
        assert request.strategy == "dip"
        assert request.timestep == "1h"
        assert request.days == 3
        assert request.item_ids == (4151, 11802)
        assert request.start == 1_790_812_800
        assert request.settings.starting_capital == 1_500_000_000
        assert request.settings.fill_share == pytest.approx(0.05)
        assert request.settings.offer_lifetime_seconds == 7_200

    @pytest.mark.parametrize(
        ("field", "value"),
        [("days", "0"), ("top", "0"), ("fill_share", "150"), ("offer_hours", "0"),
         ("start", "October"), ("capital", "0")],
    )  # fmt: skip
    def test_backtest_form_errors(self, field: str, value: str) -> None:
        _, form = backtest_request({field: value})

        assert form.errors


class TestMarketHelpers:
    @pytest.fixture
    def items(self) -> dict[int, Item]:
        names = ["Iron arrow", "Iron arrowtips", "Iron bar", "Rune arrow"]
        return {i: Item(i, name, False, 100, 1, 1, 1, "") for i, name in enumerate(names, 1)}

    def test_exact_name_beats_partial_matches(self, items: dict[int, Item]) -> None:
        assert [i.name for i in find_items(items, "iron arrow")] == ["Iron arrow"]

    def test_partial_matches_shortest_first(self, items: dict[int, Item]) -> None:
        assert [i.name for i in find_items(items, "arrow")] == [
            "Iron arrow",
            "Rune arrow",
            "Iron arrowtips",
        ]

    def test_id_and_blank_queries(self, items: dict[int, Item]) -> None:
        assert [i.name for i in find_items(items, " 3 ")] == ["Iron bar"]
        assert find_items(items, "99") == []
        assert find_items(items, "  ") == []

    def test_price_summary_needs_both_sides(self, items: dict[int, Item]) -> None:
        assert price_summary(items[1], None) is None


class TestAlerts:
    def check(self, client: FlaskClient, since: int = 0) -> TestResponse:
        return client.post(f"/alerts/check?since={since}")

    def test_badge_is_hidden_without_alerts(self, client: FlaskClient) -> None:
        page = text(client.get("/watchlist"))

        assert 'id="alert-count" class="badge" hidden' in page
        assert 'data-latest-alert="0"' in page

    def test_check_raises_alerts_and_returns_them_once(self, client: FlaskClient) -> None:
        first = self.check(client).get_json()

        assert [a["itemName"] for a in first["alerts"]] == ["Tormented synapse", "Noxious halberd"]
        assert first["alerts"][1] == {
            "id": 2,
            "itemName": "Noxious halberd",
            "margin": 158_046,
            "potentialProfit": 790_230,
            "confidence": 59,
            "url": "/item/29796",
        }
        assert (first["unread"], first["latestId"], first["error"]) == (2, 2, None)

        second = self.check(client, since=first["latestId"]).get_json()
        assert (second["alerts"], second["unread"], second["latestId"]) == ([], 2, 2)

    def test_another_tab_still_hears_about_alerts(self, client: FlaskClient) -> None:
        self.check(client)

        other_tab = self.check(client, since=0).get_json()

        assert len(other_tab["alerts"]) == 2

    def test_badge_shows_unread_count(self, client: FlaskClient) -> None:
        self.check(client)

        page = text(client.get("/"))

        assert 'id="alert-count" class="badge" >' in page
        assert "<span data-count>2</span>" in page
        assert 'data-latest-alert="2"' in page

    def test_alerts_page_and_mark_as_read(self, client: FlaskClient) -> None:
        self.check(client)

        page = text(client.get("/alerts"))
        assert page.count('class="unread"') == 2
        assert page.count('class="tag good">still a flip') == 2
        assert "158,046" in page
        assert "Mark all as read" in page

        response = client.post("/alerts/read")
        assert response.headers["Location"] == "/alerts"

        page = text(client.get("/alerts"))
        assert 'class="unread"' not in page
        assert "Mark all as read" not in page

    def test_alerts_page_describes_the_rules(self, client: FlaskClient) -> None:
        page = text(client.get("/alerts"))

        assert "No alerts yet." in page
        assert "Each item alerts at most once every 60 minutes." in page

    def test_api_failure_during_check(self, client: FlaskClient, fake_client: FakeClient) -> None:
        fake_client._error = ApiError("/mapping returned HTTP 503")

        response = self.check(client)

        assert response.status_code == 502
        assert response.get_json() == {
            "alerts": [],
            "unread": 0,
            "latestId": 0,
            "error": "/mapping returned HTTP 503",
        }

    def test_history_shows_when_prices_cannot_load(
        self, client: FlaskClient, fake_client: FakeClient
    ) -> None:
        self.check(client)
        fake_client._error = ApiError("/mapping returned HTTP 503")

        page = text(client.get("/alerts"))

        assert "Current prices could not be loaded" in page
        assert "Noxious halberd" in page

    def test_check_needs_a_post(self, client: FlaskClient) -> None:
        assert client.get("/alerts/check").status_code == 405


def test_dashboard_refuses_to_start_without_a_user_agent(db_path: Path) -> None:
    with pytest.raises(ConfigError, match="FLIPWATCH_USER_AGENT"):
        create_app(Settings(db_path=str(db_path)))
