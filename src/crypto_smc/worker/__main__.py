import asyncio

from prometheus_client import start_http_server

from crypto_smc.aggregation.reconciliation import AggregationReconciliationService
from crypto_smc.aggregation.service import AggregationService
from crypto_smc.analysis import AnalysisProcessPool, StrategyAnalysisService
from crypto_smc.config import get_settings
from crypto_smc.db.repositories.strategy import StrategyRepository
from crypto_smc.db.session import create_engine, create_session_factory
from crypto_smc.market_data import LiveMarketDataService, MarketDataBackfillService
from crypto_smc.observability.logging import configure_logging
from crypto_smc.providers.bybit import BybitClient, BybitKlineWebSocketManager
from crypto_smc.providers.coingecko import CoinGeckoClient
from crypto_smc.runtime import run_until_stopped
from crypto_smc.services.universe_refresh import UniverseRefreshService
from crypto_smc.signals import SignalPolicyConfig
from crypto_smc.universe import UniversePolicyConfig


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.worker_metrics_port, addr="0.0.0.0")
    engine = create_engine(settings.database_url)
    provider = BybitClient(
        base_url=settings.bybit_base_url,
        timeout_seconds=settings.bybit_request_timeout_seconds,
        instrument_page_size=settings.bybit_instrument_page_size,
        max_requests_per_second=settings.bybit_max_requests_per_second,
        max_concurrency=settings.bybit_max_concurrency,
        max_retries=settings.bybit_max_retries,
        retry_base_seconds=settings.bybit_retry_base_seconds,
    )
    ranking_provider = CoinGeckoClient(
        base_url=settings.coingecko_base_url,
        timeout_seconds=settings.coingecko_request_timeout_seconds,
        api_key=settings.coingecko_api_key,
        api_key_type=settings.coingecko_api_key_type,
    )
    session_factory = create_session_factory(engine)
    market_data_ready = asyncio.Event()
    universe_service = UniverseRefreshService(
        instrument_provider=provider,
        ticker_provider=provider,
        ranking_provider=ranking_provider,
        session_factory=session_factory,
        ranking_fetch_limit=settings.universe_ranking_fetch_limit,
        policy_config=UniversePolicyConfig(
            size=settings.universe_size,
            min_turnover_24h_usdt=settings.universe_min_turnover_24h_usdt,
            max_spread_bps=settings.universe_max_spread_bps,
            min_trading_history_days=settings.universe_min_trading_history_days,
            manual_denylist=settings.universe_manual_denylist,
        ),
    )
    market_data_service = MarketDataBackfillService(
        provider=provider,
        session_factory=session_factory,
        initial_history_minutes=settings.market_data_initial_history_minutes,
        batch_candles=settings.market_data_backfill_batch_candles,
        max_parallel_symbols=settings.market_data_max_parallel_symbols,
    )
    stream_manager = BybitKlineWebSocketManager(
        url=settings.bybit_ws_url,
        shard_size=settings.bybit_ws_shard_size,
        queue_size=settings.bybit_ws_queue_size,
        heartbeat_seconds=settings.bybit_ws_heartbeat_seconds,
        reconnect_base_seconds=settings.bybit_ws_reconnect_base_seconds,
        reconnect_max_seconds=settings.bybit_ws_reconnect_max_seconds,
        ready_timeout_seconds=settings.bybit_ws_ready_timeout_seconds,
    )
    live_market_data = LiveMarketDataService(
        stream=stream_manager,
        backfill=market_data_service,
        universe_refresh=universe_service,
        session_factory=session_factory,
        reconciliation_interval_seconds=settings.market_data_sync_interval_seconds,
        readiness_event=market_data_ready,
    )
    aggregation_service = AggregationService(
        session_factory=session_factory,
        job_batch_size=settings.aggregation_job_batch_size,
        source_scan_batch_size=settings.aggregation_source_scan_batch_size,
        poll_interval_seconds=settings.aggregation_poll_interval_seconds,
        cpu_budget_ms=settings.aggregation_cpu_budget_ms,
        stale_job_seconds=settings.aggregation_stale_job_seconds,
    )
    aggregation_reconciliation = AggregationReconciliationService(
        provider=provider,
        session_factory=session_factory,
        interval_seconds=settings.aggregation_reconciliation_interval_seconds,
        sample_size=settings.aggregation_reconciliation_sample_size,
    )
    analysis_process_pool = AnalysisProcessPool(
        max_workers=settings.strategy_process_workers,
        max_pending_batches=settings.strategy_max_pending_batches,
    )
    strategy_analysis = StrategyAnalysisService(
        ticker_provider=provider,
        session_factory=session_factory,
        process_pool=analysis_process_pool,
        interval_seconds=settings.strategy_analysis_interval_seconds,
        history_candles=settings.strategy_history_candles,
        minimum_history_candles=settings.strategy_minimum_history_candles,
        readiness_event=market_data_ready,
        repository=StrategyRepository(
            signal_policy=SignalPolicyConfig(
                cooldown_minutes=settings.signal_cooldown_minutes,
                maximum_active_signals=settings.signal_maximum_active,
                maximum_signals_per_hour=settings.signal_maximum_per_hour,
                burst_window_minutes=settings.signal_burst_window_minutes,
                burst_maximum_signals=settings.signal_burst_maximum,
                pause_on_abnormal_btc=settings.signal_pause_on_abnormal_btc,
            )
        ),
    )

    async def run_worker() -> None:
        await asyncio.gather(
            live_market_data.run(),
            aggregation_service.run(),
            aggregation_reconciliation.run(),
            strategy_analysis.run(),
        )

    try:
        await run_until_stopped(
            run_worker,
            service_name="worker",
        )
    finally:
        await analysis_process_pool.close()
        await provider.close()
        await ranking_provider.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
