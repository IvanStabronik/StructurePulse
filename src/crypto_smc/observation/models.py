from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EvaluationWindow:
    id: int
    name: str
    strategy_version: str
    strategy_parameter_checksum: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    minimum_completed_signals: int
    minimum_profit_factor: Decimal
    maximum_drawdown_fraction: Decimal
    maximum_symbol_share: Decimal
    reference_balance: Decimal


@dataclass(frozen=True, slots=True)
class TradeObservation:
    signal_id: int
    symbol: str
    direction: str
    score: int
    status: str
    created_at: datetime
    entered_at: datetime | None
    resolved_at: datetime | None
    realized_pnl: Decimal
    fees: Decimal
    estimated_funding: Decimal
    r_multiple: Decimal
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    symbol: str
    status: str
    score: int


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    total: int
    accepted: int
    suppressed: int
    symbols: int
    duration_hours: Decimal
    accepted_per_symbol_day: Decimal
    average_score: Decimal
    score_bands: dict[str, int]


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    completed: int
    entered: int
    not_entered: int
    wins: int
    losses: int
    breakeven: int
    ambiguous: int
    net_profit: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    expectancy: Decimal
    average_r: Decimal
    profit_factor: Decimal | None
    fees: Decimal
    estimated_funding: Decimal


@dataclass(frozen=True, slots=True)
class ReadinessAssessment:
    verdict: str
    eligible_for_execution_review: bool
    checks: dict[str, bool]
    completed_required: int
    completed_observed: int


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    window: EvaluationWindow
    generated_at: datetime
    candidates: CandidateMetrics
    overall: PerformanceMetrics
    maximum_drawdown: Decimal
    maximum_drawdown_fraction: Decimal
    maximum_symbol_share: Decimal
    by_symbol: dict[str, PerformanceMetrics]
    by_direction: dict[str, PerformanceMetrics]
    by_score_band: dict[str, PerformanceMetrics]
    by_session: dict[str, PerformanceMetrics]
    suppression_reasons: dict[str, int]
    unresolved_data_gaps: int
    coverage_failures: int
    readiness: ReadinessAssessment
