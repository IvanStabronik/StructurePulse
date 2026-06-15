from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import Message

from crypto_smc.telegram.commands import TelegramCommandService
from crypto_smc.telegram.outbox import (
    PermanentDeliveryError,
    RetryableDeliveryError,
    UnknownDeliveryOutcome,
)


class AiogramSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, user_id: int, text: str) -> int:
        try:
            message = await self._bot.send_message(
                chat_id=user_id,
                text=text,
                disable_web_page_preview=True,
            )
        except TelegramRetryAfter as exc:
            raise RetryableDeliveryError(
                str(exc),
                retry_after_seconds=float(exc.retry_after),
            ) from exc
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            raise PermanentDeliveryError(str(exc)) from exc
        except (TelegramNetworkError, TelegramServerError) as exc:
            raise UnknownDeliveryOutcome(str(exc)) from exc
        except TelegramAPIError as exc:
            raise PermanentDeliveryError(str(exc)) from exc
        return message.message_id


def create_router(
    *,
    allowed_user_ids: tuple[int, ...],
    command_service: TelegramCommandService,
) -> Router:
    router = Router(name="structurepulse-commands")
    allowed = frozenset(allowed_user_ids)

    @router.message(F.text.startswith("/"))
    async def command_handler(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in allowed:
            return
        response = await command_service.handle(
            message.from_user.id,
            message.text or "",
        )
        if response:
            await message.answer(response, disable_web_page_preview=True)

    return router
