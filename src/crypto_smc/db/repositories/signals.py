from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    AnalysisSnapshotRecord,
    InstrumentRecord,
    NotificationOutboxRecord,
    SignalCandidateRecord,
    SignalEventRecord,
    SignalRecord,
    VirtualTradeRecord,
)
from crypto_smc.signals import (
    SignalObservation,
    SignalPolicyConfig,
    SignalStatus,
    VirtualTradeStatus,
    evaluate_publication,
    transition_signal,
    transition_virtual_trade,
)
from crypto_smc.signals.policy import ACTIVE_SIGNAL_STATUSES
from crypto_smc.strategy import SignalCandidate

SIGNAL_PUBLICATION_LOCK_ID = 7_216_846_135


@dataclass(frozen=True, slots=True)
class SignalPublicationResult:
    prepared: int = 0
    suppressed: int = 0


@dataclass(frozen=True, slots=True)
class SignalTransitionResult:
    applied: bool
    signal_id: int
    status: str


@dataclass(frozen=True, slots=True)
class SignalFilters:
    symbol: str | None = None
    status: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class SignalView:
    id: int
    candidate_id: int
    symbol: str
    direction: str
    status: str
    suppression_reason: str | None
    entry_lower: Decimal
    entry_upper: Decimal
    planned_entry: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    quantity: Decimal
    risk_amount: Decimal
    expires_at: datetime
    created_at: datetime
    trade_status: str | None
    realized_pnl: Decimal | None
    r_multiple: Decimal | None
    ambiguous: bool | None
    fees: Decimal | None = None
    estimated_funding: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TrackingSignalView:
    id: int
    symbol: str
    direction: str
    status: str
    entry_lower: Decimal
    entry_upper: Decimal
    planned_entry: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    quantity: Decimal
    risk_amount: Decimal
    taker_fee_rate: Decimal
    expires_at: datetime
    created_at: datetime
    current_stop: Decimal
    remaining_quantity: Decimal
    last_trade_id: str | None
    last_trade_time: datetime | None
    last_trade_sequence: int | None
    funding_rate: Decimal = Decimal(0)
    funding_interval_minutes: int = 480
    entered_at: datetime | None = None
    tp1_reached_at: datetime | None = None

    @property
    def coverage_anchor(self) -> datetime:
        return self.last_trade_time or self.created_at


