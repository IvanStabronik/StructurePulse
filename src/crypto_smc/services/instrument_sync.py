import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.instruments import InstrumentRepository
from crypto_smc.providers.protocols import InstrumentProvider

logger = structlog.get_logger(__name__)


class InstrumentSyncService:
    def __init__(
        self,
        *,
        provider: InstrumentProvider,
        session_factory: async_sessionmaker[AsyncSession],
        repository: InstrumentRepository | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._repository = repository or InstrumentRepository()

    async def sync(self) -> int:
        instruments = await self._provider.list_usdt_perpetual_instruments()
        async with self._session_factory() as session, session.begin():
            await self._repository.replace_active_set(session, instruments)
        await logger.ainfo("instruments_synchronized", count=len(instruments))
        return len(instruments)
