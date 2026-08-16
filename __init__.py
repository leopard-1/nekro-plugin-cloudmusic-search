"""网易云音乐点歌插件"""

import tempfile
from pathlib import Path
from typing import Annotated

import httpx
from nekro_agent.api.plugin import (
    Arg,
    CmdCtl,
    CommandExecutionContext,
    CommandPermission,
    CommandResponse,
    ConfigBase,
    NekroPlugin,
    SandboxMethodType,
)
from nekro_agent.api.schemas import AgentCtx
from pydantic import Field

from .card_api import get_cover_url, get_signed_netease_card
from .image_gen import generate_result_image
from .models import AlbumInfo, ArtistSearchResult, SongInfo
from .ncm_api import (
    cleanup_pyncm_session,
    ensure_session_initialized,
    get_album_detail,
    get_song_audio_info,
    get_song_detail,
    normalize_quality,
    search_albums_from_ncm,
    search_artist_music_from_ncm,
    search_songs_from_ncm,
)
from .utils import detect_audio_extension, format_duration, parse_chat_key, safe_filename


plugin = NekroPlugin(
    name="网易云点歌",
    module_name="cloudmusic_search",
    description="提供网易云音乐搜索、歌手/专辑查询、可选音质播放和歌曲文件发送功能",
    version="0.5.0",
    author="sakuralis",
    url="https://github.com/leopard-1/nekro-plugin-cloudmusic-search",
)


@plugin.mount_config()
class NetEaseCloudMusicConfig(ConfigBase):
    """网易云音乐插件配置"""

    NCM_COOKIE: str = Field(
        "",
        title="网易云音乐 Cookie",
        description="从浏览器复制的完整 Cookie 字符串，建议包含 MUSIC_U 和 __csrf 字段。",
        json_schema_extra={"is_secret": True},
    )

    DEFAULT_QUALITY: str = Field(
        "lossless",
        title="默认音质",
        description="默认请求音质：standard / higher / lossless。用户会话或命令中指定音质时优先生效。",
    )

    SEARCH_OUTPUT_MODE: str = Field(
        "text",
        title="搜索结果输出模式",
        description="text 为纯文本，image 为图片。命令中附带 text/image 时优先。",
    )

    IMAGE_BACKGROUND_URL: str = Field(
        "https://cdn.jsdelivr.net/gh/leopard-1/cloudmusic02@main/default_bg.jpg",
        title="搜索结果图片背景",
        description="搜索结果图片背景 URL。",
    )

    FONT_PATH: str = Field(
        "fonts/font.ttf",
        title="字体文件路径",
        description="图片生成使用的字体文件路径，支持 ttf/ttc。容器部署时请填写容器内可访问路径。",
    )

    MAX_SEARCH_RESULTS: int = Field(
        20,
        title="最大搜索结果数",
        description="歌曲或专辑列表显示数量，最多 20 条。搜索后可用编号播放或下载。",
        ge=1,
        le=20,
    )

    DEFAULT_COVER_URL: str = Field(
        "https://p2.music.126.net/6y-UfFfE3WcTq964nK1X6Q==/109951163158079773.jpg",
        title="默认封面 URL",
        description="无法获取封面或背景时使用的备用图片。",
    )

    HTTP_TIMEOUT: int = Field(
        15,
        title="HTTP 超时(秒)",
        description="HTTP 请求超时时间。",
        ge=5,
        le=60,
    )

    ENABLE_JSON_CARD: bool = Field(
        True,
        title="启用 JSON 卡片",
        description="OneBot v11 下优先发送网易云音乐 JSON 卡片，失败后降级为文字、封面和语音。",
    )

    CARD_FALLBACK_MODE: str = Field(
        "voice",
        title="卡片失败降级模式",
        description="JSON 卡片不可用时的降级方式：voice 发送语音，text 只发文字链接，none 不降级。",
    )

    COVER_SIZE: int = Field(
        500,
        title="封面尺寸",
        description="发送封面图片的尺寸，0 表示不发送封面。",
        ge=0,
        le=1000,
    )

    MAX_DOWNLOAD_MB: int = Field(
        30,
        title="最大下载文件大小(MB)",
        description="下载并发送歌曲文件的最大体积，避免误发过大的音频。",
        ge=1,
        le=200,
    )


