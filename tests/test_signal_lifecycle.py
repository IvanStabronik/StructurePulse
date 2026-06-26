import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.db.repositories.signals import (
    SignalTransitionResult,
    TrackingSignalView,
)
from crypto_smc.providers.bybit.trade_websocket import TradeStreamReadyEvent
from crypto_smc.providers.models import Candle1m, PublicTrade
from crypto_smc.signals.coverage import merge_trade_coverage
from crypto_smc.signals.fallback import evaluate_closed_candle
from crypto_smc.signals.funding import estimate_funding_cost
from crypto_smc.signals.lifecycle import LifecycleState, evaluate_public_trade
from crypto_smc.signals.service import SignalLifecycleService

NOW = datetime.now(UTC).replace(microsecond=0)


def trade(
    trade_id: str,
    price: str,
    *,
    milliseconds: int = 0,
    sequence: int = 1,
) -> PublicTrade:
    return PublicTrade(
        trade_id=trade_id,
        symbol="BTCUSDT",
        price=Decimal(price),
        size=Decimal(1),
        side="Buy",
        executed_at=NOW + timedelta(milliseconds=milliseconds),
        sequence=sequence,
    )


def lifecycle_state(status: str = "active") -> LifecycleState:
    return LifecycleState(
        signal_id=1,
        symbol="BTCUSDT",
        direction="long",
        status=status,  # type: ignore[arg-type]
        entry_lower=Decimal(99),
        entry_upper=Decimal(101),
        planned_entry=Decimal(100),
        stop_loss=Decimal(95),
        take_profit_1=Decimal(105),
        take_profit_2=Decimal(110),
        quantity=Decimal(20),
        risk_amount=Decimal(100),
        taker_fee_rate=Decimal("0.00055"),
        expires_at=NOW + timedelta(minutes=90),
        current_stop=Decimal(95 if status != "tp1_reached" else "100.1100605"),
    )


def candle(
    *,
    open_time: datetime,
    open_price: str = "100",
    high_price: str = "104",
    low_price: str = "96",
    close_price: str = "100",
) -> Candle1m:
    return Candle1m(
        symbol="BTCUSDT",
        open_time=open_time,
        open_price=Decimal(open_price),
        high_price=Decimal(high_price),
        low_price=Decimal(low_price),
        close_price=Decimal(close_price),
        volume=Decimal(1),
        turnover=Decimal(100),
    )


def tracking_signal(
    *,
    status: str = "preparing",
    last_trade_time: datetime | None = None,
) -> TrackingSignalView:
    return TrackingSignalView(
        id=1,
        symbol="BTCUSDT",
        direction="long",
        status=status,
        entry_lower=Decimal(99),
        entry_upper=Decimal(101),
        planned_entry=Decimal(100),
        stop_loss=Decimal(95),
        take_profit_1=Decimal(105),
        take_profit_2=Decimal(110),
        quantity=Decimal(20),
        risk_amount=Decimal(100),
        taker_fee_rate=Decimal("0.00055"),
        expires_at=NOW + timedelta(minutes=90),
        created_at=NOW,
        current_stop=Decimal(95),
        remaining_quantity=Decimal(20),
        last_trade_id=None,
        last_trade_time=last_trade_time,
        last_trade_sequence=None,
    )


def test_coverage_requires_rest_depth_and_identity_overlap() -> None:
    rest = (trade("old", "102", milliseconds=-1000), trade("shared", "101"))
    websocket = (trade("shared", "101"), trade("new", "100", milliseconds=1000))

    coverage = merge_trade_coverage(rest, websocket, coverage_start=NOW)
    missing = merge_trade_coverage(
        rest,
        (trade("different", "100"),),
        coverage_start=NOW,
    )

    assert coverage.proven is True
    assert [item.trade_id for item in coverage.trades] == ["shared", "new"]
    assert missing.reason == "rest_websocket_overlap_missing"


