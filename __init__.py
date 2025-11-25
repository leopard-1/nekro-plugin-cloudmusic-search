"""
网易云音乐点歌插件

提供网易云音乐的歌曲搜索、列表展示和播放功能。
支持通过Cookie登录,搜索歌曲并以图片列表形式展示结果。
支持播放指定歌曲,返回音频URL和歌曲信息。
"""

import json
import base64
import io
import json
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

import httpx
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from nonebot.adapters.onebot.v11 import Message, MessageSegment  
from nekro_agent.adapters.onebot_v11.core.bot import get_bot
from nekro_agent.api.plugin import dynamic_import_pkg
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import logger
from nekro_agent.services.plugin.base import ConfigBase, NekroPlugin, SandboxMethodType

# 类型检查时导入,用于IDE类型提示
if TYPE_CHECKING:
    import pyncm
    from pyncm import GetCurrentSession, Session, SetCurrentSession
    from pyncm.apis import cloudsearch, track

# 运行时动态导入
pyncm = dynamic_import_pkg("pyncm==1.8.1", import_name="pyncm")
Session = pyncm.Session
SetCurrentSession = pyncm.SetCurrentSession
GetCurrentSession = pyncm.GetCurrentSession

# --- Pydantic Models ---


class SongInfo(BaseModel):
    """单首歌曲的信息模型"""

    id: int = Field(..., description="歌曲ID")
    name: str = Field(..., description="歌曲名称")
    artist: str = Field(..., description="艺术家")
    album: str = Field(..., description="专辑名称")
    duration: int = Field(..., description="时长(毫秒)")
    cover_url: str = Field(..., description="封面URL")


class PlaySongResponseCard(BaseModel):
    """播放歌曲响应卡片"""

    type: Literal["music_card"] = Field(default="music_card", description="卡片类型")
    title: str = Field(..., description="歌曲标题")
    artist: str = Field(..., description="艺术家")
    album: str = Field(..., description="专辑名称")
    cover_url: str = Field(..., description="封面URL")
    audio_url: str = Field(..., description="音频URL")
    message: str = Field(..., description="附加信息")


# --- Plugin Instance ---

plugin = NekroPlugin(
    name="NetEaseCloudMusicPlayer",
    module_name="netease_cloud_music_player",
    description="提供网易云音乐搜索、图片列表展示和歌曲播放功能",
    version="0.3.0",
    author="sakuralis",
    url="https://github.com/leopard-1/cloudmusic02",
)

# --- Configuration ---


@plugin.mount_config()
class NetEaseCloudMusicConfig(ConfigBase):
    """网易云音乐插件配置"""

    NCM_COOKIE: str = Field(
        "",
        title="网易云音乐Cookie",
        description="从浏览器复制的完整Cookie字符串,包含MUSIC_U等字段。获取方式: 登录music.163.com → F12开发者工具 → Network → 复制Cookie请求头",
        json_schema_extra={"is_secret": True},
    )

    IMAGE_BACKGROUND_URL: str = Field(
        "https://cdn.jsdelivr.net/gh/leopard-1/cloudmusic02@main/default_bg.jpg",
        title="背景图URL",
        description="歌曲列表图片背景,建议800x800",
    )

    FONT_PATH: str = Field(
        "simsun.ttc",
        title="字体文件路径",
        description="图片生成使用的字体文件,如 'msyh.ttc' 或 'simsun.ttc'",
    )

    MAX_SEARCH_RESULTS: int = Field(
        15,
        title="最大搜索结果数",
        description="图片列表显示的最大歌曲数量",
        ge=1,
        le=20,
    )

    DEFAULT_COVER_URL: str = Field(
        "https://p2.music.126.net/6y-UfFfE3WcTq964nK1X6Q==/109951163158079773.jpg",
        title="默认封面URL",
        description="无法获取封面时的默认图片",
    )

    HTTP_TIMEOUT: int = Field(
        15,
        title="HTTP超时(秒)",
        description="HTTP请求超时时间",
        ge=5,
        le=60,
    )


