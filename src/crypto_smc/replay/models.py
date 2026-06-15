from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crypto_smc.providers.models import Candle1m
from crypto_smc.strategy import SignalCandidate

type ReplayOutcomeStatus = Literal[
    "expired",
    "invalidated_before_entry",
    "stopped",
    "stopped_after_tp1",
    "tp2",
    "ambiguous",
    "open",
]


@dataclass(frozen=True, slots=True)
class ReplayMarketRow:
    candle: Candle1m
    open_interest: Decimal | None = None
    funding_rate: Decimal | None = None
    spread_bps: Decimal | None = None
    turnover_24h_usdt: Decimal | None = None
    instrument_max_leverage: Decimal | None = None
    instrument_quantity_step: Decimal | None = None
    instrument_min_notional: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ReplayCandidate:
    sequence: int
    candidate: SignalCandidate
    input_cutoffs: tuple[tuple[str, datetime], ...]


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    candidate_sequence: int
    symbol: str
    direction: str
    status: ReplayOutcomeStatus
    entered_at: datetime | None
    resolved_at: datetime | None
    pnl: Decimal
    r_multiple: Decimal
    fees: Decimal
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    strategy_version: str
    input_rows: int
    symbols: int
    candidate_count: int
    accepted_count: int
    suppressed_count: int
    outcome_counts: dict[str, int]
    score_bands: dict[str, int]
    net_profit: Decimal
    profit_factor: Decimal | None
    maximum_drawdown: Decimal
    maximum_drawdown_fraction: Decimal
    ambiguity_count: int


@dataclass(frozen=True, slots=True)
class ReplayResult:
    candidates: tuple[ReplayCandidate, ...]
    outcomes: tuple[ReplayOutcome, ...]
    summary: ReplaySummary
