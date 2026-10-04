"""Магазин апгрейдов: новый UI с кнопками.

Секретная часть: игрок, не активировавший промокод, не должен даже знать,
что магазин существует. Поэтому хендлеры зарегистрированы, но при
jarvis_unlocked = 0 выходят молча — как несуществующая команда.
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
from bot.handlers.common import safe_answer, touch_player, touch_user
from bot.models import User as Player
from bot.services.upgrade_service import (
    BuyStatus,
    UpgradeService,
    status_text as upgrades_status_text,
)
from bot.utils.formatters import format_duration

logger = logging.getLogger(__name__)

BUY_PREFIX = "shop:buy:"
REFRESH = "shop:refresh"


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="shop")
    router.message.register(open_shop, F.text.regexp(r"^/(jshop|jupgrade)\b"))
    router.callback_query.register(buy_callback, F.data.startswith(BUY_PREFIX))
    router.callback_query.register(refresh_callback, F.data == REFRESH)
    return router


def _price_label(price: int) -> str:
    return f"{price:,}".replace(",", " ")


def _upgrade_line(user: Player, upgrade_id: str, settings: dict,
                  links: list[dict] | None = None) -> str:
    """Строка каталога: цена, описание и пометка текущего состояния."""
    owned = UpgradeService.is_owned(user, upgrade_id)
    price = _price_label(int(settings["cost"]))
    if owned:
        mark = "✅"
    elif user.jarvis_coins >= int(settings["cost"]):
        mark = "🟢"
    else:
        mark = "⚪"
    line = (
        f"{mark} <b>{settings['title']}</b> — {price} 🔋\n"
        f"    <i>{settings['description']}</i>"
    )
    if links:
        # Названия ссылок повторяем и в тексте: кнопка под товаром не
        # всегда заметна, а по ссылке игрок может прийти сразу.
        names = ", ".join(item["title"] for item in links)
        line += f"\n    🔗 {names}"
    return line


def build_shop_text(user: Player,
                    links: dict[str, list[dict]] | None = None) -> str:
    links = links or {}
    catalog = UpgradeService.catalog()
    multipliers = [
        (uid, s) for uid, s in catalog.items() if s["kind"] == "multiplier"
    ]
    cooldowns = [
        (uid, s) for uid, s in catalog.items() if s["kind"] == "cooldown"
    ]

    lines = [
        messages.SHOP_HEADER,
        messages.SHOP_BALANCE.format(coins=_price_label(user.jarvis_coins)),
    ]
    owned = upgrades_status_text(user)
    lines.append(
        messages.SHOP_OWNED.format(status=owned) if owned != "нет апгрейдов"
        else "✅ Твои апгрейды: нет"
    )
    if not user.has_instant:
        lines.append(
            messages.SHOP_COOLDOWN_NOW.format(
                time=format_duration(user.effective_cooldown)
            )
        )

    lines.append(messages.SHOP_MULTIPLIERS)
    for upgrade_id, settings in sorted(multipliers, key=lambda i: i[1]["order"]):
        lines.append(
            _upgrade_line(user, upgrade_id, settings, links.get(upgrade_id))
        )

    lines.append(messages.SHOP_COOLDOWNS)
    for upgrade_id, settings in sorted(cooldowns, key=lambda i: i[1]["order"]):
        lines.append(
            _upgrade_line(user, upgrade_id, settings, links.get(upgrade_id))
        )

    lines.append(messages.SHOP_HINT)
    return "\n".join(lines)


def _link_row(title: str, url: str) -> list[InlineKeyboardButton]:
    """Кнопка-ссылка. url=None — обычная кнопка с callback."""
    button = (InlineKeyboardButton(text=title, url=url) if url
              else InlineKeyboardButton(text=title, callback_data=REFRESH))
    return [button]


def build_shop_keyboard(user: Player,
                        links: dict[str, list[dict]] | None = None) -> InlineKeyboardMarkup:
    links = links or {}
    catalog = UpgradeService.catalog()
    rows: list[list[InlineKeyboardButton]] = []
    for kind in ("multiplier", "cooldown"):
        row: list[InlineKeyboardButton] = []
        entries = sorted(
            ((uid, s) for uid, s in catalog.items() if s["kind"] == kind),
            key=lambda item: item[1]["order"],
        )
        for upgrade_id, settings in entries:
            owned = UpgradeService.is_owned(user, upgrade_id)
            label = f"{settings['title']} — {'✅' if owned else _price_label(int(settings['cost'])) + ' 🔋'}"
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{BUY_PREFIX}{upgrade_id}",
                )
            )
        if row:
            rows.append(row)
    # Ссылки товаров идут отдельными кнопками под каталогом: так их
    # видно независимо от того, как встали кнопки покупки.
    for upgrade_id, items in links.items():
        title = catalog.get(upgrade_id, {}).get("title", upgrade_id)
        for item in items:
            rows.append(_link_row(f"🔗 {item['title']} — {title}", item["url"]))
    rows.append([
        InlineKeyboardButton(text=messages.SHOP_REFRESH, callback_data=REFRESH)
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _shop_links(data: dict[str, Any]) -> dict[str, list[dict]]:
    """Ссылки всех апгрейдов витрины — одним запросом."""
    link_service = data.get("links")
    if link_service is None:
        return {}
    codes = list(UpgradeService.catalog())
    return await link_service.product_links_bulk("upgrade", codes)


def outcome_text(outcome) -> str:
    """Человеческий текст результата покупки."""
    if outcome.status is BuyStatus.SUCCESS:
        return messages.SHOP_BOUGHT.format(title=outcome.title)
    if outcome.status is BuyStatus.NO_COINS:
        return messages.SHOP_NOT_ENOUGH.format(
            missing=_price_label(max(0, outcome.missing))
        )
    if outcome.status is BuyStatus.ALREADY:
        return messages.SHOP_ALREADY_OWNED
    if outcome.status is BuyStatus.NOT_FOR_SALE:
        return messages.SHOP_NOT_FOR_SALE
    if outcome.status is BuyStatus.UNKNOWN:
        return messages.SHOP_UNKNOWN
    return messages.BOT_ERROR


async def open_shop(message: Message, **data: Any) -> None:
    """/jshop и /jupgrade открывают магазин (секретно)."""
    player: Player = await touch_player(message, data)
    if not player.secret_unlocked:
        logger.debug("Магазин проигнорирован для %s", player.user_id)
        return
    links = await _shop_links(data)
    await safe_answer(
        message,
        build_shop_text(player, links),
        reply_markup=build_shop_keyboard(player, links),
    )


async def _show_shop(callback: CallbackQuery, player: Player,
                     data: dict[str, Any]) -> None:
    links = await _shop_links(data)
    text = build_shop_text(player, links)
    keyboard = build_shop_keyboard(player, links)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001 - «message is not modified»
        logger.debug("Не удалось обновить магазин: %s", exc)
    await callback.answer()


async def refresh_callback(callback: CallbackQuery, **data: Any) -> None:
    if callback.message is None:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return
    player: Player = await touch_user(callback.from_user, data)
    if not player.secret_unlocked:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return
    await _show_shop(callback, player, data)


async def buy_callback(callback: CallbackQuery, **data: Any) -> None:
    # Кнопка может висеть на сообщении, которого уже нет (например, его
    # удалили). Отвечаем всплывающим окном, а не падаем.
    if callback.message is None:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return
    upgrade_id = (callback.data or "")[len(BUY_PREFIX):]
    # Профиль берём у callback, а не у сообщения: у сообщения, к которому
    # привязана кнопка, from_user может отсутствовать.
    player: Player = await touch_user(callback.from_user, data)
    if not player.secret_unlocked:
        # Секретная часть закрыта — не подтверждаем существование магазина.
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return

    upgrades: UpgradeService = data["upgrade"]
    outcome = await upgrades.buy(player, upgrade_id)
    if outcome.status is BuyStatus.LOCKED:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return

    # Показываем результат покупки и сразу обновлённый магазин.
    player = await touch_user(callback.from_user, data)
    notice = outcome_text(outcome)
    # Сообщение могло исчезнуть между нажатием и обработкой.
    message = callback.message
    if message is not None:
        links = await _shop_links(data)
        try:
            await message.edit_text(
                f"{notice}\n\n{build_shop_text(player, links)}",
                reply_markup=build_shop_keyboard(player, links),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось обновить магазин: %s", exc)
    await callback.answer(notice, show_alert=True)
