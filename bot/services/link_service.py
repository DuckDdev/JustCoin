"""Ссылки: общие для бота и привязанные к товарам.

Ссылки настраиваются в админ-панели и доступны всем игрокам. Название
у ссылки либо своё (кастомное), либо берётся из названия сайта, если
админ указал только домен.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from bot.db.database import Database
from bot.db.repository import LinkRepository

logger = logging.getLogger(__name__)

# Где показываются общие ссылки: в /help и в меню бота.
SECTION_HELP = "help"

# Названия, которые подставляются, если админ не придумал своё.
KNOWN_SITES: dict[str, str] = {
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "discord.gg": "Discord",
    "discord.com": "Discord",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "github.com": "GitHub",
    "vk.com": "ВКонтакте",
    "boosty.to": "Boosty",
    "donationalerts.com": "DonationAlerts",
    "justcoin": "JustCoin",
}


@dataclass(slots=True)
class LinkResult:
    ok: bool
    title: str = ""
    url: str = ""
    detail: str = ""


# Схемы, которые Telegram открывает как ссылку. Всё остальное
# (javascript:, data:, file:) — это попытка вставить свой код.
ALLOWED_SCHEMES = ("http", "https", "tg")


def normalize_url(raw: str) -> str | None:
    """Приводит ссылку к виду, который понимает Telegram.

    Telegram открывает только http(s) и tg-ссылки; без схемы адрес
    считается обычным текстом, поэтому «example.com» дополняем сами.
    None — адрес непригоден.
    """
    text = (raw or "").strip()
    if not text:
        return None
    # Берём первый пробельный кусок: «Закат example.com/shop» уже
    # разбито вызывающим кодом, лишние слова тут не нужны.
    text = text.split()[0]
    if not text:
        return None

    # @username — привычная ссылка на Telegram-профиль.
    if text.startswith("@") and len(text) > 1:
        handle = text[1:]
        if not handle.replace("_", "").isalnum():
            return None
        return f"https://t.me/{handle}"

    # Схема, отличная от пустой, должна быть разрешённой. Иначе
    # «javascript:…» молча превратился бы в https-ссылку.
    scheme = ""
    if "://" in text:
        scheme, _, rest = text.partition("://")
        text = rest
        scheme = scheme.lower()
    elif text.lower().startswith("tg:"):
        scheme, text = "tg", text[3:].lstrip("/")

    if scheme and scheme not in ALLOWED_SCHEMES:
        return None

    if scheme == "tg":
        return f"tg://{text}" if text else None

    # Схему мы выше срезали, теперь собираем адрес заново: без неё
    # Telegram считает ссылку обычным текстом.
    url = f"{scheme or 'https'}://{text}"
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.split("@")[-1].split(":")[0]
    if not host or "." not in host:
        # Без точки это опечатка («привет»), а не адрес. localhost
        # оставляем — им пользуются при локальном запуске.
        if host != "localhost":
            return None
    return url


def suggest_title(url: str, custom: str = "") -> str:
    """Название ссылки: своё, иначе имя сайта, иначе сам домен."""
    if (custom or "").strip():
        return custom.strip()[:40]
    try:
        parts = urlsplit(url)
        host = parts.netloc.lower().removeprefix("www.")
        if parts.scheme == "tg":
            # У tg-ссылок домен лежит в запросе: tg://resolve?domain=durov.
            # В пути может лежать служебное слово «resolve», оно названием
            # быть не может.
            domain = dict(
                pair.split("=", 1) for pair in parts.query.split("&") if "=" in pair
            ).get("domain", "")
            if domain and host in ("", "resolve"):
                host = domain.lower().removeprefix("www.")
    except ValueError:  # pragma: no cover - normalize_url уже проверил
        host = ""
    if not host:
        return url[:40]
    for domain, name in KNOWN_SITES.items():
        if host == domain or host.endswith(f".{domain}"):
            return name
    return host


class LinkService:
    """Общие и товарные ссылки. Валидация — в статических методах,
    чтобы её могли проверять и тесты, и панель."""

    def __init__(self, database: Database) -> None:
        self.db = database
        self.links = LinkRepository(database)

    # --- разбор ввода ------------------------------------------------------
    @staticmethod
    def parse(raw: str) -> LinkResult:
        """Разбирает «Название ссылка» или одну ссылку.

        Формат: сначала название, потом адрес, через пробел. Без
        названия подставляется имя известного сайта.
        """
        parts = (raw or "").strip().split()
        if not parts:
            return LinkResult(ok=False, detail="Пустая ссылка")

        url = normalize_url(parts[-1])
        if url is None:
            return LinkResult(ok=False, detail="Не похоже на ссылку")

        custom = " ".join(parts[:-1]).strip()
        title = suggest_title(url, custom)
        if not title:
            return LinkResult(ok=False, detail="Нужно название и ссылка")
        return LinkResult(ok=True, title=title, url=url)

    # --- общие ссылки ------------------------------------------------------
    async def bot_links(self, section: str = SECTION_HELP) -> list[dict]:
        """Ссылки, которые видит игрок: только активные."""
        return await self.links.bot_links(section)

    async def all_bot_links(self, section: str = SECTION_HELP) -> list[dict]:
        """Все ссылки раздела, включая выключенные — для панели."""
        return [link for link in await self.links.all_bot_links()
                if link["section"] == section]

    async def add_bot_link(self, raw: str,
                           section: str = SECTION_HELP) -> LinkResult:
        parsed = self.parse(raw)
        if not parsed.ok:
            return parsed
        link_id = await self.links.add_bot_link(parsed.title, parsed.url, section)
        logger.info("Добавлена ссылка «%s» → %s", parsed.title, parsed.url)
        return LinkResult(ok=True, title=parsed.title, url=parsed.url,
                          detail=str(link_id))

    async def toggle_bot_link(self, link_id: int) -> bool:
        """Включает или выключает ссылку. False — ссылки нет."""
        current = await self.db.fetch_one(
            "SELECT active FROM bot_links WHERE id = ?", (link_id,)
        )
        if current is None:
            return False
        await self.links.set_bot_link_active(
            link_id, not bool(current["active"])
        )
        return True

    async def delete_bot_link(self, link_id: int) -> bool:
        return await self.links.delete_bot_link(link_id)

    # --- ссылки товаров ----------------------------------------------------
    async def product_links(self, scope: str, code: str) -> list[dict]:
        return await self.links.product_links(scope, code)

    async def product_links_bulk(self, scope: str,
                                 codes: list[str]) -> dict[str, list[dict]]:
        return await self.links.product_links_bulk(scope, codes)

    async def add_product_link(self, scope: str, code: str,
                               raw: str) -> LinkResult:
        parsed = self.parse(raw)
        if not parsed.ok:
            return parsed
        link_id = await self.links.add_product_link(
            scope, code, parsed.title, parsed.url
        )
        logger.info("Ссылка «%s» добавлена товару %s/%s",
                    parsed.title, scope, code)
        return LinkResult(ok=True, title=parsed.title, url=parsed.url,
                          detail=str(link_id))

    async def delete_product_link(self, link_id: int) -> bool:
        return await self.links.delete_product_link(link_id)
