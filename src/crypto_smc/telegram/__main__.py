import asyncio

import structlog

from crypto_smc.config import get_settings
from crypto_smc.observability.logging import configure_logging
from crypto_smc.runtime import run_periodic

logger = structlog.get_logger(__name__)


async def heartbeat() -> None:
    await logger.ainfo("telegram_not_implemented")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await run_periodic(
        heartbeat,
        interval_seconds=60 * 60,
        service_name="telegram",
    )


if __name__ == "__main__":
    asyncio.run(main())
