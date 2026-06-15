from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from smc_core import SMCAnalysis

type TradeDirection = Literal["long", "short"]
type CandidateStatus = Literal["accepted", "suppressed"]
type Strength = Literal["standard", "strong"]


@dataclass(frozen=True, slots=True)
class StrategyMarketContext:
    current_price: Decimal
    volume_ratio: Decimal | None = None
    open_interest_change_ratio: Decimal | None = None
    funding_rate: Decimal | None = None
    spread_bps: Decimal | None = None
    turnover_24h_usdt: Decimal | None = None
    btc_5m_return: Decimal | None = None
    btc_true_range_atr_ratio: Decimal | None = None
    taker_fee_rate: Decimal = Decimal("0.00055")
    instrument_max_leverage: Decimal = Decimal(100)
    instrument_quantity_step: Decimal = Decimal("0.00000001")
    instrument_min_notional: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class StrategyInput:
    symbol: str
    analyzed_at: datetime
    analysis_4h: SMCAnalysis
    analysis_1h: SMCAnalysis
    analysis_15m: SMCAnalysis
    analysis_5m: SMCAnalysis
    market: StrategyMarketContext
    input_cutoffs: tuple[tuple[str, datetime], ...] = ()


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    awarded: int
    maximum: int
    evidence: str


@dataclass(frozen=True, slots=True)
class TradePlan:
    entry_lower: Decimal
    entry_upper: Decimal
    planned_entry: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    gross_reward_to_risk: Decimal
    net_reward_to_risk: Decimal
    risk_amount: Decimal
    quantity: Decimal
    notional: Decimal
    recommended_leverage: Decimal
    estimated_margin: Decimal
    estimated_entry_fee: Decimal
    estimated_exit_fee: Decimal
    estimated_loss_at_stop: Decimal
    invalidation: str


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    symbol: str
    direction: TradeDirection
    strategy_version: str
    status: CandidateStatus
    score: int
    strength: Strength
    components: tuple[ScoreComponent, ...]
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    suppression_reasons: tuple[str, ...]
    trade_plan: TradePlan | None
    analyzed_at: datetime
    expires_at: datetime
