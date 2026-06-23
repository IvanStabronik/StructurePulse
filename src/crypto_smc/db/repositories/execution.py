from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    InstrumentRecord,
    LiveExecutionRecord,
    NotificationOutboxRecord,
    SignalCandidateRecord,
    SignalRecord,
    VirtualTradeRecord,
)

OPEN_LIVE_EXECUTION_STATUSES = frozenset(
    {
        "entry_submitting",
        "entry_pending",
        "open",
        "tp1_submitting",
        "tp1_reduced",
        "closing",
    }
)
TERMINAL_SIGNAL_STATUSES = frozenset(
    {
        "stopped",
        "stopped_at_breakeven",
        "tp2_completed",
        "ambiguous",
        "coverage_failed",
        "expired",
        "invalidated",
    }
)


@dataclass(frozen=True, slots=True)
class LiveSignalView:
    signal_id: int
    symbol: str
    direction: str
    score: int
    signal_status: str
    entry_lower: Decimal
    entry_upper: Decimal
    planned_entry: Decimal
    stop_loss: Decimal
    current_stop: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    virtual_remaining_quantity: Decimal
    price_tick_size: Decimal
    quantity_step: Decimal
    min_order_quantity: Decimal
    max_market_order_quantity: Decimal
    min_notional_value: Decimal
    max_leverage: Decimal
    live_id: int | None
    live_status: str | None
    live_remaining_qty: Decimal | None
    live_entry_order_id: str | None
    live_entry_order_link_id: str | None
    live_entry_submitted_at: datetime | None


