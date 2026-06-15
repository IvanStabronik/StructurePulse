from decimal import ROUND_DOWN, Decimal

from crypto_smc.strategy.config import StrategyConfig
from crypto_smc.strategy.models import TradeDirection, TradePlan


def build_trade_plan(
    *,
    direction: TradeDirection,
    entry_lower: Decimal,
    entry_upper: Decimal,
    atr: Decimal,
    target_price: Decimal,
    fee_rate: Decimal,
    instrument_max_leverage: Decimal,
    quantity_step: Decimal,
    minimum_notional: Decimal,
    config: StrategyConfig,
) -> tuple[TradePlan | None, tuple[str, ...]]:
    if entry_lower <= 0 or entry_upper <= entry_lower or atr <= 0 or quantity_step <= 0:
        return None, ("invalid_entry_zone",)

    planned_entry = (entry_lower + entry_upper) / Decimal(2)
    buffer = atr * config.stop_atr_buffer
    if direction == "long":
        stop_loss = entry_lower - buffer
        risk_per_unit = planned_entry - stop_loss
        reward_per_unit = target_price - planned_entry
    else:
        stop_loss = entry_upper + buffer
        risk_per_unit = stop_loss - planned_entry
        reward_per_unit = planned_entry - target_price
    if stop_loss <= 0 or risk_per_unit <= 0:
        return None, ("invalid_stop_loss",)
    if reward_per_unit <= 0:
        return None, ("no_directional_liquidity_target",)

    entry_fee_per_unit = planned_entry * fee_rate
    exit_fee_per_unit = target_price * fee_rate
    net_risk_per_unit = risk_per_unit + entry_fee_per_unit + stop_loss * fee_rate
    net_reward_per_unit = reward_per_unit - entry_fee_per_unit - exit_fee_per_unit
    if net_reward_per_unit <= 0:
        return None, ("fees_consume_reward",)

    raw_quantity = config.risk_amount / net_risk_per_unit
    quantity = (raw_quantity / quantity_step).to_integral_value(rounding=ROUND_DOWN) * quantity_step
    if quantity <= 0:
        return None, ("quantity_below_exchange_minimum",)
    notional = quantity * planned_entry
    if notional < minimum_notional:
        return None, ("notional_below_exchange_minimum",)
    gross_rr = reward_per_unit / risk_per_unit
    net_rr = net_reward_per_unit / net_risk_per_unit
    stop_fraction = risk_per_unit / planned_entry
    safe_leverage = (Decimal(1) / (stop_fraction * config.liquidation_buffer_multiplier)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    recommended_leverage = max(
        Decimal(1),
        min(
            config.maximum_display_leverage,
            instrument_max_leverage,
            safe_leverage,
        ),
    )
    estimated_margin = notional / recommended_leverage
    tp1_distance = risk_per_unit * config.take_profit_1_r_multiple
    take_profit_1 = (
        planned_entry + tp1_distance if direction == "long" else planned_entry - tp1_distance
    )
    warnings: list[str] = []
    if recommended_leverage < config.maximum_display_leverage:
        warnings.append("leverage_reduced_for_liquidation_buffer")

    plan = TradePlan(
        entry_lower=entry_lower,
        entry_upper=entry_upper,
        planned_entry=planned_entry,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=target_price,
        gross_reward_to_risk=gross_rr,
        net_reward_to_risk=net_rr,
        risk_amount=config.risk_amount,
        quantity=quantity,
        notional=notional,
        recommended_leverage=recommended_leverage,
        estimated_margin=estimated_margin,
        estimated_entry_fee=quantity * entry_fee_per_unit,
        estimated_exit_fee=quantity * exit_fee_per_unit,
        estimated_loss_at_stop=quantity * net_risk_per_unit,
        invalidation=(
            f"close below {stop_loss}" if direction == "long" else f"close above {stop_loss}"
        ),
    )
    return plan, tuple(warnings)
