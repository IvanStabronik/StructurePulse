import asyncio
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.execution import (
    TERMINAL_SIGNAL_STATUSES,
    LiveExecutionRepository,
    LiveSignalView,
)
from crypto_smc.providers.bybit import BybitPrivateAPIError, BybitPrivateClient

logger = structlog.get_logger(__name__)


class LiveExecutionService:
    def __init__(
        self,
        *,
        client: BybitPrivateClient,
        session_factory: async_sessionmaker[AsyncSession],
        risk_usdt: Decimal,
        leverage: Decimal,
        max_open_positions: int,
        max_trades_per_day: int,
        max_daily_loss_usdt: Decimal,
        poll_interval_seconds: float,
        repository: LiveExecutionRepository | None = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._risk_usdt = risk_usdt
        self._leverage = leverage
        self._max_open_positions = max_open_positions
        self._max_trades_per_day = max_trades_per_day
        self._max_daily_loss_usdt = max_daily_loss_usdt
        self._poll_interval_seconds = poll_interval_seconds
        self._repository = repository or LiveExecutionRepository()

    async def run(self) -> None:
        while True:
            await self._tick()
            await asyncio.sleep(self._poll_interval_seconds)

    async def _tick(self) -> None:
        for signal in await self._repository.list_actionable(self._session_factory):
            try:
                await self._handle(signal)
            except Exception:
                await logger.aexception(
                    "live_execution_signal_failed",
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                )

    async def _handle(self, signal: LiveSignalView) -> None:
        if signal.live_id is None:
            if signal.signal_status == "entered":
                await self._enter(signal)
            return
        if signal.live_status == "open" and signal.signal_status == "tp1_reached":
            await self._take_profit_1(signal)
            return
        if (
            signal.live_status in {"open", "tp1_reduced"}
            and signal.signal_status in TERMINAL_SIGNAL_STATUSES
        ):
            await self._close(signal)

    async def _enter(self, signal: LiveSignalView) -> None:
        now = datetime.now(UTC)
        qty = _quantity_for_risk(signal, self._risk_usdt)
        if qty is None:
            return
        notional = qty * signal.planned_entry
        estimated_margin = notional / self._leverage
        balance = await self._client.get_wallet_balance(coin="USDT")
        if balance.total_available_balance < estimated_margin:
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=self._risk_usdt,
                qty=qty,
                leverage=self._leverage,
                error=(
                    f"available balance {balance.total_available_balance} is below "
                    f"{estimated_margin} USDT estimated margin for "
                    f"{self._risk_usdt} USDT risk at {self._leverage}x"
                ),
                now=now,
            )
            return
        live_id = await self._repository.claim_entry(
            self._session_factory,
            signal=signal,
            risk_usdt=self._risk_usdt,
            qty=qty,
            leverage=self._leverage,
            now=now,
            max_open_positions=self._max_open_positions,
            max_trades_per_day=self._max_trades_per_day,
            max_daily_loss_usdt=self._max_daily_loss_usdt,
        )
        if live_id is None:
            return

        side = _entry_side(signal.direction)
        close_side = _close_side(signal.direction)
        entry_order_submitted = False
        try:
            await self._client.set_linear_leverage(
                symbol=signal.symbol,
                leverage=self._leverage,
            )
            result = await self._client.place_market_order(
                symbol=signal.symbol,
                side=side,
                qty=qty,
                order_link_id=f"sp-{signal.signal_id}-entry",
            )
            entry_order_submitted = True
            actual_qty = await self._position_size(signal.symbol, fallback=qty)
            await self._client.set_full_position_stop(
                symbol=signal.symbol,
                stop_loss=signal.stop_loss,
            )
        except Exception as exc:
            if entry_order_submitted:
                await self._emergency_close(signal.symbol, close_side, qty)
            await self._repository.mark_failed(
                self._session_factory,
                live_id=live_id,
                error=str(exc),
                now=datetime.now(UTC),
            )
            return
        await self._repository.mark_entry_open(
            self._session_factory,
            live_id=live_id,
            order_id=result.order_id,
            qty=actual_qty,
            stop_loss=signal.stop_loss,
            now=datetime.now(UTC),
        )

    async def _take_profit_1(self, signal: LiveSignalView) -> None:
        if signal.live_id is None or signal.live_remaining_qty is None:
            return
        if not await self._repository.claim_tp1(self._session_factory, live_id=signal.live_id):
            return
        close_side = _close_side(signal.direction)
        close_qty = _round_down(signal.live_remaining_qty / Decimal(2), signal.quantity_step)
        if close_qty < signal.min_order_quantity:
            close_qty = signal.live_remaining_qty
        try:
            result = await self._client.place_market_order(
                symbol=signal.symbol,
                side=close_side,
                qty=close_qty,
                order_link_id=f"sp-{signal.signal_id}-tp1",
                reduce_only=True,
            )
            remaining_qty = max(Decimal(0), signal.live_remaining_qty - close_qty)
            await self._client.set_full_position_stop(
                symbol=signal.symbol,
                stop_loss=signal.current_stop,
            )
        except Exception as exc:
            await self._repository.mark_failed(
                self._session_factory,
                live_id=signal.live_id,
                error=str(exc),
                now=datetime.now(UTC),
            )
            return
        await self._repository.mark_tp1_reduced(
            self._session_factory,
            live_id=signal.live_id,
            order_id=result.order_id,
            remaining_qty=remaining_qty,
            current_stop=signal.current_stop,
            now=datetime.now(UTC),
        )

    async def _close(self, signal: LiveSignalView) -> None:
        if signal.live_id is None:
            return
        qty = await self._repository.claim_close(self._session_factory, live_id=signal.live_id)
        if qty is None or qty <= 0:
            return
        close_side = _close_side(signal.direction)
        actual_qty = await self._live_position_size(signal.symbol)
        if actual_qty <= 0:
            await self._repository.mark_closed(
                self._session_factory,
                live_id=signal.live_id,
                order_id="",
                now=datetime.now(UTC),
            )
            return
        qty = min(qty, actual_qty)
        try:
            result = await self._client.place_market_order(
                symbol=signal.symbol,
                side=close_side,
                qty=qty,
                order_link_id=f"sp-{signal.signal_id}-close",
                reduce_only=True,
            )
        except Exception as exc:
            if _is_already_flat_error(exc) or await self._live_position_size(signal.symbol) <= 0:
                await self._repository.mark_closed(
                    self._session_factory,
                    live_id=signal.live_id,
                    order_id="",
                    now=datetime.now(UTC),
                )
                return
            await self._repository.mark_failed(
                self._session_factory,
                live_id=signal.live_id,
                error=str(exc),
                now=datetime.now(UTC),
            )
            return
        await self._repository.mark_closed(
            self._session_factory,
            live_id=signal.live_id,
            order_id=result.order_id,
            now=datetime.now(UTC),
        )

    async def _position_size(self, symbol: str, *, fallback: Decimal) -> Decimal:
        for _ in range(5):
            size = await self._live_position_size(symbol)
            if size > 0:
                return size
            await asyncio.sleep(0.5)
        return fallback

    async def _live_position_size(self, symbol: str) -> Decimal:
        position = await self._client.get_linear_position(symbol=symbol)
        if position is None or position.size <= 0:
            return Decimal(0)
        return position.size

    async def _emergency_close(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: Decimal,
    ) -> None:
        try:
            await self._client.place_market_order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_link_id=f"sp-emergency-{int(datetime.now(UTC).timestamp())}",
                reduce_only=True,
            )
        except Exception:
            await logger.aexception("live_execution_emergency_close_failed", symbol=symbol)


def _quantity_for_risk(signal: LiveSignalView, risk_usdt: Decimal) -> Decimal | None:
    if signal.planned_entry <= 0 or signal.quantity_step <= 0:
        return None
    risk_per_unit = abs(signal.planned_entry - signal.stop_loss)
    if risk_per_unit <= 0:
        return None
    qty = _round_down(risk_usdt / risk_per_unit, signal.quantity_step)
    if qty < signal.min_order_quantity:
        return None
    if qty * signal.planned_entry < signal.min_notional_value:
        return None
    if qty > signal.max_market_order_quantity:
        qty = _round_down(signal.max_market_order_quantity, signal.quantity_step)
    return qty if qty > 0 else None


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _entry_side(direction: str) -> Literal["Buy", "Sell"]:
    return "Buy" if direction == "long" else "Sell"


def _close_side(direction: str) -> Literal["Buy", "Sell"]:
    return "Sell" if direction == "long" else "Buy"


def _is_already_flat_error(exc: Exception) -> bool:
    return (
        isinstance(exc, BybitPrivateAPIError)
        and "110017" in str(exc)
        and "position is zero" in str(exc).lower()
    )
