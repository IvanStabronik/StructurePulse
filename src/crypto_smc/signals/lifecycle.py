from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_smc.providers.models import PublicTrade
from crypto_smc.signals.state_machine import SignalStatus


@dataclass(frozen=True, slots=True)
class LifecycleState:
    signal_id: int
    symbol: str
    direction: str
    status: SignalStatus
    entry_lower: Decimal
    entry_upper: Decimal
    planned_entry: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    quantity: Decimal
    risk_amount: Decimal
    taker_fee_rate: Decimal
    expires_at: datetime
    current_stop: Decimal


@dataclass(frozen=True, slots=True)
class LifecycleAction:
    target: SignalStatus
    event_type: str
    realized_pnl: Decimal | None = None
    fees: Decimal | None = None
    r_multiple: Decimal | None = None
    current_stop: Decimal | None = None
    remaining_quantity: Decimal | None = None


def evaluate_public_trade(
    state: LifecycleState,
    trade: PublicTrade,
) -> tuple[LifecycleAction, ...]:
    if trade.symbol != state.symbol:
        return ()
    if state.status in {"preparing", "suppressed"}:
        return ()
    if state.status == "active":
        if trade.executed_at >= state.expires_at:
            return (LifecycleAction("expired", "signal_expired"),)
        if _beyond_stop(state.direction, state.stop_loss, trade.price):
            return (LifecycleAction("invalidated", "entry_invalidated"),)
        if state.entry_lower <= trade.price <= state.entry_upper:
            entry_fee = state.quantity * state.planned_entry * state.taker_fee_rate
            return (
                LifecycleAction(
                    "entered",
                    "entry_filled",
                    realized_pnl=-entry_fee,
                    fees=entry_fee,
                    r_multiple=-entry_fee / state.risk_amount,
                ),
            )
        return ()
    if state.status == "entered":
        if _beyond_stop(state.direction, state.current_stop, trade.price):
            pnl, fees = _full_exit(state, state.current_stop)
            return (
                LifecycleAction(
                    "stopped",
                    "stop_loss_filled",
                    realized_pnl=pnl,
                    fees=fees,
                    r_multiple=pnl / state.risk_amount,
                    remaining_quantity=Decimal(0),
                ),
            )
        if _target_reached(state.direction, state.take_profit_2, trade.price):
            return _tp1_and_tp2(state)
        if _target_reached(state.direction, state.take_profit_1, trade.price):
            pnl, fees = _tp1_result(state)
            return (
                LifecycleAction(
                    "tp1_reached",
                    "take_profit_1_filled",
                    realized_pnl=pnl,
                    fees=fees,
                    r_multiple=pnl / state.risk_amount,
                    current_stop=_fee_adjusted_breakeven(state),
                    remaining_quantity=state.quantity / Decimal(2),
                ),
            )
        return ()
    if state.status == "tp1_reached":
        if _beyond_stop(state.direction, state.current_stop, trade.price):
            pnl, fees = _tp1_then_exit(state, state.current_stop)
            return (
                LifecycleAction(
                    "stopped_at_breakeven",
                    "breakeven_stop_filled",
                    realized_pnl=pnl,
                    fees=fees,
                    r_multiple=pnl / state.risk_amount,
                    remaining_quantity=Decimal(0),
                ),
            )
        if _target_reached(state.direction, state.take_profit_2, trade.price):
            pnl, fees = _tp1_then_exit(state, state.take_profit_2)
            return (
                LifecycleAction(
                    "tp2_completed",
                    "take_profit_2_filled",
                    realized_pnl=pnl,
                    fees=fees,
                    r_multiple=pnl / state.risk_amount,
                    remaining_quantity=Decimal(0),
                ),
            )
    return ()


def _tp1_and_tp2(state: LifecycleState) -> tuple[LifecycleAction, ...]:
    tp1_pnl, tp1_fees = _tp1_result(state)
    final_pnl, final_fees = _tp1_then_exit(state, state.take_profit_2)
    return (
        LifecycleAction(
            "tp1_reached",
            "take_profit_1_filled",
            realized_pnl=tp1_pnl,
            fees=tp1_fees,
            r_multiple=tp1_pnl / state.risk_amount,
            current_stop=_fee_adjusted_breakeven(state),
            remaining_quantity=state.quantity / Decimal(2),
        ),
        LifecycleAction(
            "tp2_completed",
            "take_profit_2_filled",
            realized_pnl=final_pnl,
            fees=final_fees,
            r_multiple=final_pnl / state.risk_amount,
            remaining_quantity=Decimal(0),
        ),
    )


def _full_exit(state: LifecycleState, price: Decimal) -> tuple[Decimal, Decimal]:
    gross = state.quantity * _signed_move(state.direction, state.planned_entry, price)
    fees = state.quantity * (state.planned_entry + price) * state.taker_fee_rate
    return gross - fees, fees


def _tp1_result(state: LifecycleState) -> tuple[Decimal, Decimal]:
    half = state.quantity / Decimal(2)
    gross = half * _signed_move(
        state.direction,
        state.planned_entry,
        state.take_profit_1,
    )
    fees = (
        state.quantity * state.planned_entry + half * state.take_profit_1
    ) * state.taker_fee_rate
    return gross - fees, fees


def _tp1_then_exit(
    state: LifecycleState,
    second_exit: Decimal,
) -> tuple[Decimal, Decimal]:
    half = state.quantity / Decimal(2)
    gross = half * (
        _signed_move(state.direction, state.planned_entry, state.take_profit_1)
        + _signed_move(state.direction, state.planned_entry, second_exit)
    )
    fees = (
        state.quantity * state.planned_entry + half * state.take_profit_1 + half * second_exit
    ) * state.taker_fee_rate
    return gross - fees, fees


def _fee_adjusted_breakeven(state: LifecycleState) -> Decimal:
    fee = state.taker_fee_rate
    if state.direction == "long":
        return state.planned_entry * (Decimal(1) + fee) / (Decimal(1) - fee)
    return state.planned_entry * (Decimal(1) - fee) / (Decimal(1) + fee)


def _signed_move(direction: str, entry: Decimal, exit_price: Decimal) -> Decimal:
    return exit_price - entry if direction == "long" else entry - exit_price


def _beyond_stop(direction: str, stop: Decimal, price: Decimal) -> bool:
    return price <= stop if direction == "long" else price >= stop


def _target_reached(direction: str, target: Decimal, price: Decimal) -> bool:
    return price >= target if direction == "long" else price <= target
