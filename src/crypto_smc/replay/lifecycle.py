from datetime import datetime, timedelta
from decimal import Decimal

from crypto_smc.replay.models import (
    ReplayMarketRow,
    ReplayOutcome,
    ReplayOutcomeStatus,
)
from crypto_smc.strategy import SignalCandidate, TradePlan

ONE_MINUTE = timedelta(minutes=1)


def resolve_candidate(
    sequence: int,
    candidate: SignalCandidate,
    rows: tuple[ReplayMarketRow, ...],
) -> ReplayOutcome:
    plan = candidate.trade_plan
    if candidate.status != "accepted" or plan is None:
        raise ValueError("Only accepted candidates with a trade plan can be resolved")

    future = tuple(
        row
        for row in rows
        if row.candle.symbol == candidate.symbol and row.candle.open_time >= candidate.analyzed_at
    )
    entered_at = None
    tp1_reached = False
    current_stop = plan.stop_loss

    for row in future:
        candle = row.candle
        candle_end = candle.open_time + ONE_MINUTE
        if entered_at is None:
            if candle.open_time >= candidate.expires_at:
                return _flat_outcome(sequence, candidate, "expired", candidate.expires_at)
            if _gapped_beyond_stop(candidate.direction, plan, candle.open_price):
                return _flat_outcome(
                    sequence,
                    candidate,
                    "invalidated_before_entry",
                    candle.open_time,
                )
            if not _touches_entry(plan, candle.low_price, candle.high_price):
                continue
            entered_at = candle.open_time
            if _touches_stop(
                candidate.direction,
                current_stop,
                candle.low_price,
                candle.high_price,
            ):
                return _stopped_outcome(
                    sequence,
                    candidate,
                    entered_at,
                    candle_end,
                    ambiguous=True,
                )
            if _touches_tp1(candidate.direction, plan, candle.low_price, candle.high_price):
                return _flat_outcome(
                    sequence,
                    candidate,
                    "ambiguous",
                    candle_end,
                    entered_at=entered_at,
                    ambiguous=True,
                )
            continue

        stop_touched = _touches_stop(
            candidate.direction,
            current_stop,
            candle.low_price,
            candle.high_price,
        )
        tp1_touched = not tp1_reached and _touches_tp1(
            candidate.direction,
            plan,
            candle.low_price,
            candle.high_price,
        )
        tp2_touched = _touches_tp2(
            candidate.direction,
            plan,
            candle.low_price,
            candle.high_price,
        )
        if stop_touched and (tp1_touched or tp2_touched):
            return _stopped_outcome(
                sequence,
                candidate,
                entered_at,
                candle_end,
                ambiguous=True,
                after_tp1=tp1_reached,
            )
        if stop_touched:
            return _stopped_outcome(
                sequence,
                candidate,
                entered_at,
                candle_end,
                ambiguous=False,
                after_tp1=tp1_reached,
            )
        if tp2_touched:
            return _target_outcome(sequence, candidate, entered_at, candle_end)
        if tp1_touched:
            tp1_reached = True
            current_stop = _fee_adjusted_breakeven(candidate.direction, plan)

    return _flat_outcome(
        sequence,
        candidate,
        "open",
        None,
        entered_at=entered_at,
    )


def _touches_entry(plan: TradePlan, low: Decimal, high: Decimal) -> bool:
    return low <= plan.entry_upper and high >= plan.entry_lower


def _touches_stop(
    direction: str,
    stop: Decimal,
    low: Decimal,
    high: Decimal,
) -> bool:
    return low <= stop if direction == "long" else high >= stop


def _touches_tp1(
    direction: str,
    plan: TradePlan,
    low: Decimal,
    high: Decimal,
) -> bool:
    return high >= plan.take_profit_1 if direction == "long" else low <= plan.take_profit_1


def _touches_tp2(
    direction: str,
    plan: TradePlan,
    low: Decimal,
    high: Decimal,
) -> bool:
    return high >= plan.take_profit_2 if direction == "long" else low <= plan.take_profit_2


def _gapped_beyond_stop(direction: str, plan: TradePlan, open_price: Decimal) -> bool:
    return open_price < plan.stop_loss if direction == "long" else open_price > plan.stop_loss


