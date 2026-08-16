"""网易云音乐 API 封装"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse

from nekro_agent.api.plugin import dynamic_import_pkg
from nekro_agent.core import logger

from .models import AlbumDetail, AlbumInfo, ArtistSearchResult, AudioDownloadInfo, SongInfo

# 类型检查时导入
if TYPE_CHECKING:
    import pyncm
    from pyncm import GetCurrentSession, Session, SetCurrentSession
    from pyncm.apis import album, cloudsearch, track

# 运行时动态导入
pyncm = dynamic_import_pkg(
    "pyncm==1.8.1",
    import_name="pyncm",
    mirror="https://pypi.com.cn/simple",
)

# 导入后需要显式导入子模块
from pyncm import GetCurrentSession, Session, SetCurrentSession
from pyncm.apis import album, cloudsearch, track

# 会话管理状态
_session_state = {
    "initialized": False,
    "last_cookie": None,
}


def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
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


def normalize_quality(raw_quality: str, default_quality: str = "lossless") -> str:
    """归一化音质配置或用户会话里的音质要求"""
    value = (raw_quality or "").strip().lower()
    mapping = {
        "standard": "standard",
        "std": "standard",
        "normal": "standard",
        "mp3": "standard",
        "标准": "standard",
        "普通": "standard",
        "higher": "higher",
        "high": "higher",
        "hq": "higher",
        "较高": "higher",
        "高": "higher",
        "高音质": "higher",
        "lossless": "lossless",
        "flac": "lossless",
        "无损": "lossless",
        "极高": "lossless",
    }
    if value in mapping:
        return mapping[value]
    default_value = (default_quality or "lossless").strip().lower()
    return mapping.get(default_value, "lossless")


def _first_dict(*values: Any) -> Dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _artist_names(raw_artists: Any) -> str:
    if not isinstance(raw_artists, list):
        return "未知歌手"
    names = [str(item.get("name", "")).strip() for item in raw_artists if isinstance(item, dict)]
    return ", ".join(name for name in names if name) or "未知歌手"


def _parse_song(data: Dict[str, Any], default_cover_url: str) -> SongInfo:
    album_data = _first_dict(data.get("al"), data.get("album"))
    artists = data.get("ar") or data.get("artists") or []
    cover_url_raw = album_data.get("picUrl") or data.get("picUrl")
    cover_url = f"{cover_url_raw}?param=140y140" if cover_url_raw else default_cover_url
    return SongInfo(
        id=int(data["id"]),
        name=str(data.get("name") or "未知歌曲"),
        artist=_artist_names(artists),
        album=str(album_data.get("name") or "未知专辑"),
        duration=int(data.get("dt") or data.get("duration") or 0),
        cover_url=cover_url,
    )


def _parse_album(data: Dict[str, Any], default_cover_url: str) -> AlbumInfo:
    artist_data = _first_dict(data.get("artist"))
    artists = data.get("artists") or ([artist_data] if artist_data else [])
    cover_url_raw = data.get("picUrl") or data.get("blurPicUrl")
    cover_url = f"{cover_url_raw}?param=180y180" if cover_url_raw else default_cover_url
    return AlbumInfo(
        id=int(data["id"]),
        name=str(data.get("name") or "未知专辑"),
        artist=_artist_names(artists),
        song_count=int(data.get("size") or data.get("trackCount") or 0),
        publish_time=data.get("publishTime"),
        cover_url=cover_url,
    )


def ensure_session_initialized(cookie_string: str) -> Optional[str]:
    """确保 pyncm 会话已初始化（支持配置热重载）
    
    Args:
        cookie_string: Cookie 字符串
        
    Returns:
        错误信息，如果成功则返回 None
    """
    # 检查 Cookie 是否为空
    if not cookie_string or not cookie_string.strip():
        _session_state["initialized"] = False
        return "未配置网易云音乐Cookie，请在插件配置中填写完整的Cookie字符串"
    
    # 检查配置是否变更
    if _session_state["initialized"] and _session_state["last_cookie"] == cookie_string:
        # 配置未变更，无需重新初始化
        return None
    
    # 解析 Cookie 字符串
    cookies_dict = parse_cookie_string(cookie_string)
    
    # 验证必需字段
    required_keys = ["MUSIC_U", "__csrf"]
    missing_keys = [k for k in required_keys if k not in cookies_dict]
    if missing_keys:
        _session_state["initialized"] = False
        return f"Cookie字符串缺少必需字段: {', '.join(missing_keys)}。请确保Cookie包含 MUSIC_U 和 __csrf 字段"
    
    # 创建并设置 Session
    session = Session()
    for key, value in cookies_dict.items():
        session.cookies.set(key, value)
    SetCurrentSession(session)
    
    # 更新状态
    _session_state["initialized"] = True
    _session_state["last_cookie"] = cookie_string
    logger.info("pyncm会话初始化成功")
    
    return None


def _ensure_search_result(result: Any, keyword: str) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError(f"搜索'{keyword}'失败：接口返回格式异常")
    result_data = result.get("result")
    if not isinstance(result_data, dict):
        raise ValueError(f"搜索'{keyword}'失败：接口没有返回有效结果")
    return result_data


def search_songs_from_ncm(
    keyword: str,
    max_results: int,
    default_cover_url: str,
) -> List[SongInfo]:
    """从网易云音乐搜索歌曲
    
    Args:
        keyword: 搜索关键词
        max_results: 最大返回结果数
        default_cover_url: 默认封面URL
        
    Returns:
        歌曲信息列表
        
    Raises:
        ValueError: 搜索无结果
    """
    # 调用 pyncm API 搜索。pyncm 参数名是 type，不是 stype。
    search_result = cloudsearch.GetSearchResult(
        keyword,
        type=cloudsearch.TYPE_SONG,
        limit=max_results,
    )

    result_data = _ensure_search_result(search_result, keyword)
    songs_data: List[Dict[str, Any]] = result_data.get("songs", [])

    if not songs_data:
        raise ValueError(f"未找到与'{keyword}'相关的歌曲")

    # 处理歌曲数据
    song_infos: List[SongInfo] = []
    for s in songs_data[:max_results]:
        try:
            song_infos.append(_parse_song(s, default_cover_url))
        except Exception as e:
            # 跳过格式异常的歌曲数据
            logger.warning(f"处理歌曲 {s.get('name', 'Unknown')} 失败: {e}")
            continue

    if not song_infos:
        raise ValueError(f"未能解析'{keyword}'的搜索结果")

    return song_infos


def search_albums_from_ncm(
    keyword: str,
    max_results: int,
    default_cover_url: str,
) -> List[AlbumInfo]:
    """从网易云音乐搜索专辑"""
    search_result = cloudsearch.GetSearchResult(
        keyword,
        type=cloudsearch.TYPE_ALBUM,
        limit=max_results,
    )
    result_data = _ensure_search_result(search_result, keyword)
    albums_data: List[Dict[str, Any]] = result_data.get("albums", [])
    if not albums_data:
        raise ValueError(f"未找到与'{keyword}'相关的专辑")

    album_infos: List[AlbumInfo] = []
    for item in albums_data[:max_results]:
        try:
            album_infos.append(_parse_album(item, default_cover_url))
        except Exception as e:
            logger.warning(f"处理专辑 {item.get('name', 'Unknown')} 失败: {e}")

    if not album_infos:
        raise ValueError(f"未能解析'{keyword}'的专辑搜索结果")
    return album_infos


def search_artist_music_from_ncm(
    artist_keyword: str,
    max_results: int,
    default_cover_url: str,
) -> ArtistSearchResult:
    """根据歌手关键词聚合搜索相关歌曲和专辑"""
    limit = min(max_results, 20)
    try:
        songs = search_songs_from_ncm(artist_keyword, limit, default_cover_url)
    except ValueError:
        songs = []
    try:
        albums = search_albums_from_ncm(artist_keyword, limit, default_cover_url)
    except ValueError:
        albums = []
    if not songs and not albums:
        raise ValueError(f"未找到与歌手'{artist_keyword}'相关的歌曲或专辑")
    return ArtistSearchResult(keyword=artist_keyword, songs=songs, albums=albums)


def get_song_detail(song_id: int) -> Dict[str, Any]:
    """获取歌曲详情
    
    Args:
        song_id: 歌曲ID
        
    Returns:
        歌曲详情字典
        
    Raises:
        ValueError: 歌曲不存在
    """
    track_details_result = track.GetTrackDetail([song_id])
    # pyncm 返回的类型不固定，这里做类型断言
    if isinstance(track_details_result, dict):
        track_details = track_details_result
    else:
        # 如果是 tuple，取第二个元素
        track_details = track_details_result[1] if isinstance(track_details_result, tuple) else {}

    if not isinstance(track_details, dict) or not track_details.get("songs"):
        raise ValueError(f"未找到歌曲ID {song_id}")

    songs = track_details.get("songs", [])
    if not isinstance(songs, list) or len(songs) == 0:
        raise ValueError(f"未找到歌曲ID {song_id}")

    return songs[0]  # type: ignore


def get_album_detail(album_id: int, default_cover_url: str, max_songs: int = 20) -> AlbumDetail:
    """获取专辑详情"""
    result = album.GetAlbumInfo(str(album_id))
    if not isinstance(result, dict):
        raise ValueError(f"未找到专辑ID {album_id}")

    raw_album = result.get("album")
    if not isinstance(raw_album, dict):
        raise ValueError(f"未找到专辑ID {album_id}")

    raw_songs = result.get("songs") or raw_album.get("songs") or []
    songs: List[SongInfo] = []
    for item in raw_songs[:max_songs]:
        if not isinstance(item, dict):
            continue
        try:
            songs.append(_parse_song(item, default_cover_url))
        except Exception as e:
            logger.warning(f"处理专辑歌曲失败: {e}")

    return AlbumDetail(album=_parse_album(raw_album, default_cover_url), songs=songs)


def get_song_audio_info(song_id: int, quality: str, default_quality: str = "lossless") -> AudioDownloadInfo:
    """获取指定音质的歌曲音频 URL"""
    normalized_quality = normalize_quality(quality, default_quality)
    result = track.GetTrackAudio([song_id], quality=normalized_quality, encodeType="mp3")
    if not isinstance(result, dict):
        raise ValueError("获取歌曲音频失败：接口返回格式异常")
    data = result.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("获取歌曲音频失败：接口没有返回音频数据")

    item = data[0]
    if not isinstance(item, dict):
        raise ValueError("获取歌曲音频失败：音频数据格式异常")
    url = str(item.get("url") or "").strip()
    if not url:
        raise ValueError("该歌曲暂时无法获取可播放音频 URL，可能需要会员、已下架或受版权限制")

    parsed_ext = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else "mp3"
    extension = "mp3" if parsed_ext not in {"mp3", "wav", "ncm"} else parsed_ext
    return AudioDownloadInfo(
        song_id=song_id,
        url=url,
        quality=normalized_quality,
        extension=extension,
        size=int(item.get("size") or 0),
        bitrate=int(item.get("br") or 0),
    )


def cleanup_pyncm_session():
    """清理pyncm会话"""
    if _session_state["initialized"]:
        empty_session = Session()
        SetCurrentSession(empty_session)
        _session_state["initialized"] = False
        _session_state["last_cookie"] = None
        logger.info("pyncm会话已清理")

