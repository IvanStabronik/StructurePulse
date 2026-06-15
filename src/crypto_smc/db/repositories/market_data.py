from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.aggregation.domain import (
    TIMEFRAMES,
    Timeframe,
    interval_end,
    interval_start,
)
from crypto_smc.db.models import (
    AggregationJobRecord,
    Candle1mRecord,
    DataCheckpointRecord,
    DataGapRecord,
    InstrumentRecord,
    UniverseMemberRecord,
    UniverseSnapshotRecord,
)
from crypto_smc.providers.models import Candle1m

LiveIngestResult = Literal["ready", "duplicate", "gap"]


@dataclass(frozen=True, slots=True)
class MarketDataTarget:
    symbol: str
    market_cap_rank: int
    launch_time: datetime


class MarketDataRepository:
    async def list_active_symbols(self, session: AsyncSession) -> tuple[str, ...]:
        targets = await self.list_active_targets(session)
        return tuple(target.symbol for target in targets)

    async def list_active_targets(
        self,
        session: AsyncSession,
    ) -> list[MarketDataTarget]:
        rows = (
            await session.execute(
                select(
                    UniverseMemberRecord.instrument_symbol,
                    UniverseMemberRecord.market_cap_rank,
                    InstrumentRecord.launch_time,
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
            MarketDataTarget(
                symbol=symbol,
                market_cap_rank=rank,
                launch_time=launch_time,
            )
            for symbol, rank, launch_time in rows
        ]

    async def get_checkpoints(
        self,
        session: AsyncSession,
        *,
        stream: str,
    ) -> dict[str, DataCheckpointRecord]:
        checkpoints = (
            await session.scalars(
                select(DataCheckpointRecord).where(DataCheckpointRecord.stream == stream)
            )
        ).all()
        return {checkpoint.symbol: checkpoint for checkpoint in checkpoints}

    async def start_gap(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        symbol: str,
        stream: str,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        async with session_factory() as session, session.begin():
            gap = DataGapRecord(
                symbol=symbol,
                stream=stream,
                start_time=start_time,
                end_time=end_time,
                status="recovering",
                attempts=1,
            )
            session.add(gap)
            await session.flush()
            await self._upsert_checkpoint(
                session,
                symbol=symbol,
                stream=stream,
                state="recovering",
                last_confirmed_open_time=None,
                last_error=None,
                preserve_last_confirmed=True,
            )
        return gap.id

    async def save_batch(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        symbol: str,
        stream: str,
        candles: list[Candle1m],
    ) -> None:
        if not candles:
            return
        async with session_factory() as session, session.begin():
            await self._ensure_partitions(session, candles)
            await self._upsert_candles(session, candles=candles, source="rest")
            await self._enqueue_aggregation_jobs(
                session,
                candles=candles,
                priority=100,
                completed_only=False,
            )
            await self._upsert_checkpoint(
                session,
                symbol=symbol,
                stream=stream,
                state="recovering",
                last_confirmed_open_time=candles[-1].open_time,
                last_error=None,
            )

    async def ingest_live_candle(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        stream: str,
        candle: Candle1m,
    ) -> LiveIngestResult:
        async with session_factory() as session, session.begin():
            checkpoint = await session.scalar(
                select(DataCheckpointRecord)
                .where(
                    DataCheckpointRecord.symbol == candle.symbol,
                    DataCheckpointRecord.stream == stream,
                )
                .with_for_update()
            )
            await self._ensure_partitions(session, [candle])
            await self._upsert_candles(session, candles=[candle], source="websocket")
            await self._enqueue_aggregation_jobs(
                session,
                candles=[candle],
                priority=0,
                completed_only=True,
            )

            last_confirmed = checkpoint.last_confirmed_open_time if checkpoint is not None else None
            if last_confirmed is None:
                await self._upsert_checkpoint(
                    session,
                    symbol=candle.symbol,
                    stream=stream,
                    state="degraded",
                    last_confirmed_open_time=None,
                    last_error="live candle received before recovery checkpoint",
                    preserve_last_confirmed=True,
                )
                return "gap"

            if candle.open_time <= last_confirmed:
                return "duplicate"

            expected_open_time = last_confirmed + timedelta(minutes=1)
            if candle.open_time == expected_open_time:
                await self._upsert_checkpoint(
                    session,
                    symbol=candle.symbol,
                    stream=stream,
                    state="ready",
                    last_confirmed_open_time=candle.open_time,
                    last_error=None,
                )
                return "ready"

            await self._upsert_checkpoint(
                session,
                symbol=candle.symbol,
                stream=stream,
                state="degraded",
                last_confirmed_open_time=None,
                last_error=(
                    f"missing candles from {expected_open_time.isoformat()} "
                    f"to {(candle.open_time - timedelta(minutes=1)).isoformat()}"
                ),
                preserve_last_confirmed=True,
            )
            return "gap"

    async def repair_candles(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        candles: list[Candle1m],
    ) -> None:
        if not candles:
            return
        async with session_factory() as session, session.begin():
            await self._ensure_partitions(session, candles)
            await self._upsert_candles(session, candles=candles, source="rest")
            await self._enqueue_aggregation_jobs(
                session,
                candles=candles,
                priority=10,
                completed_only=False,
            )

    async def mark_stream_state(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        symbols: tuple[str, ...],
        stream: str,
        state: str,
        error: str | None = None,
    ) -> None:
        if not symbols:
            return
        async with session_factory() as session, session.begin():
            for symbol in symbols:
                await self._upsert_checkpoint(
                    session,
                    symbol=symbol,
                    stream=stream,
                    state=state,
                    last_confirmed_open_time=None,
                    last_error=error,
                    preserve_last_confirmed=True,
                )

    async def mark_inactive_streams(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        active_symbols: tuple[str, ...],
        stream: str,
    ) -> None:
        statement = update(DataCheckpointRecord).where(DataCheckpointRecord.stream == stream)
        if active_symbols:
            statement = statement.where(DataCheckpointRecord.symbol.not_in(active_symbols))
        statement = statement.values(
            state="offline",
            last_error=None,
            updated_at=datetime.now(UTC),
        )
        async with session_factory() as session, session.begin():
            await session.execute(statement)

    async def complete_gap(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gap_id: int | None,
        symbol: str,
        stream: str,
        last_confirmed_open_time: datetime,
    ) -> None:
        async with session_factory() as session, session.begin():
            if gap_id is not None:
                gap = await session.get(DataGapRecord, gap_id)
                if gap is not None:
                    gap.status = "recovered"
                    gap.recovered_at = datetime.now(UTC)
                    gap.error = None
            await self._upsert_checkpoint(
                session,
                symbol=symbol,
                stream=stream,
                state="ready",
                last_confirmed_open_time=last_confirmed_open_time,
                last_error=None,
            )

    async def fail_gap(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gap_id: int | None,
        symbol: str,
        stream: str,
        error: str,
    ) -> None:
        async with session_factory() as session, session.begin():
            if gap_id is not None:
                gap = await session.get(DataGapRecord, gap_id)
                if gap is not None:
                    gap.status = "failed"
                    gap.error = error
            await self._upsert_checkpoint(
                session,
                symbol=symbol,
                stream=stream,
                state="degraded",
                last_confirmed_open_time=None,
                last_error=error,
                preserve_last_confirmed=True,
            )

    async def status_summary(self, session: AsyncSession) -> dict[str, object]:
        state_rows = (
            await session.execute(
                select(DataCheckpointRecord.state, func.count())
                .group_by(DataCheckpointRecord.state)
                .order_by(DataCheckpointRecord.state)
            )
        ).all()
        unresolved_gaps = await session.scalar(
            select(func.count())
            .select_from(DataGapRecord)
            .where(DataGapRecord.status.in_(("recovering", "failed")))
        )
        latest_candle = await session.scalar(select(func.max(Candle1mRecord.open_time)))
        candle_count = await session.scalar(select(func.count()).select_from(Candle1mRecord))
        return {
            "states": {state: count for state, count in state_rows},
            "unresolved_gaps": unresolved_gaps or 0,
            "latest_candle_open_time": latest_candle,
            "candle_count": candle_count or 0,
        }

    async def checkpoint_details(self, session: AsyncSession) -> list[dict[str, object]]:
        checkpoints = (
            await session.scalars(
                select(DataCheckpointRecord).order_by(DataCheckpointRecord.symbol)
            )
        ).all()
        return [
            {
                "symbol": checkpoint.symbol,
                "stream": checkpoint.stream,
                "state": checkpoint.state,
                "last_confirmed_open_time": checkpoint.last_confirmed_open_time,
                "last_error": checkpoint.last_error,
                "updated_at": checkpoint.updated_at,
            }
            for checkpoint in checkpoints
        ]

    async def load_reconciled_1m_window(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        symbol: str,
        start_open_time: datetime,
        end_open_time: datetime,
        stream: str = "kline_1m",
    ) -> tuple[Candle1m, ...]:
        if end_open_time < start_open_time:
            return ()
        async with session_factory() as session:
            checkpoint = await session.scalar(
                select(DataCheckpointRecord).where(
                    DataCheckpointRecord.symbol == symbol,
                    DataCheckpointRecord.stream == stream,
                )
            )
            if (
                checkpoint is None
                or checkpoint.state != "ready"
                or checkpoint.last_confirmed_open_time is None
                or checkpoint.last_confirmed_open_time < end_open_time
            ):
                return ()
            records = (
                await session.scalars(
                    select(Candle1mRecord)
                    .where(
                        Candle1mRecord.symbol == symbol,
                        Candle1mRecord.open_time >= start_open_time,
                        Candle1mRecord.open_time <= end_open_time,
                    )
                    .order_by(Candle1mRecord.open_time)
                )
            ).all()
        expected_count = int((end_open_time - start_open_time) / timedelta(minutes=1)) + 1
        if len(records) != expected_count:
            return ()
        for index, record in enumerate(records):
            if record.open_time != start_open_time + timedelta(minutes=index):
                return ()
        return tuple(
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
        )

    @staticmethod
    async def _upsert_checkpoint(
        session: AsyncSession,
        *,
        symbol: str,
        stream: str,
        state: str,
        last_confirmed_open_time: datetime | None,
        last_error: str | None,
        preserve_last_confirmed: bool = False,
    ) -> None:
        values = {
            "symbol": symbol,
            "stream": stream,
            "state": state,
            "last_confirmed_open_time": last_confirmed_open_time,
            "last_error": last_error,
            "updated_at": datetime.now(UTC),
        }
        statement = insert(DataCheckpointRecord).values(**values)
        update_values: dict[str, Any] = {
            "state": statement.excluded.state,
            "last_error": statement.excluded.last_error,
            "updated_at": statement.excluded.updated_at,
        }
        if not preserve_last_confirmed:
            update_values["last_confirmed_open_time"] = case(
                (
                    DataCheckpointRecord.last_confirmed_open_time.is_(None),
                    statement.excluded.last_confirmed_open_time,
                ),
                (
                    statement.excluded.last_confirmed_open_time.is_(None),
                    DataCheckpointRecord.last_confirmed_open_time,
                ),
                else_=func.greatest(
                    DataCheckpointRecord.last_confirmed_open_time,
                    statement.excluded.last_confirmed_open_time,
                ),
            )
        statement = statement.on_conflict_do_update(
            constraint="uq_data_checkpoints_symbol_stream",
            set_=update_values,
        )
        await session.execute(statement)

    @staticmethod
    async def _upsert_candles(
        session: AsyncSession,
        *,
        candles: list[Candle1m],
        source: str,
    ) -> None:
        values = [
            {
                "symbol": candle.symbol,
                "open_time": candle.open_time,
                "open_price": candle.open_price,
                "high_price": candle.high_price,
                "low_price": candle.low_price,
                "close_price": candle.close_price,
                "volume": candle.volume,
                "turnover": candle.turnover,
                "source": source,
                "updated_at": datetime.now(UTC),
            }
            for candle in candles
        ]
        statement = insert(Candle1mRecord).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[Candle1mRecord.symbol, Candle1mRecord.open_time],
            set_={
                "open_price": statement.excluded.open_price,
                "high_price": statement.excluded.high_price,
                "low_price": statement.excluded.low_price,
                "close_price": statement.excluded.close_price,
                "volume": statement.excluded.volume,
                "turnover": statement.excluded.turnover,
                "source": statement.excluded.source,
                "updated_at": statement.excluded.updated_at,
            },
        )
        await session.execute(statement)

    @staticmethod
    async def _enqueue_aggregation_jobs(
        session: AsyncSession,
        *,
        candles: list[Candle1m],
        priority: int,
        completed_only: bool,
    ) -> None:
        jobs: set[tuple[str, Timeframe, datetime]] = set()
        for candle in candles:
            for timeframe in TIMEFRAMES:
                aggregate_open_time = interval_start(candle.open_time, timeframe)
                is_interval_complete = candle.open_time + timedelta(minutes=1) == interval_end(
                    aggregate_open_time, timeframe
                )
                if completed_only and not is_interval_complete:
                    continue
                jobs.add((candle.symbol, timeframe, aggregate_open_time))

        now = datetime.now(UTC)
        for symbol, timeframe, open_time in sorted(jobs):
            statement = insert(AggregationJobRecord).values(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                priority=priority,
                state="pending",
                attempts=0,
                last_error=None,
                available_at=now,
                updated_at=now,
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_aggregation_jobs_interval",
                set_={
                    "priority": func.least(AggregationJobRecord.priority, priority),
                    "state": "pending",
                    "last_error": None,
                    "available_at": now,
                    "updated_at": now,
                },
            )
            await session.execute(statement)

    @staticmethod
    async def _ensure_partitions(
        session: AsyncSession,
        candles: list[Candle1m],
    ) -> None:
        months = {(candle.open_time.year, candle.open_time.month) for candle in candles}
        for year, month in sorted(months):
            start = datetime(year, month, 1, tzinfo=UTC)
            if month == 12:
                end = datetime(year + 1, 1, 1, tzinfo=UTC)
            else:
                end = datetime(year, month + 1, 1, tzinfo=UTC)
            partition_name = f"candles_1m_{year}_{month:02d}"
            await session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {partition_name} "
                    "PARTITION OF candles_1m "
                    f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                )
            )
