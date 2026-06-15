import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol, cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.signals import (
    SignalRepository,
    TrackingSignalView,
)
from crypto_smc.observability.metrics import SIGNAL_COVERAGE_RESULTS
from crypto_smc.providers.bybit.trade_websocket import (
    PublicTradeEvent,
    TradeStreamDisconnectedEvent,
    TradeStreamEvent,
    TradeStreamReadyEvent,
)
from crypto_smc.providers.models import PublicTrade
from crypto_smc.providers.protocols import PublicTradeProvider
from crypto_smc.signals.coverage import merge_trade_coverage
from crypto_smc.signals.lifecycle import LifecycleState, evaluate_public_trade
from crypto_smc.signals.state_machine import SignalStatus

logger = structlog.get_logger(__name__)


class PublicTradeStream(Protocol):
    @property
    def symbols(self) -> tuple[str, ...]: ...

    async def subscribe(self, symbol: str) -> datetime: ...

    def buffered_trades(
        self,
        symbol: str,
        *,
        since: datetime,
    ) -> tuple[PublicTrade, ...]: ...

    async def next_event(self) -> TradeStreamEvent: ...

    async def unsubscribe(self, symbol: str) -> None: ...

    async def stop(self) -> None: ...


class SignalLifecycleService:
    def __init__(
        self,
        *,
        provider: PublicTradeProvider,
        stream: PublicTradeStream,
        session_factory: async_sessionmaker[AsyncSession],
        poll_interval_seconds: float,
        recent_trade_limit: int,
        checkpoint_interval_seconds: float,
        repository: SignalRepository | None = None,
    ) -> None:
        self._provider = provider
        self._stream = stream
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._recent_trade_limit = recent_trade_limit
        self._checkpoint_interval_seconds = checkpoint_interval_seconds
        self._repository = repository or SignalRepository()
        self._signals: dict[str, TrackingSignalView] = {}
        self._coverage_pending: dict[str, datetime] = {}
        self._last_checkpoint: dict[str, float] = {}

    async def run(self) -> None:
        await self._reconcile()
        next_reconcile = monotonic() + self._poll_interval_seconds
        try:
            while True:
                timeout = max(0.0, next_reconcile - monotonic())
                try:
                    event = await asyncio.wait_for(
                        self._stream.next_event(),
                        timeout=timeout,
                    )
                except TimeoutError:
                    await self._reconcile()
                    next_reconcile = monotonic() + self._poll_interval_seconds
                else:
                    await self._handle_event(event)
        finally:
            await self._stream.stop()

    async def _reconcile(self) -> None:
        tracking = await self._repository.list_tracking_signals(self._session_factory)
        current = {item.symbol: item for item in tracking}
        for symbol in set(self._signals) - set(current):
            await self._stream.unsubscribe(symbol)
            self._coverage_pending.pop(symbol, None)
            self._last_checkpoint.pop(symbol, None)
        self._signals = current

        now = datetime.now(UTC)
        for symbol, signal in tuple(self._signals.items()):
            if signal.status in {"preparing", "active"} and signal.expires_at <= now:
                await self._transition(
                    signal,
                    target="expired",
                    event_time=signal.expires_at,
                    idempotency_key=f"signal:{signal.id}:expired",
                    event_type="signal_expired",
                )
                continue
            if symbol not in self._stream.symbols:
                await self._stream.subscribe(symbol)
                self._coverage_pending[symbol] = signal.coverage_anchor

    async def _handle_event(self, event: TradeStreamEvent) -> None:
        if isinstance(event, TradeStreamDisconnectedEvent):
            signal = self._signals.get(event.symbol)
            if signal is not None:
                self._coverage_pending[event.symbol] = event.disconnected_at
            return
        if isinstance(event, TradeStreamReadyEvent):
            await self._establish_coverage(event.symbol)
            return
        if isinstance(event, PublicTradeEvent):
            if event.trade.symbol in self._coverage_pending:
                return
            await self._process_trade(event.trade)

    async def _establish_coverage(self, symbol: str) -> None:
        signal = self._signals.get(symbol)
        anchor = self._coverage_pending.get(symbol)
        if signal is None or anchor is None:
            return
        rest = tuple(
            await self._provider.get_recent_public_trades(
                symbol=symbol,
                limit=self._recent_trade_limit,
            )
        )
        buffered = self._stream.buffered_trades(symbol, since=anchor)
        coverage = merge_trade_coverage(
            rest,
            buffered,
            coverage_start=anchor,
        )
        if not coverage.proven:
            SIGNAL_COVERAGE_RESULTS.labels(result=coverage.reason or "failed").inc()
            target: SignalStatus = (
                "ambiguous" if signal.status in {"entered", "tp1_reached"} else "coverage_failed"
            )
            await self._transition(
                signal,
                target=target,
                event_time=datetime.now(UTC),
                idempotency_key=f"signal:{signal.id}:coverage:{int(anchor.timestamp() * 1000)}",
                event_type="trade_coverage_failed",
                ambiguous=target == "ambiguous",
                resolution_note=coverage.reason,
            )
            self._coverage_pending.pop(symbol, None)
            return

        SIGNAL_COVERAGE_RESULTS.labels(result="proven").inc()
        if signal.status == "preparing":
            await self._transition(
                signal,
                target="active",
                event_time=anchor,
                idempotency_key=f"signal:{signal.id}:coverage:{int(anchor.timestamp() * 1000)}",
                event_type="trade_coverage_ready",
                payload={"trade_count": len(coverage.trades)},
            )
        self._coverage_pending.pop(symbol, None)
        for trade in coverage.trades:
            if symbol not in self._signals:
                break
            await self._process_trade(trade, force_checkpoint=False)
        if coverage.trades and symbol in self._signals:
            await self._checkpoint(self._signals[symbol], coverage.trades[-1])

    async def _process_trade(
        self,
        trade: PublicTrade,
        *,
        force_checkpoint: bool = True,
    ) -> None:
        signal = self._signals.get(trade.symbol)
        if signal is None:
            return
        state = _lifecycle_state(signal)
        for index, action in enumerate(evaluate_public_trade(state, trade)):
            await self._transition(
                signal,
                target=action.target,
                event_time=trade.executed_at,
                idempotency_key=(f"trade:{signal.id}:{trade.trade_id}:{action.target}:{index}"),
                event_type=action.event_type,
                source_event_id=trade.trade_id,
                payload={
                    "price": str(trade.price),
                    "size": str(trade.size),
                    "sequence": trade.sequence,
                },
                realized_pnl=action.realized_pnl,
                fees=action.fees,
                r_multiple=action.r_multiple,
                current_stop=action.current_stop,
                remaining_quantity=action.remaining_quantity,
            )
            signal = self._signals.get(trade.symbol)
            if signal is None:
                return
            state = _lifecycle_state(signal)
        if force_checkpoint:
            last = self._last_checkpoint.get(trade.symbol, 0.0)
            if monotonic() - last >= self._checkpoint_interval_seconds:
                await self._checkpoint(signal, trade)

    async def _transition(
        self,
        signal: TrackingSignalView,
        *,
        target: SignalStatus,
        event_time: datetime,
        idempotency_key: str,
        event_type: str,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
        realized_pnl: Decimal | None = None,
        fees: Decimal | None = None,
        r_multiple: Decimal | None = None,
        ambiguous: bool | None = None,
        resolution_note: str | None = None,
        current_stop: Decimal | None = None,
        remaining_quantity: Decimal | None = None,
    ) -> None:
        result = await self._repository.apply_transition(
            self._session_factory,
            signal_id=signal.id,
            target=target,
            event_time=event_time,
            idempotency_key=idempotency_key,
            event_type=event_type,
            source_event_id=source_event_id,
            payload=payload,
            realized_pnl=realized_pnl,
            fees=fees,
            r_multiple=r_multiple,
            ambiguous=ambiguous,
            resolution_note=resolution_note,
            current_stop=current_stop,
            remaining_quantity=remaining_quantity,
        )
        if not result.applied:
            await self._refresh_signal(signal.symbol)
            return
        terminal = target in {
            "expired",
            "invalidated",
            "stopped",
            "stopped_at_breakeven",
            "tp2_completed",
            "ambiguous",
            "coverage_failed",
        }
        if terminal:
            self._signals.pop(signal.symbol, None)
            await self._stream.unsubscribe(signal.symbol)
            return
        self._signals[signal.symbol] = replace(
            signal,
            status=target,
            current_stop=(current_stop if current_stop is not None else signal.current_stop),
            remaining_quantity=(
                remaining_quantity if remaining_quantity is not None else signal.remaining_quantity
            ),
        )

    async def _checkpoint(
        self,
        signal: TrackingSignalView,
        trade: PublicTrade,
    ) -> None:
        await self._repository.checkpoint_trade(
            self._session_factory,
            signal_id=signal.id,
            trade_id=trade.trade_id,
            executed_at=trade.executed_at,
            sequence=trade.sequence,
        )
        self._last_checkpoint[signal.symbol] = monotonic()
        self._signals[signal.symbol] = replace(
            signal,
            last_trade_id=trade.trade_id,
            last_trade_time=trade.executed_at,
            last_trade_sequence=trade.sequence,
        )

    async def _refresh_signal(self, symbol: str) -> None:
        tracking = await self._repository.list_tracking_signals(self._session_factory)
        refreshed = next((item for item in tracking if item.symbol == symbol), None)
        if refreshed is None:
            self._signals.pop(symbol, None)
        else:
            self._signals[symbol] = refreshed


def _lifecycle_state(signal: TrackingSignalView) -> LifecycleState:
    return LifecycleState(
        signal_id=signal.id,
        symbol=signal.symbol,
        direction=signal.direction,
        status=cast(SignalStatus, signal.status),
        entry_lower=signal.entry_lower,
        entry_upper=signal.entry_upper,
        planned_entry=signal.planned_entry,
        stop_loss=signal.stop_loss,
        take_profit_1=signal.take_profit_1,
        take_profit_2=signal.take_profit_2,
        quantity=signal.quantity,
        risk_amount=signal.risk_amount,
        taker_fee_rate=signal.taker_fee_rate,
        expires_at=signal.expires_at,
        current_stop=signal.current_stop,
    )
