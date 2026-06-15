from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from crypto_smc.providers.models import Candle1m, PublicTrade
from crypto_smc.signals.lifecycle import (
    LifecycleAction,
    LifecycleState,
    evaluate_public_trade,
)

ONE_MINUTE = timedelta(minutes=1)


def evaluate_closed_candle(
    state: LifecycleState,
    candle: Candle1m,
) -> tuple[LifecycleAction, ...]:
    if candle.symbol != state.symbol:
        return ()
    resolved_at = candle.open_time + ONE_MINUTE
    if state.status == "active":
        if candle.open_time >= state.expires_at or resolved_at > state.expires_at:
            return (LifecycleAction("expired", "fallback_signal_expired"),)
        entry_touched = (
            candle.low_price <= state.entry_upper and candle.high_price >= state.entry_lower
        )
        stop_touched = _touches_stop(
            state.direction,
            state.stop_loss,
            candle.low_price,
            candle.high_price,
        )
        if not entry_touched:
            if stop_touched:
                return (
                    LifecycleAction(
                        "invalidated",
                        "fallback_entry_invalidated",
                    ),
                )
            return ()
        if stop_touched:
            entered = replace(state, status="entered")
            stopped = evaluate_public_trade(
                entered,
                _synthetic_trade(candle, state.stop_loss),
            )[0]
            return (
                replace(
                    stopped,
                    target="ambiguous",
                    event_type="fallback_ambiguous_stop",
                ),
            )
        return evaluate_public_trade(
            state,
            _synthetic_trade(candle, state.planned_entry),
        )

    if state.status == "entered":
        stop_touched = _touches_stop(
            state.direction,
            state.current_stop,
            candle.low_price,
            candle.high_price,
        )
        target_touched = _touches_target(
            state.direction,
            state.take_profit_1,
            candle.low_price,
            candle.high_price,
        )
        target_2_touched = _touches_target(
            state.direction,
            state.take_profit_2,
            candle.low_price,
            candle.high_price,
        )
        if stop_touched and (target_touched or target_2_touched):
            stopped = evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.current_stop),
            )[0]
            return (
                replace(
                    stopped,
                    target="ambiguous",
                    event_type="fallback_ambiguous_stop",
                ),
            )
        if stop_touched:
            return evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.current_stop),
            )
        if target_2_touched:
            return evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.take_profit_2),
            )
        if target_touched:
            return evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.take_profit_1),
            )
        return ()

    if state.status == "tp1_reached":
        stop_touched = _touches_stop(
            state.direction,
            state.current_stop,
            candle.low_price,
            candle.high_price,
        )
        target_touched = _touches_target(
            state.direction,
            state.take_profit_2,
            candle.low_price,
            candle.high_price,
        )
        if stop_touched and target_touched:
            stopped = evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.current_stop),
            )[0]
            return (
                replace(
                    stopped,
                    target="ambiguous",
                    event_type="fallback_ambiguous_breakeven",
                ),
            )
        if stop_touched:
            return evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.current_stop),
            )
        if target_touched:
            return evaluate_public_trade(
                state,
                _synthetic_trade(candle, state.take_profit_2),
            )
    return ()


def _synthetic_trade(candle: Candle1m, price: Decimal) -> PublicTrade:
    return PublicTrade(
        trade_id=f"fallback:{int(candle.open_time.timestamp())}",
        symbol=candle.symbol,
        price=price,
        size=Decimal(0),
        side="Buy",
        executed_at=candle.open_time + ONE_MINUTE - timedelta(microseconds=1),
        sequence=0,
    )


def _touches_stop(
    direction: str,
    stop: Decimal,
    low: Decimal,
    high: Decimal,
) -> bool:
    return low <= stop if direction == "long" else high >= stop


def _touches_target(
    direction: str,
    target: Decimal,
    low: Decimal,
    high: Decimal,
) -> bool:
    return high >= target if direction == "long" else low <= target
