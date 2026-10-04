"""Конфигурация бота. Все настройки берутся из .env с безопасными значениями по умолчанию."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "bot" / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _int_list_env(name: str, default: list[int]) -> list[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    result: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.append(int(chunk))
        except ValueError:
            continue
    return result or list(default)


# --- Telegram ------------------------------------------------------------------
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: list[int] = _int_list_env("ADMIN_IDS", [])

# --- Валюта --------------------------------------------------------------------
CURRENCY_EMOJI = "🪙"
CURRENCY_NAME = "ДжастКоин"

# --- /jcoin --------------------------------------------------------------------
CLAIM_COOLDOWN_SECONDS: int = _int_env("CLAIM_COOLDOWN_SECONDS", 3 * 60 * 60)
CLAIM_MIN_AMOUNT: int = _int_env("CLAIM_MIN_AMOUNT", 1)
CLAIM_MAX_AMOUNT: int = _int_env("CLAIM_MAX_AMOUNT", 5)

# --- Курьер --------------------------------------------------------------------
# Шанс выпадения курьера при получении коинов. 0.01 = 1 из 100.
COURIER_CHANCE: float = float(os.getenv("COURIER_CHANCE", "0.01") or 0.01)
COURIER_PACKAGE_MIN: int = _int_env("COURIER_PACKAGE_MIN", 10)
COURIER_PACKAGE_MAX: int = _int_env("COURIER_PACKAGE_MAX", 50)
COURIER_DELAY_MIN_SECONDS: int = _int_env("COURIER_DELAY_MIN_SECONDS", 30 * 60)
COURIER_DELAY_MAX_SECONDS: int = _int_env("COURIER_DELAY_MAX_SECONDS", 3 * 60 * 60)
COURIER_CHECK_INTERVAL: int = _int_env("COURIER_CHECK_INTERVAL", 30)

# --- Топ -----------------------------------------------------------------------
TOP_SIZE: int = _int_env("TOP_SIZE", 10)
# Размер страницы топа. По умолчанию равен TOP_SIZE: игроков больше
# не показываем по одному, а листаем страницами кнопками.
TOP_PAGE_SIZE: int = _int_env("TOP_PAGE_SIZE", TOP_SIZE)
# Сколько строк в одном сообщении максимум: Telegram не любит длинные
# сообщения, поэтому страница разбивается ещё и по этому пределу.
TOP_PAGE_ROWS: int = _int_env("TOP_PAGE_ROWS", 20)

# --- Промокоды -----------------------------------------------------------------
# Секретные коды НИГДЕ не упоминаются публично: ни в /help, ни в меню команд,
# ни в README для игроков. Коды из этого словаря засеиваются в таблицу
# promo_codes при старте; новые коды добавляются админ-командой /jpromo.
#
# jarvis_coins — сколько секретных монет даёт код
# unlock_secret — открывает ли секретную часть (магазин апгрейдов)
PROMO_CODES: dict[str, dict] = {
    # Стартовый код: открывает секретную часть и даёт монету на x2.
    "JARVIS": {
        "jarvis_coins": 1,
        "unlock_secret": True,
    },
    # Обновление 1.1: расширенные коды.
    "JARVISNEW": {
        "jarvis_coins": 10,
        "unlock_secret": True,
    },
    "OBNOVA": {
        "jarvis_coins": 5,
        "unlock_secret": True,
    },
    # Запасные коды. Раздаются администратором вручную.
    "JARVIS10": {"jarvis_coins": 10},
    "JARVIS25": {"jarvis_coins": 25},
    "JARVIS100": {"jarvis_coins": 100},
}

# --- Темы карточки -------------------------------------------------------------
# Покупаются в магазине «Джасгнит» за обычных джаст коинов.
# Палитра целиком задаётся здесь — карточка не знает про конкретные цвета.
@dataclass(frozen=True, slots=True)
class CardTheme:
    id: str
    title: str
    cost: int
    bg_top: tuple[int, int, int]
    bg_bottom: tuple[int, int, int]
    glow: tuple[int, int, int]
    accent: tuple[int, int, int]
    accent_soft: tuple[int, int, int]
    jarvis: tuple[int, int, int]
    jarvis_soft: tuple[int, int, int]
    panel: tuple[int, int, int, int]
    panel_edge: tuple[int, int, int, int]


# Описание каждой темы — его видит игрок в Джасгните. Отдельный словарь,
# а не поле в CardTheme: палитра — это про картинку, описание — про
# витрину, и меняются они по разным поводам.
THEME_DESCRIPTIONS: dict[str, str] = {
    "default": "Классическая карточка, ничего лишнего",
    "gold": "Тёплые золотые тона с янтарным свечением",
    "ocean": "Холодные синие оттенки цвета океана",
    "emerald": "Глубокая зелень, как изумруд",
    "crimson": "Насыщенный багрянец с тёмным свечением",
    "royal": "Королевский фиолетовый с золотыми акцентами",
    "midnight": "Почти чёрный фон и холодный синий акцент",
    "sunset": "Тёплый закат: от розового к оранжевому",
}

# Префикс темы, созданной админом в панели. Встроенные темы лежат в
# THEMES под своим кодом (gold), кастомные — под кодом с этим
# префиксом (custom:zolotoy-zakat), поэтому их нельзя спутать.
CUSTOM_THEME_PREFIX = "custom:"

THEMES: dict[str, CardTheme] = {
    "default": CardTheme(
        id="default", title="Классика", cost=0,
        bg_top=(24, 22, 44), bg_bottom=(44, 30, 68), glow=(86, 62, 140),
        accent=(255, 205, 92), accent_soft=(255, 226, 160),
        jarvis=(108, 214, 255), jarvis_soft=(166, 236, 255),
        panel=(255, 255, 255, 22), panel_edge=(255, 255, 255, 38),
    ),
    "gold": CardTheme(
        id="gold", title="Золото", cost=250,
        bg_top=(38, 28, 8), bg_bottom=(74, 52, 12), glow=(150, 106, 26),
        accent=(255, 199, 64), accent_soft=(255, 228, 150),
        jarvis=(255, 214, 120), jarvis_soft=(255, 238, 190),
        panel=(255, 208, 110, 26), panel_edge=(255, 208, 110, 62),
    ),
    "ocean": CardTheme(
        id="ocean", title="Океан", cost=150,
        bg_top=(8, 26, 46), bg_bottom=(12, 48, 82), glow=(24, 96, 150),
        accent=(94, 214, 255), accent_soft=(168, 234, 255),
        jarvis=(96, 240, 220), jarvis_soft=(168, 250, 240),
        panel=(120, 200, 255, 22), panel_edge=(120, 200, 255, 58),
    ),
    "emerald": CardTheme(
        id="emerald", title="Изумруд", cost=150,
        bg_top=(6, 32, 24), bg_bottom=(10, 58, 42), glow=(26, 118, 78),
        accent=(126, 240, 168), accent_soft=(190, 252, 214),
        jarvis=(255, 216, 120), jarvis_soft=(255, 238, 186),
        panel=(120, 240, 180, 20), panel_edge=(120, 240, 180, 56),
    ),
    "crimson": CardTheme(
        id="crimson", title="Багрец", cost=200,
        bg_top=(40, 10, 16), bg_bottom=(72, 16, 26), glow=(150, 34, 48),
        accent=(255, 118, 118), accent_soft=(255, 178, 178),
        jarvis=(255, 160, 90), jarvis_soft=(255, 208, 160),
        panel=(255, 130, 130, 22), panel_edge=(255, 130, 130, 58),
    ),
    "royal": CardTheme(
        id="royal", title="Королевский", cost=300,
        bg_top=(28, 8, 44), bg_bottom=(52, 14, 76), glow=(120, 44, 168),
        accent=(214, 160, 255), accent_soft=(238, 208, 255),
        jarvis=(255, 214, 120), jarvis_soft=(255, 238, 190),
        panel=(200, 150, 255, 24), panel_edge=(210, 170, 255, 66),
    ),
    "midnight": CardTheme(
        id="midnight", title="Полночь", cost=180,
        bg_top=(10, 10, 14), bg_bottom=(18, 18, 26), glow=(60, 60, 80),
        accent=(226, 232, 255), accent_soft=(248, 250, 255),
        jarvis=(150, 200, 255), jarvis_soft=(200, 226, 255),
        panel=(220, 230, 255, 16), panel_edge=(220, 230, 255, 48),
    ),
    "sunset": CardTheme(
        id="sunset", title="Закат", cost=220,
        bg_top=(44, 16, 24), bg_bottom=(80, 32, 32), glow=(178, 74, 52),
        accent=(255, 168, 92), accent_soft=(255, 212, 160),
        jarvis=(255, 120, 160), jarvis_soft=(255, 186, 208),
        panel=(255, 170, 110, 24), panel_edge=(255, 180, 120, 60),
    ),
}

# --- Статусы игроков -----------------------------------------------------------
# Показываются бейджем на карточке. Приоритет: чем больше, тем главнее;
# игрок получает статус с наибольшим приоритетом из доступных.
#   creator — владелец бота, выдаётся вручную
#   admin   — назначается автоматически по ADMIN_IDS
#   vip     — особый статус, выдаётся вручную
#   donator — донатер, выдаётся вручную
@dataclass(frozen=True, slots=True)
class PlayerStatus:
    id: str
    title: str
    label: str
    color: tuple[int, int, int]
    priority: int
    auto: bool = False  # выдаётся автоматически по правилу


STATUSES: dict[str, PlayerStatus] = {
    "creator": PlayerStatus("creator", "Создатель", "СОЗДАТЕЛЬ",
                            (255, 214, 96), 100, auto=True),
    "admin": PlayerStatus("admin", "Админ", "АДМИН",
                          (120, 200, 255), 80, auto=True),
    "vip": PlayerStatus("vip", "VIP", "VIP",
                        (214, 160, 255), 60),
    "donator": PlayerStatus("donator", "Донатор", "ДОНАТОР",
                            (255, 150, 190), 40),
}

# Владелец бота — получает статус «Создатель» автоматически.
CREATOR_IDS: list[int] = _int_list_env("CREATOR_IDS", [])

# --- Донаты (Telegram Stars) ---------------------------------------------------
# Игрок кидает звёзды разработчику. Цифровые товары и услуги Telegram
# принимает ТОЛЬКО в звёздах (XTR), provider_token для них пустой.
# Специфика товара: название, описание и сумма — обычные строки.
DONATE_SPEC: list[dict] = [
    {
        "id": "donate_1",
        "stars": 1,
        "title": "1 звезда",
        "description": "Символическая благодарность разработчику",
    },
    {
        "id": "donate_10",
        "stars": 10,
        "title": "10 звёзд",
        "description": "Помочь с сервером и доменом на месяц",
    },
    {
        "id": "donate_50",
        "stars": 50,
        "title": "50 звёзд",
        "description": "Поддержка фич и ответы в чате",
    },
    {
        "id": "donate_100",
        "stars": 100,
        "title": "100 звёзд",
        "description": "Большая поддержка: новые темы и фичи",
    },
    {
        "id": "donate_500",
        "stars": 500,
        "title": "500 звёзд",
        "description": "Серьёзная поддержка развития бота",
    },
    {
        "id": "donate_1000",
        "stars": 1000,
        "title": "1000 звёзд",
        "description": "Максимальная поддержка. Спасибо!",
    },
]
# Границы произвольной суммы: /donate 250.
DONATE_MIN_STARS: int = _int_env("DONATE_MIN_STARS", 1)
DONATE_MAX_STARS: int = _int_env("DONATE_MAX_STARS", 1000)

# --- Антиспам ------------------------------------------------------------------
# Порог тишины: события чаще этого интервала просто игнорируются, без
# ответа игроку. Раньше бот отвечал «Подожди» на каждое лишнее нажатие,
# и это превращалось в спам — особенно на кнопках.
ANTISPAM_COOLDOWN: float = float(
    os.getenv("ANTISPAM_COOLDOWN", "1") or "1"
)
# Столько событий подряд за ANTISPAM_WINDOW секунд — уже флуд, и на него
# бот отвечает один раз. Дальше до остывания снова молчит.
ANTISPAM_FLOOD_EVENTS: int = _int_env("ANTISPAM_FLOOD_EVENTS", 4)
ANTISPAM_WINDOW: float = float(os.getenv("ANTISPAM_WINDOW", "3") or "3")
ANTISPAM_FLOOD_COOLDOWN: int = _int_env("ANTISPAM_FLOOD_COOLDOWN", 60)

# --- Баны ----------------------------------------------------------------------
# Баны игроков. Администраторы банам не подлежат.
DEFAULT_BAN_REASON: str = os.getenv("DEFAULT_BAN_REASON", "Нарушение правил")

# --- JustID --------------------------------------------------------------------
# Короткая ссылка на профиль игрока вида $John.
# Пишется в сообщении или в аргументе команды /id.
JUSTID_PREFIX = "$"
JUSTID_MIN_LENGTH = _int_env("JUSTID_MIN_LENGTH", 3)
JUSTID_MAX_LENGTH = _int_env("JUSTID_MAX_LENGTH", 24)
# Зарезервированы системой и не могут быть заняты игроками.
JUSTID_RESERVED: set[str] = {
    "justcoin", "jarvis", "jasgnight", "admin", "creator", "bot",
    "support", "help", "top", "shop", "news", "official",
}

# --- Объявления ----------------------------------------------------------------
# /announce рассылает сообщение по всем чатам, где бот встречался.
# Ограничения по частоте нет (обновление 1.1.2), поэтому здесь только
# предохранитель на длину списка чатов.
ANNOUNCE_MAX_CHATS: int = _int_env("ANNOUNCE_MAX_CHATS", 5000)

# --- «Замедлись» ---------------------------------------------------------------
# Если бот был оффлайн, игрок не мог получить коины вовремя. Когда он
# возвращается, бот сообщает, насколько он «замедлился», и показывает
# множитель ожидания вместо обычного кулдауна.
# Множитель растёт на каждом пропущенном цикле и сбрасывается со временем.
SLOWDOWN_ENABLED: bool = os.getenv("SLOWDOWN_ENABLED", "1") not in ("0", "false", "False")
SLOWDOWN_MIN_MULTIPLIER: int = _int_env("SLOWDOWN_MIN_MULTIPLIER", 2)
SLOWDOWN_MAX_MULTIPLIER: int = _int_env("SLOWDOWN_MAX_MULTIPLIER", 20)
# Через сколько часов множитель сбрасывается на единицу.
SLOWDOWN_DECAY_HOURS: int = _int_env("SLOWDOWN_DECAY_HOURS", 24)
# Порог оффлайна, с которого начинаем считать, что игрок «замедлился».
SLOWDOWN_OFFLINE_THRESHOLD: int = _int_env(
    "SLOWDOWN_OFFLINE_THRESHOLD", 10 * 60
)
# Jarvis-коины тратятся в магазине (/jshop). Цены задаются здесь.
#
# kind:
#   "multiplier" — множитель начисления (кулдаун не меняется)
#   "cooldown"   — сокращение кулдауна в секундах (множитель не меняется)
#   "instant"    — кулдаун 0; покупается только админом, не продаётся игрокам
#
# Множитель x999 убран из каталога в обновлении 1.1.2: он ломал экономику,
# потому что за 900 000 Jarvis-коинов игрок уходил далеко за пределы
# игрового цикла. Апгрейд admin_only был промежуточным решением —
# теперь его просто нет в каталоге. Уже купившие его сохраняют
# множитель, но выдать его больше нечем.
#
# value: множитель (например 3) либо секунды сокращения кулдауна (например 1800)
# order: апгрейды одного kind нельзя покупать «вниз» — только вверх по порядку.
#        Апгрейды разных kind складываются: можно и x3, и сокращение кулдауна.
UPGRADES: dict[str, dict] = {
    "x2": {
        "kind": "multiplier", "value": 2, "cost": 1, "order": 1,
        "title": "Множитель x2",
        "description": "Джаст коины начисляются вдвое больше",
    },
    "x3": {
        "kind": "multiplier", "value": 3, "cost": 10, "order": 2,
        "title": "Множитель x3",
        "description": "Джаст коины начисляются втрое больше",
    },
    "cd30": {
        "kind": "cooldown", "value": 30 * 60, "cost": 5, "order": 1,
        "title": "Кулдаун −30 минут",
        "description": "Ожидание между получениями короче на полчаса",
    },
    "cd60": {
        "kind": "cooldown", "value": 60 * 60, "cost": 15, "order": 2,
        "title": "Кулдаун −1 час",
        "description": "Ожидание между получениями короче на час",
    },
    "cd120": {
        "kind": "cooldown", "value": 2 * 60 * 60, "cost": 40, "order": 3,
        "title": "Кулдаун −2 часа",
        "description": "Ожидание между получениями короче на два часа",
    },
    "instant": {
        "kind": "instant", "value": 0, "cost": 0, "order": 99,
        "title": "Мгновенное получение",
        "description": "Кулдаун полностью снят. Выдаётся только администратором",
        "admin_only": True,
    },
}

# Цена апгрейда x2 (оставлена для совместимости старых конфигов).
UPGRADE_COST: int = _int_env("UPGRADE_COST", 1)
# "permanent" — навсегда, "<число>" — на N получений коинов
UPGRADE_MODE: str = os.getenv("UPGRADE_MODE", "permanent").strip().lower()

# --- Техработы -----------------------------------------------------------------
DEFAULT_MAINTENANCE_REASON: str = os.getenv(
    "DEFAULT_MAINTENANCE_REASON", "Технические работы"
)
MAINTENANCE_FEATURES: tuple[str, ...] = ("claim", "courier", "top", "stats", "promo", "all")

# --- Прочее --------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "justcoin.db"))
STARTUP_NOTIFY_ADMINS: bool = os.getenv("STARTUP_NOTIFY_ADMINS", "1") not in ("0", "false", "False")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def effective_cooldown(cooldown_reduction: int, instant: bool) -> int:
    """Кулдаун с учётом купленных сокращений.

    Сокращения не складываются: игрок держит только одно, самое большое.
    Нижняя граница — 1 минута, чтобы получение не превратилось в
    бесконечный цикл запросов.
    """
    if instant:
        return 0
    return max(60, CLAIM_COOLDOWN_SECONDS - max(0, cooldown_reduction))


def validate() -> None:
    """Проверка критичных настроек при старте."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан. Создайте .env на основе .env.example и укажите токен бота."
        )
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS пуст. Укажите хотя бы один Telegram ID администратора.")
    if DONATE_MIN_STARS < 1 or DONATE_MAX_STARS > 1000:
        raise RuntimeError(
            "Донаты: границы должны быть в пределах Telegram Stars (1..1000)."
        )
    for item in DONATE_SPEC:
        if not DONATE_MIN_STARS <= item["stars"] <= DONATE_MAX_STARS:
            raise RuntimeError(f"Донат {item['id']} вне границ 1..1000.")
    for item_id in [i["id"] for i in DONATE_SPEC]:
        if len([i for i in DONATE_SPEC if i["id"] == item_id]) > 1:
            raise RuntimeError(f"Дубль id платежа в DONATE_SPEC: {item_id}")
    if CLAIM_MAX_AMOUNT < CLAIM_MIN_AMOUNT:
        raise RuntimeError("CLAIM_MAX_AMOUNT меньше CLAIM_MIN_AMOUNT.")
    if COURIER_DELAY_MAX_SECONDS < COURIER_DELAY_MIN_SECONDS:
        raise RuntimeError("COURIER_DELAY_MAX_SECONDS меньше COURIER_DELAY_MIN_SECONDS.")
    if not 0.0 <= COURIER_CHANCE <= 1.0:
        raise RuntimeError("COURIER_CHANCE должен быть в диапазоне 0..1.")
    for upgrade_id, settings in UPGRADES.items():
        if settings["kind"] == "cooldown":
            if settings["value"] >= CLAIM_COOLDOWN_SECONDS:
                raise RuntimeError(
                    f"Апгрейд {upgrade_id} сокращает кулдаун полностью — "
                    "это делает апгрейд instant."
                )
        if not settings.get("admin_only") and settings["cost"] <= 0:
            raise RuntimeError(f"У апгрейда {upgrade_id} не задана цена.")
