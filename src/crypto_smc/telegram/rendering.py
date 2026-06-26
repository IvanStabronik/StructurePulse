from decimal import Decimal, InvalidOperation
from typing import Any


def render_notification(
    event_type: str,
    payload: dict[str, object],
    language: str,
) -> str:
    symbol = str(payload.get("symbol", "?"))
    direction = _direction(str(payload.get("direction", "?")), language)
    status = _status(str(payload.get("status", "?")), language)
    if event_type in {"service_warning", "service_recovered"}:
        recovered = event_type == "service_recovered"
        title = (
            ("СЕРВИС ВОССТАНОВЛЕН" if recovered else "СЕРВИС ДЕГРАДИРОВАН")
            if language == "ru"
            else ("SERVICE RECOVERED" if recovered else "SERVICE DEGRADED")
        )
        service_label = "Сервис" if language == "ru" else "Service"
        status_label = "Статус" if language == "ru" else "Status"
        reason_label = "Причина" if language == "ru" else "Reason"
        return "\n".join(
            (
                title,
                f"{service_label}: {payload.get('service', '?')}",
                f"{status_label}: {status}",
                f"{reason_label}: {payload.get('reason', '?')}",
            )
        )
    if event_type == "new_signal":
        title = "НОВЫЙ СИГНАЛ" if language == "ru" else "NEW SIGNAL"
        labels = (
            ("Оценка", "Вход", "Стоп", "Вирт. риск")
            if language == "ru"
            else ("Score", "Entry", "Stop", "Virtual risk")
        )
        return "\n".join(
            (
                f"{title}: {symbol} {direction}",
                f"{labels[0]}: {payload.get('score', '?')}/100",
                (
                    f"{labels[1]}: {_number(payload.get('entry_lower'))}"
                    f"-{_number(payload.get('entry_upper'))}"
                ),
                f"{labels[2]}: {_number(payload.get('stop_loss'))}",
                f"TP1: {_number(payload.get('take_profit_1'))}",
                f"TP2: {_number(payload.get('take_profit_2'))}",
                f"{labels[3]}: {_number(payload.get('risk_amount'))} USDT",
            )
        )
    if event_type == "entry_filled":
        title = "ВИРТУАЛЬНЫЙ ВХОД" if language == "ru" else "VIRTUAL ENTRY"
        entry = "Вход" if language == "ru" else "Entry"
        return f"{title}: {symbol} {direction}\n{entry}: {_number(payload.get('planned_entry'))}"
    if event_type in {
        "live_entry_submitting",
        "live_entry_pending",
        "live_entry_open",
        "live_tp1_reduced",
        "live_position_closed",
        "live_execution_failed",
        "live_entry_skipped",
    }:
        titles = {
            "live_entry_submitting": "LIVE: SUBMITTING ORDER",
            "live_entry_pending": "LIVE: LIMIT ORDER PLACED",
            "live_entry_open": "LIVE: POSITION OPEN",
            "live_tp1_reduced": "LIVE: TP1 HALF CLOSED",
            "live_position_closed": "LIVE: POSITION CLOSED",
            "live_execution_failed": "LIVE: EXECUTION FAILED",
            "live_entry_skipped": "LIVE: VIRTUAL ONLY",
        }
        lines = [
            f"{titles[event_type]}: {symbol} {direction}",
            f"Status: {payload.get('status', '?')}",
            f"Qty: {_number(payload.get('qty'))}",
            f"Stop: {_number(payload.get('stop_loss'))}",
        ]
        if payload.get("remaining_qty") is not None:
            lines.insert(3, f"Remaining: {_number(payload.get('remaining_qty'))}")
        if payload.get("risk_usdt") is not None:
            lines.insert(-1, f"Risk: {_number(payload.get('risk_usdt'))} USDT")
        if payload.get("leverage") is not None:
            lines.insert(-1, f"Leverage: {_number(payload.get('leverage'))}x")
        if payload.get("notional_usdt") is not None:
            lines.insert(-1, f"Notional: {_number(payload.get('notional_usdt'))} USDT")
        if payload.get("estimated_margin_usdt") is not None:
            lines.insert(-1, f"Est. margin: {_number(payload.get('estimated_margin_usdt'))} USDT")
        if payload.get("real_pnl_usdt") is not None:
            lines.append(f"Real PnL: {_number(payload.get('real_pnl_usdt'))} USDT")
        if payload.get("real_entry_price") is not None:
            lines.append(f"Real entry: {_number(payload.get('real_entry_price'))}")
        if payload.get("real_exit_price") is not None:
            lines.append(f"Real exit: {_number(payload.get('real_exit_price'))}")
        if payload.get("error"):
            lines.append(f"Error: {payload.get('error')}")
        return "\n".join(lines)
    if event_type == "take_profit_1":
        title = "ВИРТУАЛЬНЫЙ TP1" if language == "ru" else "VIRTUAL TP1"
        stop = "Стоп" if language == "ru" else "Stop"
        return (
            f"{title}: {symbol}\n"
            f"{stop}: BE\n"
            f"Virtual PnL: {_number(payload.get('realized_pnl'))} USDT"
        )
    if event_type == "signal_warning":
        title = "ПРЕДУПРЕЖДЕНИЕ" if language == "ru" else "WARNING"
        status_label = "Статус" if language == "ru" else "Status"
        return f"{title}: {symbol}\n{status_label}: {status}"
    if event_type in {"signal_expired", "signal_invalidated"}:
        title = "СИГНАЛ ЗАКРЫТ" if language == "ru" else "SIGNAL CLOSED"
        status_label = "Статус" if language == "ru" else "Status"
        return f"{title}: {symbol}\n{status_label}: {status}"
    title = "ВИРТУАЛЬНЫЙ РЕЗУЛЬТАТ" if language == "ru" else "VIRTUAL RESULT"
    result_labels = (
        ("Статус", "Комиссии", "Фандинг") if language == "ru" else ("Status", "Fees", "Funding")
    )
    return "\n".join(
        (
            f"{title}: {symbol} {direction}",
            f"{result_labels[0]}: {status}",
            f"Virtual PnL: {_number(payload.get('realized_pnl'))} USDT",
            f"R: {_number(payload.get('r_multiple'))}",
            f"{result_labels[1]}: {_number(payload.get('fees'))} USDT",
            f"{result_labels[2]}: {_number(payload.get('estimated_funding'))} USDT",
        )
    )


