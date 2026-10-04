import pytest

from flipwatch.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_DB_PATH,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_RETENTION_DAYS,
    AlertSettings,
    ConfigError,
    load_settings,
    require_user_agent,
)

USER_AGENT = "osrs-flipwatch (github.com/example/osrs-flipwatch)"


def test_loads_user_agent_and_defaults() -> None:
    settings = load_settings({"FLIPWATCH_USER_AGENT": USER_AGENT})

    assert settings.user_agent == USER_AGENT
    assert settings.api_base_url == DEFAULT_API_BASE_URL
    assert settings.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert settings.db_path == DEFAULT_DB_PATH
    assert settings.retention_days == DEFAULT_RETENTION_DAYS


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_user_agent_loads_but_is_refused_where_needed(value: str | None) -> None:
    env = {} if value is None else {"FLIPWATCH_USER_AGENT": value}

    settings = load_settings(env)

    assert settings.user_agent == ""
    with pytest.raises(ConfigError, match="FLIPWATCH_USER_AGENT"):
        require_user_agent(settings)


def test_user_agent_present_passes_the_check() -> None:
    require_user_agent(load_settings({"FLIPWATCH_USER_AGENT": USER_AGENT}))


def test_user_agent_is_trimmed() -> None:
    settings = load_settings({"FLIPWATCH_USER_AGENT": f"  {USER_AGENT}\n"})

    assert settings.user_agent == USER_AGENT


def test_overrides_are_read() -> None:
    settings = load_settings(
        {
            "FLIPWATCH_USER_AGENT": USER_AGENT,
            "FLIPWATCH_API_BASE_URL": "http://localhost:8000/api/",
            "FLIPWATCH_HTTP_TIMEOUT_SECONDS": "2.5",
            "FLIPWATCH_DB_PATH": "data/prices.sqlite3",
            "FLIPWATCH_RETENTION_DAYS": "0",
        }
    )

    assert settings.api_base_url == "http://localhost:8000/api"
    assert settings.http_timeout_seconds == 2.5
    assert settings.db_path == "data/prices.sqlite3"
    assert settings.retention_days == 0


@pytest.mark.parametrize("value", ["soon", "0", "-1"])
def test_invalid_timeout_is_rejected(value: str) -> None:
    env = {"FLIPWATCH_USER_AGENT": USER_AGENT, "FLIPWATCH_HTTP_TIMEOUT_SECONDS": value}

    with pytest.raises(ConfigError, match="FLIPWATCH_HTTP_TIMEOUT_SECONDS"):
        load_settings(env)


def test_reads_process_environment_when_no_mapping_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLIPWATCH_USER_AGENT", USER_AGENT)
    monkeypatch.setattr("flipwatch.config.load_dotenv", lambda: None)

    assert load_settings().user_agent == USER_AGENT


@pytest.mark.parametrize("value", ["forever", "1.5", "-1"])
def test_invalid_retention_is_rejected(value: str) -> None:
    env = {"FLIPWATCH_USER_AGENT": USER_AGENT, "FLIPWATCH_RETENTION_DAYS": value}

    with pytest.raises(ConfigError, match="FLIPWATCH_RETENTION_DAYS"):
        load_settings(env)


def test_alert_defaults() -> None:
    assert load_settings({"FLIPWATCH_USER_AGENT": USER_AGENT}).alerts == AlertSettings()


def test_alert_settings_are_read() -> None:
    settings = load_settings(
        {
            "FLIPWATCH_USER_AGENT": USER_AGENT,
            "FLIPWATCH_ALERT_MIN_MARGIN": "25",
            "FLIPWATCH_ALERT_MIN_PROFIT": "1000000",
            "FLIPWATCH_ALERT_MIN_ROI": "2.5",
            "FLIPWATCH_ALERT_MIN_VOLUME": "100",
            "FLIPWATCH_ALERT_MIN_CONFIDENCE": "60",
            "FLIPWATCH_ALERT_WATCHLIST_ONLY": "yes",
            "FLIPWATCH_ALERT_COOLDOWN_MINUTES": "15",
        }
    )

    assert settings.alerts == AlertSettings(
        min_margin=25,
        min_profit=1_000_000,
        min_roi=0.025,
        min_volume=100,
        min_confidence=60,
        watchlist_only=True,
        cooldown_minutes=15,
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FLIPWATCH_ALERT_MIN_MARGIN", "ten"),
        ("FLIPWATCH_ALERT_MIN_ROI", "-1"),
        ("FLIPWATCH_ALERT_MIN_CONFIDENCE", "101"),
        ("FLIPWATCH_ALERT_WATCHLIST_ONLY", "maybe"),
        ("FLIPWATCH_ALERT_COOLDOWN_MINUTES", "0"),
    ],
)
def test_invalid_alert_settings_are_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_settings({"FLIPWATCH_USER_AGENT": USER_AGENT, name: value})
