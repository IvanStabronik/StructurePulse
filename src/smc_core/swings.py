from collections.abc import Sequence

from smc_core.models import Candle, Swing


def detect_swings(
    candles: Sequence[Candle],
    *,
    lookback: int,
) -> tuple[Swing, ...]:
    if lookback < 1:
        raise ValueError("lookback must be positive")
    if len(candles) < lookback * 2 + 1:
        return ()

    swings: list[Swing] = []
    for index in range(lookback, len(candles) - lookback):
        candle = candles[index]
        neighbours = tuple(candles[index - lookback : index]) + tuple(
            candles[index + 1 : index + lookback + 1]
        )
        if all(candle.high_price > neighbour.high_price for neighbour in neighbours):
            swings.append(
                Swing(
                    kind="high",
                    index=index,
                    confirmation_index=index + lookback,
                    time=candle.open_time,
                    price=candle.high_price,
                )
            )
        if all(candle.low_price < neighbour.low_price for neighbour in neighbours):
            swings.append(
                Swing(
                    kind="low",
                    index=index,
                    confirmation_index=index + lookback,
                    time=candle.open_time,
                    price=candle.low_price,
                )
            )
    return tuple(
        sorted(
            swings,
            key=lambda swing: (swing.confirmation_index, swing.index, swing.kind),
        )
    )
