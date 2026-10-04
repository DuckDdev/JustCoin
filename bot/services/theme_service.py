"""Магазин «Джасгнит»: покупка тем карточки за обычных джаст коинов.

Темы покупаются один раз и остаются у игрока навсегда, переключать их
можно бесплатно. Базовая тема бесплатна и доступна всем.

Покупка атомарна: списание коинов и запись о покупке в одной транзакции,
поэтому двойное нажатие не спишет монеты дважды.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from bot.config import CUSTOM_THEME_PREFIX, THEMES, CardTheme
from bot.db.database import Database
from bot.db.repository import CustomShopRepository, ThemeRepository, UserRepository
from bot.models import User

logger = logging.getLogger(__name__)


class ThemeStatus(Enum):
    SUCCESS = "success"
    ALREADY = "already"
    NO_COINS = "no_coins"
    UNKNOWN = "unknown"
    BASE = "base"          # тема бесплатная, покупать не нужно
    ACTIVE = "active"      # тема уже активна


@dataclass(slots=True)
class ThemeOutcome:
    status: ThemeStatus
    theme_id: str = ""
    title: str = ""
    cost: int = 0
    missing: int = 0


class ThemeService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.themes = ThemeRepository(database)
        self.users = UserRepository(database)
        self.custom = CustomShopRepository(database)

    # --- каталог -----------------------------------------------------------
    @staticmethod
    def catalog() -> dict[str, CardTheme]:
        """Встроенные темы из конфига. Не ходит в базу."""
        return THEMES

    @staticmethod
    def is_custom(theme_id: str | None) -> bool:
        return bool(theme_id) and str(theme_id).startswith(CUSTOM_THEME_PREFIX)

    @staticmethod
    def custom_id(code: str) -> str:
        """Код товара из базы -> идентификатор темы для игрока."""
        return f"{CUSTOM_THEME_PREFIX}{code}"

    @staticmethod
    def custom_code(theme_id: str) -> str:
        return str(theme_id)[len(CUSTOM_THEME_PREFIX):]

    async def custom_catalog(self) -> dict[str, CardTheme]:
        """Темы, созданные админом в панели. Палитра достраивается из
        одного базового цвета по правилам градиента."""
        # Импорт локальный: custom_product_service уже опирается на
        # конфиг, а тема — на его же палитру.
        from bot.services.custom_product_service import build_theme, parse_hex

        result: dict[str, CardTheme] = {}
        for row in await self.custom.list_themes():
            rgb = parse_hex(row["base_color"])
            if rgb is None:
                logger.info("Тема %s пропущена: цвет %s не разобран",
                            row["code"], row["base_color"])
                continue
            theme_id = self.custom_id(row["code"])
            result[theme_id] = build_theme(
                theme_id, row["title"], rgb, int(row["cost"])
            )
        return result

    async def full_catalog(self) -> dict[str, CardTheme]:
        """Витрина целиком: встроенные темы плюс созданные админом."""
        return {**THEMES, **await self.custom_catalog()}

    async def custom_descriptions(self) -> dict[str, str]:
        """Описания кастомных тем по идентификатору темы."""
        rows = await self.custom.list_themes()
        return {
            self.custom_id(row["code"]): row["description"]
            for row in rows if row.get("description")
        }

    @staticmethod
    def exists(theme_id: str) -> bool:
        """Известна ли встроенная тема. Кастомные проверяет async-метод."""
        return theme_id in THEMES

    async def find(self, theme_id: str | None) -> CardTheme | None:
        """Тема по id из витрины — встроенная или кастомная."""
        if not theme_id:
            return None
        theme = THEMES.get(theme_id)
        if theme is not None:
            return theme
        if not self.is_custom(theme_id):
            return None
        return (await self.custom_catalog()).get(theme_id)

    async def resolve(self, theme_id: str | None) -> CardTheme:
        """Тема для карточки. Неизвестная откатывается к базовой."""
        return await self.find(theme_id) or THEMES["default"]

    # --- состояние ---------------------------------------------------------
    async def owned(self, user: User) -> set[str]:
        result = await self.themes.owned(user.user_id)
        # Базовая тема есть у всех и не покупается.
        result.add("default")
        return result

    @staticmethod
    def is_owned(user: User, theme_id: str, owned: set[str]) -> bool:
        return theme_id in owned

    # --- покупка -----------------------------------------------------------
    async def buy(self, user: User, theme_id: str) -> ThemeOutcome:
        theme = await self.find(theme_id)
        if theme is None:
            return ThemeOutcome(status=ThemeStatus.UNKNOWN, theme_id=theme_id)
        if theme.cost == 0:
            return ThemeOutcome(
                status=ThemeStatus.BASE, theme_id=theme_id, title=theme.title
            )

        async with self.db.transaction() as tx:
            fresh = await self.users.get_in_tx(tx, user.user_id)
            if fresh is None:  # pragma: no cover
                return ThemeOutcome(status=ThemeStatus.UNKNOWN, theme_id=theme_id)
            already = await self._owned_in_tx(tx, fresh.user_id, theme_id)
            if already:
                return ThemeOutcome(
                    status=ThemeStatus.ALREADY, theme_id=theme_id,
                    title=theme.title, cost=theme.cost,
                )
            if not await self.users.spend_balance_in_tx(tx, fresh.user_id, theme.cost):
                return ThemeOutcome(
                    status=ThemeStatus.NO_COINS, theme_id=theme_id,
                    title=theme.title, cost=theme.cost,
                    missing=theme.cost - fresh.balance,
                )
            await ThemeRepository.buy_in_tx(tx, fresh.user_id, theme_id)
            # Купленную тему сразу активируем — покупка «в стол» сбивает с толку.
            await self.users.set_theme_in_tx(tx, fresh.user_id, theme_id)
        logger.info("Игрок %s купил тему %s за %s", user.user_id, theme_id, theme.cost)
        return ThemeOutcome(
            status=ThemeStatus.SUCCESS, theme_id=theme_id,
            title=theme.title, cost=theme.cost,
        )

    async def _owned_in_tx(self, tx, user_id: int, theme_id: str) -> bool:
        if theme_id == "default":
            return True
        row = await tx.fetch_one(
            "SELECT 1 FROM user_themes WHERE user_id = ? AND theme = ?",
            (user_id, theme_id),
        )
        return row is not None

    # --- переключение ------------------------------------------------------
    async def activate(self, user: User, theme_id: str) -> ThemeOutcome:
        """Включить уже купленную тему. Переключение бесплатное."""
        theme = await self.find(theme_id)
        if theme is None:
            return ThemeOutcome(status=ThemeStatus.UNKNOWN, theme_id=theme_id)
        owned = await self.owned(user)
        if theme_id not in owned:
            return ThemeOutcome(
                status=ThemeStatus.NO_COINS, theme_id=theme_id, title=theme.title,
                cost=theme.cost, missing=theme.cost - user.balance,
            )
        if user.theme == theme_id:
            return ThemeOutcome(
                status=ThemeStatus.ACTIVE, theme_id=theme_id, title=theme.title
            )
        async with self.db.transaction() as tx:
            await self.users.set_theme_in_tx(tx, user.user_id, theme_id)
        logger.info("Игрок %s включил тему %s", user.user_id, theme_id)
        return ThemeOutcome(
            status=ThemeStatus.SUCCESS, theme_id=theme_id, title=theme.title
        )

    async def reset(self, user: User) -> ThemeOutcome:
        async with self.db.transaction() as tx:
            await self.users.set_theme_in_tx(tx, user.user_id, "default")
        return ThemeOutcome(
            status=ThemeStatus.SUCCESS, theme_id="default", title=THEMES["default"].title
        )
