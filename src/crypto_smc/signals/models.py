from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SignalPolicyConfig:
    cooldown_minutes: int = 60
    maximum_active_signals: int = 5
    maximum_signals_per_hour: int = 10
    burst_window_minutes: int = 5
    burst_maximum_signals: int = 3
    pause_on_abnormal_btc: bool = True

    def __post_init__(self) -> None:
        if self.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes cannot be negative")
        if self.maximum_active_signals < 1:
            raise ValueError("maximum_active_signals must be positive")
        if self.maximum_signals_per_hour < 1:
            raise ValueError("maximum_signals_per_hour must be positive")
        if self.burst_window_minutes < 1:
            raise ValueError("burst_window_minutes must be positive")
        if self.burst_maximum_signals < 1:
            raise ValueError("burst_maximum_signals must be positive")


@dataclass(frozen=True, slots=True)
class SignalObservation:
    symbol: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    allowed: bool
    reason: str | None = None