def test_exact_trades_drive_entry_tp1_and_tp2_without_ambiguity() -> None:
    entry = evaluate_public_trade(lifecycle_state(), trade("entry", "100"))
    tp1 = evaluate_public_trade(lifecycle_state("entered"), trade("tp1", "105"))
    tp2 = evaluate_public_trade(lifecycle_state("tp1_reached"), trade("tp2", "110"))

    assert [action.target for action in entry] == ["entered"]
    assert [action.target for action in tp1] == ["tp1_reached"]
    assert tp1[0].current_stop is not None
    assert tp1[0].current_stop > Decimal(100)
    assert [action.target for action in tp2] == ["tp2_completed"]
    assert tp2[0].realized_pnl is not None and tp2[0].realized_pnl > 0


def test_trade_beyond_stop_before_entry_invalidates_signal() -> None:
    actions = evaluate_public_trade(lifecycle_state(), trade("gap", "94"))

    assert [action.target for action in actions] == ["invalidated"]


def test_direct_trade_at_tp2_emits_ordered_partial_and_final_exits() -> None:
    actions = evaluate_public_trade(lifecycle_state("entered"), trade("tp2", "111"))

    assert [action.target for action in actions] == ["tp1_reached", "tp2_completed"]
    assert actions[0].remaining_quantity == Decimal(10)
    assert actions[1].remaining_quantity == 0


def test_fallback_marks_same_candle_stop_and_target_ambiguous() -> None:
    actions = evaluate_closed_candle(
        lifecycle_state("entered"),
        candle(
            open_time=NOW,
            high_price="111",
            low_price="94",
        ),
    )

    assert [action.target for action in actions] == ["ambiguous"]
    assert actions[0].realized_pnl is not None
    assert actions[0].realized_pnl < 0


def test_fallback_allows_entry_in_last_full_candle_before_expiration() -> None:
    state = replace(
        lifecycle_state(),
        expires_at=NOW + timedelta(minutes=1),
    )
    actions = evaluate_closed_candle(
        state,
        candle(
            open_time=NOW,
            high_price="101",
            low_price="99",
        ),
    )

    assert [action.target for action in actions] == ["entered"]


def test_fallback_is_mirrored_for_short_stop_and_target() -> None:
    state = replace(
        lifecycle_state("entered"),
        direction="short",
        stop_loss=Decimal(105),
        current_stop=Decimal(105),
        take_profit_1=Decimal(95),
        take_profit_2=Decimal(90),
    )
    actions = evaluate_closed_candle(
        state,
        candle(
            open_time=NOW,
            high_price="106",
            low_price="89",
        ),
    )

    assert [action.target for action in actions] == ["ambiguous"]
    assert actions[0].realized_pnl is not None
    assert actions[0].realized_pnl < 0


def test_funding_estimate_is_directional_and_halves_after_tp1() -> None:
    entered = replace(
        lifecycle_state("entered"),
        entered_at=NOW,
        funding_rate=Decimal("0.0001"),
        funding_interval_minutes=480,
    )
    long_cost = estimate_funding_cost(
        entered,
        event_time=NOW + timedelta(hours=8),
        target="stopped",
    )
    short_cost = estimate_funding_cost(
        replace(entered, direction="short"),
        event_time=NOW + timedelta(hours=8),
        target="stopped",
    )
    after_tp1 = estimate_funding_cost(
        replace(
            entered,
            status="tp1_reached",
            tp1_reached_at=NOW + timedelta(hours=4),
        ),
        event_time=NOW + timedelta(hours=8),
        target="tp2_completed",
    )

    assert long_cost == Decimal("0.2")
    assert short_cost == Decimal("-0.2")
    assert after_tp1 == Decimal("0.15")


class FakeProvider:
    def __init__(self, trades: tuple[PublicTrade, ...]) -> None:
        self.trades = trades

    async def get_recent_public_trades(
        self,
        *,
        symbol: str,
        limit: int,
    ) -> list[PublicTrade]:
        assert symbol == "BTCUSDT"
        assert limit == 1000
        return list(self.trades)

    async def close(self) -> None:
        return None


