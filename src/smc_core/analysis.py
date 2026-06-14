from collections.abc import Sequence

from smc_core.config import SMCConfig
from smc_core.displacement import detect_displacements
from smc_core.imbalances import detect_fair_value_gaps
from smc_core.liquidity import detect_equal_levels, detect_liquidity_sweeps
from smc_core.models import Candle, SMCAnalysis
from smc_core.order_blocks import detect_order_blocks
from smc_core.ranges import active_dealing_range, classify_price
from smc_core.statistics import average_true_range
from smc_core.structure import detect_structure_breaks
from smc_core.swings import detect_swings
from smc_core.validation import validate_candle_series


def analyze(
    candles: Sequence[Candle],
    config: SMCConfig | None = None,
) -> SMCAnalysis:
    validate_candle_series(candles)
    settings = config or SMCConfig()
    first = candles[0]
    atr_values = average_true_range(candles, settings.atr_period)
    swings = detect_swings(
        candles,
        lookback=settings.swing_lookback(first.timeframe),
    )
    structure_breaks = detect_structure_breaks(candles, swings)
    liquidity_sweeps = detect_liquidity_sweeps(candles, swings)
    equal_levels = detect_equal_levels(
        swings,
        atr_values,
        tolerance_ratio=settings.equal_level_atr_tolerance,
        max_separation=settings.equal_level_max_separation,
    )
    displacements = detect_displacements(
        candles,
        atr_values,
        range_average_period=settings.range_average_period,
        body_atr_ratio=settings.displacement_body_atr_ratio,
        range_average_ratio=settings.displacement_range_average_ratio,
        close_fraction=settings.displacement_close_fraction,
    )
    fair_value_gaps = detect_fair_value_gaps(
        candles,
        atr_values,
        min_atr_ratio=settings.fvg_min_atr_ratio,
    )
    order_blocks = detect_order_blocks(
        candles,
        structure_breaks,
        displacements,
        search_lookback=settings.order_block_search_lookback,
    )
    dealing_range = active_dealing_range(swings)
    price_location = (
        classify_price(candles[-1].close_price, dealing_range)
        if dealing_range is not None
        else None
    )
    return SMCAnalysis(
        symbol=first.symbol,
        timeframe=first.timeframe,
        candle_count=len(candles),
        atr=atr_values,
        swings=swings,
        structure_breaks=structure_breaks,
        liquidity_sweeps=liquidity_sweeps,
        equal_levels=equal_levels,
        displacements=displacements,
        fair_value_gaps=fair_value_gaps,
        order_blocks=order_blocks,
        dealing_range=dealing_range,
        price_location=price_location,
    )
