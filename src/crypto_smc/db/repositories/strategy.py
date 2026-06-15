import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    AggregatedCandleRecord,
    AnalysisSnapshotRecord,
    EvaluationWindowRecord,
    InstrumentRecord,
    SignalCandidateRecord,
    StrategyVersionRecord,
    UniverseMemberRecord,
    UniverseSnapshotRecord,
)
from crypto_smc.db.repositories.signals import SignalRepository
from crypto_smc.signals import SignalPolicyConfig
from crypto_smc.strategy import SignalCandidate, StrategyConfig, StrategyInput
from crypto_smc.strategy.serialization import json_safe
from smc_core import Candle, Timeframe


@dataclass(frozen=True, slots=True)
class CandidateFilters:
    symbol: str | None = None
    direction: Literal["long", "short"] | None = None
    status: Literal["accepted", "suppressed"] | None = None
    minimum_score: int | None = None
    strategy_version: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class CandidateView:
    id: int
    analysis_snapshot_id: int
    symbol: str
    direction: str
    status: str
    score: int
    strength: str
    strategy_version: str
    entry_lower: Decimal | None
    entry_upper: Decimal | None
    planned_entry: Decimal | None
    stop_loss: Decimal | None
    take_profit_1: Decimal | None
    take_profit_2: Decimal | None
    gross_reward_to_risk: Decimal | None
    net_reward_to_risk: Decimal | None
    risk_amount: Decimal | None
    quantity: Decimal | None
    notional: Decimal | None
    recommended_leverage: Decimal | None
    estimated_margin: Decimal | None
    estimated_loss_at_stop: Decimal | None
    invalidation: str | None
    score_components: list[dict[str, Any]]
    evidence: list[str]
    warnings: list[str]
    suppression_reasons: list[str]
    analyzed_at: datetime
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StrategySymbolProfile:
    symbol: str
    turnover_24h_usdt: Decimal | None
    spread_bps: Decimal | None
    instrument_max_leverage: Decimal
    instrument_quantity_step: Decimal
    instrument_min_notional: Decimal


