"""Экономика: получение коинов, кулдаун, апгрейд x2, событие курьера."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from bot.config import (
    CLAIM_COOLDOWN_SECONDS,
    CLAIM_MAX_AMOUNT,
    CLAIM_MIN_AMOUNT,
    COURIER_CHANCE,
    COURIER_DELAY_MAX_SECONDS,
    COURIER_DELAY_MIN_SECONDS,
    COURIER_PACKAGE_MAX,
    COURIER_PACKAGE_MIN,
    is_admin,
)
from bot.db.database import Database
from bot.db.repository import PackageRepository, UserRepository
from bot.models import User, Package
from bot.utils.formatters import from_timestamp, random_float, random_int_between, utcnow

logger = logging.getLogger(__name__)


class ClaimStatus(Enum):
    SUCCESS = "success"
    COOLDOWN = "cooldown"


@dataclass(slots=True)
class ClaimResult:
    status: ClaimStatus
    amount: int = 0
    cooldown_left: int = 0
    upgraded: bool = False
    package: Package | None = None
    courier_seconds: int = 0
    balance: int = 0
    # «Замедление»: во сколько раз выросло ожидание из-за простоя бота.
    slowdown_multiplier: int = 1
    offline_seconds: int = 0


class EconomyService:
    def __init__(self, database: Database, slowdown=None) -> None:
        self.db = database
        self.users = UserRepository(database)
        self.packages = PackageRepository(database)
        # Замедление внедряется из main.py: сервис нужен и сам по себе,
        # поэтому здесь остаётся необязательным.
        self.slowdown = slowdown
        # Момент, когда бот последний раз выходил на связь. Всё, что было
        # до него, — простой, во время которого игроки не могли получить
        # коины. Обновляется при старте.
        self._online_since: int | None = None

    def mark_online(self, moment: int | None = None) -> None:
        """Отмечает момент выхода бота на связь."""
        self._online_since = moment or int(utcnow().timestamp())

    def mark_offline(self) -> None:
        self._online_since = None

    def offline_since(self) -> float:
        """Метка последнего выхода на связь, в UNIX-секундах.

        Сравнивается с last_claim_at, который в репозитории приводится
        к datetime, поэтому отдаём метку в том же формате.
        """
        if not self._online_since:
            # Бот ещё не запускался: считаем, что простоя не было,
            # иначе первый же запрос трактовался бы как простой.
            return from_timestamp(0)
        return from_timestamp(self._online_since)

    @staticmethod
    def _cooldown_for(user: User, slowdown_multiplier: int) -> int:
        base = user.effective_cooldown
        if slowdown_multiplier > 1:
            base *= slowdown_multiplier
        return base

    @staticmethod
    def cooldown() -> int:
        return CLAIM_COOLDOWN_SECONDS

    @staticmethod
    def roll_amount(multiplier: int) -> int:
        base = random_int_between(CLAIM_MIN_AMOUNT, CLAIM_MAX_AMOUNT)
        return base * max(1, multiplier)

    async def claim(self, user: User) -> ClaimResult:
        """Начислить коины. Вся операция — одна транзакция, поэтому двойное
        нажатие не начислит дважды: второй запрос увидит свежий last_claim_at.

        Кулдаун берётся из строки игрока: он учитывает купленные сокращения
        и мгновенное получение. У администраторов кулдауна нет вовсе.
        """
        now = utcnow()
        now_ts = int(now.timestamp())

        async with self.db.transaction() as tx:
            fresh = await self.users.get_in_tx(tx, user.user_id)
            if fresh is None:  # pragma: no cover - игрок создаётся до вызова
                raise RuntimeError("Игрок не найден")

            # Админы получают коины без ожидания. Метку времени всё равно
            # обновляем, иначе при снятии прав у игрока накопится
            # огромный остаток кулдауна.
            slowdown_multiplier = 1
            offline_seconds = 0
            left = 0 if is_admin(fresh.user_id) else fresh.cooldown_left(now)
            if left > 0:
                # Простой бота увеличивает ожидание: игрок не мог получить
                # коины, пока бот был выключен.
                if self.slowdown is not None and fresh.last_claim_at is not None:
                    if fresh.last_claim_at < self.offline_since():
                        offline_seconds = int(
                            (now - fresh.last_claim_at).total_seconds()
                        )
                        # Именно in_tx: claim уже держит транзакцию,
                        # а вложенная ждала бы сама себя до вечности.
                        slowdown_multiplier = (
                            await self.slowdown.register_offline_in_tx(
                                tx, fresh, offline_seconds
                            )
                        )
                left = self._cooldown_for(fresh, slowdown_multiplier) - int(
                    (now - fresh.last_claim_at).total_seconds()
                )
                left = max(0, left)
                if left > 0:
                    return ClaimResult(
                        status=ClaimStatus.COOLDOWN,
                        cooldown_left=left,
                        balance=fresh.balance,
                        slowdown_multiplier=slowdown_multiplier,
                        offline_seconds=offline_seconds,
                    )

            multiplier = max(1, fresh.multiplier)
            amount = self.roll_amount(multiplier)

            package: Package | None = None
            courier_seconds = 0
            if random_float() < COURIER_CHANCE:
                courier_amount = random_int_between(
                    COURIER_PACKAGE_MIN, COURIER_PACKAGE_MAX
                ) * multiplier  # курьер тоже удваивается при апгрейде
                courier_seconds = random_int_between(
                    COURIER_DELAY_MIN_SECONDS, COURIER_DELAY_MAX_SECONDS
                )
                deliver_at = now + timedelta(seconds=courier_seconds)
                package_id = await PackageRepository.create_in_tx(
                    tx, fresh.user_id, courier_amount, int(deliver_at.timestamp())
                )
                package = Package(
                    id=package_id,
                    user_id=fresh.user_id,
                    amount=courier_amount,
                    deliver_at=deliver_at,
                    created_at=now,
                )

            updated = await self.users.apply_claim_in_tx(
                tx, fresh.user_id, amount, now_ts,
                0 if is_admin(fresh.user_id) else self._cooldown_for(
                    fresh, 1
                ),
            )
            if updated is None:
                # Кулдаун мог выставиться конкурентным запросом
                # (например, два одновременных обновления).
                current = await self.users.get_in_tx(tx, fresh.user_id)
                if current is None:  # pragma: no cover
                    return ClaimResult(status=ClaimStatus.COOLDOWN, cooldown_left=0)
                return ClaimResult(
                    status=ClaimStatus.COOLDOWN,
                    cooldown_left=current.cooldown_left(now),
                    balance=current.balance,
                )

        return ClaimResult(
            status=ClaimStatus.SUCCESS,
            amount=amount,
            upgraded=multiplier > 1,
            package=package,
            courier_seconds=courier_seconds,
            balance=updated.balance if updated else user.balance,
            slowdown_multiplier=slowdown_multiplier,
            offline_seconds=offline_seconds,
        )
