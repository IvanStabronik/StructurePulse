"""Deterministic aggregation of canonical closed 1m candles."""

from crypto_smc.aggregation.domain import (
    TIMEFRAMES,
    AggregatedCandle,
    Timeframe,
    aggregate_candles,
    interval_end,
    interval_start,
)

__all__ = [
    "TIMEFRAMES",
    "AggregatedCandle",
    "Timeframe",
    "aggregate_candles",
    "interval_end",
    "interval_start",
]
