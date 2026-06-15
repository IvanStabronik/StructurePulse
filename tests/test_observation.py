from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from crypto_smc.api.main import create_app
from crypto_smc.config import Settings
from crypto_smc.observation import (
    EvaluationReport,
    EvaluationWindow,
    TradeObservation,
    build_evaluation_report,
)
from crypto_smc.observation.__main__ import parser
from tests.test_api import FakeEngine, FakeInstrumentProvider

START = datetime(2026, 6, 15, 7, tzinfo=UTC)


def window(*, minimum_completed_signals: int = 100) -> EvaluationWindow:
    return EvaluationWindow(
        id=1,
        name="live-2026-06",
        strategy_version="smc-v1.0.0",
        status="active",
        started_at=START,
        ended_at=None,
        minimum_completed_signals=minimum_completed_signals,
        minimum_profit_factor=Decimal("1.3"),
        maximum_drawdown_fraction=Decimal("0.15"),
        maximum_symbol_share=Decimal("0.35"),
        reference_balance=Decimal(10_000),
    )


def trade(
    sequence: int,
    *,
    symbol: str,
    pnl: str,
    score: int = 80,
    direction: str = "long",
    entered: bool = True,
    ambiguous: bool = False,
) -> TradeObservation:
    created_at = START + timedelta(minutes=sequence)
    pnl_value = Decimal(pnl)
    return TradeObservation(
        signal_id=sequence,
        symbol=symbol,
        direction=direction,
        score=score,
        status="ambiguous" if ambiguous else "tp2_completed",
        created_at=created_at,
        entered_at=created_at if entered else None,
        resolved_at=created_at + timedelta(minutes=5),
        realized_pnl=pnl_value,
        fees=Decimal("1.25") if entered else Decimal(0),
        estimated_funding=Decimal("0.10") if entered else Decimal(0),
        r_multiple=pnl_value / Decimal(100),
        ambiguous=ambiguous,
    )


def test_report_accounts_for_costs_ambiguity_and_no_entry() -> None:
    report = build_evaluation_report(
        window=window(),
        trades=(
            trade(1, symbol="BTCUSDT", pnl="200", score=90),
            trade(
                2,
                symbol="ETHUSDT",
                pnl="-100",
                direction="short",
                ambiguous=True,
            ),
            trade(3, symbol="SOLUSDT", pnl="0", score=65, entered=False),
        ),
        suppression_reasons={"cooldown": 2},
        unresolved_data_gaps=1,
        coverage_failures=0,
        generated_at=START + timedelta(hours=1),
    )

    assert report.overall.completed == 3
    assert report.overall.entered == 2
    assert report.overall.not_entered == 1
    assert report.overall.breakeven == 0
    assert report.overall.ambiguous == 1
    assert report.overall.net_profit == Decimal(100)
    assert report.overall.profit_factor == Decimal(2)
    assert report.overall.fees == Decimal("2.50")
    assert report.maximum_drawdown == Decimal(100)
    assert report.by_score_band["85-100"].wins == 1
    assert report.by_direction["short"].losses == 1
    assert report.suppression_reasons == {"cooldown": 2}
    assert report.readiness.verdict == "insufficient_sample"
    assert report.readiness.eligible_for_execution_review is False


def test_start_command_defaults_to_production_strategy_version() -> None:
    args = parser().parse_args(("start", "--name", "live"))

    assert args.strategy_version == "smc-v1.0.0"


def test_report_marks_diversified_positive_sample_for_manual_review() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    trades = tuple(
        trade(
            sequence,
            symbol=symbols[sequence % len(symbols)],
            pnl="-50" if sequence % 4 == 3 else "100",
            score=75 if sequence % 2 else 90,
            direction="short" if sequence % 2 else "long",
        )
        for sequence in range(1, 101)
    )

    report = build_evaluation_report(
        window=window(),
        trades=trades,
        suppression_reasons={},
        unresolved_data_gaps=0,
        coverage_failures=0,
    )

    assert report.overall.completed == 100
    assert report.overall.expectancy > 0
    assert report.overall.profit_factor is not None
    assert report.overall.profit_factor > Decimal("1.3")
    assert report.maximum_drawdown_fraction < Decimal("0.15")
    assert report.maximum_symbol_share == Decimal("0.25")
    assert all(report.readiness.checks.values())
    assert report.readiness.verdict == "ready_for_manual_review"
    assert report.readiness.eligible_for_execution_review is True


class FakeObservationRepository:
    def __init__(self, report: EvaluationReport) -> None:
        self.report_value = report

    async def current_window(self, _: object) -> EvaluationWindow:
        return self.report_value.window

    async def report(
        self,
        _: object,
        *,
        window_name: str | None = None,
    ) -> EvaluationReport:
        assert window_name == "live-2026-06"
        return self.report_value


@pytest.mark.asyncio
async def test_observation_api_exposes_current_window_and_report() -> None:
    report = build_evaluation_report(
        window=window(minimum_completed_signals=1),
        trades=(trade(1, symbol="BTCUSDT", pnl="200"),),
        suppression_reasons={},
        unresolved_data_gaps=0,
        coverage_failures=0,
        generated_at=START + timedelta(hours=1),
    )
    app = create_app(
        Settings(app_env="test"),
        instrument_provider=FakeInstrumentProvider(),
        engine=FakeEngine(),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(report),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        current_response = await client.get("/observation/current")
        report_response = await client.get(
            "/observation/report",
            params={"name": "live-2026-06"},
        )

    assert current_response.status_code == 200
    assert current_response.json()["strategy_version"] == "smc-v1.0.0"
    assert report_response.status_code == 200
    assert report_response.json()["overall"]["net_profit"] == "200"
    assert report_response.json()["readiness"]["verdict"] == "criteria_not_met"
