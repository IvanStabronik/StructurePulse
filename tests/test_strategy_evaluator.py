from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.strategy import (
    StrategyConfig,
    StrategyInput,
    StrategyMarketContext,
    build_trade_plan,
    evaluate_candidates,
)
from smc_core import (
    DealingRange,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    SMCAnalysis,
    StructureBreak,
    Swing,
)

NOW = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)


def swing(kind: str, price: str, index: int = 2) -> Swing:
    return Swing(
        kind=kind,  # type: ignore[arg-type]
        index=index,
        confirmation_index=index + 1,
        time=NOW - timedelta(minutes=30),
        price=Decimal(price),
    )


def analysis(
    timeframe: str,
    direction: str,
    *,
    zone: tuple[str, str] | None = None,
    target: str | None = None,
    location: str | None = None,
    include_sweep: bool = False,
    include_displacement: bool = False,
    include_break: bool = True,
) -> SMCAnalysis:
    broken = swing("high" if direction == "bullish" else "low", "100")
    break_event = StructureBreak(
        kind="bos",
        direction=direction,  # type: ignore[arg-type]
        index=8,
        time=NOW - timedelta(minutes=5),
        close_price=Decimal("105" if direction == "bullish" else "95"),
        broken_swing=broken,
        prior_trend=direction,  # type: ignore[arg-type]
    )
    swings = [broken]
    if target is not None:
        swings.append(swing("high" if direction == "bullish" else "low", target, index=6))
    low = swing("low", "80", index=1)
    high = swing("high", "120", index=3)
    dealing_range = DealingRange(
        low_swing=low,
        high_swing=high,
        low_price=low.price,
        high_price=high.price,
        midpoint=Decimal(100),
    )
    gap = (
        FairValueGap(
            direction=direction,  # type: ignore[arg-type]
            start_index=6,
            created_index=8,
            created_at=NOW - timedelta(minutes=5),
            lower_price=Decimal(zone[0]),
            upper_price=Decimal(zone[1]),
            status="partially_filled",
            first_touch_index=9,
        )
        if zone is not None
        else None
    )
    sweep_event = (
        LiquiditySweep(
            direction=direction,  # type: ignore[arg-type]
            index=7,
            time=NOW - timedelta(minutes=10),
            level=Decimal(90),
            extreme_price=Decimal(89),
            swept_swing=low if direction == "bullish" else high,
        )
        if include_sweep
        else None
    )
    displacement = (
        Displacement(
            direction=direction,  # type: ignore[arg-type]
            index=8,
            time=NOW - timedelta(minutes=5),
            body_size=Decimal(8),
            range_size=Decimal(10),
            atr=Decimal(5),
            average_range=Decimal(4),
        )
        if include_displacement
        else None
    )
    return SMCAnalysis(
        symbol="BTCUSDT",
        timeframe=timeframe,  # type: ignore[arg-type]
        candle_count=100,
        atr=(None, Decimal(5)),
        swings=tuple(swings),
        structure_breaks=(break_event,) if include_break else (),
        liquidity_sweeps=(sweep_event,) if sweep_event is not None else (),
        equal_levels=(),
        displacements=(displacement,) if displacement is not None else (),
        fair_value_gaps=(gap,) if gap is not None else (),
        order_blocks=(),
        dealing_range=dealing_range,
        price_location=location,  # type: ignore[arg-type]
    )


def strategy_input(direction: str, *, confirm_5m: bool = True) -> StrategyInput:
    is_long = direction == "bullish"
    return StrategyInput(
        symbol="BTCUSDT",
        analyzed_at=NOW,
        analysis_4h=analysis(
            "4h",
            direction,
            location="discount" if is_long else "premium",
        ),
        analysis_1h=analysis(
            "1h",
            direction,
            target="130" if is_long else "70",
            location="discount" if is_long else "premium",
        ),
        analysis_15m=analysis(
            "15m",
            direction,
            zone=("90", "92") if is_long else ("108", "110"),
            target="130" if is_long else "70",
            include_sweep=True,
            include_displacement=True,
        ),
        analysis_5m=analysis("5m", direction, include_break=confirm_5m),
        market=StrategyMarketContext(
            current_price=Decimal(95 if is_long else 105),
            volume_ratio=Decimal("1.2"),
            open_interest_change_ratio=Decimal("0.02"),
            funding_rate=Decimal(0),
            spread_bps=Decimal(5),
            turnover_24h_usdt=Decimal(100_000_000),
            btc_5m_return=Decimal("0.001"),
            btc_true_range_atr_ratio=Decimal(1),
            taker_fee_rate=Decimal("0.00055"),
            instrument_max_leverage=Decimal(100),
        ),
    )


