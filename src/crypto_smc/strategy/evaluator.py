from datetime import timedelta
from decimal import Decimal

from crypto_smc.strategy.config import StrategyConfig
from crypto_smc.strategy.models import (
    CandidateStatus,
    ScoreComponent,
    SignalCandidate,
    StrategyInput,
    TradeDirection,
)
from crypto_smc.strategy.risk import build_trade_plan
from smc_core import SMCAnalysis


def evaluate_candidates(
    strategy_input: StrategyInput,
    config: StrategyConfig | None = None,
) -> tuple[SignalCandidate, SignalCandidate]:
    settings = config or StrategyConfig()
    return (
        _evaluate_direction(strategy_input, "long", settings),
        _evaluate_direction(strategy_input, "short", settings),
    )


def _evaluate_direction(
    strategy_input: StrategyInput,
    direction: TradeDirection,
    config: StrategyConfig,
) -> SignalCandidate:
    smc_direction = "bullish" if direction == "long" else "bearish"
    desired_location = "discount" if direction == "long" else "premium"
    components: list[ScoreComponent] = []
    evidence: list[str] = []
    warnings: list[str] = []
    suppression: list[str] = []

    htf_directions = (
        _latest_break_direction(strategy_input.analysis_4h),
        _latest_break_direction(strategy_input.analysis_1h),
    )
    aligned = sum(item == smc_direction for item in htf_directions)
    opposing = sum(item is not None and item != smc_direction for item in htf_directions)
    htf_score = (
        config.weights.higher_timeframe_alignment
        if aligned == 2
        else (
            config.weights.higher_timeframe_alignment // 2 if aligned == 1 and opposing == 0 else 0
        )
    )
    components.append(
        ScoreComponent(
            name="higher_timeframe_alignment",
            awarded=htf_score,
            maximum=config.weights.higher_timeframe_alignment,
            evidence=f"4h={htf_directions[0] or 'neutral'},1h={htf_directions[1] or 'neutral'}",
        )
    )
    if opposing == 2:
        suppression.append("higher_timeframes_opposed")

    sweep = next(
        (
            item
            for item in reversed(strategy_input.analysis_15m.liquidity_sweeps)
            if item.direction == smc_direction
        ),
        None,
    )
    components.append(
        ScoreComponent(
            name="liquidity_sweep",
            awarded=config.weights.liquidity_sweep if sweep is not None else 0,
            maximum=config.weights.liquidity_sweep,
            evidence=f"{smc_direction}_15m_sweep" if sweep is not None else "no_15m_sweep",
        )
    )
    if sweep is not None:
        evidence.append(f"15m liquidity sweep at {sweep.level}")

    setup_break = next(
        (
            item
            for item in reversed(strategy_input.analysis_15m.structure_breaks)
            if item.direction == smc_direction
        ),
        None,
    )
    has_displacement = setup_break is not None and any(
        item.direction == smc_direction and item.index == setup_break.index
        for item in strategy_input.analysis_15m.displacements
    )
    structure_score = (
        config.weights.structure_confirmation
        if has_displacement
        else (config.weights.structure_confirmation // 2 if setup_break is not None else 0)
    )
    components.append(
        ScoreComponent(
            name="structure_confirmation",
            awarded=structure_score,
            maximum=config.weights.structure_confirmation,
            evidence=(
                f"15m_{setup_break.kind}_with_displacement"
                if has_displacement and setup_break is not None
                else f"15m_{setup_break.kind}_without_displacement"
                if setup_break is not None
                else "no_15m_structure_break"
            ),
        )
    )
    if not has_displacement:
        suppression.append("missing_15m_structure_displacement")

    zone = _latest_entry_zone(strategy_input.analysis_15m, smc_direction)
    zone_score = 0
    if zone is not None:
        zone_score = config.weights.entry_zone_quality
        evidence.append(f"15m {zone[0]} zone {zone[1]}-{zone[2]}")
    components.append(
        ScoreComponent(
            name="entry_zone_quality",
            awarded=zone_score,
            maximum=config.weights.entry_zone_quality,
            evidence=zone[0] if zone is not None else "no_open_entry_zone",
        )
    )
    if zone is None:
        suppression.append("missing_entry_zone")
    elif zone[3] != "partially_filled" and not (
        zone[1] <= strategy_input.market.current_price <= zone[2]
    ):
        suppression.append("entry_zone_not_retested")

    location_matches = (
        strategy_input.analysis_4h.price_location == desired_location
        or strategy_input.analysis_1h.price_location == desired_location
    )
    components.append(
        ScoreComponent(
            name="premium_discount",
            awarded=config.weights.premium_discount if location_matches else 0,
            maximum=config.weights.premium_discount,
            evidence=(
                f"price_in_{desired_location}" if location_matches else "location_not_preferred"
            ),
        )
    )

    volume_ok = (
        strategy_input.market.volume_ratio is not None
        and strategy_input.market.volume_ratio >= config.volume_confirmation_ratio
    )
    oi_ok = (
        strategy_input.market.open_interest_change_ratio is not None
        and strategy_input.market.open_interest_change_ratio
        >= config.open_interest_confirmation_ratio
    )
    market_score = config.weights.volume_open_interest * (int(volume_ok) + int(oi_ok)) // 2
    components.append(
        ScoreComponent(
            name="volume_open_interest",
            awarded=market_score,
            maximum=config.weights.volume_open_interest,
            evidence=f"volume={volume_ok},open_interest={oi_ok}",
        )
    )

    crowded_funding = _funding_is_crowded(
        direction,
        strategy_input.market.funding_rate,
        config.crowded_funding_rate,
    )
    btc_abnormal = _btc_is_abnormal(strategy_input, config)
    condition_score = 0 if crowded_funding or btc_abnormal else config.weights.funding_btc_condition
    components.append(
        ScoreComponent(
            name="funding_btc_condition",
            awarded=condition_score,
            maximum=config.weights.funding_btc_condition,
            evidence=f"crowded_funding={crowded_funding},btc_abnormal={btc_abnormal}",
        )
    )
    if crowded_funding:
        warnings.append("crowded_funding")
    if btc_abnormal:
        warnings.append("abnormal_btc_movement")

    _append_market_filter_reasons(strategy_input, config, suppression)

    confirmation = next(
        (
            item
            for item in reversed(strategy_input.analysis_5m.structure_breaks)
            if item.direction == smc_direction
        ),
        None,
    )
    if confirmation is None:
        suppression.append("missing_5m_confirmation")
    else:
        evidence.append(f"5m {confirmation.kind} confirmation")

    trade_plan = None
    if zone is not None:
        atr = _latest_atr(strategy_input.analysis_15m)
        target = _liquidity_target(
            strategy_input.analysis_15m,
            strategy_input.analysis_1h,
            direction,
            zone,
        )
        if atr is None:
            suppression.append("atr_unavailable")
        elif target is None:
            suppression.append("liquidity_target_unavailable")
        else:
            trade_plan, plan_details = build_trade_plan(
                direction=direction,
                entry_lower=zone[1],
                entry_upper=zone[2],
                atr=atr,
                target_price=target,
                fee_rate=strategy_input.market.taker_fee_rate,
                instrument_max_leverage=strategy_input.market.instrument_max_leverage,
                quantity_step=strategy_input.market.instrument_quantity_step,
                minimum_notional=strategy_input.market.instrument_min_notional,
                config=config,
            )
            if trade_plan is None:
                suppression.extend(plan_details)
            elif trade_plan.net_reward_to_risk < config.minimum_net_reward_to_risk:
                warnings.extend(plan_details)
                suppression.append("reward_to_risk_below_minimum")
            else:
                warnings.extend(plan_details)

    score = sum(component.awarded for component in components)
    if score < config.minimum_score:
        suppression.append("score_below_threshold")
    suppression = list(dict.fromkeys(suppression))
    status: CandidateStatus = "suppressed" if suppression else "accepted"
    return SignalCandidate(
        symbol=strategy_input.symbol,
        direction=direction,
        strategy_version=config.version,
        status=status,
        score=score,
        strength="strong" if score >= config.strong_score else "standard",
        components=tuple(components),
        evidence=tuple(evidence),
        warnings=tuple(dict.fromkeys(warnings)),
        suppression_reasons=tuple(suppression),
        trade_plan=trade_plan,
        analyzed_at=strategy_input.analyzed_at,
        expires_at=strategy_input.analyzed_at + timedelta(minutes=config.signal_lifetime_minutes),
    )


def _latest_break_direction(analysis: SMCAnalysis) -> str | None:
    return analysis.structure_breaks[-1].direction if analysis.structure_breaks else None


def _latest_entry_zone(
    analysis: SMCAnalysis,
    direction: str,
) -> tuple[str, Decimal, Decimal, str] | None:
    zones: list[tuple[int, str, Decimal, Decimal, str]] = []
    zones.extend(
        (
            gap.created_index,
            "fvg",
            gap.lower_price,
            gap.upper_price,
            gap.status,
        )
        for gap in analysis.fair_value_gaps
        if gap.direction == direction and gap.status in {"open", "partially_filled"}
    )
    zones.extend(
        (
            block.created_index,
            "order_block",
            block.lower_price,
            block.upper_price,
            block.status,
        )
        for block in analysis.order_blocks
        if block.direction == direction and block.status in {"open", "partially_filled"}
    )
    if not zones:
        return None
    _, kind, lower, upper, status = max(zones, key=lambda item: item[0])
    return kind, lower, upper, status


def _latest_atr(analysis: SMCAnalysis) -> Decimal | None:
    return next((value for value in reversed(analysis.atr) if value is not None), None)


def _liquidity_target(
    setup: SMCAnalysis,
    context: SMCAnalysis,
    direction: TradeDirection,
    zone: tuple[str, Decimal, Decimal, str],
) -> Decimal | None:
    entry = (zone[1] + zone[2]) / Decimal(2)
    kind = "high" if direction == "long" else "low"
    candidates = [
        swing.price
        for analysis in (setup, context)
        for swing in analysis.swings
        if swing.kind == kind
        and (
            (direction == "long" and swing.price > entry)
            or (direction == "short" and swing.price < entry)
        )
    ]
    if not candidates:
        return None
    return min(candidates) if direction == "long" else max(candidates)


def _funding_is_crowded(
    direction: TradeDirection,
    funding_rate: Decimal | None,
    threshold: Decimal,
) -> bool:
    if funding_rate is None:
        return False
    return funding_rate >= threshold if direction == "long" else funding_rate <= -threshold


def _btc_is_abnormal(strategy_input: StrategyInput, config: StrategyConfig) -> bool:
    market = strategy_input.market
    return bool(
        (
            market.btc_5m_return is not None
            and abs(market.btc_5m_return) >= config.btc_return_warning_threshold
        )
        or (
            market.btc_true_range_atr_ratio is not None
            and market.btc_true_range_atr_ratio >= config.btc_true_range_warning_ratio
        )
    )


def _append_market_filter_reasons(
    strategy_input: StrategyInput,
    config: StrategyConfig,
    suppression: list[str],
) -> None:
    market = strategy_input.market
    if (
        market.turnover_24h_usdt is not None
        and market.turnover_24h_usdt < config.minimum_turnover_24h_usdt
    ):
        suppression.append("turnover_below_minimum")
    if market.spread_bps is not None and market.spread_bps > config.maximum_spread_bps:
        suppression.append("spread_above_maximum")
    atr = _latest_atr(strategy_input.analysis_15m)
    if atr is not None and market.current_price > 0:
        atr_percent = atr / market.current_price
        if atr_percent < config.minimum_atr_percent:
            suppression.append("volatility_below_minimum")
        if atr_percent > config.maximum_atr_percent:
            suppression.append("volatility_above_maximum")
