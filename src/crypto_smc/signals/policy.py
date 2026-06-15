from datetime import datetime, timedelta

from crypto_smc.signals.models import (
    PublicationDecision,
    SignalObservation,
    SignalPolicyConfig,
)
from crypto_smc.strategy import SignalCandidate

ACTIVE_SIGNAL_STATUSES = frozenset({"preparing", "active", "entered", "tp1_reached"})
PUBLISHED_SIGNAL_STATUSES = ACTIVE_SIGNAL_STATUSES | frozenset(
    {
        "expired",
        "invalidated",
        "stopped",
        "stopped_at_breakeven",
        "tp2_completed",
        "ambiguous",
        "coverage_failed",
    }
)


def evaluate_publication(
    candidate: SignalCandidate,
    observations: tuple[SignalObservation, ...],
    *,
    now: datetime,
    config: SignalPolicyConfig,
) -> PublicationDecision:
    if candidate.status != "accepted" or candidate.trade_plan is None:
        return PublicationDecision(False, "candidate_not_accepted")
    if candidate.expires_at <= now:
        return PublicationDecision(False, "candidate_expired")
    if config.pause_on_abnormal_btc and "abnormal_btc_movement" in candidate.warnings:
        return PublicationDecision(False, "btc_circuit_breaker")

    published = tuple(item for item in observations if item.status in PUBLISHED_SIGNAL_STATUSES)
    active = tuple(item for item in published if item.status in ACTIVE_SIGNAL_STATUSES)
    if any(item.symbol == candidate.symbol for item in active):
        return PublicationDecision(False, "active_signal_exists")

    cooldown_start = now - timedelta(minutes=config.cooldown_minutes)
    if any(
        item.symbol == candidate.symbol and item.created_at > cooldown_start for item in published
    ):
        return PublicationDecision(False, "symbol_cooldown")
    if len(active) >= config.maximum_active_signals:
        return PublicationDecision(False, "portfolio_active_limit")

    hour_start = now - timedelta(hours=1)
    if sum(item.created_at > hour_start for item in published) >= config.maximum_signals_per_hour:
        return PublicationDecision(False, "portfolio_hourly_limit")

    burst_start = now - timedelta(minutes=config.burst_window_minutes)
    if sum(item.created_at > burst_start for item in published) >= config.burst_maximum_signals:
        return PublicationDecision(False, "portfolio_burst_limit")
    return PublicationDecision(True)
