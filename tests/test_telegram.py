from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from crypto_smc.db.models import NotificationOutboxRecord, TelegramUserSettingsRecord
from crypto_smc.db.repositories.notifications import (
    PendingDelivery,
    TelegramUserSettings,
    delivery_policy,
    notification_window_is_open,
)
from crypto_smc.telegram.commands import (
    ActiveSignal,
    CoinAnalysis,
    PerformanceStats,
    ServiceStatus,
    TelegramCommandService,
)
from crypto_smc.telegram.outbox import (
    NotificationOutboxService,
    RetryableDeliveryError,
    UnknownDeliveryOutcome,
)
from crypto_smc.telegram.rendering import render_notification

NOW = datetime(2026, 6, 15, 10, tzinfo=UTC)


def settings(*, language: str = "ru", paused: bool = False) -> TelegramUserSettings:
    return TelegramUserSettings(
        user_id=42,
        language=language,
        minimum_score=85,
        schedule_timezone="Europe/Warsaw",
        schedule_start=time(7),
        schedule_end=time(20),
        risk_percent=Decimal(1),
        reference_balance=Decimal(10_000),
        paused=paused,
    )


def delivery() -> PendingDelivery:
    return PendingDelivery(
        delivery_id=1,
        outbox_id=2,
        user_id=42,
        event_type="new_signal",
        payload={
            "symbol": "BTCUSDT",
            "direction": "long",
            "score": 90,
            "entry_lower": "100",
            "entry_upper": "101",
            "stop_loss": "98",
            "take_profit_1": "104",
            "take_profit_2": "110",
            "risk_amount": "100",
        },
        language="ru",
        attempts=1,
    )


def test_warsaw_schedule_and_delivery_filters_are_deterministic() -> None:
    assert notification_window_is_open(
        NOW,
        timezone="Europe/Warsaw",
        start=time(7),
        end=time(20),
    )
    assert not notification_window_is_open(
        datetime(2026, 6, 15, 3, tzinfo=UTC),
        timezone="Europe/Warsaw",
        start=time(7),
        end=time(20),
    )

    outbox = NotificationOutboxRecord(
        idempotency_key="signal:1",
        event_type="new_signal",
        payload={"score": 90},
    )
    user = TelegramUserSettingsRecord(
        user_id=42,
        language="ru",
        minimum_score=95,
        schedule_timezone="Europe/Warsaw",
        schedule_start=time(7),
        schedule_end=time(20),
        paused=False,
    )
    assert delivery_policy(outbox, user, now=NOW) == (
        "skipped",
        "below_score_threshold",
    )
    outbox.event_type = "signal_result"
    assert delivery_policy(outbox, user, now=NOW) == (
        "skipped",
        "below_score_threshold",
    )
    outbox.payload = {"status": "ready"}
    assert delivery_policy(outbox, user, now=NOW) == ("pending", None)


def test_notifications_render_in_russian_and_english() -> None:
    payload = delivery().payload
    russian = render_notification("new_signal", payload, "ru")
    english = render_notification("new_signal", payload, "en")

    assert "НОВЫЙ СИГНАЛ" in russian
    assert "ЛОНГ" in russian
    assert "Вирт. риск: 100 USDT" in russian
    assert "NEW SIGNAL" in english
    assert "LONG" in english
    assert "Virtual risk: 100 USDT" in english


def test_breakeven_result_mentions_tp1_before_be() -> None:
    payload = delivery().payload | {
        "status": "stopped_at_breakeven",
        "realized_pnl": "28.9275",
        "r_multiple": "0.2893",
        "fees": "36.887",
        "estimated_funding": "-0.082",
    }

    russian = render_notification("signal_result", payload, "ru")
    english = render_notification("signal_result", payload, "en")

    assert "TP1 + BE" in russian
    assert "TP1 + BE" in english
    assert "Virtual PnL: 28.9275 USDT" in russian
    assert "Virtual PnL: 28.9275 USDT" in english
    assert "stopped_at_breakeven" not in english


def test_live_submitting_notification_omits_unknown_remaining_quantity() -> None:
    payload = delivery().payload | {
        "status": "entry_submitting",
        "qty": "0.69",
        "risk_usdt": "50",
        "notional_usdt": "8980.5",
        "estimated_margin_usdt": "449.025",
        "stop_loss": "72.4335",
    }

    message = render_notification("live_entry_submitting", payload, "ru")

    assert "Qty: 0.69" in message
    assert "Remaining:" not in message
    assert "Risk: 50 USDT" in message
    assert "Notional: 8980.5 USDT" in message
    assert "Est. margin: 449.025 USDT" in message


