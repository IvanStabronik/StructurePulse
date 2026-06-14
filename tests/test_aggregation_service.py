from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.aggregation.service import AggregationService
from crypto_smc.db.repositories.aggregation import AggregationJob
from crypto_smc.providers.models import Candle1m


class FakeAggregationRepository:
    def __init__(self, candles: list[Candle1m]) -> None:
        self.candles = candles
        self.finished = None
        self.failed = False

    async def load_source_candles(self, *_: object, **__: object) -> list[Candle1m]:
        return self.candles

    async def finish_job(self, *_: object, candle: object, **__: object) -> None:
        self.finished = candle

    async def fail_job(self, *_: object, **__: object) -> None:
        self.failed = True


def candles(count: int) -> list[Candle1m]:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    return [
        Candle1m(
            symbol="BTCUSDT",
            open_time=start + timedelta(minutes=index),
            open_price=Decimal(100 + index),
            high_price=Decimal(101 + index),
            low_price=Decimal(99 + index),
            close_price=Decimal("100.5") + index,
            volume=Decimal(1),
            turnover=Decimal(100),
        )
        for index in range(count)
    ]


def job() -> AggregationJob:
    return AggregationJob(
        id=1,
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
        priority=0,
        attempts=1,
        claimed_at=datetime.now(UTC),
    )


def service(repository: FakeAggregationRepository) -> AggregationService:
    return AggregationService(
        session_factory=object(),  # type: ignore[arg-type]
        job_batch_size=10,
        source_scan_batch_size=100,
        poll_interval_seconds=0.1,
        cpu_budget_ms=10,
        stale_job_seconds=60,
        repository=repository,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_complete_job_persists_aggregate() -> None:
    repository = FakeAggregationRepository(candles(5))

    await service(repository)._process_job(job())

    assert repository.finished is not None
    assert repository.finished.close_price == Decimal("104.5")
    assert not repository.failed


@pytest.mark.asyncio
async def test_incomplete_job_withholds_aggregate() -> None:
    repository = FakeAggregationRepository(candles(4))

    await service(repository)._process_job(job())

    assert repository.finished is None
    assert not repository.failed
