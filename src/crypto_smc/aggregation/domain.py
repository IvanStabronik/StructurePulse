from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crypto_smc.providers.models import Candle1m

type Timeframe = Literal["5m", "15m", "1h", "4h"]

TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
}
TIMEFRAMES: tuple[Timeframe, ...] = ("5m", "15m", "1h", "4h")
ONE_MINUTE = timedelta(minutes=1)


class AggregatedCandle(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal
    source_candle_count: int


def interval_start(value: datetime, timeframe: Timeframe) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Candle timestamps must be timezone-aware")
    utc_value = value.astimezone(UTC)
    minute_index = int(utc_value.timestamp()) // 60
    duration = TIMEFRAME_MINUTES[timeframe]
    start_index = minute_index - minute_index % duration
    return datetime.fromtimestamp(start_index * 60, tz=UTC)


def interval_end(start: datetime, timeframe: Timeframe) -> datetime:
    return interval_start(start, timeframe) + timedelta(minutes=TIMEFRAME_MINUTES[timeframe])


def aggregate_candles(
    candles: list[Candle1m],
    *,
    timeframe: Timeframe,
    expected_open_time: datetime | None = None,
) -> AggregatedCandle | None:
    if not candles:
        return None

    ordered = sorted(candles, key=lambda candle: candle.open_time)
    start = interval_start(ordered[0].open_time, timeframe)
    if expected_open_time is not None and start != interval_start(expected_open_time, timeframe):
        return None

    expected_count = TIMEFRAME_MINUTES[timeframe]
    if len(ordered) != expected_count:
        return None
    if any(candle.symbol != ordered[0].symbol for candle in ordered):
        return None

    for index, candle in enumerate(ordered):
        if candle.open_time != start + ONE_MINUTE * index:
            return None

    return AggregatedCandle(
        symbol=ordered[0].symbol,
        timeframe=timeframe,
        open_time=start,
        close_time=interval_end(start, timeframe),
        open_price=ordered[0].open_price,
        high_price=max(candle.high_price for candle in ordered),
        low_price=min(candle.low_price for candle in ordered),
        close_price=ordered[-1].close_price,
        volume=sum((candle.volume for candle in ordered), Decimal(0)),
        turnover=sum((candle.turnover for candle in ordered), Decimal(0)),
        source_candle_count=expected_count,
    )
