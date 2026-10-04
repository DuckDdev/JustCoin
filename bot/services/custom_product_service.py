"""Товары, созданные админом: кастомные апгрейды и темы.

Обычный каталог лежит в config.py, эти товары живут в базе. Покупатель
видит их наравне со штатными, а палитра кастомной темы достраивается
из одного базового цвета по тем же правилам, что и у встроенных тем.

Типы апгрейдов:
    multiplier — множитель начисления (2, 3, 5, 10...)
    cooldown   — сокращение кулдауна в секундах
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bot.config import THEMES, CardTheme
from bot.db.database import Database
from bot.db.repository import CustomShopRepository, UserRepository
from bot.models import User

logger = logging.getLogger(__name__)

_SLUG = re.compile(r"[^a-z0-9]+")
HEX_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6})$")

# Префиксы, по которым в объединённом каталоге отличают свои товары.
CUSTOM_PREFIX = "product:"


def make_code(title: str) -> str:
    """Код товара из названия: «Золотой закат» -> «zolotoy-zakat»."""
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
        "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
        "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
        "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    result = []
    for char in (title or "").lower():
        result.append(translit.get(char, char))
    slug = _SLUG.sub("-", "".join(result)).strip("-")
    return slug[:32] or "custom"


# Описание попадает в текст, который бот показывает игроку: длинное или
# пустое ломает вёрстку витрины.
DESCRIPTION_MAX = 120


def clean_description(raw: str | None) -> str | None:
    """Обрезает описание и убирает пустые. None — описания нет."""
    text = " ".join((raw or "").split())
    if not text:
        return None
    if len(text) > DESCRIPTION_MAX:
        return text[: DESCRIPTION_MAX - 1].rstrip() + "…"
    return text


def parse_hex(value: str) -> tuple[int, int, int] | None:
    match = HEX_COLOR.match((value or "").strip())
    if not match:
        return None
    raw = match.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _mix(color: tuple[int, int, int], factor: float,
        toward: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    """Смешивает цвет: factor < 1 — темнее, > 1 — светлее (к toward)."""
    return tuple(
        max(0, min(255, int(c * factor + t * (1 - factor) if factor <= 1
                            else c + (255 - c) * (factor - 1))))
        for c, t in zip(color, toward)
    )


def build_theme(theme_id: str, title: str, base: tuple[int, int, int],
                cost: int) -> CardTheme:
    """Строит полную палитру темы из одного базового цвета.

    theme_id обязателен: по нему карточка узнаёт, какая тема применена,
    а панель — под каким кодом товар доступен.
    """
    # Фон — сильно затемнённый базовый, чтобы текст оставался читаемым.
    bg_top = _mix(base, 0.16)
    bg_bottom = _mix(base, 0.34)
    glow = _mix(base, 0.75)
    accent = base
    accent_soft = _mix(base, 1.25)
    jarvis = _mix(base, 1.1, toward=(90, 210, 255))
    jarvis_soft = _mix(base, 1.3, toward=(170, 235, 255))
    return CardTheme(
        id=theme_id, title=title, cost=cost,
        bg_top=bg_top, bg_bottom=bg_bottom, glow=glow,
        accent=accent, accent_soft=accent_soft,
        jarvis=jarvis, jarvis_soft=jarvis_soft,
        panel=(*_mix(base, 1.0), 22), panel_edge=(*_mix(base, 1.0), 56),
    )


@dataclass(slots=True)
class CreateResult:
    ok: bool
    code: str = ""
    title: str = ""
    cost: int = 0
    detail: str = ""


class CustomProductService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.shop = CustomShopRepository(database)
        self.users = UserRepository(database)

    # --- чтение ------------------------------------------------------------
    async def upgrades(self) -> list[dict]:
        return await self.shop.list_upgrades()

    async def themes(self) -> list[dict]:
        return await self.shop.list_themes()

    async def owned_product(self, user: User, code: str) -> bool:
        row = await self.db.fetch_one(
            "SELECT 1 FROM user_themes WHERE user_id = ? AND theme = ?",
            (user.user_id, f"{CUSTOM_PREFIX}{code}"),
        )
        return row is not None

    # --- создание ----------------------------------------------------------
    async def create_upgrade(self, title: str, cost: int, kind: str,
                             value: int, description: str | None = None,
                             code: str | None = None) -> CreateResult:
        title = (title or "").strip()
        if not title:
            return CreateResult(ok=False, detail="Пустое название")
        if cost <= 0:
            return CreateResult(ok=False, detail="Цена должна быть больше нуля")
        if kind not in ("multiplier", "cooldown"):
            return CreateResult(ok=False, detail=f"Неизвестный тип: {kind}")
        if value <= 0:
            return CreateResult(ok=False, detail="Значение должно быть больше нуля")

        final_code = (code or make_code(title)).lower()[:32]
        created = await self.shop.create_upgrade(
            final_code, title, clean_description(description),
            kind, int(value), int(cost),
        )
        if created is None:
            return CreateResult(ok=False, code=final_code, detail="Код уже занят")
        logger.info("Создан апгрейд %s (%s, %s, %s)",
                    final_code, title, kind, value)
        return CreateResult(ok=True, code=final_code, title=title, cost=cost,
                            detail=f"{kind}={value}")

    async def create_theme(self, title: str, cost: int,
                           base_color: str,
                           description: str | None = None) -> CreateResult:
        title = (title or "").strip()
        if not title:
            return CreateResult(ok=False, detail="Пустое название")
        if cost <= 0:
            return CreateResult(ok=False, detail="Цена должна быть больше нуля")
        rgb = parse_hex(base_color)
        if rgb is None:
            return CreateResult(ok=False, detail="Цвет должен быть вида #RRGGBB")

        final_code = make_code(title)[:32]
        created = await self.shop.create_theme(
            final_code, title, int(cost), base_color.strip().upper(),
            clean_description(description),
        )
        if created is None:
            return CreateResult(ok=False, code=final_code, detail="Код уже занят")
        logger.info("Создана тема %s (%s, #%s)", final_code, title, base_color)
        return CreateResult(ok=True, code=final_code, title=title, cost=cost,
                            detail=base_color.upper())

    # --- удаление ----------------------------------------------------------
    async def delete_upgrade(self, code: str) -> bool:
        return await self.shop.delete_upgrade(code.lower())

    async def delete_theme(self, code: str) -> bool:
        return await self.shop.delete_theme(code.lower())

    # --- выдача ------------------------------------------------------------
    async def grant(self, user: User, code: str) -> bool:
        """Выдаёт товар бесплатно и сразу применяет к игроку."""
        upgrades = {u["code"]: u for u in await self.upgrades()}
        themes = {t["code"]: t for t in await self.themes()}

        if code in upgrades:
            item = upgrades[code]
            await self.shop.grant_product(user.user_id, code)
            async with self.db.transaction() as tx:
                if item["kind"] == "multiplier":
                    await UserRepository.set_multiplier_in_tx(
                        tx, user.user_id, item["value"]
                    )
                else:
                    await UserRepository.set_cooldown_reduction_in_tx(
                        tx, user.user_id, item["value"]
                    )
            return True

        if code in themes:
            await self.shop.grant_product(user.user_id, code)
            await self.db.execute(
                "INSERT OR IGNORE INTO user_themes (user_id, theme, bought_at) "
                "VALUES (?, ?, ?)",
                (user.user_id, code, 0),
            )
            async with self.db.transaction() as tx:
                await UserRepository.set_theme_in_tx(tx, user.user_id, code)
            return True
        return False


def all_theme_ids() -> set[str]:
    """Коды всех тем: встроенные и кастомные (без обращения к БД)."""
    return set(THEMES)
