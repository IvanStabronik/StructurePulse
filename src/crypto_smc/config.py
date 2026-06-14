from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    debug_api_enabled: bool = False

    database_url: str = "postgresql+asyncpg://crypto_smc:crypto_smc@localhost:5432/crypto_smc"

    bybit_base_url: str = "https://api.bybit.com"
    bybit_request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    bybit_instrument_page_size: int = Field(default=1000, ge=1, le=1000)

    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str | None = None
    coingecko_api_key_type: Literal["demo", "pro"] = "demo"
    coingecko_request_timeout_seconds: float = Field(default=15.0, gt=0, le=60)

    universe_size: int = Field(default=30, ge=1, le=60)
    universe_ranking_fetch_limit: int = Field(default=150, ge=30, le=250)
    universe_min_turnover_24h_usdt: Decimal = Field(default=Decimal("10000000"), ge=0)
    universe_max_spread_bps: Decimal = Field(default=Decimal("20"), ge=0)
    universe_min_trading_history_days: int = Field(default=30, ge=0)
    universe_manual_denylist: frozenset[str] = frozenset()

    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: tuple[int, ...] = ()

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("universe_manual_denylist", mode="before")
    @classmethod
    def parse_denylist(cls, value: object) -> object:
        if value is None or value == "":
            return frozenset()
        if isinstance(value, str):
            return frozenset(item.strip().upper() for item in value.split(",") if item.strip())
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
