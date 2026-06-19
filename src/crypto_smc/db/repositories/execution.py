from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    InstrumentRecord,
    LiveExecutionRecord,
    NotificationOutboxRecord,
    SignalRecord,
    VirtualTradeRecord,
)

OPEN_LIVE_EXECUTION_STATUSES = frozenset(
    {"entry_submitting", "open", "tp1_submitting", "tp1_reduced", "closing"}
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
    signal_status: str
    planned_entry: Decimal
    stop_loss: Decimal
    current_stop: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    virtual_remaining_quantity: Decimal
    quantity_step: Decimal
    min_order_quantity: Decimal
    max_market_order_quantity: Decimal
    min_notional_value: Decimal
    live_id: int | None
    live_status: str | None
    live_remaining_qty: Decimal | None


class LiveExecutionRepository:
    async def list_actionable(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> tuple[LiveSignalView, ...]:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        SignalRecord,
                        VirtualTradeRecord,
                        InstrumentRecord,
                        LiveExecutionRecord,
                    )
                    .join(VirtualTradeRecord, VirtualTradeRecord.signal_id == SignalRecord.id)
                    .join(InstrumentRecord, InstrumentRecord.symbol == SignalRecord.symbol)
                    .outerjoin(
                        LiveExecutionRecord,
                        LiveExecutionRecord.signal_id == SignalRecord.id,
                    )
                    .where(
                        SignalRecord.status.in_(
                            {
                                "entered",
                                "tp1_reached",
                                *TERMINAL_SIGNAL_STATUSES,
                            }
                        )
                    )
                    .order_by(SignalRecord.created_at, SignalRecord.id)
                )
            ).all()
        return tuple(
            LiveSignalView(
                signal_id=signal.id,
                symbol=signal.symbol,
                direction=signal.direction,
                signal_status=signal.status,
                planned_entry=signal.planned_entry,
                stop_loss=signal.stop_loss,
                current_stop=trade.current_stop,
                take_profit_1=signal.take_profit_1,
                take_profit_2=signal.take_profit_2,
                virtual_remaining_quantity=trade.remaining_quantity,
                quantity_step=instrument.quantity_step,
                min_order_quantity=instrument.min_order_quantity,
                max_market_order_quantity=instrument.max_market_order_quantity,
                min_notional_value=instrument.min_notional_value,
                live_id=live.id if live is not None else None,
                live_status=live.status if live is not None else None,
                live_remaining_qty=live.remaining_qty if live is not None else None,
            )
            for signal, trade, instrument, live in rows
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
                        LiveExecutionRecord.status != "failed",
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
        )
        return Decimal(str(total or 0))

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
            if signal.signal_status != "entered":
                return None
            existing = await session.scalar(
                select(LiveExecutionRecord).where(
                    LiveExecutionRecord.signal_id == signal.signal_id
                )
            )
            if existing is not None:
                return None
            if await self.open_live_count(session) >= max_open_positions:
                return None
            if await self.todays_trade_count(session, now=now) >= max_trades_per_day:
                return None
            if await self.todays_virtual_pnl(session, now=now) <= -max_daily_loss_usdt:
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
        now: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await self._locked(session, live_id)
            record.status = "closed"
            record.close_order_id = order_id
            record.close_order_link_id = f"sp-{record.signal_id}-close"
            record.remaining_qty = Decimal(0)
            record.error = None
            record.closed_at = now
            record.updated_at = now
            await self._notify(
                session,
                signal_id=record.signal_id,
                event_type="live_position_closed",
                idempotency_key=f"live:{record.signal_id}:closed",
                now=now,
                payload=_live_payload(record),
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
        "status": status,
        "qty": str(qty),
        "risk_usdt": str(risk_usdt),
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
