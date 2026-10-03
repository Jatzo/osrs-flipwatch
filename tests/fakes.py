"""Test doubles shared between test modules."""

from typing import Any, Self

from flipwatch.api import ApiError
from flipwatch.models import Item, LatestPrice, PriceWindow


class FakeClient:
    """Serves the fixture payloads in place of the real API."""

    def __init__(
        self,
        mapping: list[dict[str, Any]],
        latest: dict[str, Any],
        hourly: dict[str, Any],
        five_minute: dict[str, Any] | None = None,
        timeseries: dict[str, Any] | None = None,
        error: ApiError | None = None,
    ) -> None:
        self._mapping = mapping
        self._latest = latest
        self._hourly = hourly
        self._five_minute = five_minute or {"data": {}}
        self._timeseries = timeseries or {"data": []}
        self._error = error
        self.closed = False
        self.timeseries_requests: list[tuple[int, str]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True

    def mapping(self) -> dict[int, Item]:
        if self._error:
            raise self._error
        return {r["id"]: Item.from_api(r) for r in self._mapping}

    def latest(self) -> dict[int, LatestPrice]:
        return {int(k): LatestPrice.from_api(int(k), v) for k, v in self._latest["data"].items()}

    def one_hour(self) -> dict[int, PriceWindow]:
        timestamp = self._hourly["timestamp"]
        return {
            int(k): PriceWindow.from_api(int(k), timestamp, v)
            for k, v in self._hourly["data"].items()
        }

    def five_minute(self, timestamp: int | None = None) -> dict[int, PriceWindow]:
        # Every window gets the same fixture data, stamped with the requested time.
        assert timestamp is not None
        return {
            int(k): PriceWindow.from_api(int(k), timestamp, v)
            for k, v in self._five_minute["data"].items()
        }

    def timeseries(self, item_id: int, timestep: str = "5m") -> list[PriceWindow]:
        self.timeseries_requests.append((item_id, timestep))
        return [
            PriceWindow.from_api(item_id, point["timestamp"], point)
            for point in self._timeseries["data"]
        ]
