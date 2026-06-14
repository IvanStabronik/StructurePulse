from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.market_data.backfill import (
    IncompleteKlineRangeError,
    MarketDataBackfillService,
)
from crypto_smc.providers.models import Candle1m


def candle(open_time: datetime) -> Candle1m:
    return Candle1m(
        symbol="BTCUSDT",
        open_time=open_time,
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100.5"),
        volume=Decimal("10"),
        turnover=Decimal("1000"),
    )


def test_last_closed_minute_excludes_current_candle() -> None:
    server_time = datetime(2026, 6, 14, 12, 34, 59, tzinfo=UTC)

    assert MarketDataBackfillService.last_closed_minute(server_time) == datetime(
        2026,
        6,
        14,
        12,
        33,
        tzinfo=UTC,
    )


def test_gap_is_not_created_for_single_incremental_candle() -> None:
    checkpoint = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)

    assert not MarketDataBackfillService._requires_gap(
        checkpoint_time=checkpoint,
        start_time=checkpoint + timedelta(minutes=1),
        end_time=checkpoint + timedelta(minutes=1),
    )


def test_gap_is_created_for_initial_or_multi_candle_recovery() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)

    assert MarketDataBackfillService._requires_gap(
        checkpoint_time=None,
        start_time=start,
        end_time=start,
    )
    assert MarketDataBackfillService._requires_gap(
        checkpoint_time=start - timedelta(minutes=1),
        start_time=start,
        end_time=start + timedelta(minutes=1),
    )


def test_validate_contiguous_accepts_complete_range() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    candles = [candle(start + timedelta(minutes=index)) for index in range(3)]

    MarketDataBackfillService._validate_contiguous(
        candles,
        start_time=start,
        end_time=start + timedelta(minutes=2),
    )


def test_validate_contiguous_rejects_missing_minute() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    candles = [candle(start), candle(start + timedelta(minutes=2))]

    with pytest.raises(IncompleteKlineRangeError):
        MarketDataBackfillService._validate_contiguous(
            candles,
            start_time=start,
            end_time=start + timedelta(minutes=2),
        )
