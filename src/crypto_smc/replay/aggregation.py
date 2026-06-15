from collections import defaultdict
from datetime import datetime

from crypto_smc.aggregation.domain import (
    TIMEFRAMES,
    Timeframe,
    aggregate_candles,
    interval_start,
)
from crypto_smc.providers.models import Candle1m
from smc_core import Candle


def build_replay_aggregates(
    candles: tuple[Candle1m, ...],
) -> dict[str, dict[Timeframe, tuple[Candle, ...]]]:
    by_symbol: dict[str, list[Candle1m]] = defaultdict(list)
    for candle in candles:
        by_symbol[candle.symbol].append(candle)

    return {
        symbol: {
            timeframe: _aggregate_timeframe(tuple(symbol_candles), timeframe)
            for timeframe in TIMEFRAMES
        }
        for symbol, symbol_candles in sorted(by_symbol.items())
    }


def _aggregate_timeframe(
    candles: tuple[Candle1m, ...],
    timeframe: Timeframe,
) -> tuple[Candle, ...]:
    buckets: dict[datetime, list[Candle1m]] = defaultdict(list)
    for candle in candles:
        buckets[interval_start(candle.open_time, timeframe)].append(candle)

    aggregates: list[Candle] = []
    for bucket_start in sorted(buckets):
        aggregate = aggregate_candles(
            buckets[bucket_start],
            timeframe=timeframe,
            expected_open_time=bucket_start,
        )
        if aggregate is None:
            continue
        aggregates.append(
            Candle(
                symbol=aggregate.symbol,
                timeframe=timeframe,
                open_time=aggregate.open_time,
                close_time=aggregate.close_time,
                open_price=aggregate.open_price,
                high_price=aggregate.high_price,
                low_price=aggregate.low_price,
                close_price=aggregate.close_price,
                volume=aggregate.volume,
            )
        )
    return tuple(aggregates)
