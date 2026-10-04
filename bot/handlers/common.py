"""Общие хелперы для хендлеров: доступ к сервисам, техработы, безопасный ответ."""

from __future__ import annotations

import logging
from typing import Any

from aiogram.types import CallbackQuery, Message, User as TgUser

from bot import messages
from bot.config import is_admin
from bot.models import User as Player
from bot.services.maintenance_service import MaintenanceService
from bot.services.user_service import UserService

logger = logging.getLogger(__name__)


def services(data: dict[str, Any]) -> tuple[UserService, MaintenanceService]:
    return data["users"], data["maintenance"]


async def touch_user(tg_user: TgUser, data: dict[str, Any]) -> Player:
    """Гарантирует, что игрок зарегистрирован, и обновляет его профиль.

    Принимает сам Telegram-объект пользователя, а не Message: у сообщения,
    к которому привязан callback, from_user может быть пустым.
    """
    user_service: UserService = data["users"]
    return await user_service.touch(
        tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name
    )


async def touch_player(message: Message, data: dict[str, Any]) -> Player:
    """Обёртка для хендлеров сообщений."""
    return await touch_user(message.from_user, data)


async def maintenance_text(data: dict[str, Any], feature: str,
                           tg_user: TgUser) -> str | None:
    """Текст блокировки, если функция на техработах. None — можно выполнять."""
    if is_admin(tg_user.id):  # админы обходят техработы
        return None
    maintenance: MaintenanceService = data["maintenance"]
    if await maintenance.is_disabled(feature):
        reason = await maintenance.reason_for(feature)
        return messages.MAINTENANCE_DISABLED.format(reason=reason)
    return None


async def place_line(data: dict[str, Any], player: Player) -> str:
    user_service: UserService = data["users"]
    place, total = await user_service.place_and_total(player)
    return messages.CLAIM_TOP_PLACE.format(place=place, total=total)


async def safe_answer(message: Message, text: str, **kwargs) -> None:
    """Ответ с проглатыванием ошибок Telegram (например, заблокированный бот)."""
    try:
        await message.answer(text, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось отправить сообщение %s: %s",
                       getattr(message.from_user, "id", "?"), exc)


async def safe_edit(callback: CallbackQuery, text: str | None = None, **kwargs) -> None:
    """Правка сообщения с проглатыванием ошибок «message is not modified»."""
    try:
        if text is not None:
            await callback.message.edit_text(text, **kwargs)
        else:
            await callback.message.edit_reply_markup(**kwargs)
    except Exception as exc:  # noqa: BLE001 - «message is not modified» не страшно
        logger.debug("Не удалось отредактировать сообщение: %s", exc)
