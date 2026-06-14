from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from smc_core.models import Candle, Direction, FairValueGap


def detect_fair_value_gaps(
    candles: Sequence[Candle],
    atr_values: Sequence[Decimal | None],
    *,
    min_atr_ratio: Decimal,
) -> tuple[FairValueGap, ...]:
    if min_atr_ratio < 0:
        raise ValueError("min_atr_ratio cannot be negative")

    gaps: list[FairValueGap] = []
    for index in range(2, len(candles)):
        first = candles[index - 2]
        third = candles[index]
        atr = atr_values[index]
        if atr is None:
            continue

        if third.low_price > first.high_price:
            lower = first.high_price
            upper = third.low_price
            direction: Direction = "bullish"
        elif third.high_price < first.low_price:
            lower = third.high_price
            upper = first.low_price
            direction = "bearish"
        else:
            continue
        if upper - lower < atr * min_atr_ratio:
            continue

        gap = FairValueGap(
            direction=direction,
            start_index=index - 2,
            created_index=index,
            created_at=third.close_time,
            lower_price=lower,
            upper_price=upper,
            status="open",
        )
        gaps.append(_resolve_gap(gap, candles))
    return tuple(gaps)


def _resolve_gap(gap: FairValueGap, candles: Sequence[Candle]) -> FairValueGap:
    status = gap.status
    first_touch: int | None = None
    resolved: int | None = None

    for index in range(gap.created_index + 1, len(candles)):
        candle = candles[index]
        if gap.direction == "bullish":
            if candle.low_price <= gap.lower_price:
                first_touch = first_touch if first_touch is not None else index
                status = "filled"
                resolved = index
                break
            if candle.low_price < gap.upper_price:
                first_touch = first_touch if first_touch is not None else index
                status = "partially_filled"
        else:
            if candle.high_price >= gap.upper_price:
                first_touch = first_touch if first_touch is not None else index
                status = "filled"
                resolved = index
                break
            if candle.high_price > gap.lower_price:
                first_touch = first_touch if first_touch is not None else index
                status = "partially_filled"

    return replace(
        gap,
        status=status,
        first_touch_index=first_touch,
        resolved_index=resolved,
    )
