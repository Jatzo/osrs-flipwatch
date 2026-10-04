"""Settings loaded from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

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

SECONDS_PER_DAY = 24 * 60 * 60

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
class AlertSettings:
    min_margin: int = 10
    min_profit: int = 500_000
    # A fraction, so 0.01 is 1%. The environment variable takes a percentage.
    min_roi: float = 0.01
    min_volume: int = 50
    min_confidence: int = 40
    watchlist_only: bool = False
    cooldown_minutes: int = 60


@dataclass(frozen=True)
class Settings:
    # Only commands that call the prices API need this, so it is checked there.
    user_agent: str = ""
    api_base_url: str = DEFAULT_API_BASE_URL
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    db_path: str = DEFAULT_DB_PATH
    # Zero keeps stored price data forever.
    retention_days: int = DEFAULT_RETENTION_DAYS
    alerts: AlertSettings = field(default_factory=AlertSettings)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from `env`, or from the process environment and `.env` if not given."""
    if env is None:
        load_dotenv()
        env = os.environ

    return Settings(
        user_agent=env.get("FLIPWATCH_USER_AGENT", "").strip(),
        api_base_url=env.get("FLIPWATCH_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
        http_timeout_seconds=_positive_float(
            env, "FLIPWATCH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS
        ),
        db_path=env.get("FLIPWATCH_DB_PATH", "").strip() or DEFAULT_DB_PATH,
        retention_days=_non_negative_int(env, "FLIPWATCH_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
        alerts=_alert_settings(env),
    )


def require_user_agent(settings: Settings) -> None:
    """Refuse to talk to the prices API without a descriptive user agent."""
    if not settings.user_agent.strip():
        raise ConfigError(
            "FLIPWATCH_USER_AGENT is not set. The OSRS Wiki blocks generic user agents, "
            "so set it to something that names the project and gives a contact, for example "
            "'osrs-flipwatch (github.com/USERNAME/osrs-flipwatch)'."
        )


def _alert_settings(env: Mapping[str, str]) -> AlertSettings:
    defaults = AlertSettings()
    min_confidence = _non_negative_int(
        env, "FLIPWATCH_ALERT_MIN_CONFIDENCE", defaults.min_confidence
    )
    if min_confidence > 100:
        raise ConfigError(f"FLIPWATCH_ALERT_MIN_CONFIDENCE must be 0 to 100, got {min_confidence}")
    cooldown = _non_negative_int(env, "FLIPWATCH_ALERT_COOLDOWN_MINUTES", defaults.cooldown_minutes)
    if cooldown == 0:
        raise ConfigError("FLIPWATCH_ALERT_COOLDOWN_MINUTES must be at least 1")
    return AlertSettings(
        min_margin=_non_negative_int(env, "FLIPWATCH_ALERT_MIN_MARGIN", defaults.min_margin),
        min_profit=_non_negative_int(env, "FLIPWATCH_ALERT_MIN_PROFIT", defaults.min_profit),
        min_roi=_non_negative_float(env, "FLIPWATCH_ALERT_MIN_ROI", defaults.min_roi * 100) / 100,
        min_volume=_non_negative_int(env, "FLIPWATCH_ALERT_MIN_VOLUME", defaults.min_volume),
        min_confidence=min_confidence,
        watchlist_only=_flag(env, "FLIPWATCH_ALERT_WATCHLIST_ONLY", defaults.watchlist_only),
        cooldown_minutes=cooldown,
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


def _non_negative_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value < 0:
        raise ConfigError(f"{name} cannot be negative, got {raw!r}")
    return value


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")
