import asyncio
from datetime import timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.aggregation.domain import TIMEFRAME_MINUTES, Timeframe, interval_end
from crypto_smc.db.repositories.aggregation import AggregationRepository, AggregationSample
from crypto_smc.db.repositories.market_data import MarketDataRepository
from crypto_smc.observability.metrics import AGGREGATION_RECONCILIATIONS
from crypto_smc.providers.models import Candle1m
from crypto_smc.providers.protocols import KlineProvider

logger = structlog.get_logger(__name__)

BYBIT_INTERVALS: dict[Timeframe, str] = {
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
}


class AggregationReconciliationService:
    def __init__(
        self,
        *,
        provider: KlineProvider,
        session_factory: async_sessionmaker[AsyncSession],
        interval_seconds: float,
        sample_size: int,
        repository: AggregationRepository | None = None,
        market_data_repository: MarketDataRepository | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._interval_seconds = interval_seconds
        self._sample_size = sample_size
        self._repository = repository or AggregationRepository()
        self._market_data_repository = market_data_repository or MarketDataRepository()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.reconcile_once()

    async def reconcile_once(self) -> dict[str, int]:
        samples = await self._repository.list_reconciliation_samples(
            self._session_factory,
            limit=self._sample_size,
        )

        results = {"matched": 0, "repaired": 0, "failed": 0}
        for sample in samples:
            try:
                outcome = await self._reconcile_sample(sample)
            except Exception:
                results["failed"] += 1
                AGGREGATION_RECONCILIATIONS.labels(
                    timeframe=sample.timeframe,
                    result="failed",
                ).inc()
                await logger.aexception(
                    "aggregation_reconciliation_failed",
                    symbol=sample.symbol,
                    timeframe=sample.timeframe,
                    open_time=sample.open_time,
                )
            else:
                results[outcome] += 1
                AGGREGATION_RECONCILIATIONS.labels(
                    timeframe=sample.timeframe,
                    result=outcome,
                ).inc()
        return results

    async def _reconcile_sample(
        self,
        sample: AggregationSample,
    ) -> str:
        exchange = await self._provider.get_klines(
            symbol=sample.symbol,
            interval=BYBIT_INTERVALS[sample.timeframe],
            start_time=sample.open_time,
            end_time=sample.open_time,
            limit=1,
        )
        if exchange and self._matches(sample, exchange[0]):
            return "matched"

        repaired = await self._provider.get_closed_1m_klines(
            symbol=sample.symbol,
            start_time=sample.open_time,
            end_time=interval_end(sample.open_time, sample.timeframe) - timedelta(minutes=1),
            limit=TIMEFRAME_MINUTES[sample.timeframe],
        )
        await self._market_data_repository.repair_candles(
            session_factory=self._session_factory,
            candles=repaired,
        )
        return "repaired"

    @staticmethod
    def _matches(sample: AggregationSample, exchange: Candle1m) -> bool:
        return all(
            (
                sample.candle.open_price == exchange.open_price,
                sample.candle.high_price == exchange.high_price,
                sample.candle.low_price == exchange.low_price,
                sample.candle.close_price == exchange.close_price,
                sample.candle.volume == exchange.volume,
                sample.candle.turnover == exchange.turnover,
            )
        )
