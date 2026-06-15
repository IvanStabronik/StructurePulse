from collections.abc import Sequence

from smc_core.models import BreakKind, Candle, Direction, StructureBreak, Swing


def detect_structure_breaks(
    candles: Sequence[Candle],
    swings: Sequence[Swing],
) -> tuple[StructureBreak, ...]:
    events: list[StructureBreak] = []
    trend: Direction | None = None
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

        direction: Direction | None = None
        target: Swing | None = None
        if latest_high is not None and candle.close_price > latest_high.price:
            direction = "bullish"
            target = latest_high
            latest_high = None
        elif latest_low is not None and candle.close_price < latest_low.price:
            direction = "bearish"
            target = latest_low
            latest_low = None
        if direction is None or target is None:
            continue

        kind: BreakKind = "choch" if trend is not None and trend != direction else "bos"
        events.append(
            StructureBreak(
                kind=kind,
                direction=direction,
                index=index,
                time=candle.close_time,
                close_price=candle.close_price,
                broken_swing=target,
                prior_trend=trend,
            )
        )
        trend = direction

    return tuple(events)
