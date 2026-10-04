"""Рассылка объявлений по всем чатам, где работал бот.

Список чатов накапливается в таблице chats по мере общения с ботом.

Ограничения по частоте нет (обновление 1.1.2): рассылка доступна на
каждый вызов. Единственный предохранитель — ANNOUNCE_MAX_CHATS, он
не даёт отправить в непомерно длинный список. Скорость отправки
поднимает SEND_DELAY, чтобы не упереться в лимиты Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import ANNOUNCE_MAX_CHATS
from bot.db.database import Database
from bot.db.repository import ChatRepository
from bot.utils.formatters import format_duration, utcnow

logger = logging.getLogger(__name__)

# Пауза между отправками: не долбим API подряд.
SEND_DELAY = 0.05


@dataclass(slots=True)
class AnnounceResult:
    total: int = 0
    sent: int = 0
    failed: int = 0
    last_announce: int | None = None


class AnnounceService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.chats = ChatRepository(database)

    async def register_chat(self, chat) -> None:
        """Запоминает чат при любом общении с ботом."""
        await self.chats.touch(
            chat.id, getattr(chat, "title", None), getattr(chat, "type", None),
            getattr(chat, "username", None),
        )

    async def last_announce(self) -> int | None:
        """Когда была прошлая рассылка — для статистики, без ограничений."""
        return await self.chats.recent_announce()

    async def broadcast(self, bot: Bot, text: str) -> AnnounceResult:
        """Отправляет текст во все чаты. Кулдауна нет."""
        chats = await self.chats.all(ANNOUNCE_MAX_CHATS)
        if not chats:
            return AnnounceResult()

        now = int(utcnow().timestamp())
        sent = failed = 0
        for chat in chats:
            try:
                await bot.send_message(chat_id=chat["chat_id"], text=text)
                sent += 1
                await self.chats.mark_announced(chat["chat_id"], now)
            except TelegramAPIError as exc:
                # Пользователь мог заблокировать бота — не повторяем туда.
                failed += 1
                logger.debug("Чат %s недоступен: %s", chat["chat_id"], exc)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("Ошибка рассылки в %s: %s", chat["chat_id"], exc)
            await asyncio.sleep(SEND_DELAY)

        logger.info("Рассылка: %d из %d доставлено, %d ошибок", sent, len(chats), failed)
        return AnnounceResult(total=len(chats), sent=sent, failed=failed,
                              last_announce=now)


def format_left(seconds: int) -> str:
    return format_duration(seconds)


def format_ago(seconds: int) -> str:
    return format_duration(seconds)


def eta(total: int) -> str:
    return format_duration(total * SEND_DELAY)
