import sqlite3
from pathlib import Path

import pytest

from flipwatch.api import ApiError
from flipwatch.cli import main
from flipwatch.config import Settings
from flipwatch.models import Item, PriceWindow
from flipwatch.store import SCHEMA_VERSION, Store
from tests.fakes import FakeClient

NOW = 1_791_066_600
SYNAPSE = "Tormented synapse"
HALBERD = "Noxious halberd"
# Turns off the default margin and profit floors so the cheap fixture items show up.
NO_FLOORS = ["--min-margin", "0", "--min-profit", "0"]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "flipwatch.sqlite3"


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setattr("flipwatch.config.load_dotenv", lambda: None)
    monkeypatch.setenv("FLIPWATCH_USER_AGENT", "osrs-flipwatch-tests (example.test)")
    monkeypatch.setenv("FLIPWATCH_DB_PATH", str(db_path))


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


@pytest.fixture
def market_db(db_path: Path) -> Path:
    """Two days of five minute windows for two items with a steady margin."""
    start = NOW - 2 * 24 * 3600
    with Store.open(db_path) as store:
        store.save_items([make_item(1, "Steady sword"), make_item(2, "Steady shield")])
        for i in range(2 * 24 * 12):
            timestamp = start + i * 300
            windows = {
                item_id: PriceWindow(item_id, timestamp, 1_200, 1_000, 1_000, 1_000)
                for item_id in (1, 2)
            }
            store.save_snapshot("5m", timestamp, windows, collected_at=timestamp)
    return db_path


def make_item(item_id: int, name: str, buy_limit: int | None = 5_000) -> Item:
    return Item(item_id, name, True, buy_limit, 100, 60, 40, f"{name}.png")


class TestBacktest:
    def test_prints_report_and_saves_the_run(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str], market_db: Path
    ) -> None:
        assert run_command(fake_client, "backtest", "--days", "1") == 0

        output = capsys.readouterr().out
        assert output.startswith("Backtest 1: margin strategy, 5 minute windows")
        assert "(24.0 hours), 2 items, 50,000,000 starting capital" in output
        assert "Steady sword" in output
        assert "not a promise of future profit" in output
        with Store.open(market_db) as store:
            runs = store.backtest_runs()
        assert [run.strategy for run in runs] == ["margin"]
        assert runs[0].realised_profit > 0

    def test_options_reach_the_backtest(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str], market_db: Path
    ) -> None:
        args = [
            "backtest", "--strategy", "dip", "--items", "1", "--capital", "1.5m",
            "--fill-share", "5", "--offer-hours", "2", "--start", "2026-10-02",
        ]  # fmt: skip

        assert run_command(fake_client, *args) == 0

        with Store.open(market_db) as store:
            result = store.backtest_run(store.backtest_runs()[0].id)
        assert result is not None
        assert result.strategy == "dip"
        assert result.settings.starting_capital == 1_500_000
        assert result.settings.fill_share == pytest.approx(0.05)
        assert result.settings.offer_lifetime_seconds == 7_200
        # Midnight UTC on 2 October 2026. The stored data starts earlier than that.
        assert result.start == 1_790_899_200

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--items", "1", "99"], "no stored item with a buy limit for ids [99]"),
            (["--timestep", "1h"], "no 1h data stored yet"),
            (["--start", "2026-10-03", "--end", "2026-10-01"], "start must be before the end"),
            (["--start", "2020-01-01", "--end", "2020-01-02"], "no stored data"),
        ],
    )
    def test_errors(
        self,
        fake_client: FakeClient,
        capsys: pytest.CaptureFixture[str],
        market_db: Path,
        args: list[str],
        message: str,
    ) -> None:
        assert run_command(fake_client, "backtest", *args) == 1

        assert message in capsys.readouterr().err

    def test_missing_database(
        self, fake_client: FakeClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_command(fake_client, "backtest") == 1

        assert "No database at" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "args",
        [
            ["--capital", "lots"],
            ["--capital", "0"],
            ["--days", "2", "--start", "2026-10-01"],
            ["--items", "1", "--top", "5"],
            ["--start", "1st October"],
            ["--strategy", "martingale"],
        ],
    )
    def test_invalid_options(self, fake_client: FakeClient, args: list[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run_command(fake_client, "backtest", *args)

        assert exit_info.value.code == 2