def _fee_adjusted_breakeven(direction: str, plan: TradePlan) -> Decimal:
    fee_rate = plan.estimated_entry_fee / plan.notional if plan.notional > 0 else Decimal(0)
    if direction == "long":
        return plan.planned_entry * (Decimal(1) + fee_rate) / (Decimal(1) - fee_rate)
    return plan.planned_entry * (Decimal(1) - fee_rate) / (Decimal(1) + fee_rate)


def _stopped_outcome(
    sequence: int,
    candidate: SignalCandidate,
    entered_at: datetime,
    resolved_at: datetime,
    *,
    ambiguous: bool,
    after_tp1: bool = False,
) -> ReplayOutcome:
    plan = _plan(candidate)
    if after_tp1 and not ambiguous:
        pnl, fees = _pnl_after_tp1_stop(candidate.direction, plan)
        status: ReplayOutcomeStatus = "stopped_after_tp1"
    else:
        pnl = -plan.estimated_loss_at_stop
        fees = plan.estimated_entry_fee + plan.quantity * plan.stop_loss * _fee_rate(plan)
        status = "ambiguous" if ambiguous else "stopped"
    return ReplayOutcome(
        candidate_sequence=sequence,
        symbol=candidate.symbol,
        direction=candidate.direction,
        status=status,
        entered_at=entered_at,
        resolved_at=resolved_at,
        pnl=pnl,
        r_multiple=pnl / plan.risk_amount,
        fees=fees,
        ambiguous=ambiguous,
    )


def _target_outcome(
    sequence: int,
    candidate: SignalCandidate,
    entered_at: datetime,
    resolved_at: datetime,
) -> ReplayOutcome:
    plan = _plan(candidate)
    half = plan.quantity / Decimal(2)
    direction = Decimal(1) if candidate.direction == "long" else Decimal(-1)
    gross = half * (
        (plan.take_profit_1 - plan.planned_entry) * direction
        + (plan.take_profit_2 - plan.planned_entry) * direction
    )
    fee_rate = _fee_rate(plan)
    fees = (
        plan.estimated_entry_fee
        + half * plan.take_profit_1 * fee_rate
        + half * plan.take_profit_2 * fee_rate
    )
    pnl = gross - fees
    return ReplayOutcome(
        candidate_sequence=sequence,
        symbol=candidate.symbol,
        direction=candidate.direction,
        status="tp2",
        entered_at=entered_at,
        resolved_at=resolved_at,
        pnl=pnl,
        r_multiple=pnl / plan.risk_amount,
        fees=fees,
        ambiguous=False,
    )


def _pnl_after_tp1_stop(direction: str, plan: TradePlan) -> tuple[Decimal, Decimal]:
    half = plan.quantity / Decimal(2)
    direction_sign = Decimal(1) if direction == "long" else Decimal(-1)
    breakeven = _fee_adjusted_breakeven(direction, plan)
    gross = half * (
        (plan.take_profit_1 - plan.planned_entry) * direction_sign
        + (breakeven - plan.planned_entry) * direction_sign
    )
    fee_rate = _fee_rate(plan)
    fees = (
        plan.estimated_entry_fee
        + half * plan.take_profit_1 * fee_rate
        + half * breakeven * fee_rate
    )
    return gross - fees, fees


def _flat_outcome(
    sequence: int,
    candidate: SignalCandidate,
    status: ReplayOutcomeStatus,
    resolved_at: datetime | None,
    *,
    entered_at: datetime | None = None,
    ambiguous: bool = False,
) -> ReplayOutcome:
    return ReplayOutcome(
        candidate_sequence=sequence,
        symbol=candidate.symbol,
        direction=candidate.direction,
        status=status,
        entered_at=entered_at,
        resolved_at=resolved_at,
        pnl=Decimal(0),
        r_multiple=Decimal(0),
        fees=Decimal(0),
        ambiguous=ambiguous,
    )


def _fee_rate(plan: TradePlan) -> Decimal:
    return plan.estimated_entry_fee / plan.notional if plan.notional > 0 else Decimal(0)


def _plan(candidate: SignalCandidate) -> TradePlan:
    if candidate.trade_plan is None:
        raise ValueError("Candidate has no trade plan")
    return candidate.trade_plan
