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
from crypto_smc.providers.models import PublicTrade
from crypto_smc.signals.coverage import merge_trade_coverage
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
        self.checkpoints: list[str] = []

    async def list_tracking_signals(self, _: object) -> tuple[TrackingSignalView, ...]:
        return (self.signal,)

    async def apply_transition(self, _: object, **kwargs: object) -> SignalTransitionResult:
        target = str(kwargs["target"])
        self.transitions.append(target)
        self.signal = replace(self.signal, status=target)
        if kwargs.get("current_stop") is not None:
            self.signal = replace(
                self.signal,
                current_stop=kwargs["current_stop"],  # type: ignore[arg-type]
            )
        return SignalTransitionResult(True, self.signal.id, target)

    async def checkpoint_trade(self, _: object, **kwargs: object) -> None:
        self.checkpoints.append(str(kwargs["trade_id"]))


@pytest.mark.asyncio
async def test_service_activates_only_after_overlap_and_replays_handshake_trade() -> None:
    old = trade("old", "102", milliseconds=-1000)
    shared = trade("shared", "100")
    repository = FakeRepository(tracking_signal())
    stream = FakeStream((shared,))
    service = SignalLifecycleService(
        provider=FakeProvider((old, shared)),
        stream=stream,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recent_trade_limit=1000,
        checkpoint_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
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
