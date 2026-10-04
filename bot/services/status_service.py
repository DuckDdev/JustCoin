"""Статусы игроков: создатель, админ, VIP, донатор.

Статус с наибольшим приоритетом выигрывает: у игрока может быть
выдан донат, но если он админ, на карточке будет «АДМИН».
Администраторский статус проставляется автоматически по ADMIN_IDS и
CREATOR_IDS, остальные выдаются вручную через /setstatus.
"""

from __future__ import annotations

import logging
from enum import Enum

from bot.config import ADMIN_IDS, CREATOR_IDS, STATUSES, PlayerStatus
from bot.db.database import Database
from bot.db.repository import UserRepository
from bot.models import User

logger = logging.getLogger(__name__)


class StatusResult(Enum):
    SUCCESS = "success"
    CLEARED = "cleared"
    UNKNOWN = "unknown"


class StatusService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.users = UserRepository(database)

    @staticmethod
    def catalog() -> dict[str, PlayerStatus]:
        return STATUSES

    @staticmethod
    def exists(status_id: str) -> bool:
        return status_id.lower() in STATUSES

    @staticmethod
    def auto_status(user_id: int) -> str | None:
        """Статус, выдаваемый автоматически по ID."""
        if user_id in CREATOR_IDS:
            return "creator"
        if user_id in ADMIN_IDS:
            return "admin"
        return None

    @classmethod
    def effective(cls, user: User) -> PlayerStatus | None:
        """Итоговый статус игрока с учётом автоматических правил.

        Автоматический статус всегда сильнее выданного вручную:
        админ не должен выглядеть «донатором».
        """
        auto = cls.auto_status(user.user_id)
        best: PlayerStatus | None = None
        for candidate in (auto, user.status):
            if not candidate:
                continue
            status = STATUSES.get(candidate.lower())
            if status is None:
                continue
            if best is None or status.priority > best.priority:
                best = status
        return best

    async def grant(self, user: User, status_id: str) -> StatusResult:
        status_id = status_id.lower().strip()
        if status_id not in STATUSES:
            return StatusResult.UNKNOWN
        async with self.db.transaction() as tx:
            await self.users.set_status_in_tx(tx, user.user_id, status_id)
        logger.info("Игроку %s выдан статус %s", user.user_id, status_id)
        return StatusResult.SUCCESS

    async def clear(self, user: User) -> StatusResult:
        async with self.db.transaction() as tx:
            await self.users.set_status_in_tx(tx, user.user_id, None)
        logger.info("Статус игрока %s снят", user.user_id)
        return StatusResult.CLEARED
