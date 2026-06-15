from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal

from crypto_smc.observation.models import (
    EvaluationReport,
    EvaluationWindow,
    PerformanceMetrics,
    ReadinessAssessment,
    TradeObservation,
)


def build_evaluation_report(
    *,
    window: EvaluationWindow,
    trades: tuple[TradeObservation, ...],
    suppression_reasons: dict[str, int],
    unresolved_data_gaps: int,
    coverage_failures: int,
    generated_at: datetime | None = None,
) -> EvaluationReport:
    ordered = tuple(
        sorted(
            trades,
            key=lambda item: (
                item.resolved_at or datetime.max.replace(tzinfo=UTC),
                item.signal_id,
            ),
        )
    )
    overall = _metrics(ordered)
    maximum_drawdown, maximum_drawdown_fraction = _drawdown(
        ordered,
        reference_balance=window.reference_balance,
    )
    symbol_counts = Counter(item.symbol for item in ordered)
    maximum_symbol_share = (
        Decimal(max(symbol_counts.values())) / Decimal(len(ordered)) if ordered else Decimal(0)
    )
    checks = {
        "minimum_sample": overall.completed >= window.minimum_completed_signals,
        "positive_expectancy": overall.expectancy > 0,
        "profit_factor": (
            overall.profit_factor is not None
            and overall.profit_factor > window.minimum_profit_factor
        ),
        "maximum_drawdown": maximum_drawdown_fraction < window.maximum_drawdown_fraction,
        "symbol_diversification": maximum_symbol_share <= window.maximum_symbol_share,
        "data_quality": unresolved_data_gaps == 0 and coverage_failures == 0,
        "ambiguity_reported": True,
    }
    ready = all(checks.values())
    verdict = (
        "ready_for_manual_review"
        if ready
        else ("insufficient_sample" if not checks["minimum_sample"] else "criteria_not_met")
    )
    return EvaluationReport(
        window=window,
        generated_at=generated_at or datetime.now(UTC),
        overall=overall,
        maximum_drawdown=maximum_drawdown,
        maximum_drawdown_fraction=maximum_drawdown_fraction,
        maximum_symbol_share=maximum_symbol_share,
        by_symbol=_group_metrics(ordered, lambda item: item.symbol),
        by_direction=_group_metrics(ordered, lambda item: item.direction),
        by_score_band=_group_metrics(ordered, lambda item: _score_band(item.score)),
        by_session=_group_metrics(ordered, _trading_session),
        suppression_reasons=dict(sorted(suppression_reasons.items())),
        unresolved_data_gaps=unresolved_data_gaps,
        coverage_failures=coverage_failures,
        readiness=ReadinessAssessment(
            verdict=verdict,
            eligible_for_execution_review=ready,
            checks=checks,
            completed_required=window.minimum_completed_signals,
            completed_observed=overall.completed,
        ),
    )


def _metrics(trades: Iterable[TradeObservation]) -> PerformanceMetrics:
    items = tuple(trades)
    gross_profit = sum(
        (item.realized_pnl for item in items if item.realized_pnl > 0),
        Decimal(0),
    )
    gross_loss = abs(
        sum(
            (item.realized_pnl for item in items if item.realized_pnl < 0),
            Decimal(0),
        )
    )
    net_profit = sum((item.realized_pnl for item in items), Decimal(0))
    completed = len(items)
    entered = sum(item.entered_at is not None for item in items)
    return PerformanceMetrics(
        completed=completed,
        entered=entered,
        not_entered=completed - entered,
        wins=sum(item.realized_pnl > 0 for item in items),
        losses=sum(item.realized_pnl < 0 for item in items),
        breakeven=sum(item.entered_at is not None and item.realized_pnl == 0 for item in items),
        ambiguous=sum(item.ambiguous for item in items),
        net_profit=net_profit,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        expectancy=net_profit / Decimal(completed) if completed else Decimal(0),
        average_r=(
            sum((item.r_multiple for item in items), Decimal(0)) / Decimal(completed)
            if completed
            else Decimal(0)
        ),
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else None,
        fees=sum((item.fees for item in items), Decimal(0)),
        estimated_funding=sum(
            (item.estimated_funding for item in items),
            Decimal(0),
        ),
    )


def _drawdown(
    trades: tuple[TradeObservation, ...],
    *,
    reference_balance: Decimal,
) -> tuple[Decimal, Decimal]:
    balance = reference_balance
    peak = balance
    maximum = Decimal(0)
    maximum_fraction = Decimal(0)
    for trade in trades:
        balance += trade.realized_pnl
        peak = max(peak, balance)
        drawdown = peak - balance
        maximum = max(maximum, drawdown)
        if peak > 0:
            maximum_fraction = max(maximum_fraction, drawdown / peak)
    return maximum, maximum_fraction


def _group_metrics(
    trades: tuple[TradeObservation, ...],
    key: Callable[[TradeObservation], str],
) -> dict[str, PerformanceMetrics]:
    groups: dict[str, list[TradeObservation]] = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _score_band(score: int) -> str:
    if score < 70:
        return "0-69"
    if score < 85:
        return "70-84"
    return "85-100"


def _trading_session(trade: TradeObservation) -> str:
    timestamp = trade.entered_at or trade.created_at
    hour = timestamp.astimezone(UTC).hour
    if hour < 8:
        return "asia_00-08_utc"
    if hour < 13:
        return "london_08-13_utc"
    if hour < 21:
        return "new_york_13-21_utc"
    return "off_hours_21-24_utc"