class LiveExecutionRepository:
    async def list_actionable(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> tuple[LiveSignalView, ...]:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    self._actionable_statement().order_by(
                        SignalRecord.created_at,
                        SignalRecord.id,
                    )
                )
            ).all()
        return tuple(self._view(row) for row in rows)

    async def get_actionable(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal_id: int,
    ) -> LiveSignalView | None:
        async with session_factory() as session:
            row = (
                await session.execute(
                    self._actionable_statement().where(SignalRecord.id == signal_id)
                )
            ).one_or_none()
        return self._view(row) if row is not None else None

    @staticmethod
    def _actionable_statement() -> Any:
        return (
            select(
                SignalRecord,
                VirtualTradeRecord,
                InstrumentRecord,
                SignalCandidateRecord.score,
                LiveExecutionRecord,
            )
            .join(VirtualTradeRecord, VirtualTradeRecord.signal_id == SignalRecord.id)
            .join(InstrumentRecord, InstrumentRecord.symbol == SignalRecord.symbol)
            .join(SignalCandidateRecord, SignalCandidateRecord.id == SignalRecord.candidate_id)
            .outerjoin(
                LiveExecutionRecord,
                LiveExecutionRecord.signal_id == SignalRecord.id,
            )
            .where(
                SignalRecord.status.in_(
                    {
                        "entered",
                        "active",
                        "tp1_reached",
                        *TERMINAL_SIGNAL_STATUSES,
                    }
                )
            )
        )

    @staticmethod
    def _view(row: Any) -> LiveSignalView:
        signal, trade, instrument, score, live = row
        return LiveSignalView(
            signal_id=signal.id,
            symbol=signal.symbol,
            direction=signal.direction,
            score=score,
            signal_status=signal.status,
            entry_lower=signal.entry_lower,
            entry_upper=signal.entry_upper,
            planned_entry=signal.planned_entry,
            stop_loss=signal.stop_loss,
            current_stop=trade.current_stop,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            virtual_remaining_quantity=trade.remaining_quantity,
            price_tick_size=instrument.tick_size,
            quantity_step=instrument.quantity_step,
            min_order_quantity=instrument.min_order_quantity,
            max_market_order_quantity=instrument.max_market_order_quantity,
            min_notional_value=instrument.min_notional_value,
            max_leverage=instrument.max_leverage,
            live_id=live.id if live is not None else None,
            live_status=live.status if live is not None else None,
            live_remaining_qty=live.remaining_qty if live is not None else None,
            live_entry_order_id=live.entry_order_id if live is not None else None,
            live_entry_order_link_id=(live.entry_order_link_id if live is not None else None),
            live_entry_submitted_at=(live.entry_submitted_at if live is not None else None),
        )

    async def open_live_count(self, session: AsyncSession) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(LiveExecutionRecord)
                .where(LiveExecutionRecord.status.in_(OPEN_LIVE_EXECUTION_STATUSES))
            )
            or 0
        )

    async def todays_trade_count(self, session: AsyncSession, *, now: datetime) -> int:
        day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(
            await session.scalar(
                select(func.count())
                .select_from(LiveExecutionRecord)
                .where(LiveExecutionRecord.created_at >= day_start)
                .where(
                    or_(
                        LiveExecutionRecord.status.notin_(("failed", "skipped")),
                        LiveExecutionRecord.entry_order_id.is_not(None),
                        LiveExecutionRecord.entry_submitted_at.is_not(None),
                    )
                )
            )
            or 0
        )

    async def todays_virtual_pnl(self, session: AsyncSession, *, now: datetime) -> Decimal:
        day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = await session.scalar(
            select(func.coalesce(func.sum(VirtualTradeRecord.realized_pnl), 0))
            .select_from(LiveExecutionRecord)
            .join(VirtualTradeRecord, VirtualTradeRecord.signal_id == LiveExecutionRecord.signal_id)
            .where(LiveExecutionRecord.created_at >= day_start)
            .where(LiveExecutionRecord.status.in_({"closed", "failed"}))
            .where(
                or_(
                    LiveExecutionRecord.status.notin_(("failed", "skipped")),
                    LiveExecutionRecord.entry_order_id.is_not(None),
                    LiveExecutionRecord.entry_submitted_at.is_not(None),
                )
            )
        )
        return Decimal(str(total or 0))

    async def reject_entry(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal: LiveSignalView,
        risk_usdt: Decimal,
        qty: Decimal,
        leverage: Decimal,
        error: str,
        now: datetime,
        notify: bool = True,
    ) -> None:
        async with session_factory() as session, session.begin():
            existing = await session.scalar(
                select(LiveExecutionRecord).where(LiveExecutionRecord.signal_id == signal.signal_id)
            )
            if existing is not None:
                return
            await self._reject_entry_record(
                session,
                signal=signal,
                risk_usdt=risk_usdt,
                qty=qty,
                leverage=leverage,
                error=error,
                now=now,
                notify=notify,
            )

    async def claim_entry(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal: LiveSignalView,
        risk_usdt: Decimal,
        qty: Decimal,
        leverage: Decimal,
        now: datetime,
        max_open_positions: int,
        max_trades_per_day: int,
        max_daily_loss_usdt: Decimal,
    ) -> int | None:
        async with session_factory() as session, session.begin():
            if signal.signal_status not in {"active", "entered"}:
                return None
            existing = await session.scalar(
                select(LiveExecutionRecord).where(LiveExecutionRecord.signal_id == signal.signal_id)
            )
            if existing is not None:
                return None
            if await self.open_live_count(session) >= max_open_positions:
                await self._reject_entry_record(
                    session,
                    signal=signal,
                    risk_usdt=risk_usdt,
                    qty=qty,
                    leverage=leverage,
                    error=f"max open live positions reached: {max_open_positions}",
                    now=now,
                )
                return None
            if await self.todays_trade_count(session, now=now) >= max_trades_per_day:
                await self._reject_entry_record(
                    session,
                    signal=signal,
                    risk_usdt=risk_usdt,
                    qty=qty,
                    leverage=leverage,
                    error=f"max live trades per day reached: {max_trades_per_day}",
                    now=now,
                )
                return None
            if await self.todays_virtual_pnl(session, now=now) <= -max_daily_loss_usdt:
                await self._reject_entry_record(
                    session,
                    signal=signal,
                    risk_usdt=risk_usdt,
                    qty=qty,
                    leverage=leverage,
                    error=f"max live daily loss reached: {max_daily_loss_usdt} USDT",
                    now=now,
                )
                return None

            record = LiveExecutionRecord(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                direction=signal.direction,
                status="entry_submitting",
                order_budget_usdt=risk_usdt,
                entry_qty=qty,
                remaining_qty=qty,
                entry_price=signal.planned_entry,
                current_stop=signal.stop_loss,
                entry_order_link_id=f"sp-{signal.signal_id}-entry",
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            await session.flush()
            await self._notify(
                session,
                signal_id=signal.signal_id,
                event_type="live_entry_submitting",
                idempotency_key=f"live:{signal.signal_id}:entry_submitting",
                now=now,
                payload=_payload(
                    signal,
                    qty=qty,
                    status="entry_submitting",
                    risk_usdt=risk_usdt,
                    leverage=leverage,
                ),
            )
            return record.id

    async def mark_entry_pending(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
        order_id: str,
        leverage: Decimal,
        limit_price: Decimal,
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "entry_pending"
            record.entry_order_id = order_id
            record.entry_price = limit_price
            record.entry_submitted_at = now
            record.updated_at = now
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_entry_pending",
                idempotency_key=f"live:{record.signal_id}:entry_pending",
                now=now,
                payload=_live_payload(record)
                | {
                    "leverage": str(leverage),
                    "estimated_margin_usdt": str(record.entry_qty * limit_price / leverage),
                },
            )

    async def mark_entry_cancelled(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
        error: str,
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "skipped"
            record.remaining_qty = Decimal(0)
            record.error = error[:2000]
            record.updated_at = now
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_entry_skipped",
                idempotency_key=f"live:{record.signal_id}:entry_cancelled:{record.id}",
                now=now,
                payload=_live_payload(record) | {"error": record.error},
            )

    async def _reject_entry_record(
        self,
        session: AsyncSession,
        *,
        signal: LiveSignalView,
        risk_usdt: Decimal,
        qty: Decimal,
        leverage: Decimal,
        error: str,
        now: datetime,
        notify: bool = True,
    ) -> None:
        record = LiveExecutionRecord(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            direction=signal.direction,
            status="skipped",
            order_budget_usdt=risk_usdt,
            entry_qty=qty,
            remaining_qty=qty,
            entry_price=signal.planned_entry,
            current_stop=signal.stop_loss,
            error=error[:2000],
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.flush()
        if notify:
            await self._notify(
                session,
                signal_id=signal.signal_id,
                event_type="live_entry_skipped",
                idempotency_key=f"live:{signal.signal_id}:skipped:{record.id}",
                now=now,
                payload=_payload(
                    signal,
                    qty=qty,
                    status="skipped",
                    risk_usdt=risk_usdt,
                    leverage=leverage,
                )
                | {"error": record.error},
            )

    async def mark_entry_open(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
        order_id: str,
        qty: Decimal,
        stop_loss: Decimal,
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "open"
            record.entry_order_id = order_id
            record.entry_submitted_at = now
            record.entry_qty = qty
            record.remaining_qty = qty
            record.current_stop = stop_loss
            record.updated_at = now
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_entry_open",
                idempotency_key=f"live:{record.signal_id}:entry_open",
                now=now,
                payload=_live_payload(record),
            )

    async def claim_tp1(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
    ) -> bool:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            if record.status != "open":
                return False
            record.status = "tp1_submitting"
            record.updated_at = datetime.now(UTC)
            return True

    async def mark_tp1_reduced(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
        order_id: str,
        remaining_qty: Decimal,
        current_stop: Decimal,
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "tp1_reduced"
            record.tp1_order_id = order_id
            record.tp1_order_link_id = f"sp-{record.signal_id}-tp1"
            record.tp1_submitted_at = now
            record.remaining_qty = remaining_qty
            record.current_stop = current_stop
            record.updated_at = now
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_tp1_reduced",
                idempotency_key=f"live:{record.signal_id}:tp1_reduced",
                now=now,
                payload=_live_payload(record),
            )

    async def claim_close(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
    ) -> Decimal | None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            if record.status not in {"open", "tp1_reduced"}:
                return None
            qty = record.remaining_qty
            record.status = "closing"
            record.updated_at = datetime.now(UTC)
            return qty

    async def mark_closed(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
        order_id: str,
        real_pnl: Decimal | None = None,
        real_entry_price: Decimal | None = None,
        real_exit_price: Decimal | None = None,
        error: str | None = None,
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "closed"
            record.close_order_id = order_id
            record.close_order_link_id = f"sp-{record.signal_id}-close"
            record.remaining_qty = Decimal(0)
            record.real_pnl = real_pnl
            record.real_entry_price = real_entry_price
            record.real_exit_price = real_exit_price
            record.error = error[:2000] if error else None
            record.closed_at = now
            record.updated_at = now
            payload = _live_payload(record) | _real_pnl_payload(
                real_pnl=real_pnl,
                real_entry_price=real_entry_price,
                real_exit_price=real_exit_price,
            )
            if record.error:
                payload["error"] = record.error
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_position_closed",
                idempotency_key=f"live:{record.signal_id}:closed",
                now=now,
                payload=payload,
            )

    async def mark_failed(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        live_id: int,
        error: str,
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "failed"
            record.error = error[:2000]
            record.updated_at = now
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_execution_failed",
                idempotency_key=f"live:{record.signal_id}:failed:{record.id}",
                now=now,
                payload=_live_payload(record) | {"error": record.error},
            )

    @staticmethod
    async def _locked(session: AsyncSession, live_id: int) -> LiveExecutionRecord:
        record = await session.scalar(
            select(LiveExecutionRecord).where(LiveExecutionRecord.id == live_id).with_for_update()
        )
        if record is None:
            raise ValueError(f"Live execution {live_id} does not exist")
        return record

    @staticmethod
    async def _notify(
        session: AsyncSession,
        *,
        signal_id: int,
        event_type: str,
        idempotency_key: str,
        now: datetime,
        payload: dict[str, object],
    ) -> None:
        exists = await session.scalar(
            select(NotificationOutboxRecord.id).where(
                NotificationOutboxRecord.idempotency_key == idempotency_key
            )
        )
        if exists is not None:
            return
        session.add(
            NotificationOutboxRecord(
                idempotency_key=idempotency_key,
                event_type=event_type,
                signal_id=signal_id,
                payload=payload,
                status="pending",
                available_at=now,
            )
        )


def _payload(
    signal: LiveSignalView,
    *,
    qty: Decimal,
    status: str,
    risk_usdt: Decimal,
    leverage: Decimal,
) -> dict[str, object]:
    notional = qty * signal.planned_entry
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "score": signal.score,
        "status": status,
        "qty": str(qty),
        "risk_usdt": str(risk_usdt),
        "leverage": str(leverage),
        "notional_usdt": str(notional),
        "estimated_margin_usdt": str(notional / leverage),
        "planned_entry": str(signal.planned_entry),
        "stop_loss": str(signal.stop_loss),
    }


def _live_payload(record: LiveExecutionRecord) -> dict[str, object]:
    return {
        "signal_id": record.signal_id,
        "symbol": record.symbol,
        "direction": record.direction,
        "status": record.status,
        "qty": str(record.entry_qty),
        "remaining_qty": str(record.remaining_qty),
        "risk_usdt": str(record.order_budget_usdt),
        "notional_usdt": str(record.entry_qty * record.entry_price),
        "planned_entry": str(record.entry_price),
        "stop_loss": str(record.current_stop),
        "entry_order_id": record.entry_order_id or "",
        "tp1_order_id": record.tp1_order_id or "",
        "close_order_id": record.close_order_id or "",
    }


def _real_pnl_payload(
    *,
    real_pnl: Decimal | None,
    real_entry_price: Decimal | None,
    real_exit_price: Decimal | None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if real_pnl is not None:
        payload["real_pnl_usdt"] = str(real_pnl)
    if real_entry_price is not None:
        payload["real_entry_price"] = str(real_entry_price)
    if real_exit_price is not None:
        payload["real_exit_price"] = str(real_exit_price)
    return payload
