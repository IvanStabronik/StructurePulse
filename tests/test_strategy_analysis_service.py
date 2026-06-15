import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.analysis.strategy_service import StrategyAnalysisService
from crypto_smc.db.repositories.strategy import StrategySymbolProfile
from crypto_smc.providers.models import MarketTicker
from smc_core import Candle, analyze


def candle_series(timeframe: str, minutes: int) -> tuple[Candle, ...]:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol="BTCUSDT",
            timeframe=timeframe,  # type: ignore[arg-type]
            open_time=start + timedelta(minutes=index * minutes),
            close_time=start + timedelta(minutes=(index + 1) * minutes),
            open_price=Decimal(100 + index % 4),
            high_price=Decimal(102 + index % 4),
            low_price=Decimal(99 + index % 4),
            close_price=Decimal(101 + index % 4),
            volume=Decimal(1000 + index),
        )
        for index in range(25)
    )


class FakeTickerProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def list_linear_tickers(self) -> dict[str, MarketTicker]:
        self.calls += 1
        return {
            "BTCUSDT": MarketTicker(
                symbol="BTCUSDT",
                last_price=Decimal(105),
                mark_price=Decimal(105),
                bid_price=Decimal("104.9"),
                ask_price=Decimal("105.1"),
                turnover_24h=Decimal(100_000_000),
                volume_24h=Decimal(1_000_000),
                open_interest=Decimal(10_000),
                open_interest_value=Decimal(1_050_000),
                funding_rate=Decimal("0.0001"),
            )
        }

    async def close(self) -> None:
        return None


class FakeProcessPool:
    async def analyze_batch(self, requests: object) -> tuple[object, ...]:
        return tuple(analyze(candles, config) for candles, config in requests)  # type: ignore[union-attr]


class FakeStrategyRepository:
    def __init__(self) -> None:
        self.save_calls = 0
        self.saved_input = None

    async def list_active_profiles(self, _: object) -> list[StrategySymbolProfile]:
        return [
            StrategySymbolProfile(
                symbol="BTCUSDT",
                turnover_24h_usdt=Decimal(100_000_000),
                spread_bps=Decimal(5),
                instrument_max_leverage=Decimal(100),
                instrument_quantity_step=Decimal("0.001"),
                instrument_min_notional=Decimal(5),
            )
        ]

    async def load_candles(
        self,
        _: object,
        *,
        timeframe: str,
        **__: object,
    ) -> tuple[Candle, ...]:
        minutes = {"4h": 240, "1h": 60, "15m": 15, "5m": 5}[timeframe]
        return candle_series(timeframe, minutes)

    async def save_analysis(self, **kwargs: object) -> tuple[int, bool]:
        self.save_calls += 1
        self.saved_input = kwargs["strategy_input"]
        return 1, self.save_calls == 1


@pytest.mark.asyncio
async def test_strategy_service_uses_closed_cutoffs_and_reports_duplicates() -> None:
    repository = FakeStrategyRepository()
    service = StrategyAnalysisService(
        ticker_provider=FakeTickerProvider(),
        session_factory=object(),  # type: ignore[arg-type]
        process_pool=FakeProcessPool(),  # type: ignore[arg-type]
        interval_seconds=60,
        history_candles=25,
        minimum_history_candles=20,
        repository=repository,  # type: ignore[arg-type]
    )

    first = await service.analyze_once()
    second = await service.analyze_once()

    assert first == {"created": 1, "duplicate": 0, "skipped": 0, "failed": 0}
    assert second == {"created": 0, "duplicate": 1, "skipped": 0, "failed": 0}
    assert repository.saved_input is not None
    assert len(repository.saved_input.input_cutoffs) == 4
    assert all(cutoff.tzinfo is not None for _, cutoff in repository.saved_input.input_cutoffs)


@pytest.mark.asyncio
async def test_strategy_run_waits_for_market_data_readiness() -> None:
    repository = FakeStrategyRepository()
    provider = FakeTickerProvider()
    readiness = asyncio.Event()
    service = StrategyAnalysisService(
        ticker_provider=provider,
        session_factory=object(),  # type: ignore[arg-type]
        process_pool=FakeProcessPool(),  # type: ignore[arg-type]
        interval_seconds=3600,
        history_candles=25,
        minimum_history_candles=20,
        readiness_event=readiness,
        repository=repository,  # type: ignore[arg-type]
    )
    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.02)
    assert provider.calls == 0

    readiness.set()
    for _ in range(100):
        if repository.save_calls:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.calls == 1
    assert repository.save_calls == 1
