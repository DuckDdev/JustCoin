"""Приём донатов в Telegram Stars.

По правилам Telegram цифровые товары и услуги принимаются только в
звёздах: валюта XTR, provider_token пустой. Оплата идёт в два шага —
сначала бот шлёт инвойс и ждёт pre_checkout_query, потом получает
successful_payment. Только второй апдейт означает реальную оплату.

Идентификатор payload уникален: он защищает от двойного начисления,
если Telegram пришлёт один и тот же платёж дважды.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from bot.config import DONATE_MAX_STARS, DONATE_MIN_STARS, DONATE_SPEC
from bot.db.database import Database
from bot.db.repository import DonationRepository
from bot.utils.formatters import utcnow

logger = logging.getLogger(__name__)


# Валюта цифровых товаров по требованиям Telegram.
STARS_CURRENCY = "XTR"

# provider_token для звёзд не нужен, но метод его требует.
STARS_PROVIDER_TOKEN = ""


@dataclass(slots=True)
class DonationPlan:
    """Готовый к отправке инвойс: что показать и сколько списать."""
    payload: str
    title: str
    description: str
    stars: int
    label: str


class DonateService:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.donations = DonationRepository(database)

    # --- каталог -----------------------------------------------------------
    @staticmethod
    def catalog() -> list[dict]:
        return DONATE_SPEC

    @staticmethod
    def catalog_text() -> str:
        lines = []
        for item in DONATE_SPEC:
            lines.append(f"{item['stars']} ⭐️ — {item['description']}")
        return "\n".join(lines)

    # --- разбор запроса ----------------------------------------------------
    @staticmethod
    def resolve(argument: str) -> DonationPlan | None:
        """Инвойс по аргументу команды.

        Аргумент может быть числом звёзд («50») или кодом позиции
        («donate_100»). None — аргумент пустой или недопустимый.
        """
        text = (argument or "").strip()
        if not text:
            return None
        for item in DONATE_SPEC:
            if text.lower() == item["id"]:
                return _plan(item)

        stars = parse_stars(text)
        if stars is None:
            return None
        return DonateService._custom_plan(stars)

    @staticmethod
    def _custom_plan(stars: int) -> DonationPlan | None:
        """Произвольная сумма: берём ближайшую позицию каталога как
        образец и подставляем свою сумму."""
        template = DONATE_SPEC[0]
        return DonationPlan(
            payload=build_payload("custom", stars),
            title=template["title"],
            description=f"Донат {stars} ⭐️ разработчику JustCoin",
            stars=stars,
            label=f"{stars} ⭐️",
        )

    # --- жизненный цикл платежа --------------------------------------------
    async def register(self, payload: str, user_id: int, username: str | None,
                       stars: int) -> bool:
        """Заводит платёж до предоплаты. False — такой payload уже был."""
        return await self.donations.register(payload, user_id, username, stars)

    async def approve(self, payload: str) -> bool:
        """Предоплата подтверждена: платёж разрешено проводить."""
        return await self.donations.set_status(payload, "approved")

    async def reject(self, payload: str, reason: str) -> bool:
        """Предоплата отклонена — платёж не состоится."""
        return await self.donations.set_status(payload, "cancelled", reason)

    async def complete(self, payload: str, charge_id: str,
                       provider_charge_id: str | None = None) -> bool:
        """Платёж прошёл. False — уже учтён (повторный апдейт)."""
        done = await self.donations.complete(payload, charge_id,
                                             provider_charge_id)
        if done:
            logger.info("Донат %s зачислен", payload)
        else:
            logger.info("Повторный successful_payment для %s игнорирован", payload)
        return done

    async def by_payload(self, payload: str) -> dict | None:
        return await self.donations.by_payload(payload)

    async def total_stars(self) -> int:
        return await self.donations.total_stars()

    async def refund(self, charge_id: str) -> bool:
        """Помечает платёж возвращённым — сам возврат делает Telegram
        через refundStarPayment, нам нужен лишь след."""
        return await self.donations.refund(charge_id)


def _plan(item: dict) -> DonationPlan:
    return DonationPlan(
        payload=build_payload(item["id"], item["stars"]),
        title=item["title"],
        description=item["description"],
        stars=item["stars"],
        label=f"{item['stars']} ⭐️",
    )


def build_payload(source: str, stars: int) -> str:
    """Идентификатор заказа: он же служит защитой от двойной оплаты.

    Часть со случайным суффиксом: два чека, оформленные в одну
    миллисекунду, должны быть разными заказами, иначе второй
    платёж молча присоединился бы к первому.
    """
    return f"{source}:{stars}:{int(utcnow().timestamp() * 1000)}:{uuid.uuid4().hex[:8]}"


def parse_stars(text: str) -> int | None:
    """Число звёзд из строки. None — не число или вне границ 1..1000."""
    cleaned = (text or "").strip().replace("⭐️", "").replace("⭐", "")
    cleaned = cleaned.replace(" ", "").lstrip("xх")
    if not cleaned.isdigit():
        return None
    stars = int(cleaned)
    if not DONATE_MIN_STARS <= stars <= DONATE_MAX_STARS:
        return None
    return stars