class FakeStream:
    def __init__(self, buffered: tuple[PublicTrade, ...]) -> None:
        self.buffered = buffered
        self.events: asyncio.Queue[object] = asyncio.Queue()
        self.active: set[str] = set()
        self.stopped = False

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.active))

    async def subscribe(self, symbol: str) -> datetime:
        self.active.add(symbol)
        await self.events.put(TradeStreamReadyEvent(symbol, NOW, False))
        return NOW

    def buffered_trades(
        self,
        symbol: str,
        *,
        since: datetime,
    ) -> tuple[PublicTrade, ...]:
        return tuple(item for item in self.buffered if item.executed_at >= since)

    async def next_event(self) -> object:
        return await self.events.get()

    async def unsubscribe(self, symbol: str) -> None:
        self.active.discard(symbol)

    async def stop(self) -> None:
        self.stopped = True


class FakeRepository:
    def __init__(self, signal: TrackingSignalView) -> None:
        self.signal = signal
        self.transitions: list[str] = []
        self.transition_kwargs: list[dict[str, object]] = []
        self.checkpoints: list[str] = []
        self.tracking_events: list[str] = []

    async def list_tracking_signals(self, _: object) -> tuple[TrackingSignalView, ...]:
        return (self.signal,)

    async def apply_transition(self, _: object, **kwargs: object) -> SignalTransitionResult:
        target = str(kwargs["target"])
        self.transitions.append(target)
        self.transition_kwargs.append(kwargs)
        self.signal = replace(self.signal, status=target)
        if kwargs.get("current_stop") is not None:
            self.signal = replace(
                self.signal,
                current_stop=kwargs["current_stop"],  # type: ignore[arg-type]
            )
        return SignalTransitionResult(True, self.signal.id, target)

    async def checkpoint_trade(self, _: object, **kwargs: object) -> None:
        self.checkpoints.append(str(kwargs["trade_id"]))

    async def record_tracking_event(self, _: object, **kwargs: object) -> bool:
        self.tracking_events.append(str(kwargs["event_type"]))
        return True


class FakeFallbackRepository:
    def __init__(self, candles: tuple[Candle1m, ...]) -> None:
        self.candles = candles
        self.requests: list[tuple[datetime, datetime]] = []

    async def load_reconciled_1m_window(
        self,
        _: object,
        *,
        symbol: str,
        start_open_time: datetime,
        end_open_time: datetime,
        stream: str = "kline_1m",
    ) -> tuple[Candle1m, ...]:
        assert symbol == "BTCUSDT"
        assert stream == "kline_1m"
        self.requests.append((start_open_time, end_open_time))
        return self.candles


class FakeLiveExecution:
    def __init__(self) -> None:
        self.signal_ids: list[int] = []

    async def handle_signal_id(self, signal_id: int) -> None:
        self.signal_ids.append(signal_id)


