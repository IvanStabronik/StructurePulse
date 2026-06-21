import asyncio
from decimal import Decimal

from crypto_smc.aggregation.reconciliation import AggregationReconciliationService
from crypto_smc.aggregation.service import AggregationService
from crypto_smc.analysis import AnalysisProcessPool, StrategyAnalysisService
from crypto_smc.config import get_settings
from crypto_smc.db.repositories.strategy import StrategyRepository
from crypto_smc.db.session import create_engine, create_session_factory
from crypto_smc.execution.service import LiveExecutionService
from crypto_smc.market_data import LiveMarketDataService, MarketDataBackfillService
from crypto_smc.observability.logging import configure_logging
from crypto_smc.observability.runtime import EventLoopMonitor, WorkerRuntimeState
from crypto_smc.observability.worker_health import WorkerHealthServer
from crypto_smc.providers.bybit import (
    BybitClient,
    BybitKlineWebSocketManager,
    BybitPrivateClient,
    BybitPublicTradeWebSocketManager,
)
from crypto_smc.providers.coingecko import CoinGeckoClient
from crypto_smc.runtime import run_until_stopped
from crypto_smc.services.maintenance import MaintenanceService
from crypto_smc.services.operational_warnings import OperationalWarningService
from crypto_smc.services.universe_refresh import UniverseRefreshService
from crypto_smc.signals import SignalPolicyConfig
from crypto_smc.signals.service import SignalLifecycleService
from crypto_smc.strategy import StrategyConfig
from crypto_smc.universe import UniversePolicyConfig


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
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
    runtime = WorkerRuntimeState(market_data_ready=market_data_ready)
    event_loop_monitor = EventLoopMonitor(
        interval_seconds=settings.event_loop_probe_interval_seconds,
        warning_seconds=settings.event_loop_warning_seconds,
        runtime=runtime,
    )
    health_server = WorkerHealthServer(
        engine=engine,
        runtime=runtime,
        port=settings.worker_metrics_port,
        required_database_revision=settings.required_database_revision,
        dependency_timeout_seconds=settings.worker_health_timeout_seconds,
    )
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
    strategy_config = _strategy_config(
        settings.strategy_profile,
        live_risk_usdt=(
            settings.execution_risk_usdt
            if settings.execution_enabled and settings.execution_mode == "auto"
            else None
        ),
    )
    strategy_analysis = StrategyAnalysisService(
        ticker_provider=provider,
        session_factory=session_factory,
        process_pool=analysis_process_pool,
        interval_seconds=settings.strategy_analysis_interval_seconds,
        history_candles=settings.strategy_history_candles,
        minimum_history_candles=settings.strategy_minimum_history_candles,
        readiness_event=market_data_ready,
        config=strategy_config,
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
    public_trade_stream = BybitPublicTradeWebSocketManager(
        url=settings.bybit_ws_url,
        queue_size=settings.signal_trade_queue_size,
        buffer_size=settings.signal_trade_buffer_size,
        heartbeat_seconds=settings.bybit_ws_heartbeat_seconds,
        reconnect_base_seconds=settings.bybit_ws_reconnect_base_seconds,
        reconnect_max_seconds=settings.bybit_ws_reconnect_max_seconds,
        ready_timeout_seconds=settings.bybit_ws_ready_timeout_seconds,
    )
    execution_client: BybitPrivateClient | None = None
    live_execution: LiveExecutionService | None = None
    if settings.execution_enabled and settings.execution_mode == "auto":
        if not settings.bybit_api_key or not settings.bybit_api_secret:
            raise RuntimeError("Live execution is enabled but Bybit API credentials are missing")
        execution_client = BybitPrivateClient(
            base_url=settings.bybit_base_url,
            api_key=settings.bybit_api_key,
            api_secret=settings.bybit_api_secret,
            timeout_seconds=settings.bybit_request_timeout_seconds,
            recv_window_ms=settings.bybit_recv_window_ms,
            max_requests_per_second=settings.bybit_max_requests_per_second,
            max_concurrency=settings.bybit_max_concurrency,
        )
        live_execution = LiveExecutionService(
            client=execution_client,
            session_factory=session_factory,
            risk_usdt=settings.execution_risk_usdt,
            leverage=settings.execution_leverage,
            min_risk_usdt=settings.execution_min_risk_usdt,
            max_effective_leverage=settings.execution_max_effective_leverage,
            max_slippage_bps=settings.execution_max_slippage_bps,
            max_open_positions=settings.execution_max_open_positions,
            max_trades_per_day=settings.execution_max_trades_per_day,
            max_daily_loss_usdt=settings.execution_max_daily_loss_usdt,
            poll_interval_seconds=settings.execution_poll_interval_seconds,
            ticker_provider=provider,
        )
    signal_lifecycle = SignalLifecycleService(
        provider=provider,
        stream=public_trade_stream,
        session_factory=session_factory,
        poll_interval_seconds=settings.signal_trade_poll_interval_seconds,
        recent_trade_limit=settings.signal_trade_recent_limit,
        checkpoint_interval_seconds=(settings.signal_trade_checkpoint_interval_seconds),
        live_execution=live_execution,
    )
    maintenance = MaintenanceService(
        session_factory=session_factory,
        interval_seconds=settings.maintenance_interval_seconds,
        candle_1m_retention_days=settings.maintenance_candle_1m_retention_days,
        candle_agg_retention_days=settings.maintenance_candle_agg_retention_days,
        batch_size=settings.maintenance_delete_batch_size,
    )
    operational_warnings = OperationalWarningService(
        session_factory=session_factory,
        runtime=runtime,
        interval_seconds=settings.operational_monitor_interval_seconds,
        warning_delay_seconds=settings.operational_warning_delay_seconds,
        cooldown_seconds=settings.operational_warning_cooldown_seconds,
    )

    async def run_worker() -> None:
        tasks = [
            live_market_data.run(),
            aggregation_service.run(),
            aggregation_reconciliation.run(),
            strategy_analysis.run(),
            signal_lifecycle.run(),
            event_loop_monitor.run(),
            maintenance.run(),
            operational_warnings.run(),
        ]
        if live_execution is not None:
            tasks.append(live_execution.run())
        await asyncio.gather(*tasks)

    await health_server.start()
    try:
        await run_until_stopped(
            run_worker,
            service_name="worker",
            quiesce=runtime.begin_quiescence,
            quiesce_seconds=settings.runtime_quiesce_seconds,
            shutdown_timeout_seconds=settings.runtime_shutdown_timeout_seconds,
        )
    finally:
        await health_server.close()
        await analysis_process_pool.close()
        await provider.close()
        if execution_client is not None:
            await execution_client.close()
        await ranking_provider.close()
        await engine.dispose()


def _strategy_config(profile: str, *, live_risk_usdt: Decimal | None = None) -> StrategyConfig:
    if profile == "aggressive_test":
        risk_amount = live_risk_usdt or StrategyConfig().risk_amount
        risk_fraction = Decimal("0.01")
        return StrategyConfig(
            version=f"smc-v1.1.1-aggressive-test-risk-{_version_decimal(risk_amount)}",
            require_15m_displacement=False,
            require_entry_zone_retest=False,
            ignore_active_evaluation_window=True,
            reference_balance=risk_amount / risk_fraction,
            risk_fraction=risk_fraction,
        )
    return StrategyConfig()


def _version_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f").replace(".", "p")


if __name__ == "__main__":
    asyncio.run(main())
