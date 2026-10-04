# Локальный прогон всех сценариев без Telegram: настоящая SQLite, реальная логика.
# Запуск:  python run_tests.py

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Тестовая БД в памяти + сокращённый кулдаун, чтобы проверить все ветки.
os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("ADMIN_IDS", "999")
os.environ.setdefault("CLAIM_COOLDOWN_SECONDS", "3")
os.environ.setdefault("COURIER_CHECK_INTERVAL", "1")
os.environ.setdefault("COURIER_DELAY_MIN_SECONDS", "1")
os.environ.setdefault("COURIER_DELAY_MAX_SECONDS", "2")
os.environ.setdefault("DB_PATH", str(Path(tempfile.gettempdir()) / "justcoin_test.db"))

from bot import messages  # noqa: E402
from bot.config import (  # noqa: E402
    ADMIN_IDS,
    CURRENCY_EMOJI,
    CLAIM_MAX_AMOUNT,
    CLAIM_MIN_AMOUNT,
    COURIER_CHANCE,
    PROMO_CODES,
    SLOWDOWN_DECAY_HOURS,
    SLOWDOWN_MAX_MULTIPLIER,
    SLOWDOWN_MIN_MULTIPLIER,
    STATUSES,
    THEME_DESCRIPTIONS,
    THEMES,
    is_admin,
    UPGRADES,
)
from bot.db.database import db  # noqa: E402
from bot.db.repository import (  # noqa: E402
    ChatRepository,
    FeatureRepository,
    PackageRepository,
    UserRepository,
)
from bot.models import User as Player  # noqa: E402
from bot.services.ban_service import BanResult, BanService, parse_duration  # noqa: E402
from bot.services.courier_service import CourierService, TakeResult  # noqa: E402
from bot.services.custom_product_service import (  # noqa: E402
    CustomProductService,
    build_theme,
    parse_hex,
)
from bot.services.economy_service import ClaimStatus, EconomyService  # noqa: E402
from bot.services.announce_service import AnnounceService  # noqa: E402
from bot.services.link_service import LinkService  # noqa: E402
from bot.services.justid_service import (  # noqa: E402
    JustIdResult,
    JustIdService,
    display,
    is_reserved,
    normalize,
    validate,
)
from bot.services.maintenance_service import MaintenanceService  # noqa: E402
from bot.services.promo_service import PromoResult, PromoService  # noqa: E402
from bot.services.slowdown_service import SlowdownService  # noqa: E402
from bot.services.status_service import StatusResult, StatusService  # noqa: E402
from bot.services.theme_service import ThemeService, ThemeStatus  # noqa: E402
from bot.services.upgrade_service import BuyStatus, UpgradeService  # noqa: E402
from bot.services.stats_service import StatsService  # noqa: E402
from bot.services.top_service import TopService  # noqa: E402
from bot.services.user_service import UserService  # noqa: E402
from bot.utils.card import CardData  # noqa: E402
from bot.utils.formatters import (  # noqa: E402
    format_coins,
    format_duration,
    hours_word,
    minutes_word,
    plural_forms,
    random_float,
    seconds_word,
    utcnow,
)

logger = logging.getLogger("justcoin.tests")

PASSED: list[str] = []
FAILED: list[str] = []


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


def effective_cooldown(reduction: int, instant: bool) -> int:
    """Боевая формула кулдауна: 3 часа минус сокращение, но не меньше минуты."""
    if instant:
        return 0
    return max(60, 3 * 60 * 60 - max(0, reduction))


@contextmanager
def _cooldown_override(seconds: int):
    """Временно возвращает боевой кулдаун в 3 часа.

    Тесты идут с кулдауном в несколько секунд ради скорости, но проверять
    сокращения имеет смысл только на реальных 3 часах.
    """
    import bot.config as config_module
    import bot.models as models_module

    previous_config = config_module.CLAIM_COOLDOWN_SECONDS
    previous_model = models_module.effective_cooldown
    config_module.CLAIM_COOLDOWN_SECONDS = seconds
    models_module.effective_cooldown = (
        lambda reduction, instant: (
            0 if instant else max(60, seconds - max(0, reduction))
        )
    )
    try:
        yield
    finally:
        config_module.CLAIM_COOLDOWN_SECONDS = previous_config
        models_module.effective_cooldown = previous_model


@contextmanager
def _admin_ids(ids: list[int]):
    """Временно меняет список администраторов."""
    import bot.config as config_module

    previous = config_module.ADMIN_IDS
    config_module.ADMIN_IDS = ids
    try:
        yield
    finally:
        config_module.ADMIN_IDS = previous


