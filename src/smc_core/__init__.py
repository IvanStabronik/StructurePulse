"""Pure synchronous SMC domain package.

This package must remain independent from exchange, database, web, Telegram,
and asyncio infrastructure.
"""

from smc_core.analysis import analyze
from smc_core.config import SMCConfig
from smc_core.displacement import detect_displacements
from smc_core.imbalances import detect_fair_value_gaps
from smc_core.liquidity import detect_equal_levels, detect_liquidity_sweeps
from smc_core.models import (
    Candle,
    DealingRange,
    Direction,
    Displacement,
    EqualLevel,
    FairValueGap,
    LiquiditySweep,
    OrderBlock,
    PriceLocation,
    SMCAnalysis,
    StructureBreak,
    Swing,
    Timeframe,
)
from smc_core.order_blocks import detect_order_blocks
from smc_core.ranges import active_dealing_range, classify_price
from smc_core.statistics import average_true_range, rolling_mean, true_ranges
from smc_core.structure import detect_structure_breaks
from smc_core.swings import detect_swings

__all__ = [
    "Candle",
    "DealingRange",
    "Direction",
    "Displacement",
    "EqualLevel",
    "FairValueGap",
    "LiquiditySweep",
    "OrderBlock",
    "PriceLocation",
    "SMCAnalysis",
    "SMCConfig",
    "StructureBreak",
    "Swing",
    "Timeframe",
    "active_dealing_range",
    "analyze",
    "average_true_range",
    "classify_price",
    "detect_displacements",
    "detect_equal_levels",
    "detect_fair_value_gaps",
    "detect_liquidity_sweeps",
    "detect_order_blocks",
    "detect_structure_breaks",
    "detect_swings",
    "rolling_mean",
    "true_ranges",
]