@pytest.mark.parametrize(
    ("smc_direction", "trade_direction"),
    [("bullish", "long"), ("bearish", "short")],
)
def test_mirrored_candidate_is_accepted_and_score_is_reconstructable(
    smc_direction: str,
    trade_direction: str,
) -> None:
    candidates = evaluate_candidates(strategy_input(smc_direction))
    candidate = next(item for item in candidates if item.direction == trade_direction)

    assert candidate.status == "accepted"
    assert candidate.score == 100
    assert candidate.score == sum(item.awarded for item in candidate.components)
    assert candidate.strength == "strong"
    assert candidate.trade_plan is not None
    assert candidate.trade_plan.net_reward_to_risk >= Decimal(3)
    assert candidate.trade_plan.estimated_loss_at_stop <= Decimal(100)


def test_high_score_is_still_suppressed_without_mandatory_5m_confirmation() -> None:
    candidate = evaluate_candidates(strategy_input("bullish", confirm_5m=False))[0]

    assert candidate.score == 100
    assert candidate.status == "suppressed"
    assert "missing_5m_confirmation" in candidate.suppression_reasons


def test_open_zone_must_be_retested_before_acceptance() -> None:
    source = strategy_input("bullish")
    open_gap = replace(
        source.analysis_15m.fair_value_gaps[0],
        status="open",
        first_touch_index=None,
    )
    modified = replace(
        source,
        analysis_15m=replace(source.analysis_15m, fair_value_gaps=(open_gap,)),
    )

    candidate = evaluate_candidates(modified)[0]

    assert candidate.score == 100
    assert candidate.status == "suppressed"
    assert "entry_zone_not_retested" in candidate.suppression_reasons


def test_aggressive_test_treats_15m_displacement_and_retest_as_warnings() -> None:
    source = strategy_input("bullish")
    open_gap = replace(
        source.analysis_15m.fair_value_gaps[0],
        status="open",
        first_touch_index=None,
    )
    modified_15m = analysis(
        "15m",
        "bullish",
        zone=("90", "92"),
        target="130",
        include_sweep=True,
        include_displacement=False,
    )
    modified = replace(
        source,
        analysis_15m=replace(modified_15m, fair_value_gaps=(open_gap,)),
    )

    candidate = evaluate_candidates(
        modified,
        StrategyConfig(
            version="test-aggressive",
            require_15m_displacement=False,
            require_entry_zone_retest=False,
        ),
    )[0]

    assert candidate.status == "accepted"
    assert "missing_15m_structure_displacement" in candidate.warnings
    assert "entry_zone_not_retested" in candidate.warnings
    assert "missing_15m_structure_displacement" not in candidate.suppression_reasons
    assert "entry_zone_not_retested" not in candidate.suppression_reasons


def test_crowded_funding_and_abnormal_btc_reduce_score_and_add_warnings() -> None:
    source = strategy_input("bullish")
    modified = StrategyInput(
        symbol=source.symbol,
        analyzed_at=source.analyzed_at,
        analysis_4h=source.analysis_4h,
        analysis_1h=source.analysis_1h,
        analysis_15m=source.analysis_15m,
        analysis_5m=source.analysis_5m,
        market=StrategyMarketContext(
            current_price=source.market.current_price,
            volume_ratio=source.market.volume_ratio,
            open_interest_change_ratio=source.market.open_interest_change_ratio,
            funding_rate=Decimal("0.002"),
            spread_bps=source.market.spread_bps,
            turnover_24h_usdt=source.market.turnover_24h_usdt,
            btc_5m_return=Decimal("0.025"),
            btc_true_range_atr_ratio=Decimal(3),
        ),
    )

    candidate = evaluate_candidates(modified)[0]

    assert candidate.score == 95
    assert set(candidate.warnings) >= {"crowded_funding", "abnormal_btc_movement"}


def test_risk_plan_caps_loss_and_reduces_unsafe_20x_leverage() -> None:
    plan, warnings = build_trade_plan(
        direction="long",
        entry_lower=Decimal(90),
        entry_upper=Decimal(110),
        atr=Decimal(10),
        target_price=Decimal(150),
        fee_rate=Decimal("0.00055"),
        instrument_max_leverage=Decimal(100),
        quantity_step=Decimal("0.001"),
        minimum_notional=Decimal(5),
        config=StrategyConfig(),
    )

    assert plan is not None
    assert plan.estimated_loss_at_stop <= Decimal(100)
    assert plan.recommended_leverage < Decimal(20)
    assert "leverage_reduced_for_liquidation_buffer" in warnings


def test_trade_plan_rejects_target_on_wrong_side() -> None:
    plan, reasons = build_trade_plan(
        direction="short",
        entry_lower=Decimal(100),
        entry_upper=Decimal(102),
        atr=Decimal(5),
        target_price=Decimal(110),
        fee_rate=Decimal("0.00055"),
        instrument_max_leverage=Decimal(100),
        quantity_step=Decimal("0.001"),
        minimum_notional=Decimal(5),
        config=StrategyConfig(),
    )

    assert plan is None
    assert reasons == ("no_directional_liquidity_target",)
