"""Форматирование чисел, времени и склонение существительных."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from bot.config import CURRENCY_EMOJI

_HOUR_FORMS = ("час", "часа", "часов")
_MINUTE_FORMS = ("минута", "минуты", "минут")
_SECOND_FORMS = ("секунда", "секунды", "секунд")


def plural_forms(count: int, forms: tuple[str, str, str]) -> str:
    """Выбор правильной формы слова по числу.

    1 час, 2 часа, 5 часов, 11 часов, 21 час, 22 часа, 25 часов...
    """
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return forms[0]
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return forms[1]
    return forms[2]


def hours_word(count: int) -> str:
    return plural_forms(count, _HOUR_FORMS)


def minutes_word(count: int) -> str:
    return plural_forms(count, _MINUTE_FORMS)


def seconds_word(count: int) -> str:
    return plural_forms(count, _SECOND_FORMS)


def format_duration(total_seconds: int) -> str:
    """Человекочитаемый остаток времени: «3 часа 00 минут 50 секунд»."""
    total_seconds = max(0, int(total_seconds))
    hours, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (
        f"{hours} {hours_word(hours)} "
        f"{minutes:02d} {minutes_word(minutes)} "
        f"{seconds:02d} {seconds_word(seconds)}"
    )


def format_coins(amount: int | float) -> str:
    """Единый формат суммы валюты: «3 🪙»."""
    return f"{int(amount)} {CURRENCY_EMOJI}"


def random_int_between(minimum: int, maximum: int) -> int:
    if maximum <= minimum:
        return minimum
    return random.randint(minimum, maximum)


def random_float() -> float:
    return random.random()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_timestamp(value: datetime | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def from_timestamp(value: datetime | int | float | None) -> datetime | None:
    """Принимает и UNIX-секунды, и уже готовый datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def format_date(value: datetime | int | float | None) -> str:
    """Дата регистрации в формате «29.09.2026»."""
    dt = from_timestamp(value)
    if dt is None:
        return "—"
    return dt.strftime("%d.%m.%Y")


def to_epoch(value: datetime | int | float | None) -> int:
    """UNIX-секунды из datetime или числа — для запросов к БД."""
    if value is None:
        return 0
    if isinstance(value, datetime):
        return int(to_timestamp(value))
    return int(value)


def display_name(username: str | None, first_name: str | None,
                 last_name: str | None = None) -> str:
    """Имя для топа и карточек: @username, если есть, иначе Имя Фамилия."""
    if username:
        return f"@{username}"
    parts = [p for p in (first_name, last_name) if p]
    if parts:
        return " ".join(parts)
    return "Игрок"
