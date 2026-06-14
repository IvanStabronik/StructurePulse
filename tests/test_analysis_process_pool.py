from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.analysis import AnalysisProcessPool
from smc_core import Candle, SMCConfig


def candles() -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    prices = (
        ("100", "102", "99", "101"),
        ("101", "106", "100", "105"),
        ("105", "105", "97", "98"),
        ("98", "104", "98", "103"),
        ("103", "110", "102", "109"),
    )
    return tuple(
        Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            open_time=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1),
            open_price=Decimal(open_price),
            high_price=Decimal(high_price),
            low_price=Decimal(low_price),
            close_price=Decimal(close_price),
        )
        for index, (open_price, high_price, low_price, close_price) in enumerate(prices)
    )


@pytest.mark.asyncio
async def test_process_pool_analyzes_picklable_domain_requests() -> None:
    config = SMCConfig(atr_period=2, range_average_period=2)

    async with AnalysisProcessPool(max_workers=1, max_pending_batches=1) as pool:
        result = await pool.analyze_batch(((candles(), config),))

    assert len(result) == 1
    assert result[0].symbol == "BTCUSDT"
    assert result[0].timeframe == "1h"


@pytest.mark.asyncio
async def test_process_pool_returns_empty_batch_without_starting_work() -> None:
    async with AnalysisProcessPool(max_workers=1, max_pending_batches=1) as pool:
        assert await pool.analyze_batch(()) == ()