config = plugin.get_config(NetEaseCloudMusicConfig)
_last_song_results: dict[str, list[SongInfo]] = {}


def _session_error() -> str | None:
    return ensure_session_initialized(config.NCM_COOKIE)


def _result_limit() -> int:
    return max(1, min(int(config.MAX_SEARCH_RESULTS or 20), 20))


def _fallback_mode() -> str:
    mode = (config.CARD_FALLBACK_MODE or "voice").strip().lower()
    return mode if mode in {"voice", "text", "none"} else "voice"


def _output_mode(raw: str = "") -> str:
    value = (raw or "").strip().lower()
    if value in {"image", "图片", "图", "pic"}:
        return "image"
    if value in {"text", "文本", "纯文本"}:
        return "text"
    configured = (config.SEARCH_OUTPUT_MODE or "text").strip().lower()
    return "image" if configured in {"image", "图片", "pic"} else "text"


def _extract_output_mode(raw_text: str) -> tuple[str, str]:
    tokens = raw_text.split()
    mode = ""
    kept: list[str] = []
    for token in tokens:
        if token.lower() in {"image", "pic", "text"} or token in {"图片", "图", "文本", "纯文本"}:
            mode = token
        else:
            kept.append(token)
    return " ".join(kept).strip(), _output_mode(mode)


def _extract_quality(raw_text: str) -> tuple[str, str]:
    tokens = raw_text.split()
    quality = ""
    kept: list[str] = []
    quality_words = {
        "standard",
        "std",
        "normal",
        "mp3",
        "higher",
        "high",
        "hq",
        "lossless",
        "flac",
        "标准",
        "普通",
        "较高",
        "高",
        "高音质",
        "无损",
        "极高",
    }
    for token in tokens:
        lower = token.lower()
        if lower.startswith(("quality:", "quality=", "q:", "q=")):
            quality = token.split(":", 1)[-1].split("=", 1)[-1]
            continue
        if lower in quality_words or token in quality_words:
            quality = token
            continue
        kept.append(token)
    return " ".join(kept).strip(), normalize_quality(quality, config.DEFAULT_QUALITY)


def _format_songs(title: str, songs: list[SongInfo]) -> str:
    if not songs:
        return f"{title}\n\n没有找到歌曲。"
    lines = [title, "", "可用 /cm_play 编号 播放，或 /cm_download 编号 下载。", ""]
    for index, song in enumerate(songs, start=1):
        lines.append(f"{index}. {song.name} - {song.artist}")
        lines.append(f"   专辑：{song.album} | 时长：{format_duration(song.duration)} | ID：{song.id}")
    return "\n".join(lines)


def _format_albums(title: str, albums: list[AlbumInfo]) -> str:
    if not albums:
        return f"{title}\n\n没有找到专辑。"
    lines = [title, ""]
    for index, album in enumerate(albums, start=1):
        count = f"{album.song_count} 首" if album.song_count else "歌曲数未知"
        lines.append(f"{index}. {album.name} - {album.artist}")
        lines.append(f"   {count} | 专辑 ID：{album.id}")
    return "\n".join(lines)


def _format_artist_result(result: ArtistSearchResult) -> str:
    lines = [f"网易云歌手相关搜索 | {result.keyword}", ""]
    lines.append("相关歌曲（可用 /cm_play 编号 播放）：")
    for index, song in enumerate(result.songs, start=1):
        lines.append(f"{index}. {song.name} - {song.artist} | {song.album} | ID：{song.id}")
    if not result.songs:
        lines.append("未找到相关歌曲。")

    lines.extend(["", "相关专辑："])
    for index, album in enumerate(result.albums, start=1):
        count = f"{album.song_count} 首" if album.song_count else "歌曲数未知"
        lines.append(f"{index}. {album.name} - {album.artist} | {count} | 专辑 ID：{album.id}")
    if not result.albums:
        lines.append("未找到相关专辑。")
    return "\n".join(lines)


