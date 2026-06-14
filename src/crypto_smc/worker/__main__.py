import asyncio

from crypto_smc.config import get_settings
from crypto_smc.db.session import create_engine, create_session_factory
from crypto_smc.market_data import MarketDataBackfillService
from crypto_smc.observability.logging import configure_logging
from crypto_smc.providers.bybit import BybitClient
from crypto_smc.providers.coingecko import CoinGeckoClient
from crypto_smc.runtime import run_periodic
from crypto_smc.services.universe_refresh import UniverseRefreshService
from crypto_smc.services.worker_cycle import WorkerCycle
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
    worker_cycle = WorkerCycle(
        universe_refresh=universe_service,
        market_data_sync=market_data_service,
    )

    try:
        await run_periodic(
            worker_cycle.run_once,
            interval_seconds=settings.market_data_sync_interval_seconds,
            service_name="worker",
        )
    finally:
        await provider.close()
        await ranking_provider.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
