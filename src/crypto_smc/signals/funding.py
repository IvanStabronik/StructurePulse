from datetime import datetime
from decimal import Decimal

from crypto_smc.signals.lifecycle import LifecycleState
from crypto_smc.signals.state_machine import SignalStatus


def estimate_funding_cost(
    state: LifecycleState,
    *,
    event_time: datetime,
    target: SignalStatus,
) -> Decimal:
    entered_at = state.entered_at
    if (
        entered_at is None
        or event_time <= entered_at
        or state.funding_interval_minutes <= 0
        or state.funding_rate == 0
    ):
        return Decimal(0)

    direction_sign = Decimal(1) if state.direction == "long" else Decimal(-1)
    rate = state.funding_rate * direction_sign
    interval_seconds = Decimal(state.funding_interval_minutes * 60)

    def segment_cost(
        quantity: Decimal,
        start: datetime,
        end: datetime,
    ) -> Decimal:
        seconds = Decimal(str(max(0.0, (end - start).total_seconds())))
        return quantity * state.planned_entry * rate * seconds / interval_seconds

    if target == "tp1_reached":
        return segment_cost(state.quantity, entered_at, event_time)
    if state.tp1_reached_at is None:
        return segment_cost(state.quantity, entered_at, event_time)

    tp1_time = min(max(state.tp1_reached_at, entered_at), event_time)
    return segment_cost(
        state.quantity,
        entered_at,
        tp1_time,
    ) + segment_cost(
        state.quantity / Decimal(2),
        tp1_time,
        event_time,
    )