@pytest.mark.asyncio
async def test_service_activates_only_after_overlap_and_replays_handshake_trade() -> None:
    old = trade("old", "102", milliseconds=-1000)
    shared = trade("shared", "100")
    repository = FakeRepository(tracking_signal())
    stream = FakeStream((shared,))
    live_execution = FakeLiveExecution()
    service = SignalLifecycleService(
        provider=FakeProvider((old, shared)),
        stream=stream,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recent_trade_limit=1000,
        checkpoint_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
        live_execution=live_execution,
    )

    task = asyncio.create_task(service.run())
    for _ in range(100):
        if repository.transitions == ["active", "entered"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.transitions == ["active", "entered"]
    assert live_execution.signal_ids == [1]
    assert repository.checkpoints == ["shared"]
    assert stream.stopped is True


@pytest.mark.asyncio
async def test_service_recovers_entered_signal_and_replays_downtime_stop() -> None:
    old = trade("checkpoint", "100", milliseconds=-1000)
    stopped = trade("stopped", "94")
    repository = FakeRepository(
        tracking_signal(status="entered", last_trade_time=NOW - timedelta(seconds=1))
    )
    stream = FakeStream((stopped,))
    service = SignalLifecycleService(
        provider=FakeProvider((old, stopped)),
        stream=stream,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recent_trade_limit=1000,
        checkpoint_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(service.run())
    for _ in range(100):
        if repository.transitions == ["stopped"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.transitions == ["stopped"]
    assert stream.active == set()


@pytest.mark.asyncio
async def test_service_includes_funding_in_final_pnl_and_r_multiple() -> None:
    old = trade("checkpoint", "100", milliseconds=-1000)
    stopped = trade("stopped", "94")
    repository = FakeRepository(
        replace(
            tracking_signal(
                status="entered",
                last_trade_time=NOW - timedelta(seconds=1),
            ),
            entered_at=NOW - timedelta(hours=8),
            funding_rate=Decimal("0.0001"),
            funding_interval_minutes=480,
        )
    )
    stream = FakeStream((stopped,))
    service = SignalLifecycleService(
        provider=FakeProvider((old, stopped)),
        stream=stream,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recent_trade_limit=1000,
        checkpoint_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(service.run())
    for _ in range(100):
        if repository.transitions == ["stopped"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    transition = repository.transition_kwargs[0]
    assert transition["estimated_funding"] == Decimal("0.2")
    assert transition["realized_pnl"] == Decimal("-102.34500")
    assert transition["r_multiple"] == Decimal("-1.02345")


@pytest.mark.asyncio
async def test_service_uses_conservative_candle_fallback_when_overlap_fails() -> None:
    anchor = NOW - timedelta(minutes=2)
    repository = FakeRepository(
        replace(
            tracking_signal(status="entered", last_trade_time=anchor),
            entered_at=anchor - timedelta(minutes=10),
        )
    )
    stream_trade = trade("websocket-only", "100")
    stream = FakeStream((stream_trade,))
    fallback = FakeFallbackRepository(
        (
            candle(
                open_time=anchor.replace(second=0, microsecond=0),
                high_price="111",
                low_price="94",
            ),
        )
    )
    service = SignalLifecycleService(
        provider=FakeProvider((trade("rest-only", "100", milliseconds=-1000),)),
        stream=stream,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recent_trade_limit=1000,
        checkpoint_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
        fallback_repository=fallback,
    )

    task = asyncio.create_task(service.run())
    for _ in range(100):
        if repository.transitions == ["ambiguous"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.transitions == ["ambiguous"]
    assert repository.transition_kwargs[0]["ambiguous"] is True
    assert str(repository.transition_kwargs[0]["resolution_note"]).startswith(
        "conservative_1m_fallback:"
    )
    assert fallback.requests
    assert stream.active == set()


@pytest.mark.asyncio
async def test_service_audits_fallback_recovery_without_level_touch() -> None:
    anchor = NOW - timedelta(minutes=2)
    repository = FakeRepository(tracking_signal(status="active", last_trade_time=anchor))
    stream = FakeStream((trade("websocket-only", "103"),))
    fallback = FakeFallbackRepository(
        (
            candle(
                open_time=anchor.replace(second=0, microsecond=0),
                high_price="98",
                low_price="96",
                close_price="97",
            ),
        )
    )
    service = SignalLifecycleService(
        provider=FakeProvider((trade("rest-only", "103", milliseconds=-1000),)),
        stream=stream,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recent_trade_limit=1000,
        checkpoint_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
        fallback_repository=fallback,
    )

    task = asyncio.create_task(service.run())
    for _ in range(100):
        if repository.tracking_events:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.transitions == []
    assert repository.tracking_events == ["trade_coverage_fallback_recovered"]
    assert repository.checkpoints == ["websocket-only"]
