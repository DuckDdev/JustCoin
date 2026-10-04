"""Кнопка «Забрать посылку»."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot import messages
from bot.handlers.common import (
    maintenance_text,
    place_line,
    safe_answer,
    safe_edit,
    touch_user,
)
from bot.models import User as Player
from bot.services.courier_service import CALLBACK_PREFIX, CourierService, TakeResult
from bot.utils.formatters import format_coins

logger = logging.getLogger(__name__)

def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="packages")
    router.callback_query.register(
        take_package, F.data.startswith(CALLBACK_PREFIX)
    )
    return router


async def take_package(callback: CallbackQuery, **data: Any) -> None:
    """Выдача посылки по нажатию кнопки."""
    try:
        package_id = int((callback.data or "")[len(CALLBACK_PREFIX):])
    except ValueError:
        logger.warning("Некорректный id посылки: %s", callback.data)
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return

    blocked = await maintenance_text(data, "courier", callback.from_user)
    if blocked:
        await callback.answer(blocked, show_alert=True)
        return

    courier: CourierService = data["courier"]
    message: Message | None = callback.message

    try:
        result, package = await courier.take(callback.from_user.id, package_id)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка выдачи посылки %s", package_id)
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return

    if result is TakeResult.TAKEN and package is not None:
        # from_user берём у callback, а не у сообщения: у сообщения,
        # к которому привязана кнопка, он может отсутствовать.
        # Перечитываем игрока: баланс уже изменился в БД, а локальный
        # объект загружен до выдачи и содержит устаревшую сумму.
        player: Player = await touch_user(callback.from_user, data)
        if message is not None:
            await safe_answer(
                message,
                messages.PACKAGE_TAKEN.format(
                    amount=format_coins(package.amount),
                    place=await place_line(data, player),
                ),
            )
            await safe_edit(callback, reply_markup=None)
        await callback.answer()
        return

    if result is TakeResult.ALREADY:
        await callback.answer(messages.PACKAGE_ALREADY_TAKEN, show_alert=True)
        return

    if result is TakeResult.NOT_YOURS:
        await callback.answer(messages.PACKAGE_NOT_YOURS, show_alert=True)
        return

    if result is TakeResult.IN_TRANSIT and package is not None:
        await callback.answer(await courier.transit_text(package), show_alert=True)
        return

    await callback.answer(messages.BOT_ERROR, show_alert=True)