config = plugin.get_config(NetEaseCloudMusicConfig)

# --- Initialization ---


def _parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    """解析Cookie字符串为字典"""
    cookies = {}
    if not cookie_string or not cookie_string.strip():
        return cookies

    # 支持多种分隔符: 分号、换行
    cookie_string = cookie_string.replace("\n", "; ").replace("\r", "")

    for item in cookie_string.split(";"):
        item = item.strip()
        if "=" in item:
            key, _, value = item.partition("=")
            cookies[key.strip()] = value.strip()

    return cookies


def _init_pyncm_session() -> bool:
    """初始化pyncm会话,加载Cookie"""
    if not config.NCM_COOKIE or not config.NCM_COOKIE.strip():
        logger.warning("未配置网易云音乐Cookie,部分功能可能受限")
        return False

    try:
        # 解析Cookie字符串
        cookies_dict = _parse_cookie_string(config.NCM_COOKIE)

        # 验证必需字段
        required_keys = ["MUSIC_U", "__csrf"]
        missing_keys = [k for k in required_keys if k not in cookies_dict]
        if missing_keys:
            logger.error(f"Cookie字符串缺少必需字段: {missing_keys}")
            logger.info("请确保Cookie包含 MUSIC_U 和 __csrf 字段")
            return False

        # 创建并设置Session
        session = Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)
        SetCurrentSession(session)
        logger.info("pyncm会话初始化成功")

    except Exception as e:
        logger.error(f"初始化pyncm会话失败: {e}", exc_info=True)
        return False

    else:
        return True


# 初始化
_pyncm_ready: bool = _init_pyncm_session()

# --- Helper Functions ---


async def _download_image_as_pil(
    url: str,
    size: tuple[int, int],
    fallback_url: str,
) -> Image.Image:
    """下载图片并转换为PIL Image,支持fallback"""
    for attempt_url in [url, fallback_url]:
        try:
            async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
                response = await client.get(str(attempt_url))
                response.raise_for_status()
                img = Image.open(io.BytesIO(response.content)).convert("RGBA")
                return img.resize(size)
        except Exception as e:
            logger.warning(f"下载图片失败 {attempt_url}: {e}")

    # 最终fallback: 灰色背景
    logger.error("所有图片下载失败,使用纯色背景")
    return Image.new("RGBA", size, (200, 200, 200, 255))


