from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, cast

from crypto_smc.strategy.serialization import json_safe
from smc_core import SMCConfig


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    higher_timeframe_alignment: int = 20
    liquidity_sweep: int = 20
    structure_confirmation: int = 20
    entry_zone_quality: int = 15
    premium_discount: int = 10
    volume_open_interest: int = 10
    funding_btc_condition: int = 5

    def __post_init__(self) -> None:
        if any(value < 0 for value in asdict(self).values()):
            raise ValueError("score weights cannot be negative")
        if sum(asdict(self).values()) != 100:
            raise ValueError("score weights must sum to 100")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    version: str = "smc-v1.0.0"
    smc: SMCConfig = field(default_factory=SMCConfig)
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    require_15m_displacement: bool = True
    require_entry_zone_retest: bool = True
    ignore_active_evaluation_window: bool = False
    minimum_score: int = 70
    strong_score: int = 85
    minimum_net_reward_to_risk: Decimal = Decimal(3)
    signal_lifetime_minutes: int = 90
    reference_balance: Decimal = Decimal(10_000)
    risk_fraction: Decimal = Decimal("0.01")
    maximum_display_leverage: Decimal = Decimal(20)
    liquidation_buffer_multiplier: Decimal = Decimal("1.5")
    stop_atr_buffer: Decimal = Decimal("0.10")
    take_profit_1_r_multiple: Decimal = Decimal("1.5")
    minimum_turnover_24h_usdt: Decimal = Decimal(10_000_000)
    maximum_spread_bps: Decimal = Decimal(20)
    minimum_atr_percent: Decimal = Decimal("0.001")
    maximum_atr_percent: Decimal = Decimal("0.15")
    volume_confirmation_ratio: Decimal = Decimal(1)
    open_interest_confirmation_ratio: Decimal = Decimal(0)
    crowded_funding_rate: Decimal = Decimal("0.001")
    btc_return_warning_threshold: Decimal = Decimal("0.02")
    btc_true_range_warning_ratio: Decimal = Decimal("2.5")

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("version cannot be empty")
        if not 0 <= self.minimum_score <= 100:
            raise ValueError("minimum_score must be between 0 and 100")
        if not self.minimum_score <= self.strong_score <= 100:
            raise ValueError("strong_score must be between minimum_score and 100")
        if self.minimum_net_reward_to_risk <= 0:
            raise ValueError("minimum_net_reward_to_risk must be positive")
        if self.signal_lifetime_minutes < 1:
            raise ValueError("signal_lifetime_minutes must be positive")
        if self.reference_balance <= 0:
            raise ValueError("reference_balance must be positive")
        if not Decimal(0) < self.risk_fraction < Decimal(1):
            raise ValueError("risk_fraction must be between zero and one")
        if self.maximum_display_leverage < 1:
            raise ValueError("maximum_display_leverage must be at least one")
        if self.liquidation_buffer_multiplier <= 1:
            raise ValueError("liquidation_buffer_multiplier must exceed one")
        if self.stop_atr_buffer < 0:
            raise ValueError("stop_atr_buffer cannot be negative")

    @property
    def risk_amount(self) -> Decimal:
        return self.reference_balance * self.risk_fraction

    def parameter_snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], json_safe(asdict(self)))
