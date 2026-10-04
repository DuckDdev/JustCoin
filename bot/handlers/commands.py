"""Публичные команды: /start, /help, /jcoin, /jtop, /jstats, /promo."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import messages
from bot.config import JUSTID_MAX_LENGTH, JUSTID_MIN_LENGTH, TOP_PAGE_SIZE
from bot.utils.keyboards import main_keyboard, menu_button_markup
from bot.handlers.common import (
    maintenance_text,
    place_line,
    safe_answer,
    touch_player,
)
from bot.handlers.jasgnight import open_shop as open_jasgnight
from bot.models import User as Player
from bot.services.courier_service import courier_arrival_text
from bot.services.economy_service import ClaimStatus, EconomyService
from bot.services.link_service import LinkService
from bot.services.justid_service import JustIdResult, display, normalize
from bot.services.promo_service import PromoResult, PromoService
from bot.services.stats_service import StatsService
from bot.services.theme_service import ThemeService, ThemeStatus
from bot.services.top_service import TopService
from bot.utils.card import render_card_async
from bot.utils.formatters import format_coins, format_duration

logger = logging.getLogger(__name__)


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="commands")
    _register(router)
    return router


def _register(router: Router) -> None:
    router.message.register(cmd_start, Command("start"))
    router.message.register(cmd_help, Command("help", "h"))
    router.message.register(cmd_jcoin, Command("jcoin", "justcoins", "jcoins"))
    router.message.register(cmd_jtop, Command("jtop", "top"))
    router.callback_query.register(
        cmd_jtop_page, F.data.startswith("top:page:")
    )
    router.message.register(cmd_jstats, Command("jstats", "stats"))
    router.message.register(cmd_textstats, Command("textstats", "stats_text"))
    router.message.register(cmd_promo, Command("promo"))
    router.message.register(cmd_setid, Command("setid"))
    router.message.register(cmd_myid, Command("myid"))
    router.message.register(cmd_id, Command("id"))
    router.message.register(cmd_theme, Command("theme"))
    router.message.register(cmd_jasgnight, Command("jasgnight", "jg"))
    router.message.register(cmd_profile, Command("profile", "me"))

def _price_label(value: int) -> str:
    """Разряды с пробелом: 900000 -> «900 000»."""
    return f"{int(value):,}".replace(",", " ")


async def cmd_start(message: Message, **data: Any) -> None:
    """`/start` — приветствие с нижним меню."""
    await touch_player(message, data)
    await message.answer(
        messages.HELP,
        reply_markup=main_keyboard(),
    )


async def cmd_help(message: Message, **data: Any) -> None:
    """`/help` — список команд: нижнее меню и inline-кнопки."""
    markup = await help_keyboard(data)
    await safe_answer(message, messages.HELP, reply_markup=markup)


async def cmd_donate_view(message: Message, **data: Any) -> None:
    """Витрина донатов. Вход с нижней кнопки — без аргументов."""
    from aiogram.filters import CommandObject

    from bot.handlers.donate import cmd_donate

    await cmd_donate(
        message, CommandObject(prefix="/", command="donate",
                               mention="", args=""), **data
    )


async def help_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup | None:
    """Кнопки-ссылки бота. Настраиваются в панели, доступны всем.

    Пока ссылок нет, клавиатуру не показываем: пустая панель под текстом
    выглядит как поломка интерфейса.
    """
    link_service: LinkService | None = data.get("links")
    if link_service is None:
        return None
    links = await link_service.bot_links()
    if not links:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔗 {link['title']}", url=link["url"])]
        for link in links
    ])


async def cmd_jcoin(message: Message, **data: Any) -> None:
    """Получение джаст коинов."""
    blocked = await maintenance_text(data, "claim", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return

    economy: EconomyService = data["economy"]
    player: Player = await touch_player(message, data)

    try:
        result = await economy.claim(player)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка начисления коинов для %s", player.user_id)
        await safe_answer(message, messages.BOT_ERROR)
        return

    # Если бот был оффлайн, игрок об этом узнаёт один раз за запуск:
    # дальше повторяться об этом незачем.
    offline = await _offline_notice(message, result, data)

    if result.status is ClaimStatus.COOLDOWN:
        cooldown_text = messages.CLAIM_COOLDOWN.format(
            time=format_duration(result.cooldown_left)
        )
        # Ответ о простое идёт перед отказом: игрок ждал не из-за
        # кулдауна, а из-за того, что бота не было.
        if offline:
            cooldown_text = f"{messages.OFFLINE_NOTICE}\n\n{cooldown_text}"
        await safe_answer(
            message, cooldown_text,
            reply_markup=None if offline else menu_button_markup(),
        )
        return

    lines = [messages.CLAIM_SUCCESS.format(amount=format_coins(result.amount))]
    if result.upgraded:
        lines.append(messages.CLAIM_UPGRADE_NOTE)
    if result.package is not None:
        lines.append(
            courier_arrival_text(result.courier_seconds, result.package.amount)
        )
    # Место считаем по игроку ПЕРЕЧИТАННОМУ после начисления: объект player
    # загружен до claim и содержит устаревший баланс, из-за чего позиция
    # в топе выходила неверной.
    lines.append(await place_line(data, await touch_player(message, data)))
    if offline:
        lines.insert(0, messages.OFFLINE_NOTICE)
    await safe_answer(message, "\n".join(lines),
                      reply_markup=menu_button_markup())


async def _offline_notice(message: Message, result, data: dict[str, Any]) -> bool:
    """Сообщить, что бот был офлайн. Один раз за сессию.

    Обновление 1.1.3: игрок получает одну короткую фразу вместо
    подробного разбора множителя — он и так уже видит, что ждал.
    """
    if result.offline_seconds <= 0:
        return False
    state = data.get("bot_state")
    if state is not None and state.offline_notified:
        return False
    if state is not None:
        state.offline_notified = True
    return True


def top_text(result) -> str:
    """Текст страницы топа с подсказкой о своём месте."""
    lines = [
        messages.TOP_TITLE_PAGE.format(
            page=result.page + 1, pages=result.pages, total=result.total
        ),
        *(row.line for row in result.rows),
    ]
    first = result.page * TOP_PAGE_SIZE + 1 if result.rows else 0
    lines.append(messages.TOP_PAGE_HINT.format(
        start=first, finish=first + len(result.rows) - 1, total=result.total
    ))
    # Строка «Твоё место» нужна всегда: игрок может быть и на этой
    # странице, и на другой, и потерять свой номер в списке не должен.
    my_place = result.user_row or next(
        (row for row in result.rows if row.user.user_id == result.user_id),
        None,
    )
    if my_place is None:
        my_place = result.user_row
    if my_place is not None:
        lines.append("")
        lines.append(messages.TOP_YOUR_PLACE.format(
            place=my_place.index,
            total=result.total,
            amount=format_coins(my_place.user.balance),
        ))
    return "\n".join(lines)


def top_keyboard(result):
    """Кнопки листания. На единственной странице их не показываем."""
    if result.pages <= 1:
        return None
    rows = []
    nav = []
    if result.page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"top:page:{result.page - 1}"
        ))
    nav.append(InlineKeyboardButton(
        text=f"{result.page + 1}/{result.pages}", callback_data="top:page:none"
    ))
    if result.page + 1 < result.pages:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"top:page:{result.page + 1}"
        ))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_jtop(message: Message, page: int = 0, **data: Any) -> None:
    """Глобальный топ игроков по всем чатам. Показывает всех, постранично."""
    blocked = await maintenance_text(data, "top", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return

    top_service: TopService = data["top"]
    player: Player = await touch_player(message, data)
    result = await top_service.build(player, page)

    if not result.rows:
        await safe_answer(message, messages.TOP_EMPTY)
        return

    await safe_answer(message, top_text(result),
                      reply_markup=top_keyboard(result))


async def cmd_jtop_page(callback: CallbackQuery, **data: Any) -> None:
    """Кнопки листания топа."""
    raw = (callback.data or "")[len("top:page:"):]
    page = int(raw) if raw.isdigit() else 0
    await cmd_jtop(callback.message, page, **data)
    await callback.answer()


async def _send_card(message: Message, data: dict[str, Any], target: Player) -> None:
    """Отправляет карточку игрока. Общая точка для /jstats и /id."""
    bot: Bot = data["bot"]
    stats: StatsService = data["stats"]
    card = await stats.build(target)

    try:
        # Аватар запрашиваем только у самого игрока: у чужого профиля
        # Telegram не даст его скачать.
        if target.user_id == message.from_user.id:
            card.avatar = await stats.download_avatar(bot, message.from_user)
    except Exception:  # noqa: BLE001
        logger.debug("Аватар недоступен, будет заглушка")

    # Под карточкой — кнопки: карточка, текстовые статы, меню.
    markup = stats_keyboard(target)

    try:
        png = await render_card_async(card)
        await message.answer_photo(
            BufferedInputFile(png, filename="justcoin_stats.png"),
            caption=stats.caption(),
            reply_markup=markup,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось отрисовать карточку для %s", target.user_id)
        await safe_answer(message, stats.fallback_text(target, card),
                          reply_markup=markup)


def stats_keyboard(target: Player) -> InlineKeyboardMarkup:
    """Кнопки под карточкой и статистикой."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=messages.BTN_STATS_TEXT,
                                 callback_data="stats:text"),
            InlineKeyboardButton(text=messages.BTN_CARD,
                                 callback_data="stats:card"),
        ],
        [InlineKeyboardButton(text="📱 Меню команд", callback_data="menu:help")],
    ])


