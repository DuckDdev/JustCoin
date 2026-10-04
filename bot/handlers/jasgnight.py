"""Магазин «Джасгнит»: покупка тем карточки за джаст коины.

Открыт всем игрокам (в отличие от секретного магазина апгрейдов):
тема — это внешний вид, секретом быть не может.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import messages
from bot.config import THEME_DESCRIPTIONS
from bot.handlers.common import safe_answer, touch_player, touch_user
from bot.models import User as Player
from bot.services.theme_service import ThemeService, ThemeStatus
from bot.utils.formatters import format_coins

logger = logging.getLogger(__name__)

BUY_PREFIX = "jasg:buy:"
APPLY_PREFIX = "jasg:apply:"
REFRESH = "jasg:refresh"


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="jasgnight")
    router.callback_query.register(
        buy_callback, F.data.startswith(BUY_PREFIX)
    )
    router.callback_query.register(
        apply_callback, F.data.startswith(APPLY_PREFIX)
    )
    router.callback_query.register(refresh_callback, F.data == REFRESH)
    return router


def _price(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _theme_description(theme_id: str, custom: dict[str, str]) -> str:
    """Описание темы: у созданной админом своё, у встроенной — из конфига."""
    return custom.get(theme_id) or THEME_DESCRIPTIONS.get(theme_id) or ""


def build_jasg_text(user: Player, owned: set[str], catalog: dict,
                    current_title: str,
                    links: dict[str, list[dict]] | None = None,
                    descriptions: dict[str, str] | None = None) -> str:
    links = links or {}
    descriptions = descriptions or {}
    lines = [
        messages.JASG_HEADER,
        messages.JASG_BALANCE.format(balance=_price(user.balance)),
        messages.JASG_CURRENT.format(title=current_title),
        messages.JASG_OWNED.format(count=len(owned)),
        "",
    ]
    for theme_id, theme in catalog.items():
        if theme_id in owned:
            mark = "✅ активна" if user.theme == theme_id else "✅ куплена"
        else:
            mark = f"🔒 {_price(theme.cost)} 🪙"
        line = f"{mark} <b>{theme.title}</b>"
        description = _theme_description(theme_id, descriptions)
        if description:
            line += f"\n    <i>{description}</i>"
        if links.get(theme_id):
            names = ", ".join(item["title"] for item in links[theme_id])
            line += f"\n    🔗 {names}"
        lines.append(line)
    lines.append(messages.JASG_HINT)
    return "\n".join(lines)


def build_jasg_keyboard(user: Player, owned: set[str], catalog: dict,
                        links: dict[str, list[dict]] | None = None
                        ) -> InlineKeyboardMarkup:
    links = links or {}
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for theme_id, theme in catalog.items():
        if theme_id in owned:
            label = "✅ " if user.theme == theme_id else "🎨 "
            current.append(
                InlineKeyboardButton(
                    text=f"{label}{theme.title}",
                    callback_data=f"{APPLY_PREFIX}{theme_id}",
                )
            )
        else:
            current.append(
                InlineKeyboardButton(
                    text=f"🔒 {theme.title} — {_price(theme.cost)}",
                    callback_data=f"{BUY_PREFIX}{theme_id}",
                )
            )
    if current:
        # Кнопки по две в ряд: длинный список не растягивает клавиатуру.
        for i in range(0, len(current), 2):
            rows.append(current[i:i + 2])
    # Ссылки конкретных тем — отдельными кнопками под каталогом.
    for theme_id, items in links.items():
        title = catalog.get(theme_id).title if theme_id in catalog else theme_id
        for item in items:
            rows.append([
                InlineKeyboardButton(text=f"🔗 {item['title']} — {title}",
                                     url=item["url"])
            ])
    rows.append([
        InlineKeyboardButton(text=messages.SHOP_REFRESH, callback_data=REFRESH)
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _jasg_view(data: dict[str, Any], player: Player) -> dict[str, Any]:
    """Всё, что нужно витрине: каталог, описание текущей темы, ссылки
    и описания. Собирается один раз на показ."""
    themes: ThemeService = data["themes"]
    catalog = await themes.full_catalog()
    current = await themes.resolve(player.theme)
    link_service = data.get("links")
    links: dict[str, list[dict]] = {}
    if link_service is not None:
        links = await link_service.product_links_bulk("theme", list(catalog))
    return {
        "catalog": catalog,
        "current_title": current.title,
        "links": links,
        "descriptions": await themes.custom_descriptions(),
    }


def _render_jasg(player: Player, owned: set[str], view: dict[str, Any]) -> tuple:
    """Текст и клавиатура витрины из подготовленного вида."""
    return (
        build_jasg_text(player, owned, view["catalog"], view["current_title"],
                        view["links"], view["descriptions"]),
        build_jasg_keyboard(player, owned, view["catalog"], view["links"]),
    )


def outcome_text(outcome) -> str:
    if outcome.status is ThemeStatus.SUCCESS:
        return messages.JASG_BOUGHT.format(title=outcome.title)
    if outcome.status is ThemeStatus.NO_COINS:
        return messages.JASG_NOT_ENOUGH.format(missing=_price(max(0, outcome.missing)))
    if outcome.status is ThemeStatus.ALREADY:
        return messages.JASG_ALREADY.format(title=outcome.title)
    if outcome.status is ThemeStatus.ACTIVE:
        return messages.JASG_ACTIVE.format(title=outcome.title)
    return messages.BOT_ERROR


async def buy_callback(callback: CallbackQuery, **data: Any) -> None:
    theme_id = (callback.data or "")[len(BUY_PREFIX):]
    if callback.message is None:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return
    player: Player = await touch_user(callback.from_user, data)
    themes: ThemeService = data["themes"]
    outcome = await themes.buy(player, theme_id)
    player = await touch_user(callback.from_user, data)
    owned = await themes.owned(player)
    notice = outcome_text(outcome)
    text, keyboard = _render_jasg(player, owned, await _jasg_view(data, player))
    try:
        await callback.message.edit_text(f"{notice}\n\n{text}", reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось обновить Джасгнит: %s", exc)
    await callback.answer(notice, show_alert=True)


async def apply_callback(callback: CallbackQuery, **data: Any) -> None:
    theme_id = (callback.data or "")[len(APPLY_PREFIX):]
    if callback.message is None:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return
    player: Player = await touch_user(callback.from_user, data)
    themes: ThemeService = data["themes"]
    outcome = await themes.activate(player, theme_id)
    player = await touch_user(callback.from_user, data)
    owned = await themes.owned(player)
    notice = outcome_text(outcome)
    text, keyboard = _render_jasg(player, owned, await _jasg_view(data, player))
    try:
        await callback.message.edit_text(f"{notice}\n\n{text}",
                                         reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось обновить Джасгнит: %s", exc)
    await callback.answer(notice, show_alert=True)


async def refresh_callback(callback: CallbackQuery, **data: Any) -> None:
    if callback.message is None:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return
    player: Player = await touch_user(callback.from_user, data)
    themes: ThemeService = data["themes"]
    owned = await themes.owned(player)
    text, keyboard = _render_jasg(player, owned, await _jasg_view(data, player))
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось обновить Джасгнит: %s", exc)
    await callback.answer()


async def open_shop(message: Message, **data: Any) -> None:
    """Точка входа из команд — держим для симметрии с shop.py."""
    player: Player = await touch_player(message, data)
    themes: ThemeService = data["themes"]
    owned = await themes.owned(player)
    text, keyboard = _render_jasg(player, owned, await _jasg_view(data, player))
    await safe_answer(message, text, reply_markup=keyboard)


__all__ = [
    "make_router", "build_jasg_text", "build_jasg_keyboard",
    "open_shop", "format_coins",
]
