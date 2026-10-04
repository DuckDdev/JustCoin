"""JustID — короткая ссылка на профиль игрока вида $John.

Игрок сам выбирает себе идентификатор, по которому его можно найти:
    $John, $JustCoiner, $JustChelik
Идентификатор уникален глобально и не может быть переприсвоен другому
игроку: смена возможна только на ещё свободный.

Зарезервированные имена (JUSTID_RESERVED) нужны боту и служебным
страницам. Обычные игроки их занять не могут, но администратор может —
через /setid.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from bot.config import (
    JUSTID_MAX_LENGTH,
    JUSTID_MIN_LENGTH,
    JUSTID_PREFIX,
    JUSTID_RESERVED,
    is_admin,
)
from bot.db.database import Database
from bot.db.repository import UserRepository
from bot.models import User

logger = logging.getLogger(__name__)

# Разрешены латиница, цифры, подчёркивание и точка внутри.
_ALLOWED = re.compile(r"^[A-Za-z0-9_.]+$")


class JustIdResult(Enum):
    SUCCESS = "success"
    INVALID = "invalid"
    RESERVED = "reserved"
    TAKEN = "taken"
    UNKNOWN = "unknown"
    LOCKED = "locked"        # секретная часть не открыта


@dataclass(slots=True)
class JustIdOutcome:
    result: JustIdResult
    just_id: str = ""
    hint: str = ""


def normalize(raw: str) -> str:
    """Приводит ввод к каноническому виду: без префикса, в верхнем регистре."""
    text = (raw or "").strip()
    if text.startswith(JUSTID_PREFIX):
        text = text[len(JUSTID_PREFIX):]
    return text.strip().lstrip("@").upper()


def display(just_id: str | None) -> str:
    """Готовый к показу вид: $John."""
    if not just_id:
        return "—"
    return f"{JUSTID_PREFIX}{just_id}"


def validate(raw: str) -> str | None:
    """Нормализованный id либо None, если он недопустим по формату."""
    just_id = normalize(raw)
    if not (JUSTID_MIN_LENGTH <= len(just_id) <= JUSTID_MAX_LENGTH):
        return None
    if not _ALLOWED.match(just_id):
        return None
    return just_id


def looks_like_just_id(text: str) -> bool:
    """Похоже ли сообщение на JustID — чтобы реагировать на них в тексте."""
    return (text or "").strip().startswith(JUSTID_PREFIX)


def is_reserved(just_id: str) -> bool:
    return normalize(just_id).lower() in JUSTID_RESERVED


class JustIdService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.users = UserRepository(database)

    async def by_id(self, just_id: str) -> User | None:
        """Игрок по идентификатору. $john и $John — один и тот же игрок."""
        normalized = normalize(just_id)
        if not normalized:
            return None
        row = await self.db.fetch_one(
            f"SELECT {USER_COLUMNS} FROM users WHERE just_id = ?",
            (normalized,),
        )
        return _row_to_user(row) if row else None

    async def claim(self, user: User, raw: str) -> JustIdOutcome:
        """Занять JustID за игроком или сменить свой.

        Зарезервированные имена доступны только администраторам.
        Сам JustID открыт всем: это просто ссылка на профиль, секретом
        быть не может.
        """
        just_id = validate(raw)
        if just_id is None:
            return JustIdOutcome(result=JustIdResult.INVALID, hint=raw)
        if is_reserved(just_id) and not is_admin(user.user_id):
            return JustIdOutcome(
                result=JustIdResult.RESERVED, just_id=just_id, hint=just_id
            )

        owner = await self.by_id(just_id)
        if owner is not None and owner.user_id != user.user_id:
            return JustIdOutcome(
                result=JustIdResult.TAKEN, just_id=just_id, hint=just_id
            )
        if user.just_id == just_id:
            return JustIdOutcome(
                result=JustIdResult.SUCCESS, just_id=just_id, hint=just_id
            )

        async with self.db.transaction() as tx:
            await UserRepository.set_just_id_in_tx(tx, user.user_id, just_id)
        logger.info("Игрок %s занял JustID %s", user.user_id, just_id)
        return JustIdOutcome(result=JustIdResult.SUCCESS, just_id=just_id, hint=just_id)

    async def release(self, user: User) -> bool:
        if not user.just_id:
            return False
        async with self.db.transaction() as tx:
            await UserRepository.set_just_id_in_tx(tx, user.user_id, None)
        logger.info("Игрок %s освободил JustID %s", user.user_id, user.just_id)
        return True


# Импортируем в конце: repository тянет models, а тот — config.
from bot.db.repository import USER_COLUMNS, _row_to_user  # noqa: E402
