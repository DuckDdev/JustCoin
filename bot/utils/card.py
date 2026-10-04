"""Генерация карточки статистики игрока (Pillow, 900x500).

Особенности:
  * Pillow не рисует цветные эмодзи, поэтому в тексте карточки их нет,
    а 🪙 подставляется картинкой из assets/coin.png. Все «иконки» на
    карточке нарисованы примитивами.
  * Полупрозрачные слои вклеиваются через alpha_composite: ImageDraw по
    RGBA-изображению ЗАМЕНЯЕТ альфу, а не смешивает цвета.
  * Карточка собирается в памяти (BytesIO) и в отдельном потоке.
  * Блок Jarvis рисуется только для игроков, открывших секретную часть;
    в нём перечислены купленные апгрейды.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot import config
from bot.config import ASSETS_DIR, FONTS_DIR, CardTheme

logger = logging.getLogger(__name__)

CARD_WIDTH = 900
CARD_HEIGHT = 500
CORNER_RADIUS = 32
MARGIN = 44
CONTENT_WIDTH = CARD_WIDTH - 2 * MARGIN

# Базовые цвета текста не зависят от темы: на любом фоне они читаются.
TEXT_PRIMARY = (245, 244, 252)
TEXT_SECONDARY = (168, 164, 196)

# Палитра карточки задаётся темой игрока (config.THEMES). Модуль не должен
# зависеть от services, поэтому берём палитры прямо из конфига.
DEFAULT_THEME = config.THEMES["default"]

# Имена констант оставлены для читаемости: внутри рендера палитра
# достаётся из data.theme, а эти — значения темы по умолчанию.
PANEL = DEFAULT_THEME.panel
PANEL_EDGE = DEFAULT_THEME.panel_edge
ACCENT = DEFAULT_THEME.accent
JARVIS = DEFAULT_THEME.jarvis

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_FONT_FILES = {
    "regular": ["Montserrat.ttf", "DejaVuSans.ttf"],
    "bold": ["Montserrat.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"],
}


def _font(weight: str, size: int):
    """Шрифт с кириллицей из assets/fonts, с кэшем и настройкой веса."""
    key = (weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    chosen: str | None = None
    for name in _FONT_FILES.get(weight, _FONT_FILES["regular"]):
        candidate = FONTS_DIR / name
        if candidate.exists():
            chosen = str(candidate)
            break
    if chosen is None:  # pragma: no cover - защита от отсутствия ассетов
        font = ImageFont.load_default(size=size)
    else:
        font = ImageFont.truetype(chosen, size)
        try:
            for axis in font.get_variation_axes():
                if axis["name"] == b"Weight":
                    font.set_variation_by_axes([700 if weight == "bold" else 450])
        except (OSError, AttributeError):
            pass
    _FONT_CACHE[key] = font
    return font


@dataclass(slots=True)
class CardData:
    name: str
    username: str | None
    balance: int
    place: int
    total: int
    total_earned: int
    claims_count: int
    packages_count: int
    registered: str
    avatar: bytes | None = None
    # Jarvis-блок виден только разблокированным игрокам.
    jarvis_unlocked: bool = False
    jarvis_coins: int = 0
    has_upgrade: bool = False
    # Краткий список купленных апгрейдов, например "x3, кулдаун −1 час".
    upgrades: str = "нет"
    # Человеческое имя (first_name + last_name) для шапки карточки.
    # Если пустое, используется name.
    display_name: str = ""
    # Палитра карточки: тема игрока из config.THEMES.
    theme: CardTheme = DEFAULT_THEME
    # Бейдж статуса (админ/создатель/донатор). Пусто — бейдж не рисуется.
    status_label: str = ""
    status_color: tuple[int, int, int] | None = None


def palette(data: CardData) -> CardTheme:
    """Палитра карточки с запасным вариантом.

    Темы, созданные админом, в THEMES не лежат — их палитра строится
    из базового цвета, поэтому проверяем не наличие в каталоге, а
    целостность самой палитры: без неё рендер упал бы.
    """
    theme = getattr(data, "theme", None) or DEFAULT_THEME
    needed = ("bg_top", "bg_bottom", "glow", "accent", "accent_soft",
              "jarvis", "jarvis_soft", "panel", "panel_edge")
    if all(hasattr(theme, name) for name in needed):
        return theme
    logger.warning("Некорректная тема карточки, беру базовую: %r", theme)
    return DEFAULT_THEME


# --- вспомогательные примитивы ------------------------------------------------
def _blank() -> Image.Image:
    return Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))


def _coin_badge(size: int) -> Image.Image:
    """Иконка монеты, приведённая к нужному размеру."""
    path = ASSETS_DIR / "coin.png"
    if not path.exists():  # pragma: no cover
        logger.warning("Не найдена иконка монеты: %s", path)
        return Image.new("RGBA", (size, size), (*DEFAULT_THEME.accent, 255))
    with Image.open(path) as source:
        return source.convert("RGBA").resize((size, size), Image.LANCZOS)


def _gradient_background(theme: CardTheme) -> Image.Image:
    """Тёмный вертикальный градиент с мягким свечением сверху."""
    bg = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT))
    draw = ImageDraw.Draw(bg)
    for y in range(CARD_HEIGHT):
        t = y / (CARD_HEIGHT - 1)
        color = tuple(
            int(theme.bg_top[i] + (theme.bg_bottom[i] - theme.bg_top[i]) * t)
            for i in range(3)
        )
        draw.line([(0, y), (CARD_WIDTH, y)], fill=(*color, 255))

    glow = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [-180, -320, CARD_WIDTH + 180, 300], fill=(*theme.glow, 90)
    )
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(radius=110)))
    return bg


def _rounded_panel(
    size: tuple[int, int], radius: int = 20,
    fill: tuple[int, int, int, int] = PANEL,
    edge: tuple[int, int, int, int] = PANEL_EDGE,
) -> Image.Image:
    panel = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1],
        radius=radius, fill=fill, outline=edge, width=1,
    )
    return panel


def _width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Обрезает строку с многоточием, если она не влезает в блок."""
    if _width(draw, text, font) <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high) // 2
        if _width(draw, text[:mid] + "…", font) <= max_width:
            low = mid + 1
        else:
            high = mid
    return text[: max(0, low - 1)].rstrip() + "…"