async def send_text_stats(message: Message, data: dict[str, Any],
                          target: Player | None = None) -> None:
    """Статистика текстом: тот же набор данных, что на карточке.

    Нужна как основной способ, так и запасной — если картинка не
    отрисовалась, игрок всё равно должен увидеть свои цифры.
    """
    stats: StatsService = data["stats"]
    player = target or await touch_player(message, data)
    card = await stats.build(player)
    await safe_answer(message, stats.fallback_text(player, card),
                      reply_markup=stats_keyboard(player))


async def cmd_textstats(message: Message, **data: Any) -> None:
    """`/textstats` — статистика без картинки."""
    blocked = await maintenance_text(data, "stats", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return
    await send_text_stats(message, data)


async def cmd_jstats(message: Message, bot: Bot, **data: Any) -> None:
    """Статистика игрока: карточка-картинка, при ошибке — текст."""
    blocked = await maintenance_text(data, "stats", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return

    player: Player = await touch_player(message, data)
    data.setdefault("bot", bot)
    await _send_card(message, data, player)


async def cmd_promo(message: Message, command: CommandObject, **data: Any) -> None:
    """Активация промокода."""
    blocked = await maintenance_text(data, "promo", message.from_user)
    if blocked:
        await safe_answer(message, blocked)
        return

    code = (command.args or "").strip()
    if not code:
        await safe_answer(message, messages.PROMO_USAGE)
        return

    await touch_player(message, data)
    promo: PromoService = data["promo"]
    outcome = await promo.redeem(message.from_user.id, code)

    if outcome.result is PromoResult.INVALID:
        await safe_answer(message, messages.PROMO_INVALID)
    elif outcome.result is PromoResult.ALREADY:
        await safe_answer(message, messages.PROMO_ALREADY)
    elif outcome.result is PromoResult.EXHAUSTED:
        await safe_answer(message, messages.PROMO_EXHAUSTED)
    elif outcome.unlock_secret:
        # Первый промокод открывает секретную часть.
        await safe_answer(
            message,
            messages.PROMO_JARVIS_ACTIVATED.format(amount=outcome.jarvis_coins),
        )
    elif outcome.jarvis_coins > 0:
        await safe_answer(
            message,
            messages.PROMO_COINS_ACTIVATED.format(
                amount=_price_label(outcome.jarvis_coins)
            ),
        )
    else:
        await safe_answer(message, messages.PROMO_GENERIC)


# --- JustID --------------------------------------------------------------------
async def cmd_setid(message: Message, command: CommandObject, **data: Any) -> None:
    """/setid <имя> — задать свой JustID вида $John. /setid off — освободить."""
    player: Player = await touch_player(message, data)
    raw = (command.args or "").strip()
    if not raw:
        await safe_answer(message, messages.JUSTID_USAGE)
        return

    just_ids = data["justids"]
    # «off» освобождает имя: иначе занятый JustID нельзя было бы отдать
    # другому игроку.
    if raw.lower() in ("off", "-", "none", "del", "delete"):
        if not await just_ids.release(player):
            await safe_answer(message, messages.JUSTID_MY_NONE)
            return
        await safe_answer(
            message, messages.JUSTID_RELEASED.format(just_id=display(player.just_id))
        )
        return

    outcome = await just_ids.claim(player, raw)

    if outcome.result is JustIdResult.SUCCESS:
        text = messages.JUSTID_SET.format(just_id=display(outcome.just_id))
        if player.just_id == outcome.just_id:
            text = messages.JUSTID_SAME.format(just_id=display(outcome.just_id))
        await safe_answer(message, text)
    elif outcome.result is JustIdResult.INVALID:
        await safe_answer(
            message,
            messages.JUSTID_INVALID.format(
                min=JUSTID_MIN_LENGTH, max=JUSTID_MAX_LENGTH
            ),
        )
    elif outcome.result is JustIdResult.RESERVED:
        await safe_answer(
            message,
            messages.JUSTID_RESERVED.format(just_id=display(outcome.just_id)),
        )
    elif outcome.result is JustIdResult.TAKEN:
        await safe_answer(
            message,
            messages.JUSTID_TAKEN.format(just_id=display(outcome.just_id)),
        )
    else:
        await safe_answer(message, messages.BOT_ERROR)


async def cmd_myid(message: Message, **data: Any) -> None:
    """/myid — показать свой JustID."""
    player: Player = await touch_player(message, data)
    if not player.just_id:
        await safe_answer(message, messages.JUSTID_MY_NONE)
        return
    await safe_answer(message, messages.JUSTID_MY.format(just_id=display(player.just_id)))


async def show_card_by_just_id(message: Message, raw: str,
                               data: dict[str, Any]) -> bool:
    """Показывает карточку по ссылке $JustID. False — игрок не найден.

    Общая точка для команды /id и для текстовых сообщений вида «$John»,
    поэтому ответ об ошибке отличается: команда всегда объясняет,
    а случайная ссылка в группе молчит.
    """
    just_ids = data["justids"]
    target = await just_ids.by_id(raw)
    if target is None:
        return False
    await _send_card(message, data, target)
    return True


async def cmd_id(message: Message, command: CommandObject, **data: Any) -> None:
    """/id $John — карточка игрока по его JustID."""
    raw = (command.args or "").strip()
    if not raw:
        await safe_answer(message, messages.JUSTID_USAGE)
        return

    if not await show_card_by_just_id(message, raw, data):
        await safe_answer(
            message, messages.JUSTID_NOT_FOUND.format(just_id=display(normalize(raw)))
        )


async def cmd_profile(message: Message, bot: Bot, **data: Any) -> None:
    """/profile — своя карточка (то же, что /jstats)."""
    await cmd_jstats(message, bot, **data)


# --- Темы ----------------------------------------------------------------------
async def cmd_theme(message: Message, command: CommandObject, **data: Any) -> None:
    """/theme <тема> — переключить купленную тему карточки."""
    player: Player = await touch_player(message, data)
    themes: ThemeService = data["themes"]
    raw = (command.args or "").strip().lower()

    if not raw:
        owned = await themes.owned(player)
        available = [
            themes.resolve(t).title for t in sorted(owned)
        ]
        current = themes.resolve(player.theme).title
        await safe_answer(
            message,
            f"{messages.THEME_RESET.format(title=current)}\n"
            f"Купленные темы: {', '.join(available)}\n"
            f"{messages.THEME_USAGE}",
        )
        return

    outcome = await themes.activate(player, raw)
    if outcome.status is ThemeStatus.SUCCESS:
        await safe_answer(message, messages.THEME_SET.format(title=outcome.title))
    elif outcome.status is ThemeStatus.ACTIVE:
        await safe_answer(message, messages.JASG_ACTIVE.format(title=outcome.title))
    elif outcome.status is ThemeStatus.UNKNOWN:
        await safe_answer(
            message,
            messages.THEME_UNKNOWN.format(
                themes=", ".join(t.title for t in themes.catalog().values())
            ),
        )
    else:
        await safe_answer(
            message,
            f"{messages.JASG_NOT_ENOUGH.format(missing=_price_label(outcome.missing))}\n"
            f"{messages.THEME_USAGE}",
        )


async def cmd_jasgnight(message: Message, **data: Any) -> None:
    """/jasgnight — магазин тем карточки.

    Сборка витрины живёт в jasgnight.py, чтобы команда и кнопки
    показывали ровно одно и то же.
    """
    await open_jasgnight(message, **data)