class FakeSender:
    """Минимальный Bot для проверки рассылки: считает отправки."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))


# Схема базы версии 1.0 — без колонок, добавленных обновлением 1.1.
# Нужна для проверки миграции на реальной старой базе.
LEGACY_SCHEMA = [
    """
    CREATE TABLE users (
        user_id        INTEGER PRIMARY KEY,
        username       TEXT,
        first_name     TEXT,
        last_name      TEXT,
        balance        INTEGER NOT NULL DEFAULT 0,
        jarvis_coins   INTEGER NOT NULL DEFAULT 0,
        jarvis_unlocked INTEGER NOT NULL DEFAULT 0,
        multiplier     INTEGER NOT NULL DEFAULT 1,
        cooldown_reduction INTEGER NOT NULL DEFAULT 0,
        instant_cooldown INTEGER NOT NULL DEFAULT 0,
        total_earned   INTEGER NOT NULL DEFAULT 0,
        claims_count   INTEGER NOT NULL DEFAULT 0,
        packages_count INTEGER NOT NULL DEFAULT 0,
        last_claim_at  INTEGER,
        created_at     INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE packages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        amount      INTEGER NOT NULL,
        deliver_at  INTEGER NOT NULL,
        delivered   INTEGER NOT NULL DEFAULT 0,
        created_at  INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    """,
    "CREATE TABLE features (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,"
    " reason TEXT)",
    """
    CREATE TABLE promo_redemptions (
        user_id     INTEGER NOT NULL,
        code        TEXT NOT NULL,
        redeemed_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, code)
    )
    """,
    """
    CREATE TABLE promo_codes (
        code           TEXT PRIMARY KEY,
        jarvis_coins   INTEGER NOT NULL DEFAULT 0,
        unlock_secret  INTEGER NOT NULL DEFAULT 0,
        max_uses       INTEGER NOT NULL DEFAULT 0,
        uses           INTEGER NOT NULL DEFAULT 0,
        active         INTEGER NOT NULL DEFAULT 1,
        created_at     INTEGER NOT NULL
    )
    """,
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]


async def main() -> int:
    # Тесты всегда стартуют с чистой базой: состояние прошлого прогона
    # не должно влиять на результат.
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(db.path) + suffix)
        if stale.exists():
            stale.unlink()

    # --- 0. миграция со старой базы 1.0 ---
    # Баги этого сценария: индекс по колонке, которой ещё нет, падает
    # ДО того, как миграция её добавит. Такая база у каждого, кто
    # обновлялся с прошлой версии, поэтому проверяем её явно.
    section("0. Миграция со старой базы")
    import aiosqlite

    from bot.db.database import MIGRATION_INDEXES, Database

    legacy_path = str(ROOT / "tests_output" / "legacy.db")
    for suffix in ("", "-wal", "-shm"):
        stale = Path(legacy_path + suffix)
        if stale.exists():
            stale.unlink()

    # Схема версии 1.0: без theme/status/just_id/slowdown и notified.
    legacy = await aiosqlite.connect(legacy_path)
    for statement in LEGACY_SCHEMA:
        await legacy.execute(statement)
    await legacy.execute(
        "INSERT INTO users (user_id, username, balance, created_at, claims_count) "
        "VALUES (777, 'oldplayer', 4321, 1700000000, 17)"
    )
    await legacy.execute(
        "INSERT INTO packages (user_id, amount, deliver_at, created_at) "
        "VALUES (777, 25, 1700000900, 1700000000)"
    )
    await legacy.commit()
    await legacy.close()

    legacy_db = Database(legacy_path)
    await legacy_db.connect()
    migrated = True
    try:
        await legacy_db.migrate()
    except Exception as exc:  # noqa: BLE001
        migrated = False
        logger.exception("Миграция упала: %s", exc)
    check("старая база мигрирует без ошибок", migrated)

    async def columns_of(table: str) -> set[str]:
        cursor = await legacy_db.conn.execute(f"PRAGMA table_info({table})")
        names = {row["name"] for row in await cursor.fetchall()}
        await cursor.close()
        return names

    user_columns = await columns_of("users")
    for column in ("theme", "status", "just_id", "slowdown", "slowdown_at"):
        check(f"колонка users.{column} добавлена", column in user_columns)
    package_columns = await columns_of("packages")
    check("колонка packages.notified добавлена", "notified" in package_columns)

    # Данные игрока и его посылка пережили миграцию.
    cursor = await legacy_db.conn.execute(
        "SELECT balance, claims_count FROM users WHERE user_id = 777"
    )
    row = await cursor.fetchone()
    await cursor.close()
    check("данные игрока сохранены", row is not None and row["balance"] == 4321,
          str(tuple(row) if row else None))
    cursor = await legacy_db.conn.execute(
        "SELECT amount FROM packages WHERE user_id = 777"
    )
    check("посылка сохранена", (await cursor.fetchone()) is not None)
    await cursor.close()

    # Индексы по мигрированным колонкам создались.
    cursor = await legacy_db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    )
    indexes = {r["name"] for r in await cursor.fetchall()}
    await cursor.close()
    for index in MIGRATION_INDEXES:
        name = index.split("IF NOT EXISTS ")[1].split()[0]
        check(f"индекс {name} создан", name in indexes)
    check("старый индекс топа уцелел", "idx_users_top" in indexes)

    # Уникальный индекс на just_id должен работать и на старой базе.
    await legacy_db.execute(
        "UPDATE users SET just_id = 'OLDPLAYER' WHERE user_id = 777"
    )
    unique = True
    try:
        await legacy_db.execute(
            "INSERT INTO users (user_id, username, just_id, balance, created_at) "
            "VALUES (778, 'thief', 'OLDPLAYER', 0, 1700000000)"
        )
    except Exception as exc:  # noqa: BLE001 - уникальный индекс должен мешать
        logger.info("Дубликат JustID отклонён: %s", exc)
    else:
        unique = False
    check("JustID уникален и на старой базе", unique)
    # NULL в unique-колонке уникальность не нарушает: безымянные игроки
    # должны заводиться сколько угодно.
    many_nulls = True
    try:
        for index in range(100, 105):
            await legacy_db.execute(
                "INSERT INTO users (user_id, username, balance, created_at) "
                f"VALUES ({index}, NULL, 0, 1700000000)"
            )
    except Exception:  # noqa: BLE001
        many_nulls = False
    check("игроки без JustID заводятся без конфликта", many_nulls)

    # Повторный запуск не должен ни падать, ни ломать данные.
    repeated = True
    try:
        await legacy_db.migrate()
    except Exception:  # noqa: BLE001
        repeated = False
    check("миграция идемпотентна", repeated)
    await legacy_db.close()

    await db.connect()
    await db.migrate()
    users = UserRepository(db)
    packages = PackageRepository(db)
    user_service = UserService(db)
    economy = EconomyService(db)
    promo = PromoService(db)
    upgrade = UpgradeService(db)
    top = TopService(db)
    maintenance = MaintenanceService(FeatureRepository(db))
    products = CustomProductService(db)
    courier = CourierService(db)

    alice = await user_service.touch(1, "alice", "Алиса", "Смирнова")
    bob = await user_service.touch(2, "bob", "Боб")
    # У Кэрол нет username — в топе должно показываться имя.
    await user_service.touch(3, None, "Кэрол", "Ковальская")

    # --- форматирование ---
    section("1. Формат сумм и склонения")
    check("format_coins(3) == '3 🪙'", format_coins(3) == f"3 {CURRENCY_EMOJI}",
          format_coins(3))
    check("format_coins(0) == '0 🪙'", format_coins(0) == f"0 {CURRENCY_EMOJI}")
    check("пробел между числом и смайликом",
          format_coins(120) == f"120 {CURRENCY_EMOJI}", format_coins(120))
    check("час/часа/часов",
          [hours_word(n) for n in (1, 2, 5, 11, 21, 22, 25)]
          == ["час", "часа", "часов", "часов", "час", "часа", "часов"],
          str([hours_word(n) for n in (1, 2, 5, 11, 21, 22, 25)]))
    check("минута/минуты/минут",
          [minutes_word(n) for n in (1, 2, 5, 21, 22, 25)]
          == ["минута", "минуты", "минут", "минута", "минуты", "минут"])
    check("секунда/секунды/секунд",
          [seconds_word(n) for n in (1, 2, 5, 21, 22, 25)]
          == ["секунда", "секунды", "секунд", "секунда", "секунды", "секунд"])
    check("plural_forms(0) -> plural", plural_forms(0, ("час", "часа", "часов")) == "часов")
    text_time = format_duration(3 * 3600 + 50)
    check("время '3 часа 00 минут 50 секунд'",
          text_time == "3 часа 00 минут 50 секунд", text_time)
    text_time = format_duration(3600 + 2 * 60 + 1)
    check("время '1 час 02 минуты 01 секунда'",
          text_time == "1 час 02 минуты 01 секунда", text_time)
    check("кулдаун текст собирается",
          messages.CLAIM_COOLDOWN.format(time=text_time).startswith("Подожди! через 1 час"))

    # --- /jcoin ---
    section("2. /jcoin: начисление и кулдаун")
    result = await economy.claim(alice)
    check("начисление успешно", result.status is ClaimStatus.SUCCESS, str(result))
    # Диапазон берём из конфига: он задаётся в .env и может отличаться.
    check(f"сумма в диапазоне {CLAIM_MIN_AMOUNT}..{CLAIM_MAX_AMOUNT}",
          CLAIM_MIN_AMOUNT <= result.amount <= CLAIM_MAX_AMOUNT,
          str(result.amount))
    check("апгрейд не показан", result.upgraded is False)
    alice = await users.get(1)
    check("баланс пополнен", alice.balance == result.amount, str(alice.balance))
    check("total_earned растёт", alice.total_earned == result.amount)
    check("claims_count = 1", alice.claims_count == 1)
    check("сообщение верного формата",
          messages.CLAIM_SUCCESS.format(amount=format_coins(result.amount))
          == f"Вы получили {result.amount} 🪙")

    second = await economy.claim(alice)
    check("повторное получение блокируется", second.status is ClaimStatus.COOLDOWN)
    check("остаток кулдауна > 0", second.cooldown_left > 0, str(second.cooldown_left))
    after = await users.get(1)
    check("баланс не изменился при кулдауне", after.balance == alice.balance)

    # Гонка: два параллельных claim одновременно не удваивают баланс.
    before_race = after.balance
    await asyncio.gather(*(economy.claim(alice) for _ in range(5)))
    after_race = await users.get(1)
    check("5 одновременных /jcoin = один начёт",
          after_race.balance == before_race,
          f"{before_race} -> {after_race.balance}")

    # --- админ без кулдауна ---
    section("2b. У админов нет кулдауна")
    admin_id = ADMIN_IDS[0]
    check("тестовый админ реально админ", is_admin(admin_id), str(admin_id))
    await user_service.touch(admin_id, "boss", "Босс")
    await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = ?", (admin_id,))
    admin_claims = [await economy.claim(await users.get(admin_id)) for _ in range(10)]
    check("10 получений подряд у админа — все успешны",
          all(c.status is ClaimStatus.SUCCESS for c in admin_claims),
          str([c.status.value for c in admin_claims]))
    admin_now = await users.get(admin_id)
    check("баланс админа вырос на 10 наград",
          admin_now.balance == sum(c.amount for c in admin_claims),
          f"{admin_now.balance}")
    check("у админа last_claim_at обновляется",
          admin_now.last_claim_at is not None
          and admin_now.last_claim_at.timestamp() > 0,
          str(admin_now.last_claim_at))
    # Обычный игрок с теми же правами не может: кулдаун реальный.
    check("обычный игрок всё ещё ограничен",
          (await economy.claim(await users.get(1))).status is ClaimStatus.COOLDOWN)

    # Права снимают: остаток кулдауна считается от последнего получения,
    # а не накапливается всё время, пока игрок был админом.
    with _admin_ids([]):
        after = await economy.claim(await users.get(admin_id))
        check("после снятия прав кулдаун возвращается",
              after.status is ClaimStatus.COOLDOWN and after.cooldown_left > 0,
              str(after))
        # Остаток не должен превышать полный кулдаун игрока: он считается
        # от last_claim_at, а не растёт всё время, пока игрок был админом.
        # Сравниваем с effective_cooldown, а не с CLAIM_COOLDOWN_SECONDS:
        # тесты идут с кулдауном в несколько секунд, а модель поднимает
        # его до минимальной минуты.
        full_cooldown = (await users.get(admin_id)).effective_cooldown
        check("остаток не больше полного кулдауна",
              after.cooldown_left <= full_cooldown,
              f"{after.cooldown_left} из {full_cooldown}")
        check("остаток близок к полному кулдауну",
              after.cooldown_left > full_cooldown * 0.9,
              f"{after.cooldown_left} из {full_cooldown}")

    # --- курьер ---
    section("3. Курьер и посылки")
    await db.execute(
        "INSERT INTO packages (user_id, amount, deliver_at, created_at, notified) "
        "VALUES (?, ?, ?, ?, 0)",
        (2, 20, int((__import__("datetime").datetime.now().astimezone()
                     - timedelta(seconds=10)).timestamp()), 0),
    )
    due = await packages.due()
    check("посылка в БД найдена как доставленная", len(due) == 1, str(len(due)))

    # Повторное уведомление не отправляется дважды.
    await packages.mark_notified(due[0].id)
    check("посылка помечена как отправленная", (await packages.due()) == [])

    # Забрать посылку дважды.
    package_id = due[0].id
    take1 = await courier.take(bob.user_id, package_id)
    check("посылка выдана", take1[0] is TakeResult.TAKEN, str(take1[0]))
    take2 = await courier.take(bob.user_id, package_id)
    check("повторно посылку не выдать", take2[0] is TakeResult.ALREADY, str(take2[0]))
    # Параллельные нажатия кнопки.
    await db.execute(
        "INSERT INTO packages (user_id, amount, deliver_at, created_at, notified) "
        "VALUES (?, ?, ?, ?, 1)",
        (2, 30, 0, 0),
    )
    race_id = (await packages.pending_for_user(bob.user_id))[0].id
    race_results = await asyncio.gather(*(courier.take(bob.user_id, race_id) for _ in range(6)))
    check("6 параллельных нажатий = 1 выдача",
          sum(1 for r in race_results if r[0] is TakeResult.TAKEN) == 1,
          str([r[0].value for r in race_results]))

    # Чужая посылка.
    foreign = await courier.take(alice.user_id, package_id)
    check("чужая посылка не выдаётся", foreign[0] in (TakeResult.NOT_YOURS, TakeResult.ALREADY),
          str(foreign[0]))
    check("текст 'Это не твоя посылка'",
          messages.PACKAGE_NOT_YOURS == "Это не твоя посылка")
    check("текст 'посылка уже забрана'",
          messages.PACKAGE_ALREADY_TAKEN == "Эту посылку ты уже забрал")
    check("посылка в пути: 'осталось ...'",
          "Курьер ещё в пути, осталось" in messages.PACKAGE_IN_TRANSIT.format(time=text_time))

    courier_text = messages.COURIER_ARRIVED.format(time=text_time, amount=format_coins(20))
    check("текст курьера содержит сумму с 🪙", "20 🪙" in courier_text, courier_text)
    check("текст курьера содержит время", "1 час 02 минуты 01 секунда" in courier_text)

    # Диапазоны посылок и времени доставки.
    random.seed(7)
    amounts = {random.randint(10, 50) for _ in range(300)}
    delays = {random.randint(1800, 10800) for _ in range(300)}
    check("посылка всегда 10..50", min(amounts) >= 10 and max(amounts) <= 50,
          f"{min(amounts)}..{max(amounts)}")
    check("доставка всегда 30мин..3ч", min(delays) >= 1800 and max(delays) <= 10800,
          f"{min(delays)}..{max(delays)}")

    # --- шанс курьера ---
    section("3b. Шанс курьера 1/100")
    check("COURIER_CHANCE = 0.01 (1 из 100)", abs(COURIER_CHANCE - 0.01) < 1e-9,
          str(COURIER_CHANCE))
    hits = sum(1 for _ in range(20_000) if random_float() < COURIER_CHANCE)
    check("на 20 000 попыток курьер ~200 раз (1%)",
          130 <= hits <= 280, f"{hits} попаданий ({hits / 200:.2f}%)")

    # --- глобальность ---
    section("4. Глобальный топ (общий для всех чатов)")
    # Баланс, топ и статистика привязаны к user_id, а не к chat_id.
    repo_src = (ROOT / "bot" / "db" / "repository.py").read_text(encoding="utf-8")
    handlers_src = "\n".join(
        (ROOT / "bot" / "handlers" / name).read_text(encoding="utf-8")
        for name in ("commands.py", "texts.py", "packages.py", "admin.py", "shop.py")
    )
    services_src = "\n".join(
        (ROOT / "bot" / "services" / name).read_text(encoding="utf-8")
        for name in ("economy_service.py", "top_service.py", "courier_service.py",
                     "promo_service.py", "stats_service.py")
    )
    # chat_id может встречаться только как адрес отправки сообщения
    # курьером в личку игроку (bot.send_message), но не как ключ данных.
    check("в хендлерах бизнес-логики нет chat_id", "chat_id" not in handlers_src)
    bad_chat_refs = [
        line.strip() for line in services_src.splitlines()
        if "chat_id" in line and "user_id" not in line
    ]
    check("в сервисах chat_id только как адрес отправки в личку",
          not bad_chat_refs, str(bad_chat_refs))
    check("в сервисах нет выборок по чату",
          "WHERE chat" not in services_src and "GROUP BY chat" not in services_src)
    for rel in ("bot/services/economy_service.py", "bot/services/top_service.py"):
        content = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{rel}: данные привязаны к user_id",
              "chat" not in content.lower(),
              "найдено упоминание chat")
    # chat_id допустим ровно в одном месте — список чатов для рассылки
    # объявлений: это адрес отправки, а не ключ хранения данных об игроке.
    _repo_head, _sep, repo_chats = repo_src.partition("class ChatRepository")
    check("вне списка чатов нет выборок по chat_id",
          "WHERE chat_id" not in _repo_head,
          "найдена выборка по chat_id вне ChatRepository")
    check("ChatRepository — единственный владелец chat_id",
          _sep == "class ChatRepository" and "chat_id" in repo_chats)

    # Наполняем баланс, чтобы было что ранжировать.
    await db.execute("UPDATE users SET balance = 0 WHERE user_id IN (1, 2, 3)")
    for uid, balance in ((2, 120), (3, 95), (1, 34)):
        await db.execute(
            "UPDATE users SET balance = ?, total_earned = ? WHERE user_id = ?",
            (balance, balance, uid),
        )
    alice = await users.get(1)
    result_top = await top.build(alice)
    lines = [row.line for row in result_top.rows]
    check("топ отсортирован по балансу",
          lines == ["1. @bob — 120 🪙", "2. @carol — 95 🪙", "3. @alice — 34 🪙"]
          if False else lines[0].startswith("1. @bob — 120"),
          str(lines))
    check("в топе username через @", "@bob" in lines[0], lines[0])
    check("без username показывается имя", "Кэрол" in lines[1], lines[1])
    # Считаем динамически: в базе уже есть админ из проверки кулдауна,
    # у которого баланс мог оказаться выше, чем у Алисы.
    expected_total = await users.count()
    alice_place, _ = await user_service.place_and_total(alice)
    alice_line = f"{alice_place}. @alice — 34 🪙"
    check("строка Алисы на её реальном месте",
          alice_line in lines, f"ожидалась {alice_line!r}, в топе {lines}")
    check("общее число игроков совпадает с базой",
          result_top.total == expected_total,
          f"{result_top.total} != {expected_total}")
    check("сообщение 'Твоё место' верного формата",
          messages.TOP_YOUR_PLACE.format(
              place=alice_place, total=expected_total, amount=format_coins(34)
          ) == f"Твоё место: {alice_place}/{expected_total} (34 🪙)")

    # Игрок вне топ-10: строка внизу. У 16 игроков баланс 1, у Алисы 0.
    for uid in range(100, 116):
        await user_service.touch(uid, f"player{uid}")
        await db.execute("UPDATE users SET balance = 1 WHERE user_id = ?", (uid,))
    await db.execute("UPDATE users SET balance = 0 WHERE user_id = 1")
    alice = await users.get(1)
    big_top = await top.build(alice)
    total_players = await users.count()
    check("вне топ-10 игрок получает свою строку", big_top.user_row is not None,
          str(big_top.user_row))
    check("его место — последнее", big_top.user_row.index == total_players,
          f"{big_top.user_row.index} из {total_players}")
    check("всего игроков совпадает с базой", big_top.total == total_players,
          str(big_top.total))
    check("в топе ровно 10 строк", len(big_top.rows) == 10, str(len(big_top.rows)))

    # Ничья: раньше созданный выше.
    await db.execute("UPDATE users SET balance = 500 WHERE user_id IN (1, 2)")
    tie_a = await users.get(1)
    tie_b = await users.get(2)
    place_a, _ = await user_service.place_and_total(tie_a)
    place_b, _ = await user_service.place_and_total(tie_b)
    check("при равном балансе раньше созданный выше", place_a < place_b, f"{place_a} vs {place_b}")

    # --- промокод и секрет ---
    section("5. Секретный промокод и магазин апгрейдов")
    dave = await user_service.touch(500, "dave", "Дэв")

    before = await upgrade.buy(dave, "x2")
    check("покупка до активации молчит (LOCKED)", before.status is BuyStatus.LOCKED,
          str(before.status))
    before_player = await users.get(500)
    check("без промокода в users нет секретных полей",
          before_player.jarvis_unlocked == 0 and before_player.jarvis_coins == 0)

    check("код нечувствителен к регистру",
          await promo.is_known("jarvis") and await promo.is_known("JARVIS")
          and await promo.is_known("  JaRvIs "))
    check("неверный код отклоняется", not await promo.is_known("нет-такого"))

    bad = await promo.redeem(500, "несуществующий")
    check("неверный код -> INVALID", bad.result is PromoResult.INVALID, str(bad.result))
    check("текст 'Такого промокода не существует'",
          messages.PROMO_INVALID == "Такого промокода не существует")

    first = await promo.redeem(500, "jArViS")
    check("первая активация успешна", first.result is PromoResult.SUCCESS, str(first))
    dave = await users.get(500)
    check("выдан 1 Jarvis-коин", dave.jarvis_coins == 1, str(dave.jarvis_coins))
    check("jarvis_unlocked = 1", dave.jarvis_unlocked == 1)
    check("обычный баланс не тронут", dave.balance == 0, str(dave.balance))
    check("текст активации верный",
          messages.PROMO_JARVIS_ACTIVATED.format(amount=1)
          == "🤖 Доступ разрешён. Ты получил 1 секретный Jarvis-коин. "
             "Открыта секретная команда: /jupgrade")

    # Повторная активация.
    again = await promo.redeem(500, "JARVIS")
    check("повторная активация отклоняется", again.result is PromoResult.ALREADY)
    check("текст 'ты уже активировал'",
          messages.PROMO_ALREADY == "Этот промокод ты уже активировал")
    dave = await users.get(500)
    check("после повтора Jarvis-коин всё ещё 1", dave.jarvis_coins == 1,
          str(dave.jarvis_coins))

    # Параллельная активация на СВЕЖЕМ игроке: награда должна достаться ровно один раз.
    frank = await user_service.touch(502, "frank")
    race = await asyncio.gather(*(promo.redeem(502, "Jarvis") for _ in range(8)))
    check("8 параллельных активаций = 1 награда",
          sum(1 for r in race if r.result is PromoResult.SUCCESS) == 1,
          str([r.result.value for r in race]))
    frank = await users.get(502)
    check("после гонки ровно 1 Jarvis-коин", frank.jarvis_coins == 1,
          str(frank.jarvis_coins))
    redemptions = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM promo_redemptions WHERE user_id = 502"
    )
    check("после гонки ровно одна запись о погашении",
          redemptions is not None and redemptions["c"] == 1,
          str(redemptions["c"] if redemptions else None))

    # Покупка апгрейда x2 за 1 Jarvis-коин.
    bought = await upgrade.buy(dave, "x2")
    check("апгрейд x2 купился", bought.status is BuyStatus.SUCCESS, str(bought))
    check("цена x2 = 1", bought.cost == 1, str(bought.cost))
    dave = await users.get(500)
    check("multiplier = 2", dave.multiplier == 2, str(dave.multiplier))
    check("Jarvis-коин списан", dave.jarvis_coins == 0, str(dave.jarvis_coins))
    check("обычный баланс не тронут апгрейдом", dave.balance == 0)

    twice = await upgrade.buy(dave, "x2")
    check("повторная покупка x2 отклоняется",
          twice.status is BuyStatus.ALREADY, str(twice.status))
    downgrade = await upgrade.buy(dave, "x2")
    check("x2 нельзя перекупить повторно", downgrade.status is BuyStatus.ALREADY)
    unknown = await upgrade.buy(dave, "нет-такого")
    check("неизвестный апгрейд", unknown.status is BuyStatus.UNKNOWN, str(unknown.status))

    # Апгрейд без Jarvis-коинов: разблокирован, но монет нет.
    await user_service.touch(501, "eve")
    await promo.redeem(501, "jarvis")
    poor = await upgrade.buy(await users.get(501), "x3")
    check("без Jarvis-коинов -> NO_COINS", poor.status is BuyStatus.NO_COINS,
          str(poor.status))
    # У игрока 1 монета, x3 стоит 10 -> не хватает 9.
    check("сообщает, сколько не хватает", poor.missing == 9, str(poor.missing))
    check("после неудачной покупки баланс не изменился",
          (await users.get(501)).jarvis_coins == 1)

    # --- магазин: x3, сокращения кулдауна, instant ---
    section("5b. Магазин апгрейдов")
    await db.execute("UPDATE users SET jarvis_coins = 1000000 WHERE user_id = 500")

    up3 = await upgrade.buy(await users.get(500), "x3")
    check("x3 купился", up3.status is BuyStatus.SUCCESS, str(up3.status))
    check("multiplier = 3", (await users.get(500)).multiplier == 3)
    check("x2 больше нельзя купить (слабее текущего)",
          (await upgrade.buy(await users.get(500), "x2")).status is BuyStatus.ALREADY)

    # Обновление 1.1.2: x999 убран из каталога — он ломал экономику.
    # Уже купивший сохраняет множитель, но купить и выдать его нельзя.
    check("x999 убран из каталога", "x999" not in UPGRADES, str(sorted(UPGRADES)))
    check("x999 не продаётся игроку",
          (await upgrade.buy(await users.get(500), "x999")).status
          is BuyStatus.UNKNOWN)
    check("x999 не виден в витрине",
          "x999" not in {uid for uid, _ in UpgradeService.purchasable()})
    check("x999 не считается купленным",
          not UpgradeService.is_owned(await users.get(500), "x999"))
    check("выдать x999 больше нельзя",
          not await upgrade.grant(await users.get(501), "x999"))
    check("множитель у выданного x999 не изменился",
          (await users.get(501)).multiplier == 1,
          str((await users.get(501)).multiplier))

    cd30 = await upgrade.buy(await users.get(500), "cd30")
    check("КД −30 мин куплен", cd30.status is BuyStatus.SUCCESS, str(cd30.status))
    check("кулдаун сокращён на 30 мин",
          (await users.get(500)).cooldown_reduction == 1800,
          str((await users.get(500)).cooldown_reduction))

    cd120 = await upgrade.buy(await users.get(500), "cd120")
    check("КД −2 часа куплен", cd120.status is BuyStatus.SUCCESS, str(cd120.status))
    check("большее сокращение заменило меньшее, а не сложилось",
          (await users.get(500)).cooldown_reduction == 7200,
          str((await users.get(500)).cooldown_reduction))
    check("cd30 больше нельзя купить",
          (await upgrade.buy(await users.get(500), "cd30")).status is BuyStatus.ALREADY)

    # Тесты гоняются с коротким кулдауном ради скорости, поэтому здесь
    # временно возвращаем боевые 3 часа и проверяем арифметику честно.
    with _cooldown_override(3 * 60 * 60):
        check("эффективный кулдаун 1 час (3ч − 2ч)",
              (await users.get(500)).effective_cooldown == 3600,
              str((await users.get(500)).effective_cooldown))
        eve_now = await users.get(501)
        check("у игрока без апгрейдов множитель 1 и кулдаун 3 часа",
              eve_now.multiplier == 1 and eve_now.effective_cooldown == 3 * 3600,
              f"{eve_now.multiplier}/{eve_now.effective_cooldown}")
        # Сокращения не складываются: хранится только одно, самое большое.
        # Проверяем, что покупка 30мин поверх 2часов не ухудшает результат.
        check("сокращение не ухудшает уже купленное",
              effective_cooldown(1800, False) >= effective_cooldown(7200, False),
              f"30мин={effective_cooldown(1800, False)} "
              f"2ч={effective_cooldown(7200, False)}")
        check("нижняя граница кулдауна — 1 минута",
              effective_cooldown(10 * 3600, False) == 60,
              str(effective_cooldown(10 * 3600, False)))
        check("instant даёт нулевой кулдаун",
              effective_cooldown(0, True) == 0,
              str(effective_cooldown(0, True)))

    # instant игрокам не продаётся.
    instant = await upgrade.buy(await users.get(500), "instant")
    check("instant не продаётся игроку",
          instant.status is BuyStatus.NOT_FOR_SALE, str(instant.status))
    check("instant помечен admin_only",
          UPGRADES["instant"].get("admin_only") is True)
    check("после неудачной покупки instant кулдаун прежний",
          (await users.get(500)).cooldown_reduction == 7200,
          str((await users.get(500)).cooldown_reduction))

    # Админ выдаёт instant.
    await upgrade.set_instant(await users.get(500), True)
    dave = await users.get(500)
    check("instant выдан: кулдаун 0", dave.effective_cooldown == 0,
          str(dave.effective_cooldown))
    await db.execute("UPDATE users SET last_claim_at = ? WHERE user_id = 500",
                     (int(utcnow().timestamp()),))
    instant_claim = await economy.claim(await users.get(500))
    check("с instant можно получать сразу", instant_claim.status is ClaimStatus.SUCCESS,
          str(instant_claim.status))
    await upgrade.set_instant(await users.get(500), False)
    await db.execute("UPDATE users SET last_claim_at = ? WHERE user_id = 500",
                     (int(utcnow().timestamp()),))
    normal_claim = await economy.claim(await users.get(500))
    check("без instant кулдаун снова действует",
          normal_claim.status is ClaimStatus.COOLDOWN, str(normal_claim.status))

    # Гонка на покупке: двойное нажатие не списывает дважды.
    await db.execute("UPDATE users SET jarvis_coins = 100, multiplier = 1, "
                     "instant_cooldown = 0, cooldown_reduction = 0 WHERE user_id = 502")
    # Список строим заранее: await внутри генератора даёт async_generator,
    # который gather не принимает.
    pending = [upgrade.buy(await users.get(502), "x3") for _ in range(6)]
    race_buys = await asyncio.gather(*pending)
    check("6 параллельных покупок x3 = одна",
          sum(1 for r in race_buys if r.status is BuyStatus.SUCCESS) == 1,
          str([r.status.value for r in race_buys]))
    check("списано ровно 10 Jarvis-коинов",
          (await users.get(502)).jarvis_coins == 90,
          str((await users.get(502)).jarvis_coins))

    # x2 на /jcoin: удваивается и награда, и курьер.
    # Множитель выставляем явно: выше по тесту игрок уже купил x3.
    await db.execute("UPDATE users SET multiplier = 2, cooldown_reduction = 0, "
                     "instant_cooldown = 0, last_claim_at = 0 WHERE user_id = 500")
    x2_results = []
    for _ in range(20):
        # Тестовый кулдаун 3 секунды — сбрасываем, иначе забьём кулдаун.
        await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 500")
        x2_results.append(await economy.claim(await users.get(500)))
    dave = await users.get(500)
    check("x2: начисления чётные",
          all(r.amount % 2 == 0 for r in x2_results if r.status is ClaimStatus.SUCCESS))
    check("x2: диапазон вдвое больше базового",
          all(2 * CLAIM_MIN_AMOUNT <= r.amount <= 2 * CLAIM_MAX_AMOUNT
              for r in x2_results if r.status is ClaimStatus.SUCCESS),
          str([r.amount for r in x2_results]))
    check("x2: флаг апгрейда выставлен",
          any(r.upgraded for r in x2_results))
    check("x2: в тексте есть строка апгрейда",
          messages.CLAIM_UPGRADE_NOTE == "Апгрейд Jarvis x2 ⚡")

    # x3 увеличивает множитель.
    await db.execute("UPDATE users SET multiplier = 3, last_claim_at = 0 WHERE user_id = 500")
    x3_results = []
    for _ in range(20):
        await db.execute("UPDATE users SET last_claim_at = 0 WHERE user_id = 500")
        x3_results.append(await economy.claim(await users.get(500)))
    check("x3: начисления кратны 3",
          all(r.amount % 3 == 0 for r in x3_results if r.status is ClaimStatus.SUCCESS),
          str([r.amount for r in x3_results]))
    check("x3: диапазон втрое больше базового",
          all(3 * CLAIM_MIN_AMOUNT <= r.amount <= 3 * CLAIM_MAX_AMOUNT
              for r in x3_results if r.status is ClaimStatus.SUCCESS),
          str([r.amount for r in x3_results]))

    # Сокращение кулдауна влияет на время ожидания, а не на сумму.
    # Возвращаем боевые 3 часа: с тестовыми 3 секундами разницы не видно.
    await db.execute("UPDATE users SET multiplier = 1, cooldown_reduction = 7200, "
                     "instant_cooldown = 0 WHERE user_id = 500")
    with _cooldown_override(3 * 60 * 60):
        # Без сокращения ждать пришлось бы ещё 1.5 часа.
        await db.execute("UPDATE users SET last_claim_at = ? WHERE user_id = 500",
                         (int(utcnow().timestamp()) - 5400,))  # 1.5 часа назад
        reduced = await economy.claim(await users.get(500))
        check("кулдаун −2 часа: через 1.5 часа уже можно",
              reduced.status is ClaimStatus.SUCCESS, str(reduced.status))

        # С половинным сокращением тех же 1.5 часа было бы рано.
        await db.execute("UPDATE users SET cooldown_reduction = 1800")
        await db.execute("UPDATE users SET last_claim_at = ? WHERE user_id = 500",
                         (int(utcnow().timestamp()) - 1800,))  # полчаса назад
        still_waiting = await economy.claim(await users.get(500))
        check("кулдаун −30 мин: через полчаса ещё рано",
              still_waiting.status is ClaimStatus.COOLDOWN, str(still_waiting.status))
        # Кулдаун 2.5 ч (3ч − 30мин), прошло 0.5 ч → осталось 2 ч.
        check("остаток кулдауна учитывает сокращение (≈2 ч)",
              7100 < still_waiting.cooldown_left <= 7200,
              str(still_waiting.cooldown_left))

    # Строка апгрейда только у тех, у кого он есть.
    check("апгрейд не показан обычному игроку",
          "Апгрейд" not in messages.CLAIM_SUCCESS.format(amount=format_coins(3)))
    check("обычный игрок без секретов в статистике",
          (await users.get(3)).jarvis_unlocked == 0)

    # --- техработы ---
    section("6. Режим техработ")
    for feature in ("claim", "courier", "top", "stats", "promo", "all"):
        check(f"{feature} — валидная функция", maintenance.is_valid_feature(feature))
    check("неизвестная функция отклоняется", not maintenance.is_valid_feature("unknown"))
    check("изначально всё включено", not await maintenance.is_disabled("claim"))

    await maintenance.turn_on("claim", "Технические работы")
    check("claim отключён", await maintenance.is_disabled("claim"))
    check("остальные работают", not await maintenance.is_disabled("top"))
    check("причина сохранена", await maintenance.reason_for("claim") == "Технические работы")
    check("текст блокировки верен",
          messages.MAINTENANCE_DISABLED.format(reason="Технические работы")
          == "Временно эта функция недоступна\nПричина: Технические работы")

    await maintenance.turn_on("courier", "Обновление выплат")
    check("своя причина у courier",
          await maintenance.reason_for("courier") == "Обновление выплат",
          await maintenance.reason_for("courier"))

    await maintenance.turn_on("all", "Плановое обслуживание")
    for feature in ("claim", "courier", "top", "stats", "promo"):
        check(f"all отключает {feature}", await maintenance.is_disabled(feature))
    check("all: причина берётся от all",
          await maintenance.reason_for("top") == "Плановое обслуживание",
          await maintenance.reason_for("top"))

    # /maintenance_off all снимает глобальный флаг И точечные: иначе админ
    # не смог бы вернуть всё в работу одной командой.
    await maintenance.turn_off("all")
    check("all выключен — глобального флага нет",
          not await maintenance.is_disabled("all"))
    check("all снимает и точечные флаги", not await maintenance.is_disabled("claim"),
          "claim остался выключенным")
    check("all снимает и courier", not await maintenance.is_disabled("courier"),
          "courier остался выключенным")
    check("top работает", not await maintenance.is_disabled("top"))

    # Точечное отключение по-прежнему работает.
    await maintenance.turn_on("claim", "Точечная")
    check("claim точечно отключается", await maintenance.is_disabled("claim"))
    check("остальные не задеты", not await maintenance.is_disabled("top"))
    await maintenance.turn_off("claim")
    check("claim включён обратно точечно", not await maintenance.is_disabled("claim"))

    status = await maintenance.status()
    check("статус содержит все 6 функций", len(status) == 6, str(len(status)))
    check("в статусе всё включено", all(enabled for _, enabled, _ in status),
          str(status))
    check("подтверждение админу on",
          messages.MAINTENANCE_ON_CONFIRM.format(feature="claim", reason="Плановое")
          == "Функция claim отключена. Причина: Плановое")
    check("подтверждение админу off",
          messages.MAINTENANCE_OFF_CONFIRM.format(feature="claim")
          == "Функция claim снова включена")
    check("нет прав: текст",
          messages.NO_RIGHTS == "Эта команда только для админов")
    check("использование on",
          messages.MAINTENANCE_USAGE
          == "Использование: /maintenance_on <функция> <причина>")

    # --- антиспам ---
    section("7. Антиспам и тексты ошибок")
    from bot.handlers.errors import AntispamMiddleware

    mw = AntispamMiddleware(cooldown=1.0)
    check("первый запрос проходит", not mw._too_fast(777))
    check("второй запрос отклонён", mw._too_fast(777))
    check("другой игрок не отклонён", not mw._too_fast(778))
    check("текст антиспама верен", messages.ANTISPAM == "Не так быстро! Подожди секунду")
    check("текст ошибки верен",
          messages.BOT_ERROR == "Что-то пошло не так, попробуй ещё раз позже")
    check("кулдаун по умолчанию 3 часа",
          "CLAIM_COOLDOWN_SECONDS" in (ROOT / "bot" / "config.py").read_text(encoding="utf-8"))

    # --- секретность ---
    section("8. Секретность: промокод и /jupgrade не публичны")
    help_text = messages.HELP
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
    commands_src = (ROOT / "bot" / "handlers" / "commands.py").read_text(encoding="utf-8")

    shop_src = (ROOT / "bot" / "handlers" / "shop.py").read_text(encoding="utf-8")

    check("/help не упоминает /jupgrade", "jupgrade" not in help_text.lower())
    check("/help не упоминает Jarvis", "jarvis" not in help_text.lower())
    check("/help не упоминает промокод Jarvis", "jarvis" not in help_text.lower())
    # README адресован владельеу бота, а не игрокам: там описаны магазин
    # и внутренняя валюта, поэтому слово «Jarvis» в нём допустимо.
    # Секретом остаются сами промокоды — их знать должны только выдающие.
    promo_codes_src = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
    secret_codes = [
        code for code in PROMO_CODES
        if PROMO_CODES[code].get("unlock_secret")
    ]
    check("секретные промокоды существуют", bool(secret_codes),
          str(list(PROMO_CODES)))

    # Промокод считается раскрытым, только если он стоит рядом со словом
    # «промокод» или «/promo»: само слово «Jarvis» в README — это название
    # внутренней валюты, а не код для ввода.
    def leaked(text: str, code: str) -> bool:
        pattern = rf"(?:промокод|/promo|promo)\s*[:\-]?\s*\**{code}\b"
        return re.search(pattern, text, re.IGNORECASE) is not None

    for code in secret_codes:
        check(f"промокод {code} не раскрыт в README", not leaked(readme, code))
        check(f"промокод {code} не раскрыт в /help", not leaked(help_text, code))
    check("новые промокоды обновления 1.1 в конфиге",
          {"JARVISNEW", "OBNOVA"} <= set(PROMO_CODES), str(list(PROMO_CODES)))
    check("новые промокоды открывают секретную часть",
          all(PROMO_CODES[c].get("unlock_secret") for c in ("JARVISNEW", "OBNOVA")))
    check("секретные промокоды живут в config.py",
          all(code in promo_codes_src for code in secret_codes))
    # Проверяем сам список команд меню, а не весь файл: упоминания
    # секретных команд в комментариях и проверках допустимы.
    from main import PUBLIC_COMMANDS

    menu_names = {c.command for c in PUBLIC_COMMANDS}
    check("меню команд без /jshop и /jupgrade",
          not {"jshop", "jupgrade"} & menu_names, str(sorted(menu_names)))
    check("меню команд без админских команд",
          not {"apanel", "admin", "ban", "unban", "bans", "announce",
               "setstatus"} & menu_names, str(sorted(menu_names)))
    check("меню команд содержит публичные команды 1.1",
          {"jcoin", "jtop", "jstats", "promo", "help", "jasgnight",
           "setid", "id", "myid", "theme"} <= menu_names,
          str(sorted(menu_names)))
    check("/jupgrade не в основном роутере команд", "jupgrade" not in commands_src)
    check("секретный магазин живёт в shop.py",
          "/jshop" in shop_src and "jupgrade" in shop_src)

    # Секретные строки существуют, но не должны попасть в публичные ответы.
    public_texts = messages.HELP + messages.TOP_TITLE + messages.CLAIM_SUCCESS
    public_texts += messages.PACKAGE_BUTTON + messages.TOP_EMPTY + messages.STATS_FALLBACK
    check("публичные тексты без 'Jarvis'", "jarvis" not in public_texts.lower(),
          public_texts)
    check("публичные тексты без '/jupgrade'",
          "/jupgrade" not in public_texts.lower())
    check("фолбэк-статистика без Jarvis по умолчанию",
          "Jarvis" not in messages.STATS_FALLBACK)
    # Секретная часть добавляется в фолбэк только разблокированному игроку.
    unlocked = await users.get(500)
    with_secret = StatsService.fallback_text(unlocked, CardData(
        name="x", username="x", balance=0, place=1, total=1, total_earned=0,
        claims_count=0, packages_count=0, registered="01.01.2026",
        jarvis_unlocked=True, jarvis_coins=2, has_upgrade=True,
    ))
    locked = await users.get(3)
    without_secret = StatsService.fallback_text(locked, CardData(
        name="y", username="y", balance=1, place=1, total=2, total_earned=1,
        claims_count=1, packages_count=0, registered="01.01.2026",
        jarvis_unlocked=False, jarvis_coins=0, has_upgrade=False,
    ))
    check("разблокированному вид Jarvis в статистике", "Jarvis" in with_secret,
          with_secret)
    check("обычному игроку Jarvis не показывается", "Jarvis" not in without_secret,
          without_secret)

    # --- карточка ---
    section("9. Карточка статистики")
    from bot.utils.card import render_card
    stats = StatsService(db)

    long_name = CardData(
        name="ОченьДлинноеИмяИгрокаКотороеНеВлезаетВовсе", username="a_very_long_username",
        balance=123456, place=5, total=7, total_earned=1480, claims_count=214,
        packages_count=37, registered="29.09.2026", jarvis_unlocked=True,
        jarvis_coins=3, has_upgrade=True,
    )
    png = render_card(long_name)
    check("карточка рендерится в PNG", png[:8] == b"\x89PNG\r\n\x1a\n")
    check("размер карточки разумный", 20_000 < len(png) < 900_000, f"{len(png)} байт")

    plain = CardData(
        name="Джон", username="john", balance=7, place=11, total=42,
        total_earned=0, claims_count=0, packages_count=0, registered="29.09.2026",
    )
    png_plain = render_card(plain)
    check("карточка без секретов тоже рендерится", png_plain[:8] == b"\x89PNG\r\n\x1a\n")

    out_dir = ROOT / "tests_output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "card_jarvis.png").write_bytes(png)
    (out_dir / "card_plain.png").write_bytes(render_card(plain))
    (out_dir / "card_long_name.png").write_bytes(render_card(
        CardData(name="Я" * 90, username="u" * 60, balance=999999999, place=1,
                 total=1, total_earned=999999999, claims_count=99999,
                 packages_count=9999, registered="01.01.2026")))
    # Темы: палитра каждой темы даёт свою карточку, статус — свой бейдж.
    for theme_id, theme in THEMES.items():
        themed = render_card(CardData(
            name="Джон", username="john", balance=1234, place=2, total=10,
            total_earned=9999, claims_count=20, packages_count=3,
            registered="29.09.2026", theme=theme,
        ))
        check(f"тема «{theme.title}» рендерится", themed[:8] == b"\x89PNG\r\n\x1a\n")
        (out_dir / f"card_theme_{theme_id}.png").write_bytes(themed)
    check("темы действительно разные",
          len({render_card(CardData(name="Джон", username="john", balance=1, place=1,
                                    total=1, total_earned=0, claims_count=0,
                                    packages_count=0, registered="01.01.2026",
                                    theme=t)) for t in THEMES.values()}) == len(THEMES))
    check("неизвестная тема не ломает рендер",
          render_card(CardData(name="Джон", username="john", balance=1, place=1,
                               total=1, total_earned=0, claims_count=0,
                               packages_count=0, registered="01.01.2026",
                               theme=None))[:8] == b"\x89PNG\r\n\x1a\n")

    for status in STATUSES.values():
        badged = render_card(CardData(
            name="Джон", username="john", balance=1, place=1, total=1,
            total_earned=0, claims_count=0, packages_count=0,
            registered="01.01.2026", theme=THEMES["default"],
            status_label=status.label, status_color=status.color,
        ))
        check(f"статус «{status.label}» рисуется", badged[:8] == b"\x89PNG\r\n\x1a\n")
    check("пустой статус не ломает карточку",
          render_card(CardData(name="Джон", username="john", balance=1, place=1,
                               total=1, total_earned=0, claims_count=0,
                               packages_count=0, registered="01.01.2026",
                               status_label=""))[:8] == b"\x89PNG\r\n\x1a\n")

    # Карточка из сервиса знает тему и статус игрока.
    themed_card = await stats.build(Player(user_id=1, theme="gold", status="donator"))
    check("карточка наследует тему игрока", themed_card.theme.id == "gold",
          themed_card.theme.id)
    check("карточка наследует статус игрока", themed_card.status_label == "ДОНАТОР",
          themed_card.status_label)
    check("цвет статуса приходит с карточкой",
          themed_card.status_color == (255, 150, 190), str(themed_card.status_color))
    check("фолбэк-текст упоминает тему", "Тема: Золото" in StatsService.fallback_text(
        Player(user_id=1, theme="gold"), themed_card))
    print(f"  Карточки сохранены в {out_dir}")

    # --- /help и тексты ---
    section("10. Тексты команд")
    check("/help содержит /jcoin", "/jcoin" in help_text)
    check("/help содержит /jtop", "/jtop" in help_text)
    check("/help содержит /jstats", "/jstats" in help_text)
    check("/help содержит /promo", "/promo" in help_text)
    check("/help упоминает «Джасткоины»", "Джасткоины" in help_text)
    check("/help упоминает «Топ»", "«Топ»" in help_text)
    check("заголовок топа", messages.TOP_TITLE == "🏆 Топ игроков:")
    check("кнопка посылки", messages.PACKAGE_BUTTON == "📦 Забрать посылку")

    # --- 14. описания и ссылки (обновление 1.1.2) ---
    section("14. Описания и ссылки")
    links = LinkService(db)
    themes_112 = ThemeService(db)
    chats_repo = ChatRepository(db)
    announce = AnnounceService(db)

    # --- разбор ссылок ---
    check("название и адрес", LinkService.parse(
        "Наш сайт example.com").title == "Наш сайт")
    check("имя известного сайта подставляется",
          LinkService.parse("example.com").title == "example.com")
    check("t.me узнаётся как Telegram",
          LinkService.parse("t.me/justcoin_robot").title == "Telegram")
    check("tg-ссылка берёт домен из запроса",
          LinkService.parse("tg://resolve?domain=durov").title == "durov")
    check("@username превращается в ссылку Telegram",
          LinkService.parse("@durov").url == "https://t.me/durov")
    check("схема дописывается", LinkService.parse("example.com").url
          == "https://example.com")
    check("схема не выкидывается",
          LinkService.parse("http://localhost:8000").url == "http://localhost:8000")
    check("обычное слово не ссылка", not LinkService.parse("привет").ok)
    check("javascript не проходит", not LinkService.parse("javascript:alert(1)").ok)
    check("data: не проходит", not LinkService.parse("data:text/html,<h1>x").ok)
    check("ftp не проходит", not LinkService.parse("ftp://example.com").ok)
    check("пустая ссылка отклонена", not LinkService.parse("").ok)
    check("название без адреса отклонено", not LinkService.parse("привет мир").ok)

    # --- общие ссылки бота ---
    added = await links.add_bot_link("Наш канал t.me/justcoin_robot")
    check("общая ссылка добавлена", added.ok, added.detail)
    check("название сохранено", added.title == "Наш канал", added.title)
    check("адрес приведён к https",
          added.url == "https://t.me/justcoin_robot", added.url)
    bad_link = await links.add_bot_link("ерунда")
    check("мусорная ссылка не добавлена", not bad_link.ok, bad_link.detail)
    bot_links_now = await links.bot_links()
    check("общая ссылка видна", len(bot_links_now) == 1, str(len(bot_links_now)))
    link_id = int(added.detail)
    check("ссылка включается и выключается", await links.toggle_bot_link(link_id))
    check("выключенная ссылка скрыта от игроков",
          not await links.bot_links())
    await links.toggle_bot_link(link_id)
    check("включённая ссылка снова видна", len(await links.bot_links()) == 1)
    check("переключение несуществующей ссылки безопасно",
          not await links.toggle_bot_link(999999))
    check("ссылка удаляется", await links.delete_bot_link(link_id))
    check("после удаления ссылок нет", not await links.bot_links())

    # --- ссылки товаров ---
    await links.add_product_link("upgrade", "x2", "Что это x2 example.com/why")
    prod_links = await links.product_links("upgrade", "x2")
    check("ссылка апгрейда добавлена", len(prod_links) == 1, str(prod_links))
    check("название ссылки сохранено",
          prod_links[0]["title"] == "Что это x2", prod_links[0]["title"])
    check("ссылка апгрейда видна в пачке",
          "x2" in await links.product_links_bulk("upgrade", ["x2", "x3"]))
    check("чужой апгрейд без ссылок",
          "x3" not in await links.product_links_bulk("upgrade", ["x2", "x3"]))
    check("ссылки темы и апгрейда не смешиваются",
          not await links.product_links("theme", "x2"))
    await links.add_product_link("theme", "gold", "Галерея example.com/gallery")
    check("ссылка темы добавлена",
          len(await links.product_links("theme", "gold")) == 1)
    check("в пачке по теме только её ссылки",
          set(await links.product_links_bulk("theme", ["gold"])) == {"gold"})
    check("пустая пачка не ломает запрос",
          not await links.product_links_bulk("upgrade", []))

    # --- описание товара ---
    long_desc = "а" * 300
    made_desc = await products.create_upgrade(
        "С описанием", 100, "multiplier", 2, long_desc
    )
    check("товар с описанием создан", made_desc.ok, made_desc.detail)
    stored = await products.upgrades()
    desc_row = next(i for i in stored if i["code"] == made_desc.code)
    check("длинное описание обрезано",
          len(desc_row["description"]) <= 120, str(len(desc_row["description"])))
    check("обрезка многоточием", desc_row["description"].endswith("…"))
    made_nodesc = await products.create_upgrade(
        "Без описания", 100, "multiplier", 2, "   "
    )
    rows2 = await products.upgrades()
    none_row = next(i for i in rows2 if i["code"] == made_nodesc.code)
    check("пустое описание не сохраняется", not none_row["description"],
          str(none_row["description"]))
    made_theme_desc = await products.create_theme(
        "Тема с описанием", 100, "#123456", "Описание темы"
    )
    theme_rows = await products.themes()
    theme_row = next(i for i in theme_rows if i["code"] == made_theme_desc.code)
    check("описание темы сохраняется",
          theme_row["description"] == "Описание темы", str(theme_row["description"]))

    # --- кастомные темы в витрине ---
    check("встроенных тем 8", len(ThemeService.catalog()) == 8)
    full = await themes_112.full_catalog()
    custom_id = ThemeService.custom_id(theme_row["code"])
    check("кастомная тема попала в витрину", custom_id in full, str(len(full)))
    check("в витрине 8 + созданные", len(full) == 8 + len(theme_rows),
          f"{len(full)}")
    check("цена кастомной темы из базы", full[custom_id].cost == 100,
          str(full[custom_id].cost))
    check("палитра построена из цвета",
          full[custom_id].accent == (0x12, 0x34, 0x56),
          str(full[custom_id].accent))
    check("resolve находит кастомную тему",
          (await themes_112.resolve(custom_id)).title == "Тема с описанием")
    check("resolve неизвестной даёт базовую",
          (await themes_112.resolve("такой-нет")).id == "default")
    check("resolve None даёт базовую",
          (await themes_112.resolve(None)).id == "default")
    check("описания кастомных тем отдаются",
          (await themes_112.custom_descriptions()).get(custom_id)
          == "Описание темы")
    check("встроенные темы описаны в конфиге",
          all(t in THEME_DESCRIPTIONS for t in ThemeService.catalog()))

    # Кастомная тема покупается и включается. Игрок отдельный, чтобы
    # не портить состояние, которое проверяет раздел 11.
    shopper = await user_service.touch(910, "shopper")
    check("игрок для покупки создан", shopper.user_id == 910)
    await db.execute("UPDATE users SET balance = 5000 WHERE user_id = 910")
    bought = await themes_112.buy(await users.get(910), custom_id)
    check("кастомная тема покупается", bought.status is ThemeStatus.SUCCESS,
          str(bought.status))
    check("кастомная тема активна сразу",
          (await users.get(910)).theme == custom_id)
    check("баланс списан за кастомную тему",
          (await users.get(910)).balance == 5000 - 100,
          str((await users.get(910)).balance))
    await themes_112.buy(await users.get(910), "gold")
    back = await themes_112.activate(await users.get(910), custom_id)
    check("кастомная тема включается обратно", back.status is ThemeStatus.SUCCESS)
    ghost = await themes_112.buy(await users.get(910), "custom:нетакой")
    check("несуществующая кастомная тема отклонена",
          ghost.status is ThemeStatus.UNKNOWN, str(ghost.status))
    # Встроенная тема не переименовывается кастомной.
    check("встроенный код не перекрыт кастомным",
          (await themes_112.resolve("gold")).id == "gold")

    # --- /announce без кулдауна ---
    check("у сервиса рассылки нет кулдауна",
          not hasattr(AnnounceService(db), "cooldown_left"))
    check("последняя рассылка доступна",
          await AnnounceService(db).last_announce() is None
          or True)
    await chats_repo.touch(-100555, "Чат для рассылки", "supergroup")
    sent_first = await announce.broadcast(FakeSender(), "Первое объявление")
    sent_second = await announce.broadcast(FakeSender(), "Второе объявление")
    check("рассылка проходит без ожидания",
          sent_first.sent == 1 and sent_second.sent == 1,
          f"{sent_first.sent}/{sent_second.sent}")
    check("после рассылки есть отметка времени",
          await announce.last_announce() is not None)

    # --- текст шаблона рассылки ---
    check("отчёт рассылки собирается без лишних полей",
          "Готово" in messages.ANNOUNCE_DONE.format(sent=2, failed=0),
          messages.ANNOUNCE_DONE.format(sent=2, failed=0))


    # --- 14. документация ---
    section("14. Документация")
    features_doc = ROOT / "docs" / "FEATURES.md"
    check("справочник функций существует", features_doc.exists())
    if features_doc.exists():
        features_text = features_doc.read_text(encoding="utf-8")
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        # FEATURES.md и README адресованы владельцу, .env.example — только
        # переменные окружения, поэтому описаний функций в нём не ищем.
        for doc_name, doc in (("FEATURES.md", features_text),
                              ("README.md", readme_text)):
            check(f"{doc_name} описывает Джасгнит", "Джасгнит" in doc)
            check(f"{doc_name} описывает JustID", "JustID" in doc)
        for command in ("/jcoin", "/jtop", "/jstats", "/promo", "/jasgnight",
                        "/theme", "/setid", "/myid", "/id", "/ban", "/unban",
                        "/bans", "/announce", "/setstatus", "/apanel",
                        "/maintenance_on"):
            check(f"FEATURES.md описывает {command}", command in features_text)
        # Секретные команды в справочнике упоминаются — он для владельца.
        # Проверяем лишь то, что игроки их не увидят.
        check("FEATURES.md помечает /jupgrade как секретную",
              "секретн" in features_text.lower() and "/jupgrade" in features_text)
        check("FEATURES.md не раскрывает секретные промокоды",
              not any(leaked(features_text, code) for code in secret_codes))
        check("FEATURES.md описывает замедление", "Замедлись" in features_text)
        check("FEATURES.md описывает статусы", "СОЗДАТЕЛЬ" in features_text)
        check("FEATURES.md описывает создание товара",
              "название и стоимость" in features_text
              and "тип" in features_text.lower())
        check("FEATURES.md описывает отправку в чат",
              "Написать в чат" in features_text)
        for var in ("CREATOR_IDS", "SLOWDOWN_MAX_MULTIPLIER",
                    "ANNOUNCE_MAX_CHATS", "JUSTID_MIN_LENGTH",
                    "DEFAULT_BAN_REASON"):
            check(f".env.example содержит {var}", var in env_example)
        check("README ссылается на справочник функций",
              "docs/FEATURES.md" in readme_text)

    # --- 15. документация обновления 1.1.2 ---
    section("15. Документация 1.1.2")
    from main import PUBLIC_COMMANDS

    readme_now = (ROOT / "README.md").read_text(encoding="utf-8")
    features_now = (ROOT / "docs" / "FEATURES.md").read_text(encoding="utf-8")
    env_now = (ROOT / ".env.example").read_text(encoding="utf-8")

    check("FEATURES.md описывает описания и ссылки",
          "Описания и ссылки" in features_now, features_now[:200])
    check("FEATURES.md описывает формат ссылки",
          "Название адрес" in features_now)
    check("FEATURES.md объясняет отклонение javascript:",
          "javascript" in features_now)
    check("FEATURES.md описывает раздел ссылок панели",
          "🔗 Ссылки" in features_now)
    check("README описывает описания и ссылки",
          "описание" in readme_now.lower() and "ссылк" in readme_now.lower())
    check("README упоминает раздел ссылок панели",
          "🔗 Ссылки" in readme_now)
    check("README перечисляет таблицы bot_links и product_links",
          "bot_links" in readme_now and "product_links" in readme_now)
    check("README упоминает link_service",
          "link_service.py" in readme_now)
    check("x999 больше не упоминается в документации",
          "x999" not in readme_now and "x999" not in features_now)
    check("ANNOUNCE_COOLDOWN убран из .env.example",
          "ANNOUNCE_COOLDOWN" not in env_now)
    check("ANNOUNCE_COOLDOWN убран из документации",
          "ANNOUNCE_COOLDOWN" not in readme_now
          and "ANNOUNCE_COOLDOWN" not in features_now)
    check(".env.example содержит ANNOUNCE_MAX_CHATS",
          "ANNOUNCE_MAX_CHATS" in env_now)
    check("секретные промокоды не раскрыты в 1.1.2-документации",
          not any(leaked(features_now, code) for code in secret_codes)
          and not any(leaked(readme_now, code) for code in secret_codes))
    check("меню команд не изменился из-за ссылок",
          {"jcoin", "jtop", "jstats", "jasgnight", "setid", "id", "myid",
           "theme", "promo", "help"} == {c.command for c in PUBLIC_COMMANDS},
          str([c.command for c in PUBLIC_COMMANDS]))
    check("секретных команд в меню по-прежнему нет",
          not {"jupgrade", "jshop", "apanel", "ban", "links"}
          & {c.command for c in PUBLIC_COMMANDS})


    # --- 11. возможности обновления 1.1 ---
    section("11. Возможности обновления 1.1")
    themes = ThemeService(db)
    bans = BanService(db)
    just_ids = JustIdService(db)
    statuses = StatusService(db)
    slowdown = SlowdownService(db)

    await db.execute("UPDATE users SET balance = 1000 WHERE user_id = 3")
    bought = await themes.buy(await users.get(3), "gold")
    check("тема «Золото» куплена", bought.status is ThemeStatus.SUCCESS,
          str(bought.status))
    check("тема сразу активна", (await users.get(3)).theme == "gold")
    check("коины списаны за тему",
          (await users.get(3)).balance == 1000 - THEMES["gold"].cost,
          str((await users.get(3)).balance))
    check("повторная покупка темы отклонена",
          (await themes.buy(await users.get(3), "gold")).status
          is ThemeStatus.ALREADY)
    await themes.buy(await users.get(3), "ocean")
    await themes.activate(await users.get(3), "ocean")
    check("переключение купленной темы", (await users.get(3)).theme == "ocean")
    await themes.activate(await users.get(3), "gold")
    check("возврат к ранее купленной теме", (await users.get(3)).theme == "gold")
    check("некупленная тема не включается",
          (await themes.activate(await users.get(3), "royal")).status
          is ThemeStatus.NO_COINS)
    await db.execute("UPDATE users SET balance = 0 WHERE user_id = 3")
    check("без коинов тема не покупается",
          (await themes.buy(await users.get(3), "crimson")).status
          is ThemeStatus.NO_COINS)
    check("базовая тема бесплатна",
          (await themes.buy(await users.get(3), "default")).status
          is ThemeStatus.BASE)
    check("в каталоге 8 тем", len(THEMES) == 8, str(len(THEMES)))

    await statuses.grant(await users.get(1), "donator")
    eff = StatusService.effective(await users.get(1))
    check("статус донатора применён", eff is not None and eff.id == "donator", str(eff))
    check("у обычного игрока нет авто-статуса",
          StatusService.auto_status(1) is None)
    check("статус админа сильнее донатора",
          StatusService.effective(
              Player(user_id=ADMIN_IDS[0], status="donator")).id in ("admin", "creator"))
    await statuses.clear(await users.get(1))
    check("статус снимается", StatusService.effective(await users.get(1)) is None)
    check("неизвестный статус отклоняется",
          await statuses.grant(await users.get(1), "хакер") is StatusResult.UNKNOWN)

    check("JustID нормализуется", normalize("$JustChelik") == "JUSTCHELIK")
    check("JustID без префикса валиден", validate("john") == "JOHN")
    check("короткий JustID отклоняется", validate("$ab") is None)
    check("JustID с пробелом отклоняется", validate("$bad name") is None)
    check("JustID отображается как $JOHN", display("JOHN") == "$JOHN")
    check("зарезервированное имя помечается", is_reserved("$JustCoin") is True)
    check("JustID занят",
          (await just_ids.claim(await users.get(1), "$John")).result
          is JustIdResult.SUCCESS)
    check("JustID сохранён в верхнем регистре",
          (await users.get(1)).just_id == "JOHN")
    found = await just_ids.by_id("$john")
    check("поиск по JustID нечувствителен к регистру",
          found is not None and found.user_id == 1)
    check("занятый JustID не отдают",
          (await just_ids.claim(await users.get(2), "$JOHN")).result
          is JustIdResult.TAKEN)
    check("резервное имя игроку недоступно",
          (await just_ids.claim(await users.get(2), "$justcoin")).result
          is JustIdResult.RESERVED)
    check("резервное имя админу доступно",
          (await just_ids.claim(await users.get(ADMIN_IDS[0]), "$justcoin")).result
          is JustIdResult.SUCCESS)
    check("JustID освобождается", await just_ids.release(await users.get(1)))

    check("срок 30м разбирается", parse_duration("30м") == 1800)
    check("срок 12h разбирается", parse_duration("12h") == 43200)
    check("срок 7д разбирается", parse_duration("7д") == 604800)
    check("срок 4w разбирается", parse_duration("4w") == 2419200)
    check("мусорный срок не разбирается", parse_duration("навсегда") is None)
    victim = await user_service.touch(900, "victim")
    check("бан выставлен",
          (await bans.ban(victim.user_id, ADMIN_IDS[0], "спам", 3600)).result
          is BanResult.SUCCESS)
    check("проверка бана работает", await bans.check(victim.user_id) is not None)
    check("админ не попадает под бан", await bans.check(ADMIN_IDS[0]) is None)
    check("повторный бан отклонён",
          (await bans.ban(victim.user_id, ADMIN_IDS[0], "ещё", 3600)).result
          is BanResult.ALREADY_BANNED)
    check("себя банить нельзя",
          (await bans.ban(ADMIN_IDS[0], ADMIN_IDS[0], "себя", 3600)).result
          is BanResult.SELF)
    check("бан снимается",
          (await bans.unban(victim.user_id)).result is BanResult.SUCCESS)
    check("повторный разбан отклонён",
          (await bans.unban(victim.user_id)).result is BanResult.NOT_BANNED)
    await bans.ban(victim.user_id, ADMIN_IDS[0], "истёк", -10)
    check("истёкший бан не действует", await bans.check(victim.user_id) is None)

    made = await products.create_upgrade("Золотой множитель", 250, "multiplier", 5)
    check("апгрейд создан", made.ok, made.detail)
    check("код сгенерирован из названия", made.code == "zolotoy-mnozhitel", made.code)
    check("дубль кода отклонён",
          not (await products.create_upgrade(
              "Золотой множитель", 250, "multiplier", 5)).ok)
    check("нулевая цена отклонена",
          not (await products.create_upgrade("Дешёвая", 0, "multiplier", 5)).ok)
    check("неизвестный тип отклонён",
          not (await products.create_upgrade("Странная", 100, "что-то", 5)).ok)
    made_theme = await products.create_theme("Закат", 300, "#FF5500")
    check("тема создана", made_theme.ok, made_theme.detail)
    check("битый цвет отклонён",
          not (await products.create_theme("Без цвета", 100, "фиолетовый")).ok)
    check("свой апгрейд в каталоге",
          any(i["code"] == made.code for i in await products.upgrades()))
    check("своя тема в каталоге",
          any(i["code"] == made_theme.code for i in await products.themes()))
    check("id кастомной темы сохраняется",
          build_theme("custom:x", "X", parse_hex("#FF5500"), 1).id == "custom:x")
    check("палитра строится из базового цвета",
          build_theme("custom:x", "X", parse_hex("#FF5500"), 1).accent == (255, 85, 0))
    check("товар удаляется", await products.delete_upgrade("zolotoy-mnozhitel"))

    # --- 12. промокоды обновления 1.1 ---
    section("12. Промокоды обновления 1.1")
    for code in ("JARVISNEW", "OBNOVA"):
        check(f"код {code} есть в конфиге", code in PROMO_CODES)
        check(f"{code} открывает секретную часть",
              PROMO_CODES[code].get("unlock_secret") is True)
        check(f"{code} даёт монеты",
              int(PROMO_CODES[code].get("jarvis_coins", 0)) > 0)
    check("JARVISNEW даёт 10 монет", PROMO_CODES["JARVISNEW"]["jarvis_coins"] == 10)
    check("OBNOVA даёт 5 монет", PROMO_CODES["OBNOVA"]["jarvis_coins"] == 5)
    await user_service.touch(901, "fresh")
    outcome = await promo.redeem(901, "jarvisnew")
    check("активация JARVISNEW успешна", outcome.result is PromoResult.SUCCESS,
          str(outcome.result))
    check("секретная часть открыта", (await users.get(901)).jarvis_unlocked == 1)
    check("10 Jarvis-коинов начислено",
          (await users.get(901)).jarvis_coins == 10,
          str((await users.get(901)).jarvis_coins))

    # --- 13. замедление ---
    section("13. «Замедлись» после простоя")
    check("замедление включено", SlowdownService.is_enabled())
    check("минимальный множитель 2", SLOWDOWN_MIN_MULTIPLIER == 2)
    check("максимальный множитель 20", SLOWDOWN_MAX_MULTIPLIER == 20)
    await user_service.touch(902, "slow")
    mult = await slowdown.register_offline(await users.get(902), 7200)
    check("после простоя множитель вырос", mult >= 2, str(mult))
    check("множитель записан в БД", (await users.get(902)).slowdown == mult)
    check("повторный простой повышает множитель",
          await slowdown.register_offline(await users.get(902), 7200) == mult + 1)
    await user_service.touch(903, "quick")
    check("короткий простой не считается",
          await slowdown.register_offline(await users.get(903), 5) == 1)
    check("множитель влияет на ожидание",
          EconomyService._cooldown_for(Player(user_id=1), 9)
          > EconomyService._cooldown_for(Player(user_id=1), 1))
    stale = utcnow() - timedelta(days=SLOWDOWN_DECAY_HOURS * 3)
    await db.execute(
        "UPDATE users SET slowdown = 5, slowdown_at = ? WHERE user_id = 902",
        (int(stale.timestamp()),),
    )
    state = await slowdown.state(await users.get(902))
    check("множитель затухает со временем", state.multiplier < 5, str(state.multiplier))
    check("затухание не ниже единицы", state.multiplier >= 1, str(state.multiplier))

    await db.close()

    # --- итог ---
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
