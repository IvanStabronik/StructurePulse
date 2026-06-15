import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from crypto_smc.config import get_settings
from crypto_smc.db.repositories.notifications import NotificationRepository
from crypto_smc.db.session import create_engine, create_session_factory
from crypto_smc.observability.logging import configure_logging
from crypto_smc.telegram.commands import TelegramCommandService
from crypto_smc.telegram.outbox import NotificationOutboxService
from crypto_smc.telegram.transport import AiogramSender, create_router

logger = structlog.get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    repository = NotificationRepository()
    await repository.ensure_users(
        session_factory,
        user_ids=settings.telegram_allowed_user_ids,
        default_language=settings.telegram_default_language,
        schedule_timezone=settings.telegram_schedule_timezone,
        schedule_start=settings.telegram_schedule_start,
        schedule_end=settings.telegram_schedule_end,
    )
    if not settings.telegram_bot_token:
        await logger.awarning("telegram_disabled_missing_token")
        try:
            await asyncio.Event().wait()
        finally:
            await engine.dispose()
        return
    if not settings.telegram_allowed_user_ids:
        await logger.aerror("telegram_disabled_missing_allowed_users")
        await engine.dispose()
        return

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    command_service = TelegramCommandService(
        session_factory=session_factory,
        settings_repository=repository,
    )
    dispatcher.include_router(
        create_router(
            allowed_user_ids=settings.telegram_allowed_user_ids,
            command_service=command_service,
        )
    )
    sender = AiogramSender(bot)
    outbox = NotificationOutboxService(
        session_factory=session_factory,
        sender=sender,
        user_ids=settings.telegram_allowed_user_ids,
        poll_seconds=settings.telegram_outbox_poll_seconds,
        batch_size=settings.telegram_outbox_batch_size,
        max_attempts=settings.telegram_outbox_max_attempts,
        retry_base_seconds=settings.telegram_retry_base_seconds,
        repository=repository,
    )
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(
        [
            BotCommand(command="signals", description="Active signals"),
            BotCommand(command="coin", description="Latest coin analysis"),
            BotCommand(command="settings", description="Notification settings"),
            BotCommand(command="status", description="Service status"),
            BotCommand(command="stats", description="Virtual performance"),
            BotCommand(command="pause", description="Pause notifications"),
            BotCommand(command="resume", description="Resume notifications"),
        ]
    )
    await logger.ainfo(
        "telegram_started",
        allowed_user_count=len(settings.telegram_allowed_user_ids),
    )
    try:
        await asyncio.gather(
            outbox.run(),
            dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            ),
        )
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
