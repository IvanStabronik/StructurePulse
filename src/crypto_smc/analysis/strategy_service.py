import asyncio
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.analysis.process_pool import AnalysisProcessPool
from crypto_smc.db.repositories.strategy import (
    StrategyRepository,
    StrategySymbolProfile,
)
from crypto_smc.observability.metrics import STRATEGY_ANALYSIS_RESULTS
from crypto_smc.providers.models import MarketTicker
from crypto_smc.providers.protocols import MarketTickerProvider
from crypto_smc.strategy import (
    StrategyConfig,
    StrategyInput,
    StrategyMarketContext,
    evaluate_candidates,
)
from smc_core import Candle, SMCAnalysis, Timeframe

logger = structlog.get_logger(__name__)
TIMEFRAMES: tuple[Timeframe, ...] = ("4h", "1h", "15m", "5m")


@dataclass(frozen=True, slots=True)
class PreparedSymbol:
    profile: StrategySymbolProfile
    candles: tuple[tuple[Candle, ...], ...]


class StrategyAnalysisService:
    def __init__(
        self,
        *,
        ticker_provider: MarketTickerProvider,
        session_factory: async_sessionmaker[AsyncSession],
        process_pool: AnalysisProcessPool,
        interval_seconds: float,
        history_candles: int,
        minimum_history_candles: int,
        readiness_event: asyncio.Event | None = None,
        config: StrategyConfig | None = None,
        repository: StrategyRepository | None = None,
    ) -> None:
        self._ticker_provider = ticker_provider
        self._session_factory = session_factory
        self._process_pool = process_pool
        self._interval_seconds = interval_seconds
        self._history_candles = history_candles
        self._minimum_history_candles = minimum_history_candles
        self._readiness_event = readiness_event
        self._config = config or StrategyConfig()
        self._repository = repository or StrategyRepository()
        self._previous_open_interest: dict[str, Decimal] = {}

    async def run(self) -> None:
        while True:
            if self._readiness_event is not None:
                await self._readiness_event.wait()
            try:
                await self.analyze_once()
            except Exception:
                STRATEGY_ANALYSIS_RESULTS.labels(result="cycle_failed").inc()
                await logger.aexception("strategy_analysis_cycle_failed")
            await asyncio.sleep(self._interval_seconds)

    async def analyze_once(self) -> dict[str, int]:
        profiles, tickers = await asyncio.gather(
            self._repository.list_active_profiles(self._session_factory),
            self._ticker_provider.list_linear_tickers(),
        )
        prepared: list[PreparedSymbol] = []
        for profile in profiles:
            candles = await asyncio.gather(
                *(
                    self._repository.load_candles(
                        self._session_factory,
                        symbol=profile.symbol,
                        timeframe=timeframe,
                        limit=self._history_candles,
                    )
                    for timeframe in TIMEFRAMES
                )
            )
            if any(len(items) < self._minimum_history_candles for items in candles):
                STRATEGY_ANALYSIS_RESULTS.labels(result="insufficient_history").inc()
                continue
            prepared.append(PreparedSymbol(profile=profile, candles=tuple(candles)))

        requests = tuple(
            (candles, self._config.smc) for item in prepared for candles in item.candles
        )
        analyses = await self._process_pool.analyze_batch(requests)
        grouped = {
            item.profile.symbol: tuple(
                analyses[index * len(TIMEFRAMES) : (index + 1) * len(TIMEFRAMES)]
            )
            for index, item in enumerate(prepared)
        }
        btc_state = self._btc_state(prepared, grouped)
        results = {"created": 0, "duplicate": 0, "skipped": 0, "failed": 0}
        for item in prepared:
            ticker = tickers.get(item.profile.symbol)
            symbol_analyses = grouped[item.profile.symbol]
            if ticker is None or len(symbol_analyses) != len(TIMEFRAMES):
                results["skipped"] += 1
                STRATEGY_ANALYSIS_RESULTS.labels(result="missing_ticker").inc()
                continue
            try:
                strategy_input = self._strategy_input(
                    item,
                    symbol_analyses,
                    ticker,
                    btc_state,
                )
                candidates = evaluate_candidates(strategy_input, self._config)
                _, created = await self._repository.save_analysis(
                    session_factory=self._session_factory,
                    strategy_input=strategy_input,
                    candidates=candidates,
                    config=self._config,
                )
            except Exception:
                results["failed"] += 1
                STRATEGY_ANALYSIS_RESULTS.labels(result="failed").inc()
                await logger.aexception(
                    "strategy_symbol_analysis_failed",
                    symbol=item.profile.symbol,
                )
            else:
                result = "created" if created else "duplicate"
                results[result] += 1
                STRATEGY_ANALYSIS_RESULTS.labels(result=result).inc()

        self._previous_open_interest = {
            symbol: ticker.open_interest for symbol, ticker in tickers.items()
        }
        await logger.ainfo("strategy_analysis_completed", **results)
        return results

    def _strategy_input(
        self,
        prepared: PreparedSymbol,
        analyses: tuple[SMCAnalysis, ...],
        ticker: MarketTicker,
        btc_state: tuple[Decimal | None, Decimal | None],
    ) -> StrategyInput:
        by_timeframe = dict(zip(TIMEFRAMES, analyses, strict=True))
        candle_by_timeframe = dict(zip(TIMEFRAMES, prepared.candles, strict=True))
        previous_oi = self._previous_open_interest.get(prepared.profile.symbol)
        oi_change = (
            (ticker.open_interest - previous_oi) / previous_oi
            if previous_oi is not None and previous_oi > 0
            else None
        )
        return StrategyInput(
            symbol=prepared.profile.symbol,
            analyzed_at=max(candles[-1].close_time for candles in prepared.candles),
            analysis_4h=by_timeframe["4h"],
            analysis_1h=by_timeframe["1h"],
            analysis_15m=by_timeframe["15m"],
            analysis_5m=by_timeframe["5m"],
            market=StrategyMarketContext(
                current_price=ticker.last_price,
                volume_ratio=_latest_volume_ratio(candle_by_timeframe["15m"]),
                open_interest_change_ratio=oi_change,
                funding_rate=ticker.funding_rate,
                spread_bps=prepared.profile.spread_bps,
                turnover_24h_usdt=prepared.profile.turnover_24h_usdt,
                btc_5m_return=btc_state[0],
                btc_true_range_atr_ratio=btc_state[1],
                instrument_max_leverage=prepared.profile.instrument_max_leverage,
                instrument_quantity_step=prepared.profile.instrument_quantity_step,
                instrument_min_notional=prepared.profile.instrument_min_notional,
            ),
            input_cutoffs=tuple(
                (timeframe, candle_by_timeframe[timeframe][-1].close_time)
                for timeframe in TIMEFRAMES
            ),
        )

    @staticmethod
    def _btc_state(
        prepared: list[PreparedSymbol],
        grouped: dict[str, tuple[SMCAnalysis, ...]],
    ) -> tuple[Decimal | None, Decimal | None]:
        btc = next(
            (item for item in prepared if item.profile.symbol == "BTCUSDT"),
            None,
        )
        btc_analyses = grouped.get("BTCUSDT")
        if btc is None or btc_analyses is None:
            return None, None
        candle_5m = btc.candles[TIMEFRAMES.index("5m")][-1]
        return_value = (
            (candle_5m.close_price - candle_5m.open_price) / candle_5m.open_price
            if candle_5m.open_price > 0
            else None
        )
        analysis_5m = btc_analyses[TIMEFRAMES.index("5m")]
        atr = next((value for value in reversed(analysis_5m.atr) if value is not None), None)
        range_ratio = candle_5m.range_size / atr if atr is not None and atr > 0 else None
        return return_value, range_ratio


def _latest_volume_ratio(candles: tuple[Candle, ...], period: int = 20) -> Decimal | None:
    if len(candles) <= period:
        return None
    baseline = sum(
        (candle.volume for candle in candles[-period - 1 : -1]),
        Decimal(0),
    ) / Decimal(period)
    return candles[-1].volume / baseline if baseline > 0 else None
