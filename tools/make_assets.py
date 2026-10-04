"""Генератор иконки монеты bot/assets/coin.png.

Pillow не умеет рисовать цветные эмодзи, поэтому иконка 🪙 рисуется
векторно: золотая монета с градиентом, ободком и рельефной буквой «J».

Важно: ImageDraw по RGBA-изображению ЗАМЕНЯЕТ альфа-канал, а не смешивает
цвета. Поэтому каждый полупрозрачный слой рисуется отдельно и вклеивается
через alpha_composite, иначе монета получится с «дырявой» прозрачностью.

Запуск:  python tools/make_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "bot" / "assets"
SIZE = 512
SS = 4  # суперсэмплинг для гладких краёв
FONT_CANDIDATES = [
    ASSETS / "fonts" / "Montserrat.ttf",
    ASSETS / "fonts" / "DejaVuSans-Bold.ttf",
]


def _font(size: int):
    for path in FONT_CANDIDATES:
        if not path.exists():
            continue
        font = ImageFont.truetype(str(path), size)
        try:
            for axis in font.get_variation_axes():
                if axis["name"] == b"Weight":
                    font.set_variation_by_axes([800])
        except (OSError, AttributeError):
            pass
        return font
    return ImageFont.load_default(size=size)


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _blank(size: int) -> Image.Image:
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def make_coin() -> Image.Image:
    big = SIZE * SS
    cx = cy = big // 2
    radius = int(big * 0.47)

    canvas = _blank(big)

    # --- маска монетного круга ---
    circle_mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(circle_mask).ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], fill=255
    )

    def blend(layer: Image.Image, mask: Image.Image | None = None) -> None:
        """Вклеивает полупрозрачный слой с сохранением альфы основы."""
        if mask is not None:
            layer = Image.composite(layer, _blank(big), mask)
        canvas.alpha_composite(layer)

    # --- 1. тело с вертикальным градиентом ---
    body = _blank(big)
    body_draw = ImageDraw.Draw(body)
    top_color, bottom_color = (255, 231, 140), (214, 146, 30)
    for y in range(cy - radius, cy + radius + 1):
        t = (y - (cy - radius)) / (2 * radius)
        body_draw.line([(0, y), (big, y)], fill=(*_lerp(top_color, bottom_color, t), 255))
    blend(body, circle_mask)

    # --- 2. ободок ---
    rim = _blank(big)
    rim_draw = ImageDraw.Draw(rim)
    rim_width = int(big * 0.055)
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    rim_draw.ellipse(box, outline=(146, 90, 12, 255), width=rim_width)
    inner_box = [
        cx - radius + rim_width, cy - radius + rim_width,
        cx + radius - rim_width, cy + radius - rim_width,
    ]
    rim_draw.arc(inner_box, start=148, end=332, fill=(255, 244, 205, 235), width=int(big * 0.020))
    rim_draw.arc(inner_box, start=15, end=148, fill=(120, 72, 8, 200), width=int(big * 0.020))
    blend(rim, circle_mask)

    # --- 3. внутренний круг-рельеф ---
    relief = _blank(big)
    relief_draw = ImageDraw.Draw(relief)
    inner = int(radius * 0.78)
    relief_draw.ellipse(
        [cx - inner, cy - inner, cx + inner, cy + inner],
        fill=(255, 198, 72, 54), outline=(255, 244, 196, 120), width=int(big * 0.010),
    )
    blend(relief, circle_mask)

    # --- 4. рельефная буква «J» ---
    letter = _blank(big)
    letter_draw = ImageDraw.Draw(letter)
    font = _font(int(big * 0.44))
    text = "J"
    bbox = letter_draw.textbbox((0, 0), text, font=font)
    tx = cx - (bbox[0] + bbox[2]) / 2
    ty = cy - (bbox[1] + bbox[3]) / 2
    letter_draw.text((tx, ty + int(big * 0.013)), text, font=font, fill=(104, 58, 2, 170))
    letter_draw.text((tx, ty), text, font=font, fill=(255, 251, 236, 255))
    blend(letter, circle_mask)

    # --- 5. мягкий блик в верхней левой четверти ---
    gloss = _blank(big)
    ImageDraw.Draw(gloss).ellipse(
        [cx - radius * 0.60, cy - radius * 0.82,
         cx + radius * 0.20, cy - radius * 0.28],
        fill=(255, 255, 255, 40),
    )
    gloss = gloss.filter(ImageFilter.GaussianBlur(radius=big * 0.055))
    blend(gloss, circle_mask)

    # --- 6. мягкая тень снизу для объёма ---
    shade = _blank(big)
    ImageDraw.Draw(shade).ellipse(
        [cx - radius * 0.70, cy + radius * 0.30,
         cx + radius * 0.70, cy + radius * 1.00],
        fill=(112, 60, 2, 46),
    )
    shade = shade.filter(ImageFilter.GaussianBlur(radius=big * 0.07))
    blend(shade, circle_mask)

    coin = canvas.resize((SIZE, SIZE), Image.LANCZOS)

    # Проверка: центр монеты должен быть полностью непрозрачным.
    alpha = coin.getpixel((SIZE // 2, SIZE // 2))[3]
    if alpha < 250:  # pragma: no cover
        raise RuntimeError(f"Иконка монеты получилась с альфой {alpha} — слои не смешались")
    return coin


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    coin = make_coin()
    path = ASSETS / "coin.png"
    coin.save(path)
    print(f"Сохранено: {path} ({path.stat().st_size} байт)")
    print(f"Центр RGBA: {coin.getpixel((SIZE // 2, SIZE // 2))}")


if __name__ == "__main__":
    main()