def render_settings(settings: Any, language: str) -> str:
    paused = (
        ("да" if settings.paused else "нет")
        if language == "ru"
        else ("yes" if settings.paused else "no")
    )
    labels = (
        ("Язык", "Мин. score", "Расписание", "Риск", "Баланс", "Пауза")
        if language == "ru"
        else ("Language", "Min score", "Schedule", "Risk", "Balance", "Paused")
    )
    return "\n".join(
        (
            f"{labels[0]}: {settings.language}",
            f"{labels[1]}: {settings.minimum_score}",
            (
                f"{labels[2]}: {settings.schedule_start:%H:%M}-"
                f"{settings.schedule_end:%H:%M} {settings.schedule_timezone}"
            ),
            f"{labels[3]}: {_number(settings.risk_percent)}%",
            f"{labels[4]}: {_number(settings.reference_balance)} USDT",
            f"{labels[5]}: {paused}",
        )
    )


def _number(value: object) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    normalized = number.quantize(Decimal("0.0001")).normalize()
    return format(normalized, "f")


def _direction(direction: str, language: str) -> str:
    if language == "ru":
        return {"long": "ЛОНГ", "short": "ШОРТ"}.get(direction, direction.upper())
    return direction.upper()


def _status(status: str, language: str) -> str:
    if language != "ru":
        return {
            "stopped_at_breakeven": "TP1 + BE",
        }.get(status, status)
    return {
        "active": "активен",
        "entered": "в позиции",
        "tp1_reached": "TP1",
        "stopped": "стоп",
        "stopped_at_breakeven": "TP1 + BE",
        "tp2_completed": "TP2",
        "ambiguous": "неоднозначно",
        "coverage_failed": "нет надёжного покрытия",
        "expired": "истёк",
        "invalidated": "инвалидирован",
        "degraded": "деградирован",
        "ready": "готов",
    }.get(status, status)
