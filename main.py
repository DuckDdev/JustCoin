"""Точка входа: инициализация бота, регистрация хендлеров, фоновые задачи."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand, CallbackQuery, Message

from bot import config, messages
from bot.config import ADMIN_IDS, BOT_TOKEN, DB_PATH, validate
from bot.db.database import db
from bot.db.repository import FeatureRepository
from bot.handlers import (
    admin,
    admin_panel,
    buttons,
    commands,
    donate,
    jasgnight,
    packages,
    shop,
    texts,
)
from bot.handlers.ban_middleware import (
    BanMiddleware,
    ChatTrackingMiddleware,
    FreshUpdatesMiddleware,
)
from bot.handlers.errors import AntispamMiddleware
from bot.services.announce_service import AnnounceService
from bot.services.ban_service import BanService
from bot.services.courier_service import CourierService
from bot.services.donate_service import DonateService
from bot.services.custom_product_service import CustomProductService
from bot.services.economy_service import EconomyService
from bot.services.justid_service import JustIdService
from bot.services.link_service import LinkService
from bot.services.maintenance_service import MaintenanceService
from bot.services.promo_service import PromoService
from bot.services.slowdown_service import SlowdownService
from bot.services.stats_service import StatsService
from bot.services.status_service import StatusService
from bot.services.theme_service import ThemeService
from bot.services.top_service import TopService
from bot.services.upgrade_service import UpgradeService
from bot.services.user_service import UserService

logger = logging.getLogger("justcoin")

# Публичное меню команд. Секретные команды сюда НЕ попадают.
# Меню Telegram показывает только публичные команды. Секретные
# (/jupgrade, /jshop) и админские (/apanel, /ban) сюда не попадают.
PUBLIC_COMMANDS = [
    BotCommand(command="jcoin", description="Получить джаст коины"),
    BotCommand(command="jtop", description="Глобальный топ игроков"),
    BotCommand(command="jstats", description="Статистика и карточка"),
    BotCommand(command="textstats", description="Статистика текстом"),
    BotCommand(command="donate", description="Поддержать звёздами"),
    BotCommand(command="jasgnight", description="Джасгнит: темы карточки"),
    BotCommand(command="setid", description="Задать свой JustID"),
    BotCommand(command="id", description="Карточка по JustID"),
    BotCommand(command="myid", description="Показать свой JustID"),
    BotCommand(command="theme", description="Переключить тему карточки"),
    BotCommand(command="promo", description="Активировать промокод"),
    BotCommand(command="help", description="Список команд"),
]


class BotState:
    """Состояние текущего запуска.

    Живёт только в памяти: перезапуск бота должен означать новый
    сеанс, а не продолжение старого.
    """

    def __init__(self) -> None:
        self.offline_notified = False


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def build_dispatcher() -> Dispatcher:
    """Собирает диспетчер, хендлеры и зависимости."""
    dp = Dispatcher()

    features = FeatureRepository(db)
    slowdown = SlowdownService(db)
    dp["db"] = db
    dp["users"] = UserService(db)
    dp["maintenance"] = MaintenanceService(features)
    dp["economy"] = EconomyService(db, slowdown)
    dp["slowdown"] = slowdown
    dp["courier"] = CourierService(db)
    dp["promo"] = PromoService(db)
    dp["upgrade"] = UpgradeService(db)
    dp["themes"] = ThemeService(db)
    dp["statuses"] = StatusService(db)
    dp["justids"] = JustIdService(db)
    dp["bans"] = BanService(db)
    dp["announce"] = AnnounceService(db)
    dp["products"] = CustomProductService(db)
    dp["links"] = LinkService(db)
    dp["donate"] = DonateService(db)
    # Ключ не "state": этот занят FSM aiogram, наше состояние
    # перезатиралось бы молча.
    dp["bot_state"] = BotState()
    dp["top"] = TopService(db)
    dp["stats"] = StatsService(db)

    # Чаты запоминаем для рассылки: список должен быть полным даже если
    # бот ничем не отвечал в чате.
    dp.update.outer_middleware(ChatTrackingMiddleware(dp["announce"]))
    # Свежесть: сообщения, накопленные пока бот был выключен, игнорируются.
    # Метка старта ставится в on_startup.
    fresh = FreshUpdatesMiddleware()
    dp["fresh"] = fresh
    dp.update.outer_middleware(fresh)
    # Баны проверяются дальше по цепочке: забаненный не должен получать
    # антиспам-ответы вместо внятного объяснения. Админы банам не подлежат.
    dp.message.outer_middleware(BanMiddleware(dp["bans"]))
    dp.callback_query.outer_middleware(BanMiddleware(dp["bans"]))
    dp.pre_checkout_query.outer_middleware(BanMiddleware(dp["bans"]))
    # Антиспам оборачивает все хендлеры: команды, тексты, callback'и.
    dp.message.middleware(AntispamMiddleware())
    dp.callback_query.middleware(AntispamMiddleware())

    # Панель администратора идёт первым роутером: её диалоги ловят
    # обычный текст, пока активны.
    # Кнопки идут до команд: нажатие нижней кнопки превращается
    # в вызов команды, а не в отдельный ответ.
    dp.include_router(buttons.make_router())
    dp.include_router(donate.make_router())
    dp.include_router(admin_panel.make_router())
    dp.include_router(admin.make_router())
    dp.include_router(commands.make_router())
    dp.include_router(shop.make_router())
    dp.include_router(jasgnight.make_router())
    dp.include_router(packages.make_router())
    dp.include_router(texts.make_router())

    @dp.errors()
    async def on_error(event: Exception, update=None) -> bool:
        """Ловим необработанные исключения: игрок получает текст, лог — детали."""
        logger.exception("Необработанная ошибка при обработке апдейта", exc_info=event)
        if update is None:
            return True
        try:
            if isinstance(update, Message):
                await update.answer(messages.BOT_ERROR)
            elif isinstance(update, CallbackQuery):
                await update.answer(messages.BOT_ERROR, show_alert=True)
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось отправить сообщение об ошибке")
        return True

    return dp


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)


def make_startup(dp: Dispatcher):
    """Создаёт startup-хендлер, замкнутый на Dispatcher.

    aiogram передаёт в startup-хендлеры только bot и workflow_data,
    поэтому сам Dispatcher нужно захватить замыканием.
    """

    async def on_startup(bot: Bot) -> None:
        await db.connect()
        await db.migrate()
        await bot.set_my_commands(PUBLIC_COMMANDS)
        # Момент запуска: всё, что Telegram прислал из оффлайна,
        # будет проигнорировано, а игрок получит сообщение о простое.
        dp["fresh"].mark_started()
        dp["economy"].mark_online()

        courier: CourierService = dp["courier"]
        dp["courier_task"] = asyncio.create_task(
            courier.run_polling(bot, dp["maintenance"])
        )

        logger.info(
            "%s Админов: %s. Функции техработ: %s. База: %s",
            messages.LOG_BOT_STARTED, len(ADMIN_IDS),
            ", ".join(config.MAINTENANCE_FEATURES), DB_PATH,
        )
        if config.STARTUP_NOTIFY_ADMINS:
            await notify_admins(
                bot,
                messages.LOG_BOT_STARTED
                + "\n"
                + messages.LOG_BOT_STARTED_DETAILS.format(
                    admins=", ".join(str(a) for a in ADMIN_IDS) or "—",
                    features=", ".join(config.MAINTENANCE_FEATURES),
                    db=DB_PATH,
                ),
            )

    return on_startup


async def on_shutdown() -> None:
    # Останавливаем фоновые задачи (цикл курьера) перед закрытием БД.
    for pending in asyncio.all_tasks():
        if pending is not asyncio.current_task():
            pending.cancel()
    await db.close()
    logger.info("Бот остановлен")


async def main() -> None:
    setup_logging()
    validate()

    bot = Bot(token=BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    dp.startup.register(make_startup(dp))
    dp.shutdown.register(on_shutdown)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка по сигналу пользователя")
    except TelegramUnauthorizedError:
        # Самая частая ошибка запуска: неверный или с пробелами токен.
        logger.critical(
            "Telegram отклонил токен. Проверьте BOT_TOKEN в .env — "
            "токен должен быть без кавычек и лишних пробелов."
        )
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        logger.critical(messages.LOG_CRITICAL.format(error=exc), exc_info=True)
        raise
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
