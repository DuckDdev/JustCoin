"""Общие операции с игроком: регистрация, обновление профиля, место в топе."""

from __future__ import annotations

from bot.db.database import Database
from bot.db.repository import UserRepository
from bot.models import User


class UserService:
    def __init__(self, database: Database) -> None:
        self.users = UserRepository(database)

    async def touch(self, user_id: int, username: str | None = None,
                    first_name: str | None = None,
                    last_name: str | None = None) -> User:
        """Гарантирует существование игрока и обновляет его имя/username."""
        return await self.users.ensure(user_id, username, first_name, last_name)

    async def get_or_none(self, user_id: int) -> User | None:
        return await self.users.get(user_id)

    async def place_and_total(self, user: User) -> tuple[int, int]:
        place = await self.users.place(user)
        total = await self.users.count()
        return place, total
