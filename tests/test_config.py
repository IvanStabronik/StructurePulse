from crypto_smc.config import Settings


def test_settings_parse_telegram_user_ids() -> None:
    settings = Settings(telegram_allowed_user_ids="123, 456")  # type: ignore[arg-type]

    assert settings.telegram_allowed_user_ids == (123, 456)


def test_settings_normalize_log_level() -> None:
    settings = Settings(log_level="debug")

    assert settings.log_level == "DEBUG"


def test_signal_protection_defaults_are_bounded() -> None:
    settings = Settings()

    assert settings.signal_cooldown_minutes == 60
    assert settings.signal_maximum_active == 5
    assert settings.signal_maximum_per_hour == 10
    assert settings.signal_burst_window_minutes == 5
    assert settings.signal_burst_maximum == 3
    assert settings.signal_pause_on_abnormal_btc is True
