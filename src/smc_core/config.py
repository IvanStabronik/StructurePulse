from dataclasses import dataclass
from decimal import Decimal

from smc_core.models import Timeframe


@dataclass(frozen=True, slots=True)
class SMCConfig:
    atr_period: int = 14
    range_average_period: int = 20
    swing_lookback_5m: int = 3
    swing_lookback_15m: int = 3
    swing_lookback_1h: int = 1
    swing_lookback_4h: int = 1
    equal_level_atr_tolerance: Decimal = Decimal("0.10")
    equal_level_max_separation: int = 100
    fvg_min_atr_ratio: Decimal = Decimal("0.10")
    displacement_body_atr_ratio: Decimal = Decimal("1.0")
    displacement_range_average_ratio: Decimal = Decimal("1.5")
    displacement_close_fraction: Decimal = Decimal("0.70")
    order_block_search_lookback: int = 20

    def __post_init__(self) -> None:
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive")
        if self.range_average_period < 1:
            raise ValueError("range_average_period must be positive")
        if any(
            value < 1
            for value in (
                self.swing_lookback_5m,
                self.swing_lookback_15m,
                self.swing_lookback_1h,
                self.swing_lookback_4h,
            )
        ):
            raise ValueError("swing lookbacks must be positive")
        if self.equal_level_atr_tolerance < 0:
            raise ValueError("equal_level_atr_tolerance cannot be negative")
        if self.equal_level_max_separation < 1:
            raise ValueError("equal_level_max_separation must be positive")
        if self.fvg_min_atr_ratio < 0:
            raise ValueError("fvg_min_atr_ratio cannot be negative")
        if self.displacement_body_atr_ratio < 0:
            raise ValueError("displacement_body_atr_ratio cannot be negative")
        if self.displacement_range_average_ratio < 0:
            raise ValueError("displacement_range_average_ratio cannot be negative")
        if not Decimal(0) <= self.displacement_close_fraction <= Decimal(1):
            raise ValueError("displacement_close_fraction must be between zero and one")
        if self.order_block_search_lookback < 1:
            raise ValueError("order_block_search_lookback must be positive")

    def swing_lookback(self, timeframe: Timeframe) -> int:
        return {
            "5m": self.swing_lookback_5m,
            "15m": self.swing_lookback_15m,
            "1h": self.swing_lookback_1h,
            "4h": self.swing_lookback_4h,
        }[timeframe]
