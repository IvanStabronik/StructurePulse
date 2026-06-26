from crypto_smc.telegram.rendering import render_notification


def test_virtual_notifications_are_explicitly_labeled() -> None:
    entry_message = render_notification(
        "entry_filled",
        {"symbol": "SOLUSDT", "direction": "long", "planned_entry": "70.5"},
        "ru",
    )
    tp1_message = render_notification(
        "take_profit_1",
        {"symbol": "SOLUSDT", "realized_pnl": "10"},
        "ru",
    )
    result_message = render_notification(
        "stopped",
        {
            "symbol": "SOLUSDT",
            "direction": "long",
            "status": "stopped",
            "realized_pnl": "-50",
            "r_multiple": "-1",
            "fees": "0",
            "estimated_funding": "0",
        },
        "ru",
    )

    assert "ВИРТУАЛЬНЫЙ ВХОД" in entry_message
    assert "ВИРТУАЛЬНЫЙ TP1" in tp1_message
    assert "ВИРТУАЛЬНЫЙ РЕЗУЛЬТАТ" in result_message
