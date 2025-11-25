"""图片生成模块"""

import base64
import io
import textwrap
from typing import List

import httpx
from nekro_agent.core import logger
from PIL import Image, ImageDraw, ImageFont

from .models import SongInfo


async def download_image_as_pil(
    url: str,
    size: tuple[int, int],
    fallback_url: str,
    timeout: int = 15,
) -> Image.Image:
    """下载图片并转换为PIL Image,支持fallback
    
    Args:
        url: 图片URL
        size: 目标尺寸 (width, height)
        fallback_url: 备用URL
        timeout: 超时时间(秒)
        
    Returns:
        PIL Image对象
    """
    for attempt_url in [url, fallback_url]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(str(attempt_url))
                response.raise_for_status()
                img = Image.open(io.BytesIO(response.content)).convert("RGBA")
                return img.resize(size)
        except Exception as e:
            logger.warning(f"下载图片失败 {attempt_url}: {e}")

    # 最终fallback: 灰色背景
    logger.error("所有图片下载失败,使用纯色背景")
    return Image.new("RGBA", size, (200, 200, 200, 255))


async def generate_song_list_image(
    songs: List[SongInfo],
    background_url: str,
    font_path: str,
    max_results: int,
    default_cover_url: str,
    timeout: int = 15,
) -> str:
    """生成歌曲列表图片,返回base64编码
    
    Args:
        songs: 歌曲列表
        background_url: 背景图URL
        font_path: 字体文件路径
        max_results: 最大显示数量
        default_cover_url: 默认封面URL
        timeout: HTTP超时时间
        
    Returns:
        base64编码的PNG图片
    """
    img_width, img_height = 800, 800
    margin = 30
    header_height = 80
    song_item_height = (img_height - header_height - margin * 2) // max_results

    # 加载背景
    background_img = await download_image_as_pil(
        background_url,
        (img_width, img_height),
        default_cover_url,
        timeout=timeout,
    )
    draw = ImageDraw.Draw(background_img)

    # 加载字体
    try:
        font_title = ImageFont.truetype(font_path, 30)
        font_item_name = ImageFont.truetype(font_path, 22)
        font_item_detail = ImageFont.truetype(font_path, 18)
    except IOError:
        logger.warning(f"字体文件'{font_path}'加载失败,使用默认字体")
        font_title = ImageFont.load_default(size=30)
        font_item_name = ImageFont.load_default(size=22)
        font_item_detail = ImageFont.load_default(size=18)

    # 绘制标题
    header_text = "网易云音乐搜索结果"
    bbox_title = draw.textbbox((0, 0), header_text, font=font_title)
    text_width = bbox_title[2] - bbox_title[0]
    draw.text(
        ((img_width - text_width) / 2, margin),
        header_text,
        font=font_title,
        fill=(255, 255, 255, 255),
        stroke_fill=(0, 0, 0, 150),
        stroke_width=2,
    )

    # 绘制歌曲列表
    current_y = header_height + margin
    text_color = (255, 255, 255, 255)

    for i, song in enumerate(songs[:max_results]):
        if current_y + song_item_height > img_height - margin:
            break

        # 半透明背景
        draw.rectangle(
            (margin, current_y, img_width - margin, current_y + song_item_height - 5),
            fill=(0, 0, 0, 100),
        )

        # 序号
        draw.text(
            (margin + 10, current_y + 8),
            f"{i+1}.",
            font=font_item_name,
            fill=text_color,
        )

        # 歌曲名(自动换行)
        available_width = img_width - margin * 2 - 60 - 120
        chars_per_line = int(available_width / 20)
        wrapped_name = textwrap.fill(song.name, width=min(chars_per_line, 25))
        bbox_name = draw.textbbox((0, 0), wrapped_name, font=font_item_name)
        name_height = bbox_name[3] - bbox_name[1]
        draw.text(
            (margin + 50, current_y + 8),
            wrapped_name,
            font=font_item_name,
            fill=text_color,
        )

        # 艺术家和专辑
        artist_album_text = f"{song.artist} - {song.album}"
        wrapped_artist_album = textwrap.fill(artist_album_text, width=min(chars_per_line + 5, 30))
        draw.text(
            (margin + 50, current_y + 8 + name_height + 5),
            wrapped_artist_album,
            font=font_item_detail,
            fill=text_color,
        )

        # 歌曲ID
        draw.text(
            (img_width - margin - 100, current_y + 10),
            f"ID: {song.id}",
            font=font_item_detail,
            fill=text_color,
        )

        current_y += song_item_height

    # 转换为base64
    buffered = io.BytesIO()
    background_img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

