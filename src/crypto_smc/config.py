from datetime import time
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    worker_metrics_port: int = Field(default=8001, ge=1, le=65_535)
    worker_health_timeout_seconds: float = Field(default=2, gt=0, le=30)
    runtime_quiesce_seconds: float = Field(default=2, ge=0, le=30)
    runtime_shutdown_timeout_seconds: float = Field(default=15, gt=0, le=120)
    event_loop_probe_interval_seconds: float = Field(default=0.5, ge=0.1, le=10)
    event_loop_warning_seconds: float = Field(default=0.25, gt=0, le=10)
    required_database_revision: str = "0013"
    migration_lock_timeout_seconds: int = Field(default=15, ge=1, le=300)
    migration_statement_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    maintenance_interval_seconds: float = Field(default=86_400, ge=60, le=604_800)
    maintenance_candle_1m_retention_days: int = Field(default=180, ge=30, le=3650)
    maintenance_candle_agg_retention_days: int = Field(default=730, ge=30, le=3650)
    maintenance_delete_batch_size: int = Field(default=5000, ge=100, le=50_000)
    operational_warning_delay_seconds: float = Field(default=300, ge=30, le=3600)
    operational_warning_cooldown_seconds: int = Field(default=1800, ge=300, le=86_400)
    operational_monitor_interval_seconds: float = Field(default=30, ge=5, le=300)

    database_url: str = "postgresql+asyncpg://crypto_smc:crypto_smc@localhost:5432/crypto_smc"

    bybit_base_url: str = "https://api.bybit.com"
    bybit_request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    bybit_instrument_page_size: int = Field(default=1000, ge=1, le=1000)
    bybit_max_requests_per_second: float = Field(default=8.0, gt=0, le=100)
    bybit_max_concurrency: int = Field(default=4, ge=1, le=20)
    bybit_max_retries: int = Field(default=5, ge=0, le=10)
    bybit_retry_base_seconds: float = Field(default=0.5, gt=0, le=10)
    bybit_ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    bybit_ws_shard_size: int = Field(default=15, ge=1, le=100)
    bybit_ws_queue_size: int = Field(default=2000, ge=100, le=100_000)
    bybit_ws_heartbeat_seconds: float = Field(default=20, ge=5, le=60)
    bybit_ws_reconnect_base_seconds: float = Field(default=1, gt=0, le=30)
    bybit_ws_reconnect_max_seconds: float = Field(default=30, ge=1, le=300)
    bybit_ws_ready_timeout_seconds: float = Field(default=30, gt=0, le=120)
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None
    bybit_account_type: Literal["UNIFIED"] = "UNIFIED"
    bybit_recv_window_ms: int = Field(default=5000, ge=1000, le=60_000)

    execution_enabled: bool = False
    execution_mode: Literal["disabled", "approval", "auto"] = "disabled"
    execution_order_budget_usdt: Decimal = Field(default=Decimal("50"), gt=0, le=100_000)
    execution_risk_usdt: Decimal = Field(default=Decimal("50"), gt=0, le=100_000)
    execution_min_risk_usdt: Decimal = Field(default=Decimal("20"), gt=0, le=100_000)
    execution_max_open_positions: int = Field(default=1, ge=1, le=30)
    execution_max_trades_per_day: int = Field(default=2, ge=1, le=100)
    execution_max_daily_loss_usdt: Decimal = Field(default=Decimal("30"), gt=0, le=100_000)
    execution_max_slippage_bps: Decimal = Field(default=Decimal("20"), ge=0, le=1000)
    execution_leverage: Decimal = Field(default=Decimal("1"), ge=1, le=100)
    execution_max_effective_leverage: Decimal = Field(default=Decimal("45"), ge=1, le=100)
    execution_min_signal_score: int = Field(default=80, ge=0, le=100)
    execution_max_notional_to_wallet_ratio: Decimal = Field(default=Decimal("6"), ge=1, le=100)
    execution_symbol_allowlist: Annotated[frozenset[str], NoDecode] = frozenset()
    execution_symbol_denylist: Annotated[frozenset[str], NoDecode] = frozenset()
    execution_tp1_close_fraction: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    execution_move_stop_to_be_after_tp1: bool = True
    execution_pending_entry_timeout_seconds: int = Field(default=180, ge=60, le=86_400)
    execution_poll_interval_seconds: float = Field(default=1, ge=0.2, le=30)

    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str | None = None
    coingecko_api_key_type: Literal["demo", "pro"] = "demo"
    coingecko_request_timeout_seconds: float = Field(default=15.0, gt=0, le=60)

    universe_size: int = Field(default=30, ge=1, le=60)
    universe_ranking_fetch_limit: int = Field(default=150, ge=30, le=250)
    universe_min_turnover_24h_usdt: Decimal = Field(default=Decimal("10000000"), ge=0)
    universe_max_spread_bps: Decimal = Field(default=Decimal("20"), ge=0)
    universe_min_trading_history_days: int = Field(default=30, ge=0)
    universe_manual_denylist: Annotated[frozenset[str], NoDecode] = frozenset()

    market_data_initial_history_minutes: int = Field(default=10_080, ge=60, le=259_200)
    market_data_sync_interval_seconds: int = Field(default=60, ge=10, le=3600)
    market_data_backfill_batch_candles: int = Field(default=1000, ge=1, le=1000)
    market_data_max_parallel_symbols: int = Field(default=3, ge=1, le=20)

    aggregation_job_batch_size: int = Field(default=100, ge=1, le=1000)
    aggregation_source_scan_batch_size: int = Field(default=5000, ge=100, le=50_000)
    aggregation_poll_interval_seconds: float = Field(default=0.25, ge=0.05, le=10)
    aggregation_cpu_budget_ms: float = Field(default=25, ge=1, le=1000)
    aggregation_stale_job_seconds: float = Field(default=300, ge=30, le=3600)
    aggregation_reconciliation_interval_seconds: float = Field(
        default=900,
        ge=60,
        le=86_400,
    )
    aggregation_reconciliation_sample_size: int = Field(default=4, ge=1, le=100)

    strategy_analysis_interval_seconds: float = Field(default=60, ge=10, le=3600)
    strategy_profile: Literal["strict", "aggressive_test"] = "strict"
    strategy_history_candles: int = Field(default=300, ge=50, le=5000)
    strategy_minimum_history_candles: int = Field(default=30, ge=20, le=1000)
    strategy_process_workers: int = Field(default=2, ge=1, le=16)
    strategy_max_pending_batches: int = Field(default=1, ge=1, le=20)
    strategy_minimum_net_reward_to_risk: Decimal = Field(default=Decimal("1.5"), gt=0, le=10)
    strategy_minimum_stop_percent: Decimal = Field(default=Decimal("0.002"), ge=0, lt=1)
    strategy_maximum_entry_chase_to_tp1: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    strategy_maximum_entry_adverse_to_stop: Decimal = Field(default=Decimal("0.40"), ge=0, le=1)
    strategy_max_trade_notional_usdt: Decimal = Field(default=Decimal("2000"), ge=0)

    signal_cooldown_minutes: int = Field(default=60, ge=0, le=1440)
    signal_maximum_active: int = Field(default=5, ge=1, le=30)
    signal_maximum_per_hour: int = Field(default=10, ge=1, le=100)
    signal_burst_window_minutes: int = Field(default=5, ge=1, le=60)
    signal_burst_maximum: int = Field(default=3, ge=1, le=30)
    signal_pause_on_abnormal_btc: bool = False
    signal_trade_queue_size: int = Field(default=20_000, ge=100, le=200_000)
    signal_trade_buffer_size: int = Field(default=5000, ge=1000, le=100_000)
    signal_trade_recent_limit: int = Field(default=1000, ge=1, le=1000)
    signal_trade_poll_interval_seconds: float = Field(default=1, ge=0.1, le=30)
    signal_trade_checkpoint_interval_seconds: float = Field(
        default=1,
        ge=0.1,
        le=60,
    )

    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: Annotated[tuple[int, ...], NoDecode] = ()
    telegram_default_language: Literal["ru", "en"] = "ru"
    telegram_schedule_timezone: str = "Europe/Warsaw"
    telegram_schedule_start: time = time(7, 0)
    telegram_schedule_end: time = time(20, 0)
    telegram_outbox_poll_seconds: float = Field(default=1, ge=0.1, le=60)
    telegram_outbox_batch_size: int = Field(default=20, ge=1, le=100)
    telegram_outbox_max_attempts: int = Field(default=5, ge=1, le=20)
    telegram_retry_base_seconds: float = Field(default=2, ge=0.1, le=300)

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
            normalized = value.strip()
            if normalized.startswith("[") and normalized.endswith("]"):
                normalized = normalized[1:-1]
            if not normalized:
                return ()
            return tuple(int(item.strip()) for item in normalized.split(",") if item.strip())
        return value

    @field_validator("universe_manual_denylist", mode="before")
    @classmethod
    def parse_denylist(cls, value: object) -> object:
        return _parse_upper_set(value)

    @field_validator("execution_symbol_allowlist", "execution_symbol_denylist", mode="before")
    @classmethod
    def parse_execution_symbol_set(cls, value: object) -> object:
        return _parse_symbol_set(value)


def _parse_symbol_set(value: object) -> object:
    parsed = _parse_upper_set(value)
    if isinstance(parsed, frozenset):
        return frozenset(_normalize_symbol(item) for item in parsed)
    return parsed


def _parse_upper_set(value: object) -> object:
    if value is None or value == "":
        return frozenset()
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.startswith("[") and normalized.endswith("]"):
            normalized = normalized[1:-1]
        if not normalized:
            return frozenset()
        return frozenset(item.strip().upper() for item in normalized.split(",") if item.strip())
    return value


def _normalize_symbol(value: str) -> str:
    symbol = value.upper()
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


@lru_cache
def get_settings() -> Settings:
    return Settings()
