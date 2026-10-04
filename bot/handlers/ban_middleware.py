"""Мидлвары, общие для всех апдейтов: баны и свежесть сообщений."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    Message,
    TelegramObject,
    User as TgUser,
)

from bot import messages
from bot.config import is_admin
from bot.services.ban_service import BanService
from bot.services.announce_service import AnnounceService
from bot.utils.formatters import utcnow

logger = logging.getLogger(__name__)


class ChatTrackingMiddleware(BaseMiddleware):
    """Запоминает чаты, в которых бот работает.

    Список нужен для /announce и для отправки сообщений из админ-панели.
    """

    def __init__(self, announce: AnnounceService) -> None:
        self.announce = announce

    async def __call__(self, handler, event, data):
        chat = data.get("event_chat")
        if chat is not None:
            try:
                await self.announce.register_chat(chat)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось запомнить чат: %s", exc)
        return await handler(event, data)


class FreshUpdatesMiddleware(BaseMiddleware):
    """Обрабатывает только события, случившиеся после запуска бота.

    Когда бот выключен, Telegram копит апдейты и отдаёт их пачкой при
    следующем старте. Игрок видит отставшие на час команды, а бот
    отвечает на них так, будто они только что пришли. Молчаливый
    пропуск понятнее: человек уже получил ответ, пока бота не было.

    Событие считается прошлым, если его время меньше метки старта.
    """

    def __init__(self) -> None:
        self.started_at: datetime | None = None

    def mark_started(self, moment: datetime | None = None) -> None:
        self.started_at = moment or utcnow()

    def _moment(self, event: TelegramObject) -> datetime | None:
        """Время события.

        Мидлвара висит на update, где самого поля date нет: оно лежит
        внутри сообщения или callback'а. Без этого разбора все события
        выглядели бы как «без даты» и проходили без проверки.
        """
        message = getattr(event, "message", None)
        if message is None:
            query = getattr(event, "callback_query", None)
            message = getattr(query, "message", None) if query else None
        date = getattr(message, "date", None)
        return date if isinstance(date, datetime) else None

    async def __call__(self, handler, event, data):
        if self.started_at is None:
            # Мидлвара не инициализирована (тесты, ручной запуск):
            # считаем все события свежими.
            return await handler(event, data)

        moment = self._moment(event)
        if moment is None or moment >= self.started_at:
            return await handler(event, data)

        sender = data.get("event_from_user")
        if isinstance(sender, TgUser) and is_admin(sender.id):
            return await handler(event, data)

        logger.info("Прошлое событие проигнорировано: %s от %s",
                    moment, getattr(sender, "id", "?"))
        return None


class BanMiddleware(BaseMiddleware):
    """Пускает незабаненных, забаненным объясняет причину.

    Стоит выше антиспама: иначе забаненный получал бы «слишком часто»
    вместо внятного объяснения, и непонятно было бы, что вообще
    происходит.
    """

    def __init__(self, bans: BanService) -> None:
        self.bans = bans
        # Отказ о бане — один раз НА ИГРОКА: иначе при каждом нажатии
        # прилетает новый отказ, и это сам по себе спам. Общий флаг на
        # всех был ошибкой: объяснение получал только первый забаненный,
        # а остальные молчали, не понимая причины.
        self._notified: set[int] = set()

    async def __call__(self, handler, event, data):
        sender: TgUser | None = data.get("event_from_user")
        if sender is None or is_admin(sender.id):
            return await handler(event, data)

        ban = await self.bans.check(sender.id)
        if ban is None:
            return await handler(event, data)

        await self._explain(event, ban, sender.id)
        return None

    async def _explain(self, event: TelegramObject, ban: dict,
                       user_id: int) -> None:
        """Ответ о бане уходит один раз: иначе он тоже становится спамом.

        Причина и срок показываем прямо в сообщении — иначе игрок
        не понимает, за что его отключили.
        """
        if user_id in self._notified:
            return
        self._notified.add(user_id)
        lines = [
            messages.BAN_HEADER,
            f"Причина: {ban['reason'] or 'причина не указана'}",
        ]
        until = _until_text(ban.get("until"))
        if until != "бессрочно":
            lines.append(f"Бан до {until}")
        lines.append(messages.BAN_FOOTER)
        try:
            if isinstance(event, Message):
                await event.answer("\n".join(lines))
            elif isinstance(event, CallbackQuery):
                await event.answer("\n".join(lines)[:180], show_alert=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось сообщить о бане: %s", exc)


def _until_text(until: Any) -> str:
    """Человеческая дата окончания бана."""
    if not until:
        return "бессрочно"
    if hasattr(until, "strftime"):
        return until.strftime("%d.%m.%Y %H:%M")
    return str(until)

__all__ = ["BanMiddleware", "ChatTrackingMiddleware", "FreshUpdatesMiddleware"]