class SignalRepository:
    async def publish_candidates(
        self,
        session: AsyncSession,
        *,
        candidates: tuple[tuple[SignalCandidateRecord, SignalCandidate], ...],
        config: SignalPolicyConfig,
        now: datetime | None = None,
    ) -> SignalPublicationResult:
        accepted = tuple(
            (record, candidate)
            for record, candidate in candidates
            if candidate.status == "accepted" and candidate.trade_plan is not None
        )
        if not accepted:
            return SignalPublicationResult()

        publication_time = now or datetime.now(UTC)
        await session.scalar(select(func.pg_advisory_xact_lock(SIGNAL_PUBLICATION_LOCK_ID)))
        earliest = publication_time - max(
            timedelta(hours=1),
            timedelta(minutes=config.cooldown_minutes),
            timedelta(minutes=config.burst_window_minutes),
        )
        existing = (
            await session.scalars(
                select(SignalRecord).where(
                    or_(
                        SignalRecord.status.in_(ACTIVE_SIGNAL_STATUSES),
                        SignalRecord.created_at > earliest,
                    )
                )
            )
        ).all()
        observations = [
            SignalObservation(
                symbol=item.symbol,
                status=item.status,
                created_at=item.created_at,
            )
            for item in existing
        ]
        prepared = 0
        suppressed = 0
        for record, candidate in accepted:
            decision = evaluate_publication(
                candidate,
                tuple(observations),
                now=publication_time,
                config=config,
            )
            status = "preparing" if decision.allowed else "suppressed"
            snapshot = await session.get(
                AnalysisSnapshotRecord,
                record.analysis_snapshot_id,
            )
            instrument = await session.get(InstrumentRecord, record.symbol)
            funding_rate = Decimal(0)
            if snapshot is not None:
                raw_funding_rate = snapshot.market_context.get("funding_rate")
                if raw_funding_rate is not None:
                    funding_rate = Decimal(str(raw_funding_rate))
            signal = self._signal_record(
                record,
                status=status,
                reason=decision.reason,
                now=publication_time,
                funding_rate=funding_rate,
                funding_interval_minutes=(
                    instrument.funding_interval_minutes if instrument is not None else 480
                ),
            )
            session.add(signal)
            await session.flush()
            session.add(
                SignalEventRecord(
                    signal_id=signal.id,
                    event_type="signal_prepared" if decision.allowed else "signal_suppressed",
                    status_from=None,
                    status_to=status,
                    event_time=publication_time,
                    source_event_id=None,
                    idempotency_key=f"candidate:{record.id}:publication",
                    payload={"reason": decision.reason} if decision.reason else {},
                )
            )
            observations.append(
                SignalObservation(
                    symbol=signal.symbol,
                    status=signal.status,
                    created_at=publication_time,
                )
            )
            if decision.allowed:
                session.add(self._virtual_trade(signal, record))
                prepared += 1
            else:
                suppressed += 1
        return SignalPublicationResult(prepared=prepared, suppressed=suppressed)

    async def list_recoverable(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> tuple[int, ...]:
        async with session_factory() as session:
            signal_ids = (
                await session.scalars(
                    select(SignalRecord.id)
                    .where(SignalRecord.status.in_(ACTIVE_SIGNAL_STATUSES))
                    .order_by(SignalRecord.created_at, SignalRecord.id)
                )
            ).all()
        return tuple(signal_ids)

    async def list_tracking_signals(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> tuple[TrackingSignalView, ...]:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(SignalRecord, VirtualTradeRecord)
                    .join(
                        VirtualTradeRecord,
                        VirtualTradeRecord.signal_id == SignalRecord.id,
                    )
                    .where(SignalRecord.status.in_(ACTIVE_SIGNAL_STATUSES))
                    .order_by(SignalRecord.created_at, SignalRecord.id)
                )
            ).all()
        return tuple(
            TrackingSignalView(
                id=signal.id,
                symbol=signal.symbol,
                direction=signal.direction,
                status=signal.status,
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
                created_at=signal.created_at,
                current_stop=trade.current_stop,
                remaining_quantity=trade.remaining_quantity,
                last_trade_id=trade.last_trade_id,
                last_trade_time=trade.last_trade_time,
                last_trade_sequence=trade.last_trade_sequence,
                funding_rate=signal.funding_rate,
                funding_interval_minutes=signal.funding_interval_minutes,
                entered_at=signal.entered_at,
                tp1_reached_at=signal.tp1_reached_at,
            )
            for signal, trade in rows
        )

    async def list_signals(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        filters: SignalFilters,
    ) -> list[SignalView]:
        statement = (
            select(SignalRecord, VirtualTradeRecord)
            .outerjoin(
                VirtualTradeRecord,
                VirtualTradeRecord.signal_id == SignalRecord.id,
            )
            .order_by(SignalRecord.created_at.desc(), SignalRecord.id.desc())
            .limit(filters.limit)
        )
        if filters.symbol is not None:
            statement = statement.where(SignalRecord.symbol == filters.symbol.upper())
        if filters.status is not None:
            statement = statement.where(SignalRecord.status == filters.status)
        async with session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            SignalView(
                id=signal.id,
                candidate_id=signal.candidate_id,
                symbol=signal.symbol,
                direction=signal.direction,
                status=signal.status,
                suppression_reason=signal.suppression_reason,
                entry_lower=signal.entry_lower,
                entry_upper=signal.entry_upper,
                planned_entry=signal.planned_entry,
                stop_loss=signal.stop_loss,
                take_profit_1=signal.take_profit_1,
                take_profit_2=signal.take_profit_2,
                quantity=signal.quantity,
                risk_amount=signal.risk_amount,
                expires_at=signal.expires_at,
                created_at=signal.created_at,
                trade_status=trade.status if trade is not None else None,
                realized_pnl=trade.realized_pnl if trade is not None else None,
                r_multiple=trade.r_multiple if trade is not None else None,
                ambiguous=trade.ambiguous if trade is not None else None,
                fees=trade.fees if trade is not None else None,
                estimated_funding=(trade.estimated_funding if trade is not None else None),
            )
            for signal, trade in rows
        ]

    async def apply_transition(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal_id: int,
        target: SignalStatus,
        event_time: datetime,
        idempotency_key: str,
        event_type: str,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
        realized_pnl: Decimal | None = None,
        fees: Decimal | None = None,
        estimated_funding: Decimal | None = None,
        r_multiple: Decimal | None = None,
        ambiguous: bool | None = None,
        resolution_note: str | None = None,
        current_stop: Decimal | None = None,
        remaining_quantity: Decimal | None = None,
    ) -> SignalTransitionResult:
        async with session_factory() as session, session.begin():
            duplicate = await session.scalar(
                select(SignalEventRecord).where(
                    SignalEventRecord.idempotency_key == idempotency_key
                )
            )
            if duplicate is not None:
                if duplicate.signal_id != signal_id:
                    raise ValueError(
                        f"Idempotency key {idempotency_key} belongs to signal {duplicate.signal_id}"
                    )
                current = await session.get(SignalRecord, signal_id)
                if current is None:
                    raise ValueError(f"Signal {signal_id} does not exist")
                return SignalTransitionResult(False, signal_id, current.status)

            signal = await session.scalar(
                select(SignalRecord).where(SignalRecord.id == signal_id).with_for_update()
            )
            if signal is None:
                raise ValueError(f"Signal {signal_id} does not exist")
            current_status = cast(SignalStatus, signal.status)
            next_status = transition_signal(current_status, target)
            trade = await session.scalar(
                select(VirtualTradeRecord)
                .where(VirtualTradeRecord.signal_id == signal_id)
                .with_for_update()
            )
            if trade is None:
                raise ValueError(f"Signal {signal_id} has no virtual trade")

            trade_target = _virtual_trade_target(next_status)
            if trade_target is not None:
                trade.status = transition_virtual_trade(
                    cast(VirtualTradeStatus, trade.status),
                    trade_target,
                )
            signal.status = next_status
            signal.version += 1
            trade.version += 1
            if next_status == "active":
                signal.activated_at = event_time
            if next_status == "entered":
                signal.entered_at = event_time
                trade.entered_at = event_time
            if next_status == "tp1_reached":
                signal.tp1_reached_at = event_time
            if next_status in TERMINAL_SIGNAL_STATUSES:
                signal.closed_at = event_time
                trade.resolved_at = event_time
            if realized_pnl is not None:
                trade.realized_pnl = realized_pnl
            if fees is not None:
                trade.fees = fees
            if estimated_funding is not None:
                trade.estimated_funding = estimated_funding
            if r_multiple is not None:
                trade.r_multiple = r_multiple
            if ambiguous is not None:
                trade.ambiguous = ambiguous
            if resolution_note is not None:
                trade.resolution_note = resolution_note
            if current_stop is not None:
                trade.current_stop = current_stop
            if remaining_quantity is not None:
                trade.remaining_quantity = remaining_quantity
            signal_event = SignalEventRecord(
                signal_id=signal_id,
                event_type=event_type,
                status_from=current_status,
                status_to=next_status,
                event_time=event_time,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key,
                payload=payload or {},
            )
            session.add(signal_event)
            candidate = await session.get(SignalCandidateRecord, signal.candidate_id)
            notification_type = _notification_type(next_status)
            if notification_type is not None and candidate is not None:
                session.add(
                    NotificationOutboxRecord(
                        idempotency_key=f"signal-event:{idempotency_key}",
                        event_type=notification_type,
                        signal_id=signal.id,
                        payload=_notification_payload(
                            signal,
                            trade,
                            candidate,
                            event_type=event_type,
                            event_time=event_time,
                        ),
                        status="pending",
                        available_at=event_time,
                    )
                )
        return SignalTransitionResult(True, signal_id, next_status)

    async def checkpoint_trade(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal_id: int,
        trade_id: str,
        executed_at: datetime,
        sequence: int,
    ) -> None:
        async with session_factory() as session, session.begin():
            trade = await session.scalar(
                select(VirtualTradeRecord)
                .where(VirtualTradeRecord.signal_id == signal_id)
                .with_for_update()
            )
            if trade is None:
                raise ValueError(f"Signal {signal_id} has no virtual trade")
            if trade.last_trade_time is not None and (
                executed_at,
                sequence,
                trade_id,
            ) <= (
                trade.last_trade_time,
                trade.last_trade_sequence or 0,
                trade.last_trade_id or "",
            ):
                return
            trade.last_trade_id = trade_id
            trade.last_trade_time = executed_at
            trade.last_trade_sequence = sequence

    async def record_tracking_event(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        signal_id: int,
        event_time: datetime,
        idempotency_key: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        async with session_factory() as session, session.begin():
            existing = await session.scalar(
                select(SignalEventRecord.id).where(
                    SignalEventRecord.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return False
            signal = await session.get(SignalRecord, signal_id)
            if signal is None:
                raise ValueError(f"Signal {signal_id} does not exist")
            session.add(
                SignalEventRecord(
                    signal_id=signal_id,
                    event_type=event_type,
                    status_from=signal.status,
                    status_to=signal.status,
                    event_time=event_time,
                    source_event_id=None,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
            )
        return True

    @staticmethod
    def _signal_record(
        candidate: SignalCandidateRecord,
        *,
        status: str,
        reason: str | None,
        now: datetime,
        funding_rate: Decimal,
        funding_interval_minutes: int,
    ) -> SignalRecord:
        required = (
            candidate.entry_lower,
            candidate.entry_upper,
            candidate.planned_entry,
            candidate.stop_loss,
            candidate.take_profit_1,
            candidate.take_profit_2,
            candidate.quantity,
            candidate.risk_amount,
        )
        if any(value is None for value in required):
            raise ValueError("Accepted candidate is missing a trade plan")
        return SignalRecord(
            candidate_id=candidate.id,
            symbol=candidate.symbol,
            direction=candidate.direction,
            status=status,
            suppression_reason=reason,
            entry_lower=candidate.entry_lower,
            entry_upper=candidate.entry_upper,
            planned_entry=candidate.planned_entry,
            stop_loss=candidate.stop_loss,
            take_profit_1=candidate.take_profit_1,
            take_profit_2=candidate.take_profit_2,
            quantity=candidate.quantity,
            risk_amount=candidate.risk_amount,
            taker_fee_rate=(
                candidate.estimated_entry_fee / candidate.notional
                if candidate.estimated_entry_fee is not None
                and candidate.notional is not None
                and candidate.notional > 0
                else Decimal("0.00055")
            ),
            funding_rate=funding_rate,
            funding_interval_minutes=funding_interval_minutes,
            expires_at=candidate.expires_at,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _virtual_trade(
        signal: SignalRecord,
        candidate: SignalCandidateRecord,
    ) -> VirtualTradeRecord:
        if (
            candidate.planned_entry is None
            or candidate.stop_loss is None
            or candidate.take_profit_1 is None
            or candidate.take_profit_2 is None
            or candidate.quantity is None
        ):
            raise ValueError("Accepted candidate is missing virtual trade levels")
        return VirtualTradeRecord(
            signal_id=signal.id,
            status="waiting_entry",
            planned_entry=candidate.planned_entry,
            current_stop=candidate.stop_loss,
            take_profit_1=candidate.take_profit_1,
            take_profit_2=candidate.take_profit_2,
            quantity=candidate.quantity,
            remaining_quantity=candidate.quantity,
        )


TERMINAL_SIGNAL_STATUSES = frozenset(
    {
        "expired",
        "invalidated",
        "stopped",
        "stopped_at_breakeven",
        "tp2_completed",
        "ambiguous",
        "coverage_failed",
    }
)


def _virtual_trade_target(status: SignalStatus) -> VirtualTradeStatus | None:
    if status in {"preparing", "active"}:
        return None
    mapping: dict[SignalStatus, VirtualTradeStatus] = {
        "suppressed": "coverage_failed",
        "expired": "expired",
        "invalidated": "invalidated",
        "entered": "entered",
        "stopped": "stopped",
        "tp1_reached": "tp1_reached",
        "stopped_at_breakeven": "stopped_at_breakeven",
        "tp2_completed": "tp2_completed",
        "ambiguous": "ambiguous",
        "coverage_failed": "coverage_failed",
    }
    return mapping[status]


def _notification_type(status: SignalStatus) -> str | None:
    mapping: dict[SignalStatus, str] = {
        "active": "new_signal",
        "entered": "entry_filled",
        "tp1_reached": "take_profit_1",
        "stopped": "signal_result",
        "stopped_at_breakeven": "signal_result",
        "tp2_completed": "signal_result",
        "ambiguous": "signal_warning",
        "coverage_failed": "signal_warning",
        "expired": "signal_expired",
        "invalidated": "signal_invalidated",
        "preparing": "signal_warning",
        "suppressed": "signal_warning",
    }
    return mapping.get(status)


def _notification_payload(
    signal: SignalRecord,
    trade: VirtualTradeRecord,
    candidate: SignalCandidateRecord,
    *,
    event_type: str,
    event_time: datetime,
) -> dict[str, object]:
    return {
        "signal_id": signal.id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "status": signal.status,
        "event_type": event_type,
        "event_time": event_time.isoformat(),
        "score": candidate.score,
        "strength": candidate.strength,
        "entry_lower": str(signal.entry_lower),
        "entry_upper": str(signal.entry_upper),
        "planned_entry": str(signal.planned_entry),
        "stop_loss": str(signal.stop_loss),
        "take_profit_1": str(signal.take_profit_1),
        "take_profit_2": str(signal.take_profit_2),
        "quantity": str(signal.quantity),
        "risk_amount": str(signal.risk_amount),
        "realized_pnl": str(trade.realized_pnl),
        "fees": str(trade.fees),
        "estimated_funding": str(trade.estimated_funding),
        "r_multiple": str(trade.r_multiple),
        "ambiguous": trade.ambiguous,
    }
