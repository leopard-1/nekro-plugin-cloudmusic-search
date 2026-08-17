"""网易云音乐 API 封装，基于 NeteaseCloudMusic Python SDK。"""

import json
import importlib.util
import re
import sys
import time
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from nekro_agent.api.plugin import dynamic_import_pkg
from nekro_agent.core import logger

from .models import AlbumDetail, AlbumInfo, ArtistSearchResult, AudioDownloadInfo, SongInfo

NeteaseCloudMusicApi = None
_api = None

_session_state = {
    "initialized": False,
    "last_cookie": None,
    "load_error": None,
}

LOGIN_COOKIE_KEYS = ("MUSIC_A_T", "MUSIC_R_T", "__csrf", "MUSIC_SNS", "MUSIC_U", "NMTID")


def _ensure_pkg_resources_compat() -> None:
    """为 NeteaseCloudMusic SDK 提供最小 pkg_resources 兼容层。

    该 SDK 只使用 pkg_resources.resource_filename() 读取包内 JS 文件。
    在精简容器里 pkg_resources 可能不存在，直接提供这个函数即可。
    """
    if "pkg_resources" in sys.modules:
        return

    module = ModuleType("pkg_resources")

    def resource_filename(package_or_requirement: str, resource_name: str) -> str:
        spec = importlib.util.find_spec(package_or_requirement)
        if (not spec or not spec.submodule_search_locations) and "." in package_or_requirement:
            spec = importlib.util.find_spec(package_or_requirement.rsplit(".", 1)[0])
        if not spec or not spec.submodule_search_locations:
            raise ModuleNotFoundError(f"No module named {package_or_requirement!r}")
        return str(Path(next(iter(spec.submodule_search_locations))) / resource_name)

    module.resource_filename = resource_filename
    sys.modules["pkg_resources"] = module


def _ensure_ncm_help_compat() -> None:
    """为缺失或失效的 NeteaseCloudMusic.help 提供兼容实现。"""
    if "NeteaseCloudMusic.help" in sys.modules:
        return

    module = ModuleType("NeteaseCloudMusic.help")
    exclude = {
        "/request/reference",
        "/avatar/upload",
        "/cloud",
        "/playlist/cover/update",
        "/voice/upload",
        "/register/anonimous",
        "/verify/getQr",
    }
    config_cache: dict[str, Any] | None = None

    def _load_config() -> dict[str, Any]:
        nonlocal config_cache
        if config_cache is not None:
            return config_cache
        spec = importlib.util.find_spec("NeteaseCloudMusic")
        if not spec or not spec.submodule_search_locations:
            config_cache = {}
            return config_cache
        config_path = Path(next(iter(spec.submodule_search_locations))) / "config.json"
        try:
            config_cache = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 NeteaseCloudMusic config.json 失败: {e}")
            config_cache = {}
        return config_cache

    def api_list() -> list[str]:
        config = _load_config()
        return [item for item in config if item not in exclude]

    def api_help(name: str | None = None) -> str:
        config = _load_config()
        if name is None:
            return "NeteaseCloudMusicApi request(apiName, queryDict)"
        if name in api_list():
            item = config.get(name, {})
            return f'name: {name}\n    {item.get("name", "")}\n    {item.get("explain", "")}'
        return f"apiName: {name} not found"

    module.api_list = api_list
    module.api_help = api_help
    sys.modules["NeteaseCloudMusic.help"] = module


def _clear_ncm_module_cache() -> None:
    """清理失败导入后残留的 NeteaseCloudMusic 模块缓存，保留兼容 help 模块。"""
    for name in list(sys.modules):
        if name == "NeteaseCloudMusic.help":
            continue
        if name == "NeteaseCloudMusic" or name.startswith("NeteaseCloudMusic."):
            sys.modules.pop(name, None)


