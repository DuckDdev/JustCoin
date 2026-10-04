"""Блокировки игроков.

Забаненный игрок не может пользоваться ботом: команды, текст и кнопки
не обрабатываются, вместо ответа приходит причина бана. Баны бывают
временными (срок) и бессрочными. Администраторы банам не подлежат —
иначе можно случайно заблокировать самому себе доступ к управлению.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from bot.config import DEFAULT_BAN_REASON, is_admin
from bot.db.database import Database
from bot.db.repository import BanRepository
from bot.utils.formatters import format_date, utcnow

logger = logging.getLogger(__name__)

# Сроки: 30м, 12h, 7д, 4w, 2н (недели и «н» тоже принимаются).
_DURATION = re.compile(r"^(\d+)\s*([мhдdwnн])$", re.IGNORECASE)
_UNITS = {
    "м": 60,
    "h": 3600,
    "д": 86400,
    "d": 86400,
    "w": 604800,
    "н": 604800,
}


def parse_duration(raw: str) -> int | None:
    """Разбирает срок бана: 30м, 12h, 7д. None — не разобрано."""
    match = _DURATION.match((raw or "").strip())
    if not match:
        return None
    value, unit = match.groups()
    factor = _UNITS.get(unit.lower())
    if factor is None:
        return None
    return int(value) * factor


class BanResult(Enum):
    SUCCESS = "success"
    UNKNOWN_TARGET = "unknown_target"
    INVALID_DURATION = "invalid_duration"
    SELF = "self"
    ADMIN_TARGET = "admin_target"
    ALREADY_BANNED = "already_banned"
    NOT_BANNED = "not_banned"


@dataclass(slots=True)
class BanOutcome:
    result: BanResult
    user_id: int | None = None
    name: str = ""
    until: int | None = None
    raw: str = ""


class BanService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.bans = BanRepository(database)

    async def check(self, user_id: int) -> dict | None:
        """Действующий бан игрока либо None."""
        if is_admin(user_id):
            return None
        return await self.bans.active(user_id)

    async def ban(self, user_id: int, banned_by: int, reason: str | None = None,
                  duration_seconds: int | None = None) -> BanOutcome:
        if user_id == banned_by:
            return BanOutcome(result=BanResult.SELF, user_id=user_id)
        # Админа банить нельзя: иначе он потеряет доступ к управлению.
        if is_admin(user_id):
            return BanOutcome(result=BanResult.ADMIN_TARGET, user_id=user_id)
        if await self.bans.is_banned(user_id):
            return BanOutcome(result=BanResult.ALREADY_BANNED, user_id=user_id)

        until = (
            int((utcnow() + timedelta(seconds=duration_seconds)).timestamp())
            if duration_seconds
            else None
        )
        await self.bans.ban(user_id, reason or DEFAULT_BAN_REASON, banned_by, until)
        logger.info(
            "Игрок %s забанен до %s: %s",
            user_id, until or "бессрочно", reason or DEFAULT_BAN_REASON,
        )
        return BanOutcome(result=BanResult.SUCCESS, user_id=user_id, until=until)

    async def unban(self, user_id: int) -> BanOutcome:
        lifted = await self.bans.lift(user_id)
        if lifted:
            logger.info("Бан игрока %s снят", user_id)
            return BanOutcome(result=BanResult.SUCCESS, user_id=user_id)
        return BanOutcome(result=BanResult.NOT_BANNED, user_id=user_id)

    async def list_active(self) -> list[dict]:
        return await self.bans.list_active()

    async def count_active(self) -> int:
        return await self.bans.count_active()

    async def history(self, user_id: int) -> list[dict]:
        return await self.bans.history(user_id)


def ban_text(reason: str | None, until, has_ends: bool) -> str:
    """Человеческий текст блокировки для игрока."""
    from bot import messages

    parts = [messages.BAN_HEADER]
    parts.append(f"Причина: {reason or DEFAULT_BAN_REASON}")
    if has_ends and until is not None:
        parts.append(
            f"Бан до {format_date(until)}"
        )
    parts.append(messages.BAN_FOOTER)
    return "\n".join(parts)
