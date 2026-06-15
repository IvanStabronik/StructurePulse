from collections.abc import Mapping

Language = str

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "unauthorized": "Доступ запрещён.",
        "help": (
            "Команды: /signals, /coin SYMBOL, /settings, /status, /stats, "
            "/language ru|en, /threshold SCORE, "
            "/schedule HH:MM HH:MM [TIMEZONE], /risk PERCENT BALANCE, "
            "/pause, /resume"
        ),
        "no_signals": "Активных сигналов нет.",
        "coin_missing": "Укажите символ, например: /coin BTCUSDT",
        "coin_not_found": "Анализ для {symbol} не найден.",
        "invalid_value": "Некорректное значение.",
        "updated": "Настройки обновлены.",
        "paused": "Уведомления приостановлены.",
        "resumed": "Уведомления возобновлены.",
    },
    "en": {
        "unauthorized": "Access denied.",
        "help": (
            "Commands: /signals, /coin SYMBOL, /settings, /status, /stats, "
            "/language ru|en, /threshold SCORE, "
            "/schedule HH:MM HH:MM [TIMEZONE], /risk PERCENT BALANCE, "
            "/pause, /resume"
        ),
        "no_signals": "There are no active signals.",
        "coin_missing": "Provide a symbol, for example: /coin BTCUSDT",
        "coin_not_found": "No analysis found for {symbol}.",
        "invalid_value": "Invalid value.",
        "updated": "Settings updated.",
        "paused": "Notifications paused.",
        "resumed": "Notifications resumed.",
    },
}


def text(language: Language, key: str, **values: object) -> str:
    translations: Mapping[str, str] = TEXTS.get(language, TEXTS["en"])
    template = translations.get(key, TEXTS["en"].get(key, key))
    return template.format(**values)
