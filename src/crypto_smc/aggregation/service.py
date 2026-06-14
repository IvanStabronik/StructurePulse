import asyncio
from time import monotonic

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.aggregation.domain import aggregate_candles
from crypto_smc.db.repositories.aggregation import AggregationJob, AggregationRepository
from crypto_smc.observability.metrics import (
    AGGREGATION_JOB_DURATION,
    AGGREGATION_QUEUE_DEPTH,
    AGGREGATION_RESULTS,
)

logger = structlog.get_logger(__name__)


class AggregationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        job_batch_size: int,
        source_scan_batch_size: int,
        poll_interval_seconds: float,
        cpu_budget_ms: float,
        stale_job_seconds: float,
        repository: AggregationRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._job_batch_size = job_batch_size
        self._source_scan_batch_size = source_scan_batch_size
        self._poll_interval_seconds = poll_interval_seconds
        self._cpu_budget_seconds = cpu_budget_ms / 1000
        self._stale_job_seconds = stale_job_seconds
        self._repository = repository or AggregationRepository()

    async def run(self) -> None:
        recovered = await self._repository.recover_stale_jobs(
            self._session_factory,
            stale_after_seconds=self._stale_job_seconds,
        )
        if recovered:
            await logger.awarning("aggregation_stale_jobs_recovered", count=recovered)

        while True:
            await self._repository.seed_next_batch(
                self._session_factory,
                source_batch_size=self._source_scan_batch_size,
            )
            jobs = await self._repository.claim_jobs(
                self._session_factory,
                limit=self._job_batch_size,
            )
            AGGREGATION_QUEUE_DEPTH.set(await self._repository.queue_depth(self._session_factory))
            if not jobs:
                await asyncio.sleep(self._poll_interval_seconds)
                continue

            budget_started_at = monotonic()
            for job in jobs:
                await self._process_job(job)
                if monotonic() - budget_started_at >= self._cpu_budget_seconds:
                    await asyncio.sleep(0)
                    budget_started_at = monotonic()

    async def _process_job(self, job: AggregationJob) -> None:
        started_at = monotonic()
        try:
            source_candles = await self._repository.load_source_candles(
                self._session_factory,
                job=job,
            )
            aggregate = aggregate_candles(
                source_candles,
                timeframe=job.timeframe,
                expected_open_time=job.open_time,
            )
            await self._repository.finish_job(
                self._session_factory,
                job=job,
                candle=aggregate,
            )
        except Exception as exc:
            AGGREGATION_RESULTS.labels(
                timeframe=job.timeframe,
                result="failed",
            ).inc()
            await self._repository.fail_job(
                self._session_factory,
                job=job,
                error=f"{type(exc).__name__}: {exc}",
                retry_delay_seconds=min(60.0, 2 ** min(job.attempts, 5)),
            )
            await logger.aexception(
                "aggregation_job_failed",
                symbol=job.symbol,
                timeframe=job.timeframe,
                open_time=job.open_time,
            )
        else:
            AGGREGATION_RESULTS.labels(
                timeframe=job.timeframe,
                result="ready" if aggregate is not None else "withheld",
            ).inc()
        finally:
            AGGREGATION_JOB_DURATION.labels(timeframe=job.timeframe).observe(
                monotonic() - started_at
            )
