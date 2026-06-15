from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.aggregation.domain import AggregatedCandle, Timeframe, interval_end
from crypto_smc.db.models import (
    AggregatedCandleRecord,
    AggregationCursorRecord,
    AggregationJobRecord,
    Candle1mRecord,
)
from crypto_smc.db.repositories.market_data import MarketDataRepository
from crypto_smc.providers.models import Candle1m


@dataclass(frozen=True, slots=True)
class AggregationJob:
    id: int
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    priority: int
    attempts: int
    claimed_at: datetime


@dataclass(frozen=True, slots=True)
class AggregationSample:
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    candle: AggregatedCandle


class AggregationRepository:
    def __init__(self, market_data_repository: MarketDataRepository | None = None) -> None:
        self._market_data_repository = market_data_repository or MarketDataRepository()

    async def recover_stale_jobs(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stale_after_seconds: float,
    ) -> int:
        stale_before = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        async with session_factory() as session, session.begin():
            result = await session.execute(
                update(AggregationJobRecord)
                .where(
                    AggregationJobRecord.state == "processing",
                    AggregationJobRecord.updated_at < stale_before,
                )
                .values(
                    state="pending",
                    available_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def seed_next_batch(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        source_batch_size: int,
    ) -> int:
        async with session_factory() as session, session.begin():
            symbols = await self._market_data_repository.list_active_symbols(session)
            cursors = {
                cursor.symbol: cursor
                for cursor in (
                    await session.scalars(
                        select(AggregationCursorRecord).where(
                            AggregationCursorRecord.symbol.in_(symbols)
                        )
                    )
                ).all()
            }

            for symbol in symbols:
                cursor = cursors.get(symbol)
                statement = (
                    select(Candle1mRecord.open_time)
                    .where(Candle1mRecord.symbol == symbol)
                    .order_by(Candle1mRecord.open_time)
                    .limit(source_batch_size)
                )
                if cursor is not None and cursor.last_scanned_open_time is not None:
                    statement = statement.where(
                        Candle1mRecord.open_time > cursor.last_scanned_open_time
                    )
                open_times = list((await session.scalars(statement)).all())
                if not open_times:
                    continue

                placeholder_candles = [
                    Candle1m(
                        symbol=symbol,
                        open_time=open_time,
                        open_price=0,
                        high_price=0,
                        low_price=0,
                        close_price=0,
                        volume=0,
                        turnover=0,
                    )
                    for open_time in open_times
                ]
                await self._market_data_repository._enqueue_aggregation_jobs(
                    session,
                    candles=placeholder_candles,
                    priority=100,
                    completed_only=False,
                )
                cursor_statement = insert(AggregationCursorRecord).values(
                    symbol=symbol,
                    last_scanned_open_time=open_times[-1],
                    updated_at=datetime.now(UTC),
                )
                cursor_statement = cursor_statement.on_conflict_do_update(
                    index_elements=[AggregationCursorRecord.symbol],
                    set_={
                        "last_scanned_open_time": cursor_statement.excluded.last_scanned_open_time,
                        "updated_at": cursor_statement.excluded.updated_at,
                    },
                )
                await session.execute(cursor_statement)
                return len(open_times)
        return 0

    async def claim_jobs(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        limit: int,
    ) -> list[AggregationJob]:
        claimed_at = datetime.now(UTC)
        async with session_factory() as session, session.begin():
            records = list(
                (
                    await session.scalars(
                        select(AggregationJobRecord)
                        .where(
                            AggregationJobRecord.state == "pending",
                            AggregationJobRecord.available_at <= claimed_at,
                        )
                        .order_by(
                            AggregationJobRecord.priority,
                            AggregationJobRecord.open_time,
                            AggregationJobRecord.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for record in records:
                record.state = "processing"
                record.attempts += 1
                record.updated_at = claimed_at

        return [
            AggregationJob(
                id=record.id,
                symbol=record.symbol,
                timeframe=record.timeframe,  # type: ignore[arg-type]
                open_time=record.open_time,
                priority=record.priority,
                attempts=record.attempts,
                claimed_at=claimed_at,
            )
            for record in records
        ]

    async def load_source_candles(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        job: AggregationJob,
    ) -> list[Candle1m]:
        async with session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(Candle1mRecord)
                        .where(
                            Candle1mRecord.symbol == job.symbol,
                            Candle1mRecord.open_time >= job.open_time,
                            Candle1mRecord.open_time < interval_end(job.open_time, job.timeframe),
                        )
                        .order_by(Candle1mRecord.open_time)
                    )
                ).all()
            )
        return [
            Candle1m(
                symbol=record.symbol,
                open_time=record.open_time,
                open_price=record.open_price,
                high_price=record.high_price,
                low_price=record.low_price,
                close_price=record.close_price,
                volume=record.volume,
                turnover=record.turnover,
            )
            for record in records
        ]

    async def finish_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        job: AggregationJob,
        candle: AggregatedCandle | None,
    ) -> None:
        async with session_factory() as session, session.begin():
            if candle is None:
                await session.execute(
                    delete(AggregatedCandleRecord).where(
                        AggregatedCandleRecord.symbol == job.symbol,
                        AggregatedCandleRecord.timeframe == job.timeframe,
                        AggregatedCandleRecord.open_time == job.open_time,
                    )
                )
            else:
                statement = insert(AggregatedCandleRecord).values(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe,
                    open_time=candle.open_time,
                    close_time=candle.close_time,
                    open_price=candle.open_price,
                    high_price=candle.high_price,
                    low_price=candle.low_price,
                    close_price=candle.close_price,
                    volume=candle.volume,
                    turnover=candle.turnover,
                    source_candle_count=candle.source_candle_count,
                    updated_at=datetime.now(UTC),
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        AggregatedCandleRecord.symbol,
                        AggregatedCandleRecord.timeframe,
                        AggregatedCandleRecord.open_time,
                    ],
                    set_={
                        "close_time": statement.excluded.close_time,
                        "open_price": statement.excluded.open_price,
                        "high_price": statement.excluded.high_price,
                        "low_price": statement.excluded.low_price,
                        "close_price": statement.excluded.close_price,
                        "volume": statement.excluded.volume,
                        "turnover": statement.excluded.turnover,
                        "source_candle_count": statement.excluded.source_candle_count,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
                await session.execute(statement)

            await session.execute(
                delete(AggregationJobRecord).where(
                    AggregationJobRecord.id == job.id,
                    AggregationJobRecord.state == "processing",
                    AggregationJobRecord.updated_at == job.claimed_at,
                )
            )

    async def fail_job(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        job: AggregationJob,
        error: str,
        retry_delay_seconds: float,
    ) -> None:
        awaitable_at = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
        async with session_factory() as session, session.begin():
            await session.execute(
                update(AggregationJobRecord)
                .where(
                    AggregationJobRecord.id == job.id,
                    AggregationJobRecord.state == "processing",
                    AggregationJobRecord.updated_at == job.claimed_at,
                )
                .values(
                    state="pending",
                    last_error=error,
                    available_at=awaitable_at,
                    updated_at=datetime.now(UTC),
                )
            )

    async def queue_depth(self, session_factory: async_sessionmaker[AsyncSession]) -> int:
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(AggregationJobRecord)
                .where(AggregationJobRecord.state.in_(("pending", "processing")))
            )
        return count or 0

    async def list_reconciliation_samples(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        limit: int,
    ) -> list[AggregationSample]:
        async with session_factory() as session:
            symbols = await self._market_data_repository.list_active_symbols(session)
            records = list(
                (
                    await session.scalars(
                        select(AggregatedCandleRecord)
                        .where(AggregatedCandleRecord.symbol.in_(symbols))
                        .distinct(AggregatedCandleRecord.timeframe)
                        .order_by(
                            AggregatedCandleRecord.timeframe,
                            AggregatedCandleRecord.open_time.desc(),
                            AggregatedCandleRecord.symbol,
                        )
                        .limit(limit)
                    )
                ).all()
            )
        return [
            AggregationSample(
                symbol=record.symbol,
                timeframe=record.timeframe,  # type: ignore[arg-type]
                open_time=record.open_time,
                candle=AggregatedCandle(
                    symbol=record.symbol,
                    timeframe=record.timeframe,
                    open_time=record.open_time,
                    close_time=record.close_time,
                    open_price=record.open_price,
                    high_price=record.high_price,
                    low_price=record.low_price,
                    close_price=record.close_price,
                    volume=record.volume,
                    turnover=record.turnover,
                    source_candle_count=record.source_candle_count,
                ),
            )
            for record in records
        ]

    async def status_summary(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> dict[str, object]:
        async with session_factory() as session:
            aggregate_rows = (
                await session.execute(
                    select(
                        AggregatedCandleRecord.timeframe,
                        func.count(),
                        func.max(AggregatedCandleRecord.open_time),
                    )
                    .group_by(AggregatedCandleRecord.timeframe)
                    .order_by(AggregatedCandleRecord.timeframe)
                )
            ).all()
            job_rows = (
                await session.execute(
                    select(AggregationJobRecord.state, func.count())
                    .group_by(AggregationJobRecord.state)
                    .order_by(AggregationJobRecord.state)
                )
            ).all()
        return {
            "timeframes": {
                timeframe: {
                    "count": count,
                    "latest_open_time": latest_open_time,
                }
                for timeframe, count, latest_open_time in aggregate_rows
            },
            "jobs": {state: count for state, count in job_rows},
        }
