from collections.abc import Sequence
from itertools import pairwise

from smc_core.models import Candle


def validate_candle_series(candles: Sequence[Candle]) -> None:
    if not candles:
        raise ValueError("At least one candle is required")

    first = candles[0]
    for previous, candle in pairwise(candles):
        if candle.symbol != first.symbol:
            raise ValueError("All candles must have the same symbol")
        if candle.timeframe != first.timeframe:
            raise ValueError("All candles must have the same timeframe")
        if candle.open_time <= previous.open_time:
            raise ValueError("Candles must be strictly ordered by open_time")
