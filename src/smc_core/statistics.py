from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise

from smc_core.models import Candle


def rolling_mean(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    if period < 1:
        raise ValueError("period must be positive")

    result: list[Decimal | None] = [None] * len(values)
    running_total = Decimal(0)
    for index, value in enumerate(values):
        running_total += value
        if index >= period:
            running_total -= values[index - period]
        if index >= period - 1:
            result[index] = running_total / Decimal(period)
    return tuple(result)


def true_ranges(candles: Sequence[Candle]) -> tuple[Decimal, ...]:
    if not candles:
        return ()

    ranges = [candles[0].range_size]
    for previous, candle in pairwise(candles):
        ranges.append(
            max(
                candle.range_size,
                abs(candle.high_price - previous.close_price),
                abs(candle.low_price - previous.close_price),
            )
        )
    return tuple(ranges)


def average_true_range(
    candles: Sequence[Candle],
    period: int = 14,
) -> tuple[Decimal | None, ...]:
    if period < 1:
        raise ValueError("period must be positive")
    if not candles:
        return ()

    ranges = true_ranges(candles)
    result: list[Decimal | None] = [None] * len(ranges)
    if len(ranges) < period:
        return tuple(result)

    current = sum(ranges[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * Decimal(period - 1) + ranges[index]) / Decimal(period)
        result[index] = current
    return tuple(result)
