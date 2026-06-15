from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smc_core import Candle, SMCConfig, analyze, average_true_range, detect_swings


def valid_candle(index: int, *, symbol: str = "BTCUSDT") -> Candle:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index * 5)
    return Candle(
        symbol=symbol,
        timeframe="5m",
        open_time=start,
        close_time=start + timedelta(minutes=5),
        open_price=Decimal(100),
        high_price=Decimal(102),
        low_price=Decimal(99),
        close_price=Decimal(101),
    )


def test_domain_models_are_immutable() -> None:
    item = valid_candle(0)

    with pytest.raises(FrozenInstanceError):
        item.close_price = Decimal(200)  # type: ignore[misc]


def test_candle_rejects_invalid_ohlc_and_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Candle(
            symbol="BTCUSDT",
            timeframe="5m",
            open_time=datetime(2026, 1, 1),
            close_time=datetime(2026, 1, 1, 0, 5),
            open_price=Decimal(100),
            high_price=Decimal(102),
            low_price=Decimal(99),
            close_price=Decimal(101),
        )
    with pytest.raises(ValueError, match="body cannot be above"):
        Candle(
            symbol="BTCUSDT",
            timeframe="5m",
            open_time=datetime(2026, 1, 1, tzinfo=UTC),
            close_time=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            open_price=Decimal(100),
            high_price=Decimal(101),
            low_price=Decimal(99),
            close_price=Decimal(102),
        )


def test_analysis_rejects_mixed_or_unsorted_series() -> None:
    with pytest.raises(ValueError, match="same symbol"):
        analyze((valid_candle(0), valid_candle(1, symbol="ETHUSDT")))
    with pytest.raises(ValueError, match="strictly ordered"):
        analyze((valid_candle(1), valid_candle(0)))


def test_invalid_parameters_fail_fast() -> None:
    with pytest.raises(ValueError, match="atr_period"):
        SMCConfig(atr_period=0)
    with pytest.raises(ValueError, match="period"):
        average_true_range((valid_candle(0),), 0)
    with pytest.raises(ValueError, match="lookback"):
        detect_swings((valid_candle(0),), lookback=0)