async def _generate_song_list_image(
    songs: List[SongInfo],
    background_url: str,
    font_path: str,
    max_results: int,
    default_cover_url: str,
) -> str:
    """生成歌曲列表图片,返回base64编码"""
    img_width, img_height = 800, 800
    margin = 30
    header_height = 80
    song_item_height = (img_height - header_height - margin * 2) // max_results

    # 加载背景
    background_img = await _download_image_as_pil(
        background_url,
        (img_width, img_height),
        default_cover_url,
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


# --- Sandbox Methods ---


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "搜索歌曲")
async def search_songs(_ctx: AgentCtx, keyword: str) -> str:
    """搜索网易云音乐歌曲并生成图片列表

    Args:
        keyword (str): 搜索关键词,如"晴天"、"周杰伦"

    Returns:
        str: 包含搜索结果和图片数据的字符串
    """
    if not keyword or not keyword.strip():
        raise ValueError("搜索关键词不能为空")

    if not _pyncm_ready:
        return "网易云音乐插件未正确配置。请在插件配置中填写完整的Cookie字符串(需包含MUSIC_U和__csrf字段)。"

    try:
        # 动态获取API模块
        from pyncm.apis import cloudsearch

        # 调用pyncm API搜索
        search_result = cloudsearch.GetSearchResult(keyword, stype=cloudsearch.SONG)

        # pyncm API返回的是dict
        result_dict: Dict[str, Any] = search_result  # type: ignore
        songs_data: List[Dict[str, Any]] = result_dict.get("result", {}).get("songs", [])
        if not songs_data:
            return f"未找到与'{keyword}'相关的歌曲,请尝试其他关键词。"

        # 处理歌曲数据
        song_infos: List[SongInfo] = []
        for s in songs_data[: config.MAX_SEARCH_RESULTS]:
            try:
                cover_url_raw = s.get("al", {}).get("picUrl")
                cover_url = f"{cover_url_raw}?param=140y140" if cover_url_raw else config.DEFAULT_COVER_URL

                song_infos.append(
                    SongInfo(
                        id=s["id"],
                        name=s["name"],
                        artist=", ".join([ar["name"] for ar in s.get("ar", [])]),
                        album=s.get("al", {}).get("name", "未知专辑"),
                        duration=s.get("dt", 0),
                        cover_url=cover_url,
                    ),
                )
            except Exception as e:
                logger.warning(f"处理歌曲 {s.get('name', 'Unknown')} 失败: {e}")
                continue

        if not song_infos:
            return f"未能解析'{keyword}'的搜索结果,请重试。"

        # 生成图片
        image_base64 = await _generate_song_list_image(
            song_infos,
            config.IMAGE_BACKGROUND_URL,
            config.FONT_PATH,
            config.MAX_SEARCH_RESULTS,
            config.DEFAULT_COVER_URL,
        )

        # 构建响应
        response = f"为您找到以下歌曲(关键词: {keyword}):\n\n"
        for i, song in enumerate(song_infos):
            response += f"{i+1}. {song.name} - {song.artist} (ID: {song.id})\n"

        response += f"\n若要播放,请使用 '播放歌曲 <歌曲ID>' 命令,例如 '播放歌曲 {song_infos[0].id}'。"
        response += f"\n\n[图片数据:base64,{image_base64}]"

    except Exception as e:
        logger.error(f"搜索歌曲失败,关键词'{keyword}': {e}", exc_info=True)
        raise Exception(f"搜索失败: {e}") from e
    
    else:
        return response


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "播放歌曲")
async def play_song(_ctx: AgentCtx, song_id: int, chat_key: str) -> str:
    """播放指定ID的歌曲

    Args:
        song_id (int): 歌曲ID
        chat_key (int): 聊天标识符,必须是群聊ID,忽略其他字，例如"onebot-v11-group-123456789"<--只截取后面的数字"123456789"

    Returns:
        str: 包含播放信息和JSON卡片的字符串
    """
    try:
        from pyncm.apis import track

        # 获取歌曲详情用于显示信息
        track_details_result = track.GetTrackDetail([song_id])
        track_details: Dict[str, Any] = track_details_result

        if not track_details or not track_details.get("songs"):
            return f"未找到歌曲ID {song_id},请检查ID是否正确。"

        song_detail = track_details["songs"][0]
        song_name = song_detail["name"]
        artist_name = ", ".join([ar["name"] for ar in song_detail.get("ar", [])])

        # 发送网易云音乐卡片
        music_message = Message([
            MessageSegment.music("163", str(song_id))
        ])
        
        # 使用 _ctx.chat_key 作为群号
        await get_bot().send_group_msg(
            group_id=int(_ctx.chat_key),  # 符合规范
            message=music_message
        )
        
        return f"🎵 正在播放: {song_name} - {artist_name} (ID: {song_id})"
        
    except ValueError as e:
        return f"错误: chat_key '{_ctx.chat_key}' 不是有效的群号"
    except Exception as e:
        logger.error(f"播放歌曲ID {song_id} 失败: {e}", exc_info=True)
        return f"播放失败: {e}"
            
    else:
        return response


@plugin.mount_cleanup_method()
async def cleanup():
    """清理插件资源"""
    try:
        if _pyncm_ready:
            empty_session = Session()
            SetCurrentSession(empty_session)
            logger.info("pyncm会话已清理")
    except Exception as e:
        logger.warning(f"清理pyncm会话时出错: {e}")
        