async def _send_search_result(
    ctx: AgentCtx,
    title: str,
    text: str,
    image_items: list[SongInfo | AlbumInfo],
    mode: str,
) -> str:
    if mode == "image" and image_items:
        try:
            image_path = await generate_result_image(
                title=title,
                items=image_items,
                background_url=config.IMAGE_BACKGROUND_URL,
                font_path=config.FONT_PATH,
                default_cover_url=config.DEFAULT_COVER_URL,
                timeout=config.HTTP_TIMEOUT,
            )
            sandbox_path = await ctx.fs.mixed_forward_file(Path(image_path), file_name=Path(image_path).name)
            await ctx.send_image(sandbox_path)
            return text
        except Exception as e:
            plugin.logger.exception(f"搜索结果图片发送失败，回退文本: {e}")
            text = f"{text}\n\n图片输出失败，已回退文本：{e}"
    await ctx.send_text(text)
    return text


def _remember_song_results(chat_key: str, songs: list[SongInfo]) -> None:
    if songs:
        _last_song_results[chat_key] = songs[:20]


def _parse_song_id(raw_text: str, chat_key: str = "") -> int:
    first = (raw_text or "").split(maxsplit=1)[0] if raw_text else ""
    if not first.isdigit():
        raise ValueError("请输入有效的歌曲编号或歌曲 ID。")
    value = int(first)
    cached = _last_song_results.get(chat_key, [])
    if 1 <= value <= len(cached):
        return cached[value - 1].id
    if value <= 20 and cached:
        raise ValueError(f"最近一次搜索只有 {len(cached)} 条结果，无法选择编号 {value}。")
    return value