def test_live_pending_notification_mentions_limit_order() -> None:
    payload = delivery().payload | {
        "status": "entry_pending",
        "qty": "23.07",
        "remaining_qty": "23.07",
        "risk_usdt": "15.2",
        "leverage": "50",
        "notional_usdt": "10504.8092",
        "estimated_margin_usdt": "210.0962",
        "stop_loss": "456.0038",
    }

    message = render_notification("live_entry_pending", payload, "ru")

    assert "LIVE: LIMIT ORDER PLACED" in message
    assert "LIVE: POSITION OPEN" not in message
    assert "Status: entry_pending" in message
    assert "Remaining: 23.07" in message


def test_live_skipped_notification_is_not_rendered_as_failed_execution() -> None:
    payload = delivery().payload | {
        "status": "skipped",
        "qty": "23027",
        "risk_usdt": "20",
        "leverage": "50",
        "notional_usdt": "4790.6327",
        "estimated_margin_usdt": "95.8127",
        "stop_loss": "0.2072",
        "error": "live entry skipped: ask 0.20883 is above allowed 0.20848113",
    }

    message = render_notification("live_entry_skipped", payload, "ru")

    assert "LIVE: VIRTUAL ONLY" in message
    assert "LIVE: EXECUTION FAILED" not in message
    assert "Status: skipped" in message
    assert "live entry skipped" in message


def test_live_closed_notification_renders_real_pnl() -> None:
    payload = delivery().payload | {
        "status": "closed",
        "qty": "288",
        "remaining_qty": "0",
        "risk_usdt": "20",
        "notional_usdt": "4051.44",
        "stop_loss": "14.1367",
        "real_pnl_usdt": "4.2020167",
        "real_entry_price": "14.14053819",
        "real_exit_price": "14.09488889",
    }

    message = render_notification("live_position_closed", payload, "ru")

    assert "Real PnL: 4.202 USDT" in message
    assert "Real entry: 14.1405" in message
    assert "Real exit: 14.0949" in message


class FakeOutboxRepository:
    def __init__(self, pending: PendingDelivery) -> None:
        self.pending = pending
        self.available = True
        self.sent: list[int] = []
        self.retries: list[timedelta] = []
        self.failed: list[bool] = []

    async def recover_stale_sending(self, _: object) -> int:
        return 0

    async def materialize_pending(self, _: object, **__: object) -> int:
        return 0

    async def claim_delivery(self, _: object, **__: object) -> PendingDelivery | None:
        if not self.available:
            return None
        self.available = False
        return self.pending

    async def mark_retry(self, _: object, **kwargs: object) -> None:
        self.retries.append(kwargs["retry_after"])  # type: ignore[arg-type]
        self.pending = replace(self.pending, attempts=self.pending.attempts + 1)
        self.available = True

    async def mark_sent(self, _: object, **kwargs: object) -> None:
        message_id = kwargs["message_id"]
        assert isinstance(message_id, int)
        self.sent.append(message_id)

    async def mark_failed(self, _: object, **kwargs: object) -> None:
        self.failed.append(bool(kwargs["outcome_unknown"]))


class RetryThenSend:
    def __init__(self) -> None:
        self.calls = 0

    async def send(self, user_id: int, text: str) -> int:
        assert user_id == 42
        assert text
        self.calls += 1
        if self.calls == 1:
            raise RetryableDeliveryError("rate limited", retry_after_seconds=1)
        return 777


class UnknownSender:
    async def send(self, user_id: int, text: str) -> int:
        raise UnknownDeliveryOutcome("timeout after request")


@pytest.mark.asyncio
async def test_outbox_retries_known_failure_and_sends_once() -> None:
    repository = FakeOutboxRepository(delivery())
    sender = RetryThenSend()
    service = NotificationOutboxService(
        session_factory=object(),  # type: ignore[arg-type]
        sender=sender,
        user_ids=(42,),
        poll_seconds=1,
        batch_size=20,
        max_attempts=5,
        retry_base_seconds=2,
        repository=repository,  # type: ignore[arg-type]
    )

    await service.run_once()
    await service.run_once()
    await service.run_once()

    assert sender.calls == 2
    assert repository.sent == [777]
    assert repository.retries == [timedelta(seconds=2)]


