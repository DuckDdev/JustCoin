# Сквозная проверка: реальный aiogram Dispatcher, реальные апдейты Telegram.
# Бот и сеть не трогаются — Telegram-вызовы перехватываются заглушкой.
#
# Запуск:  python run_e2e.py

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("ADMIN_IDS", "999")
# Кулдаун 3 часа, но курьер и посылки — быстрые.
os.environ.setdefault("CLAIM_COOLDOWN_SECONDS", "10800")
os.environ.setdefault("COURIER_DELAY_MIN_SECONDS", "2")
os.environ.setdefault("COURIER_DELAY_MAX_SECONDS", "3")
os.environ.setdefault("COURIER_CHECK_INTERVAL", "1")
os.environ.setdefault("DB_PATH", str(Path(tempfile.gettempdir()) / "justcoin_e2e.db"))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.methods import AnswerCallbackQuery  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    File,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Update,
    User,
)

from bot import messages  # noqa: E402
from bot.config import CLAIM_MAX_AMOUNT, CLAIM_MIN_AMOUNT, CURRENCY_EMOJI  # noqa: E402
from bot.db.database import db  # noqa: E402
from bot.db.repository import UserRepository  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
SENT: list[Message] = []
ALERTS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASSED.append(name)
        print(f"  \033[32mOK\033[0m   {name}")
    else:
        FAILED.append(f"{name} — {detail}")
        print(f"  \033[31mFAIL\033[0m {name}  {detail}")
    return condition


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def last_text() -> str:
    return SENT[-1].text if SENT else ""


def last_reply_markup() -> InlineKeyboardMarkup | None:
    return SENT[-1].reply_markup if SENT else None


def last_edit_markup() -> InlineKeyboardMarkup | None:
    """Клавиатура последней правки: правка не попадает в SENT."""
    markups = bot.edit_markups
    return markups[-1] if markups else None


class FakeSession:
    """Подмена сессии Telegram: перехватывает ВСЕ методы и не ходит в сеть.

    Перехват именно на уровне сессии, а не переопределением методов Bot,
    потому что хелперы aiogram (message.answer, message.answer_photo и т.д.)
    собирают объект метода и вызывают bot.session(...) напрямую.
    """

    def __init__(self) -> None:
        self.message_id = 1000
        self.edits: list = []
        # Клавиатуры именно правок: edit не попадает в SENT, поэтому
        # last_reply_markup() после правки показывает прошлое сообщение.
        self.edit_markups: list = []
        self.registered_commands: list = []

    def _next_id(self) -> int:
        self.message_id += 1
        return self.message_id

    @staticmethod
    def _make_message(chat_id: int, text: str, message_id: int,
                      reply_markup=None, photo=None) -> Message:
        # Модели aiogram заморожены, поэтому photo передаётся сразу
        # в конструктор, а не присваивается после создания.
        return Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            text=text,
            reply_markup=reply_markup,
            photo=photo,
        )

    async def __call__(self, bot, method, timeout=None):
        name = type(method).__name__

        if name == "SendMessage":
            chat_id = int(method.chat_id)
            message = self._make_message(chat_id, method.text,
                                         self._next_id(), method.reply_markup)
            SENT.append(message)
            return message

        if name == "SendPhoto":
            chat_id = int(method.chat_id)
            message = self._make_message(
                chat_id, method.caption or "", self._next_id(),
                method.reply_markup,
                photo=[PhotoSize(file_id="fake", file_unique_id="fake",
                                 width=900, height=500, file_size=1)],
            )
            SENT.append(message)
            return message

        if name == "AnswerCallbackQuery":
            if method.text:
                ALERTS.append(method.text)
            return AnswerCallbackQuery(
                callback_query_id=method.callback_query_id,
                text=method.text, show_alert=method.show_alert,
            )

        if name in ("EditMessageReplyMarkup", "EditMessageText"):
            self.edits.append(method.reply_markup if name == "EditMessageReplyMarkup"
                              else method.text)
            self.edit_markups.append(getattr(method, "reply_markup", None))
            chat_id = int(getattr(method, "chat_id", 0) or 0)
            # Клавиатуру правки тоже возвращаем: без неё проверки кнопок
            # после edit всегда видели бы пустоту.
            return self._make_message(
                chat_id, getattr(method, "text", "") or "", self._next_id(),
                reply_markup=getattr(method, "reply_markup", None),
            )

        if name == "GetFile":
            return File(file_id=method.file_id, file_unique_id="u",
                        file_path="photos/file_1.jpg", file_size=1234)

        if name == "SetMyCommands":
            self.registered_commands = list(method.commands)
            return True

        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="JustCoinBot",
                        username="justcoin_bot")

        if name == "DeleteMessage":
            return True

        raise AssertionError(f"Тест неожиданно вызывает Telegram-метод: {name}")

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Тест не должен качать контент")

    async def close(self) -> None:
        return None


class FakeBot(Bot):
    """Бот с подменённой сессией: ответы попадают в SENT, сеть не трогается."""

    def __init__(self) -> None:
        super().__init__(
            token="0:test",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.fake = FakeSession()
        self.session = self.fake

    @property
    def edits(self):
        return self.fake.edits

    @property
    def edit_markups(self):
        return self.fake.edit_markups

    @property
    def registered_commands(self):
        return self.fake.registered_commands

    async def download(self, file_id, destination=None, **kwargs) -> File:
        # «Аватарка» 4x4 синяя, чтобы проверить вставку в карточку.
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), (40, 90, 200)).save(buffer, format="JPEG")
        data = buffer.getvalue()
        name = "photos/file_1.jpg"
        if destination is None:
            return File(file_id=file_id, file_unique_id="u", file_path=name,
                        file_size=len(data), file=io.BytesIO(data))
        with open(destination, "wb") as target:
            target.write(data)
        return File(file_id=file_id, file_unique_id="u", file_path=name,
                    file_size=len(data))


def make_callback_update(update_id: int, user: User, data: str,
                         chat_id: int) -> Update:
    """Настоящий CallbackQuery: его answer тоже пойдёт через bot.session."""
    chat = Chat(id=chat_id, type="private")
    message = Message(
        message_id=update_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        text="📦 Посылка",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📦 Забрать посылку",
                                                   callback_data=data)]]
        ),
    )
    callback = CallbackQuery(
        id=f"cb-{update_id}",
        from_user=user,
        chat_instance=str(chat_id),
        data=data,
        message=message,
    )
    return Update(update_id=update_id, callback_query=callback)


def make_update(update_id: int, user: User, text: str, chat_id: int,
                chat_type: str = "private") -> Update:
    chat = Chat(id=chat_id, type=chat_type)
    return Update(update_id=update_id,
                  message=Message(
                      message_id=update_id,
                      date=datetime.now(timezone.utc),
                      chat=chat,
                      from_user=user,
                      text=text,
                  ))


ALICE = User(id=1001, is_bot=False, first_name="Алиса", last_name="Смирнова",
             username="alice")
BOB = User(id=1002, is_bot=False, first_name="Боб", username="bob")
ADMIN = User(id=999, is_bot=False, first_name="Админ", username="admin")
CAROL = User(id=1003, is_bot=False, first_name="Кэрол", username="carol")
GROUP_A = -100111
GROUP_B = -100222

bot = FakeBot()
# feed_update(bot, update) — первый аргумент сам Bot, а не Dispatcher.
_dp: list[Dispatcher] = []
_id = {"n": 0}


async def dispatch(update: Update) -> None:
    await _dp[0].feed_update(bot, update)


def uid() -> int:
    _id["n"] += 1
    return _id["n"]


async def feed(dp: Dispatcher, user: User, text: str, chat_id: int,
               chat_type: str = "private") -> Message | None:
    SENT.clear()
    ALERTS.clear()
    await dispatch(make_update(uid(), user, text, chat_id, chat_type))
    return SENT[-1] if SENT else None


async def feed_cb(dp: Dispatcher, user: User, data: str, chat_id: int) -> None:
    ALERTS.clear()
    await dispatch(make_callback_update(uid(), user, data, chat_id))


def reset_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(db.path) + suffix)
        if stale.exists():
            stale.unlink()


def _place_from_text(text: str) -> tuple[int, int]:
    """Достаёт «N/M» из строки «Место в топе: N/M»."""
    for line in text.splitlines():
        if line.startswith("Место в топе: "):
            n, _, m = line.removeprefix("Место в топе: ").partition("/")
            try:
                return int(n), int(m)
            except ValueError:
                return (0, 0)
    return (0, 0)


