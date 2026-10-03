import sqlite3
from pathlib import Path
from typing import Any, Self

import pytest

from flipwatch.api import ApiError
from flipwatch.cli import main
from flipwatch.config import Settings
from flipwatch.models import Item, LatestPrice, PriceWindow
from flipwatch.store import SCHEMA_VERSION

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
        return [
            PriceWindow.from_api(item_id, point["timestamp"], point)
            for point in self._timeseries["data"]
        ]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "flipwatch.sqlite3"


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setattr("flipwatch.config.load_dotenv", lambda: None)
    monkeypatch.setenv("FLIPWATCH_USER_AGENT", "osrs-flipwatch-tests (example.test)")
    monkeypatch.setenv("FLIPWATCH_DB_PATH", str(db_path))


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


def run_command(client: FakeClient, *argv: str, now: float = NOW) -> int:
    def factory(settings: Settings) -> FakeClient:
        assert settings.user_agent
        return client

    return main(list(argv), client_factory=factory, clock=lambda: now)


def run(client: FakeClient, *args: str) -> int:
    return run_command(client, "scan", *args)


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


class TestCollect:
    def test_once_stores_snapshots(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_command(fake_client, "collect", "--once") == 0
        assert run_command(fake_client, "status") == 0

        output = capsys.readouterr().out
        assert (
            "Five minute snapshots: 13, from 2026-10-03 21:25 UTC to 2026-10-03 22:25 UTC" in output
        )
        assert "Missing windows: 0" in output
        # Seven items in the fixture window, less the excluded bond.
        assert "Stored 5m rows: 78" in output

    def test_running_once_twice_stores_nothing_new(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_command(fake_client, "collect", "--once")
        run_command(fake_client, "collect", "--once")
        run_command(fake_client, "status")

        assert "Five minute snapshots: 13," in capsys.readouterr().out

    def test_loop_stops_cleanly_on_ctrl_c(
        self,
        fake_client: FakeClient,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def interrupted(*args: object, **kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr("flipwatch.cli.collector.run_forever", interrupted)

        with caplog.at_level("INFO"):
            assert run_command(fake_client, "collect") == 0

        assert "Stopped" in caplog.text

    def test_api_failure_exits_with_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        failing = FakeClient([], {}, {}, error=ApiError("/mapping returned HTTP 503"))

        assert run_command(failing, "collect", "--once") == 1

        assert "error: /mapping returned HTTP 503" in capsys.readouterr().err


class TestSeed:
    def test_seeds_one_item(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_command(fake_client, "seed", "4151") == 0
        assert run_command(fake_client, "status") == 0

        output = capsys.readouterr().out
        assert "Added 12 new 5m rows for 1 item." in output
        assert "Five minute snapshots: none yet" in output
        assert "Stored 5m rows: 12" in output

    def test_seeds_another_timestep(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_command(fake_client, "seed", "4151", "560", "--timestep", "1h")

        assert "Added 24 new 1h rows for 2 items." in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("ids", "message"),
        [
            ([str(i) for i in range(1, 12)], "at most 10"),
            (["13190"], "excluded"),
            (["999999"], "unknown item ids"),
        ],
    )
    def test_rejected_items_exit_with_error(
        self,
        fake_client: FakeClient,
        capsys: pytest.CaptureFixture[str],
        ids: list[str],
        message: str,
    ) -> None:
        assert run_command(fake_client, "seed", *ids) == 1

        assert message in capsys.readouterr().err

    def test_rejects_unknown_timestep(self, fake_client: FakeClient) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run_command(fake_client, "seed", "4151", "--timestep", "10m")

        assert exit_info.value.code == 2


class TestStatus:
    def test_missing_database(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str], db_path: Path
    ) -> None:
        assert run_command(fake_client, "status") == 1

        assert "No database at" in capsys.readouterr().out
        assert not db_path.exists()

    def test_newer_database_is_refused(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str], db_path: Path
    ) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        assert run_command(fake_client, "status") == 1

        assert "newer than this code supports" in capsys.readouterr().err
