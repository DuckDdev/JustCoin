"""Сборка статистики игрока для карточки и текстового ответа."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import User as TgUser

from bot import messages
from bot.config import CURRENCY_NAME
from bot.db.database import Database
from bot.models import User
from bot.services.status_service import StatusService
from bot.services.theme_service import ThemeService
from bot.services.user_service import UserService
from bot.utils.card import CardData
from bot.utils.formatters import format_coins, format_date

logger = logging.getLogger(__name__)


def upgrade_status(user: User) -> str:
    """Краткое описание апгрейдов игрока для карточки и фолбэка."""
    parts: list[str] = []
    if user.multiplier > 1:
        parts.append(f"x{user.multiplier}")
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
    return ", ".join(parts) if parts else "нет"


class StatsService:
    def __init__(self, database: Database) -> None:
        self.user_service = UserService(database)
        self.themes = ThemeService(database)

    async def build(self, user: User) -> CardData:
        place, total = await self.user_service.place_and_total(user)
        # В шапке карточки — человеческое имя, @username отдельной строкой.
        human = " ".join(p for p in (user.first_name, user.last_name) if p)
        # Тема и статус берутся из профиля: карточка должна выглядеть так,
        # как игрок её настроил, независимо от того, кто её открывает.
        theme = await self.themes.resolve(user.theme)
        status = StatusService.effective(user)
        return CardData(
            name=user.name,
            username=user.username,
            display_name=human or user.name,
            balance=user.balance,
            place=place,
            total=total,
            total_earned=user.total_earned,
            claims_count=user.claims_count,
            packages_count=user.packages_count,
            registered=format_date(user.created_at),
            jarvis_unlocked=user.secret_unlocked,
            jarvis_coins=user.jarvis_coins,
            has_upgrade=user.multiplier > 1,
            upgrades=upgrade_status(user),
            theme=theme,
            status_label=status.label if status else "",
            status_color=status.color if status else None,
        )

    @staticmethod
    def caption() -> str:
        return messages.STATS_CAPTION.format(currency=CURRENCY_NAME)

    @staticmethod
    async def download_avatar(bot: Bot, tg_user: TgUser) -> bytes | None:
        """Аватарка Telegram. При любой ошибке возвращаем None — будет заглушка."""
        try:
            if tg_user.photo:
                source = tg_user.photo[-1]
            else:
                source = await tg_user.get_profile_photo(bot)
                if not source:
                    return None
            return await bot.download(source.file_id)
        except Exception as exc:  # noqa: BLE001 - фото может быть недоступно
            logger.debug("Не удалось скачать аватар %s: %s", tg_user.id, exc)
            return None

    @staticmethod
    def fallback_text(user: User, data: CardData) -> str:
        """Текстовая статистика — используется, если карточка не отрисовалась."""
        text = messages.STATS_FALLBACK.format(
            name=user.name,
            balance=format_coins(data.balance),
            place=data.place,
            total=data.total,
            earned=format_coins(data.total_earned),
            claims=data.claims_count,
            packages=data.packages_count,
            since=data.registered,
        )
        if data.jarvis_unlocked:
            # Секретный блок виден только разблокированным игрокам.
            text += (
                f"\n\n🤖 Jarvis-коинов: {data.jarvis_coins}\n"
                f"⚡ Апгрейды: {data.upgrades}"
            )
        # Тема и статус важны и в текстовом ответе: карточка могла не
        # отрисоваться, а игрок всё равно должен увидеть свой профиль.
        details = [f"🎨 Тема: {data.theme.title}"]
        if data.status_label:
            details.append(f"🏅 Статус: {data.status_label}")
        text += "\n" + "\n".join(details)
        return text