@pytest.mark.asyncio
async def test_unknown_delivery_outcome_is_never_retried() -> None:
    repository = FakeOutboxRepository(delivery())
    service = NotificationOutboxService(
        session_factory=object(),  # type: ignore[arg-type]
        sender=UnknownSender(),
        user_ids=(42,),
        poll_seconds=1,
        batch_size=20,
        max_attempts=5,
        retry_base_seconds=2,
        repository=repository,  # type: ignore[arg-type]
    )

    await service.run_once()
    await service.run_once()

    assert repository.failed == [True]


class FakeSettingsRepository:
    def __init__(self, current: TelegramUserSettings | None) -> None:
        self.current = current

    async def get_user(self, _: object, user_id: int) -> TelegramUserSettings | None:
        return self.current if self.current and self.current.user_id == user_id else None

    async def update_user(
        self,
        _: object,
        user_id: int,
        **values: object,
    ) -> TelegramUserSettings:
        assert self.current is not None and self.current.user_id == user_id
        replace_settings = cast(Any, replace)
        self.current = replace_settings(self.current, **values)
        return self.current


class FakeQueries:
    async def active_signals(self, _: object) -> tuple[ActiveSignal, ...]:
        return (
            ActiveSignal(
                symbol="BTCUSDT",
                direction="long",
                status="active",
                score=90,
                entry_lower=Decimal(100),
                entry_upper=Decimal(101),
                stop_loss=Decimal(98),
                take_profit_1=Decimal(104),
                take_profit_2=Decimal(110),
            ),
        )

    async def latest_coin(self, _: object, symbol: str) -> CoinAnalysis | None:
        return CoinAnalysis(
            symbol=symbol,
            direction="long",
            status="accepted",
            score=90,
            strength="strong",
            evidence=("bullish_context",),
            suppression_reasons=(),
        )

    async def stats(self, _: object) -> PerformanceStats:
        return PerformanceStats(
            completed=10,
            wins=6,
            ambiguous=1,
            pnl=Decimal("250.5"),
            average_r=Decimal("0.4"),
            live_total=8,
            live_submitted=4,
            live_closed=2,
            live_skipped=3,
            live_failed=1,
            live_known_real_pnl=Decimal("12.34"),
            live_known_real_count=2,
        )

    async def status(self, _: object) -> ServiceStatus:
        return ServiceStatus(30, 0, 1, 0, 0)


class DegradedQueries(FakeQueries):
    async def status(self, _: object) -> ServiceStatus:
        return ServiceStatus(30, 2, 1, 0, 0)


@pytest.mark.asyncio
async def test_commands_support_localization_and_settings_updates() -> None:
    settings_repository = FakeSettingsRepository(settings())
    service = TelegramCommandService(
        session_factory=object(),  # type: ignore[arg-type]
        settings_repository=settings_repository,  # type: ignore[arg-type]
        query_repository=FakeQueries(),  # type: ignore[arg-type]
    )

    signals = await service.handle(42, "/signals")
    coin = await service.handle(42, "/coin btc")
    changed = await service.handle(42, "/language en")
    schedule = await service.handle(42, "/schedule 08:00 19:30 Europe/Warsaw")
    risk = await service.handle(42, "/risk 1.5 12000")
    stats = await service.handle(42, "/stats")
    paused = await service.handle(42, "/pause")

    assert "Активный сигнал" in signals
    assert "BTCUSDT" in coin
    assert "Language: en" in changed
    assert "08:00-19:30" in schedule
    assert "1.5%" in risk
    assert "Live" in stats
    assert "12.3400 USDT" in stats
    assert paused == "Notifications paused."
    assert settings_repository.current is not None
    assert settings_repository.current.paused is True


@pytest.mark.asyncio
async def test_status_renders_ready_and_degraded_separately() -> None:
    service = TelegramCommandService(
        session_factory=object(),  # type: ignore[arg-type]
        settings_repository=FakeSettingsRepository(settings(language="en")),  # type: ignore[arg-type]
        query_repository=DegradedQueries(),  # type: ignore[arg-type]
    )

    response = await service.handle(42, "/status")

    assert "Market ready: 30" in response
    assert "Market degraded: 2" in response
    assert "30/2" not in response


@pytest.mark.asyncio
async def test_unauthorized_user_receives_no_private_response() -> None:
    service = TelegramCommandService(
        session_factory=object(),  # type: ignore[arg-type]
        settings_repository=FakeSettingsRepository(None),  # type: ignore[arg-type]
        query_repository=FakeQueries(),  # type: ignore[arg-type]
    )

    assert await service.handle(999, "/signals") == ""
