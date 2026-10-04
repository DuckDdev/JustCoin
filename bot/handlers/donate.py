"""Донаты звёздами: команда /donate и приём платежа.

По правилам Telegram цифровые товары оплачиваются только в звёздах:
валюта XTR, provider_token пустой. Оплата состоит из двух апдейтов —
сначала pre_checkout_query (надо подтвердить за 10 секунд), затем
successful_payment (только он означает реальную оплату).
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot import messages
from bot.config import DONATE_MAX_STARS, DONATE_MIN_STARS
from bot.handlers.common import safe_answer, touch_user
from bot.services.donate_service import (
    STARS_CURRENCY,
    STARS_PROVIDER_TOKEN,
    DonateService,
)

logger = logging.getLogger(__name__)

# Префикс кнопок быстрого доната: don:<id>.
DONATE_PREFIX = "donate:buy:"

# Понятная игроку причина отказа в оплате: Telegram показывает её
# прямо в форме оплаты.
REJECT_REASONS = {
    "unknown": "Счёт не найден. Отправьте /donate заново",
    "amount": "Неверная сумма доната",
    "duplicate": "Этот счёт уже оплачен",
    "database": "Не удалось проверить платёж",
}


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="donate")
    router.message.register(cmd_donate, Command("donate", "donate_stars"))
    router.callback_query.register(
        pick_callback, F.data.startswith(DONATE_PREFIX)
    )
    router.pre_checkout_query.register(pre_checkout)
    router.message.register(payment_received, F.successful_payment)
    return router


def _price_label(stars: int) -> str:
    return f"{stars:,}".replace(",", " ")


def catalog_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"⭐️ {item['stars']}",
            callback_data=f"{DONATE_PREFIX}{item['id']}",
        )]
        for item in DonateService.catalog()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_invoice(message: Message, plan, tg_user,
                        data: dict[str, Any]) -> bool:
    """Отправляет инвойс и заводит платёж в базе."""
    donate: DonateService = data["donate"]
    bot = data["bot"]

    # Платёж заводим ДО отправки: если Telegram ответит успешной
    # оплатой, запись обязана существовать.
    if not await donate.register(plan.payload, tg_user.id,
                                 tg_user.username, plan.stars):
        logger.info("Дубль payload доната %s", plan.payload)
        return False

    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title=plan.title,
            description=plan.description,
            payload=plan.payload,
            currency=STARS_CURRENCY,
            prices=[LabeledPrice(label=plan.label, amount=plan.stars)],
            provider_token=STARS_PROVIDER_TOKEN,
        )
    except Exception as exc:  # noqa: BLE001 - счёт мог не дойти
        logger.info("Не удалось отправить счёт на %s: %s", plan.stars, exc)
        await donate.reject(plan.payload, str(exc))
        await safe_answer(
            message, messages.DONATE_SENDING_FAILED.format(error=exc)
        )
        return False
    return True


async def cmd_donate(message: Message, command, **data: Any) -> None:
    """`/donate` — каталог сумм, `/donate 50` — счёт на 50 звёзд."""
    await offer(message, (command.args or "").strip(),
                message.from_user, data)


async def offer(message: Message, argument: str, tg_user, data: dict[str, Any]) -> None:
    """Показывает витрину или высылает счёт.

    tg_user передаётся отдельно: у сообщения, к которому привязан
    callback, from_user часто пуст, и оплату пришлось бы вести
    на никого.
    """
    await touch_user(tg_user, data)

    if not argument:
        await safe_answer(
            message,
            "\\n".join([
                messages.DONATE_HEADER,
                messages.DONATE_INTRO,
                messages.DONATE_CATALOG.format(
                    catalog=DonateService.catalog_text()
                ),
                messages.DONATE_CUSTOM_HINT.format(
                    min=DONATE_MIN_STARS, max=DONATE_MAX_STARS
                ),
            ]),
            reply_markup=catalog_keyboard(),
        )
        return

    plan = DonateService.resolve(argument)
    if plan is None:
        await safe_answer(
            message,
            messages.DONATE_BAD_AMOUNT.format(
                min=DONATE_MIN_STARS, max=DONATE_MAX_STARS
            ),
            reply_markup=catalog_keyboard(),
        )
        return

    if await _send_invoice(message, plan, tg_user, data):
        await safe_answer(
            message, messages.DONATE_INVOICE_SENT.format(stars=plan.stars)
        )


async def pick_callback(callback: CallbackQuery, **data: Any) -> None:
    """Кнопка каталога: отправляем счёт на выбранную сумму."""
    item_id = (callback.data or "")[len(DONATE_PREFIX):]
    item = next(
        (i for i in DonateService.catalog() if i["id"] == item_id), None
    )
    if item is None:
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return

    plan = DonateService.resolve(item_id)
    if plan is None:  # pragma: no cover - рассинхрон каталога и конфига
        await callback.answer(messages.BOT_ERROR, show_alert=True)
        return

    await offer(callback.message, item_id, callback.from_user, data)
    await callback.answer()


async def pre_checkout(query: PreCheckoutQuery, **data: Any) -> None:
    """Подтверждение заказа. Telegram ждёт ответ ровно 10 секунд."""
    donate: DonateService = data["donate"]
    payload = query.invoice_payload
    reason = None

    # Сумму из апдейта не берём: доверяем только своей базе, иначе
    # игрок подменил бы цену в самом счёте.
    record = await donate.by_payload(payload)
    if record is None:
        reason = REJECT_REASONS["unknown"]
    elif record["stars"] != query.total_amount:
        reason = REJECT_REASONS["amount"]
    elif record["status"] == "paid":
        reason = REJECT_REASONS["duplicate"]
    elif not await donate.approve(payload):
        reason = REJECT_REASONS["database"]

    if reason:
        await donate.reject(payload, reason)
        await query.answer(ok=False, error_message=reason)
        logger.info("Предоплата отклонена (%s): %s", payload, reason)
        return

    await query.answer(ok=True)
    logger.debug("Предоплата подтверждена: %s", payload)


async def payment_received(message: Message, **data: Any) -> None:
    """Успешная оплата: зачисляем и благодарим."""
    donate: DonateService = data["donate"]
    payment = message.successful_payment
    payload = payment.invoice_payload

    charged = await donate.complete(
        payload, payment.telegram_payment_charge_id,
        getattr(payment, "provider_payment_charge_id", None),
    )
    if not charged:
        await safe_answer(message, messages.DONATE_ALREADY_PAID)
        return

    record = await donate.by_payload(payload)
    stars = record["stars"] if record else payment.total_amount
    await safe_answer(message, messages.DONATE_SUCCESS.format(stars=_price_label(stars)))

    # Администраторам — уведомление, чтобы видеть поддержку.
    await _notify_admins(bot=data["bot"], stars=stars, user=message.from_user)


async def _notify_admins(bot, stars: int, user) -> None:
    from bot.config import ADMIN_IDS

    text = messages.DONATE_THANKS_ADMIN.format(
        stars=_price_label(stars), name=user.first_name or user.username or user.id
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:  # noqa: BLE001 - админ мог заблокировать бота
            logger.debug("Не удалось уведомить админа %s: %s", admin_id, exc)