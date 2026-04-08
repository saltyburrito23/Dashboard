from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    app_key: str | None
    app_secret: str | None
    callback_url: str
    default_symbol: str
    default_min_dte: int
    default_max_dte: int
    default_strike_window: int
    default_strike_count: int
    default_refresh_seconds: int
    default_expiry_chart_max_dte: int
    history_period_type: str
    history_period: int
    history_frequency_type: str
    history_frequency: int
    default_stream_option_limit: int
    vix_odte_multiplier: float

    @property
    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret)


def load_settings() -> Settings:
    return Settings(
        app_key=os.getenv("APP_KEY"),
        app_secret=os.getenv("APP_SECRET"),
        callback_url=os.getenv("CALLBACK_URL", "https://127.0.0.1"),
        default_symbol=os.getenv("DEFAULT_SYMBOL", "SPY").strip().upper(),
        default_min_dte=_int_env("DEFAULT_MIN_DTE", 0),
        default_max_dte=_int_env("DEFAULT_MAX_DTE", 30),
        default_strike_window=_int_env("DEFAULT_STRIKE_WINDOW", 8),
        default_strike_count=_int_env("DEFAULT_STRIKE_COUNT", 30),
        default_refresh_seconds=_int_env("DEFAULT_REFRESH_SECONDS", 120),
        default_expiry_chart_max_dte=_int_env("DEFAULT_EXPIRY_CHART_MAX_DTE", 60),
        history_period_type=os.getenv("HISTORY_PERIOD_TYPE", "day"),
        history_period=_int_env("HISTORY_PERIOD", 1),
        history_frequency_type=os.getenv("HISTORY_FREQUENCY_TYPE", "minute"),
        history_frequency=_int_env("HISTORY_FREQUENCY", 5),
        default_stream_option_limit=_int_env("DEFAULT_STREAM_OPTION_LIMIT", 8),
        vix_odte_multiplier=_float_env("VIX_ODTE_MULTIPLIER", 1.15),
    )
