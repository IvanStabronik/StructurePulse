import asyncio
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.execution import (
    TERMINAL_SIGNAL_STATUSES,
    LiveExecutionRepository,
    LiveSignalView,
)
from crypto_smc.providers.bybit import BybitPrivateAPIError, BybitPrivateClient
from crypto_smc.providers.protocols import MarketTickerProvider

logger = structlog.get_logger(__name__)
MARGIN_USAGE_LIMIT = Decimal("0.80")


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
        min_risk_usdt: Decimal = Decimal("20"),
        max_effective_leverage: Decimal = Decimal("50"),
        max_slippage_bps: Decimal = Decimal("20"),
        ticker_provider: MarketTickerProvider | None = None,
        repository: LiveExecutionRepository | None = None,
    ) -> None:
        self._client = client
        self._ticker_provider = ticker_provider
        self._session_factory = session_factory
        self._risk_usdt = risk_usdt
        self._min_risk_usdt = min(min_risk_usdt, risk_usdt)
        self._leverage = leverage
        self._max_effective_leverage = max(Decimal(1), max_effective_leverage)
        self._max_slippage_bps = max(Decimal(0), max_slippage_bps)
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

    async def handle_signal_id(self, signal_id: int) -> None:
        signal = await self._repository.get_actionable(
            self._session_factory,
            signal_id=signal_id,
        )
        if signal is None:
            return
        await self._handle(signal)

    async def _handle(self, signal: LiveSignalView) -> None:
        if signal.live_id is None:
            if signal.signal_status in {"active", "entered"}:
                await self._enter(signal)
            return
        if signal.live_status == "entry_pending":
            if signal.signal_status in TERMINAL_SIGNAL_STATUSES:
                await self._cancel_pending_entry(signal)
                return
            await self._sync_pending_entry(signal)
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
        balance = await self._client.get_wallet_balance(coin="USDT")
        risk_usdt = _risk_for_available_margin(
            signal,
            target_risk_usdt=self._risk_usdt,
            min_risk_usdt=self._min_risk_usdt,
            available_balance=balance.total_available_balance,
            max_effective_leverage=self._max_effective_leverage,
        )
        if risk_usdt is None:
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=self._risk_usdt,
                qty=Decimal(0),
                leverage=_effective_max_leverage(signal, self._max_effective_leverage),
                error=(
                    f"available balance {balance.total_available_balance} cannot support "
                    f"minimum live risk {self._min_risk_usdt} USDT for this setup "
                    f"within {_effective_max_leverage(signal, self._max_effective_leverage)}x "
                    "max effective leverage"
                ),
                now=now,
            )
            return
        qty = _quantity_for_risk(signal, risk_usdt)
        if qty is None:
            return
        notional = qty * signal.planned_entry
        leverage = _select_leverage(
            notional=notional,
            configured_leverage=self._leverage,
            instrument_max_leverage=signal.max_leverage,
            available_balance=balance.total_available_balance,
            max_effective_leverage=self._max_effective_leverage,
        )
        if leverage is None:
            max_usable_margin = balance.total_available_balance * MARGIN_USAGE_LIMIT
            max_leverage = _effective_max_leverage(signal, self._max_effective_leverage)
            estimated_margin = notional / max_leverage
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=risk_usdt,
                qty=qty,
                leverage=max_leverage,
                error=(
                    f"available balance {balance.total_available_balance} cannot cover "
                    f"{estimated_margin} USDT estimated margin for {risk_usdt} USDT risk "
                    f"even at {max_leverage}x max effective leverage; usable margin limit is "
                    f"{max_usable_margin} USDT"
                ),
                now=now,
            )
            return
        estimated_margin = notional / leverage
        if balance.total_available_balance < estimated_margin:
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=risk_usdt,
                qty=qty,
                leverage=leverage,
                error=(
                    f"available balance {balance.total_available_balance} is below "
                    f"{estimated_margin} USDT estimated margin for "
                    f"{risk_usdt} USDT risk at {leverage}x"
                ),
                now=now,
            )
            return
        live_id = await self._repository.claim_entry(
            self._session_factory,
            signal=signal,
            risk_usdt=risk_usdt,
            qty=qty,
            leverage=leverage,
            now=now,
            max_open_positions=self._max_open_positions,
            max_trades_per_day=self._max_trades_per_day,
            max_daily_loss_usdt=self._max_daily_loss_usdt,
        )
        if live_id is None:
            return

        side = _entry_side(signal.direction)
        limit_price = _entry_limit_price(signal)
        submitted_order_id: str | None = None
        submitted_order_link_id = f"sp-{signal.signal_id}-entry"
        try:
            await self._client.set_linear_leverage(
                symbol=signal.symbol,
                leverage=leverage,
            )
            result = await self._client.place_limit_order(
                symbol=signal.symbol,
                side=side,
                qty=qty,
                price=limit_price,
                order_link_id=submitted_order_link_id,
            )
            submitted_order_id = result.order_id
            await self._repository.mark_entry_pending(
                self._session_factory,
                live_id=live_id,
                order_id=result.order_id,
                leverage=leverage,
                limit_price=limit_price,
                now=datetime.now(UTC),
            )
        except Exception as exc:
            if submitted_order_id is not None:
                await self._cancel_untracked_entry_order(
                    symbol=signal.symbol,
                    order_id=submitted_order_id,
                    order_link_id=submitted_order_link_id,
                )
            await self._repository.mark_failed(
                self._session_factory,
                live_id=live_id,
                error=str(exc),
                now=datetime.now(UTC),
            )
            return
        await self._sync_pending_entry(
            signal,
            live_id=live_id,
            order_id=result.order_id,
        )

    async def _sync_pending_entry(
        self,
        signal: LiveSignalView,
        *,
        live_id: int | None = None,
        order_id: str | None = None,
    ) -> None:
        resolved_live_id = live_id or signal.live_id
        if resolved_live_id is None:
            return
        actual_qty = await self._live_position_size(signal.symbol)
        if actual_qty <= 0:
            return
        try:
            await self._client.set_full_position_stop(
                symbol=signal.symbol,
                stop_loss=signal.stop_loss,
            )
        except Exception as exc:
            await self._emergency_close(signal.symbol, _close_side(signal.direction), actual_qty)
            await self._repository.mark_failed(
                self._session_factory,
                live_id=resolved_live_id,
                error=str(exc),
                now=datetime.now(UTC),
            )
            return
        await self._repository.mark_entry_open(
            self._session_factory,
            live_id=resolved_live_id,
            order_id=order_id or signal.live_entry_order_id or "",
            qty=actual_qty,
            stop_loss=signal.stop_loss,
            now=datetime.now(UTC),
        )

    async def _cancel_pending_entry(self, signal: LiveSignalView) -> None:
        if signal.live_id is None:
            return
        actual_qty = await self._live_position_size(signal.symbol)
        if actual_qty > 0:
            await self._sync_pending_entry(signal)
            return
        try:
            await self._client.cancel_order(
                symbol=signal.symbol,
                order_id=signal.live_entry_order_id,
                order_link_id=signal.live_entry_order_link_id,
            )
        except Exception as exc:
            if await self._live_position_size(signal.symbol) > 0:
                await self._sync_pending_entry(signal)
                return
            if _is_order_missing_error(exc):
                await self._repository.mark_entry_cancelled(
                    self._session_factory,
                    live_id=signal.live_id,
                    error=(
                        "pending entry cancel confirmed absent: "
                        f"signal status became {signal.signal_status}"
                    ),
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
        await self._repository.mark_entry_cancelled(
            self._session_factory,
            live_id=signal.live_id,
            error=f"pending entry cancelled: signal status became {signal.signal_status}",
            now=datetime.now(UTC),
        )

    async def _cancel_untracked_entry_order(
        self,
        *,
        symbol: str,
        order_id: str,
        order_link_id: str,
    ) -> None:
        try:
            await self._client.cancel_order(
                symbol=symbol,
                order_id=order_id,
                order_link_id=order_link_id,
            )
        except Exception:
            await logger.aexception(
                "live_execution_untracked_entry_cancel_failed",
                symbol=symbol,
                order_id=order_id,
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
            real_pnl = await self._closed_pnl(signal.symbol, order_id="")
            await self._repository.mark_closed(
                self._session_factory,
                live_id=signal.live_id,
                order_id="",
                real_pnl=real_pnl[0],
                real_entry_price=real_pnl[1],
                real_exit_price=real_pnl[2],
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
                real_pnl = await self._closed_pnl(signal.symbol, order_id="")
                await self._repository.mark_closed(
                    self._session_factory,
                    live_id=signal.live_id,
                    order_id="",
                    real_pnl=real_pnl[0],
                    real_entry_price=real_pnl[1],
                    real_exit_price=real_pnl[2],
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
        real_pnl = await self._closed_pnl(signal.symbol, order_id=result.order_id)
        await self._repository.mark_closed(
            self._session_factory,
            live_id=signal.live_id,
            order_id=result.order_id,
            real_pnl=real_pnl[0],
            real_entry_price=real_pnl[1],
            real_exit_price=real_pnl[2],
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

    async def _closed_pnl(
        self,
        symbol: str,
        *,
        order_id: str,
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        try:
            closed_items = await self._client.get_closed_pnl(symbol=symbol, limit=10)
        except Exception:
            await logger.aexception("live_execution_closed_pnl_fetch_failed", symbol=symbol)
            return None, None, None
        if not closed_items:
            return None, None, None
        if order_id:
            closed_items = tuple(item for item in closed_items if item.order_id == order_id)
            if not closed_items:
                return None, None, None
        real_pnl = sum((item.closed_pnl for item in closed_items), Decimal(0))
        first = closed_items[0]
        return real_pnl, first.average_entry_price, first.average_exit_price

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

    async def _entry_execution_price(self, signal: LiveSignalView) -> Decimal | None:
        if self._ticker_provider is None:
            return None
        tickers = await self._ticker_provider.list_linear_tickers()
        ticker = tickers.get(signal.symbol)
        if ticker is None:
            return None
        if signal.direction == "long":
            return ticker.ask_price if ticker.ask_price > 0 else ticker.last_price
        return ticker.bid_price if ticker.bid_price > 0 else ticker.last_price


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


def _entry_limit_price(signal: LiveSignalView) -> Decimal:
    if signal.price_tick_size <= 0:
        return signal.planned_entry
    if signal.direction == "long":
        return _round_down(signal.planned_entry, signal.price_tick_size)
    return _round_up(signal.planned_entry, signal.price_tick_size)


def _risk_for_available_margin(
    signal: LiveSignalView,
    *,
    target_risk_usdt: Decimal,
    min_risk_usdt: Decimal,
    available_balance: Decimal,
    max_effective_leverage: Decimal,
) -> Decimal | None:
    if target_risk_usdt <= 0 or min_risk_usdt <= 0 or available_balance <= 0:
        return None
    risk_per_unit = abs(signal.planned_entry - signal.stop_loss)
    effective_max_leverage = _effective_max_leverage(signal, max_effective_leverage)
    if risk_per_unit <= 0 or signal.planned_entry <= 0 or effective_max_leverage < 1:
        return None
    max_usable_notional = available_balance * MARGIN_USAGE_LIMIT * effective_max_leverage
    max_risk = max_usable_notional * risk_per_unit / signal.planned_entry
    selected = min(target_risk_usdt, max_risk)
    selected = selected.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if selected < min_risk_usdt:
        return None
    return selected


def _select_leverage(
    *,
    notional: Decimal,
    configured_leverage: Decimal,
    instrument_max_leverage: Decimal,
    available_balance: Decimal,
    max_effective_leverage: Decimal,
) -> Decimal | None:
    effective_max_leverage = min(instrument_max_leverage, max_effective_leverage)
    if notional <= 0 or available_balance <= 0 or effective_max_leverage < 1:
        return None
    max_usable_margin = available_balance * MARGIN_USAGE_LIMIT
    if max_usable_margin <= 0:
        return None
    required = (notional / max_usable_margin).to_integral_value(rounding=ROUND_CEILING)
    selected = max(Decimal(1), configured_leverage, required)
    if selected > effective_max_leverage:
        return None
    return selected


def _effective_max_leverage(
    signal: LiveSignalView,
    max_effective_leverage: Decimal,
) -> Decimal:
    return max(Decimal(1), min(signal.max_leverage, max_effective_leverage))


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


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


def _is_order_missing_error(exc: Exception) -> bool:
    return (
        isinstance(exc, BybitPrivateAPIError)
        and "110001" in str(exc)
        and "order not exists" in str(exc).lower()
    )


def _entry_slippage_error(
    signal: LiveSignalView,
    *,
    execution_price: Decimal,
    max_slippage_bps: Decimal,
) -> str | None:
    if execution_price <= 0 or signal.planned_entry <= 0:
        return "invalid live ticker price before entry"

    max_slippage = signal.planned_entry * max_slippage_bps / Decimal(10_000)
    if signal.direction == "long":
        limit_price = min(signal.entry_upper, signal.planned_entry + max_slippage)
        if execution_price > limit_price:
            return (
                f"live entry skipped: ask {execution_price} is above allowed "
                f"{limit_price} for planned entry {signal.planned_entry}"
            )
        return None

    limit_price = max(signal.entry_lower, signal.planned_entry - max_slippage)
    if execution_price < limit_price:
        return (
            f"live entry skipped: bid {execution_price} is below allowed "
            f"{limit_price} for planned entry {signal.planned_entry}"
        )
    return None