def _load_sdk() -> Optional[str]:
    """懒加载 NeteaseCloudMusic，避免插件加载阶段被依赖安装问题卡死。"""
    global NeteaseCloudMusicApi

    if NeteaseCloudMusicApi:
        return None

    _ensure_pkg_resources_compat()
    _ensure_ncm_help_compat()

    try:
        module = import_module("NeteaseCloudMusic")
        NeteaseCloudMusicApi = module.NeteaseCloudMusicApi
        _session_state["load_error"] = None
        logger.info("NeteaseCloudMusic SDK 已在当前环境中可用")
        return None
    except Exception as e:
        logger.debug(f"直接导入 NeteaseCloudMusic 失败，将尝试动态安装: {e}")

    attempts = [
        ("NeteaseCloudMusic==0.1.10", "https://pypi.org/simple"),
        ("NeteaseCloudMusic", "https://pypi.org/simple"),
    ]
    errors: list[str] = []
    for package_spec, mirror in attempts:
        try:
            _clear_ncm_module_cache()
            _ensure_ncm_help_compat()
            module = dynamic_import_pkg(
                package_spec,
                import_name="NeteaseCloudMusic",
                mirror=mirror,
                timeout=240,
            )
            NeteaseCloudMusicApi = module.NeteaseCloudMusicApi
            _session_state["load_error"] = None
            logger.info(f"NeteaseCloudMusic SDK 加载成功: {package_spec} @ {mirror}")
            return None
        except Exception as e:
            errors.append(f"{package_spec} @ {mirror}: {e}")
            logger.warning(f"NeteaseCloudMusic SDK 加载失败: {package_spec} @ {mirror}: {e}")
            try:
                _clear_ncm_module_cache()
                _ensure_ncm_help_compat()
                module = import_module("NeteaseCloudMusic")
                NeteaseCloudMusicApi = module.NeteaseCloudMusicApi
                _session_state["load_error"] = None
                logger.info("NeteaseCloudMusic SDK 动态安装后直接导入成功")
                return None
            except Exception as import_error:
                errors.append(f"direct import after {package_spec}: {type(import_error).__name__}: {import_error}")
                logger.warning(
                    f"NeteaseCloudMusic SDK 直接导入仍失败: {type(import_error).__name__}: {import_error}",
                )

    error = "NeteaseCloudMusic SDK 安装或导入失败。请检查容器网络/PyPI 镜像，或在容器内预先安装 NeteaseCloudMusic。"
    _session_state["load_error"] = f"{error}\n" + "\n".join(errors[-2:])
    return str(_session_state["load_error"])


def _get_api():
    """获取 SDK 实例。SDK 文档提示实例不要跨线程使用，NA 插件通常在同一事件循环中调用。"""
    global _api

    load_error = _load_sdk()
    if load_error:
        raise RuntimeError(load_error)
    if _api is None:
        _api = NeteaseCloudMusicApi(debug=False, cache=False)
    return _api


def _request(api_name: str, query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    api = _get_api()
    payload = dict(query or {})
    payload.setdefault("timestamp", int(time.time() * 1000))
    if _session_state.get("last_cookie") and not payload.get("cookie"):
        payload["cookie"] = _session_state["last_cookie"]
    result = api.request(api_name, payload)
    if not isinstance(result, dict):
        raise RuntimeError(f"网易云接口 {api_name} 返回格式异常")
    return result


def _extract_cookie_value(raw_cookie: str, key: str) -> str | None:
    if not raw_cookie:
        return None
    pattern = rf"(?:^|[;,\s]){re.escape(key)}=([^;,]*)"
    match = re.search(pattern, raw_cookie)
    if not match:
        return None
    return match.group(1).strip()


def build_login_cookie_string(raw_cookie: str) -> str:
    """从登录响应 Set-Cookie 中提取插件需要的网易云 Cookie 字段。"""
    cookie_parts: list[str] = []
    for key in LOGIN_COOKIE_KEYS:
        value = _extract_cookie_value(raw_cookie, key)
        if value is not None:
            cookie_parts.append(f"{key}={value}")
    return "; ".join(cookie_parts)


def send_phone_captcha(phone: str, country_code: str = "86") -> dict[str, Any]:
    """向手机号发送网易云验证码。"""
    result = _unwrap_result(
        _request("captcha_sent", {"phone": phone, "ctcode": country_code or "86"}),
        "captcha_sent",
    )
    return result


def login_with_phone_captcha(phone: str, captcha: str, country_code: str = "86") -> tuple[str, dict[str, Any]]:
    """使用手机号验证码登录，并返回筛选后的 Cookie 字符串。"""
    api = _get_api()
    query = {
        "phone": phone,
        "captcha": captcha,
        "countrycode": country_code or "86",
        "ctcode": country_code or "86",
        "cookie": parse_cookie_string(str(_session_state.get("last_cookie") or "")),
        "realIP": getattr(api, "ip", "116.25.146.177") or "116.25.146.177",
        "timestamp": int(time.time() * 1000),
    }

    result = api.call_api("/login/cellphone", query)
    if not isinstance(result, dict):
        raise RuntimeError("网易云登录接口返回格式异常")
    result = _unwrap_result(result, "login_cellphone")

    raw_cookie = str(result.get("cookie") or result.get("data", {}).get("cookie") or "")
    cookie_string = build_login_cookie_string(raw_cookie)
    cookie_dict = parse_cookie_string(cookie_string)
    if not any(cookie_dict.get(key) for key in ("MUSIC_U", "MUSIC_A_T", "MUSIC_R_T")):
        raise RuntimeError("登录响应未包含有效登录 Cookie，请稍后重试或改用浏览器 Cookie。")
    if not cookie_dict.get("__csrf"):
        logger.warning("网易云登录响应未包含 __csrf，部分接口可能需要稍后重新登录。")

    _session_state["initialized"] = True
    _session_state["last_cookie"] = cookie_string
    return cookie_string, result


def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    """解析 Cookie 字符串为字典。保留给兼容逻辑使用。"""
    cookies = {}
    if not cookie_string or not cookie_string.strip():
        return cookies

    cookie_string = cookie_string.replace("\n", "; ").replace("\r", "")
    for item in cookie_string.split(";"):
        item = item.strip()
        if "=" in item:
            key, _, value = item.partition("=")
            cookies[key.strip()] = value.strip()
    return cookies


def normalize_quality(raw_quality: str, default_quality: str = "lossless") -> str:
    """归一化音质配置或用户会话里的音质要求。"""
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
        "exhigh": "exhigh",
        "extreme": "exhigh",
        "极高": "exhigh",
        "lossless": "lossless",
        "flac": "lossless",
        "无损": "lossless",
        "hires": "hires",
        "hi-res": "hires",
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
        song_count=int(data.get("size") or data.get("trackCount") or data.get("containedSong") or 0),
        publish_time=data.get("publishTime"),
        cover_url=cover_url,
    )