def _shadow_text(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font,
    color=TEXT_PRIMARY, offset: tuple[int, int] = (2, 3), shadow_alpha: int = 110,
) -> None:
    """Текст с мягкой тенью — читается на любом фоне."""
    x, y = xy
    draw.text((x + offset[0], y + offset[1]), text, font=font,
              fill=(0, 0, 0, shadow_alpha))
    draw.text(xy, text, font=font, fill=(*color, 255))


def _draw_avatar(canvas: Image.Image, data: CardData, x: int, y: int,
                 size: int, theme: CardTheme) -> None:
    """Аватарка или заглушка с инициалом."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    circle_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle_mask).ellipse([0, 0, size - 1, size - 1], fill=255)

    avatar: Image.Image | None = None
    if data.avatar:
        try:
            with Image.open(io.BytesIO(data.avatar)) as source:
                avatar = source.convert("RGBA").resize((size, size), Image.LANCZOS)
            avatar.putalpha(circle_mask)
        except Exception as exc:  # noqa: BLE001 - аватарка может быть битой
            logger.warning("Не удалось прочитать аватар: %s", exc)
            avatar = None

    if avatar is not None:
        layer.alpha_composite(avatar)
        # Telegram отдаёт аватарки с альфой — приводим к RGB, иначе
        # круглая маска срежет полупрозрачные края.
        flat = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        flat.paste(layer.convert("RGB"), (0, 0), circle_mask)
        layer = flat
    else:
        # Заглушка: вертикальный градиент, как у монеты, плюс инициал.
        placeholder = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pd = ImageDraw.Draw(placeholder)
        for row in range(size):
            t = row / max(1, size - 1)
            color = tuple(
                int(theme.bg_top[i] + (theme.glow[i] - theme.bg_top[i]) * t)
                for i in range(3)
            )
            pd.line([(0, row), (size, row)], fill=(*color, 255))
        placeholder.putalpha(circle_mask)
        layer.alpha_composite(placeholder)
        # Инициал рисуем НА СЛОЙ: непрозрачная заглушка иначе его закроет.
        initial = (data.name.strip() or "?")[:1].upper() or "?"
        font = _font("bold", int(size * 0.44))
        ld = ImageDraw.Draw(layer)
        bbox = ld.textbbox((0, 0), initial, font=font)
        ld.text(
            ((size - (bbox[2] - bbox[0])) / 2 - bbox[0],
             (size - (bbox[3] - bbox[1])) / 2 - bbox[1]),
            initial, font=font, fill=(*theme.accent_soft, 255),
        )

    ImageDraw.Draw(layer).ellipse(
        [0, 0, size - 1, size - 1], outline=(*theme.accent, 110), width=2
    )
    canvas.alpha_composite(layer, (x, y))


def _coin_amount(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                 x: int, y: int, amount: int, font, color=TEXT_PRIMARY) -> int:
    """Рисует «120 🪙»: число текстом, монету — картинкой. Возвращает новый x."""
    text = str(amount)
    bbox = draw.textbbox((0, 0), text, font=font)
    _shadow_text(draw, (x, y), text, font, color)
    number_width = bbox[2] - bbox[0]
    icon_size = max(16, int(font.size * 1.12))
    # Центрируем иконку по высоте цифр, а не по em-квадрату шрифта.
    icon_y = y + bbox[1] + ((bbox[3] - bbox[1]) - icon_size) / 2
    canvas.alpha_composite(_coin_badge(icon_size), (x + number_width + 9, int(icon_y)))
    return x + number_width + 9 + icon_size


def _cell(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str,
          value_font, value_color=TEXT_PRIMARY) -> None:
    draw.text((x, y), label, font=_font("regular", 17), fill=(*TEXT_SECONDARY, 255))
    _shadow_text(draw, (x, y + 24), value, value_font, value_color, offset=(1, 2),
                 shadow_alpha=80)


def _coin_cell(canvas: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int,
               label: str, amount: int, value_font) -> None:
    draw.text((x, y), label, font=_font("regular", 17), fill=(*TEXT_SECONDARY, 255))
    _coin_amount(canvas, draw, x, y + 24, amount, value_font)


def _bot_glyph(size: int = 24, color: tuple[int, int, int] = JARVIS) -> Image.Image:
    """Простая иконка «робот» — заменяет эмодзи, который Pillow не рисует."""
    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glyph)
    u = size / 24
    g.rounded_rectangle([3 * u, 6 * u, 21 * u, 20 * u], radius=5 * u,
                        outline=(*color, 255), width=max(1, int(1.8 * u)))
    g.line([(12 * u, 6 * u), (12 * u, 1.5 * u)], fill=(*color, 255),
           width=max(1, int(1.8 * u)))
    g.ellipse([7 * u, 10 * u, 10.5 * u, 13.5 * u], fill=(*color, 255))
    g.ellipse([13.5 * u, 10 * u, 17 * u, 13.5 * u], fill=(*color, 255))
    return glyph


def _bolt_glyph(size: int = 18,
                color: tuple[int, int, int] = JARVIS) -> Image.Image:
    """Молния для статуса апгрейда x2."""
    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glyph)
    g.polygon(
        [(0.55 * size, 0), (0.30 * size, 0.52 * size), (0.48 * size, 0.52 * size),
         (0.38 * size, size), (0.80 * size, 0.40 * size), (0.58 * size, 0.40 * size),
         (0.72 * size, 0)],
        fill=(*color, 255),
    )
    return glyph


def _status_badge(canvas: Image.Image, data: CardData,
                  theme: CardTheme, data_badge_x: int) -> int:
    """Бейдж статуса в правом верхнем углу. Возвращает занятую ширину.

    Статусов может быть не быть вовсе — тогда ничего не рисуется, а
    шапка остаётся ровно такой, как была до появления бейджей.
    """
    label = (data.status_label or "").strip()
    if not label:
        return 0

    color = data.status_color or theme.accent
    font = _font("bold", 17)
    draw = ImageDraw.Draw(canvas)
    padding_x, height = 13, 27
    width = _width(draw, label, font) + padding_x * 2
    # Бейдж стоит на строке «JustCoin» под именем: справа в шапке живёт
    # панель баланса, и бейдж её перекрывал бы.
    x, y = data_badge_x, 110

    badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(badge).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=height // 2,
        fill=(*color, 30), outline=(*color, 150), width=1,
    )
    canvas.alpha_composite(badge, (x, y))
    ImageDraw.Draw(canvas).text(
        (x + padding_x, y + 6), label, font=font, fill=(*color, 255)
    )
    return width


def render_card(data: CardData) -> bytes:
    """Собирает карточку и возвращает PNG в виде байтов (синхронно)."""
    theme = palette(data)
    canvas = _gradient_background(theme)

    # --- шапка: аватар, имя, username ---
    avatar_size = 100
    _draw_avatar(canvas, data, MARGIN, 38, avatar_size, theme)
    text_x = MARGIN + avatar_size + 24
    balance_panel_x = 556
    name_max_width = balance_panel_x - text_x - 24

    name_font = _font("bold", 30)
    sub_font = _font("regular", 18)
    draw = ImageDraw.Draw(canvas)
    # Имя игрока берём из first_name/last_name, а не из username:
    # в шапке это «Алиса», а @username идёт отдельной строкой ниже.
    display = data.display_name or data.name
    name = _ellipsize(draw, display, name_font, name_max_width)
    _shadow_text(draw, (text_x, 44), name, name_font, TEXT_PRIMARY)
    if data.username:
        handle = _ellipsize(draw, f"@{data.username}", sub_font, name_max_width)
        draw.text((text_x, 86), handle, font=sub_font, fill=(*TEXT_SECONDARY, 255))
    brand_font = _font("bold", 17)
    draw.text((text_x, 112), "JustCoin", font=brand_font,
              fill=(*theme.accent, 220))
    _status_badge(canvas, data, theme,
                  text_x + _width(draw, "JustCoin", brand_font) + 14)

    # --- панель баланса справа ---
    canvas.alpha_composite(
        _rounded_panel((300, 104), radius=24,
                       fill=theme.panel, edge=theme.panel_edge),
        (balance_panel_x, 34),
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((balance_panel_x + 24, 50), "БАЛАНС",
              font=_font("regular", 16), fill=(*TEXT_SECONDARY, 255))
    _coin_amount(canvas, draw, balance_panel_x + 24, 74, data.balance,
                 _font("bold", 38), theme.accent)

    # --- панель «место в топе» ---
    canvas.alpha_composite(
        _rounded_panel((CONTENT_WIDTH, 88), radius=22,
                       fill=theme.panel, edge=theme.panel_edge),
        (MARGIN, 164),
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((MARGIN + 24, 180), "МЕСТО В ТОПЕ",
              font=_font("regular", 16), fill=(*TEXT_SECONDARY, 255))
    place_font = _font("bold", 30)
    place_text = f"{data.place} из {data.total}"
    _shadow_text(draw, (MARGIN + 24, 202), place_text, place_font, TEXT_PRIMARY,
                 offset=(1, 2), shadow_alpha=80)
    place_width = _width(draw, place_text, place_font)
    draw.text((MARGIN + 34 + place_width, 214), "по всем чатам",
              font=_font("regular", 17), fill=(*TEXT_SECONDARY, 255))

    # --- панель статистики ---
    stats_top, stats_height = 264, 132
    canvas.alpha_composite(
        _rounded_panel((CONTENT_WIDTH, stats_height), radius=22,
                       fill=theme.panel, edge=theme.panel_edge),
        (MARGIN, stats_top),
    )
    draw = ImageDraw.Draw(canvas)
    value_font = _font("bold", 26)
    row_one = stats_top + 16
    column_w = (CONTENT_WIDTH - 48) // 3
    _coin_cell(canvas, draw, MARGIN + 24, row_one, "Всего заработано",
               data.total_earned, value_font)
    _cell(draw, MARGIN + 24 + column_w, row_one, "Получений",
          str(data.claims_count), value_font)
    _cell(draw, MARGIN + 24 + column_w * 2, row_one, "Посылок получено",
          str(data.packages_count), value_font)
    _cell(draw, MARGIN + 24, row_one + 66, "Дата регистрации",
          data.registered, _font("bold", 22), theme.accent_soft)

    # --- нижний блок ---
    footer_top, footer_height = 408, 76
    if data.jarvis_unlocked:
        # Секретный блок — только для тех, кто открыл магазин апгрейдов.
        canvas.alpha_composite(
            _rounded_panel((CONTENT_WIDTH, footer_height), radius=22,
                           fill=(*theme.jarvis, 26), edge=(*theme.jarvis, 72)),
            (MARGIN, footer_top),
        )
        draw = ImageDraw.Draw(canvas)
        canvas.alpha_composite(_bot_glyph(22, theme.jarvis),
                               (MARGIN + 22, footer_top + 14))
        draw.text((MARGIN + 54, footer_top + 16), "JARVIS",
                  font=_font("bold", 18), fill=(*theme.jarvis, 255))
        coins_label = f"Jarvis-коинов: {data.jarvis_coins}"
        draw.text((MARGIN + 54, footer_top + 42), coins_label,
                  font=_font("regular", 19), fill=(*theme.jarvis_soft, 235))
        status_x = MARGIN + 54 + _width(draw, coins_label, _font("regular", 19)) + 34
        # Список купленных апгрейдов: обрезаем, если не влезает в панель.
        status_text = _ellipsize(draw, data.upgrades, _font("bold", 22),
                                 MARGIN + CONTENT_WIDTH - 40 - status_x)
        status_font = _font("bold", 22)
        if data.has_upgrade:
            draw.text((status_x, footer_top + 20), status_text,
                      font=status_font, fill=(*theme.jarvis, 255))
            canvas.alpha_composite(
                _bolt_glyph(20, theme.jarvis),
                (status_x + _width(draw, status_text, status_font) + 10,
                 footer_top + 22),
            )
        else:
            draw.text((status_x, footer_top + 20), status_text,
                      font=_font("regular", 22), fill=(*TEXT_SECONDARY, 255))
    else:
        # Обычная карточка: никаких намёков на секретную часть.
        canvas.alpha_composite(
            _rounded_panel((CONTENT_WIDTH, footer_height), radius=22,
                           fill=(*theme.accent, 10), edge=(*theme.accent, 40)),
            (MARGIN, footer_top),
        )
        draw = ImageDraw.Draw(canvas)
        draw.text((MARGIN + 24, footer_top + 14), "JustCoin",
                  font=_font("bold", 20), fill=(*theme.accent, 255))
        draw.text((MARGIN + 24, footer_top + 42),
                  "Чем больше джаст коинов, тем выше место в топе",
                  font=_font("regular", 17), fill=(*TEXT_SECONDARY, 210))
        canvas.alpha_composite(_coin_badge(28), (CARD_WIDTH - MARGIN - 28, footer_top + 24))

    # --- скругляем всю карточку ---
    mask = Image.new("L", (CARD_WIDTH, CARD_HEIGHT), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1], radius=CORNER_RADIUS, fill=255
    )
    canvas.putalpha(mask)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


async def render_card_async(data: CardData) -> bytes:
    """Рендер карточки в отдельном потоке, чтобы не блокировать event loop."""
    return await asyncio.to_thread(render_card, data)
