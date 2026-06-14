from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.aggregation.domain import AggregatedCandle
from crypto_smc.aggregation.reconciliation import AggregationReconciliationService
from crypto_smc.db.repositories.aggregation import AggregationSample
from crypto_smc.providers.models import Candle1m


def source_candle(open_time: datetime, *, close: Decimal = Decimal("100.5")) -> Candle1m:
    return Candle1m(
        symbol="BTCUSDT",
        open_time=open_time,
        open_price=Decimal(100),
        high_price=Decimal(105),
        low_price=Decimal(99),
        close_price=close,
        volume=Decimal(10),
        turnover=Decimal(1000),
    )


def sample() -> AggregationSample:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    return AggregationSample(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=start,
        candle=AggregatedCandle(
            symbol="BTCUSDT",
            timeframe="5m",
            open_time=start,
            close_time=start + timedelta(minutes=5),
            open_price=Decimal(100),
            high_price=Decimal(105),
            low_price=Decimal(99),
            close_price=Decimal("100.5"),
            volume=Decimal(10),
            turnover=Decimal(1000),
            source_candle_count=5,
        ),
    )


class FakeProvider:
    def __init__(self, exchange: Candle1m) -> None:
        self.exchange = exchange
        self.repair_calls = 0

    async def get_klines(self, **_: object) -> list[Candle1m]:
        return [self.exchange]

    async def get_closed_1m_klines(self, **_: object) -> list[Candle1m]:
        self.repair_calls += 1
        return [self.exchange]


class FakeMarketDataRepository:
    def __init__(self) -> None:
        self.repaired: list[Candle1m] = []

    async def repair_candles(self, *, candles: list[Candle1m], **_: object) -> None:
        self.repaired = candles


def service(
    provider: FakeProvider,
    market_data_repository: FakeMarketDataRepository,
) -> AggregationReconciliationService:
    return AggregationReconciliationService(
        provider=provider,  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        interval_seconds=60,
        sample_size=1,
        market_data_repository=market_data_repository,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_matching_exchange_aggregate_does_not_repair_source() -> None:
    reconciliation_sample = sample()
    provider = FakeProvider(source_candle(reconciliation_sample.open_time))
    market_data_repository = FakeMarketDataRepository()

    result = await service(provider, market_data_repository)._reconcile_sample(
        reconciliation_sample
    )

    assert result == "matched"
    assert provider.repair_calls == 0
    assert market_data_repository.repaired == []


@pytest.mark.asyncio
async def test_mismatch_repairs_canonical_minutes() -> None:
    reconciliation_sample = sample()
    provider = FakeProvider(source_candle(reconciliation_sample.open_time, close=Decimal("101")))
    market_data_repository = FakeMarketDataRepository()

    result = await service(provider, market_data_repository)._reconcile_sample(
        reconciliation_sample
    )

    assert result == "repaired"
    assert provider.repair_calls == 1
    assert market_data_repository.repaired