async def _download_audio_file(song_id: int, quality: str) -> tuple[Path, str, str]:
    detail = get_song_detail(song_id)
    song_name = detail.get("name") or str(song_id)
    artist_name = ", ".join([ar["name"] for ar in detail.get("ar", []) if isinstance(ar, dict)]) or "未知歌手"
    audio = get_song_audio_info(song_id, quality, config.DEFAULT_QUALITY)

    if audio.extension == "ncm":
        raise ValueError("网易云返回的是加密 ncm 格式，已按要求跳过发送。")

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(audio.url)
        response.raise_for_status()

    ext = detect_audio_extension(str(response.url), response.headers.get("content-type", "")) or audio.extension
    if ext == "ncm":
        raise ValueError("网易云返回的是加密 ncm 格式，已按要求跳过发送。")
    if ext not in {"mp3", "wav"}:
        raise ValueError(f"仅支持发送 mp3/wav，当前识别到的格式为：{ext or '未知'}")

    max_bytes = int(config.MAX_DOWNLOAD_MB) * 1024 * 1024
    if len(response.content) > max_bytes:
        raise ValueError(f"音频文件超过 {config.MAX_DOWNLOAD_MB} MB，已取消发送。")

    filename = f"{safe_filename(song_name)} - {safe_filename(artist_name)}.{ext}"
    path = Path(tempfile.gettempdir()) / filename
    path.write_bytes(response.content)
    return path, song_name, artist_name


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "搜索网易云歌曲")
async def search_songs(ctx: AgentCtx, keyword: str, output_mode: str = "") -> str:
    """搜索网易云音乐歌曲"""
    if not keyword or not keyword.strip():
        raise ValueError("搜索关键词不能为空")
    error = _session_error()
    if error:
        return error
    songs = search_songs_from_ncm(keyword.strip(), _result_limit(), config.DEFAULT_COVER_URL)
    _remember_song_results(ctx.chat_key, songs)
    title = f"网易云歌曲搜索 | {keyword.strip()}"
    text = _format_songs(title, songs)
    await _send_search_result(ctx, title, text, songs, _output_mode(output_mode))
    return text


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "按歌手搜索网易云歌曲和专辑")
async def search_artist_music(ctx: AgentCtx, artist: str, output_mode: str = "") -> str:
    """根据歌手关键词搜索相关歌曲和专辑，歌曲和专辑最多各 20 条"""
    if not artist or not artist.strip():
        raise ValueError("歌手关键词不能为空")
    error = _session_error()
    if error:
        return error
    result = search_artist_music_from_ncm(artist.strip(), _result_limit(), config.DEFAULT_COVER_URL)
    _remember_song_results(ctx.chat_key, result.songs)
    title = f"网易云歌手搜索 | {artist.strip()}"
    text = _format_artist_result(result)
    await _send_search_result(ctx, title, text, [*result.songs, *result.albums], _output_mode(output_mode))
    return text


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "获取网易云专辑")
async def get_album(ctx: AgentCtx, album_id: int, output_mode: str = "") -> str:
    """根据专辑 ID 获取专辑信息和歌曲列表"""
    error = _session_error()
    if error:
        return error
    detail = get_album_detail(album_id, config.DEFAULT_COVER_URL, max_songs=20)
    _remember_song_results(ctx.chat_key, detail.songs)
    title = f"网易云专辑 | {detail.album.name}"
    text = "\n".join(
        [
            title,
            "",
            f"歌手：{detail.album.artist}",
            f"歌曲数：{detail.album.song_count or len(detail.songs)}",
            f"专辑 ID：{detail.album.id}",
            "",
            _format_songs("歌曲列表：", detail.songs),
        ],
    )
    await _send_search_result(ctx, title, text, detail.songs, _output_mode(output_mode))
    return text


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "播放网易云歌曲")
async def play_song(ctx: AgentCtx, song_id: int, quality: str = "") -> str:
    """播放指定 ID 的歌曲，用户指定音质时优先生效"""
    error = _session_error()
    if error:
        return error

    selected_quality = normalize_quality(quality, config.DEFAULT_QUALITY)
    song_detail = get_song_detail(song_id)
    song_name = song_detail["name"]
    artist_name = ", ".join([ar["name"] for ar in song_detail.get("ar", [])])
    audio = get_song_audio_info(song_id, selected_quality, config.DEFAULT_QUALITY)

    if ctx.adapter_key != "onebot_v11":
        text = (
            f"歌曲信息：\n"
            f"标题：{song_name}\n"
            f"艺术家：{artist_name}\n"
            f"ID：{song_id}\n"
            f"音质：{audio.quality}\n"
            f"链接：{audio.url}"
        )
        await ctx.send_text(text)
        return text

    from nonebot import get_bot
    from nonebot.adapters.onebot.v11 import ActionFailed, MessageSegment

    bot = get_bot()
    chat_type, target_id = parse_chat_key(ctx.chat_key)
    cover_url = await get_cover_url(song_detail, size=config.COVER_SIZE)

    card_sent = False
    if config.ENABLE_JSON_CARD and audio.url:
        json_payload = await get_signed_netease_card(
            song_id=song_id,
            title=song_name,
            artist=artist_name,
            cover_url=cover_url or "",
            music_url=audio.url,
        )
        if json_payload:
            try:
                json_msg = MessageSegment.json(json_payload)
                if chat_type == "private":
                    await bot.call_api("send_private_msg", user_id=target_id, message=json_msg)
                else:
                    await bot.call_api("send_group_msg", group_id=target_id, message=json_msg)
                card_sent = True
            except ActionFailed as e:
                plugin.logger.warning(f"JSON 卡片发送失败: {e}")

    if card_sent:
        return f"歌曲《{song_name}》卡片已发送，音质：{audio.quality}"

    if config.ENABLE_JSON_CARD:
        plugin.logger.warning(
            f"网易云音乐 JSON 卡片未发送成功，按 CARD_FALLBACK_MODE={_fallback_mode()} 降级处理",
        )

    fallback_mode = _fallback_mode()
    if fallback_mode == "none":
        return f"歌曲《{song_name}》卡片发送失败，已按配置不降级发送。"

    message_text = f"{song_name} - {artist_name}\n音质：{audio.quality}"
    if fallback_mode == "text":
        message_text = f"{message_text}\n网易云链接：https://music.163.com/#/song?id={song_id}\n音频链接：{audio.url}"
        if chat_type == "private":
            await bot.call_api("send_private_msg", user_id=target_id, message=message_text)
        else:
            await bot.call_api("send_group_msg", group_id=target_id, message=message_text)
        return f"歌曲《{song_name}》卡片发送失败，已发送文字链接。"

    if chat_type == "private":
        await bot.call_api("send_private_msg", user_id=target_id, message=message_text)
    else:
        await bot.call_api("send_group_msg", group_id=target_id, message=message_text)

    if cover_url and config.COVER_SIZE > 0:
        cover_msg = MessageSegment.image(cover_url)
        if chat_type == "private":
            await bot.call_api("send_private_msg", user_id=target_id, message=cover_msg)
        else:
            await bot.call_api("send_group_msg", group_id=target_id, message=cover_msg)

    voice_msg = MessageSegment.record(file=audio.url)
    if chat_type == "private":
        await bot.call_api("send_private_msg", user_id=target_id, message=voice_msg)
    else:
        await bot.call_api("send_group_msg", group_id=target_id, message=voice_msg)

    return f"歌曲《{song_name}》已发送，音质：{audio.quality}"


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "下载并发送网易云歌曲文件")
async def download_song(ctx: AgentCtx, song_id: int, quality: str = "") -> str:
    """下载并发送歌曲文件，仅发送 mp3/wav；ncm 加密格式会跳过"""
    error = _session_error()
    if error:
        return error
    selected_quality = normalize_quality(quality, config.DEFAULT_QUALITY)
    path, song_name, artist_name = await _download_audio_file(song_id, selected_quality)
    sandbox_path = await ctx.fs.mixed_forward_file(path, file_name=path.name)
    await ctx.send_file(sandbox_path)
    return f"已发送歌曲文件：{song_name} - {artist_name}"