def _unwrap_result(result: dict[str, Any], api_name: str) -> dict[str, Any]:
    code = result.get("code")
    if code not in (None, 200):
        message = result.get("message") or result.get("msg") or result.get("error") or "未知错误"
        raise RuntimeError(f"网易云接口 {api_name} 调用失败: {code} {message}")
    return result


def ensure_session_initialized(cookie_string: str) -> Optional[str]:
    """确保 SDK 可用并记录 Cookie。"""
    load_error = _load_sdk()
    if load_error:
        _session_state["initialized"] = False
        return load_error

    if not cookie_string or not cookie_string.strip():
        _session_state["initialized"] = True
        _session_state["last_cookie"] = ""
        return None

    cookies_dict = parse_cookie_string(cookie_string)
    if not any(key in cookies_dict for key in ("MUSIC_U", "MUSIC_A", "MUSIC_A_T", "MUSIC_R_T")):
        _session_state["initialized"] = False
        return "Cookie 字符串缺少网易云登录凭据，请确认包含 MUSIC_U、MUSIC_A 或 MUSIC_A_T/MUSIC_R_T 等字段"

    _session_state["initialized"] = True
    _session_state["last_cookie"] = cookie_string
    logger.info("NeteaseCloudMusic SDK 会话初始化成功")
    return None


def search_songs_from_ncm(
    keyword: str,
    max_results: int,
    default_cover_url: str,
) -> List[SongInfo]:
    """从网易云音乐搜索歌曲。"""
    result = _unwrap_result(
        _request("search", {"keywords": keyword, "type": 1, "limit": max_results, "offset": 0}),
        "search",
    )
    songs_data: List[Dict[str, Any]] = result.get("result", {}).get("songs", [])
    if not songs_data:
        raise ValueError(f"未找到与'{keyword}'相关的歌曲")

    songs: List[SongInfo] = []
    for item in songs_data[:max_results]:
        try:
            songs.append(_parse_song(item, default_cover_url))
        except Exception as e:
            logger.warning(f"处理歌曲 {item.get('name', 'Unknown')} 失败: {e}")

    if not songs:
        raise ValueError(f"未能解析'{keyword}'的搜索结果")
    return songs


def search_albums_from_ncm(
    keyword: str,
    max_results: int,
    default_cover_url: str,
) -> List[AlbumInfo]:
    """从网易云音乐搜索专辑。"""
    result = _unwrap_result(
        _request("search", {"keywords": keyword, "type": 10, "limit": max_results, "offset": 0}),
        "search",
    )
    albums_data: List[Dict[str, Any]] = result.get("result", {}).get("albums", [])
    if not albums_data:
        raise ValueError(f"未找到与'{keyword}'相关的专辑")

    albums: List[AlbumInfo] = []
    for item in albums_data[:max_results]:
        try:
            albums.append(_parse_album(item, default_cover_url))
        except Exception as e:
            logger.warning(f"处理专辑 {item.get('name', 'Unknown')} 失败: {e}")

    if not albums:
        raise ValueError(f"未能解析'{keyword}'的专辑搜索结果")
    return albums