class StrategyRepository:
    def __init__(
        self,
        *,
        signal_repository: SignalRepository | None = None,
        signal_policy: SignalPolicyConfig | None = None,
    ) -> None:
        self._signal_repository = signal_repository or SignalRepository()
        self._signal_policy = signal_policy or SignalPolicyConfig()

    async def list_active_profiles(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> list[StrategySymbolProfile]:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        UniverseMemberRecord.instrument_symbol,
                        UniverseMemberRecord.exchange_turnover_24h_usdt,
                        UniverseMemberRecord.spread_bps,
                        InstrumentRecord.max_leverage,
                        InstrumentRecord.quantity_step,
                        InstrumentRecord.min_notional_value,
                    )
                    .join(
                        UniverseSnapshotRecord,
                        UniverseSnapshotRecord.id == UniverseMemberRecord.snapshot_id,
                    )
                    .join(
                        InstrumentRecord,
                        InstrumentRecord.symbol == UniverseMemberRecord.instrument_symbol,
                    )
                    .where(
                        UniverseSnapshotRecord.is_active.is_(True),
                        UniverseMemberRecord.is_selected.is_(True),
                        UniverseMemberRecord.instrument_symbol.is_not(None),
                    )
                    .order_by(UniverseMemberRecord.market_cap_rank)
                )
            ).all()
        return [
            StrategySymbolProfile(
                symbol=symbol,
                turnover_24h_usdt=turnover,
                spread_bps=spread,
                instrument_max_leverage=max_leverage,
                instrument_quantity_step=quantity_step,
                instrument_min_notional=min_notional,
            )
            for symbol, turnover, spread, max_leverage, quantity_step, min_notional in rows
        ]

    async def load_candles(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
    ) -> tuple[Candle, ...]:
        async with session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(AggregatedCandleRecord)
                        .where(
                            AggregatedCandleRecord.symbol == symbol,
                            AggregatedCandleRecord.timeframe == timeframe,
                        )
                        .order_by(AggregatedCandleRecord.open_time.desc())
                        .limit(limit)
                    )
                ).all()
            )
        return tuple(
            Candle(
                symbol=record.symbol,
                timeframe=timeframe,
                open_time=record.open_time,
                close_time=record.close_time,
                open_price=record.open_price,
                high_price=record.high_price,
                low_price=record.low_price,
                close_price=record.close_price,
                volume=record.volume,
            )
            for record in reversed(records)
        )

    async def save_analysis(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        strategy_input: StrategyInput,
        candidates: tuple[SignalCandidate, ...],
        config: StrategyConfig,
    ) -> tuple[int, bool]:
        async with session_factory() as session, session.begin():
            version = await self._get_or_create_version(session, config)
            input_signature = self._input_signature(strategy_input, config.version)
            existing_id = await session.scalar(
                select(AnalysisSnapshotRecord.id).where(
                    AnalysisSnapshotRecord.symbol == strategy_input.symbol,
                    AnalysisSnapshotRecord.strategy_version_id == version.id,
                    AnalysisSnapshotRecord.input_signature == input_signature,
                )
            )
            if existing_id is not None:
                return existing_id, False
            snapshot = AnalysisSnapshotRecord(
                symbol=strategy_input.symbol,
                strategy_version_id=version.id,
                input_signature=input_signature,
                analyzed_at=strategy_input.analyzed_at,
                input_cutoffs={
                    timeframe: cutoff.isoformat()
                    for timeframe, cutoff in strategy_input.input_cutoffs
                },
                market_context=json_safe(asdict(strategy_input.market)),
                analyses={
                    "4h": json_safe(strategy_input.analysis_4h),
                    "1h": json_safe(strategy_input.analysis_1h),
                    "15m": json_safe(strategy_input.analysis_15m),
                    "5m": json_safe(strategy_input.analysis_5m),
                },
            )
            session.add(snapshot)
            await session.flush()

            candidate_records = [
                self._candidate_record(
                    candidate,
                    snapshot_id=snapshot.id,
                    strategy_version_id=version.id,
                )
                for candidate in candidates
            ]
            session.add_all(candidate_records)
            await session.flush()
            await self._signal_repository.publish_candidates(
                session,
                candidates=tuple(zip(candidate_records, candidates, strict=True)),
                config=self._signal_policy,
            )
        return snapshot.id, True

    async def list_candidates(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        filters: CandidateFilters,
    ) -> list[CandidateView]:
        statement = (
            select(SignalCandidateRecord, StrategyVersionRecord.version)
            .join(
                StrategyVersionRecord,
                StrategyVersionRecord.id == SignalCandidateRecord.strategy_version_id,
            )
            .order_by(SignalCandidateRecord.created_at.desc(), SignalCandidateRecord.id.desc())
            .limit(filters.limit)
        )
        if filters.symbol is not None:
            statement = statement.where(SignalCandidateRecord.symbol == filters.symbol.upper())
        if filters.direction is not None:
            statement = statement.where(SignalCandidateRecord.direction == filters.direction)
        if filters.status is not None:
            statement = statement.where(SignalCandidateRecord.status == filters.status)
        if filters.minimum_score is not None:
            statement = statement.where(SignalCandidateRecord.score >= filters.minimum_score)
        if filters.strategy_version is not None:
            statement = statement.where(StrategyVersionRecord.version == filters.strategy_version)
        if filters.created_from is not None:
            statement = statement.where(SignalCandidateRecord.created_at >= filters.created_from)
        if filters.created_to is not None:
            statement = statement.where(SignalCandidateRecord.created_at <= filters.created_to)

        async with session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [self._candidate_view(record, version) for record, version in rows]

    async def _get_or_create_version(
        self,
        session: AsyncSession,
        config: StrategyConfig,
    ) -> StrategyVersionRecord:
        parameters = config.parameter_snapshot()
        checksum = hashlib.sha256(
            json.dumps(
                parameters,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        frozen_version = await session.scalar(
            select(StrategyVersionRecord.version)
            .join(
                EvaluationWindowRecord,
                EvaluationWindowRecord.strategy_version_id == StrategyVersionRecord.id,
            )
            .where(EvaluationWindowRecord.status == "active")
        )
        if frozen_version is not None and frozen_version != config.version:
            raise ValueError(
                f"Strategy version is frozen at {frozen_version} by the active evaluation window"
            )

        existing = await session.scalar(
            select(StrategyVersionRecord).where(StrategyVersionRecord.version == config.version)
        )
        if existing is not None:
            if existing.parameter_checksum != checksum:
                raise ValueError(
                    f"Strategy version {config.version} already has different parameters"
                )
            if not existing.is_active:
                await session.execute(
                    update(StrategyVersionRecord)
                    .where(StrategyVersionRecord.is_active.is_(True))
                    .values(is_active=False)
                )
                existing.is_active = True
            return existing

        await session.execute(
            update(StrategyVersionRecord)
            .where(StrategyVersionRecord.is_active.is_(True))
            .values(is_active=False)
        )
        record = StrategyVersionRecord(
            version=config.version,
            parameter_checksum=checksum,
            parameters=parameters,
            is_active=True,
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    def _input_signature(
        strategy_input: StrategyInput,
        strategy_version: str,
    ) -> str:
        payload = {
            "symbol": strategy_input.symbol,
            "strategy_version": strategy_version,
            "cutoffs": [
                (timeframe, cutoff.isoformat())
                for timeframe, cutoff in strategy_input.input_cutoffs
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _candidate_record(
        candidate: SignalCandidate,
        *,
        snapshot_id: int,
        strategy_version_id: int,
    ) -> SignalCandidateRecord:
        plan = candidate.trade_plan
        return SignalCandidateRecord(
            analysis_snapshot_id=snapshot_id,
            strategy_version_id=strategy_version_id,
            symbol=candidate.symbol,
            direction=candidate.direction,
            status=candidate.status,
            score=candidate.score,
            strength=candidate.strength,
            entry_lower=plan.entry_lower if plan is not None else None,
            entry_upper=plan.entry_upper if plan is not None else None,
            planned_entry=plan.planned_entry if plan is not None else None,
            stop_loss=plan.stop_loss if plan is not None else None,
            take_profit_1=plan.take_profit_1 if plan is not None else None,
            take_profit_2=plan.take_profit_2 if plan is not None else None,
            gross_reward_to_risk=(plan.gross_reward_to_risk if plan is not None else None),
            net_reward_to_risk=plan.net_reward_to_risk if plan is not None else None,
            risk_amount=plan.risk_amount if plan is not None else None,
            quantity=plan.quantity if plan is not None else None,
            notional=plan.notional if plan is not None else None,
            recommended_leverage=(plan.recommended_leverage if plan is not None else None),
            estimated_margin=plan.estimated_margin if plan is not None else None,
            estimated_entry_fee=(plan.estimated_entry_fee if plan is not None else None),
            estimated_exit_fee=plan.estimated_exit_fee if plan is not None else None,
            estimated_loss_at_stop=(plan.estimated_loss_at_stop if plan is not None else None),
            invalidation=plan.invalidation if plan is not None else None,
            score_components=[json_safe(asdict(component)) for component in candidate.components],
            evidence=list(candidate.evidence),
            warnings=list(candidate.warnings),
            suppression_reasons=list(candidate.suppression_reasons),
            analyzed_at=candidate.analyzed_at,
            expires_at=candidate.expires_at,
        )

    @staticmethod
    def _candidate_view(
        record: SignalCandidateRecord,
        strategy_version: str,
    ) -> CandidateView:
        return CandidateView(
            id=record.id,
            analysis_snapshot_id=record.analysis_snapshot_id,
            symbol=record.symbol,
            direction=record.direction,
            status=record.status,
            score=record.score,
            strength=record.strength,
            strategy_version=strategy_version,
            entry_lower=record.entry_lower,
            entry_upper=record.entry_upper,
            planned_entry=record.planned_entry,
            stop_loss=record.stop_loss,
            take_profit_1=record.take_profit_1,
            take_profit_2=record.take_profit_2,
            gross_reward_to_risk=record.gross_reward_to_risk,
            net_reward_to_risk=record.net_reward_to_risk,
            risk_amount=record.risk_amount,
            quantity=record.quantity,
            notional=record.notional,
            recommended_leverage=record.recommended_leverage,
            estimated_margin=record.estimated_margin,
            estimated_loss_at_stop=record.estimated_loss_at_stop,
            invalidation=record.invalidation,
            score_components=record.score_components,
            evidence=record.evidence,
            warnings=record.warnings,
            suppression_reasons=record.suppression_reasons,
            analyzed_at=record.analyzed_at,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )
