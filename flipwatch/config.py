"""Settings loaded from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_API_BASE_URL = "https://prices.runescape.wiki/api/v1/osrs"
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0


class ConfigError(Exception):
    """Raised when a required setting is missing or a value is invalid."""


@dataclass(frozen=True)
class Settings:
    user_agent: str
    api_base_url: str = DEFAULT_API_BASE_URL
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from `env`, or from the process environment and `.env` if not given."""
    if env is None:
        load_dotenv()
        env = os.environ

    user_agent = env.get("FLIPWATCH_USER_AGENT", "").strip()
    if not user_agent:
        raise ConfigError(
            "FLIPWATCH_USER_AGENT is not set. The OSRS Wiki blocks generic user agents, "
            "so set it to something that names the project and gives a contact, for example "
            "'osrs-flipwatch (github.com/USERNAME/osrs-flipwatch)'."
        )

    return Settings(
        user_agent=user_agent,
        api_base_url=env.get("FLIPWATCH_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
        http_timeout_seconds=_positive_float(
            env, "FLIPWATCH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS
        ),
    )


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero, got {raw!r}")
    return value
