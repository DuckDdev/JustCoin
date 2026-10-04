"""Админ-панель: все административные функции в одном месте.

Разделы: техработы, промокоды, товары Jarvis Shop, темы Джасгнита,
баны, статусы, отправка сообщений в найденные чаты, статистика.

Диалоги (создание товара, ввод причины, отправка в чат) хранятся в
словаре состояний прямо здесь: aiogram-FSM для трёх коротких шагов —
избыточная сложность, а состояние живёт ровно одну сессию админа.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import messages
from bot.config import MAINTENANCE_FEATURES, is_admin
from bot.handlers.common import safe_answer
from bot.services.ban_service import BanService
from bot.services.link_service import LinkService
from bot.services.theme_service import ThemeService
from bot.services.upgrade_service import UpgradeService

logger = logging.getLogger(__name__)


class HasDialog(BaseFilter):
    """Пропускает сообщение, только если у админа открыт диалог панели.

    Без этого фильтра хендлер ловил бы любой текст и обрывал
    обработку: в aiogram первый подошедший хендлер завершает цепочку,
    поэтому /jcoin и прочее просто переставали бы работать.
    """

    async def __call__(self, message: Message) -> bool:
        if not is_admin(message.from_user.id):
            return False
        return message.from_user.id in DIALOGS

ROOT = "ap:"
SEC_MAINT = ROOT + "maint"
SEC_PROMO = ROOT + "promo"
SEC_SHOP = ROOT + "shop"
SEC_THEMES = ROOT + "themes"
SEC_BANS = ROOT + "bans"
SEC_STATUS = ROOT + "status"
SEC_CHATS = ROOT + "chats"
SEC_STATS = ROOT + "stats"
BACK = ROOT + "back"

MAINT_PREFIX = ROOT + "maint:"
PROMO_PREFIX = ROOT + "promo:"
SHOP_PREFIX = ROOT + "shop:"
THEME_PREFIX = ROOT + "theme:"
CHAT_PREFIX = ROOT + "chat:"
CHATS_PAGE = ROOT + "chats:page:"
SEC_LINKS = ROOT + "links"
LINKS_PREFIX = ROOT + "link:"
BOT_LINK_PREFIX = ROOT + "bl:"
PICK_PREFIX = ROOT + "pick:"
PLINK_DEL = ROOT + "plinkdel:"
PICK_DONE = ROOT + "pickdone"
ITEM_DONE = ROOT + "itemdone"

PAGE_SIZE = 8


@dataclass
class Dialog:
    """Состояние одного диалога администратора."""
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


# Активные диалоги: user_id -> Dialog. Сбрасываются при /apanel_cancel.
DIALOGS: dict[int, Dialog] = {}


def reset_dialog(user_id: int) -> None:
    DIALOGS.pop(user_id, None)


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="admin_panel")
    router.message.register(open_panel, Command("apanel", "admin", "panel"))
    router.message.register(cancel_dialog, Command("apanel_cancel", "panel_cancel"))
    # Диалоговые шаги ловятся раньше разделов, но ТОЛЬКО когда диалог
    # реально открыт — иначе панель съела бы весь обычный текст.
    router.message.register(dialog_text, HasDialog(), F.text)
    router.callback_query.register(navigate, F.data.startswith(ROOT))
    return router


# --- Служебное ----------------------------------------------------------------
def _price(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _denied(event) -> bool:
    """True — событие не от администратора, обрабатывать нельзя."""
    return not is_admin(event.from_user.id)


# --- Главное меню --------------------------------------------------------------
def root_keyboard() -> InlineKeyboardMarkup:
    return _kb(
        [
            _btn(messages.AP_SECTION_MAINT, SEC_MAINT),
            _btn(messages.AP_SECTION_PROMO, SEC_PROMO),
        ],
        [
            _btn(messages.AP_SECTION_SHOP, SEC_SHOP),
            _btn(messages.AP_SECTION_THEMES, SEC_THEMES),
        ],
        [
            _btn(messages.AP_SECTION_BANS, SEC_BANS),
            _btn(messages.AP_SECTION_STATUS, SEC_STATUS),
        ],
        [
            _btn(messages.AP_SECTION_LINKS, SEC_LINKS),
            _btn(messages.AP_SECTION_CHATS, SEC_CHATS),
        ],
        [
            _btn(messages.AP_SECTION_STATS, SEC_STATS),
        ],
    )


def panel_text() -> str:
    return f"{messages.AP_TITLE}\n{messages.AP_HINT}"


async def open_panel(message: Message, **data: Any) -> None:
    """/apanel — главное меню администратора."""
    if _denied(message):
        await safe_answer(message, messages.AP_DENIED)
        return
    reset_dialog(message.from_user.id)
    await safe_answer(
        message, panel_text(), reply_markup=root_keyboard()
    )


async def cancel_dialog(message: Message, **data: Any) -> None:
    if not is_admin(message.from_user.id):
        return
    had = DIALOGS.pop(message.from_user.id, None) is not None
    await safe_answer(
        message,
        messages.AP_ITEM_CANCEL if had else "Нечего отменять",
    )


# --- Навигация ----------------------------------------------------------------
async def navigate(callback: CallbackQuery, **data: Any) -> None:
    """Единая точка входа для всех кнопок панели."""
    if _denied(callback):
        await callback.answer(messages.AP_DENIED, show_alert=True)
        return
    payload = (callback.data or "")[len(ROOT):]
    if not payload or payload == "back":
        reset_dialog(callback.from_user.id)
        await _render(callback, panel_text(), root_keyboard())
        return

    # Действия диалогов: отмена, выбор типа товара, выбор чата.
    if payload == "cancel":
        reset_dialog(callback.from_user.id)
        await _render(callback, panel_text(), root_keyboard())
        return
    if payload.startswith("type:"):
        await _choose_item_type(callback, payload[5:], data)
        return
    if payload.startswith("chat:"):
        await _choose_chat(callback, payload[5:], data)
        return

    # Ссылки бота и привязка ссылки к товару.
    if payload.startswith("bl:"):
        await _bot_link_action(callback, payload[3:], data)
        return
    if payload.startswith("link:"):
        await _link_screen(callback, payload[5:], data)
        return
    if payload == "nodesc":
        await _item_desc_skip(callback, data)
        return
    if payload == "itemdone":
        await _item_done(callback, data)
        return
    if payload.startswith("plinkdel:"):
        await _delete_product_link(callback, payload[9:], data)
        return
    if payload.startswith("pick:"):
        await _pick_product(callback, payload[5:], data)
        return

    section = payload.split(":", 1)[0]
    handlers = {
        "maint": _section_maint,
        "promo": _section_promo,
        "shop": _section_shop,
        "themes": _section_themes,
        "bans": _section_bans,
        "status": _section_status,
        "chats": _section_chats,
        "stats": _section_stats,
        "links": _section_links,
    }
    handler = handlers.get(section)
    if handler is None:
        await callback.answer("Неизвестный раздел", show_alert=True)
        return
    await handler(callback, payload, data)


async def _choose_item_type(callback: CallbackQuery, kind: str,
                            data: dict[str, Any]) -> None:
    """Шаг 2→3: админ выбрал тип, спрашиваем значение."""
    dialog = DIALOGS.get(callback.from_user.id)
    if dialog is None or dialog.kind != "item_type":
        await callback.answer("Создание отменено, начните заново", show_alert=True)
        return

    hints = {
        "multiplier": "Пример: 5 — начисление будет ×5",
        "cooldown": "Укажите минуты. Пример: 90 — кулдаун короче на 90 минут",
        "theme": "",
    }
    DIALOGS[callback.from_user.id] = Dialog(
        kind="item_value",
        data={
            "title": dialog.data["title"],
            "cost": dialog.data["cost"],
            "scope": dialog.data.get("scope", "shop"),
            "item_kind": kind,
        },
    )
    if kind == "theme":
        text = messages.AP_ITEM_THEME_PROMPT.format(title=dialog.data["title"])
    else:
        text = messages.AP_ITEM_VALUE_PROMPT.format(
            title=dialog.data["title"], hint=hints.get(kind, "")
        )
    await _render(
        callback, text, _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")])
    )


async def _choose_chat(callback: CallbackQuery, raw: str,
                       data: dict[str, Any]) -> None:
    """Админ выбрал чат — ждём текст сообщения."""
    if not raw.lstrip("-").isdigit():
        await callback.answer("Неверный чат", show_alert=True)
        return
    chat_id = int(raw)
    chats = await data["announce"].chats.all(200)
    title = next(
        (_chat_title(chat) for chat in chats if chat["chat_id"] == chat_id),
        f"Чат {chat_id}",
    )
    DIALOGS[callback.from_user.id] = Dialog(
        kind="chat_send", data={"chat_id": chat_id, "title": title}
    )
    await _render(
        callback,
        messages.AP_CHATS_SEND_PROMPT.format(title=title),
        _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]),
    )


async def _render(callback: CallbackQuery, text: str,
                  keyboard: InlineKeyboardMarkup) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001 - «message is not modified»
        logger.debug("Не удалось обновить панель: %s", exc)
    await callback.answer()


def _back_row() -> list[InlineKeyboardButton]:
    return [_btn(messages.AP_BACK, BACK)]


# --- Раздел: техработы --------------------------------------------------------
async def _section_maint(callback: CallbackQuery, payload: str,
                         data: dict[str, Any]) -> None:
    maintenance = data["maintenance"]

    # Нажата конкретная функция: отключаем с причиной или включаем.
    if ":" in payload:
        feature = payload.split(":", 1)[1]
        if await maintenance.is_disabled(feature):
            await maintenance.turn_off(feature)
            await _render(
                callback,
                f"{messages.AP_MAINT_REASON_CLEARED.format(name=feature)}\n\n"
                f"{messages.AP_MAINT_TITLE}",
                await _maint_keyboard(maintenance),
            )
            return
        # Выключение требует причины — спрашиваем текстом.
        DIALOGS[callback.from_user.id] = Dialog(
            kind="maint_reason", data={"feature": feature}
        )
        await _render(
            callback,
            messages.AP_MAINT_REASON_PROMPT.format(name=feature),
            _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]),
        )
        return

    await _render(
        callback, messages.AP_MAINT_TITLE, await _maint_keyboard(maintenance)
    )


async def _maint_keyboard(maintenance) -> InlineKeyboardMarkup:
    states = {name: enabled for name, enabled, _ in await maintenance.status()}
    rows = [
        [
            _btn(
                messages.AP_MAINT_ROW.format(
                    mark="🟢" if states.get(name, True) else "🔴", name=name
                ),
                f"{MAINT_PREFIX}{name}",
            )
        ]
        for name in MAINTENANCE_FEATURES
    ]
    rows.append(_back_row())
    return _kb(*rows)


# --- Раздел: промокоды --------------------------------------------------------
async def _section_promo(callback: CallbackQuery, payload: str,
                         data: dict[str, Any]) -> None:
    promo = data["promo"]

    if payload.startswith("promo:") and ":" in payload[6:]:
        action, code = payload[6:].split(":", 1)
        if action == "new":
            DIALOGS[callback.from_user.id] = Dialog(kind="promo_new")
            await _render(
                callback,
                messages.AP_PROMO_HINT,
                _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]),
            )
            return
        if action == "toggle":
            settings = await promo.codes.get(code)
            if settings is None:
                await callback.answer("Промокод не найден", show_alert=True)
                return
            await promo.codes.set_active(code, not settings["active"])
            state = "включён" if not settings["active"] else "выключен"
            await _render(
                callback,
                f"{messages.AP_PROMO_TOGGLE.format(code=code.upper(), state=state)}\n\n"
                f"{await _promo_text(promo)}",
                await _promo_keyboard(promo),
            )
            return

    await _render(callback, await _promo_text(promo), await _promo_keyboard(promo))


async def _promo_text(promo) -> str:
    codes = await promo.codes.list_all()
    if not codes:
        return f"{messages.AP_PROMO_TITLE}\n{messages.AP_ITEM_LINKS_EMPTY}"
    lines = [messages.AP_PROMO_TITLE]
    for item in codes:
        uses = (
            f" ({item['uses']}/{item['max_uses']})" if item["max_uses"]
            else f" ({item['uses']})"
        )
        lines.append(
            messages.AP_PROMO_ROW.format(
                mark="🟢" if item["active"] else "🔴",
                code=item["code"],
                coins=_price(item["jarvis_coins"]),
                uses=uses,
            )
        )
    lines.append(messages.AP_PROMO_HINT)
    return "\n".join(lines)


async def _promo_keyboard(promo) -> InlineKeyboardMarkup:
    rows = []
    for item in await promo.codes.list_all():
        rows.append([
            _btn(
                ("🔴 " if item["active"] else "🟢 ") + item["code"],
                f"{PROMO_PREFIX}toggle:{item['code']}",
            )
        ])
    rows.append([_btn(messages.AP_PROMO_NEW, f"{PROMO_PREFIX}new:")])
    rows.append(_back_row())
    return _kb(*rows)


# --- Раздел: товары и темы ----------------------------------------------------
async def _section_shop(callback: CallbackQuery, payload: str,
                         data: dict[str, Any]) -> None:
    await _goods_screen(callback, payload, data, kind="shop")


async def _section_themes(callback: CallbackQuery, payload: str,
                          data: dict[str, Any]) -> None:
    await _goods_screen(callback, payload, data, kind="themes")


async def _goods_screen(callback: CallbackQuery, payload: str,
                        data: dict[str, Any], kind: str) -> None:
    products = data["products"]
    title = (
        messages.AP_SHOP_TITLE if kind == "shop"
        else messages.AP_SHOP_TITLE.replace("Jarvis Shop", "Джасгнит")
    )

    if payload.endswith("new:"):
        DIALOGS[callback.from_user.id] = Dialog(kind="item_step1", data={"scope": kind})
        await _render(
            callback,
            messages.AP_ITEM_STEP1,
            _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]),
        )
        return

    # Удаление товара: <prefix>del:<code>
    if ":del:" in payload:
        code = payload.split(":del:", 1)[1]
        if kind == "shop":
            await products.delete_upgrade(code)
        else:
            await products.delete_theme(code)
        await _render(
            callback,
            f"🗑 Товар <code>{code}</code> удалён\n\n{title}",
            await _goods_keyboard(products, kind),
        )
        return

    items = (
        await products.upgrades() if kind == "shop" else await products.themes()
    )
    text = title
    if not items:
        text += f"\n{messages.AP_SHOP_EMPTY}"
    else:
        for item in items:
            extra = ""
            if kind == "shop":
                extra = (
                    f" ({item['kind']}={item['value']})" if item.get("value") else ""
                )
            text += "\n" + messages.AP_SHOP_ROW.format(
                mark="🟢" if item.get("active", 1) else "🔴",
                title=item["title"], cost=_price(item["cost"]), extra=extra,
            )
    await _render(callback, text, await _goods_keyboard(products, kind))


async def _goods_keyboard(products, kind: str) -> InlineKeyboardMarkup:
    prefix = SHOP_PREFIX if kind == "shop" else THEME_PREFIX
    scope = "upgrade" if kind == "shop" else "theme"
    label = messages.AP_SHOP_NEW
    rows = [[_btn(label, f"{prefix}new:"),
             _btn(messages.AP_ITEM_LINKS_ADD, f"{PICK_PREFIX}{scope}")]]
    items = (
        await products.upgrades() if kind == "shop" else await products.themes()
    )
    if items:
        rows.append([
            _btn(
                messages.AP_SHOP_DELETE.format(code=item["code"]),
                f"{prefix}del:{item['code']}",
            )
            for item in items[:6]
        ])
    rows.append(_back_row())
    return _kb(*rows)


# --- Раздел: баны -------------------------------------------------------------
async def _section_bans(callback: CallbackQuery, payload: str,
                        data: dict[str, Any]) -> None:
    rows = await BanService(data["db"]).list_active()
    if not rows:
        text = f"🚫 <b>Баны</b>\n{messages.BAN_LIST_EMPTY}"
    else:
        lines = [messages.BAN_LIST_HEADER.format(count=len(rows))]
        for row in rows:
            name = f"@{row['username']}" if row["username"] else str(row["user_id"])
            until = (
                messages.BAN_LIST_UNTIL.format(date=str(row["until"])[:10])
                if row["until"] else messages.BAN_LIST_FOREVER
            )
            lines.append(
                messages.BAN_LIST_ROW.format(
                    name=name, reason=row["reason"] or "—", until=until
                )
            )
        text = "🚫 <b>Баны</b>\n" + "\n".join(lines)
    text += f"\n\n<i>{messages.BAN_USAGE}</i>"
    await _render(callback, text, _kb(_back_row()))


# --- Раздел: статусы ----------------------------------------------------------
async def _section_status(callback: CallbackQuery, payload: str,
                          data: dict[str, Any]) -> None:
    from bot.services.status_service import StatusService

    statuses = StatusService.catalog()
    lines = ["🏅 <b>Статусы</b>"]
    for status in statuses.values():
        kind = "вручную" if not status.auto else "автоматически"
        lines.append(f"• <b>{status.label}</b> — {kind} (приоритет {status.priority})")
    lines.append(f"\n<i>{messages.ADMIN_SETSTATUS_USAGE}</i>")
    await _render(callback, "\n".join(lines), _kb(_back_row()))


# --- Раздел: ссылки -----------------------------------------------------------
async def _section_links(callback: CallbackQuery, payload: str,
                         data: dict[str, Any]) -> None:
    """Ссылки бота: видны всем игрокам в /help."""
    links: LinkService = data["links"]
    # Админу нужны и выключенные: иначе включить их обратно нечем.
    rows = await links.all_bot_links()

    if payload.startswith("links:add"):
        DIALOGS[callback.from_user.id] = Dialog(kind="bot_link")
        await _render(callback, messages.AP_LINKS_INPUT,
                      _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]))
        return

    if not rows:
        text = f"{messages.AP_LINKS_TITLE}\n{messages.AP_LINKS_EMPTY}"
    else:
        lines = [messages.AP_LINKS_TITLE]
        for link in rows:
            mark = "🟢" if link["active"] else "🔴"
            lines.append(messages.AP_ITEM_LINKS_ROW.format(
                mark=mark, title=link["title"], url=link["url"],
                state="" if link["active"] else messages.AP_LINKS_STATE,
            ))
        text = "\n".join(lines)

    keyboard_rows = [
        [_btn(messages.AP_LINKS_ADD, f"{SEC_LINKS}:add")]
    ]
    for link in rows:
        keyboard_rows.append([
            _btn(("🔴 " if link["active"] else "🟢 ") + link["title"],
                 f"{BOT_LINK_PREFIX}{link['id']}:toggle"),
            _btn("🗑", f"{BOT_LINK_PREFIX}{link['id']}:del"),
        ])
    keyboard_rows.append(_back_row())
    await _render(callback, text, _kb(*keyboard_rows))


async def _bot_link_action(callback: CallbackQuery, raw: str,
                           data: dict[str, Any]) -> None:
    """Включение, выключение и удаление общей ссылки."""
    # Формат «<id>:toggle» или «<id>:del». partition, а не rpartition:
    # при разборе с конца действие и номер местами меняются.
    link_id, _, action = raw.partition(":")
    links: LinkService = data["links"]
    if not link_id.isdigit():
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    if action == "toggle":
        if not await links.toggle_bot_link(int(link_id)):
            await callback.answer("Ссылка не найдена", show_alert=True)
            return
    elif action == "del":
        await links.delete_bot_link(int(link_id))
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    await _section_links(callback, "links", data)
    await callback.answer()


async def _link_screen(callback: CallbackQuery, raw: str,
                       data: dict[str, Any]) -> None:
    """Ссылки конкретного товара: payload вида «upgrade:x2»."""
    scope, _, code = raw.partition(":")
    if scope not in ("upgrade", "theme") or not code:
        await callback.answer("Товар не найден", show_alert=True)
        return
    links: LinkService = data["links"]
    title = await _product_title(scope, code, data)
    items = await links.product_links(scope, code)

    if raw.endswith(":add"):
        DIALOGS[callback.from_user.id] = Dialog(
            kind="product_link", data={"scope": scope, "code": code,
                                       "title": title}
        )
        await _render(
            callback,
            messages.AP_ITEM_LINK_INPUT.format(title=title),
            _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]),
        )
        return

    lines = [messages.AP_ITEM_LINKS_TITLE.format(title=title)]
    if items:
        lines.extend(
            messages.AP_ITEM_LINKS_ROW.format(mark="🔗", title=i["title"],
                                              url=i["url"], state="")
            for i in items
        )
    else:
        lines.append(messages.AP_ITEM_LINKS_EMPTY)

    rows = [
        [_btn(messages.AP_ITEM_LINKS_ADD, f"{LINKS_PREFIX}{raw}:add")],
        [_btn(messages.AP_ITEM_CANCEL, ITEM_DONE)],
    ]
    for link in items:
        rows.append([_btn("🗑 " + link["title"],
                          f"{PLINK_DEL}{scope}:{code}:{link['title']}")])
    rows.append(_back_row())
    await _render(callback, "\n".join(lines), _kb(*rows))


async def _product_title(scope: str, code: str, data: dict[str, Any]) -> str:
    """Название товара: встроенное — из конфига, кастомное — из базы."""
    if scope == "upgrade":
        return UpgradeService.catalog().get(code, {}).get("title", code)
    theme = await ThemeService(data["db"]).resolve(code)
    return theme.title


async def _pick_product(callback: CallbackQuery, raw: str,
                        data: dict[str, Any]) -> None:
    """«pick:<scope>» — список товаров, «pick:<scope>:<code>» — ввод ссылки."""
    parts = raw.split(":")
    scope = parts[0] if parts else ""
    if scope not in ("upgrade", "theme"):
        await callback.answer("Неизвестный раздел", show_alert=True)
        return
    if len(parts) == 1:
        await _pick_product_list(callback, scope, data)
        return

    code = parts[1]
    if not code:
        await callback.answer("Товар не найден", show_alert=True)
        return
    title = await _product_title(scope, code, data)
    DIALOGS[callback.from_user.id] = Dialog(
        kind="product_link", data={"scope": scope, "code": code, "title": title}
    )
    await _render(
        callback,
        messages.AP_ITEM_LINK_INPUT.format(title=title),
        _kb([_btn(messages.AP_ITEM_CANCEL, ROOT + "cancel")]),
    )


async def _pick_product_list(callback: CallbackQuery, scope: str,
                             data: dict[str, Any]) -> None:
    """Список всех товаров, к которым можно привязать ссылку."""
    if scope == "upgrade":
        entries = [(code, settings["title"])
                   for code, settings in UpgradeService.catalog().items()
                   if not settings.get("admin_only")]
    else:
        themes: ThemeService = data["themes"]
        entries = [(theme_id, theme.title)
                   for theme_id, theme in (await themes.full_catalog()).items()]

    rows = [
        [_btn(f"🔗 {title}", f"{PICK_PREFIX}{scope}:{code}")]
        for code, title in entries
    ]
    rows.append(_back_row())
    await _render(callback, messages.AP_ITEM_LINKS_PICK, _kb(*rows))


async def _delete_product_link(callback: CallbackQuery, raw: str,
                               data: dict[str, Any]) -> None:
    """Удаление ссылки товара по её названию."""
    parts = raw.split(":")
    scope, code, title = parts[0], parts[1], ":".join(parts[2:])
    if scope not in ("upgrade", "theme") or not code or not title:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    links: LinkService = data["links"]
    # Название, а не id: id не помещается в callback вместе с кодом
    # товара, а названия у ссылок одного товара не повторяются.
    row = await data["db"].fetch_one(
        "SELECT id FROM product_links WHERE scope = ? AND code = ? AND title = ?",
        (scope, code, title),
    )
    if row:
        await links.delete_product_link(int(row["id"]))
    await _link_screen(callback, f"{scope}:{code}", data)
    await callback.answer()


# --- Раздел: написать в чат ---------------------------------------------------
async def _section_chats(callback: CallbackQuery, payload: str,
                         data: dict[str, Any]) -> None:
    announce = data["announce"]
    chats = await announce.chats.all(200)

    if not chats:
        await _render(callback, messages.AP_CHATS_EMPTY, _kb(_back_row()))
        return

    if payload.startswith("chats:page:"):
        page = int(payload.rsplit(":", 1)[1] or 0)
    else:
        page = 0
    pages = max(1, (len(chats) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(0, page), pages - 1)
    chunk = chats[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    rows = [
        [_btn(_chat_title(chat), f"{CHAT_PREFIX}{chat['chat_id']}")]
        for chat in chunk
    ]
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"{CHATS_PAGE}{page - 1}"))
    nav.append(_btn(f"{page + 1}/{pages}", f"{CHATS_PAGE}{page}"))
    if page + 1 < pages:
        nav.append(_btn("▶️", f"{CHATS_PAGE}{page + 1}"))
    rows.append(nav)
    rows.append(_back_row())

    await _render(
        callback,
        f"{messages.AP_CHATS_TITLE}\n{messages.AP_CHATS_PAGE.format(page=page + 1, pages=pages)}",
        _kb(*rows),
    )


def _chat_title(chat: dict) -> str:
    title = chat.get("title") or f"Чат {chat['chat_id']}"
    if len(title) > 28:
        title = title[:27] + "…"
    return f"💬 {title}"


# --- Раздел: статистика -------------------------------------------------------
async def _section_stats(callback: CallbackQuery, payload: str,
                         data: dict[str, Any]) -> None:
    db = data["db"]
    players = await data["users"].users.count()
    chats = await data["announce"].chats.count()
    bans = await data["bans"].count_active()
    row = await db.fetch_one("SELECT COUNT(*) AS c FROM user_themes")
    themes = int(row["c"]) if row else 0
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM custom_upgrades"
    )
    upgrades = int(row["c"]) if row else 0
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM custom_themes"
    )
    themes += int(row["c"]) if row else 0
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM packages WHERE delivered = 0"
    )
    packages = int(row["c"]) if row else 0

    text = "\n".join([
        messages.AP_STATS_TITLE,
        messages.AP_STATS_LINE.format(players=players),
        messages.AP_STATS_CHATS.format(chats=chats),
        messages.AP_STATS_BANS.format(bans=bans),
        messages.AP_STATS_THEMES.format(themes=themes),
        messages.AP_STATS_ITEMS.format(items=upgrades),
        messages.AP_STATS_PACKAGES.format(packages=packages),
    ])
    await _render(callback, text, _kb(_back_row()))


# --- Диалоги ------------------------------------------------------------------
async def dialog_text(message: Message, **data: Any) -> None:
    """Шаги диалогов панели. Ловится раньше прочих текстовых хендлеров."""
    user_id = message.from_user.id
    dialog = DIALOGS.get(user_id)
    if dialog is None:
        return  # это обычный текст, дальше его разберут другие хендлеры

    if not is_admin(user_id):
        reset_dialog(user_id)
        return

    text = (message.text or "").strip()
    if text in ("/apanel_cancel", "/panel_cancel", "/cancel"):
        reset_dialog(user_id)
        await safe_answer(message, messages.AP_ITEM_CANCEL)
        return

    if dialog.kind == "item_links":
        # Ждём нажатия кнопки, а не текста: подсказываем, что делать.
        await safe_answer(message, messages.AP_ITEM_LINKS_ADD)
        return
    if dialog.kind == "item_desc":
        await _item_desc(message, dialog, text, data)
    elif dialog.kind == "product_link":
        await _item_link(message, dialog, text, data)
    elif dialog.kind == "bot_link":
        await _item_link(message, dialog, text, data)
    elif dialog.kind == "maint_reason":
        await _finish_maint_reason(message, dialog, text, data)
    elif dialog.kind == "promo_new":
        await _finish_promo(message, text, data)
    elif dialog.kind == "item_step1":
        await _item_step1(message, dialog, text, data)
    elif dialog.kind == "item_value":
        await _item_value(message, dialog, text, data)
    elif dialog.kind == "chat_send":
        await _chat_send(message, dialog, text, data)


async def _finish_maint_reason(message: Message, dialog: Dialog, reason: str,
                               data: dict[str, Any]) -> None:
    maintenance = data["maintenance"]
    feature = dialog.data["feature"]
    reset_dialog(message.from_user.id)
    if not reason:
        await maintenance.turn_off(feature)
        await safe_answer(
            message,
            messages.AP_MAINT_REASON_CLEARED.format(name=feature),
            reply_markup=root_keyboard(),
        )
        return
    await maintenance.turn_on(feature, reason)
    await safe_answer(
        message,
        messages.AP_MAINT_REASON_SET.format(name=feature, reason=reason),
        reply_markup=root_keyboard(),
    )


async def _finish_promo(message: Message, text: str, data: dict[str, Any]) -> None:
    reset_dialog(message.from_user.id)
    parts = text.split()
    promo = data["promo"]
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await safe_answer(message, messages.AP_ITEM_WRONG_FORMAT)
        return
    code = parts[0]
    coins = int(parts[1])
    max_uses = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    created = await promo.codes.create(code, coins, False, max_uses)
    if not created:
        await safe_answer(message, messages.ADMIN_PROMO_EXISTS)
        return
    await safe_answer(
        message,
        messages.ADMIN_PROMO_CREATED.format(
            code=code.upper(), coins=_price(coins)
        ),
        reply_markup=await _promo_keyboard(promo),
    )


async def _item_step1(message: Message, dialog: Dialog, text: str,
                      data: dict[str, Any]) -> None:
    """Шаг 1: название и стоимость."""
    parts = text.rsplit(" ", 1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await safe_answer(message, messages.AP_ITEM_WRONG_FORMAT)
        return
    title, cost = parts[0].strip(), int(parts[1])
    if cost <= 0:
        await safe_answer(message, messages.AP_ITEM_BAD_VALUE)
        return
    DIALOGS[message.from_user.id] = Dialog(
        kind="item_type", data={"title": title, "cost": cost,
                                "scope": dialog.data.get("scope", "shop")}
    )
    await safe_answer(
        message,
        messages.AP_ITEM_STEP2.format(title=title, cost=_price(cost)),
        reply_markup=_kb(
            [
                _btn(messages.AP_ITEM_TYPE_MULT, ROOT + "type:multiplier"),
                _btn(messages.AP_ITEM_TYPE_CD, ROOT + "type:cooldown"),
            ],
            [
                _btn(messages.AP_ITEM_TYPE_THEME, ROOT + "type:theme"),
                _btn(messages.AP_ITEM_CANCEL, ROOT + "cancel"),
            ],
        ),
    )


async def _item_value(message: Message, dialog: Dialog, text: str,
                      data: dict[str, Any]) -> None:
    """Шаг 3: значение множителя / минуты, либо цвет темы."""
    products = data["products"]
    title = dialog.data["title"]
    cost = dialog.data["cost"]
    kind = dialog.data["item_kind"]
    scope = dialog.data.get("scope", "shop")
    reset_dialog(message.from_user.id)

    if kind == "theme":
        result = await products.create_theme(title, cost, text)
    else:
        raw = text.strip().lower().lstrip("xх")
        if not raw.isdigit() or int(raw) <= 0:
            await safe_answer(message, messages.AP_ITEM_BAD_VALUE)
            return
        if kind == "cooldown":
            result = await products.create_upgrade(
                title, cost, "cooldown", int(raw) * 60
            )
        else:
            result = await products.create_upgrade(
                title, cost, "multiplier", int(raw)
            )

    if not result.ok:
        await safe_answer(
            message, messages.AP_ITEM_FAILED.format(detail=result.detail)
        )
        return

    # Товар уже в каталоге, но без описания и ссылок. Не отправляем
    # сообщение о создании и не выходим: дальше два шага дополнения.
    DIALOGS[message.from_user.id] = Dialog(
        kind="item_desc",
        data={"title": result.title, "code": result.code, "cost": result.cost,
              "detail": result.detail, "scope": scope,
              "theme_scope": "theme" if kind == "theme" else "upgrade"},
    )
    await safe_answer(
        message,
        messages.AP_ITEM_DESC_PROMPT.format(title=result.title),
        reply_markup=_kb([
            _btn(messages.AP_ITEM_DESC_NONE, f"{ROOT}nodesc"),
            _btn(messages.AP_ITEM_CANCEL, ROOT + "cancel"),
        ]),
    )


async def _item_desc(message: Message, dialog: Dialog, text: str,
                     data: dict[str, Any]) -> None:
    """Шаг 4: описание товара."""
    scope = dialog.data["theme_scope"]
    code = dialog.data["code"]
    await _save_description(message, dialog, text, data, scope, code)
    await _ask_item_links(message, dialog, data)


async def _item_desc_skip(callback: CallbackQuery, data: dict[str, Any]) -> None:
    """Шаг 4 пропущен: описание товару не нужно."""
    dialog = DIALOGS.get(callback.from_user.id)
    if dialog is None or dialog.kind != "item_desc":
        await _render(callback, panel_text(), root_keyboard())
        return
    DIALOGS[callback.from_user.id] = Dialog(
        kind="item_links", data=dict(dialog.data)
    )
    await _show_item_links_screen(callback, dialog, data)


async def _save_description(message: Message, dialog: Dialog, text: str,
                            data: dict[str, Any], scope: str, code: str) -> None:
    """Описание уходит в товар. Пустой текст — описания не будет."""
    from bot.services.custom_product_service import clean_description

    description = clean_description(text)
    await data["db"].execute(
        f"UPDATE {'custom_upgrades' if scope == 'upgrade' else 'custom_themes'} "
        "SET description = ? WHERE code = ?", (description, code),
    )
    logger.info("Описание товара %s/%s обновлено", scope, code)


async def _ask_item_links(message: Message, dialog: Dialog,
                          data: dict[str, Any]) -> None:
    """Переход к списку ссылок товара после описания."""
    DIALOGS[message.from_user.id] = Dialog(kind="item_links", data=dict(dialog.data))
    await safe_answer(
        message,
        messages.AP_ITEM_DESC_SAVED,
        reply_markup=_kb([_btn(messages.AP_ITEM_LINKS_ADD,
                               f"{LINKS_PREFIX}{dialog.data['theme_scope']}:"
                               f"{dialog.data['code']}:add")]),
    )


async def _show_item_links_screen(callback: CallbackQuery, dialog: Dialog,
                                  data: dict[str, Any]) -> None:
    """То же, что _link_screen_after, но кнопками экранного сообщения."""
    scope = dialog.data["theme_scope"]
    code = dialog.data["code"]
    await _render(
        callback,
        messages.AP_ITEM_LINKS_TITLE.format(title=dialog.data["title"]),
        _kb([
            _btn(messages.AP_ITEM_LINKS_ADD, f"{LINKS_PREFIX}{scope}:{code}:add"),
            _btn("✅ Готово", ITEM_DONE),
        ]),
    )


async def _item_link(message: Message, dialog: Dialog, text: str,
                     data: dict[str, Any]) -> None:
    """Ввод ссылки для товара или для всего бота."""
    links: LinkService = data["links"]
    if dialog.kind == "bot_link":
        reset_dialog(message.from_user.id)
        result = await links.add_bot_link(text)
        if not result.ok:
            await safe_answer(
                message, messages.AP_ITEM_LINK_BAD.format(detail=result.detail)
            )
            return
        await safe_answer(
            message, messages.AP_LINKS_SAVED.format(title=result.title),
            reply_markup=root_keyboard(),
        )
        return

    scope = dialog.data["scope"]
    code = dialog.data["code"]
    DIALOGS[message.from_user.id] = Dialog(kind="item_links", data=dict(dialog.data))
    result = await links.add_product_link(scope, code, text)
    if not result.ok:
        await safe_answer(
            message, messages.AP_ITEM_LINK_BAD.format(detail=result.detail),
            reply_markup=_kb([_btn(messages.AP_ITEM_CANCEL, ITEM_DONE)]),
        )
        return
    items = await links.product_links(scope, code)
    rows = [
        [_btn(messages.AP_ITEM_LINKS_ADD, f"{LINKS_PREFIX}{scope}:{code}:add")],
        [_btn("✅ Готово", ITEM_DONE)],
    ]
    for link in items:
        rows.append([_btn("🗑 " + link["title"],
                          f"{PLINK_DEL}{scope}:{code}:{link['title']}")])
    await safe_answer(
        message,
        messages.AP_ITEM_LINK_SAVED.format(title=result.title)
        + f"\n{messages.AP_ITEM_LINKS_TITLE.format(title=dialog.data['title'])}",
        reply_markup=_kb(*rows),
    )


async def _item_done(callback: CallbackQuery, data: dict[str, Any]) -> None:
    """Товар дополнен и опубликован."""
    dialog = DIALOGS.get(callback.from_user.id)
    reset_dialog(callback.from_user.id)
    if dialog is None:
        await _render(callback, panel_text(), root_keyboard())
        return
    await _render(
        callback,
        messages.AP_ITEM_LINKS_DONE.format(title=dialog.data.get("title", "")),
        root_keyboard(),
    )


async def _chat_send(message: Message, dialog: Dialog, text: str,
                     data: dict[str, Any]) -> None:
    chat_id = dialog.data["chat_id"]
    title = dialog.data["title"]
    reset_dialog(message.from_user.id)
    bot: Bot = data["bot"]
    try:
        await bot.send_message(chat_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.info("Не удалось отправить в %s: %s", chat_id, exc)
        await safe_answer(message, messages.AP_CHATS_FAILED.format(title=title))
        return
    await safe_answer(message, messages.AP_CHATS_SENT.format(title=title))
