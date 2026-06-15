from typing import Literal

type SignalStatus = Literal[
    "suppressed",
    "preparing",
    "active",
    "expired",
    "invalidated",
    "entered",
    "stopped",
    "tp1_reached",
    "stopped_at_breakeven",
    "tp2_completed",
    "ambiguous",
    "coverage_failed",
]
type VirtualTradeStatus = Literal[
    "waiting_entry",
    "entered",
    "tp1_reached",
    "stopped",
    "stopped_at_breakeven",
    "tp2_completed",
    "expired",
    "invalidated",
    "ambiguous",
    "coverage_failed",
]

SIGNAL_TRANSITIONS: dict[SignalStatus, frozenset[SignalStatus]] = {
    "suppressed": frozenset(),
    "preparing": frozenset({"active", "coverage_failed", "expired"}),
    "active": frozenset({"entered", "expired", "invalidated", "ambiguous", "coverage_failed"}),
    "entered": frozenset({"stopped", "tp1_reached", "ambiguous"}),
    "tp1_reached": frozenset({"stopped_at_breakeven", "tp2_completed", "ambiguous"}),
    "expired": frozenset(),
    "invalidated": frozenset(),
    "stopped": frozenset(),
    "stopped_at_breakeven": frozenset(),
    "tp2_completed": frozenset(),
    "ambiguous": frozenset(),
    "coverage_failed": frozenset(),
}
VIRTUAL_TRADE_TRANSITIONS: dict[VirtualTradeStatus, frozenset[VirtualTradeStatus]] = {
    "waiting_entry": frozenset(
        {"entered", "expired", "invalidated", "ambiguous", "coverage_failed"}
    ),
    "entered": frozenset({"stopped", "tp1_reached", "ambiguous"}),
    "tp1_reached": frozenset({"stopped_at_breakeven", "tp2_completed", "ambiguous"}),
    "stopped": frozenset(),
    "stopped_at_breakeven": frozenset(),
    "tp2_completed": frozenset(),
    "expired": frozenset(),
    "invalidated": frozenset(),
    "ambiguous": frozenset(),
    "coverage_failed": frozenset(),
}


def transition_signal(current: SignalStatus, target: SignalStatus) -> SignalStatus:
    if target not in SIGNAL_TRANSITIONS[current]:
        raise ValueError(f"Invalid signal transition: {current} -> {target}")
    return target


def transition_virtual_trade(
    current: VirtualTradeStatus,
    target: VirtualTradeStatus,
) -> VirtualTradeStatus:
    if target not in VIRTUAL_TRADE_TRANSITIONS[current]:
        raise ValueError(f"Invalid virtual trade transition: {current} -> {target}")
    return target
