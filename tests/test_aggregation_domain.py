from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.aggregation import aggregate_candles, interval_end, interval_start
from crypto_smc.providers.models import Candle1m


def candle(index: int, *, start: datetime, symbol: str = "BTCUSDT") -> Candle1m:
    price = Decimal(100 + index)
    return Candle1m(
        symbol=symbol,
        open_time=start + timedelta(minutes=index),
        open_price=price,
        high_price=price + Decimal("2"),
        low_price=price - Decimal("1"),
        close_price=price + Decimal("0.5"),
        volume=Decimal(index + 1),
        turnover=Decimal((index + 1) * 1000),
    )


@pytest.mark.parametrize(
    ("value", "timeframe", "expected"),
    [
        (
            datetime(2026, 6, 14, 12, 7, 59, tzinfo=UTC),
            "5m",
            datetime(2026, 6, 14, 12, 5, tzinfo=UTC),
        ),
        (
            datetime(2026, 6, 14, 12, 59, tzinfo=UTC),
            "15m",
            datetime(2026, 6, 14, 12, 45, tzinfo=UTC),
        ),
        (
            datetime(2026, 6, 14, 13, 59, tzinfo=UTC),
            "1h",
            datetime(2026, 6, 14, 13, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 6, 14, 15, 59, tzinfo=UTC),
            "4h",
            datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
        ),
    ],
)
def test_interval_start_uses_utc_exchange_boundaries(
    value: datetime,
    timeframe: str,
    expected: datetime,
) -> None:
    assert interval_start(value, timeframe) == expected  # type: ignore[arg-type]


def test_interval_end_is_exclusive() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)

    assert interval_end(start, "15m") == datetime(2026, 6, 14, 12, 15, tzinfo=UTC)


def test_aggregate_candles_builds_exact_ohlcv() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)

    result = aggregate_candles(
        [candle(index, start=start) for index in range(5)],
        timeframe="5m",
    )

    assert result is not None
    assert result.open_price == 100
    assert result.high_price == 106
    assert result.low_price == 99
    assert result.close_price == Decimal("104.5")
    assert result.volume == 15
    assert result.turnover == 15_000
    assert result.source_candle_count == 5


def test_aggregate_candles_rejects_missing_minute() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    candles = [candle(index, start=start) for index in (0, 1, 3, 4, 5)]

    assert aggregate_candles(candles, timeframe="5m") is None


def test_aggregate_candles_rejects_mixed_symbols() -> None:
    start = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    candles = [candle(index, start=start) for index in range(5)]
    candles[-1] = candle(4, start=start, symbol="ETHUSDT")

    assert aggregate_candles(candles, timeframe="5m") is None
