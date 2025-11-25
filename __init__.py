"""
网易云音乐点歌插件

提供网易云音乐的歌曲搜索、列表展示和播放功能。
支持通过Cookie登录,搜索歌曲并以图片列表形式展示结果。
支持播放指定歌曲,返回音频URL和歌曲信息。
"""

from nekro_agent.api import message
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import logger
from nekro_agent.services.plugin.base import ConfigBase, NekroPlugin, SandboxMethodType
from pydantic import Field

from .card_api import get_cover_url, get_signed_netease_card, get_song_play_url
from .image_gen import generate_song_list_image
from .ncm_api import (
    cleanup_pyncm_session,
    ensure_session_initialized,
    get_song_detail,
    search_songs_from_ncm,
)
from .utils import parse_chat_key

# --- Plugin Instance ---

plugin = NekroPlugin(
    name="网易云点歌",
    module_name="cloudmusic_search",
    description="提供网易云音乐搜索、图片列表展示和歌曲播放功能",
    version="0.4.0",
    author="sakuralis",
    url="https://github.com/leopard-1/nekro-plugin-cloudmusic-search",
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

    ENABLE_JSON_CARD: bool = Field(
        True,
        title="启用JSON卡片",
        description="使用网易云音乐JSON卡片发送歌曲（需API支持）",
    )

    COVER_SIZE: int = Field(
        500,
        title="封面尺寸",
        description="发送封面图片的尺寸（像素）",
        ge=0,
        le=1000,
    )


config = plugin.get_config(NetEaseCloudMusicConfig)

# --- Sandbox Methods ---


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "搜索歌曲")
async def search_songs(_ctx: AgentCtx, keyword: str) -> str:
    """搜索网易云音乐歌曲并生成图片列表

    Args:
        keyword: 搜索关键词,如"晴天"、"周杰伦"

    Returns:
        包含搜索结果和图片数据的字符串
    """
    if not keyword or not keyword.strip():
        raise ValueError("搜索关键词不能为空")

    # 确保会话已初始化（支持配置热重载）
    error = ensure_session_initialized(config.NCM_COOKIE)
    if error:
        return error

    # 搜索歌曲
    song_infos = search_songs_from_ncm(
        keyword=keyword,
        max_results=config.MAX_SEARCH_RESULTS,
        default_cover_url=config.DEFAULT_COVER_URL,
    )

    # # 生成图片
    # image_base64 = await generate_song_list_image(
    #     songs=song_infos,
    #     background_url=config.IMAGE_BACKGROUND_URL,
    #     font_path=config.FONT_PATH,
    #     max_results=config.MAX_SEARCH_RESULTS,
    #     default_cover_url=config.DEFAULT_COVER_URL,
    #     timeout=config.HTTP_TIMEOUT,
    # )

    # 构建响应
    response = f"为您找到以下歌曲(关键词: {keyword}):\n\n"
    for i, song in enumerate(song_infos):
        response += f"{i+1}. {song.name} - {song.artist} (ID: {song.id})\n"

    response += "\n若要播放,请使用 'play_song' 方法。"

    return response


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "播放歌曲")
async def play_song(_ctx: AgentCtx, song_id: int) -> str:
    """播放指定ID的歌曲

    Args:
        song_id: 歌曲ID

    Returns:
        播放结果信息
    """
    # 确保会话已初始化（支持配置热重载）
    error = ensure_session_initialized(config.NCM_COOKIE)
    if error:
        return error

    # 获取歌曲详情
    song_detail = get_song_detail(song_id)
    song_name = song_detail["name"]
    artist_name = ", ".join([ar["name"] for ar in song_detail.get("ar", [])])

    # 判断当前适配器
    if _ctx.adapter_key != "onebot_v11":
        # 其他适配器：返回歌曲信息和播放链接
        return f"🎵 歌曲信息:\n标题: {song_name}\n艺术家: {artist_name}\nID: {song_id}\n\n请在网易云音乐中搜索播放: https://music.163.com/#/song?id={song_id}"

    # OneBot v11: 发送网易云音乐卡片
    from nonebot import get_bot
    from nonebot.adapters.onebot.v11 import ActionFailed, MessageSegment

    bot = get_bot()
    chat_type, target_id = parse_chat_key(_ctx.chat_key)

    # 获取播放链接和封面
    music_url = await get_song_play_url(song_id)
    cover_url = await get_cover_url(song_detail, size=config.COVER_SIZE)

    # 尝试发送 JSON 卡片
    card_sent = False
    if config.ENABLE_JSON_CARD and music_url:
        logger.info(f"尝试发送JSON卡片: {song_name}")
        json_payload = await get_signed_netease_card(
            song_id=song_id,
            title=song_name,
            artist=artist_name,
            cover_url=cover_url or "",
            music_url=music_url,
        )

        if json_payload:
            try:
                json_msg = MessageSegment.json(json_payload)
                if chat_type == "private":
                    await bot.call_api("send_private_msg", user_id=target_id, message=json_msg)
                else:
                    await bot.call_api("send_group_msg", group_id=target_id, message=json_msg)
                card_sent = True
                logger.info("JSON卡片发送成功")
            except ActionFailed as e:
                logger.warning(f"JSON卡片发送失败: {e}")

    if card_sent:
        return f"🎵 歌曲《{song_name}》卡片已发送"

    # 降级方案：发送文字 + 封面 + 语音
    message_text = f"{song_name} - {artist_name}"
    if chat_type == "private":
        await bot.call_api("send_private_msg", user_id=target_id, message=message_text)
    else:
        await bot.call_api("send_group_msg", group_id=target_id, message=message_text)

    # 发送封面
    if cover_url and config.COVER_SIZE > 0:
        cover_msg = MessageSegment.image(cover_url)
        if chat_type == "private":
            await bot.call_api("send_private_msg", user_id=target_id, message=cover_msg)
        else:
            await bot.call_api("send_group_msg", group_id=target_id, message=cover_msg)

    # 发送语音
    if music_url:
        voice_msg = MessageSegment.record(file=music_url)
        if chat_type == "private":
            await bot.call_api("send_private_msg", user_id=target_id, message=voice_msg)
        else:
            await bot.call_api("send_group_msg", group_id=target_id, message=voice_msg)

    return f"🎵 歌曲《{song_name}》已发送（文字+封面+语音）"


@plugin.mount_cleanup_method()
async def cleanup():
    """清理插件资源"""
    cleanup_pyncm_session()
