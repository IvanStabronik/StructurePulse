import csv
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crypto_smc.providers.models import Candle1m
from crypto_smc.replay.aggregation import build_replay_aggregates
from crypto_smc.replay.lifecycle import resolve_candidate
from crypto_smc.replay.loader import load_replay_csv
from crypto_smc.replay.models import ReplayCandidate, ReplayMarketRow, ReplayOutcome
from crypto_smc.replay.reporting import build_summary, write_reports
from crypto_smc.replay.runner import ReplayConfig, run_replay
from crypto_smc.strategy import SignalCandidate, StrategyConfig, TradePlan
from crypto_smc.strategy.models import StrategyInput

START = datetime(2026, 5, 1, tzinfo=UTC)


def market_row(
    minute: int,
    *,
    symbol: str = "BTCUSDT",
    open_price: str = "100",
    high_price: str = "101",
    low_price: str = "99",
    close_price: str = "100",
) -> ReplayMarketRow:
    return ReplayMarketRow(
        candle=Candle1m(
            symbol=symbol,
            open_time=START + timedelta(minutes=minute),
            open_price=Decimal(open_price),
            high_price=Decimal(high_price),
            low_price=Decimal(low_price),
            close_price=Decimal(close_price),
            volume=Decimal(10),
            turnover=Decimal(1000),
        ),
        open_interest=Decimal(1_000_000 + minute),
        funding_rate=Decimal("0.0001"),
        spread_bps=Decimal(2),
        turnover_24h_usdt=Decimal(100_000_000),
    )


def trade_plan() -> TradePlan:
    return TradePlan(
        entry_lower=Decimal(99),
        entry_upper=Decimal(101),
        planned_entry=Decimal(100),
        stop_loss=Decimal(95),
        take_profit_1=Decimal(105),
        take_profit_2=Decimal(110),
        gross_reward_to_risk=Decimal(3),
        net_reward_to_risk=Decimal("2.9"),
        risk_amount=Decimal(100),
        quantity=Decimal(20),
        notional=Decimal(2000),
        recommended_leverage=Decimal(5),
        estimated_margin=Decimal(400),
        estimated_entry_fee=Decimal(1),
        estimated_exit_fee=Decimal(1),
        estimated_loss_at_stop=Decimal(100),
        invalidation="Close below 95",
    )


def accepted_candidate() -> SignalCandidate:
    return SignalCandidate(
        symbol="BTCUSDT",
        direction="long",
        strategy_version="test",
        status="accepted",
        score=90,
        strength="strong",
        components=(),
        evidence=("fixture",),
        warnings=(),
        suppression_reasons=(),
        trade_plan=trade_plan(),
        analyzed_at=START,
        expires_at=START + timedelta(minutes=90),
    )


def suppressed_candidate(strategy_input: StrategyInput) -> SignalCandidate:
    return SignalCandidate(
        symbol=strategy_input.symbol,
        direction="long",
        strategy_version="test",
        status="suppressed",
        score=int(strategy_input.market.current_price),
        strength="standard",
        components=(),
        evidence=(),
        warnings=(),
        suppression_reasons=("fixture",),
        trade_plan=None,
        analyzed_at=strategy_input.analyzed_at,
        expires_at=strategy_input.analyzed_at + timedelta(minutes=90),
    )


