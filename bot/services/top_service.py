"""Глобальный топ игроков."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import TOP_PAGE_SIZE
from bot.db.database import Database
from bot.db.repository import UserRepository
from bot.models import User
from bot.utils.formatters import format_coins


@dataclass(slots=True)
class TopRow:
    index: int
    user: User
    line: str


@dataclass(slots=True)
class TopResult:
    rows: list[TopRow]
    user_row: TopRow | None
    total: int
    # Номер страницы и их количество — для кнопок навигации.
    page: int = 0
    pages: int = 1
    # id смотрящего: по нему строка «Твоё место» находится даже тогда,
    # когда игрок на другой странице.
    user_id: int = 0


class TopService:
    def __init__(self, database: Database) -> None:
        self.users = UserRepository(database)

    async def build(self, user: User, page: int = 0) -> TopResult:
        """Страница топа.

        Показываем ВСЕХ игроков, а не только лидеров: страница — это
        окно в полный список, листается кнопками. Топ-10 в первом
        ТЗ был ограничением, которое со временем только мешало.
        """
        size = TOP_PAGE_SIZE
        total = await self.users.count()
        pages = max(1, (total + size - 1) // size)
        page = min(max(0, page), pages - 1)

        # limit + 1: лишняя строка нужна, чтобы узнать про следующую
        # страницу, не считая всё количество.
        chunk = await self.users.top(size, offset=page * size)
        players = chunk[:size]

        rows = [
            TopRow(index=page * size + i, user=player,
                   line=f"{page * size + i}. {player.name}"
                        f" — {format_coins(player.balance)}")
            for i, player in enumerate(players, start=1)
        ]

        place = await self.users.place(user)
        user_row: TopRow | None = None
        if page * size < place <= page * size + len(rows):
            # Игрок на этой странице — обновляем строку свежими данными.
            rows[place - page * size - 1] = TopRow(
                index=place, user=user,
                line=f"{place}. {user.name} — {format_coins(user.balance)}",
            )
        elif place > page * size + len(rows):
            # Игрок на другой странице — показываем его отдельно внизу,
            # чтобы он не искал своё место в списке.
            user_row = TopRow(
                index=place, user=user,
                line=f"{place}. {user.name} — {format_coins(user.balance)}",
            )
        return TopResult(rows=rows, user_row=user_row, total=total,
                         page=page, pages=pages, user_id=user.user_id)
