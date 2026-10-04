"""Текстовые триггеры: «Джасткоины», «Топ», код промокода и ссылка $JustID."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from bot.handlers.commands import cmd_jcoin, cmd_jtop, show_card_by_just_id

logger = logging.getLogger(__name__)

# Триггеры в нижнем регистре: сравнение регистронезависимое.
CLAIM_TRIGGERS = {"джасткоины", "джасткоин", "джасткоина", "justcoin", "justcoins"}
TOP_TRIGGERS = {"топ", "top"}


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="texts")
    router.message.register(private_text, F.chat.type == ChatType.PRIVATE, F.text)
    router.message.register(
        group_text, F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text
    )
    return router


async def private_text(message: Message, **data: Any) -> None:
    """В личке любой текст пробуем считать ссылкой, кодом промокода."""
    await _try_just_id(message, **data)
    await _try_promo_code(message, **data)


async def group_text(message: Message, **data: Any) -> None:
    """В группах реагируем на явные триггеры и ссылки $JustID."""
    text = (message.text or "").strip().lower()
    if text in CLAIM_TRIGGERS:
        await cmd_jcoin(message, **data)
    elif text in TOP_TRIGGERS:
        await cmd_jtop(message, **data)
    else:
        await _try_just_id(message, **data)


async def _try_just_id(message: Message, **data: Any) -> None:
    """Сообщение вида «$John» открывает карточку игрока.

    Молча игнорируем, если такой ссылки нет: в группе $часто встречается
    как обычный символ валюты, а лишняя ошибка на каждый такой символ
    только шумит.
    """
    from bot import messages
    from bot.handlers.common import maintenance_text, safe_answer
    from bot.services.justid_service import looks_like_just_id

    text = (message.text or "").strip()
    if not looks_like_just_id(text):
        return

    blocked = await maintenance_text(data, "stats", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return

    if await show_card_by_just_id(message, text, data):
        return

    # Неизвестная ссылка в личке — подсказываем формат; в группе молчим,
    # чтобы не реагировать на символ валюты в обычной реплике.
    if message.chat.type == ChatType.PRIVATE:
        await safe_answer(message, messages.JUSTID_LINK_UNKNOWN.format(just_id=text))


async def _try_promo_code(message: Message, **data: Any) -> None:
    from bot import messages  # локальный импорт: избегаем цикла на верхнем уровне
    from bot.handlers.common import maintenance_text, safe_answer, touch_player
    from bot.services.promo_service import PromoResult, PromoService
    text = (message.text or "").strip()
    if not text or "\n" in text:
        return

    lowered = text.lower()
    if lowered in CLAIM_TRIGGERS:
        await cmd_jcoin(message, **data)
        return
    if lowered in TOP_TRIGGERS:
        await cmd_jtop(message, **data)
        return

    promo: PromoService = data["promo"]
    # Молча игнорируем обычную переписку: отвечаем только на существующие коды.
    if not await promo.is_known(text):
        return

    blocked = await maintenance_text(data, "promo", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return

    await touch_player(message, data)
    outcome = await promo.redeem(message.from_user.id, text)
    if outcome.result is PromoResult.INVALID:
        await safe_answer(message, messages.PROMO_INVALID)
    elif outcome.result is PromoResult.ALREADY:
        await safe_answer(message, messages.PROMO_ALREADY)
    elif outcome.result is PromoResult.EXHAUSTED:
        await safe_answer(message, messages.PROMO_EXHAUSTED)
    elif outcome.unlock_secret:
        await safe_answer(
            message,
            messages.PROMO_JARVIS_ACTIVATED.format(amount=outcome.jarvis_coins),
        )
    elif outcome.jarvis_coins > 0:
        await safe_answer(
            message,
            messages.PROMO_COINS_ACTIVATED.format(
                amount=f"{outcome.jarvis_coins:,}".replace(",", " ")
            ),
        )
    else:
        await safe_answer(message, messages.PROMO_GENERIC)
