import asyncio
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Any, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.execution import (
    TERMINAL_SIGNAL_STATUSES,
    LiveExecutionRepository,
    LiveSignalView,
)
from crypto_smc.providers.bybit import BybitOrderResult, BybitPrivateAPIError, BybitPrivateClient
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
        loss_cooldown_minutes: int = 360,
        pending_entry_timeout_seconds: int = 1200,
        min_risk_usdt: Decimal = Decimal("20"),
        max_effective_leverage: Decimal = Decimal("50"),
        max_slippage_bps: Decimal = Decimal("20"),
        min_signal_score: int = 85,
        max_notional_to_wallet_ratio: Decimal = Decimal("5"),
        symbol_allowlist: frozenset[str] = frozenset(),
        symbol_denylist: frozenset[str] = frozenset(),
        tp1_close_fraction: Decimal = Decimal("0.5"),
        move_stop_to_be_after_tp1: bool = True,
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
        self._min_signal_score = min_signal_score
        self._max_notional_to_wallet_ratio = max(Decimal(1), max_notional_to_wallet_ratio)
        self._symbol_allowlist = frozenset(item.upper() for item in symbol_allowlist)
        self._symbol_denylist = frozenset(item.upper() for item in symbol_denylist)
        self._tp1_close_fraction = min(max(tp1_close_fraction, Decimal("0.01")), Decimal("1"))
        self._move_stop_to_be_after_tp1 = move_stop_to_be_after_tp1
        self._max_open_positions = max_open_positions
        self._max_trades_per_day = max_trades_per_day
        self._max_daily_loss_usdt = max_daily_loss_usdt
        self._loss_cooldown_minutes = loss_cooldown_minutes
        self._pending_entry_timeout_seconds = pending_entry_timeout_seconds
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
            if _pending_entry_timed_out(
                signal,
                timeout_seconds=self._pending_entry_timeout_seconds,
                now=datetime.now(UTC),
            ):
                await self._cancel_pending_entry(
                    signal,
                    reason=(
                        "pending entry cancelled: exceeded "
                        f"{self._pending_entry_timeout_seconds}s timeout"
                    ),
                )
                return
            await self._sync_pending_entry(signal)
            return
        if signal.live_status in {"open", "tp1_reduced"}:
            await self._sync_open_position_quantity(signal)
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
        if signal.score < self._min_signal_score:
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=self._risk_usdt,
                qty=Decimal(0),
                leverage=_effective_max_leverage(signal, self._max_effective_leverage),
                error=(
                    f"signal score {signal.score} is below live minimum {self._min_signal_score}"
                ),
                now=now,
                notify=False,
            )
            return
        if not self._symbol_allowed(signal.symbol):
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=self._risk_usdt,
                qty=Decimal(0),
                leverage=_effective_max_leverage(signal, self._max_effective_leverage),
                error=f"symbol {signal.symbol} is disabled for live execution",
                now=now,
            )
            return
        balance = await self._client.get_wallet_balance(coin="USDT")
        strategy_qty = _quantity_from_strategy_plan(signal)
        if strategy_qty is not None:
            qty = strategy_qty
            risk_usdt = min(self._risk_usdt, signal.risk_amount)
        else:
            margin_risk_usdt = _risk_for_available_margin(
                signal,
                target_risk_usdt=self._risk_usdt,
                min_risk_usdt=self._min_risk_usdt,
                available_balance=balance.total_available_balance,
                max_effective_leverage=self._max_effective_leverage,
            )
            if margin_risk_usdt is None:
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
            risk_usdt = margin_risk_usdt
            risk_qty = _quantity_for_risk(signal, risk_usdt)
            if risk_qty is None:
                return
            qty = risk_qty
        notional = qty * signal.planned_entry
        wallet_balance = max(balance.total_wallet_balance, balance.total_available_balance)
        max_notional = wallet_balance * self._max_notional_to_wallet_ratio
        if notional > max_notional:
            await self._repository.reject_entry(
                self._session_factory,
                signal=signal,
                risk_usdt=risk_usdt,
                qty=qty,
                leverage=_effective_max_leverage(signal, self._max_effective_leverage),
                error=(
                    f"notional {notional} USDT exceeds {self._max_notional_to_wallet_ratio}x "
                    f"wallet cap {max_notional} USDT"
                ),
                now=now,
                notify=False,
            )
            return
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
        execution_price = await self._entry_execution_price(signal)
        if execution_price is not None:
            slippage_error = _entry_slippage_error(
                signal,
                execution_price=execution_price,
                max_slippage_bps=self._max_slippage_bps,
            )
            if slippage_error is not None:
                await self._repository.reject_entry(
                    self._session_factory,
                    signal=signal,
                    risk_usdt=risk_usdt,
                    qty=qty,
                    leverage=leverage,
                    error=slippage_error,
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
            loss_cooldown_minutes=self._loss_cooldown_minutes,
        )
        if live_id is None:
            return

        side = _entry_side(signal.direction)
        limit_price = _entry_limit_price(
            signal,
            execution_price=execution_price,
            max_slippage_bps=self._max_slippage_bps,
        )
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
            close_result = await self._emergency_close(
                signal.symbol,
                _close_side(signal.direction),
                actual_qty,
            )
            if close_result is not None:
                real_pnl = await self._closed_pnl(signal.symbol, order_id=close_result.order_id)
                await self._repository.mark_closed(
                    self._session_factory,
                    live_id=resolved_live_id,
                    order_id=close_result.order_id,
                    real_pnl=real_pnl[0],
                    real_entry_price=real_pnl[1],
                    real_exit_price=real_pnl[2],
                    error=f"protective stop rejected; emergency close submitted: {exc}",
                    now=datetime.now(UTC),
                )
                return
            if await self._live_position_size(signal.symbol) <= 0:
                real_pnl = await self._closed_pnl(
                    signal.symbol,
                    order_id="",
                    since=signal.live_created_at,
                )
                await self._repository.mark_closed(
                    self._session_factory,
                    live_id=resolved_live_id,
                    order_id="",
                    real_pnl=real_pnl[0],
                    real_entry_price=real_pnl[1],
                    real_exit_price=real_pnl[2],
                    error=(
                        "protective stop rejected; position is flat after "
                        f"emergency close attempt: {exc}"
                    ),
                    now=datetime.now(UTC),
                )
                return
            await self._repository.mark_failed(
                self._session_factory,
                live_id=resolved_live_id,
                error=f"protective stop rejected and emergency close failed: {exc}",
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

    async def _sync_open_position_quantity(self, signal: LiveSignalView) -> None:
        if signal.live_id is None:
            return
        if signal.live_status != "open":
            return
        if signal.live_remaining_qty == signal.planned_quantity:
            return
        actual_qty = await self._live_position_size(signal.symbol)
        if actual_qty <= 0 or actual_qty == signal.live_remaining_qty:
            return
        await self._repository.mark_position_quantity(
            self._session_factory,
            live_id=signal.live_id,
            qty=actual_qty,
            now=datetime.now(UTC),
        )

    async def _cancel_pending_entry(
        self,
        signal: LiveSignalView,
        *,
        reason: str | None = None,
    ) -> None:
        if signal.live_id is None:
            return
        resolved_reason = reason or (
            f"pending entry cancelled: signal status became {signal.signal_status}"
        )
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
                        f"{resolved_reason.removeprefix('pending entry cancelled: ')}"
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
            error=resolved_reason,
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
        if signal.live_id is None:
            return
        if not await self._repository.claim_tp1(self._session_factory, live_id=signal.live_id):
            return
        close_side = _close_side(signal.direction)
        actual_qty = await self._live_position_size(signal.symbol)
        if actual_qty <= 0:
            real_pnl = await self._closed_pnl(
                signal.symbol,
                order_id="",
                since=signal.live_created_at,
            )
            await self._repository.mark_closed(
                self._session_factory,
                live_id=signal.live_id,
                order_id="",
                real_pnl=real_pnl[0],
                real_entry_price=real_pnl[1],
                real_exit_price=real_pnl[2],
                error="tp1 requested but Bybit position is already flat",
                now=datetime.now(UTC),
            )
            return
        close_qty = _round_down(actual_qty * self._tp1_close_fraction, signal.quantity_step)
        if close_qty < signal.min_order_quantity:
            close_qty = actual_qty
        next_stop = signal.current_stop if self._move_stop_to_be_after_tp1 else signal.stop_loss
        try:
            result = await self._client.place_market_order(
                symbol=signal.symbol,
                side=close_side,
                qty=close_qty,
                order_link_id=f"sp-{signal.signal_id}-tp1",
                reduce_only=True,
            )
            remaining_qty = max(Decimal(0), actual_qty - close_qty)
            await self._client.set_full_position_stop(
                symbol=signal.symbol,
                stop_loss=next_stop,
            )
        except Exception as exc:
            post_error_qty = await self._live_position_size(signal.symbol)
            if post_error_qty > 0:
                close_result = await self._emergency_close(
                    signal.symbol,
                    close_side,
                    post_error_qty,
                )
                if close_result is not None:
                    real_pnl = await self._closed_pnl(
                        signal.symbol,
                        order_id="",
                        since=signal.live_created_at,
                    )
                    await self._repository.mark_closed(
                        self._session_factory,
                        live_id=signal.live_id,
                        order_id=close_result.order_id,
                        real_pnl=real_pnl[0],
                        real_entry_price=real_pnl[1],
                        real_exit_price=real_pnl[2],
                        error=(
                            f"tp1 protective stop update failed; emergency close submitted: {exc}"
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
        await self._repository.mark_tp1_reduced(
            self._session_factory,
            live_id=signal.live_id,
            order_id=result.order_id,
            remaining_qty=remaining_qty,
            current_stop=next_stop,
            now=datetime.now(UTC),
        )

    def _symbol_allowed(self, symbol: str) -> bool:
        normalized = symbol.upper()
        if normalized in self._symbol_denylist:
            return False
        return not self._symbol_allowlist or normalized in self._symbol_allowlist

    async def _close(self, signal: LiveSignalView) -> None:
        if signal.live_id is None:
            return
        qty = await self._repository.claim_close(self._session_factory, live_id=signal.live_id)
        if qty is None or qty <= 0:
            return
        close_side = _close_side(signal.direction)
        actual_qty = await self._live_position_size(signal.symbol)
        if actual_qty <= 0:
            real_pnl = await self._closed_pnl(
                signal.symbol,
                order_id="",
                since=signal.live_created_at,
            )
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
        try:
            result = await self._client.place_market_order(
                symbol=signal.symbol,
                side=close_side,
                qty=actual_qty,
                order_link_id=f"sp-{signal.signal_id}-close",
                reduce_only=True,
            )
        except Exception as exc:
            if _is_already_flat_error(exc) or await self._live_position_size(signal.symbol) <= 0:
                real_pnl = await self._closed_pnl(
                    signal.symbol,
                    order_id="",
                    since=signal.live_created_at,
                )
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
        real_pnl = await self._closed_pnl(
            signal.symbol,
            order_id="",
            since=signal.live_created_at,
        )
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
        since: datetime | None = None,
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        closed_items: tuple[Any, ...] = ()
        for attempt in range(5):
            try:
                closed_items = await self._client.get_closed_pnl(symbol=symbol, limit=10)
            except Exception:
                await logger.aexception("live_execution_closed_pnl_fetch_failed", symbol=symbol)
                return None, None, None
            closed_items = _filter_closed_pnl(
                closed_items,
                order_id=order_id,
                since=since,
            )
            if closed_items:
                break
            if attempt < 4:
                await asyncio.sleep(0.5)
        if not closed_items:
            return None, None, None
        closed_items = tuple(sorted(closed_items, key=lambda item: int(item.updated_time_ms)))
        real_pnl = sum((item.closed_pnl for item in closed_items), Decimal(0))
        first = closed_items[0]
        last = closed_items[-1]
        return real_pnl, first.average_entry_price, last.average_exit_price

    async def _emergency_close(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: Decimal,
    ) -> BybitOrderResult | None:
        try:
            return await self._client.place_market_order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_link_id=f"sp-emergency-{int(datetime.now(UTC).timestamp())}",
                reduce_only=True,
            )
        except Exception:
            await logger.aexception("live_execution_emergency_close_failed", symbol=symbol)
            return None

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


def _filter_closed_pnl(
    closed_items: tuple[Any, ...],
    *,
    order_id: str,
    since: datetime | None,
) -> tuple[Any, ...]:
    if order_id:
        return tuple(item for item in closed_items if item.order_id == order_id)
    if since is not None:
        since_ms = int(since.timestamp() * 1000) - 60_000
        closed_items = tuple(item for item in closed_items if int(item.updated_time_ms) >= since_ms)
    return closed_items


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


def _quantity_from_strategy_plan(signal: LiveSignalView) -> Decimal | None:
    if signal.planned_quantity <= 0 or signal.quantity_step <= 0:
        return None
    qty = _round_down(signal.planned_quantity, signal.quantity_step)
    if qty < signal.min_order_quantity:
        return None
    if qty * signal.planned_entry < signal.min_notional_value:
        return None
    if qty > signal.max_market_order_quantity:
        qty = _round_down(signal.max_market_order_quantity, signal.quantity_step)
    return qty if qty > 0 else None


def _entry_limit_price(
    signal: LiveSignalView,
    *,
    execution_price: Decimal | None = None,
    max_slippage_bps: Decimal = Decimal(0),
) -> Decimal:
    if signal.price_tick_size <= 0:
        return signal.planned_entry
    if execution_price is not None and execution_price > 0 and signal.planned_entry > 0:
        max_slippage = signal.planned_entry * max_slippage_bps / Decimal(10_000)
        if signal.direction == "long":
            allowed = min(signal.entry_upper, signal.planned_entry + max_slippage)
            rounded = _round_up(min(execution_price, allowed), signal.price_tick_size)
            if rounded > allowed:
                return _round_down(allowed, signal.price_tick_size)
            return rounded

        allowed = max(signal.entry_lower, signal.planned_entry - max_slippage)
        rounded = _round_down(max(execution_price, allowed), signal.price_tick_size)
        if rounded < allowed:
            return _round_up(allowed, signal.price_tick_size)
        return rounded

    if signal.direction == "long":
        return _round_down(signal.planned_entry, signal.price_tick_size)
    return _round_up(signal.planned_entry, signal.price_tick_size)


def _pending_entry_timed_out(
    signal: LiveSignalView,
    *,
    timeout_seconds: int,
    now: datetime,
) -> bool:
    if timeout_seconds <= 0 or signal.live_entry_submitted_at is None:
        return False
    submitted_at = signal.live_entry_submitted_at
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=UTC)
    return (now - submitted_at).total_seconds() >= timeout_seconds


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
