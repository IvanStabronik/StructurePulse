from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise

from smc_core.models import Candle, EqualLevel, LiquiditySweep, Swing, SwingKind


def detect_liquidity_sweeps(
    candles: Sequence[Candle],
    swings: Sequence[Swing],
) -> tuple[LiquiditySweep, ...]:
    events: list[LiquiditySweep] = []
    available: dict[int, list[Swing]] = {}
    for swing in swings:
        available.setdefault(swing.confirmation_index + 1, []).append(swing)

    latest_high: Swing | None = None
    latest_low: Swing | None = None
    for index, candle in enumerate(candles):
        for swing in available.get(index, ()):
            if swing.kind == "high":
                latest_high = swing
            else:
                latest_low = swing

        if latest_high is not None and candle.high_price > latest_high.price > candle.close_price:
            events.append(
                LiquiditySweep(
                    direction="bearish",
                    index=index,
                    time=candle.close_time,
                    level=latest_high.price,
                    extreme_price=candle.high_price,
                    swept_swing=latest_high,
                )
            )
            latest_high = None
        if latest_low is not None and candle.low_price < latest_low.price < candle.close_price:
            events.append(
                LiquiditySweep(
                    direction="bullish",
                    index=index,
                    time=candle.close_time,
                    level=latest_low.price,
                    extreme_price=candle.low_price,
                    swept_swing=latest_low,
                )
            )
            latest_low = None
    return tuple(events)


def detect_equal_levels(
    swings: Sequence[Swing],
    atr_values: Sequence[Decimal | None],
    *,
    tolerance_ratio: Decimal,
    max_separation: int,
) -> tuple[EqualLevel, ...]:
    if tolerance_ratio < 0:
        raise ValueError("tolerance_ratio cannot be negative")
    if max_separation < 1:
        raise ValueError("max_separation must be positive")

    levels: list[EqualLevel] = []
    for kind in ("high", "low"):
        swing_kind: SwingKind = kind
        candidates = sorted(
            (swing for swing in swings if swing.kind == swing_kind),
            key=lambda swing: swing.index,
        )
        for first, second in pairwise(candidates):
            if second.index - first.index > max_separation:
                continue
            if second.confirmation_index >= len(atr_values):
                continue
            atr = atr_values[second.confirmation_index]
            if atr is None:
                continue
            tolerance = atr * tolerance_ratio
            if abs(second.price - first.price) <= tolerance:
                levels.append(
                    EqualLevel(
                        kind=swing_kind,
                        first_swing=first,
                        second_swing=second,
                        price=(first.price + second.price) / Decimal(2),
                        tolerance=tolerance,
                    )
                )
    return tuple(sorted(levels, key=lambda level: level.second_swing.confirmation_index))
