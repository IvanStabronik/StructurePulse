from collections.abc import Sequence
from decimal import Decimal

from smc_core.models import Candle, Displacement
from smc_core.statistics import rolling_mean


def detect_displacements(
    candles: Sequence[Candle],
    atr_values: Sequence[Decimal | None],
    *,
    range_average_period: int,
    body_atr_ratio: Decimal,
    range_average_ratio: Decimal,
    close_fraction: Decimal,
) -> tuple[Displacement, ...]:
    average_ranges = rolling_mean(
        tuple(candle.range_size for candle in candles),
        range_average_period,
    )
    events: list[Displacement] = []

    for index, candle in enumerate(candles):
        atr = atr_values[index]
        average_range = average_ranges[index]
        direction = candle.direction
        if atr is None or average_range is None or direction is None or candle.range_size == 0:
            continue
        if candle.body_size < atr * body_atr_ratio:
            continue
        if candle.range_size < average_range * range_average_ratio:
            continue

        if direction == "bullish":
            close_position = (candle.close_price - candle.low_price) / candle.range_size
        else:
            close_position = (candle.high_price - candle.close_price) / candle.range_size
        if close_position < close_fraction:
            continue

        events.append(
            Displacement(
                direction=direction,
                index=index,
                time=candle.close_time,
                body_size=candle.body_size,
                range_size=candle.range_size,
                atr=atr,
                average_range=average_range,
            )
        )
    return tuple(events)
