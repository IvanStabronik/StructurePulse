from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

type Timeframe = Literal["5m", "15m", "1h", "4h"]
type Direction = Literal["bullish", "bearish"]
type SwingKind = Literal["high", "low"]
type BreakKind = Literal["bos", "choch"]
type ZoneStatus = Literal["open", "partially_filled", "filled", "invalidated"]
type PriceLocation = Literal["discount", "equilibrium", "premium"]


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None or self.close_time.tzinfo is None:
            raise ValueError("Candle timestamps must be timezone-aware")
        if self.close_time <= self.open_time:
            raise ValueError("Candle close_time must be after open_time")
        if self.low_price > self.high_price:
            raise ValueError("Candle low_price cannot exceed high_price")
        if not self.low_price <= min(self.open_price, self.close_price):
            raise ValueError("Candle body cannot be below low_price")
        if not self.high_price >= max(self.open_price, self.close_price):
            raise ValueError("Candle body cannot be above high_price")
        if self.volume < 0:
            raise ValueError("Candle volume cannot be negative")

    @property
    def body_size(self) -> Decimal:
        return abs(self.close_price - self.open_price)

    @property
    def range_size(self) -> Decimal:
        return self.high_price - self.low_price

    @property
    def direction(self) -> Direction | None:
        if self.close_price > self.open_price:
            return "bullish"
        if self.close_price < self.open_price:
            return "bearish"
        return None


@dataclass(frozen=True, slots=True)
class Swing:
    kind: SwingKind
    index: int
    confirmation_index: int
    time: datetime
    price: Decimal


@dataclass(frozen=True, slots=True)
class StructureBreak:
    kind: BreakKind
    direction: Direction
    index: int
    time: datetime
    close_price: Decimal
    broken_swing: Swing
    prior_trend: Direction | None


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    direction: Direction
    index: int
    time: datetime
    level: Decimal
    extreme_price: Decimal
    swept_swing: Swing


@dataclass(frozen=True, slots=True)
class EqualLevel:
    kind: SwingKind
    first_swing: Swing
    second_swing: Swing
    price: Decimal
    tolerance: Decimal


@dataclass(frozen=True, slots=True)
class Displacement:
    direction: Direction
    index: int
    time: datetime
    body_size: Decimal
    range_size: Decimal
    atr: Decimal
    average_range: Decimal


@dataclass(frozen=True, slots=True)
class FairValueGap:
    direction: Direction
    start_index: int
    created_index: int
    created_at: datetime
    lower_price: Decimal
    upper_price: Decimal
    status: ZoneStatus
    first_touch_index: int | None = None
    resolved_index: int | None = None

    @property
    def size(self) -> Decimal:
        return self.upper_price - self.lower_price


@dataclass(frozen=True, slots=True)
class OrderBlock:
    direction: Direction
    candle_index: int
    created_index: int
    created_at: datetime
    lower_price: Decimal
    upper_price: Decimal
    break_event: StructureBreak
    status: ZoneStatus
    first_touch_index: int | None = None
    invalidated_index: int | None = None


@dataclass(frozen=True, slots=True)
class DealingRange:
    low_swing: Swing
    high_swing: Swing
    low_price: Decimal
    high_price: Decimal
    midpoint: Decimal


@dataclass(frozen=True, slots=True)
class SMCAnalysis:
    symbol: str
    timeframe: Timeframe
    candle_count: int
    atr: tuple[Decimal | None, ...]
    swings: tuple[Swing, ...]
    structure_breaks: tuple[StructureBreak, ...]
    liquidity_sweeps: tuple[LiquiditySweep, ...]
    equal_levels: tuple[EqualLevel, ...]
    displacements: tuple[Displacement, ...]
    fair_value_gaps: tuple[FairValueGap, ...]
    order_blocks: tuple[OrderBlock, ...]
    dealing_range: DealingRange | None
    price_location: PriceLocation | None
