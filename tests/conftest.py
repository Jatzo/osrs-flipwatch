import json
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeClient

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def mapping_payload() -> list[dict[str, Any]]:
    return read_fixture("mapping.json")


@pytest.fixture
def latest_payload() -> dict[str, Any]:
    return read_fixture("latest.json")


@pytest.fixture
def five_minute_payload() -> dict[str, Any]:
    return read_fixture("5m.json")


@pytest.fixture
def one_hour_payload() -> dict[str, Any]:
    return read_fixture("1h.json")


@pytest.fixture
def timeseries_payload() -> dict[str, Any]:
    return read_fixture("timeseries_5m_4151.json")


@pytest.fixture
def fake_client(
    mapping_payload: list[dict[str, Any]],
    latest_payload: dict[str, Any],
    one_hour_payload: dict[str, Any],
    five_minute_payload: dict[str, Any],
    timeseries_payload: dict[str, Any],
) -> FakeClient:
    return FakeClient(
        mapping_payload, latest_payload, one_hour_payload, five_minute_payload, timeseries_payload
    )
