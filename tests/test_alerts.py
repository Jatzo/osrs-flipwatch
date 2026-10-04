from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from flipwatch.alerts import check_for_alerts, current_status, scan_settings, select_alerts
from flipwatch.api import ApiError
from flipwatch.config import AlertSettings
from flipwatch.models import Confidence, Item, Opportunity
from flipwatch.store import Store
from tests.fakes import FakeClient

NOW = 1_791_066_600
SYNAPSE = 29580
HALBERD = 29796
# The fixture synapse has 3.1% ROI and 17 confidence, the halberd 0.4% ROI and 59
# confidence. These rules let both through so each floor can be tested on its own.
RULES = AlertSettings(min_confidence=0, min_roi=0)


def make_opportunity(item_id: int, profit: int, confidence: int) -> Opportunity:
    return Opportunity(
        item=Item(item_id, f"Item {item_id}", True, 100, 1, 1, 1, ""),
        buy_price=1_000,
        sell_price=1_200,
        tax=24,
        margin=176,
        roi=0.176,
        low_volume=1_000,
        high_volume=1_000,
        quantity=profit // 176,
        potential_profit=profit,
        confidence=Confidence(score=confidence, liquidity=1.0, stability=confidence / 100),
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store.open(tmp_path / "flipwatch.sqlite3") as opened:
        yield opened


class TestSelect:
    def test_scan_settings_carry_the_rules(self) -> None:
        settings = scan_settings(
            AlertSettings(min_margin=25, min_profit=1, min_roi=0.05, min_volume=7)
        )

        assert (settings.min_margin, settings.min_profit) == (25, 1)
        assert (settings.min_roi, settings.min_volume) == (0.05, 7)

    def test_keeps_confident_flips_best_profit_first(self) -> None:
        flips = [
            make_opportunity(1, 600_000, 39),
            make_opportunity(2, 700_000, 40),
            make_opportunity(3, 900_000, 80),
        ]

        chosen = select_alerts(flips, AlertSettings(min_confidence=40))

        assert [o.item.id for o in chosen] == [3, 2]

    def test_watchlist_limits_items(self) -> None:
        flips = [make_opportunity(1, 600_000, 90), make_opportunity(2, 700_000, 90)]

        assert [o.item.id for o in select_alerts(flips, RULES, watchlist={1})] == [1]
        assert select_alerts(flips, RULES, watchlist=set()) == []


class TestCheck:
    def test_raises_alerts_for_flips_meeting_the_rules(
        self, fake_client: FakeClient, store: Store
    ) -> None:
        raised = check_for_alerts(fake_client, store, RULES, NOW)

        assert [(a.item_id, a.item_name) for a in raised] == [
            (SYNAPSE, "Tormented synapse"),
            (HALBERD, "Noxious halberd"),
        ]
        assert raised[1].potential_profit == 790_230
        assert store.unread_alert_count() == 2

    def test_confidence_floor_drops_the_shaky_synapse(
        self, fake_client: FakeClient, store: Store
    ) -> None:
        raised = check_for_alerts(fake_client, store, replace(RULES, min_confidence=40), NOW)

        assert [a.item_id for a in raised] == [HALBERD]

    def test_roi_floor_drops_the_thin_halberd(self, fake_client: FakeClient, store: Store) -> None:
        raised = check_for_alerts(fake_client, store, replace(RULES, min_roi=0.01), NOW)

        assert [a.item_id for a in raised] == [SYNAPSE]

    def test_default_rules_raise_nothing_for_the_fixtures(
        self, fake_client: FakeClient, store: Store
    ) -> None:
        assert check_for_alerts(fake_client, store, AlertSettings(), NOW) == []

    def test_cooldown_stops_repeats_until_it_ends(
        self, fake_client: FakeClient, store: Store
    ) -> None:
        rules = replace(RULES, cooldown_minutes=60)
        assert len(check_for_alerts(fake_client, store, rules, NOW)) == 2

        assert check_for_alerts(fake_client, store, rules, NOW + 60) == []
        assert check_for_alerts(fake_client, store, rules, NOW + 3_599) == []

        # Prices this old are stale for the scan, so move the trade times along too.
        fake_client._latest = {
            "data": {
                k: {**v, "highTime": v["highTime"] and v["highTime"] + 3_600,
                    "lowTime": v["lowTime"] and v["lowTime"] + 3_600}
                for k, v in fake_client._latest["data"].items()
            }
        }  # fmt: skip
        assert len(check_for_alerts(fake_client, store, rules, NOW + 3_600)) == 2

    def test_watchlist_only(self, fake_client: FakeClient, store: Store) -> None:
        store.watch(HALBERD, added_at=NOW)

        raised = check_for_alerts(fake_client, store, replace(RULES, watchlist_only=True), NOW)

        assert [a.item_id for a in raised] == [HALBERD]

    def test_api_failure_raises_nothing(self, fake_client: FakeClient, store: Store) -> None:
        fake_client._error = ApiError("/mapping returned HTTP 503")

        with pytest.raises(ApiError):
            check_for_alerts(fake_client, store, RULES, NOW)

        assert store.alerts() == []


class TestCurrentStatus:
    @pytest.fixture
    def market(self, fake_client: FakeClient) -> tuple[dict, dict, dict]:
        return fake_client.mapping(), fake_client.latest(), fake_client.one_hour()

    def status(
        self, market: tuple[dict, dict, dict], item_id: int, rules: AlertSettings
    ) -> tuple[Opportunity | None, str | None]:
        items, latest, hourly = market
        return current_status(items[item_id], latest.get(item_id), hourly.get(item_id), rules, NOW)

    def test_uses_the_alert_rules_not_the_scan_defaults(
        self, market: tuple[dict, dict, dict]
    ) -> None:
        opportunity, reason = self.status(market, HALBERD, replace(RULES, min_profit=100_000))

        assert opportunity is not None
        assert reason is None

        _, reason = self.status(market, HALBERD, replace(RULES, min_profit=800_000))
        assert reason == "potential profit under 800,000 gp"

    def test_confidence_floor_is_a_reason(self, market: tuple[dict, dict, dict]) -> None:
        opportunity, reason = self.status(market, SYNAPSE, replace(RULES, min_confidence=40))

        assert opportunity is None
        assert reason == "confidence under 40"
