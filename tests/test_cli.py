from typing import Any, Self

import pytest

from flipwatch.api import ApiError
from flipwatch.cli import main
from flipwatch.config import Settings
from flipwatch.models import Item, LatestPrice, PriceWindow

NOW = 1_791_066_600
SYNAPSE = "Tormented synapse"
HALBERD = "Noxious halberd"
# Turns off the default margin and profit floors so the cheap fixture items show up.
NO_FLOORS = ["--min-margin", "0", "--min-profit", "0"]


class FakeClient:
    """Serves the fixture payloads in place of the real API."""

    def __init__(
        self,
        mapping: list[dict[str, Any]],
        latest: dict[str, Any],
        hourly: dict[str, Any],
        error: ApiError | None = None,
    ) -> None:
        self._mapping = mapping
        self._latest = latest
        self._hourly = hourly
        self._error = error
        self.closed = False

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


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flipwatch.config.load_dotenv", lambda: None)
    monkeypatch.setenv("FLIPWATCH_USER_AGENT", "osrs-flipwatch-tests (example.test)")


@pytest.fixture
def fake_client(
    mapping_payload: list[dict[str, Any]],
    latest_payload: dict[str, Any],
    one_hour_payload: dict[str, Any],
) -> FakeClient:
    return FakeClient(mapping_payload, latest_payload, one_hour_payload)


def run(client: FakeClient, *args: str) -> int:
    def factory(settings: Settings) -> FakeClient:
        assert settings.user_agent
        return client

    return main(["scan", *args], client_factory=factory, clock=lambda: NOW)


def item_names(rows: list[str]) -> list[str]:
    return [row.split("  ")[0].strip() for row in rows]


def table_rows(output: str) -> list[str]:
    lines = output.splitlines()
    return lines[2 : lines.index("")]


def test_scan_prints_ranked_table(
    fake_client: FakeClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake_client) == 0

    output = capsys.readouterr().out
    header = output.splitlines()[0].split()
    rows = table_rows(output)
    assert header == [
        "Item", "Buy", "Sell", "Margin", "ROI", "Limit",
        "Vol/h", "Qty", "Profit", "Conf", "Liq", "Stab",
    ]  # fmt: skip
    assert item_names(rows) == ["Tormented synapse", "Noxious halberd"]
    assert rows[0].split() == [
        "Tormented", "synapse", "39,516,908", "41,570,856", "1,222,531", "3.1%",
        "5", "85", "5", "6,112,655", "17", "72", "23",
    ]  # fmt: skip
    assert "Showing 2 of 2 flips, ranked by profit." in output
    assert fake_client.closed


def test_columns_are_aligned(fake_client: FakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    run(fake_client, *NO_FLOORS)

    lines = capsys.readouterr().out.splitlines()
    table = lines[: lines.index("")]
    assert len({len(line) for line in table[1:]}) == 1


def test_sort_option(fake_client: FakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    run(fake_client, "--sort", "confidence")

    rows = table_rows(capsys.readouterr().out)
    assert item_names(rows) == ["Noxious halberd", "Tormented synapse"]


def test_top_option(fake_client: FakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    run(fake_client, "--top", "1")

    output = capsys.readouterr().out
    assert len(table_rows(output)) == 1
    assert "Showing 1 of 2 flips" in output


def test_single_result_summary(fake_client: FakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    run(fake_client, "--min-profit", "790231")

    assert "Showing 1 of 1 flip, ranked by profit." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([*NO_FLOORS, "--min-roi", "5"], ["Feather"]),
        (["--min-profit", "0", "--min-margin", "2"], [SYNAPSE, HALBERD, "Death rune"]),
        (["--min-profit", "790230"], [SYNAPSE, HALBERD]),
        (["--min-profit", "790231"], [SYNAPSE]),
        ([*NO_FLOORS, "--max-price", "1000"], ["Death rune", "Feather"]),
        ([*NO_FLOORS, "--min-volume", "1000000"], ["Death rune", "Feather"]),
        ([*NO_FLOORS, "--f2p"], ["Death rune", "Feather"]),
        ([*NO_FLOORS, "--members"], [SYNAPSE, HALBERD]),
    ],
)
def test_filter_options(
    fake_client: FakeClient,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    expected: list[str],
) -> None:
    run(fake_client, *args)

    assert item_names(table_rows(capsys.readouterr().out)) == expected


def test_no_matches(fake_client: FakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    # The free to play items in the fixtures all have margins below the default floors.
    assert run(fake_client, "--f2p") == 0

    assert capsys.readouterr().out.strip() == "No flips match the current filters."


def test_members_and_f2p_are_mutually_exclusive(fake_client: FakeClient) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(fake_client, "--members", "--f2p")

    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    "args", [["--top", "0"], ["--min-volume", "-1"], ["--min-roi", "-2"], ["--sort", "name"]]
)
def test_invalid_options_are_rejected(fake_client: FakeClient, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(fake_client, *args)

    assert exit_info.value.code == 2


def test_missing_user_agent_exits_with_error(
    fake_client: FakeClient,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FLIPWATCH_USER_AGENT")

    assert run(fake_client) == 1

    assert "FLIPWATCH_USER_AGENT" in capsys.readouterr().err


def test_api_error_exits_with_error(capsys: pytest.CaptureFixture[str]) -> None:
    failing = FakeClient([], {}, {}, error=ApiError("/mapping returned HTTP 503"))

    assert run(failing) == 1

    assert capsys.readouterr().err.strip() == "error: /mapping returned HTTP 503"