@plugin.mount_command(
    name="cm_help",
    description="查看网易云点歌插件帮助",
    aliases=["网易云帮助", "点歌帮助"],
    permission=CommandPermission.PUBLIC,
    category="音乐",
)
async def cm_help_cmd(_context: CommandExecutionContext) -> CommandResponse:
    return CmdCtl.success(
        "\n".join(
            [
                "网易云点歌帮助",
                "",
                "/cm_search <关键词> [text|image] - 搜索歌曲",
                "/cm_artist <歌手> [text|image] - 按歌手搜索相关歌曲和专辑，最多各 20 条",
                "/cm_album <专辑ID或关键词> [text|image] - 获取专辑详情或搜索专辑",
                "/cm_play <编号或歌曲ID> [standard|higher|lossless] - 播放歌曲，命令音质优先于配置",
                "/cm_download <编号或歌曲ID> [standard|higher|lossless] - 下载并发送 mp3/wav 文件，ncm 会跳过",
                "",
                "配置项：NCM_COOKIE、DEFAULT_QUALITY、SEARCH_OUTPUT_MODE、IMAGE_BACKGROUND_URL、FONT_PATH 等。",
            ],
        ),
    )


@plugin.mount_command(
    name="cm_search",
    description="搜索网易云歌曲",
    aliases=["点歌", "搜歌", "cloudmusic_search"],
    usage="/cm_search <关键词> [text|image]",
    permission=CommandPermission.PUBLIC,
    category="音乐",
)
async def cm_search_cmd(
    context: CommandExecutionContext,
    query: Annotated[str, Arg("歌曲关键词", positional=True, greedy=True)] = "",
) -> CommandResponse:
    try:
        cleaned_query, mode = _extract_output_mode(query)
        if not cleaned_query:
            return CmdCtl.failed("请输入搜索关键词，例如：/cm_search 晴天")
        ctx = await AgentCtx.create_by_chat_key(context.chat_key)
        text = await search_songs(ctx, cleaned_query, mode)
        return CmdCtl.success(f"已搜索歌曲：{cleaned_query}", data={"result": text})
    except Exception as e:
        plugin.logger.exception(f"搜索歌曲失败: {e}")
        return CmdCtl.failed(f"搜索歌曲失败：{e}")


