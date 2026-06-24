from datetime import time
from decimal import Decimal

from crypto_smc.config import Settings
from crypto_smc.worker.__main__ import _strategy_config


def test_settings_parse_telegram_user_ids() -> None:
    settings = Settings(telegram_allowed_user_ids="123, 456")

    assert settings.telegram_allowed_user_ids == (123, 456)


def test_settings_parse_single_and_json_telegram_user_ids() -> None:
    assert Settings(telegram_allowed_user_ids="123").telegram_allowed_user_ids == (123,)
    assert Settings(telegram_allowed_user_ids="[123, 456]").telegram_allowed_user_ids == (
        123,
        456,
    )


def test_settings_parse_empty_and_json_universe_denylist() -> None:
    assert Settings(universe_manual_denylist="").universe_manual_denylist == frozenset()
    assert Settings(universe_manual_denylist="[]").universe_manual_denylist == frozenset()
    assert Settings(universe_manual_denylist="ton, h").universe_manual_denylist == frozenset(
        {"TON", "H"}
    )


def test_settings_parse_execution_symbol_lists_as_usdt_symbols() -> None:
    settings = Settings(
        execution_symbol_allowlist="btc, ETHUSDT",
        execution_symbol_denylist="[ada, sui]",
    )

    assert settings.execution_symbol_allowlist == frozenset({"BTCUSDT", "ETHUSDT"})
    assert settings.execution_symbol_denylist == frozenset({"ADAUSDT", "SUIUSDT"})


def test_settings_normalize_log_level() -> None:
    settings = Settings(log_level="debug")

    assert settings.log_level == "DEBUG"


def test_signal_protection_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.signal_cooldown_minutes == 60
    assert settings.signal_maximum_active == 5
    assert settings.signal_maximum_per_hour == 10
    assert settings.signal_burst_window_minutes == 5
    assert settings.signal_burst_maximum == 3
    assert settings.signal_pause_on_abnormal_btc is True
    assert settings.signal_trade_recent_limit == 1000
    assert settings.telegram_default_language == "ru"
    assert settings.telegram_schedule_timezone == "Europe/Warsaw"
    assert settings.telegram_schedule_start == time(7, 0)
    assert settings.telegram_schedule_end == time(20, 0)
    assert settings.signal_trade_checkpoint_interval_seconds == 1
    assert settings.required_database_revision == "0013"
    assert settings.runtime_shutdown_timeout_seconds == 15
    assert settings.maintenance_candle_1m_retention_days == 180
    assert settings.operational_warning_cooldown_seconds == 1800
    assert settings.strategy_profile == "strict"
    assert settings.bybit_account_type == "UNIFIED"
    assert settings.bybit_recv_window_ms == 5000
    assert settings.execution_enabled is False
    assert settings.execution_mode == "disabled"
    assert settings.execution_order_budget_usdt == 50
    assert settings.execution_risk_usdt == 50
    assert settings.execution_min_risk_usdt == 20
    assert settings.execution_max_open_positions == 1
    assert settings.execution_max_trades_per_day == 2
    assert settings.execution_max_daily_loss_usdt == 10
    assert settings.execution_poll_interval_seconds == 1
    assert settings.execution_max_effective_leverage == 45
    assert settings.execution_min_signal_score == 85
    assert settings.execution_max_notional_to_wallet_ratio == 5
    assert settings.execution_symbol_allowlist == frozenset()
    assert settings.execution_symbol_denylist == frozenset()
    assert settings.execution_tp1_close_fraction == Decimal("0.5")
    assert settings.execution_move_stop_to_be_after_tp1 is True
    assert settings.execution_pending_entry_timeout_seconds == 1200
    assert settings.strategy_minimum_stop_percent == Decimal("0.004")
    assert settings.strategy_max_trade_notional_usdt == 0


def test_aggressive_profile_aligns_paper_risk_with_live_risk() -> None:
    config = _strategy_config("aggressive_test", live_risk_usdt=Decimal("50"))

    assert config.risk_amount == Decimal("50")
    assert config.reference_balance == Decimal("5000")
    assert config.version == "smc-v1.1.2-aggressive-test-risk-50-stop-0p004-notional-0"
    assert config.require_15m_displacement is False
    assert config.require_entry_zone_retest is False
    assert config.ignore_active_evaluation_window is True
    assert config.minimum_stop_percent == Decimal("0.004")
