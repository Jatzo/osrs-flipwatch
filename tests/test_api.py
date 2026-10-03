from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from flipwatch.api import LATEST_TTL_SECONDS, MAPPING_TTL_SECONDS, ApiError, PricesClient
from flipwatch.config import ConfigError, Settings

BASE_URL = "https://prices.example.test/api/v1/osrs"
USER_AGENT = "osrs-flipwatch-tests (github.com/example/osrs-flipwatch)"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def api() -> respx.MockRouter:
    return respx.MockRouter(base_url=BASE_URL, assert_all_called=False)


@pytest.fixture
def client(clock: FakeClock, api: respx.MockRouter) -> Iterator[PricesClient]:
    settings = Settings(user_agent=USER_AGENT, api_base_url=BASE_URL)
    transport = httpx.MockTransport(api.handler)
    with PricesClient(settings, clock=clock, transport=transport) as prices_client:
        yield prices_client


def test_requires_user_agent() -> None:
    with pytest.raises(ConfigError, match="user agent"):
        PricesClient(Settings(user_agent="  ", api_base_url=BASE_URL))


def test_sends_user_agent(
    client: PricesClient, api: respx.MockRouter, latest_payload: dict[str, Any]
) -> None:
    route = api.get("/latest").respond(json=latest_payload)

    client.latest()

    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT


def test_mapping_is_keyed_by_item_id(
    client: PricesClient, api: respx.MockRouter, mapping_payload: list[dict[str, Any]]
) -> None:
    api.get("/mapping").respond(json=mapping_payload)

    items = client.mapping()

    assert items[4151].name == "Abyssal whip"
    assert items[28226].buy_limit is None
    assert len(items) == len(mapping_payload)


def test_mapping_is_cached_for_a_day(
    client: PricesClient,
    api: respx.MockRouter,
    clock: FakeClock,
    mapping_payload: list[dict[str, Any]],
) -> None:
    route = api.get("/mapping").respond(json=mapping_payload)

    client.mapping()
    clock.advance(MAPPING_TTL_SECONDS - 1)
    client.mapping()
    assert route.call_count == 1

    clock.advance(1)
    client.mapping()
    assert route.call_count == 2


def test_latest_is_keyed_by_integer_item_id(
    client: PricesClient, api: respx.MockRouter, latest_payload: dict[str, Any]
) -> None:
    api.get("/latest").respond(json=latest_payload)

    prices = client.latest()

    assert prices[4151].high == 818095
    assert prices[2660].low is None
    assert all(isinstance(item_id, int) for item_id in prices)


def test_latest_is_cached_for_a_minute(
    client: PricesClient,
    api: respx.MockRouter,
    clock: FakeClock,
    latest_payload: dict[str, Any],
) -> None:
    route = api.get("/latest").respond(json=latest_payload)

    client.latest()
    clock.advance(LATEST_TTL_SECONDS - 1)
    client.latest()
    assert route.call_count == 1

    clock.advance(1)
    client.latest()
    assert route.call_count == 2


def test_five_minute_windows(
    client: PricesClient, api: respx.MockRouter, five_minute_payload: dict[str, Any]
) -> None:
    api.get("/5m").respond(json=five_minute_payload)

    windows = client.five_minute()

    assert windows[4151].timestamp == 1791066300
    assert windows[4151].low_volume == 14
    assert windows[6].avg_low_price is None


def test_five_minute_is_not_cached(
    client: PricesClient, api: respx.MockRouter, five_minute_payload: dict[str, Any]
) -> None:
    route = api.get("/5m").respond(json=five_minute_payload)

    client.five_minute()
    client.five_minute()

    assert route.call_count == 2


def test_past_window_sends_timestamp(
    client: PricesClient, api: respx.MockRouter, one_hour_payload: dict[str, Any]
) -> None:
    route = api.get("/1h").respond(json=one_hour_payload)

    client.one_hour(timestamp=1_791_064_800)

    assert route.calls.last.request.url.params["timestamp"] == "1791064800"


@pytest.mark.parametrize(
    ("method", "timestamp"), [("five_minute", 1_791_066_301), ("one_hour", 1_791_066_300)]
)
def test_unaligned_timestamp_is_rejected(
    client: PricesClient, api: respx.MockRouter, method: str, timestamp: int
) -> None:
    route = api.route()

    with pytest.raises(ValueError, match="multiple of"):
        getattr(client, method)(timestamp=timestamp)
    assert not route.called


def test_empty_window_returns_no_items(client: PricesClient, api: respx.MockRouter) -> None:
    api.get("/5m").respond(json={"data": {}})

    assert client.five_minute(timestamp=300) == {}


def test_timeseries(
    client: PricesClient, api: respx.MockRouter, timeseries_payload: dict[str, Any]
) -> None:
    route = api.get("/timeseries").respond(json=timeseries_payload)

    points = client.timeseries(4151, "5m")

    assert route.calls.last.request.url.params["id"] == "4151"
    assert route.calls.last.request.url.params["timestep"] == "5m"
    assert len(points) == 12
    assert points == sorted(points, key=lambda p: p.timestamp)


def test_timeseries_rejects_unknown_timestep(client: PricesClient, api: respx.MockRouter) -> None:
    route = api.route()

    with pytest.raises(ValueError, match="timestep"):
        client.timeseries(4151, "10m")
    assert not route.called


def test_http_error_raises_api_error(client: PricesClient, api: respx.MockRouter) -> None:
    api.get("/latest").respond(status_code=403)

    with pytest.raises(ApiError, match="HTTP 403"):
        client.latest()


def test_network_error_raises_api_error(client: PricesClient, api: respx.MockRouter) -> None:
    api.get("/latest").mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(ApiError, match="failed"):
        client.latest()


def test_invalid_json_raises_api_error(client: PricesClient, api: respx.MockRouter) -> None:
    api.get("/latest").respond(text="<html>maintenance</html>")

    with pytest.raises(ApiError, match="valid JSON"):
        client.latest()


@pytest.mark.parametrize(
    ("path", "method", "body"),
    [
        ("/latest", "latest", {"items": []}),
        ("/mapping", "mapping", [{"id": 1}]),
        ("/5m", "five_minute", ["not", "an", "object"]),
    ],
)
def test_unexpected_shape_raises_api_error(
    client: PricesClient, api: respx.MockRouter, path: str, method: str, body: Any
) -> None:
    api.get(path).respond(json=body)

    with pytest.raises(ApiError, match="unexpected response shape"):
        getattr(client, method)()


def test_failed_request_is_not_cached(
    client: PricesClient, api: respx.MockRouter, latest_payload: dict[str, Any]
) -> None:
    api.get("/latest").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=latest_payload)]
    )

    with pytest.raises(ApiError):
        client.latest()
    assert client.latest()[4151].high == 818095
