import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.maintenance import MaintenanceRepository
from crypto_smc.observability.metrics import MAINTENANCE_DELETED_ROWS, MAINTENANCE_RUNS

logger = structlog.get_logger(__name__)


class MaintenanceService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        interval_seconds: float,
        candle_1m_retention_days: int,
        candle_agg_retention_days: int,
        batch_size: int,
        repository: MaintenanceRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._interval_seconds = interval_seconds
        self._candle_1m_retention_days = candle_1m_retention_days
        self._candle_agg_retention_days = candle_agg_retention_days
        self._batch_size = batch_size
        self._repository = repository or MaintenanceRepository()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self.run_once()
            except Exception:
                MAINTENANCE_RUNS.labels(outcome="failed").inc()
                await logger.aexception("maintenance_run_failed")

    async def run_once(self) -> dict[str, int]:
        deleted = await self._repository.delete_expired_candles(
            self._session_factory,
            candle_1m_retention_days=self._candle_1m_retention_days,
            candle_agg_retention_days=self._candle_agg_retention_days,
            batch_size=self._batch_size,
        )
        for table, count in deleted.items():
            if count:
                MAINTENANCE_DELETED_ROWS.labels(table=table).inc(count)
        MAINTENANCE_RUNS.labels(outcome="completed").inc()
        await logger.ainfo("maintenance_completed", **deleted)
        return deleted
