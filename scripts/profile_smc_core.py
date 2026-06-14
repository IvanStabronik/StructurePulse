from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from smc_core import Candle, SMCConfig, analyze


def fixture(size: int) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    price = Decimal(100)
    pattern = (
        Decimal(0),
        Decimal(1),
        Decimal(3),
        Decimal(7),
        Decimal(10),
        Decimal(6),
        Decimal(2),
        Decimal(-2),
        Decimal(-7),
        Decimal(-3),
    )
    for index in range(size):
        cycle, offset = divmod(index, len(pattern))
        close = Decimal(100) + Decimal(cycle) + pattern[offset]
        open_time = start + timedelta(minutes=index * 5)
        if close >= price:
            high = close + Decimal("0.5")
            low = price - Decimal("0.2")
        else:
            high = price + Decimal("0.2")
            low = close - Decimal("0.5")
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe="5m",
                open_time=open_time,
                close_time=open_time + timedelta(minutes=5),
                open_price=price,
                high_price=high,
                low_price=low,
                close_price=close,
                volume=Decimal(10_000 + index),
            )
        )
        price = close
    return tuple(candles)


def main() -> None:
    candles = fixture(10_000)
    started = perf_counter()
    result = analyze(candles, SMCConfig())
    elapsed = perf_counter() - started
    print(
        {
            "candles": len(candles),
            "seconds": round(elapsed, 4),
            "swings": len(result.swings),
            "structure_breaks": len(result.structure_breaks),
            "fair_value_gaps": len(result.fair_value_gaps),
        }
    )


if __name__ == "__main__":
    main()