def write_market_rows(path: Path, rows: tuple[ReplayMarketRow, ...]) -> None:
    fields = (
        "symbol",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            candle = row.candle
            writer.writerow(
                {
                    "symbol": candle.symbol,
                    "open_time": candle.open_time.isoformat(),
                    "open": candle.open_price,
                    "high": candle.high_price,
                    "low": candle.low_price,
                    "close": candle.close_price,
                    "volume": candle.volume,
                    "turnover": candle.turnover,
                }
            )


def test_csv_loader_sorts_rows_and_rejects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "candles.csv"
    fields = (
        "symbol",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for minute in (1, 0):
            writer.writerow(
                {
                    "symbol": "btcusdt",
                    "open_time": int((START + timedelta(minutes=minute)).timestamp() * 1000),
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100",
                    "volume": "10",
                    "turnover": "1000",
                }
            )

    rows = load_replay_csv(path)

    assert tuple(item.candle.open_time for item in rows) == (
        START,
        START + timedelta(minutes=1),
    )
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writerow(
            {
                "symbol": "BTCUSDT",
                "open_time": int(START.timestamp() * 1000),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "10",
                "turnover": "1000",
            }
        )

    with pytest.raises(ValueError, match="Duplicate 1m candle"):
        load_replay_csv(path)


def test_replay_aggregation_withholds_incomplete_intervals() -> None:
    candles = tuple(market_row(minute).candle for minute in range(245))

    aggregates = build_replay_aggregates(candles)

    assert len(aggregates["BTCUSDT"]["4h"]) == 1
    assert aggregates["BTCUSDT"]["4h"][0].close_time == START + timedelta(hours=4)
    assert len(aggregates["BTCUSDT"]["5m"]) == 49


def test_lifecycle_resolves_same_minute_stop_and_target_conservatively() -> None:
    rows = (
        market_row(0, open_price="102", high_price="102", low_price="100", close_price="101"),
        market_row(1, open_price="101", high_price="111", low_price="94", close_price="108"),
    )

    outcome = resolve_candidate(1, accepted_candidate(), rows)

    assert outcome.status == "ambiguous"
    assert outcome.ambiguous is True
    assert outcome.pnl == Decimal(-100)
    assert outcome.r_multiple == Decimal(-1)


def test_lifecycle_tracks_tp1_then_fee_adjusted_breakeven() -> None:
    rows = (
        market_row(0, open_price="102", high_price="102", low_price="100", close_price="101"),
        market_row(1, open_price="101", high_price="106", low_price="100.5", close_price="105"),
        market_row(2, open_price="105", high_price="106", low_price="100", close_price="101"),
    )

    outcome = resolve_candidate(1, accepted_candidate(), rows)

    assert outcome.status == "stopped_after_tp1"
    assert outcome.ambiguous is False
    assert outcome.pnl > 0


def test_lifecycle_invalidates_gap_beyond_stop_before_entry() -> None:
    rows = (market_row(0, open_price="94", high_price="96", low_price="93", close_price="95"),)

    outcome = resolve_candidate(1, accepted_candidate(), rows)

    assert outcome.status == "invalidated_before_entry"
    assert outcome.entered_at is None
    assert outcome.pnl == 0


def test_runner_does_not_use_future_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crypto_smc.replay import runner

    monkeypatch.setattr(
        runner,
        "evaluate_candidates",
        lambda strategy_input, _config: (suppressed_candidate(strategy_input),),
    )
    prefix = tuple(market_row(minute) for minute in range(300))
    future = tuple(
        market_row(
            minute,
            open_price="1000",
            high_price="1001",
            low_price="999",
            close_price="1000",
        )
        for minute in range(300, 320)
    )
    config = ReplayConfig(history_candles=100, minimum_history_candles=1)

    prefix_result = run_replay(prefix, replay_config=config)
    reversed_result = run_replay(tuple(reversed(prefix)), replay_config=config)
    full_result = run_replay(prefix + future, replay_config=config)
    prefix_end = prefix[-1].candle.open_time + timedelta(minutes=1)
    comparable = tuple(
        item for item in full_result.candidates if item.candidate.analyzed_at <= prefix_end
    )

    assert comparable == prefix_result.candidates
    assert reversed_result == prefix_result
    assert all(
        cutoff <= item.candidate.analyzed_at
        for item in full_result.candidates
        for _, cutoff in item.input_cutoffs
    )


def test_reports_are_reproducible(tmp_path: Path) -> None:
    candidate = accepted_candidate()
    rows = (
        market_row(0, open_price="102", high_price="102", low_price="100", close_price="101"),
        market_row(1, open_price="101", high_price="106", low_price="100", close_price="105"),
        market_row(2, open_price="105", high_price="111", low_price="104", close_price="110"),
    )
    outcome = resolve_candidate(1, candidate, rows)
    config = StrategyConfig()
    result = run_replay((), strategy_config=config)
    summary = replace(
        result.summary,
        input_rows=len(rows),
        symbols=1,
        candidate_count=1,
        accepted_count=1,
        outcome_counts={outcome.status: 1},
        net_profit=outcome.pnl,
    )
    first = tmp_path / "first"
    second = tmp_path / "second"

    replay_candidate = ReplayCandidate(sequence=1, candidate=candidate, input_cutoffs=())
    write_reports(
        first,
        config=config,
        candidates=(replay_candidate,),
        outcomes=(outcome,),
        summary=summary,
    )
    write_reports(
        second,
        config=config,
        candidates=(replay_candidate,),
        outcomes=(outcome,),
        summary=summary,
    )

    for name in ("report.json", "candidates.csv", "outcomes.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_summary_counts_ambiguous_losses_conservatively() -> None:
    outcomes = (
        ReplayOutcome(
            candidate_sequence=1,
            symbol="BTCUSDT",
            direction="long",
            status="tp2",
            entered_at=START,
            resolved_at=START + timedelta(minutes=10),
            pnl=Decimal(200),
            r_multiple=Decimal(2),
            fees=Decimal(2),
            ambiguous=False,
        ),
        ReplayOutcome(
            candidate_sequence=2,
            symbol="ETHUSDT",
            direction="short",
            status="ambiguous",
            entered_at=START,
            resolved_at=START + timedelta(minutes=20),
            pnl=Decimal(-100),
            r_multiple=Decimal(-1),
            fees=Decimal(1),
            ambiguous=True,
        ),
    )

    summary = build_summary(
        config=StrategyConfig(),
        input_rows=100,
        symbol_count=2,
        candidates=(),
        outcomes=outcomes,
    )

    assert summary.net_profit == Decimal(100)
    assert summary.profit_factor == Decimal(2)
    assert summary.ambiguity_count == 1
    assert summary.maximum_drawdown == Decimal(100)


def test_summary_uses_configured_score_bands() -> None:
    config = replace(StrategyConfig(), minimum_score=60, strong_score=80)
    candidate = replace(
        accepted_candidate(),
        status="suppressed",
        score=75,
        strength="standard",
        trade_plan=None,
    )

    summary = build_summary(
        config=config,
        input_rows=1,
        symbol_count=1,
        candidates=(ReplayCandidate(1, candidate, ()),),
        outcomes=(),
    )

    assert summary.score_bands == {"60-79": 1}


def test_cli_writes_all_report_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from crypto_smc.replay.__main__ import main

    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "reports"
    write_market_rows(
        input_path,
        tuple(market_row(minute) for minute in range(245)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto_smc.replay",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_path),
            "--history-candles",
            "10",
            "--minimum-history-candles",
            "1",
        ],
    )

    main()

    assert {path.name for path in output_path.iterdir()} == {
        "report.json",
        "candidates.csv",
        "outcomes.csv",
    }
    printed_summary = json.loads(capsys.readouterr().out)
    assert printed_summary["input_rows"] == 245
