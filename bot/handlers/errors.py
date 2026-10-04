"""Антиспам и защита от падений.

Правило обновления 1.1.3: бот не пишет «Подожди» на каждое лишнее
нажатие. Событие чаще, чем ANTISPAM_COOLDOWN секунд, просто
игнорируется — игрок ничего не видит. Ответ появляется только при
настоящем флуде: ANTISPAM_FLOOD_EVENTS событий за ANTISPAM_WINDOW
секунд, и тогда бот пишет один раз, а дальше снова молчит до остывания.

Так же ведёт себя отказ по кулдауну: игнорируем прошлые сообщения,
обрабатываем только те, что пришли после запуска бота.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser

from bot import messages
from bot.config import (
    ANTISPAM_COOLDOWN,
    ANTISPAM_FLOOD_COOLDOWN,
    ANTISPAM_FLOOD_EVENTS,
    ANTISPAM_WINDOW,
    is_admin,
)

logger = logging.getLogger(__name__)

# Админские команды антиспам не режет: ими пользуются сериями
# (/maintenance_on → /maintenance_status → /maintenance_off), и
# ограничение делает управление техработами невозможным.
EXEMPT_COMMANDS = ("/maintenance_on", "/maintenance_off", "/maintenance_status")


class AntispamMiddleware(BaseMiddleware):
    """Отбрасывает слишком частые события, отвечая только на флуд.

    Решение — тишина. Раньше на любое повторное нажатие бот слал
    «Подожди», и на кнопках это превращалось в спам: игрок видит
    десяток одинаковых сообщений вместо одного ответа.
    """

    def __init__(
        self,
        cooldown: float = ANTISPAM_COOLDOWN,
        flood_events: int = ANTISPAM_FLOOD_EVENTS,
        window: float = ANTISPAM_WINDOW,
        flood_cooldown: int = ANTISPAM_FLOOD_COOLDOWN,
    ) -> None:
        self.cooldown = cooldown
        self.flood_events = flood_events
        self.window = window
        self.flood_cooldown = flood_cooldown
        self._last_seen: dict[int, float] = defaultdict(float)
        self._history: dict[int, Deque[float]] = defaultdict(deque)
        self._warned_at: dict[int, float] = defaultdict(float)
        self._max_users = 50_000

    # --- решение по одному событию -----------------------------------------
    def _allow(self, user_id: int) -> bool:
        """True — событие можно обрабатывать.

        В историю попадают ВСЕ события, включая отброшенные: флуд и есть
        частота нажатий, а если считать только обработанные, частые
        клики выглядели бы как единичное событие.
        """
        now = time.monotonic()

        events = self._history[user_id]
        events.append(now)
        while events and now - events[0] > self.window:
            events.popleft()

        if len(self._last_seen) > self._max_users:
            self._cleanup(now)

        # Частое нажатие: молча пропускаем. Это не флуд, а обычная
        # торопливость, отвечать на неё нечего.
        if now - self._last_seen[user_id] < self.cooldown:
            self._last_seen[user_id] = now
            return False

        self._last_seen[user_id] = now
        return True

    def _is_flood(self, user_id: int) -> bool:
        """Флуд ли это, и если да — пора ли напомнить."""
        if len(self._history[user_id]) < self.flood_events:
            return False
        now = time.monotonic()
        # Предупреждение не чаще раза в минуту: иначе сам ответ
        # превратится в спам.
        if now - self._warned_at[user_id] < self.flood_cooldown:
            return False
        self._warned_at[user_id] = now
        return True

    def _cleanup(self, now: float) -> None:
        """Чистим протухшие записи, чтобы словари не росли бесконечно."""
        cutoff = now - max(self.window, self.cooldown) * 10
        self._last_seen = defaultdict(
            float, {k: v for k, v in self._last_seen.items() if v >= cutoff}
        )
        self._history = defaultdict(
            deque,
            {k: deque(v, maxlen=self.flood_events)
             for k, v in self._history.items() if v and v[-1] >= cutoff},
        )

    @staticmethod
    def _is_exempt(event: TelegramObject, tg_user: TgUser) -> bool:
        """Админы и админские команды проходят без ограничения частоты."""
        if is_admin(tg_user.id):
            return True
        if isinstance(event, Message):
            text = (event.text or "").strip().lower()
            # Учитываем суффикс @имя_бота: /maintenance_status@my_bot
            head = text.split("@", 1)[0]
            return head in EXEMPT_COMMANDS
        return False

    async def __call__(
        self,
        handler: Callable[..., Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is None or self._is_exempt(event, tg_user):
            # Следующий слой принимает (event, data) позиционно — так его
            # подставляет сам aiogram (MiddlewareManager.wrap_middlewares).
            return await handler(event, data)

        if not self._allow(tg_user.id):
            if self._is_flood(tg_user.id):
                logger.debug("Антиспам: флуд от %s", tg_user.id)
                await self._warn(event)
            return None

        return await handler(event, data)

    @staticmethod
    async def _warn(event: TelegramObject) -> None:
        """Единственный ответ антиспама — и только на настоящий флуд."""
        try:
            if isinstance(event, Message):
                await event.answer(messages.ANTISPAM_FLOOD)
            elif isinstance(event, CallbackQuery):
                await event.answer(messages.ANTISPAM_FLOOD, show_alert=True)
        except Exception as exc:  # noqa: BLE001 - чат мог пропасть
            logger.debug("Не удалось предупредить о флуде: %s", exc)