"""«Замедлись!» — учёт простоев бота.

Идея: если бот выключался надолго, игроки не могли получить коины вовремя
и фактически «отстали» от остальных. Когда бот оживает, он честно
признаётся в этом и показывает, во сколько раз выросло ожидание:

    Подожди! через 9 часов ...
    🐌 Замедлись! (х9)
    Бот был оффлайн 1 ч 12 мин, поэтому ожидание выросло в 9 раз

Множитель копится: каждый пропущенный цикл повышает его на единицу
(с ограничением), а со временем затухает обратно к единице.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.config import (
    SLOWDOWN_DECAY_HOURS,
    SLOWDOWN_ENABLED,
    SLOWDOWN_MAX_MULTIPLIER,
    SLOWDOWN_MIN_MULTIPLIER,
    SLOWDOWN_OFFLINE_THRESHOLD,
)
from bot.db.database import Database
from bot.db.repository import UserRepository
from bot.models import User
from bot.utils.formatters import format_duration, utcnow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Slowdown:
    multiplier: int
    offline_seconds: int
    decayed: bool = False


class SlowdownService:
    """Считает «замедление» игрока и его ожидание."""

    def __init__(self, database: Database) -> None:
        self.db = database
        self.users = UserRepository(database)

    # --- состояние ---------------------------------------------------------
    @staticmethod
    def is_enabled() -> bool:
        return SLOWDOWN_ENABLED

    @staticmethod
    def _decay_multiplier(user: User, now) -> int:
        """Снижает множитель, если игрок давно не получал коины.

        Каждые SLOWDOWN_DECAY_HOURS без начисления множитель падает на 1.
        Так «замедление» не становится вечной приговором.
        """
        if user.slowdown <= 0:
            return 0
        if user.slowdown_at is None:
            return user.slowdown
        hours = (now - user.slowdown_at).total_seconds() / 3600
        steps = int(hours // SLOWDOWN_DECAY_HOURS)
        if steps <= 0:
            return user.slowdown
        return max(0, user.slowdown - steps)

    async def state(self, user: User) -> Slowdown:
        """Текущее состояние «замедления» с учётом затухания."""
        now = utcnow()
        multiplier = self._decay_multiplier(user, now)
        if multiplier < SLOWDOWN_MIN_MULTIPLIER:
            return Slowdown(multiplier=1, offline_seconds=0, decayed=True)
        # Оффлайн считаем от последнего обновления множителя.
        offline = 0
        if user.slowdown_at is not None:
            offline = int((now - user.slowdown_at).total_seconds())
        return Slowdown(multiplier=multiplier, offline_seconds=offline)

    # --- начисление --------------------------------------------------------
    async def register_offline(self, user: User, offline_seconds: int) -> int:
        """Учитывает простой бота для игрока, возвращает новый множитель.

        Вызывается, когда игрок пытается получить коины, а бот стоял дольше
        порога: значит, цикл ожидания он пропустил по вине бота.
        """
        if not SLOWDOWN_ENABLED:
            return 1
        if offline_seconds < SLOWDOWN_OFFLINE_THRESHOLD:
            return await self._decayed(user)

        now = utcnow()
        base = self._decay_multiplier(user, now)
        # Первый простой уже должен давать заметное замедление, поэтому
        # нижняя граница — SLOWDOWN_MIN_MULTIPLIER, а не просто base + 1.
        multiplier = max(
            SLOWDOWN_MIN_MULTIPLIER,
            min(SLOWDOWN_MAX_MULTIPLIER, base + 1),
        )
        async with self.db.transaction() as tx:
            await UserRepository.set_slowdown_in_tx(
                tx, user.user_id, multiplier, int(now.timestamp())
            )
        return await self._log(user, multiplier, offline_seconds)

    async def register_offline_in_tx(self, tx, user: User,
                                     offline_seconds: int) -> int:
        """То же, но в уже открытой транзакции вызывающего.

        Нужна именно эта версия: claim() держит свою транзакцию, а
        обычный register_offline открывает вторую. Блокировка БД в
        database.transaction() не переиспользуется, поэтому вложенный
        захват ждал бы сам себя до вечности.
        """
        if not SLOWDOWN_ENABLED:
            return 1
        if offline_seconds < SLOWDOWN_OFFLINE_THRESHOLD:
            return self._decay_multiplier(user, utcnow())

        now = utcnow()
        base = self._decay_multiplier(user, now)
        multiplier = max(
            SLOWDOWN_MIN_MULTIPLIER,
            min(SLOWDOWN_MAX_MULTIPLIER, base + 1),
        )
        await UserRepository.set_slowdown_in_tx(
            tx, user.user_id, multiplier, int(now.timestamp())
        )
        self._log_sync(user, multiplier, offline_seconds)
        return multiplier

    async def _log(self, user: User, multiplier: int,
                   offline_seconds: int) -> int:
        self._log_sync(user, multiplier, offline_seconds)
        return multiplier

    def _log_sync(self, user: User, multiplier: int,
                  offline_seconds: int) -> None:
        logger.info(
            "Игрок %s замедлился до x%d (оффлайн %s)",
            user.user_id, multiplier, format_duration(offline_seconds),
        )

    async def _decayed(self, user: User) -> int:
        decayed = self._decay_multiplier(user, utcnow())
        if decayed != user.slowdown:
            async with self.db.transaction() as tx:
                await UserRepository.set_slowdown_in_tx(
                    tx, user.user_id, decayed,
                    int(utcnow().timestamp()) if decayed else None,
                )
        return max(1, decayed)

    async def reset(self, user: User) -> None:
        async with self.db.transaction() as tx:
            await UserRepository.set_slowdown_in_tx(
                tx, user.user_id, 0, None
            )

    # --- ожидание ----------------------------------------------------------
    @staticmethod
    def cooldown_for(user: User, base_cooldown: int) -> int:
        """Кулдаун с учётом «замедления»."""
        if not SLOWDOWN_ENABLED or user.slowdown < SLOWDOWN_MIN_MULTIPLIER:
            return base_cooldown
        return base_cooldown * user.slowdown
