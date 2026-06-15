from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    DataGapRecord,
    EvaluationWindowRecord,
    SignalCandidateRecord,
    SignalRecord,
    StrategyVersionRecord,
    VirtualTradeRecord,
)
from crypto_smc.observation.models import (
    EvaluationReport,
    EvaluationWindow,
    TradeObservation,
)
from crypto_smc.observation.reporting import build_evaluation_report

TERMINAL_TRADE_STATUSES = (
    "stopped",
    "stopped_at_breakeven",
    "tp2_completed",
    "expired",
    "invalidated",
    "ambiguous",
    "coverage_failed",
)


class ObservationRepository:
    async def start_window(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        name: str,
        strategy_version: str | None = None,
        started_at: datetime | None = None,
        minimum_completed_signals: int = 100,
        minimum_profit_factor: Decimal = Decimal("1.3"),
        maximum_drawdown_fraction: Decimal = Decimal("0.15"),
        maximum_symbol_share: Decimal = Decimal("0.35"),
    ) -> EvaluationWindow:
        if not name.strip():
            raise ValueError("Evaluation window name cannot be empty")
        async with session_factory() as session, session.begin():
            existing = await session.scalar(
                select(EvaluationWindowRecord.id).where(EvaluationWindowRecord.status == "active")
            )
            if existing is not None:
                raise ValueError("An evaluation window is already active")
            version = await session.scalar(
                select(StrategyVersionRecord).where(
                    StrategyVersionRecord.version == strategy_version
                    if strategy_version is not None
                    else StrategyVersionRecord.is_active.is_(True)
                )
            )
            if version is None:
                raise ValueError(
                    f"Strategy version {strategy_version} does not exist"
                    if strategy_version is not None
                    else "No active strategy version exists"
                )
            await session.execute(
                update(StrategyVersionRecord)
                .where(
                    StrategyVersionRecord.id != version.id,
                    StrategyVersionRecord.is_active.is_(True),
                )
                .values(is_active=False)
            )
            version.is_active = True
            reference_balance = Decimal(str(version.parameters.get("reference_balance", "10000")))
            record = EvaluationWindowRecord(
                name=name.strip(),
                strategy_version_id=version.id,
                status="active",
                started_at=started_at or datetime.now(UTC),
                minimum_completed_signals=minimum_completed_signals,
                minimum_profit_factor=minimum_profit_factor,
                maximum_drawdown_fraction=maximum_drawdown_fraction,
                maximum_symbol_share=maximum_symbol_share,
                reference_balance=reference_balance,
                configuration={
                    "strategy_version": version.version,
                    "parameter_checksum": version.parameter_checksum,
                    "parameters": version.parameters,
                },
            )
            session.add(record)
            await session.flush()
            return _window_view(record, version.version)

    async def current_window(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> EvaluationWindow | None:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(EvaluationWindowRecord, StrategyVersionRecord.version)
                    .join(
                        StrategyVersionRecord,
                        StrategyVersionRecord.id == EvaluationWindowRecord.strategy_version_id,
                    )
                    .where(EvaluationWindowRecord.status == "active")
                )
            ).first()
        return _window_view(*row) if row is not None else None

    async def close_window(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ended_at: datetime | None = None,
    ) -> EvaluationWindow:
        async with session_factory() as session, session.begin():
            row = (
                await session.execute(
                    select(EvaluationWindowRecord, StrategyVersionRecord.version)
                    .join(
                        StrategyVersionRecord,
                        StrategyVersionRecord.id == EvaluationWindowRecord.strategy_version_id,
                    )
                    .where(EvaluationWindowRecord.status == "active")
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise ValueError("No active evaluation window exists")
            record, version = row
            record.status = "closed"
            record.ended_at = ended_at or datetime.now(UTC)
            return _window_view(record, version)

    async def report(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        window_name: str | None = None,
    ) -> EvaluationReport:
        async with session_factory() as session:
            window_row = (
                await session.execute(
                    select(EvaluationWindowRecord, StrategyVersionRecord.version)
                    .join(
                        StrategyVersionRecord,
                        StrategyVersionRecord.id == EvaluationWindowRecord.strategy_version_id,
                    )
                    .where(
                        EvaluationWindowRecord.name == window_name
                        if window_name is not None
                        else EvaluationWindowRecord.status == "active"
                    )
                    .order_by(EvaluationWindowRecord.started_at.desc())
                    .limit(1)
                )
            ).first()
            if window_row is None:
                raise ValueError("Evaluation window not found")
            record, version = window_row
            window = _window_view(record, version)
            trades = await self._load_trades(session, record)
            suppression_reasons = await self._suppression_reasons(session, record)
            gap_statement = (
                select(func.count())
                .select_from(DataGapRecord)
                .where(
                    DataGapRecord.status.in_(("recovering", "failed")),
                    DataGapRecord.detected_at >= record.started_at,
                )
            )
            if record.ended_at is not None:
                gap_statement = gap_statement.where(DataGapRecord.detected_at <= record.ended_at)
            unresolved_gaps = await session.scalar(gap_statement)
            coverage_statement = (
                select(func.count())
                .select_from(SignalRecord)
                .join(
                    SignalCandidateRecord,
                    SignalCandidateRecord.id == SignalRecord.candidate_id,
                )
                .where(
                    SignalCandidateRecord.strategy_version_id == record.strategy_version_id,
                    SignalRecord.status == "coverage_failed",
                    SignalRecord.created_at >= record.started_at,
                )
            )
            if record.ended_at is not None:
                coverage_statement = coverage_statement.where(
                    SignalRecord.created_at <= record.ended_at
                )
            coverage_failures = await session.scalar(coverage_statement)
        return build_evaluation_report(
            window=window,
            trades=trades,
            suppression_reasons=suppression_reasons,
            unresolved_data_gaps=unresolved_gaps or 0,
            coverage_failures=coverage_failures or 0,
        )

    @staticmethod
    async def _load_trades(
        session: AsyncSession,
        window: EvaluationWindowRecord,
    ) -> tuple[TradeObservation, ...]:
        statement = (
            select(
                SignalRecord,
                SignalCandidateRecord.score,
                VirtualTradeRecord,
            )
            .join(
                SignalCandidateRecord,
                SignalCandidateRecord.id == SignalRecord.candidate_id,
            )
            .join(
                VirtualTradeRecord,
                VirtualTradeRecord.signal_id == SignalRecord.id,
            )
            .where(
                SignalCandidateRecord.strategy_version_id == window.strategy_version_id,
                SignalRecord.created_at >= window.started_at,
                VirtualTradeRecord.status.in_(TERMINAL_TRADE_STATUSES),
            )
            .order_by(VirtualTradeRecord.resolved_at, SignalRecord.id)
        )
        if window.ended_at is not None:
            statement = statement.where(SignalRecord.created_at <= window.ended_at)
        rows = (await session.execute(statement)).all()
        return tuple(
            TradeObservation(
                signal_id=signal.id,
                symbol=signal.symbol,
                direction=signal.direction,
                score=score,
                status=trade.status,
                created_at=signal.created_at,
                entered_at=trade.entered_at,
                resolved_at=trade.resolved_at,
                realized_pnl=trade.realized_pnl,
                fees=trade.fees,
                estimated_funding=trade.estimated_funding,
                r_multiple=trade.r_multiple,
                ambiguous=trade.ambiguous,
            )
            for signal, score, trade in rows
        )

    @staticmethod
    async def _suppression_reasons(
        session: AsyncSession,
        window: EvaluationWindowRecord,
    ) -> dict[str, int]:
        statement = select(SignalCandidateRecord.suppression_reasons).where(
            SignalCandidateRecord.strategy_version_id == window.strategy_version_id,
            SignalCandidateRecord.status == "suppressed",
            SignalCandidateRecord.created_at >= window.started_at,
        )
        if window.ended_at is not None:
            statement = statement.where(SignalCandidateRecord.created_at <= window.ended_at)
        rows = (await session.scalars(statement)).all()
        reasons = Counter(reason for items in rows for reason in items)
        return dict(sorted(reasons.items()))


def _window_view(
    record: EvaluationWindowRecord,
    strategy_version: str,
) -> EvaluationWindow:
    return EvaluationWindow(
        id=record.id,
        name=record.name,
        strategy_version=strategy_version,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        minimum_completed_signals=record.minimum_completed_signals,
        minimum_profit_factor=record.minimum_profit_factor,
        maximum_drawdown_fraction=record.maximum_drawdown_fraction,
        maximum_symbol_share=record.maximum_symbol_share,
        reference_balance=record.reference_balance,
    )
