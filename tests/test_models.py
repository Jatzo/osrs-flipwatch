from typing import Any

from flipwatch.models import Item, LatestPrice, PriceWindow


def _item(mapping_payload: list[dict[str, Any]], item_id: int) -> Item:
    record = next(r for r in mapping_payload if r["id"] == item_id)
    return Item.from_api(record)


def test_item_from_mapping(mapping_payload: list[dict[str, Any]]) -> None:
    whip = _item(mapping_payload, 4151)

    assert whip == Item(
        id=4151,
        name="Abyssal whip",
        members=True,
        buy_limit=70,
        value=120001,
        high_alch=72000,
        low_alch=48000,
        icon="Abyssal whip.png",
    )


def test_item_without_buy_limit(mapping_payload: list[dict[str, Any]]) -> None:
    felling_axe = _item(mapping_payload, 28226)

    assert felling_axe.name == "3rd Age felling axe"
    assert felling_axe.buy_limit is None


def test_item_without_alchemy_values(mapping_payload: list[dict[str, Any]]) -> None:
    bond = _item(mapping_payload, 13190)

    assert bond.high_alch is None
    assert bond.low_alch is None


def test_every_mapping_record_parses(mapping_payload: list[dict[str, Any]]) -> None:
    items = [Item.from_api(record) for record in mapping_payload]

    assert len(items) == len(mapping_payload)


def test_latest_price(latest_payload: dict[str, Any]) -> None:
    price = LatestPrice.from_api(4151, latest_payload["data"]["4151"])

    assert price == LatestPrice(
        item_id=4151, high=818095, high_time=1791066537, low=808365, low_time=1791066592
    )


def test_latest_price_with_one_side_missing(latest_payload: dict[str, Any]) -> None:
    price = LatestPrice.from_api(2660, latest_payload["data"]["2660"])

    assert price.high == 50000
    assert price.low is None
    assert price.low_time is None


def test_latest_price_above_32_bit_range(latest_payload: dict[str, Any]) -> None:
    price = LatestPrice.from_api(20014, latest_payload["data"]["20014"])

    assert price.high is not None
    assert price.high > 2**31


def test_price_window(five_minute_payload: dict[str, Any]) -> None:
    window = PriceWindow.from_api(
        4151, five_minute_payload["timestamp"], five_minute_payload["data"]["4151"]
    )

    assert window == PriceWindow(
        item_id=4151,
        timestamp=1791066300,
        avg_high_price=816277,
        high_volume=6,
        avg_low_price=808931,
        low_volume=14,
    )


def test_one_sided_price_window(five_minute_payload: dict[str, Any]) -> None:
    window = PriceWindow.from_api(
        6, five_minute_payload["timestamp"], five_minute_payload["data"]["6"]
    )

    assert window.avg_low_price is None
    assert window.low_volume == 0


def test_timeseries_points_parse_as_price_windows(timeseries_payload: dict[str, Any]) -> None:
    item_id = timeseries_payload["itemId"]
    windows = [
        PriceWindow.from_api(item_id, point["timestamp"], point)
        for point in timeseries_payload["data"]
    ]

    assert len(windows) == 12
    assert all(w.item_id == 4151 for w in windows)
    assert any(w.avg_high_price is None or w.avg_low_price is None for w in windows)
