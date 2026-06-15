from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import AggregatedCandleRecord, Candle1mRecord


class MaintenanceRepository:
    async def delete_expired_candles(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        candle_1m_retention_days: int,
        candle_agg_retention_days: int,
        batch_size: int,
        now: datetime | None = None,
    ) -> dict[str, int]:
        current_time = now or datetime.now(UTC)
        deleted_1m = await self._delete_1m_batch(
            session_factory,
            cutoff=current_time - timedelta(days=candle_1m_retention_days),
            batch_size=batch_size,
        )
        deleted_agg = await self._delete_agg_batch(
            session_factory,
            cutoff=current_time - timedelta(days=candle_agg_retention_days),
            batch_size=batch_size,
        )
        return {"candles_1m": deleted_1m, "candles_agg": deleted_agg}

    @staticmethod
    async def _delete_1m_batch(
        session_factory: async_sessionmaker[AsyncSession],
        *,
        cutoff: datetime,
        batch_size: int,
    ) -> int:
        async with session_factory() as session, session.begin():
            keys = (
                await session.execute(
                    select(Candle1mRecord.symbol, Candle1mRecord.open_time)
                    .where(Candle1mRecord.open_time < cutoff)
                    .order_by(Candle1mRecord.open_time)
                    .limit(batch_size)
                )
            ).all()
            if not keys:
                return 0
            result = await session.execute(
                delete(Candle1mRecord)
                .where(tuple_(Candle1mRecord.symbol, Candle1mRecord.open_time).in_(keys))
                .returning(Candle1mRecord.symbol)
            )
            return len(result.scalars().all())

    @staticmethod
    async def _delete_agg_batch(
        session_factory: async_sessionmaker[AsyncSession],
        *,
        cutoff: datetime,
        batch_size: int,
    ) -> int:
        async with session_factory() as session, session.begin():
            keys = (
                await session.execute(
                    select(
                        AggregatedCandleRecord.symbol,
                        AggregatedCandleRecord.timeframe,
                        AggregatedCandleRecord.open_time,
                    )
                    .where(AggregatedCandleRecord.open_time < cutoff)
                    .order_by(AggregatedCandleRecord.open_time)
                    .limit(batch_size)
                )
            ).all()
            if not keys:
                return 0
            result = await session.execute(
                delete(AggregatedCandleRecord)
                .where(
                    tuple_(
                        AggregatedCandleRecord.symbol,
                        AggregatedCandleRecord.timeframe,
                        AggregatedCandleRecord.open_time,
                    ).in_(keys)
                )
                .returning(AggregatedCandleRecord.symbol)
            )
            return len(result.scalars().all())
