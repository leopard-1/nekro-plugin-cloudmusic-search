"""搜索结果图片生成模块"""

import io
import tempfile
import textwrap
from pathlib import Path
from typing import Sequence

import httpx
from nekro_agent.core import logger
from PIL import Image, ImageDraw, ImageFont

from .models import AlbumInfo, SongInfo
from .utils import format_duration

MODULE_DIR = Path(__file__).resolve().parent


async def download_image_as_pil(
    url: str,
    size: tuple[int, int],
    fallback_url: str,
    timeout: int = 15,
) -> Image.Image:
    """下载图片并转换为 PIL Image，失败时使用纯色背景"""
    for attempt_url in [url, fallback_url]:
        if not attempt_url:
            continue
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(str(attempt_url))
                response.raise_for_status()
                img = Image.open(io.BytesIO(response.content)).convert("RGBA")
                return _resize_cover(img, size)
        except Exception as e:
            logger.warning(f"下载图片失败 {attempt_url}: {e}")

    return Image.new("RGBA", size, (230, 232, 236, 255))


def _resize_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """按 cover 方式缩放裁切背景，避免图片变形。"""
    target_w, target_h = size
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGBA", size, (36, 39, 45, 255))
    scale = max(target_w / src_w, target_h / src_h)
    resized = img.resize((max(1, int(src_w * scale)), max(1, int(src_h * scale))))
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        font_path,
        str(MODULE_DIR / font_path) if font_path and not Path(font_path).is_absolute() else "",
        str(MODULE_DIR / "fonts" / Path(font_path).name) if font_path else "",
        "fonts/font.ttf",
        "fonts/font.ttc",
        str(MODULE_DIR / "fonts" / "font.ttf"),
        str(MODULE_DIR / "fonts" / "font.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "simsun.ttc",
        "msyh.ttc",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    logger.warning("未找到可用字体，图片可能无法正确显示中文")
    return ImageFont.load_default()


def _fit_text(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)] + "…"


def _hex_to_rgba(value: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    text = (value or "").strip().lstrip("#")
    if len(text) not in {6, 8}:
        return fallback
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
        a = int(text[6:8], 16) if len(text) == 8 else 255
        return (r, g, b, a)
    except ValueError:
        return fallback


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
) -> None:
    x, y = xy
    shadow = (0, 0, 0, 165)
    draw.text((x + 2, y + 2), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    width: int,
    max_lines: int = 2,
) -> int:
    lines = textwrap.wrap(str(text), width=max(1, width), max_lines=max_lines, placeholder="…")
    if not lines:
        return 0
    y = xy[1]
    line_height = max(18, draw.textbbox((0, 0), "国", font=font)[3] + 5)
    for line in lines:
        _draw_text_with_shadow(draw, (xy[0], y), line, font, fill)
        y += line_height
    return y - xy[1]


async def generate_result_image(
    title: str,
    items: Sequence[SongInfo | AlbumInfo],
    background_url: str,
    font_path: str,
    default_cover_url: str,
    image_width: int = 900,
    image_height: int = 0,
    index_color: str = "#ff3850",
    song_name_color: str = "#ffffff",
    timeout: int = 15,
) -> str:
    """生成搜索结果图片，返回本地 PNG 路径"""
    max_items = min(len(items), 20)
    img_width = max(480, min(int(image_width or 900), 2000))
    item_height = 84
    header_height = 88
    margin = 30
    auto_height = max(360, header_height + margin * 2 + item_height * max(1, max_items))
    img_height = max(360, min(int(image_height or auto_height), 4000))
    available_height = max(1, img_height - header_height - margin)
    item_height = max(48, min(item_height, available_height // max(1, max_items)))

    background_img = await download_image_as_pil(
        background_url,
        (img_width, img_height),
        default_cover_url,
        timeout=timeout,
    )
    overlay = Image.new("RGBA", background_img.size, (0, 0, 0, 72))
    canvas = Image.alpha_composite(background_img, overlay)
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(font_path, max(22, min(30, img_width // 30)))
    font_name = _load_font(font_path, max(18, min(24, img_width // 38)))
    font_detail = _load_font(font_path, max(14, min(18, img_width // 50)))
    font_meta = _load_font(font_path, max(13, min(16, img_width // 58)))

    title_color = _hex_to_rgba(song_name_color, (255, 255, 255, 255))
    name_color = _hex_to_rgba(song_name_color, (255, 255, 255, 255))
    text_muted = (226, 232, 240, 255)
    accent = _hex_to_rgba(index_color, (255, 56, 80, 255))

    _draw_text_with_shadow(draw, (margin, 26), _fit_text(title, max(16, img_width // 26)), font_title, title_color)
    draw.line((margin, 72, img_width - margin, 72), fill=accent[:3] + (210,), width=3)

    y = header_height
    for idx, item in enumerate(items[:max_items], start=1):
        _draw_text_with_shadow(draw, (margin + 16, y + max(8, item_height // 4)), f"{idx:02d}", font_name, accent)

        x = margin + 72
        if isinstance(item, SongInfo):
            _draw_wrapped(draw, (x, y + 8), item.name, font_name, name_color, width=max(12, img_width // 34), max_lines=1)
            detail = f"{item.artist} · {item.album}"
            meta = f"歌曲 ID: {item.id} · {format_duration(item.duration)}"
        else:
            _draw_wrapped(draw, (x, y + 8), item.name, font_name, name_color, width=max(12, img_width // 34), max_lines=1)
            detail = f"{item.artist} · {item.song_count or '未知'} 首"
            meta = f"专辑 ID: {item.id}"

        _draw_wrapped(draw, (x, y + max(32, item_height // 2)), detail, font_detail, text_muted, width=max(16, img_width // 24), max_lines=1)
        _draw_text_with_shadow(draw, (max(x, img_width - margin - 230), y + max(32, item_height // 2)), meta, font_meta, text_muted)
        y += item_height

    output = Path(tempfile.mkstemp(prefix="cloudmusic_", suffix=".png")[1])
    canvas.convert("RGB").save(output, format="PNG")
    return str(output)
