from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine

from crypto_smc.config import Settings, get_settings
from crypto_smc.db.repositories.market_data import MarketDataRepository
from crypto_smc.db.repositories.universe import UniverseRepository
from crypto_smc.db.session import create_engine, create_session_factory, database_is_ready
from crypto_smc.observability.logging import configure_logging
from crypto_smc.providers.bybit import BybitClient
from crypto_smc.providers.protocols import InstrumentProvider

logger = structlog.get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    instrument_provider: InstrumentProvider | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    app_engine = engine or create_engine(app_settings.database_url)
    provider = instrument_provider or BybitClient(
        base_url=app_settings.bybit_base_url,
        timeout_seconds=app_settings.bybit_request_timeout_seconds,
        instrument_page_size=app_settings.bybit_instrument_page_size,
    )
    session_factory = create_session_factory(app_engine)
    universe_repository = UniverseRepository()
    market_data_repository = MarketDataRepository()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await logger.ainfo("api_started", environment=app_settings.app_env)
        try:
            yield
        finally:
            await provider.close()
            await app_engine.dispose()
            await logger.ainfo("api_stopped")

    app = FastAPI(
        title="Crypto SMC Signal Bot",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        if not await database_is_ready(app_engine):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database_unavailable",
            )
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/universe/current", tags=["universe"])
    async def current_universe(include_excluded: bool = False) -> dict[str, object]:
        async with session_factory() as session:
            current = await universe_repository.get_current(session)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="universe_not_initialized",
            )

        snapshot, members = current
        selected = [member for member in members if member.is_selected]
        reason_counts = Counter(
            member.exclusion_reason
            for member in members
            if not member.is_selected and member.exclusion_reason is not None
        )
        response: dict[str, object] = {
            "snapshot_id": snapshot.id,
            "source": snapshot.source,
            "created_at": snapshot.created_at,
            "activated_at": snapshot.activated_at,
            "source_updated_at": snapshot.source_updated_at,
            "candidate_count": snapshot.source_asset_count,
            "selected_count": snapshot.selected_count,
            "selected": [
                {
                    "rank": member.market_cap_rank,
                    "asset": member.asset_symbol,
                    "name": member.asset_name,
                    "instrument": member.instrument_symbol,
                    "turnover_24h_usdt": member.exchange_turnover_24h_usdt,
                    "spread_bps": member.spread_bps,
                }
                for member in selected
            ],
            "exclusion_reasons": dict(sorted(reason_counts.items())),
            "configuration": snapshot.configuration,
        }
        if include_excluded:
            response["excluded"] = [
                {
                    "rank": member.market_cap_rank,
                    "asset": member.asset_symbol,
                    "name": member.asset_name,
                    "instrument": member.instrument_symbol,
                    "reason": member.exclusion_reason,
                    "detail": member.decision_detail,
                    "turnover_24h_usdt": member.exchange_turnover_24h_usdt,
                    "spread_bps": member.spread_bps,
                }
                for member in members
                if not member.is_selected
            ]
        return response

    @app.get("/market-data/status", tags=["market-data"])
    async def market_data_status(include_symbols: bool = False) -> dict[str, object]:
        async with session_factory() as session:
            response = await market_data_repository.status_summary(session)
            if include_symbols:
                response["symbols"] = await market_data_repository.checkpoint_details(session)
        return response

    if app_settings.debug_api_enabled:

        @app.get("/debug/instruments", tags=["debug"])
        async def debug_instruments() -> dict[str, object]:
            instruments = await provider.list_usdt_perpetual_instruments()
            return {
                "count": len(instruments),
                "symbols": [instrument.symbol for instrument in instruments],
            }

    return app


app = create_app()
