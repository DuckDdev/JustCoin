"""Магазин апгрейдов за Jarvis-коины.

Правила покупки:
  * множители и сокращения кулдауна нельзя купить «вниз» (x3 → x2 запрещён);
  * сокращения кулдауна не складываются — покупка большего заменяет меньшее,
    иначе сумма сокращений обнулила бы кулдаун у всех;
  * апгрейд «instant» игрокам не продаётся, его выдаёт админ;
  * покупка атомарна: списание и применение эффекта в одной транзакции,
    поэтому двойное нажатие не спишет монеты дважды.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from bot.config import UPGRADES
from bot.db.database import Database
from bot.db.repository import UserRepository
from bot.models import User

logger = logging.getLogger(__name__)


class BuyStatus(Enum):
    SUCCESS = "success"
    LOCKED = "locked"          # секретная часть не открыта
    UNKNOWN = "unknown"        # такого апгрейда нет
    NOT_FOR_SALE = "for_sale"  # admin_only — только выдача админом
    ALREADY = "already"        # уже есть или апгрейд слабее текущего
    NO_COINS = "no_coins"


@dataclass(slots=True)
class BuyOutcome:
    status: BuyStatus
    upgrade_id: str = ""
    title: str = ""
    cost: int = 0
    # Что нужно, чтобы покупка стала возможной (для подсказки в UI).
    missing: int = 0


class UpgradeService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.users = UserRepository(database)

    # --- каталог -----------------------------------------------------------
    @staticmethod
    def catalog() -> dict[str, dict]:
        return UPGRADES

    @staticmethod
    def exists(upgrade_id: str) -> bool:
        return upgrade_id in UPGRADES

    @staticmethod
    def price(upgrade_id: str) -> int:
        return int(UPGRADES.get(upgrade_id, {}).get("cost", 0))

    @staticmethod
    def cost() -> int:
        """Цена стартового апгрейда x2 (для совместимости старых сообщений)."""
        return int(UPGRADES.get("x2", {}).get("cost", 1))

    @staticmethod
    def purchasable() -> list[tuple[str, dict]]:
        """Апгрейды, доступные игроку через магазин."""
        return [
            (uid, settings)
            for uid, settings in UPGRADES.items()
            if not settings.get("admin_only")
        ]

    # --- состояние ---------------------------------------------------------
    @staticmethod
    def is_owned(user: User, upgrade_id: str) -> bool:
        """Апгрейд уже применён к игроку."""
        settings = UPGRADES.get(upgrade_id)
        if settings is None:
            return False
        kind = settings["kind"]
        if kind == "multiplier":
            return user.multiplier >= settings["value"]
        if kind == "cooldown":
            return user.cooldown_reduction >= settings["value"]
        if kind == "instant":
            return user.has_instant
        return False

    @staticmethod
    def owned_ids(user: User) -> list[str]:
        return [uid for uid in UPGRADES if UpgradeService.is_owned(user, uid)]

    @staticmethod
    def _blocks_purchase(user: User, upgrade_id: str) -> bool:
        """True — покупка ничего не изменит (уже есть или апгрейд слабее)."""
        settings = UPGRADES[upgrade_id]
        kind = settings["kind"]
        if kind == "multiplier":
            return user.multiplier >= settings["value"]
        if kind == "cooldown":
            return user.cooldown_reduction >= settings["value"]
        if kind == "instant":
            return user.has_instant
        return False

    # --- покупка -----------------------------------------------------------
    async def buy(self, user: User, upgrade_id: str) -> BuyOutcome:
        """Покупка апгрейда. Возвращает LOCKED для незаблокированных —
        вызывающий код обязан тогда промолчать, как и для /jupgrade."""
        upgrade_id = upgrade_id.strip()
        settings = UPGRADES.get(upgrade_id)
        if settings is None:
            return BuyOutcome(status=BuyStatus.UNKNOWN, upgrade_id=upgrade_id)
        if not user.secret_unlocked:
            return BuyOutcome(status=BuyStatus.LOCKED, upgrade_id=upgrade_id)
        if settings.get("admin_only"):
            return BuyOutcome(
                status=BuyStatus.NOT_FOR_SALE, upgrade_id=upgrade_id,
                title=settings["title"],
            )

        cost = int(settings["cost"])
        async with self.db.transaction() as tx:
            fresh = await self.users.get_in_tx(tx, user.user_id)
            if fresh is None:  # pragma: no cover
                return BuyOutcome(status=BuyStatus.LOCKED, upgrade_id=upgrade_id)
            if not fresh.secret_unlocked:
                return BuyOutcome(status=BuyStatus.LOCKED, upgrade_id=upgrade_id)
            if self._blocks_purchase(fresh, upgrade_id):
                return BuyOutcome(
                    status=BuyStatus.ALREADY, upgrade_id=upgrade_id,
                    title=settings["title"], cost=cost,
                )
            if not await self.users.spend_jarvis_in_tx(tx, fresh.user_id, cost):
                return BuyOutcome(
                    status=BuyStatus.NO_COINS, upgrade_id=upgrade_id,
                    title=settings["title"], cost=cost,
                    missing=cost - fresh.jarvis_coins,
                )
            await self._apply_in_tx(tx, fresh.user_id, settings)
        logger.info(
            "Игрок %s купил апгрейд %s за %s", user.user_id, upgrade_id, cost
        )
        return BuyOutcome(
            status=BuyStatus.SUCCESS, upgrade_id=upgrade_id,
            title=settings["title"], cost=cost,
        )

    @staticmethod
    async def _apply_in_tx(tx, user_id: int, settings: dict) -> None:
        """Применяет эффект апгрейда. Вызывается внутри уже открытой транзакции."""
        kind = settings["kind"]
        value = int(settings["value"])
        if kind == "multiplier":
            await UserRepository.set_multiplier_in_tx(tx, user_id, value)
        elif kind == "cooldown":
            await UserRepository.set_cooldown_reduction_in_tx(tx, user_id, value)
        elif kind == "instant":
            await UserRepository.set_instant_in_tx(tx, user_id, True)

    # --- выдача админом ----------------------------------------------------
    async def grant(self, target: User, upgrade_id: str) -> bool:
        """Бесплатная выдача апгрейда (используется для admin_only)."""
        settings = UPGRADES.get(upgrade_id)
        if settings is None:
            return False
        async with self.db.transaction() as tx:
            fresh = await self.users.get_in_tx(tx, target.user_id)
            if fresh is None:  # pragma: no cover
                return False
            await self._apply_in_tx(tx, fresh.user_id, settings)
        return True

    async def set_instant(self, target: User, enabled: bool) -> bool:
        async with self.db.transaction() as tx:
            fresh = await self.users.get_in_tx(tx, target.user_id)
            if fresh is None:  # pragma: no cover
                return False
            await self.users.set_instant_in_tx(tx, fresh.user_id, enabled)
        logger.info(
            "Мгновенное получение %s для %s",
            "выдано" if enabled else "снято", target.user_id,
        )
        return True


def status_text(user: User) -> str:
    """Человеческое описание текущих апгрейдов игрока."""
    parts: list[str] = []
    if user.multiplier > 1:
        parts.append(f"x{user.multiplier} коины")
    if user.has_instant:
        parts.append("мгновенное получение")
    elif user.cooldown_reduction > 0:
        from bot.utils.formatters import hours_word, minutes_word

        minutes = user.cooldown_reduction // 60
        if minutes % 60 == 0:
            hours = minutes // 60
            parts.append(f"кулдаун −{hours} {hours_word(hours)}")
        else:
            parts.append(f"кулдаун −{minutes} {minutes_word(minutes)}")
    return ", ".join(parts) if parts else "нет апгрейдов"



