"""Курьер: посылки в БД, фоновая доставка и выдача по кнопке."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import messages
from bot.config import COURIER_CHECK_INTERVAL
from bot.db.database import Database
from bot.db.repository import PackageRepository
from bot.models import Package
from bot.services.user_service import UserService
from bot.utils.formatters import format_coins, format_duration, utcnow

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "package:"


class TakeResult(Enum):
    TAKEN = "taken"
    NOT_YOURS = "not_yours"
    ALREADY = "already"
    IN_TRANSIT = "in_transit"
    NOT_FOUND = "not_found"


def package_keyboard(package_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=messages.PACKAGE_BUTTON,
                    callback_data=f"{CALLBACK_PREFIX}{package_id}",
                )
            ]
        ]
    )


def courier_arrival_text(seconds: int, amount: int) -> str:
    return messages.COURIER_ARRIVED.format(
        time=format_duration(seconds), amount=format_coins(amount)
    )


class CourierService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.packages = PackageRepository(database)
        self.users = UserService(database)

    # --- фоновая доставка --------------------------------------------------
    async def notify_due(self, bot: Bot, maintenance=None) -> int:
        """Отправляет кнопку «Забрать посылку» всем, у кого посылка доставлена."""
        due = await self.packages.due()
        sent = 0
        for package in due:
            if maintenance is not None and await maintenance.is_disabled("courier"):
                # Во время техработ курьера не дёргаем игроков,
                # но и не помечаем как отправленные — вернёмся позже.
                break
            try:
                await bot.send_message(
                    chat_id=package.user_id,
                    text=messages.PACKAGE_AMOUNT.format(amount=format_coins(package.amount)),
                    reply_markup=package_keyboard(package.id),
                )
            except Exception as exc:  # noqa: BLE001 - игрок мог заблокировать бота
                logger.warning(
                    "Не удалось отправить посылку %s игроку %s: %s",
                    package.id, package.user_id, exc,
                )
                # Пользователь заблокировал бота — больше не пытаемся.
                await self.packages.mark_notified(package.id)
                continue
            await self.packages.mark_notified(package.id)
            sent += 1
            logger.info(
                "📦 Курьер прибыл к игроку %s, посылка %s",
                package.user_id, format_coins(package.amount),
            )
        return sent

    async def run_polling(self, bot: Bot, maintenance=None) -> None:
        """Фоновый цикл: проверка доставленных посылок и напоминаний."""
        while True:
            try:
                await self.notify_due(bot, maintenance)
                await self._remind_pending(bot)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - цикл не должен падать
                logger.exception("Ошибка в цикле курьера")
            await asyncio.sleep(COURIER_CHECK_INTERVAL)

    async def _remind_pending(self, bot: Bot) -> None:
        """Раз в час напоминаем о посылках, которые ещё в пути."""
        now = utcnow()
        rows = await self.db.fetch_all(
            "SELECT DISTINCT user_id FROM packages "
            "WHERE delivered = 0 AND deliver_at > ? AND deliver_at <= ?",
            (int(now.timestamp()), int(now.timestamp()) + 3600),
        )
        for row in rows:
            packages = await self.packages.pending_for_user(row["user_id"])
            ready = [p for p in packages if p.is_ready(now)]
            pending = [p for p in packages if not p.is_delivered]
            if not pending:
                continue
            soonest = min(pending, key=lambda p: p.deliver_at)
            if ready:
                text = courier_arrival_text(0, soonest.amount)
            else:
                text = messages.PACKAGE_IN_TRANSIT.format(
                    time=format_duration(soonest.time_left(now))
                )
            try:
                await bot.send_message(
                    chat_id=row["user_id"],
                    text=text,
                    reply_markup=package_keyboard(soonest.id),
                )
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось напомнить о посылке %s", row["user_id"])

    # --- выдача посылки ----------------------------------------------------
    async def take(self, user_id: int, package_id: int) -> tuple[TakeResult, Package | None]:
        """Атомарно выдать посылку.

        Повторное нажатие вернёт ALREADY, нажатие раньше срока — IN_TRANSIT.
        """
        package = await self.packages.get(package_id)
        if package is None:
            return TakeResult.NOT_FOUND, None
        if package.user_id != user_id:
            return TakeResult.NOT_YOURS, None
        if package.is_delivered:
            return TakeResult.ALREADY, None
        now = utcnow()
        if not package.is_ready(now):
            # Курьер ещё едет: посылку выдать раньше времени нельзя.
            return TakeResult.IN_TRANSIT, package
        delivered = await self.packages.deliver(package_id, user_id)
        if delivered is None:
            # Посылку успели забрать раньше (двойное нажатие).
            return TakeResult.ALREADY, None
        return TakeResult.TAKEN, delivered

    async def transit_text(self, package: Package) -> str:
        return messages.PACKAGE_IN_TRANSIT.format(
            time=format_duration(package.time_left(utcnow()))
        )

    @staticmethod
    def take_error_text(result: TakeResult) -> str:
        if result is TakeResult.ALREADY:
            return messages.PACKAGE_ALREADY_TAKEN
        if result is TakeResult.NOT_YOURS:
            return messages.PACKAGE_NOT_YOURS
        return messages.BOT_ERROR
