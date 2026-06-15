from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from crypto_smc.replay.aggregation import build_replay_aggregates
from crypto_smc.replay.lifecycle import resolve_candidate
from crypto_smc.replay.models import (
    ReplayCandidate,
    ReplayMarketRow,
    ReplayResult,
)
from crypto_smc.replay.reporting import build_summary
from crypto_smc.strategy import (
    StrategyConfig,
    StrategyInput,
    StrategyMarketContext,
    evaluate_candidates,
)
from smc_core import Candle, SMCAnalysis, Timeframe, analyze

TIMEFRAMES: tuple[Timeframe, ...] = ("4h", "1h", "15m", "5m")
ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    history_candles: int = 300
    minimum_history_candles: int = 30
    instrument_max_leverage: Decimal = Decimal(100)
    instrument_quantity_step: Decimal = Decimal("0.00000001")
    instrument_min_notional: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.history_candles < self.minimum_history_candles:
            raise ValueError("history_candles cannot be below minimum_history_candles")
        if self.minimum_history_candles < 1:
            raise ValueError("minimum_history_candles must be positive")
        if self.instrument_max_leverage < 1:
            raise ValueError("instrument_max_leverage must be at least one")
        if self.instrument_quantity_step <= 0:
            raise ValueError("instrument_quantity_step must be positive")
        if self.instrument_min_notional < 0:
            raise ValueError("instrument_min_notional cannot be negative")


def run_replay(
    rows: tuple[ReplayMarketRow, ...],
    *,
    strategy_config: StrategyConfig | None = None,
    replay_config: ReplayConfig | None = None,
) -> ReplayResult:
    settings = strategy_config or StrategyConfig()
    replay_settings = replay_config or ReplayConfig()
    ordered_rows = tuple(sorted(rows, key=lambda row: (row.candle.open_time, row.candle.symbol)))
    candles = tuple(row.candle for row in ordered_rows)
    aggregates = build_replay_aggregates(candles)
    grouped_rows: dict[str, list[ReplayMarketRow]] = defaultdict(list)
    for row in ordered_rows:
        grouped_rows[row.candle.symbol].append(row)
    rows_by_symbol = {
        symbol: tuple(symbol_rows) for symbol, symbol_rows in sorted(grouped_rows.items())
    }
    aggregate_close_times = {
        symbol: {
            timeframe: tuple(candle.close_time for candle in series)
            for timeframe, series in timeframe_map.items()
        }
        for symbol, timeframe_map in aggregates.items()
    }
    row_close_times = {
        symbol: tuple(item.candle.open_time + ONE_MINUTE for item in symbol_rows)
        for symbol, symbol_rows in rows_by_symbol.items()
    }
    candidates: list[ReplayCandidate] = []
    sequence = 1
    previous_open_interest: dict[str, Decimal] = {}
    btc_cache: dict[datetime, tuple[Decimal | None, Decimal | None]] = {}

    events = sorted(
        (
            candle.close_time,
            symbol,
        )
        for symbol, timeframe_map in aggregates.items()
        for candle in timeframe_map["5m"]
    )
    for clock, symbol in events:
        histories = _histories_at(
            symbol,
            clock,
            aggregates,
            aggregate_close_times,
            replay_settings,
        )
        if histories is None:
            continue
        market_row = _latest_market_row(
            rows_by_symbol[symbol],
            row_close_times[symbol],
            clock,
        )
        if market_row is None:
            continue
        analyses = {
            timeframe: analyze(histories[timeframe], settings.smc) for timeframe in TIMEFRAMES
        }
        btc_state = btc_cache.get(clock)
        if btc_state is None:
            btc_state = _btc_state_at(
                clock,
                aggregates,
                aggregate_close_times,
                replay_settings,
                settings,
            )
            btc_cache[clock] = btc_state
        open_interest_change = None
        if market_row.open_interest is not None:
            previous = previous_open_interest.get(symbol)
            if previous is not None and previous > 0:
                open_interest_change = (market_row.open_interest - previous) / previous
            previous_open_interest[symbol] = market_row.open_interest

        strategy_input = StrategyInput(
            symbol=symbol,
            analyzed_at=clock,
            analysis_4h=analyses["4h"],
            analysis_1h=analyses["1h"],
            analysis_15m=analyses["15m"],
            analysis_5m=analyses["5m"],
            market=StrategyMarketContext(
                current_price=market_row.candle.close_price,
                volume_ratio=_latest_volume_ratio(histories["15m"]),
                open_interest_change_ratio=open_interest_change,
                funding_rate=market_row.funding_rate,
                spread_bps=market_row.spread_bps,
                turnover_24h_usdt=market_row.turnover_24h_usdt,
                btc_5m_return=btc_state[0],
                btc_true_range_atr_ratio=btc_state[1],
                instrument_max_leverage=(
                    market_row.instrument_max_leverage
                    if market_row.instrument_max_leverage is not None
                    else replay_settings.instrument_max_leverage
                ),
                instrument_quantity_step=(
                    market_row.instrument_quantity_step
                    if market_row.instrument_quantity_step is not None
                    else replay_settings.instrument_quantity_step
                ),
                instrument_min_notional=(
                    market_row.instrument_min_notional
                    if market_row.instrument_min_notional is not None
                    else replay_settings.instrument_min_notional
                ),
            ),
            input_cutoffs=tuple(
                (timeframe, histories[timeframe][-1].close_time) for timeframe in TIMEFRAMES
            ),
        )
        for candidate in evaluate_candidates(strategy_input, settings):
            candidates.append(
                ReplayCandidate(
                    sequence=sequence,
                    candidate=candidate,
                    input_cutoffs=strategy_input.input_cutoffs,
                )
            )
            sequence += 1

    candidate_tuple = tuple(candidates)
    outcomes = tuple(
        resolve_candidate(item.sequence, item.candidate, rows_by_symbol[item.candidate.symbol])
        for item in candidate_tuple
        if item.candidate.status == "accepted" and item.candidate.trade_plan is not None
    )
    summary = build_summary(
        config=settings,
        input_rows=len(ordered_rows),
        symbol_count=len(rows_by_symbol),
        candidates=candidate_tuple,
        outcomes=outcomes,
    )
    return ReplayResult(
        candidates=candidate_tuple,
        outcomes=outcomes,
        summary=summary,
    )


