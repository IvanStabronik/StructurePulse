import asyncio

from crypto_smc.config import get_settings
from crypto_smc.db.session import create_engine, create_session_factory
from crypto_smc.observability.logging import configure_logging
from crypto_smc.providers.bybit import BybitClient
from crypto_smc.providers.coingecko import CoinGeckoClient
from crypto_smc.runtime import run_periodic
from crypto_smc.services.universe_refresh import UniverseRefreshService
from crypto_smc.universe import UniversePolicyConfig


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    provider = BybitClient(
        base_url=settings.bybit_base_url,
        timeout_seconds=settings.bybit_request_timeout_seconds,
        instrument_page_size=settings.bybit_instrument_page_size,
    )
    ranking_provider = CoinGeckoClient(
        base_url=settings.coingecko_base_url,
        timeout_seconds=settings.coingecko_request_timeout_seconds,
        api_key=settings.coingecko_api_key,
        api_key_type=settings.coingecko_api_key_type,
    )
    service = UniverseRefreshService(
        instrument_provider=provider,
        ticker_provider=provider,
        ranking_provider=ranking_provider,
        session_factory=create_session_factory(engine),
        ranking_fetch_limit=settings.universe_ranking_fetch_limit,
        policy_config=UniversePolicyConfig(
            size=settings.universe_size,
            min_turnover_24h_usdt=settings.universe_min_turnover_24h_usdt,
            max_spread_bps=settings.universe_max_spread_bps,
            min_trading_history_days=settings.universe_min_trading_history_days,
            manual_denylist=settings.universe_manual_denylist,
        ),
    )

    try:
        await run_periodic(
            service.refresh,
            interval_seconds=24 * 60 * 60,
            service_name="worker",
        )
    finally:
        await provider.close()
        await ranking_provider.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
