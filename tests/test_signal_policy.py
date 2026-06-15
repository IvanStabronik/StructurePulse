from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_smc.signals import (
    SignalObservation,
    SignalPolicyConfig,
    evaluate_publication,
    transition_signal,
    transition_virtual_trade,
)
from crypto_smc.strategy import SignalCandidate, TradePlan

NOW = datetime(2026, 6, 15, 10, tzinfo=UTC)


def candidate(
    *,
    symbol: str = "BTCUSDT",
    warnings: tuple[str, ...] = (),
) -> SignalCandidate:
    return SignalCandidate(
        symbol=symbol,
        direction="long",
        strategy_version="smc-v1.0.0",
        status="accepted",
        score=90,
        strength="strong",
        components=(),
        evidence=(),
        warnings=warnings,
        suppression_reasons=(),
        trade_plan=TradePlan(
            entry_lower=Decimal(99),
            entry_upper=Decimal(101),
            planned_entry=Decimal(100),
            stop_loss=Decimal(95),
            take_profit_1=Decimal(105),
            take_profit_2=Decimal(110),
            gross_reward_to_risk=Decimal(3),
            net_reward_to_risk=Decimal(3),
            risk_amount=Decimal(100),
            quantity=Decimal(20),
            notional=Decimal(2000),
            recommended_leverage=Decimal(5),
            estimated_margin=Decimal(400),
            estimated_entry_fee=Decimal(1),
            estimated_exit_fee=Decimal(1),
            estimated_loss_at_stop=Decimal(100),
            invalidation="close below 95",
        ),
        analyzed_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(minutes=85),
    )


def observation(
    symbol: str,
    status: str,
    *,
    minutes_ago: int,
) -> SignalObservation:
    return SignalObservation(
        symbol=symbol,
        status=status,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


def test_policy_allows_first_healthy_candidate() -> None:
    decision = evaluate_publication(
        candidate(),
        (),
        now=NOW,
        config=SignalPolicyConfig(),
    )

    assert decision.allowed is True
    assert decision.reason is None


@pytest.mark.parametrize(
    ("candidate_symbol", "observations", "expected"),
    [
        (
            "BTCUSDT",
            (observation("BTCUSDT", "active", minutes_ago=120),),
            "active_signal_exists",
        ),
        (
            "BTCUSDT",
            (observation("BTCUSDT", "stopped", minutes_ago=30),),
            "symbol_cooldown",
        ),
        (
            "NEWUSDT",
            tuple(observation(f"COIN{index}USDT", "active", minutes_ago=120) for index in range(5)),
            "portfolio_active_limit",
        ),
        (
            "NEWUSDT",
            tuple(
                observation(f"COIN{index}USDT", "stopped", minutes_ago=30) for index in range(10)
            ),
            "portfolio_hourly_limit",
        ),
        (
            "NEWUSDT",
            tuple(observation(f"COIN{index}USDT", "stopped", minutes_ago=2) for index in range(3)),
            "portfolio_burst_limit",
        ),
    ],
)
def test_policy_enforces_protection_limits(
    candidate_symbol: str,
    observations: tuple[SignalObservation, ...],
    expected: str,
) -> None:
    decision = evaluate_publication(
        candidate(symbol=candidate_symbol),
        observations,
        now=NOW,
        config=SignalPolicyConfig(),
    )

    assert decision.allowed is False
    assert decision.reason == expected


def test_btc_circuit_breaker_can_be_explicitly_disabled() -> None:
    source = candidate(warnings=("abnormal_btc_movement",))

    paused = evaluate_publication(source, (), now=NOW, config=SignalPolicyConfig())
    allowed = evaluate_publication(
        source,
        (),
        now=NOW,
        config=replace(SignalPolicyConfig(), pause_on_abnormal_btc=False),
    )

    assert paused.reason == "btc_circuit_breaker"
    assert allowed.allowed is True


def test_signal_state_machine_accepts_only_declared_transitions() -> None:
    assert transition_signal("preparing", "active") == "active"
    assert transition_signal("active", "entered") == "entered"
    assert transition_signal("entered", "tp1_reached") == "tp1_reached"
    assert transition_signal("tp1_reached", "tp2_completed") == "tp2_completed"

    with pytest.raises(ValueError, match="Invalid signal transition"):
        transition_signal("preparing", "tp2_completed")
    with pytest.raises(ValueError, match="Invalid signal transition"):
        transition_signal("stopped", "active")


def test_virtual_trade_state_machine_is_conservative() -> None:
    assert transition_virtual_trade("waiting_entry", "ambiguous") == "ambiguous"
    assert transition_signal("active", "ambiguous") == "ambiguous"
    assert transition_virtual_trade("tp1_reached", "stopped_at_breakeven") == "stopped_at_breakeven"

    with pytest.raises(ValueError, match="Invalid virtual trade transition"):
        transition_virtual_trade("waiting_entry", "tp2_completed")