def _histories_at(
    symbol: str,
    clock: datetime,
    aggregates: dict[str, dict[Timeframe, tuple[Candle, ...]]],
    close_times: dict[str, dict[Timeframe, tuple[datetime, ...]]],
    config: ReplayConfig,
) -> dict[Timeframe, tuple[Candle, ...]] | None:
    histories: dict[Timeframe, tuple[Candle, ...]] = {}
    for timeframe in TIMEFRAMES:
        end = bisect_right(close_times[symbol][timeframe], clock)
        start = max(0, end - config.history_candles)
        history = aggregates[symbol][timeframe][start:end]
        if len(history) < config.minimum_history_candles:
            return None
        histories[timeframe] = history
    return histories


def _latest_market_row(
    rows: tuple[ReplayMarketRow, ...],
    close_times: tuple[datetime, ...],
    clock: datetime,
) -> ReplayMarketRow | None:
    index = bisect_right(close_times, clock) - 1
    return rows[index] if index >= 0 else None


def _btc_state_at(
    clock: datetime,
    aggregates: dict[str, dict[Timeframe, tuple[Candle, ...]]],
    close_times: dict[str, dict[Timeframe, tuple[datetime, ...]]],
    replay_config: ReplayConfig,
    strategy_config: StrategyConfig,
) -> tuple[Decimal | None, Decimal | None]:
    if "BTCUSDT" not in aggregates:
        return None, None
    histories = _histories_at(
        "BTCUSDT",
        clock,
        aggregates,
        close_times,
        replay_config,
    )
    if histories is None:
        return None, None
    candle = histories["5m"][-1]
    btc_return = (
        (candle.close_price - candle.open_price) / candle.open_price
        if candle.open_price > 0
        else None
    )
    analysis = analyze(histories["5m"], strategy_config.smc)
    atr = _latest_atr(analysis)
    range_ratio = candle.range_size / atr if atr is not None and atr > 0 else None
    return btc_return, range_ratio


def _latest_atr(analysis: SMCAnalysis) -> Decimal | None:
    return next((value for value in reversed(analysis.atr) if value is not None), None)


def _latest_volume_ratio(candles: tuple[Candle, ...], period: int = 20) -> Decimal | None:
    if len(candles) <= period:
        return None
    baseline = sum(
        (candle.volume for candle in candles[-period - 1 : -1]),
        Decimal(0),
    ) / Decimal(period)
    return candles[-1].volume / baseline if baseline > 0 else None
