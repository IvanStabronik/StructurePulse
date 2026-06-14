from crypto_smc.config import Settings


def test_settings_parse_telegram_user_ids() -> None:
    settings = Settings(telegram_allowed_user_ids="123, 456")  # type: ignore[arg-type]

    assert settings.telegram_allowed_user_ids == (123, 456)


def test_settings_normalize_log_level() -> None:
    settings = Settings(log_level="debug")

    assert settings.log_level == "DEBUG"
