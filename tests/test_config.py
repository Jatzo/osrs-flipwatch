import pytest

from flipwatch.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_DB_PATH,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    ConfigError,
    load_settings,
)

USER_AGENT = "osrs-flipwatch (github.com/example/osrs-flipwatch)"


def test_loads_user_agent_and_defaults() -> None:
    settings = load_settings({"FLIPWATCH_USER_AGENT": USER_AGENT})

    assert settings.user_agent == USER_AGENT
    assert settings.api_base_url == DEFAULT_API_BASE_URL
    assert settings.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert settings.db_path == DEFAULT_DB_PATH


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_user_agent_is_rejected(value: str | None) -> None:
    env = {} if value is None else {"FLIPWATCH_USER_AGENT": value}

    with pytest.raises(ConfigError, match="FLIPWATCH_USER_AGENT"):
        load_settings(env)


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
        }
    )

    assert settings.api_base_url == "http://localhost:8000/api"
    assert settings.http_timeout_seconds == 2.5
    assert settings.db_path == "data/prices.sqlite3"


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