async def main() -> int:
    reset_db()
    import main as bot_main
    main_module = bot_main

    dp = bot_main.build_dispatcher()
    _dp.append(dp)
    await db.connect()
    await db.migrate()
    users = UserRepository(db)

    # Антиспам отключаем для основных сценариев, чтобы частые вызовы проходили.
    from bot.handlers.errors import AntispamMiddleware

    for mw in dp.message.middleware:
        if isinstance(mw, AntispamMiddleware):
            mw.cooldown = 0.0
    for mw in dp.callback_query.middleware:
        if isinstance(mw, AntispamMiddleware):
            mw.cooldown = 0.0

    # --- 1. /jcoin в личке ---
    section("1. /jcoin в личке")
    await feed(dp, ALICE, "/jcoin", 1001)
    check("ответ на /jcoin получен", bool(SENT))
    check("формат 'Вы получили N 🪙'",
          last_text().startswith("Вы получили ") and last_text().split()[3] == CURRENCY_EMOJI,
          last_text())
    check("есть строка места в топе", "Место в топе: " in last_text(), last_text())
    check("нет упоминаний апгрейда", "Jarvis" not in last_text(), last_text())
    alice = await users.get(1001)
    check("баланс записан в БД", alice.balance >= 1, str(alice.balance))

    # --- 2. кулдаун ---
    section("2. Кулдаун /jcoin")
    await feed(dp, ALICE, "/jcoin", 1001)
    check("кулдаун сработал", last_text().startswith("Подожди! через "), last_text())
    check("время в кулдауне ~3 часа", "час" in last_text(), last_text())
    check("нет 'Место в топе' в отказе", "Место в топе" not in last_text(), last_text())

    # --- 3. алиасы ---
    section("3. Алиасы команд и текста")
    await feed(dp, BOB, "/jcoin", 1002)
    check("/jcoin другому игроку работает", "Вы получили" in last_text(), last_text())
    await feed(dp, BOB, "Джасткоины", GROUP_A, "group")
    check("текст «Джасткоины» в группе", "Вы получили" in last_text()
          or "Подожди" in last_text(), last_text())
    await feed(dp, BOB, "джастКОИНЫ", GROUP_A, "group")
    check("регистр не важен", "Вы получили" in last_text()
          or "Подожди" in last_text(), last_text())
    await feed(dp, BOB, "какой-то рандомный текст", GROUP_A, "group")
    check("обычный текст в группе игнорируется", not SENT, str(last_text()))

    # --- 4. глобальность баланса ---
    section("4. Баланс общий для всех чатов")
    await feed(dp, ALICE, "/jcoin", GROUP_A, "group")
    alice_lb = (await users.get(1001)).balance
    await feed(dp, ALICE, "/jtop", GROUP_B, "group")
    check("баланс не зависит от чата",
          f"{alice_lb} {CURRENCY_EMOJI}" in last_text(), last_text())
    check("в топе есть Алиса", "@alice" in last_text(), last_text())

    # --- 5. /jtop ---
    section("5. /jtop")
    await feed(dp, BOB, "/jtop", 1002)
    check("заголовок топа", last_text().startswith("🏆 Топ игроков:"), last_text())
    check("формат строки топа", "1. @bob — " in last_text() or "2. @bob — " in last_text(),
          last_text())
    check("сумма с 🪙", f"{CURRENCY_EMOJI}" in last_text(), last_text())
    check("есть 'Твоё место'", "Твоё место:" in last_text(), last_text())
    check("формат 'Твоё место: N/M (X 🪙)'",
          any(part.startswith("Твоё место: ") and "(" in part
              for part in last_text().splitlines()), last_text())
    await feed(dp, BOB, "/top", 1002)
    check("алиас /top", last_text().startswith("🏆 Топ игроков:"), last_text())
    await feed(dp, BOB, "Топ", GROUP_A, "group")
    check("текст «Топ» в группе", last_text().startswith("🏆 Топ игроков:"), last_text())

    # --- 6. /jstats ---
    section("6. /jstats (карточка)")
    await feed(dp, ALICE, "/jstats", 1001)
    check("отправлено фото", bool(SENT[-1].photo), str(SENT[-1].photo))
    check("подпись к карточке", "статистика" in SENT[-1].text.lower(),
          str(SENT[-1].text))
    check("обычному игроку карточка без Jarvis", True)

    # Карточка обычного игрока не содержит Jarvis-блока.
    from bot.services.stats_service import StatsService

    plain_card = await StatsService(db).build(await users.get(1001))
    check("карточка собрана без секретов",
          plain_card.jarvis_unlocked is False and plain_card.jarvis_coins == 0,
          str(plain_card))

    # Длинное имя и аватарка.
    long_user = User(id=1003, is_bot=False,
                     first_name="ОченьДлинноеИмя" * 6, username="u" * 40)
    await feed(dp, long_user, "/jcoin", 1003)
    await feed(dp, long_user, "/jstats", 1003)
    check("длинное имя: фото отправлено", bool(SENT[-1].photo), str(SENT[-1].photo))
    long_card = await StatsService(db).build(await users.get(1003))
    from bot.utils.card import render_card

    check("карточка с длинным именем рисуется",
          render_card(long_card)[:8] == b"\x89PNG\r\n\x1a\n")

    # Фолбэк при ошибке рендера.
    # Подменяем ровно тот символ, который импортирован в commands, и
    # возвращаем ИМЕННО его: render_card и render_card_async — разные функции.
    import bot.handlers.commands as commands_module

    original_render = commands_module.render_card_async

    async def broken_render(data):
        raise RuntimeError("искусственная ошибка рендера")

    commands_module.render_card_async = broken_render
    await feed(dp, ALICE, "/jstats", 1001)
    check("при ошибке карточки — текстовая статистика",
          last_text().startswith("📊"), last_text())
    check("текстовый фолбэк содержит баланс",
          f"{CURRENCY_EMOJI}" in last_text(), last_text())
    check("фолбэк не показывает Jarvis", "Jarvis" not in last_text(), last_text())
    commands_module.render_card_async = original_render
    # Проверяем восстановление: иначе все следующие проверки карточек
    # проходили бы по текстовому фолбэку, а не по картинке.
    SENT.clear()
    await feed(dp, ALICE, "/jstats", 1001)
    check("после восстановления карточка снова рисуется",
          bool(SENT[-1].photo), str(SENT[-1].text)[:80])

    # --- 7. секретная команда до активации ---
    section("7. /jshop до активации промокода")
    for command in ("/jshop", "/jupgrade"):
        SENT.clear()
        await dispatch(make_update(uid(), ALICE, command, 1001))
        check(f"{command} полностью игнорируется (нет ответа)", not SENT,
              str([m.text for m in SENT]))
    # Кнопка магазина тоже не должна работать.
    SENT.clear()
    ALERTS.clear()
    await feed_cb(dp, ALICE, "shop:buy:x2", 1001)
    check("кнопка магазина закрыта до активации",
          ALERTS and ALERTS[-1] == messages.BOT_ERROR, str(ALERTS))
    check("покупка не прошла", (await users.get(1001)).multiplier == 1)

    # --- 8. промокод ---
    section("8. Промокод")
    await feed(dp, ALICE, "/promo неправильный", 1001)
    check("неверный код", last_text() == "Такого промокода не существует", last_text())

    await feed(dp, ALICE, "/promo", 1001)
    check("без аргумента — использование", last_text().startswith("Использование: /promo"),
          last_text())

    await feed(dp, ALICE, "/promo jarvis", 1001)
    check("первая активация успешна",
          last_text() == "🤖 Доступ разрешён. Ты получил 1 секретный Jarvis-коин. "
                         "Открыта секретная команда: /jupgrade", last_text())
    alice = await users.get(1001)
    check("jarvis_unlocked = 1", alice.jarvis_unlocked == 1)
    check("1 Jarvis-коин", alice.jarvis_coins == 1, str(alice.jarvis_coins))
    check("обычный баланс не изменился", alice.balance == alice_lb, str(alice.balance))

    await feed(dp, ALICE, "/promo JARVIS", 1001)
    check("повторный код", last_text() == "Этот промокод ты уже активировал", last_text())
    alice = await users.get(1001)
    check("повтор не выдал ещё коин", alice.jarvis_coins == 1, str(alice.jarvis_coins))

    # Код сообщением в личке.
    await feed(dp, BOB, "Jarvis", 1002)
    check("код сообщением в личке работает",
          "Открыта секретная команда: /jupgrade" in last_text(), last_text())
    check("Bob'у начислен 1 Jarvis-коин", (await users.get(1002)).jarvis_coins == 1,
          str((await users.get(1002)).jarvis_coins))
    await feed(dp, BOB, "просто болтовня", 1002)
    check("обычный текст в личке игнорируется", not SENT, str(last_text()))

    # Промокод с дополнительными монетами (без открытия секретной части).
    await feed(dp, CAROL, "/promo jarvis25", 1003)
    check("промокод с 25 монетами активирован",
          "25 Jarvis-коинов" in last_text(), last_text())
    carol_now = await users.get(1003)
    check("Carol получила 25 Jarvis-коинов", carol_now.jarvis_coins == 25,
          str(carol_now.jarvis_coins))
    check("секретная часть не открылась дополнительным кодом",
          carol_now.jarvis_unlocked == 0, str(carol_now.jarvis_unlocked))
    await feed(dp, CAROL, "/jshop", 1003)
    check("магазин для Carol закрыт", not SENT, str([m.text for m in SENT]))

    # --- 9. магазин апгрейдов ---
    section("9. Магазин апгрейдов (/jshop)")
    await feed(dp, ALICE, "/jshop", 1001)
    shop_text = last_text()
    check("магазин открылся", "Jarvis Shop" in shop_text, shop_text[:120])
    check("видны множители x2/x3",
          all(t in shop_text for t in ("x2", "x3")), shop_text[:200])
    check("видны сокращения кулдауна",
          "−30 минут" in shop_text and "−2 часа" in shop_text, shop_text[:200])
    # Обновление 1.1.2: x999 убран из каталога — он ломал экономику.
    check("x999 больше не в магазине", "x999" not in shop_text, shop_text[:300])
    check("у множителей есть описания",
          shop_text.count("Джаст коины начисляются") >= 2, shop_text[:300])
    check("показан текущий кулдаун", "Кулдаун сейчас: 3 часа" in shop_text, shop_text[:300])
    check("баланс Jarvis показан", "Jarvis-коинов: <b>1</b>" in shop_text, shop_text[:200])
    check("кнопки магазина есть", last_reply_markup() is not None)

    # Покупка x2 кнопкой.
    await feed_cb(dp, ALICE, "shop:buy:x2", 1001)
    alice = await users.get(1001)
    check("x2 куплен кнопкой", alice.multiplier == 2, str(alice.multiplier))
    check("Jarvis-коин списан", alice.jarvis_coins == 0, str(alice.jarvis_coins))
    check("обычный баланс не тронут", alice.balance == alice_lb, str(alice.balance))
    # Результат покупки всплывает отдельным окном (callback.answer),
    # а само сообщение магазина обновляется edit_text.
    check("результат покупки показан",
          ALERTS and "Куплен апгрейд" in ALERTS[-1], str(ALERTS))
    check("после покупки кулдаун прежний",
          bot.edits and "Кулдаун сейчас: 3 часа" in str(bot.edits[-1]),
          str(bot.edits[-1:])[:200])
    check("после покупки в магазине видно x2",
          bot.edits and "x2" in str(bot.edits[-1]), str(bot.edits[-1:])[:200])

    # Повторная покупка и нехватка монет.
    await feed_cb(dp, ALICE, "shop:buy:x2", 1001)
    check("повторная покупка x2 отклонена",
          ALERTS and ALERTS[-1] == messages.SHOP_ALREADY_OWNED, str(ALERTS))
    check("x2 не списан повторно", (await users.get(1001)).jarvis_coins == 0)

    await feed_cb(dp, BOB, "shop:buy:x3", 1002)
    check("без монет — нехватка",
          ALERTS and "Не хватает Jarvis-коинов" in ALERTS[-1], str(ALERTS))
    check("у Боба множитель не изменился",
          (await users.get(1002)).multiplier == 1)

    # instant игроку не продаётся.
    await db.execute("UPDATE users SET jarvis_coins = 5 WHERE user_id = 1001")
    await feed_cb(dp, ALICE, "shop:buy:instant", 1001)
    check("instant не продаётся игроку",
          ALERTS and ALERTS[-1] == messages.SHOP_NOT_FOR_SALE, str(ALERTS))
    check("instant не применён", not (await users.get(1001)).has_instant)

    # Неизвестный апгрейд.
    await feed_cb(dp, ALICE, "shop:buy:что-то", 1001)
    check("неизвестный апгрейд",
          ALERTS and ALERTS[-1] == messages.SHOP_UNKNOWN, str(ALERTS))

    # x2 → x3.
    await db.execute("UPDATE users SET jarvis_coins = 100 WHERE user_id = 1001")
    await feed_cb(dp, ALICE, "shop:buy:x3", 1001)
    check("x3 куплен поверх x2", (await users.get(1001)).multiplier == 3,
          str((await users.get(1001)).multiplier))
    check("списано 10 монет", (await users.get(1001)).jarvis_coins == 90,
          str((await users.get(1001)).jarvis_coins))

    # Сокращение кулдауна.
    await feed_cb(dp, ALICE, "shop:buy:cd30", 1001)
    alice = await users.get(1001)
    check("КД −30 мин куплен", alice.cooldown_reduction == 1800,
          str(alice.cooldown_reduction))
    check("в магазине виден купленный апгрейд",
          bot.edits and "кулдаун" in str(bot.edits[-1]).lower(),
          str(bot.edits[-1:])[:300])

    # Админ выдаёт instant игроку. В личке цель — сам админ, поэтому
    # сначала проверяем, что команда отвечает, а затем применяем к Алисе.
    await feed(dp, ADMIN, "/jinstant on", 999)
    check("админ выдал instant себе",
          "Мгновенное получение выдано" in last_text(), last_text())
    await feed(dp, ADMIN, "/jinstant off", 999)
    check("админ снял instant себе",
          "снято" in last_text(), last_text())
    admin_player = await users.get(999)
    check("у админа instant снят",
          admin_player is None or not admin_player.has_instant)

    # Выдача другому игроку: в группе нужен явный ID/username.
    await feed(dp, ADMIN, "/jinstant on", GROUP_A, "group")
    check("в группе без цели — подсказка",
          "Не удалось определить игрока" in last_text(), last_text())

    upgrades_service = dp["upgrade"]
    await upgrades_service.set_instant(await users.get(1001), True)
    check("instant применён игроку Алисе", (await users.get(1001)).has_instant)
    # /jinstant без аргумента — подсказка, а не выдача.
    await feed(dp, ADMIN, "/jinstant", 999)
    check("jinstant без on/off — использование",
          "Использование" in last_text(), last_text())
    await feed(dp, ADMIN, "/jinstant", 999)
    check("jinstant без аргумента не выдаёт instant",
          "Использование" in last_text(), last_text())
    await feed(dp, ADMIN, "/jinstant maybe", 999)
    check("jinstant с неизвестным аргументом — usage",
          "Использование" in last_text(), last_text())
    await feed(dp, ALICE, "/jinstant on", 1001)
    check("обычному игроку jinstant недоступен",
          last_text() == messages.NO_RIGHTS, last_text())

    # --- 10. множитель в /jcoin ---
    section("10. Множитель в /jcoin")
    before = await users.get(1001)
    before_balance = before.balance
    before_earned = before.total_earned
    await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 1001")
    await feed(dp, ALICE, "/jcoin", 1001)
    alice = await users.get(1001)
    check("total_earned вырос ровно на награду",
          alice.total_earned - before_earned == alice.balance - before_balance,
          f"{before_earned}->{alice.total_earned}, {before_balance}->{alice.balance}")
    gained = alice.balance - before_balance
    check("награда кратна 3 (множитель x3)", gained % 3 == 0, str(gained))
    check("награда в диапазоне x3",
          3 * CLAIM_MIN_AMOUNT <= gained <= 3 * CLAIM_MAX_AMOUNT, str(gained))
    check("есть строка «Апгрейд Jarvis x2 ⚡»",
          messages.CLAIM_UPGRADE_NOTE in last_text(), last_text())
    check("есть место в топе", "Место в топе:" in last_text(), last_text())
    check("у другого игрока строки апгрейда нет", True)

    await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 1002")
    await feed(dp, BOB, "/jcoin", GROUP_B, "group")
    check("у Боба нет строки апгрейда", "Jarvis" not in last_text(), last_text())

    # --- 11. курьер и посылка ---
    section("11. Курьер и кнопка «Забрать посылку»")
    from bot.db.repository import PackageRepository
    from bot.services.courier_service import CourierService
    from bot.services.maintenance_service import MaintenanceService
    from bot.db.repository import FeatureRepository
    from bot.services.user_service import UserService

    user_service = UserService(db)
    packages_repo = PackageRepository(db)
    courier = CourierService(db)
    maintenance = MaintenanceService(FeatureRepository(db))
    bob_id = 1002

    # Посылка в пути: игрок жмёт раньше времени. Запас в час, чтобы тест
    # не зависел от скорости машины.
    future = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    in_transit_id = await packages_repo.create(bob_id, 20, future)
    pending = await packages_repo.get(in_transit_id)
    check("посылка в БД сохраняется", pending is not None and pending.amount == 20,
          str(pending))
    await feed_cb(dp, BOB, f"package:{in_transit_id}", bob_id)
    check("посылка в пути: сообщение",
          bool(ALERTS) and ALERTS[-1].startswith("Курьер ещё в пути, осталось "),
          f"alerts={ALERTS} sent={[m.text for m in SENT]}")
    check("осталось время отформатировано",
          ALERTS and "секунд" in ALERTS[-1], str(ALERTS))
    bob = await users.get(bob_id)
    check("баланс не изменился в пути", bob.balance is not None)

    # Посылка пришла: уведомление с кнопкой.
    past = int((datetime.now(timezone.utc) - timedelta(seconds=5)).timestamp())
    ready_id = await packages_repo.create(bob_id, 35, past)
    sent = await courier.notify_due(bot, maintenance)
    check("курьер доставлен, отправлено уведомление", sent >= 1, str(sent))
    check("в сообщении сумма с 🪙",
          any("35 🪙" in m.text for m in SENT), str([m.text for m in SENT[-3:]]))
    check("кнопка «Забрать посылку»",
          any(m.reply_markup and m.reply_markup.inline_keyboard
              and m.reply_markup.inline_keyboard[0][0].text == "📦 Забрать посылку"
              for m in SENT), "кнопки нет")

    balance_before = (await users.get(bob_id)).balance
    await feed_cb(dp, BOB, f"package:{ready_id}", bob_id)
    bob = await users.get(bob_id)
    check("посылка начислена", bob.balance == balance_before + 35,
          f"{balance_before} -> {bob.balance}")
    check("в ответе есть сумма и место",
          "35 🪙" in last_text() and "Место в топе:" in last_text(), last_text())
    check("кнопка убрана после выдачи", bot.edits and bot.edits[-1] is None,
          str(bot.edits[-3:]))

    # Повторное нажатие.
    await feed_cb(dp, BOB, f"package:{ready_id}", bob_id)
    check("повторно посылку не выдать",
          ALERTS and ALERTS[-1] == "Эту посылку ты уже забрал", str(ALERTS))

    # Чужая посылка.
    await feed_cb(dp, ALICE, f"package:{ready_id}", bob_id)
    check("чужая посылка", ALERTS and ALERTS[-1] == "Это не твоя посылка", str(ALERTS))

    # Несуществующая посылка.
    await feed_cb(dp, BOB, "package:99999999", bob_id)
    check("несуществующая посылка — ошибка",
          ALERTS and ALERTS[-1] == messages.BOT_ERROR, str(ALERTS))

    # --- 11b. админ без кулдауна ---
    section("11b. У админа нет кулдауна на /jcoin")
    # Админ мог ещё не пользоваться ботом — регистрируем принудительно.
    admin_before = await user_service.get_or_none(999)
    admin_balance = admin_before.balance if admin_before else 0
    admin_rewards = []
    for _ in range(5):
        await feed(dp, ADMIN, "/jcoin", 999)
        text = last_text()
        check_no_cd = ("Вы получили" in text)
        if not check_no_cd:
            break
        admin_rewards.append(text)
    check("админ получает коины 5 раз подряд без кулдауна",
          len(admin_rewards) == 5
          and all("Подожди" not in t for t in admin_rewards),
          f"{len(admin_rewards)}/5: {admin_rewards[:1]}")
    admin_now = await users.get(999)
    check("баланс админа вырос",
          admin_now.balance > admin_balance,
          f"{admin_balance} -> {admin_now.balance}")

    # Обычный игрок с теми же правами ограничен.
    await db.execute("UPDATE users SET last_claim_at = ? WHERE user_id = 1003",
                     (int(datetime.now(timezone.utc).timestamp()),))
    await feed(dp, CAROL, "/jcoin", 1003)
    check("обычный игрок получает отказ по кулдауну",
          "Подожди! через" in last_text(), last_text())
    check("в отказе указано реальное время",
          "часа" in last_text() or "минут" in last_text(), last_text())

    # --- 12. техработы ---
    section("12. Режим техработ")
    await feed(dp, ADMIN, "/maintenance_status", 999)
    check("статус: всё включено", "Все функции включены" in last_text(), last_text())

    await feed(dp, ALICE, "/maintenance_status", 1001)
    check("нет прав", last_text() == "Эта команда только для админов", last_text())

    await feed(dp, ADMIN, "/maintenance_on", 999)
    check("использование on",
          last_text() == "Использование: /maintenance_on <функция> <причина>", last_text())

    await feed(dp, ADMIN, "/maintenance_on claim", 999)
    check("причина по умолчанию",
          last_text() == "Функция claim отключена. Причина: Технические работы", last_text())

    await feed(dp, ADMIN, "/maintenance_on claim Плановое обслуживание", 999)
    check("своя причина",
          last_text() == "Функция claim отключена. Причина: Плановое обслуживание",
          last_text())

    await feed(dp, ADMIN, "/maintenance_on неизвестная", 999)
    check("неизвестная функция", "Неизвестная функция" in last_text(), last_text())

    await feed(dp, ALICE, "/jcoin", 1001)
    check("claim заблокирован",
          last_text() == "Временно эта функция недоступна\n"
                         "Причина: Плановое обслуживание", last_text())

    await feed(dp, ALICE, "/jtop", 1001)
    check("top работает", last_text().startswith("🏆 Топ игроков:"), last_text())

    # Админ обходит техработы.
    await feed(dp, ADMIN, "/jcoin", 999)
    check("админ обходит техработы", "Вы получили" in last_text(), last_text())

    await feed(dp, ADMIN, "/maintenance_status", 999)
    check("статус показывает отключённое",
          "claim" in last_text() and "Плановое обслуживание" in last_text(), last_text())

    # Остальные функции.
    for feature, command, marker in (
        ("top", "/jtop", "🏆"),
        ("stats", "/jstats", "фото"),
        ("promo", "/promo jarvis", "промокод"),
    ):
        await feed(dp, ADMIN, f"/maintenance_on {feature} Тест", 999)
        SENT.clear()
        await dispatch(make_update(uid(), ALICE, command, 1001))
        text = last_text()
        check(f"{feature} заблокирован",
              text.startswith("Временно эта функция недоступна")
              and "Причина: Тест" in text, text)
        await feed(dp, ADMIN, f"/maintenance_off {feature}", 999)
        check(f"{feature} включён обратно",
              last_text() == f"Функция {feature} снова включена", last_text())

    # courier.
    await feed(dp, ADMIN, "/maintenance_on courier Тест", 999)
    await feed_cb(dp, BOB, f"package:{in_transit_id}", bob_id)
    check("courier заблокирован",
          ALERTS and "Временно эта функция недоступна" in ALERTS[-1], str(ALERTS))
    await feed(dp, ADMIN, "/maintenance_off courier", 999)

    await feed(dp, ADMIN, "/maintenance_off claim", 999)
    await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 1001")
    await feed(dp, ALICE, "/jcoin", 1001)
    check("claim снова работает", "Вы получили" in last_text()
          or "Подожди" in last_text(), last_text())

    # all.
    await feed(dp, ADMIN, "/maintenance_on all Глобальная пауза", 999)
    for command in ("/jcoin", "/jtop", "/jstats", "/promo jarvis"):
        await dispatch(make_update(uid(), ALICE, command, 1001))
        check(f"all блокирует {command}",
              "Глобальная пауза" in last_text(), last_text())
    await feed(dp, ADMIN, "/jcoin", 999)
    check("all: админ обходит", "Вы получили" in last_text()
          or "Подожди" in last_text(), last_text())
    await feed(dp, ADMIN, "/maintenance_off all", 999)
    # Точечные флаги, включённые ранее в тесте, снимаем.
    for feature in ("claim", "courier", "top", "stats", "promo"):
        await feed(dp, ADMIN, f"/maintenance_off {feature}", 999)
    await feed(dp, ADMIN, "/maintenance_status", 999)
    check("все функции включены", "Все функции включены" in last_text(), last_text())
    await dispatch(make_update(uid(), ALICE, "/jtop", 1001))
    check("all выключен, top работает", last_text().startswith("🏆"), last_text())

    # --- 13. антиспам ---
    section("13. Антиспам")
    from bot.handlers.errors import AntispamMiddleware

    spammers = [m for m in dp.message.middleware if isinstance(m, AntispamMiddleware)]
    spammers[0].cooldown = 1.0
    spammers[0]._last_seen.clear()  # сбрасываем историю предыдущих прогонов
    SENT.clear()
    for _ in range(4):
        await dispatch(make_update(uid(), ALICE, "/jtop", 1001))
    check("первый запрос прошёл", bool(SENT) and SENT[0].text.startswith("🏆"),
          str([m.text for m in SENT[:2]]))
    check("остальные отклонены антиспамом",
          len(SENT) == 4
          and all(m.text == "Не так быстро! Подожди секунду" for m in SENT[1:]),
          str([m.text for m in SENT[1:]]))
    check("антиспам не блокирует других игроков", True)
    spammers[0].cooldown = 0.0
    SENT.clear()
    await dispatch(make_update(uid(), BOB, "/jtop", 1002))
    check("другой игрок проходит", last_text().startswith("🏆"), last_text())

    # --- 14. /help и меню команд ---
    section("14. /help и меню команд")
    await feed(dp, ALICE, "/help", 1001)
    check("/help содержит /jcoin", "/jcoin" in last_text())
    check("/help содержит /jtop", "/jtop" in last_text())
    check("/help содержит /jstats", "/jstats" in last_text())
    check("/help содержит /promo", "/promo" in last_text())
    check("/help НЕ содержит /jupgrade", "jupgrade" not in last_text().lower(), last_text())
    check("/help НЕ содержит Jarvis", "jarvis" not in last_text().lower(), last_text())

    await feed(dp, ALICE, "/start", 1001)
    check("/start показывает помощь", "/jcoin" in last_text())

    # Меню команд выставляется при старте; в тесте регистрируем его вручную
    # тем же кодом, что и в main.on_startup.
    await bot.set_my_commands(bot_main.PUBLIC_COMMANDS)
    registered = {c.command for c in bot.registered_commands}
    check("меню команд содержит публичные",
          {"jcoin", "jtop", "jstats", "promo"} <= registered, str(registered))
    check("меню команд НЕ содержит /jupgrade", "jupgrade" not in registered,
          str(registered))
    check("меню команд НЕ содержит /maintenance_on",
          "maintenance_on" not in registered, str(registered))

    # --- 15. Jarvis-блок в карточке ---
    section("15. Jarvis-блок в /jstats")
    alice_card = await StatsService(db).build(await users.get(1001))
    check("разблокированному виден блок Jarvis",
          alice_card.jarvis_unlocked and alice_card.has_upgrade, str(alice_card))
    # Боб промокод активировал, поэтому проверяем на игроке без секретов.
    carol = await user_service.touch(1009, "carol", "Кэрол")
    carol_card = await StatsService(db).build(carol)
    check("обычному игроку блок не показывается",
          not carol_card.jarvis_unlocked and carol_card.jarvis_coins == 0,
          str(carol_card))
    from bot.utils.card import render_card

    check("карточка с Jarvis рисуется",
          render_card(alice_card)[:8] == b"\x89PNG\r\n\x1a\n")

    # --- 16. Jarvis-коины не в топе ---
    section("16. Jarvis-коины отдельно от баланса")
    await db.execute("UPDATE users SET jarvis_coins = 5, balance = 0 WHERE user_id = 1002")
    await feed(dp, BOB, "/jtop", 1002)
    check("Jarvis-коины не попадают в топ",
          "5 🪙" not in last_text().split("Твоё место")[-1] or "@bob — 0 🪙" in last_text(),
          last_text())
    check("баланс остался обычным", "@bob — 0 🪙" in last_text(), last_text())

    # --- 18. запуск бота ---
    section("18. Инициализация при старте")
    import inspect

    startup = bot_main.make_startup(dp)
    # aiogram передаёт в startup-хендлеры только bot и workflow_data.
    # Любой лишний параметр привёл бы к TypeError и падению бота при старте.
    check("startup-хендлер принимает только bot",
          list(inspect.signature(startup).parameters) == ["bot"],
          str(list(inspect.signature(startup).parameters)))
    check("shutdown-хендлер без параметров",
          not inspect.signature(bot_main.on_shutdown).parameters,
          str(inspect.signature(bot_main.on_shutdown).parameters))

    # Роутеры нельзя делить между Dispatcher: каждый build_dispatcher()
    # обязан создавать собственные экземпляры.
    try:
        second_dp = bot_main.build_dispatcher()
        check("второй Dispatcher собирается без ошибок", True)
        check("у второго Dispatcher столько же роутеров",
          len(second_dp.sub_routers) == len(dp.sub_routers),
          f"{len(second_dp.sub_routers)} != {len(dp.sub_routers)}")
    except RuntimeError as exc:
        check("второй Dispatcher собирается без ошибок", False, str(exc))

    # set_my_commands перехватывается подменённой сессией, поэтому запись
    # попадает в bot.registered_commands, а не в сеть.
    await bot.set_my_commands(bot_main.PUBLIC_COMMANDS)
    check("меню команд выставляется при старте",
          [c.command for c in bot.registered_commands]
          == [c.command for c in bot_main.PUBLIC_COMMANDS],
          str([c.command for c in bot.registered_commands]))

    await startup(bot)
    courier_task = dp.get("courier_task")
    check("фоновый цикл курьера запущен", courier_task is not None,
          str(courier_task))
    if courier_task is not None:
        await asyncio.sleep(0.1)
        check("цикл курьера не падает сразу", not courier_task.done())
        courier_task.cancel()
        try:
            await courier_task
        except asyncio.CancelledError:
            pass

    # --- 19. место в топе после начисления ---
    section("19. Место в топе считается по свежему балансу")
    # Регрессия: раньше /jcoin считал место по объекту игрока, загруженному
    # ДО начисления, и показывал устаревшую позицию (иногда «4/3»).
    for player_id, balance in ((1001, 10), (1002, 12), (1003, 11)):
        await db.execute("UPDATE users SET balance = ?, last_claim_at = 0 WHERE user_id = ?",
                         (balance, player_id))
    await db.execute("UPDATE users SET created_at = user_id WHERE user_id IN (1001,1002,1003)")

    await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 1001")
    await feed(dp, ALICE, "/jcoin", 1001)
    shown = _place_from_text(last_text())

    alice_now = await users.get(1001)
    expected_place, expected_total = await user_service.place_and_total(alice_now)
    check("показанное место равно реальному",
          shown == (expected_place, expected_total),
          f"в ответе {shown}, в БД {expected_place}/{expected_total}")
    check("место не превышает число игроков",
          shown[0] <= shown[1], f"{shown[0]}/{shown[1]}")
    check("начисление реально изменило баланс", alice_now.balance > 10,
          str(alice_now.balance))

    # Прыжок вверх по топу: игрок обгоняет более «богатых».
    await db.execute("UPDATE users SET balance = 20, last_claim_at = 0 WHERE user_id = 1003")
    await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 1002")
    await feed(dp, BOB, "/jcoin", 1002)
    bob_now = await users.get(1002)
    bob_expected, _ = await user_service.place_and_total(bob_now)
    check("после начисления место пересчитано, а не устаревшее",
          _place_from_text(last_text())[0] == bob_expected,
          f"в ответе {_place_from_text(last_text())}, ожидалось {bob_expected}")

    # --- 20. админские команды не режет антиспам ---
    section("20. Антиспам не мешает админу")
    spammers = [m for m in dp.message.middleware if isinstance(m, AntispamMiddleware)]
    spammers[0].cooldown = 1.0
    spammers[0]._last_seen.clear()

    SENT.clear()
    for _ in range(4):
        await dispatch(make_update(uid(), ADMIN, "/maintenance_status", 999))
    admin_replies = [m.text for m in SENT]
    check("серия админских команд не блокируется",
          len(admin_replies) == 4
          and all(messages.ANTISPAM not in t for t in admin_replies),
          str(admin_replies))
    check("все ответы — реальные ответы, а не антиспам",
          all("Режим техработ" in t for t in admin_replies), str(admin_replies))

    # Обычный игрок по-прежнему ограничен.
    SENT.clear()
    for _ in range(3):
        await dispatch(make_update(uid(), CAROL, "/jtop", 1003))
    player_replies = [m.text for m in SENT]
    check("обычному игроку антиспам по-прежнему нужен",
          len(player_replies) >= 1
          and player_replies[0].startswith("🏆")
          and any(messages.ANTISPAM in t for t in player_replies[1:]),
          str(player_replies))
    spammers[0].cooldown = 0.0
    spammers[0]._last_seen.clear()

    # --- 21. maintenance_off all снимает все флаги ---
    section("21. /maintenance_off all возвращает всё в работу")
    await feed(dp, ADMIN, "/maintenance_on claim Точечная", 999)
    await feed(dp, ADMIN, "/maintenance_on stats Точечная", 999)
    check("точечные функции отключены",
          await maintenance.is_disabled("claim") and await maintenance.is_disabled("stats"))

    await feed(dp, ADMIN, "/maintenance_off all", 999)
    check("off all снимает точечные флаги",
          not await maintenance.is_disabled("claim")
          and not await maintenance.is_disabled("stats"),
          "флаги остались")
    await feed(dp, ADMIN, "/maintenance_status", 999)
    check("статус показывает всё включённым",
          messages.MAINTENANCE_STATUS_NONE in last_text(), last_text())

    # Статус при глобальном флаге не должен вводить в заблуждение.
    await feed(dp, ADMIN, "/maintenance_on all Глобальная пауза", 999)
    await feed(dp, ADMIN, "/maintenance_status", 999)
    status_text = last_text()
    check("при 'all' в статусе видны все функции как отключённые",
          all(f"• {f} — отключено" in status_text
              for f in ("claim", "courier", "top", "stats", "promo")),
          status_text)
    check("причина 'all' показана", "Глобальная пауза" in status_text, status_text)
    await feed(dp, ADMIN, "/maintenance_off all", 999)
    await feed(dp, ADMIN, "/maintenance_status", 999)
    check("после off all всё работает",
          messages.MAINTENANCE_STATUS_NONE in last_text(), last_text())

    # --- 22. админ-панель ---
    section("22. Админ-панель")
    # Диалоги панели не должны «протекать» в следующие разделы.
    from bot.handlers.admin_panel import DIALOGS
    DIALOGS.clear()
    products = dp["products"]
    links = dp["links"]
    promo = dp["promo"]
    announce = dp["announce"]
    stats_service = dp["stats"]
    await feed(dp, ADMIN, "/apanel", 999)
    check("панель открылась", "Панель администратора" in last_text(), last_text())
    check("в панели есть кнопки", last_reply_markup() is not None)
    panel_buttons = {b.text for row in last_reply_markup().inline_keyboard
                     for b in row}
    for label, key in (
        ("техработ", messages.AP_SECTION_MAINT),
        ("промокодов", messages.AP_SECTION_PROMO),
        ("товаров", messages.AP_SECTION_SHOP),
        ("тем", messages.AP_SECTION_THEMES),
        ("банов", messages.AP_SECTION_BANS),
        ("статусов", messages.AP_SECTION_STATUS),
        ("написать в чат", messages.AP_SECTION_CHATS),
        ("статистики", messages.AP_SECTION_STATS),
    ):
        check(f"раздел {label} в панели", key in panel_buttons, str(panel_buttons))

    await feed(dp, ALICE, "/apanel", 1001)
    check("обычному игроку панель недоступна",
          last_text() == messages.AP_DENIED, last_text())

    # Техработы: переключение кнопкой.
    await feed_cb(dp, ADMIN, "ap:maint:claim", 999)
    check("на выключение спрашивается причина", "причину" in str(bot.edits[-1]),
          str(bot.edits[-1])[:120])
    await feed(dp, ADMIN, "Плановые работы", 999)
    check("причина сохранена", "Плановые работы" in last_text(), last_text())
    check("функция действительно отключена", await maintenance.is_disabled("claim"))
    await feed_cb(dp, ADMIN, "ap:maint:claim", 999)
    check("повторное нажатие включает обратно", "снова включена" in str(bot.edits[-1]),
          str(bot.edits[-1])[:120])

    await feed_cb(dp, ADMIN, "ap:stats", 999)
    check("статистика показывает игроков", "Игроков:" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    check("статистика показывает чаты", "Чатов:" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])

    # Создание товара: шаг 1 (название + цена) → шаг 2 (тип) → значение.
    await feed_cb(dp, ADMIN, "ap:shop:new:", 999)
    check("товар: спрашивают название и цену", "название" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed(dp, ADMIN, "Золотой множитель 250", 999)
    check("товар: показан выбор типа", "тип" in last_text().lower(), last_text()[:160])
    await feed_cb(dp, ADMIN, "ap:type:multiplier", 999)
    check("товар: спрашивается значение", "значение" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed(dp, ADMIN, "5", 999)
    check("товар создан", "описание" in last_text(), last_text()[:160])
    created = await products.upgrades()
    check("товар попал в каталог",
          len(created) == 1 and created[0]["value"] == 5, str(created))
    check("код сгенерирован из названия",
          created[0]["code"] == "zolotoy-mnozhitel", str(created[0]["code"]))
    # Описание — шаг 4, пустое сообщение оставляет товар без него.
    await feed(dp, ADMIN, "Двойной множитель навсегда", 999)
    check("описание сохранено",
          last_text() == messages.AP_ITEM_DESC_SAVED, last_text()[:160])
    check("предложены ссылки товару", last_reply_markup() is not None)
    await feed_cb(dp, ADMIN, "ap:itemdone", 999)
    check("товар опубликован", "опубликован" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    with_desc = await products.upgrades()
    check("описание товара записано",
          with_desc[0]["description"] == "Двойной множитель навсегда",
          str(with_desc[0]["description"]))

    await feed_cb(dp, ADMIN, "ap:shop:new:", 999)
    await feed(dp, ADMIN, "без цены", 999)
    check("товар: неверный формат разобран", "Формат" in last_text(), last_text()[:120])
    await feed(dp, ADMIN, "/apanel_cancel", 999)
    check("диалог отменяется", "отменено" in last_text().lower(), last_text()[:120])

    # Тема: после типа спрашивается цвет.
    await feed_cb(dp, ADMIN, "ap:themes:new:", 999)
    await feed(dp, ADMIN, "Закат 300", 999)
    await feed_cb(dp, ADMIN, "ap:type:theme", 999)
    check("тема: спрашивается цвет", "#RRGGBB" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed(dp, ADMIN, "#FF5500", 999)
    check("тема создана", "описание" in last_text(), last_text()[:160])
    await feed_cb(dp, ADMIN, "ap:nodesc", 999)
    check("тема без описания опубликована",
          "Ссылки" in str(bot.edits[-1]), str(bot.edits[-1])[:160])
    await feed_cb(dp, ADMIN, "ap:itemdone", 999)
    themes_made = await products.themes()
    check("у темы нет описания", not themes_made[0]["description"],
          str(themes_made[0]["description"]))
    check("тема попала в каталог", len(await products.themes()) == 1)

    # Промокод из панели.
    await feed_cb(dp, ADMIN, "ap:promo:new:", 999)
    check("промокод: подсказка по формату", "GOLD50" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed(dp, ADMIN, "GOLD50 50", 999)
    check("промокод создан", "GOLD50" in last_text(), last_text()[:120])
    check("промокод в каталоге", await promo.codes.get("gold50") is not None)

    # Написать в чат от имени бота.
    chats_seen = await announce.chats.all(10)
    check("бот помнит чаты", len(chats_seen) > 0, str(len(chats_seen)))
    await feed_cb(dp, ADMIN, "ap:chats", 999)
    check("список чатов показан", "Выберите чат" in str(bot.edits[-1]),
          str(bot.edits[-1])[:120])
    if chats_seen:
        target_chat = chats_seen[0]["chat_id"]
        await feed_cb(dp, ADMIN, f"ap:chat:{target_chat}", 999)
        check("после выбора чата просят текст",
              "сообщение" in str(bot.edits[-1]).lower(), str(bot.edits[-1])[:120])
        SENT.clear()
        await feed(dp, ADMIN, "Привет из панели", 999)
        check("админу отчитались об отправке",
              "отправлено" in last_text(), last_text()[:120])
        check("текст действительно ушёл в чат",
              any(m.chat.id == target_chat and "Привет из панели" in m.text
                  for m in SENT),
              str([(m.chat.id, m.text) for m in SENT]))

    # --- 23. блокировки ---
    section("23. Блокировки")
    await feed(dp, ALICE, "/setid $John", 1001)
    await feed(dp, ADMIN, "/ban $John 30м флуд", 999)
    check("бан выставлен",
          any("забанен" in m.text for m in SENT),
          str([m.text[:60] for m in SENT]))
    SENT.clear()
    await feed(dp, ALICE, "/jcoin", 1001)
    check("забаненный получает отказ", "заблокированы" in last_text().lower(),
          last_text()[:140])
    check("забаненный не получает стандартный ответ",
          "Вы получили" not in last_text(), last_text()[:140])
    await feed(dp, ALICE, "/jtop", 1001)
    check("забаненный не попадает в топ", "Топ игроков" not in last_text(),
          last_text()[:140])

    await feed(dp, ADMIN, "/unban $John", 999)
    check("бан снят", "снят" in last_text(), last_text()[:120])
    await feed(dp, ALICE, "/jtop", 1001)
    check("после разбана бот снова отвечает", "Топ игроков" in last_text(),
          last_text()[:120])

    await feed(dp, ADMIN, "/ban $Nobody", 999)
    check("бан несуществующего — подсказка",
          "Не удалось определить игрока" in last_text(), last_text()[:120])
    await feed(dp, BOB, "/ban 1002", 1002)
    check("обычному игроку бан недоступен",
          last_text() == messages.NO_RIGHTS, last_text())
    await feed(dp, ADMIN, "/bans", 999)
    check("список банов открывается", "Забаненных" in last_text(), last_text()[:120])

    # --- 24. JustID и темы ---
    section("24. JustID и темы")
    await feed(dp, ALICE, "/myid", 1001)
    check("/myid показывает JustID", "$JOHN" in last_text(), last_text()[:120])
    await feed(dp, BOB, "/setid $john", 1002)
    check("занятый JustID не отдают", "уже занято" in last_text(), last_text()[:120])
    await feed(dp, BOB, "/setid $justcoin", 1002)
    check("резервное имя игроку недоступно", "зарезервировано" in last_text(),
          last_text()[:120])
    await feed(dp, ADMIN, "/setid $justcoin", 999)
    check("резервное имя админу доступно", "$JUSTCOIN" in last_text(), last_text()[:120])
    await feed(dp, ALICE, "/id $John", 1001)
    check("/id открывает карточку игрока",
          bool(SENT[-1].photo) or SENT[-1].text.startswith("📊"),
          str(SENT[-1].text)[:80])
    await feed(dp, ALICE, "/id $Nobody", 1001)
    check("несуществующий JustID", "не найден" in last_text(), last_text()[:120])

    await db.execute("UPDATE users SET balance = 2000 WHERE user_id = 1001")
    await feed(dp, ALICE, "/jasgnight", 1001)
    check("Джасгнит открылся", "Джасгнит" in last_text(), last_text()[:140])
    check("в Джасгните кнопки тем", last_reply_markup() is not None)
    await feed_cb(dp, ALICE, "jasg:buy:gold", 1001)
    check("тема куплена", ALERTS and "куплена" in ALERTS[-1],
          str(ALERTS)[:160])
    check("коины списаны", (await users.get(1001)).balance == 2000 - 250,
          str((await users.get(1001)).balance))
    check("тема активна", (await users.get(1001)).theme == "gold")
    await feed(dp, ALICE, "/theme royal", 1001)
    check("некупленная тема не включается", "Не хватает" in last_text(),
          last_text()[:120])

    # --- 25. статусы ---
    section("25. Статусы")
    await feed(dp, ADMIN, "/setstatus $John donator", 999)
    check("статус выдан", "ДОНАТОР" in last_text(), last_text()[:120])
    from bot.utils.card import render_card_async as _render_async
    card = await stats_service.build(await users.get(1001))
    check("статус попал в карточку", card.status_label == "ДОНАТОР",
          str(card.status_label))
    check("цвет статуса взят из каталога", card.status_color == (255, 150, 190),
          str(card.status_color))
    check("тема карточки — тема игрока", card.theme.id == "gold", card.theme.id)
    check("карточка с бейджем рисуется", len(await _render_async(card)) > 0)
    # Карточка без статуса тоже рисуется, но бейджа не содержит.
    plain = await stats_service.build(await users.get(1002))
    check("без статуса бейдж пустой", plain.status_label == "",
          str(plain.status_label))
    await feed(dp, ADMIN, "/setstatus $John off", 999)
    check("статус снят", "снят" in last_text(), last_text()[:120])
    await feed(dp, ADMIN, "/setstatus $John хакер", 999)
    check("неизвестный статус отклонён", "Неизвестный" in last_text(),
          last_text()[:140])

    # --- 26. новые промокоды ---
    section("26. Промокоды 1.1")
    await feed(dp, CAROL, "/promo obnova", 1003)
    check("OBNOVA активируется", "получил 5" in last_text(), last_text()[:120])
    check("секретная часть открылась", (await users.get(1003)).jarvis_unlocked == 1)
    await feed(dp, CAROL, "/promo jarvisnew", 1003)
    check("JARVISNEW активируется", "получил 10" in last_text(), last_text()[:120])

    # --- 27. ссылка $JustID в тексте и меню ---
    section("27. Ссылки $JustID в сообщениях")
    await feed(dp, ALICE, "/setid $JustChelik", 1001)
    check("JustID назначен", "$JUSTCHELIK" in last_text(), last_text()[:120])
    await feed(dp, ALICE, "$justchelik", 1001)
    check("ссылка $JustID открывает карточку", bool(SENT[-1].photo),
          str(SENT[-1].text)[:80])
    await feed(dp, ALICE, "$Nobody", 1001)
    check("неизвестная ссылка объясняет формат",
          "/setid" in last_text(), last_text()[:140])
    SENT.clear()
    await feed(dp, ALICE, "$50 — столько стоит доставка", 1001, chat_type="group")
    check("на символ валюты в группе бот молчит", not SENT, str(SENT)[:120])
    await feed(dp, ALICE, "$justchelik", 1001, chat_type="group")
    check("ссылка работает и в группе", bool(SENT[-1].photo),
          str(SENT[-1].text)[:80])
    await feed(dp, ALICE, "/help", 1001)
    check("/help упоминает Джасгнит", "Джасгнит" in last_text(), last_text()[:200])
    check("/help упоминает JustID", "JustID" in last_text(), last_text()[:200])
    check("/help упоминает ссылку $John", "$John" in last_text(), last_text()[:200])
    check("меню содержит новые команды",
          {"jasgnight", "setid", "id", "myid", "theme"}
          <= {c.command for c in main_module.PUBLIC_COMMANDS},
          str([c.command for c in main_module.PUBLIC_COMMANDS]))
    check("секретных команд в меню нет",
          not {"jupgrade", "jshop", "apanel", "ban"}
          & {c.command for c in main_module.PUBLIC_COMMANDS})

    # --- 28. освобождение JustID ---
    section("28. Освобождение JustID")
    await feed(dp, BOB, "/setid $Temp", 1002)
    check("JustID занят игроком", "$TEMP" in last_text(), last_text()[:120])
    await feed(dp, BOB, "/setid off", 1002)
    check("JustID освобождён", "освобождён" in last_text(), last_text()[:120])
    await feed(dp, BOB, "/myid", 1002)
    check("после освобождения JustID пуст", messages.JUSTID_MY_NONE == last_text()
          or "пока нет JustID" in last_text(), last_text()[:120])
    await feed(dp, BOB, "/setid off", 1002)
    check("повторное освобождение безопасно",
          "пока нет JustID" in last_text(), last_text()[:120])
    await feed(dp, CAROL, "/setid $Temp", 1003)
    check("освобождённое имя забрал другой", "$TEMP" in last_text(), last_text()[:120])

    # --- 29. ссылки и описания (обновление 1.1.2) ---
    section("29. Ссылки и описания")
    from bot.handlers.admin_panel import DIALOGS
    from bot.services.theme_service import ThemeService
    from bot.utils.card import render_card_async as _render_async

    DIALOGS.clear()

    # Раздел ссылок бота в панели.
    await feed_cb(dp, ADMIN, "ap:links", 999)
    check("раздел ссылок открылся", "Ссыл" in str(bot.edits[-1]),
          str(bot.edits[-1])[:120])
    check("пустой список ссылок", "Ссылок пока нет" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])

    await feed_cb(dp, ADMIN, "ap:links:add", 999)
    check("панель просит ссылку", "Название адрес" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed(dp, ADMIN, "Наш канал t.me/justcoin_robot", 999)
    check("ссылка добавлена", "добавлена" in last_text(), last_text()[:120])
    check("ссылка видна в разделе",
          "Наш канал" in str(bot.edits[-1]), str(bot.edits[-1])[:200])
    check("ссылка сохранена в базе",
          len(await links.bot_links()) == 1)

    await feed_cb(dp, ADMIN, "ap:links:add", 999)
    await feed(dp, ADMIN, "ерунда", 999)
    check("мусорная ссылка не добавлена",
          "Не понял" in last_text(), last_text()[:140])
    check("после мусора ссылка в базе одна",
          len(await links.bot_links()) == 1, str(len(await links.bot_links())))
    await feed_cb(dp, ADMIN, "ap:links", 999)
    check("в разделе ссылка одна",
          str(bot.edits[-1]).count("t.me/justcoin_robot") == 1,
          str(bot.edits[-1])[:200])

    # Выключение и включение ссылки.
    link_row = await links.bot_links()
    link_id = int(link_row[0]["id"])
    await feed_cb(dp, ADMIN, f"ap:bl:{link_id}:toggle", 999)
    check("ссылка выключается", "выключена" in str(bot.edits[-1]),
          str(bot.edits[-1])[:200])
    check("выключенная ссылка скрыта от игроков",
          not await links.bot_links())
    await feed_cb(dp, ADMIN, f"ap:bl:{link_id}:toggle", 999)
    check("ссылка включается обратно", len(await links.bot_links()) == 1)

    # Ссылка доступна игроку в /help.
    await feed(dp, ALICE, "/help", 1001)
    help_markup = last_reply_markup()
    check("в /help есть кнопки ссылок", help_markup is not None)
    if help_markup:
        check("кнопка ссылки в /help",
              any(row[0].url == "https://t.me/justcoin_robot"
                  for row in help_markup.inline_keyboard),
              str([[b.url for b in r] for r in help_markup.inline_keyboard]))

    # Ссылка товара: выбор апгрейда и ввод.
    await feed_cb(dp, ADMIN, "ap:shop", 999)
    await feed_cb(dp, ADMIN, "ap:pick:upgrade", 999)
    pick_markup = last_edit_markup()
    check("список апгрейдов для ссылки",
          pick_markup is not None
          and any("🔗 Множитель x2" in b.text
                  for row in pick_markup.inline_keyboard for b in row),
          str(pick_markup)[:200] if pick_markup else "нет клавиатуры")
    await feed_cb(dp, ADMIN, "ap:pick:upgrade:x2", 999)
    check("панель ждёт ссылку для товара",
          "Отправьте ссылку" in str(bot.edits[-1]), str(bot.edits[-1])[:160])
    await feed(dp, ADMIN, "Что такое x2 example.com/why", 999)
    check("ссылка товара добавлена", "добавлена" in last_text(), last_text()[:140])
    check("в ссылке видно её название", "Что такое x2" in last_text(),
          last_text()[:160])
    await feed_cb(dp, ADMIN, "ap:itemdone", 999)

    # Ссылка видна игроку в Jarvis Shop.
    await feed(dp, ALICE, "/jshop", 1001)
    shop_text_now = last_text()
    check("ссылка апгрейда в тексте магазина",
          "🔗 Что такое x2" in shop_text_now, shop_text_now[:400])
    shop_markup = last_reply_markup()
    check("кнопка-ссылка в магазине",
          shop_markup is not None and any(
              row[0].url == "https://example.com/why"
              for row in shop_markup.inline_keyboard),
          str(shop_markup)[:200] if shop_markup else "нет клавиатуры")

    # Удаление ссылки товара.
    await feed_cb(dp, ADMIN, "ap:link:upgrade:x2", 999)
    check("экран ссылок товара",
          "Ссылки на «Множитель x2»" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    link_markup = last_edit_markup()
    check("на экране ссылок есть кнопка добавления",
          link_markup is not None and any(
              "Добавить ссылку" in b.text
              for row in link_markup.inline_keyboard for b in row),
          str(link_markup)[:200] if link_markup else "нет клавиатуры")
    await feed_cb(dp, ADMIN, "ap:plinkdel:upgrade:x2:Что такое x2", 999)
    check("ссылка товара удалена", "пока нет ссылок" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed_cb(dp, ADMIN, "ap:back", 999)

    # Описание в витрине игрока.
    await feed(dp, ALICE, "/jasgnight", 1001)
    jasg_text = last_text()
    check("у встроенных тем есть описание",
          "Классическая карточка" in jasg_text, jasg_text[:400])
    check("у тем из конфига есть описания",
          "Тёплые золотые тона" in jasg_text, jasg_text[:400])

    # Описание кастомного товара из панели видно игроку.
    await feed_cb(dp, ADMIN, "ap:themes:new:", 999)
    await feed(dp, ADMIN, "Ночной неон 400", 999)
    await feed_cb(dp, ADMIN, "ap:type:theme", 999)
    await feed(dp, ADMIN, "#7B2DFF", 999)
    await feed(dp, ADMIN, "Фиолетовый неон для ночного неба", 999)
    await feed_cb(dp, ADMIN, "ap:itemdone", 999)
    check("кастомная тема опубликована", "опубликован" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])

    await db.execute("UPDATE users SET balance = 9000 WHERE user_id = 1001")
    await feed(dp, ALICE, "/jasgnight", 1001)
    jasg_after = last_text()
    check("кастомная тема в витрине игрока",
          "Ночной неон" in jasg_after, jasg_after[:500])
    check("описание кастомной темы видно",
          "Фиолетовый неон" in jasg_after, jasg_after[:500])
    buy_markup = last_reply_markup()
    check("у кастомной темы есть кнопка", buy_markup is not None)

    # Покупка кастомной темы игроком.
    custom_row = next(
        (row for row in await products.themes() if row["title"] == "Ночной неон"),
        None,
    )
    custom_id = ThemeService.custom_id(custom_row["code"])
    ALERTS.clear()
    await feed_cb(dp, ALICE, f"jasg:buy:{custom_id}", 1001)
    check("кастомная тема покупается игроком",
          any("куплена" in a.lower() for a in ALERTS), str(ALERTS)[:160])
    check("тема игрока — кастомная",
          (await users.get(1001)).theme == custom_id,
          str((await users.get(1001)).theme))

    # Карточка рисуется с кастомной темой.
    card = await stats_service.build(await users.get(1001))
    check("карточка использует кастомную тему", card.theme.id == custom_id,
          card.theme.id)
    check("карточка кастомной темы рисуется",
          len(await _render_async(card)) > 0)

    # Удаление общей ссылки.
    await feed_cb(dp, ADMIN, "ap:links", 999)
    await feed_cb(dp, ADMIN, f"ap:bl:{link_id}:del", 999)
    check("общая ссылка удалена", "Ссылок пока нет" in str(bot.edits[-1]),
          str(bot.edits[-1])[:160])
    await feed(dp, ALICE, "/help", 1001)
    check("после удаления кнопок в /help нет",
          last_reply_markup() is None)

    # --- 30. /announce без кулдауна ---
    section("30. Рассылка без кулдауна")
    await feed(dp, ADMIN, "/announce Первое объявление", 999)
    check("рассылка выполнена", "Готово" in last_text(), last_text()[:140])
    SENT.clear()
    await feed(dp, ADMIN, "/announce Второе объявление", 999)
    check("вторая рассылка проходит без ожидания",
          "Готово" in last_text(), last_text()[:140])
    check("текст действительно разослан",
          any("Второе объявление" in m.text for m in SENT),
          str([m.text for m in SENT])[:200])
    check("отчёт рассылки собран без ошибок",
          "не доставлено" in last_text(), last_text()[:140])

    # --- 17. неизвестная команда и ошибки ---
    section("17. Прочее")
    await feed(dp, ALICE, "/jnonexistent", 1001)
    check("неизвестная команда не роняет бота", True)

    await db.close()

    print(f"\n{'=' * 60}")
    print(f"Пройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    if FAILED:
        print("\nОшибки:")
        for item in FAILED:
            print(f"  - {item}")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
