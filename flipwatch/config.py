"""Settings loaded from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_API_BASE_URL = "https://prices.runescape.wiki/api/v1/osrs"
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
DEFAULT_DB_PATH = "flipwatch.sqlite3"
DEFAULT_RETENTION_DAYS = 90

# Grand Exchange tax, checked against the OSRS Wiki Grand Exchange page in October 2026.
# The rate rose from 1% to 2% on 29 May 2025. A whole number percentage keeps the
# rounding exact, since the game rounds the tax down to the nearest coin.
GE_TAX_RATE_PERCENT = 2
GE_TAX_CAP = 5_000_000

# A buy limit applies to a rolling four hour window that starts with the first purchase.
BUY_LIMIT_WINDOW_SECONDS = 4 * 60 * 60
# Members get eight Grand Exchange offer slots, free to play accounts get three.
GE_OFFER_SLOTS = 8

GE_TAX_EXEMPT_ITEM_IDS: frozenset[int] = frozenset(
    {
        13190,  # Old school bond
        # The wiki lists "Energy potion" without a dose, so every dose is treated as exempt.
        3008,  # Energy potion(4)
        3010,  # Energy potion(3)
        3012,  # Energy potion(2)
        3014,  # Energy potion(1)
        882,  # Bronze arrow
        806,  # Bronze dart
        884,  # Iron arrow
        807,  # Iron dart
        558,  # Mind rune
        886,  # Steel arrow
        808,  # Steel dart
        365,  # Bass
        2309,  # Bread
        1891,  # Cake
        2140,  # Cooked chicken
        2142,  # Cooked meat
        347,  # Herring
        379,  # Lobster
        355,  # Mackerel
        2327,  # Meat pie
        351,  # Pike
        329,  # Salmon
        315,  # Shrimps
        361,  # Tuna
        8011,  # Ardougne teleport (tablet)
        8010,  # Camelot teleport (tablet)
        28824,  # Civitas illa fortis teleport
        8009,  # Falador teleport (tablet)
        3853,  # Games necklace(8)
        28790,  # Kourend castle teleport (tablet)
        8008,  # Lumbridge teleport (tablet)
        2552,  # Ring of dueling(8)
        8013,  # Teleport to house (tablet)
        8007,  # Varrock teleport (tablet)
        1755,  # Chisel
        5325,  # Gardening trowel
        1785,  # Glassblowing pipe
        2347,  # Hammer
        1733,  # Needle
        233,  # Pestle and mortar
        5341,  # Rake
        8794,  # Saw
        5329,  # Secateurs
        5343,  # Seed dibber
        1735,  # Shears
        952,  # Spade
        5331,  # Watering can
    }
)

# Items that are never worth flipping, so they are left out of scans and stored data.
EXCLUDED_ITEM_IDS: frozenset[int] = frozenset(
    {
        # A bond bought on the GE becomes untradeable, and making it tradeable again
        # costs 10% of its value, which is far more than any normal margin.
        13190,  # Old school bond
    }
)


class ConfigError(Exception):
    """Raised when a required setting is missing or a value is invalid."""


@dataclass(frozen=True)
class Settings:
    user_agent: str
    api_base_url: str = DEFAULT_API_BASE_URL
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    db_path: str = DEFAULT_DB_PATH
    # Zero keeps stored price data forever.
    retention_days: int = DEFAULT_RETENTION_DAYS


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
            "'osrs-flipwatch (github.com/USERNAME/OSRS-Flipping-Tool)'."
        )

    return Settings(
        user_agent=user_agent,
        api_base_url=env.get("FLIPWATCH_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
        http_timeout_seconds=_positive_float(
            env, "FLIPWATCH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS
        ),
        db_path=env.get("FLIPWATCH_DB_PATH", "").strip() or DEFAULT_DB_PATH,
        retention_days=_non_negative_int(env, "FLIPWATCH_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
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


def _non_negative_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if value < 0:
        raise ConfigError(f"{name} cannot be negative, got {raw!r}")
    return value