@plugin.mount_command(
    name="cm_artist",
    description="按歌手搜索相关歌曲和专辑",
    aliases=["搜歌手", "歌手歌曲"],
    usage="/cm_artist <歌手> [text|image]",
    permission=CommandPermission.PUBLIC,
    category="音乐",
)
async def cm_artist_cmd(
    context: CommandExecutionContext,
    query: Annotated[str, Arg("歌手关键词", positional=True, greedy=True)] = "",
) -> CommandResponse:
    try:
        cleaned_query, mode = _extract_output_mode(query)
        if not cleaned_query:
            return CmdCtl.failed("请输入歌手关键词，例如：/cm_artist 周杰伦")
        ctx = await AgentCtx.create_by_chat_key(context.chat_key)
        text = await search_artist_music(ctx, cleaned_query, mode)
        return CmdCtl.success(f"已搜索歌手相关内容：{cleaned_query}", data={"result": text})
    except Exception as e:
        plugin.logger.exception(f"搜索歌手相关内容失败: {e}")
        return CmdCtl.failed(f"搜索歌手相关内容失败：{e}")


@plugin.mount_command(
    name="cm_album",
    description="获取网易云专辑详情或搜索专辑",
    aliases=["搜专辑", "专辑"],
    usage="/cm_album <专辑ID或关键词> [text|image]",
    permission=CommandPermission.PUBLIC,
    category="音乐",
)
async def cm_album_cmd(
    context: CommandExecutionContext,
    query: Annotated[str, Arg("专辑 ID 或关键词", positional=True, greedy=True)] = "",
) -> CommandResponse:
    try:
        cleaned_query, mode = _extract_output_mode(query)
        if not cleaned_query:
            return CmdCtl.failed("请输入专辑 ID 或关键词，例如：/cm_album 35327877")
        ctx = await AgentCtx.create_by_chat_key(context.chat_key)
        if cleaned_query.isdigit():
            text = await get_album(ctx, int(cleaned_query), mode)
            return CmdCtl.success(f"已获取专辑：{cleaned_query}", data={"result": text})

        error = _session_error()
        if error:
            return CmdCtl.failed(error)
        albums = search_albums_from_ncm(cleaned_query, _result_limit(), config.DEFAULT_COVER_URL)
        title = f"网易云专辑搜索 | {cleaned_query}"
        text = _format_albums(title, albums)
        await _send_search_result(ctx, title, text, albums, mode)
        return CmdCtl.success(f"已搜索专辑：{cleaned_query}", data={"result": text})
    except Exception as e:
        plugin.logger.exception(f"获取专辑失败: {e}")
        return CmdCtl.failed(f"获取专辑失败：{e}")


@plugin.mount_command(
    name="cm_play",
    description="播放网易云歌曲",
    aliases=["播放歌曲", "放歌"],
    usage="/cm_play <歌曲ID> [standard|higher|lossless]",
    permission=CommandPermission.PUBLIC,
    category="音乐",
)
async def cm_play_cmd(
    context: CommandExecutionContext,
    query: Annotated[str, Arg("歌曲 ID 和可选音质", positional=True, greedy=True)] = "",
) -> CommandResponse:
    try:
        cleaned_query, quality = _extract_quality(query)
        ctx = await AgentCtx.create_by_chat_key(context.chat_key)
        song_id = _parse_song_id(cleaned_query, ctx.chat_key)
        text = await play_song(ctx, song_id, quality)
        return CmdCtl.success(text)
    except Exception as e:
        plugin.logger.exception(f"播放歌曲失败: {e}")
        return CmdCtl.failed(f"播放歌曲失败：{e}")


@plugin.mount_command(
    name="cm_download",
    description="下载并发送网易云歌曲文件",
    aliases=["下载歌曲", "发歌曲文件"],
    usage="/cm_download <歌曲ID> [standard|higher|lossless]",
    permission=CommandPermission.PUBLIC,
    category="音乐",
)
async def cm_download_cmd(
    context: CommandExecutionContext,
    query: Annotated[str, Arg("歌曲 ID 和可选音质", positional=True, greedy=True)] = "",
) -> CommandResponse:
    try:
        cleaned_query, quality = _extract_quality(query)
        ctx = await AgentCtx.create_by_chat_key(context.chat_key)
        song_id = _parse_song_id(cleaned_query, ctx.chat_key)
        text = await download_song(ctx, song_id, quality)
        return CmdCtl.success(text)
    except Exception as e:
        plugin.logger.exception(f"下载歌曲失败: {e}")
        return CmdCtl.failed(f"下载歌曲失败：{e}")


@plugin.mount_cleanup_method()
async def cleanup():
    """清理插件资源"""
    cleanup_pyncm_session()
