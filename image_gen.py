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
                return img.resize(size)
        except Exception as e:
            logger.warning(f"下载图片失败 {attempt_url}: {e}")

    return Image.new("RGBA", size, (230, 232, 236, 255))


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
        draw.text((xy[0], y), line, font=font, fill=fill)
        y += line_height
    return y - xy[1]


async def generate_result_image(
    title: str,
    items: Sequence[SongInfo | AlbumInfo],
    background_url: str,
    font_path: str,
    default_cover_url: str,
    timeout: int = 15,
) -> str:
    """生成搜索结果图片，返回本地 PNG 路径"""
    max_items = min(len(items), 20)
    img_width = 900
    item_height = 92
    header_height = 92
    margin = 30
    img_height = max(360, header_height + margin * 2 + item_height * max(1, max_items))

    background_img = await download_image_as_pil(
        background_url,
        (img_width, img_height),
        default_cover_url,
        timeout=timeout,
    )
    overlay = Image.new("RGBA", background_img.size, (255, 255, 255, 145))
    canvas = Image.alpha_composite(background_img, overlay)
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(font_path, 30)
    font_name = _load_font(font_path, 23)
    font_detail = _load_font(font_path, 18)
    font_meta = _load_font(font_path, 16)

    text_dark = (24, 28, 36, 255)
    text_muted = (70, 76, 88, 255)
    accent = (199, 35, 49, 255)

    draw.text((margin, 28), _fit_text(title, 28), font=font_title, fill=text_dark)
    draw.line((margin, 76, img_width - margin, 76), fill=(199, 35, 49, 180), width=3)

    y = header_height
    for idx, item in enumerate(items[:max_items], start=1):
        box = (margin, y, img_width - margin, y + item_height - 10)
        draw.rounded_rectangle(box, radius=8, fill=(255, 255, 255, 205))
        draw.text((margin + 16, y + 24), f"{idx:02d}", font=font_name, fill=accent)

        x = margin + 72
        if isinstance(item, SongInfo):
            _draw_wrapped(draw, (x, y + 13), item.name, font_name, text_dark, width=26, max_lines=1)
            detail = f"{item.artist} · {item.album}"
            meta = f"歌曲 ID: {item.id} · {format_duration(item.duration)}"
        else:
            _draw_wrapped(draw, (x, y + 13), item.name, font_name, text_dark, width=26, max_lines=1)
            detail = f"{item.artist} · {item.song_count or '未知'} 首"
            meta = f"专辑 ID: {item.id}"

        _draw_wrapped(draw, (x, y + 43), detail, font_detail, text_muted, width=38, max_lines=1)
        draw.text((img_width - margin - 210, y + 50), meta, font=font_meta, fill=text_muted)
        y += item_height

    output = Path(tempfile.mkstemp(prefix="cloudmusic_", suffix=".png")[1])
    canvas.convert("RGB").save(output, format="PNG")
    return str(output)
