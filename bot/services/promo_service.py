"""Активация промокодов: секретные Jarvis-коины и открытие секретной части."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from bot.db.database import Database
from bot.db.repository import PromoCodeRepository, PromoRepository, UserRepository

logger = logging.getLogger(__name__)


class PromoResult(Enum):
    SUCCESS = "success"
    ALREADY = "already"
    INVALID = "invalid"
    EXHAUSTED = "exhausted"


@dataclass(slots=True)
class PromoOutcome:
    result: PromoResult
    code: str
    jarvis_coins: int = 0
    unlock_secret: bool = False


class PromoService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.promo = PromoRepository(database)
        self.codes = PromoCodeRepository(database)
        self.users = UserRepository(database)

    @staticmethod
    def normalize_code(raw: str) -> str:
        return PromoCodeRepository.normalize(raw)

    async def is_known(self, code: str) -> bool:
        """Существует ли такой код (без учёта лимитов активаций)."""
        return await self.codes.exists(code)

    async def redeem(self, user_id: int, raw_code: str) -> PromoOutcome:
        """Активировать промокод. Регистр не важен, повторная активация запрещена."""
        code = self.normalize_code(raw_code)
        if not await self.codes.exists(code):
            return PromoOutcome(result=PromoResult.INVALID, code=code)

        # Одна транзакция: поиск кода, проверка лимита, запись о погашении
        # и начисление награды. Повторная активация отсекается первичным
        # ключом (user_id, code), лимит — проверкой uses/max_uses.
        async with self.db.transaction() as tx:
            settings = await PromoCodeRepository.find_usable_in_tx(tx, code)
            if settings is None:
                # Код существует, но выключен или исчерпал лимит активаций.
                redeemed = await self.promo.is_redeemed(user_id, code)
                return PromoOutcome(
                    result=PromoResult.ALREADY if redeemed else PromoResult.EXHAUSTED,
                    code=code,
                )

            fresh = await self.users.get_in_tx(tx, user_id)
            if fresh is None:
                await self.users.ensure_in_tx(tx, user_id)
                fresh = await self.users.get_in_tx(tx, user_id)
            if fresh is None:  # pragma: no cover
                return PromoOutcome(result=PromoResult.INVALID, code=code)

            inserted = await PromoRepository.redeem_in_tx(tx, user_id, code)
            if not inserted:
                return PromoOutcome(result=PromoResult.ALREADY, code=code)

            jarvis_coins = int(settings["jarvis_coins"])
            unlock = bool(settings["unlock_secret"])
            if jarvis_coins or unlock:
                await self.users.grant_jarvis_in_tx(tx, user_id, jarvis_coins, unlock)
            await PromoCodeRepository.consume_use_in_tx(tx, code)

        logger.info(
            "Игрок %s активировал промокод %s (+%s)",
            user_id, code, jarvis_coins,
        )
        return PromoOutcome(
            result=PromoResult.SUCCESS, code=code,
            jarvis_coins=jarvis_coins, unlock_secret=unlock,
        )
