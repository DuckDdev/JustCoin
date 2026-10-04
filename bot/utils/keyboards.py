"""Клавиатуры интерфейса: нижнее меню и inline-панель.

Пользователь просил интерфейс кнопками, поэтому в личке висит нижнее
меню, а под ответами — inline-кнопки быстрого доступа. Отдельная
кнопка «Меню» нужна, чтобы вернуть меню из любой точки: уехавшему
вглубь чата пользователю некуда нажать.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot import messages

# Тексты нижних кнопок. Telegram отправляет значение кнопки как текст
# сообщения, поэтому здесь не команда вида «/jcoin», а подпись —
# маршрут сопоставляется в handlers/buttons.py.
MAIN_BUTTONS: list[KeyboardButton] = [
    KeyboardButton(text=messages.BTN_CLAIM),
    KeyboardButton(text=messages.BTN_TOP),
    KeyboardButton(text=messages.BTN_CARD),
    KeyboardButton(text=messages.BTN_STATS_TEXT),
    KeyboardButton(text=messages.BTN_DONATE),
    KeyboardButton(text=messages.BTN_HELP),
]

# Что означает нажатие нижней кнопки.
BUTTON_ROUTES: dict[str, str] = {
    messages.BTN_CLAIM: "/jcoin",
    messages.BTN_TOP: "/jtop",
    messages.BTN_CARD: "/jstats",
    messages.BTN_STATS_TEXT: "/textstats",
    messages.BTN_DONATE: "/donate",
    messages.BTN_HELP: "/help",
}

MENU_BUTTON_TEXT = "📱 Меню"

# Одноразовое скрытие меню: после /menu обычное меню уже не нужно.
HIDE_MARKER = "⌨️ Скрыть меню"


def main_keyboard() -> ReplyKeyboardMarkup:
    """Нижнее меню в личке: основные кнопки и скрытие."""
    rows = [MAIN_BUTTONS[i:i + 2] for i in range(0, len(MAIN_BUTTONS), 2)]
    rows.append([KeyboardButton(text=HIDE_MARKER)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def hide_keyboard() -> ReplyKeyboardMarkup:
    """После скрытия остаётся одна кнопка возврата."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MENU_BUTTON_TEXT)]], resize_keyboard=True
    )


def menu_button_markup() -> InlineKeyboardMarkup:
    """Кнопка «в меню» под ответом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text=MENU_BUTTON_TEXT, callback_data="menu:help"
        )]]
    )


def route_for(text: str) -> str | None:
    """Команда для нажатой нижней кнопки. None — не наша кнопка."""
    return BUTTON_ROUTES.get((text or "").strip())