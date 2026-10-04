"""Обработка нажатий интерфейса: нижнее меню и inline-кнопки.

Кнопки обрабатываются здесь же, а не в командах: нажатие — это не
команда, иначе пришлось бы подставлять служебный текст в сообщение,
которое игрок видит в чате.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot import messages
from bot.utils.keyboards import (
    HIDE_MARKER,
    MENU_BUTTON_TEXT,
    main_keyboard,
    route_for,
)

logger = logging.getLogger(__name__)

# Префиксы inline-кнопок интерфейса.
MENU_PREFIX = "menu:"
STATS_PREFIX = "stats:"


class MenuText(BaseFilter):
    """Пропускает только нажатия на кнопки нижнего меню.

    Без такого фильтра хендлер ловил бы любой текст и обрывал
    обработку: в aiogram первый подошедший хендлер завершает цепочку,
    и команды просто перестали бы работать.
    """

    async def __call__(self, message: Message) -> bool:
        text = (message.text or "").strip()
        return route_for(text) is not None or text in (HIDE_MARKER,
                                                        MENU_BUTTON_TEXT)


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="buttons")
    router.message.register(hide_command, Command("hidemenu", "menu"))
    router.message.register(on_text, MenuText())
    router.callback_query.register(on_menu_callback, F.data.startswith(MENU_PREFIX))
    router.callback_query.register(
        on_stats_callback, F.data.startswith(STATS_PREFIX)
    )
    return router


# --- нижнее меню --------------------------------------------------------------
async def hide_command(message: Message, **data: Any) -> None:
    """/menu — убрать нижние кнопки."""
    await safe(message, None, ReplyKeyboardRemove())


async def on_text(message: Message, **data: Any) -> None:
    """Нижние кнопки: текст кнопки превращается в команду.

    Текст выглядит как «🪙 ДжастКоины», поэтому его нужно обрезать
    до команды и вызвать нужный обработчик. Ответ приходит на кнопку,
    а нижнее меню само убирается — занимать им экран незачем.
    """
    text = (message.text or "").strip()

    if text == HIDE_MARKER:
        await safe(message, "Меню скрыто. Вернуть — /menu",
                   ReplyKeyboardRemove())
        return

    if text == MENU_BUTTON_TEXT:
        await safe(message, messages.HELP, main_keyboard())
        return

    command = route_for(text)
    if command is None:
        return  # обычный текст, дальше его разберут другие хендлеры

    await _run_command(message, command, data)


async def _run_command(message: Message, command: str, data: dict[str, Any]) -> None:
    """Вызывает обработчик команды так же, как если бы её написали."""
    from bot.handlers.commands import (
        cmd_donate_view,
        cmd_help,
        cmd_jcoin,
        cmd_jstats,
        cmd_jtop,
        cmd_textstats,
    )

    handlers = {
        "/jcoin": cmd_jcoin,
        "/jtop": cmd_jtop,
        "/jstats": cmd_jstats,
        "/textstats": cmd_textstats,
        "/donate": cmd_donate_view,
        "/help": cmd_help,
    }
    handler = handlers.get(command)
    if handler is None:  # pragma: no cover - рассинхрон маршрутов
        return
    # Клавиатуру убираем: меню выполнило свою задачу. Ответ команды
    # приходит без неё, а пользователь увидит только свои кнопки.
    # Модель Message неизменяема, поэтому скрытие идёт отдельным
    # сообщением до вызова команды.
    await message.answer(" ", reply_markup=ReplyKeyboardRemove())
    await handler(message, **data)


async def safe(message: Message, text: str | None,
               markup: ReplyKeyboardMarkup | None = None) -> None:
    try:
        if text is None:
            await message.answer(" ", reply_markup=markup)
        else:
            await message.answer(text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось ответить на кнопку: %s", exc)


# --- inline-кнопки ------------------------------------------------------------
async def on_menu_callback(callback: CallbackQuery, **data: Any) -> None:
    """«Меню команд» из-под ответа."""
    from bot.handlers.commands import cmd_help

    await cmd_help(callback.message, **data)
    await callback.answer()


async def on_stats_callback(callback: CallbackQuery, **data: Any) -> None:
    """Карточка или текстовые статы из-под ответа."""
    from bot.handlers.commands import cmd_jstats, send_text_stats
    from bot.handlers.common import touch_user

    action = (callback.data or "")[len(STATS_PREFIX):]
    player = await touch_user(callback.from_user, data)

    if action == "text":
        await send_text_stats(callback.message, data, player)
    elif action == "card":
        await cmd_jstats(callback.message, **data)
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    await callback.answer()