def search_artist_music_from_ncm(
    artist_keyword: str,
    max_results: int,
    default_cover_url: str,
) -> ArtistSearchResult:
    """根据歌手关键词聚合搜索相关歌曲和专辑。"""
    limit = min(max_results, 20)
    songs: list[SongInfo] = []
    albums: list[AlbumInfo] = []

    artist_result = _unwrap_result(
        _request("search", {"keywords": artist_keyword, "type": 100, "limit": 3, "offset": 0}),
        "search",
    )
    artists = artist_result.get("result", {}).get("artists", [])
    if artists:
        artist_id = artists[0].get("id")
        if artist_id:
            try:
                songs_result = _unwrap_result(_request("artist_top_song", {"id": artist_id}), "artist_top_song")
                raw_songs = songs_result.get("songs") or songs_result.get("data") or []
                songs = [_parse_song(item, default_cover_url) for item in raw_songs[:limit] if isinstance(item, dict)]
            except Exception as e:
                logger.warning(f"获取歌手热门歌曲失败，回退关键词搜索: {e}")

            try:
                albums_result = _unwrap_result(
                    _request("artist_album", {"id": artist_id, "limit": limit, "offset": 0}),
                    "artist_album",
                )
                raw_albums = albums_result.get("hotAlbums") or albums_result.get("artist", {}).get("albums") or []
                albums = [_parse_album(item, default_cover_url) for item in raw_albums[:limit] if isinstance(item, dict)]
            except Exception as e:
                logger.warning(f"获取歌手专辑失败，回退关键词搜索: {e}")

    if not songs:
        try:
            songs = search_songs_from_ncm(artist_keyword, limit, default_cover_url)
        except ValueError:
            songs = []
    if not albums:
        try:
            albums = search_albums_from_ncm(artist_keyword, limit, default_cover_url)
        except ValueError:
            albums = []

    if not songs and not albums:
        raise ValueError(f"未找到与歌手'{artist_keyword}'相关的歌曲或专辑")
    return ArtistSearchResult(keyword=artist_keyword, songs=songs[:limit], albums=albums[:limit])


def get_song_detail(song_id: int) -> Dict[str, Any]:
    """获取歌曲详情。"""
    result = _unwrap_result(_request("song_detail", {"ids": str(song_id)}), "song_detail")
    songs = result.get("songs", [])
    if not isinstance(songs, list) or not songs:
        raise ValueError(f"未找到歌曲ID {song_id}")
    return songs[0]


def get_album_detail(album_id: int, default_cover_url: str, max_songs: int = 20) -> AlbumDetail:
    """获取专辑详情。"""
    result = _unwrap_result(_request("album", {"id": str(album_id)}), "album")
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


def _audio_from_result(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data")
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else {}
    if isinstance(data, dict):
        return data
    return {}


def get_song_audio_info(song_id: int, quality: str, default_quality: str = "lossless") -> AudioDownloadInfo:
    """获取指定音质的歌曲音频 URL。"""
    normalized_quality = normalize_quality(quality, default_quality)

    result = _unwrap_result(
        _request("song_url_v1", {"id": str(song_id), "level": normalized_quality}),
        "song_url_v1",
    )
    item = _audio_from_result(result)

    if not item.get("url"):
        try:
            result = _unwrap_result(
                _request("song_download_url_v1", {"id": str(song_id), "level": normalized_quality}),
                "song_download_url_v1",
            )
            item = _audio_from_result(result)
        except Exception as e:
            logger.warning(f"新版下载 URL 获取失败: {e}")

    if not item.get("url"):
        bitrate = {
            "standard": "128000",
            "higher": "320000",
            "exhigh": "320000",
            "lossless": "999000",
            "hires": "999000",
        }.get(normalized_quality, "320000")
        result = _unwrap_result(_request("song_url", {"id": str(song_id), "br": bitrate}), "song_url")
        item = _audio_from_result(result)

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


def cleanup_ncm_session():
    """兼容旧入口名称，清理 SDK 实例。"""
    global _api
    _api = None
    _session_state["initialized"] = False
    _session_state["last_cookie"] = None
    logger.info("NeteaseCloudMusic SDK 会话已清理")
