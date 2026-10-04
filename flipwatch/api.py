"""Client for the OSRS Wiki real-time prices API.

The wiki asks clients to identify themselves and to avoid needless requests, so the
client refuses to run without a user agent and caches the endpoints that change slowly.
"""

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Self

import httpx

from flipwatch.config import Settings, require_user_agent
from flipwatch.models import Item, LatestPrice, PriceWindow

MAPPING_TTL_SECONDS = 24 * 60 * 60
LATEST_TTL_SECONDS = 60
# The dashboard can ask for the same item's history on every page view.
TIMESERIES_TTL_SECONDS = 5 * 60

TIMESTEP_SECONDS = {"5m": 5 * 60, "1h": 60 * 60, "6h": 6 * 60 * 60, "24h": 24 * 60 * 60}

Clock = Callable[[], float]


class ApiError(Exception):
    """Raised when a request fails or the response is not in the expected shape."""


class _TtlCache:
    # The dashboard shares one client between request threads, so every read and
    # write holds the lock.
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                return None
            return value

    def put(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._entries[key] = (self._clock() + ttl_seconds, value)


class PricesClient:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock = time.monotonic,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        require_user_agent(settings)
        self._http = httpx.Client(
            base_url=settings.api_base_url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_seconds,
            transport=transport,
        )
        self._cache = _TtlCache(clock)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def mapping(self) -> Mapping[int, Item]:
        """Return metadata for every tradeable item, keyed by item id. Cached for 24 hours."""
        cached = self._cache.get("mapping")
        if cached is not None:
            return cached
        payload = self._get_json("/mapping")
        items = _parse(lambda: {r["id"]: Item.from_api(r) for r in payload}, "/mapping")
        self._cache.put("mapping", items, MAPPING_TTL_SECONDS)
        return items

    def latest(self) -> Mapping[int, LatestPrice]:
        """Return the latest instant buy and sell price for every item. Cached for 60 seconds."""
        cached = self._cache.get("latest")
        if cached is not None:
            return cached
        payload = self._get_json("/latest")
        prices = _parse(
            lambda: {
                int(item_id): LatestPrice.from_api(int(item_id), record)
                for item_id, record in payload["data"].items()
            },
            "/latest",
        )
        self._cache.put("latest", prices, LATEST_TTL_SECONDS)
        return prices

    def five_minute(self, timestamp: int | None = None) -> Mapping[int, PriceWindow]:
        """Return five minute averages for every item that traded in the window.

        Without a timestamp this is the most recent complete window.
        """
        return self._window("/5m", "5m", timestamp)

    def one_hour(self, timestamp: int | None = None) -> Mapping[int, PriceWindow]:
        """Return one hour averages for every item that traded in the window."""
        return self._window("/1h", "1h", timestamp)

    def timeseries(self, item_id: int, timestep: str = "5m") -> list[PriceWindow]:
        """Return up to 365 recent windows for one item, oldest first. Cached for 5 minutes."""
        check_timestep(timestep)
        key = f"timeseries:{item_id}:{timestep}"
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._get_json("/timeseries", {"id": item_id, "timestep": timestep})
        points = _parse(
            lambda: [
                PriceWindow.from_api(item_id, point["timestamp"], point)
                for point in payload["data"]
            ],
            "/timeseries",
        )
        self._cache.put(key, tuple(points), TIMESERIES_TTL_SECONDS)
        return points

    def _window(self, path: str, timestep: str, timestamp: int | None) -> Mapping[int, PriceWindow]:
        params: dict[str, int] = {}
        if timestamp is not None:
            # The API silently returns no data for a timestamp that is not on a
            # window boundary, so reject it here rather than return a misleading empty result.
            step = TIMESTEP_SECONDS[timestep]
            if timestamp % step:
                raise ValueError(
                    f"timestamp {timestamp} is not a multiple of {step} seconds for {path}"
                )
            params["timestamp"] = timestamp
        payload = self._get_json(path, params)
        return _parse(lambda: _windows_from_payload(payload), path)

    def _get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        try:
            response = self._http.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ApiError(f"{path} returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"request to {path} failed: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"{path} did not return valid JSON") from exc


def _windows_from_payload(payload: Mapping[str, Any]) -> dict[int, PriceWindow]:
    data = payload["data"]
    # An empty result comes back without a timestamp field, so there is nothing to read.
    if not data:
        return {}
    timestamp = payload["timestamp"]
    return {
        int(item_id): PriceWindow.from_api(int(item_id), timestamp, record)
        for item_id, record in data.items()
    }


def check_timestep(timestep: str) -> None:
    if timestep not in TIMESTEP_SECONDS:
        allowed = ", ".join(TIMESTEP_SECONDS)
        raise ValueError(f"timestep must be one of {allowed}, got {timestep!r}")


def _parse[T](build: Callable[[], T], path: str) -> T:
    try:
        return build()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ApiError(f"unexpected response shape from {path}: {exc!r}") from exc
