"""网易云音乐 API 封装，直接使用 apis.netstart.cn/music HTTP 接口。"""

import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .models import AlbumDetail, AlbumInfo, ArtistSearchResult, AudioDownloadInfo, SongInfo

logger = logging.getLogger(__name__)

API_BASE = "https://apis.netstart.cn/music"
LOGIN_COOKIE_KEYS = ("MUSIC_A_T", "MUSIC_R_T", "__csrf", "MUSIC_SNS", "MUSIC_U", "NMTID")

_session_state = {
    "initialized": False,
    "last_cookie": "",
}


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
    }
    cookie = str(_session_state.get("last_cookie") or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _api_get(path: str, params: Optional[dict[str, Any]] = None, timeout: int = 20) -> dict[str, Any]:
    url = f"{API_BASE}/{path.lstrip('/')}"
    query = dict(params or {})
    query.setdefault("timestamp", int(time.time() * 1000))
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_headers()) as client:
        response = client.get(url, params=query)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError(f"网易云接口 {path} 返回格式异常")
    return result


def _unwrap_result(result: dict[str, Any], api_name: str) -> dict[str, Any]:
    code = result.get("code")
    if code not in (None, 200):
        message = result.get("message") or result.get("msg") or result.get("error") or "未知错误"
        raise RuntimeError(f"网易云接口 {api_name} 调用失败: {code} {message}")
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
    cookie_parts: list[str] = []
    for key in LOGIN_COOKIE_KEYS:
        value = _extract_cookie_value(raw_cookie, key)
        if value is not None:
            cookie_parts.append(f"{key}={value}")
    return "; ".join(cookie_parts)


def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    cookies: dict[str, str] = {}
    if not cookie_string or not cookie_string.strip():
        return cookies
    for item in cookie_string.replace("\n", "; ").replace("\r", "").split(";"):
        item = item.strip()
        if "=" in item:
            key, _, value = item.partition("=")
            cookies[key.strip()] = value.strip()
    return cookies


def _artist_names(raw_artists: Any) -> str:
    if not isinstance(raw_artists, list):
        return "未知歌手"
    names = [str(item.get("name", "")).strip() for item in raw_artists if isinstance(item, dict)]
    return ", ".join(name for name in names if name) or "未知歌手"


def _first_dict(*values: Any) -> Dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _parse_song(data: Dict[str, Any], default_cover_url: str) -> SongInfo:
    album_data = _first_dict(data.get("al"), data.get("album"))
    artists = data.get("ar") or data.get("artists") or []
    cover_url_raw = album_data.get("picUrl") or album_data.get("blurPicUrl") or data.get("picUrl")
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


def normalize_quality(raw_quality: str, default_quality: str = "lossless") -> str:
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


def _quality_bitrate(quality: str) -> int:
    return {
        "standard": 128000,
        "higher": 192000,
        "exhigh": 320000,
        "lossless": 999000,
        "hires": 1999000,
    }.get(quality, 999000)


def ensure_session_initialized(cookie_string: str) -> Optional[str]:
    _session_state["initialized"] = True
    _session_state["last_cookie"] = cookie_string.strip() if cookie_string else ""
    return None


def _search_result(keyword: str, search_type: int, max_results: int) -> dict[str, Any]:
    return _unwrap_result(
        _api_get("search", {"keywords": keyword, "type": search_type, "limit": max_results, "offset": 0}),
        "search",
    )


def search_songs_from_ncm(keyword: str, max_results: int, default_cover_url: str) -> List[SongInfo]:
    result = _search_result(keyword, 1, max_results)
    songs_data = result.get("result", {}).get("songs", [])
    if not songs_data:
        raise ValueError(f"未找到与'{keyword}'相关的歌曲")

    songs: list[SongInfo] = []
    for item in songs_data[:max_results]:
        if not isinstance(item, dict):
            continue
        try:
            songs.append(_parse_song(item, default_cover_url))
        except Exception as e:
            logger.warning(f"处理歌曲 {item.get('name', 'Unknown')} 失败: {e}")
    if not songs:
        raise ValueError(f"未能解析'{keyword}'的搜索结果")
    return songs


def search_albums_from_ncm(keyword: str, max_results: int, default_cover_url: str) -> List[AlbumInfo]:
    result = _search_result(keyword, 10, max_results)
    albums_data = result.get("result", {}).get("albums", [])
    if not albums_data:
        raise ValueError(f"未找到与'{keyword}'相关的专辑")

    albums: list[AlbumInfo] = []
    for item in albums_data[:max_results]:
        if not isinstance(item, dict):
            continue
        try:
            albums.append(_parse_album(item, default_cover_url))
        except Exception as e:
            logger.warning(f"处理专辑 {item.get('name', 'Unknown')} 失败: {e}")
    if not albums:
        raise ValueError(f"未能解析'{keyword}'的专辑搜索结果")
    return albums


def search_artist_music_from_ncm(artist_keyword: str, max_results: int, default_cover_url: str) -> ArtistSearchResult:
    limit = min(max_results, 20)
    songs: list[SongInfo] = []
    albums: list[AlbumInfo] = []

    artist_result = _search_result(artist_keyword, 100, 3)
    artists = artist_result.get("result", {}).get("artists", [])
    artist_id = None
    if isinstance(artists, list) and artists:
        artist_id = artists[0].get("id") if isinstance(artists[0], dict) else None

    if artist_id:
        try:
            songs_result = _unwrap_result(_api_get("artist/top/song", {"id": artist_id}), "artist_top_song")
            raw_songs = songs_result.get("songs") or songs_result.get("data") or []
            songs = [_parse_song(item, default_cover_url) for item in raw_songs[:limit] if isinstance(item, dict)]
        except Exception as e:
            logger.warning(f"获取歌手热门歌曲失败，回退关键词搜索: {e}")

        try:
            albums_result = _unwrap_result(
                _api_get("artist/album", {"id": artist_id, "limit": limit, "offset": 0}),
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
    result = _unwrap_result(_api_get("song/detail", {"ids": str(song_id)}), "song_detail")
    songs = result.get("songs", [])
    if not isinstance(songs, list) or not songs:
        raise ValueError(f"未找到歌曲ID {song_id}")
    return songs[0]


def get_album_detail(album_id: int, default_cover_url: str, max_songs: int = 20) -> AlbumDetail:
    result = _unwrap_result(_api_get("album", {"id": str(album_id)}), "album")
    raw_album = result.get("album")
    if not isinstance(raw_album, dict):
        raise ValueError(f"未找到专辑ID {album_id}")

    raw_songs = result.get("songs") or raw_album.get("songs") or []
    songs = [_parse_song(item, default_cover_url) for item in raw_songs[:max_songs] if isinstance(item, dict)]
    return AlbumDetail(album=_parse_album(raw_album, default_cover_url), songs=songs)


def get_song_audio_info(song_id: int, quality: str, default_quality: str = "lossless") -> AudioDownloadInfo:
    normalized_quality = normalize_quality(quality, default_quality)
    bitrate = _quality_bitrate(normalized_quality)
    result = _unwrap_result(_api_get("song/download/url", {"id": str(song_id), "br": bitrate}), "song_download_url")
    item = result.get("data")
    if not isinstance(item, dict):
        raise RuntimeError("网易云音频接口返回格式异常")

    url = str(item.get("url") or "").strip()
    if not url:
        message = item.get("message") or "该歌曲暂时无法获取可播放音频 URL，可能需要会员、已下架或受版权限制"
        raise ValueError(str(message))

    parsed_ext = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
    extension = str(item.get("type") or item.get("encodeType") or parsed_ext or "mp3").lower()
    if extension not in {"mp3", "wav", "ncm", "flac"}:
        extension = "mp3" if parsed_ext not in {"mp3", "wav", "ncm", "flac"} else parsed_ext
    return AudioDownloadInfo(
        song_id=song_id,
        url=url,
        quality=str(item.get("level") or normalized_quality),
        extension=extension,
        size=int(item.get("size") or 0),
        bitrate=int(item.get("br") or bitrate),
    )


def send_phone_captcha(phone: str, country_code: str = "86") -> dict[str, Any]:
    return _unwrap_result(_api_get("captcha/sent", {"phone": phone, "ctcode": country_code or "86"}), "captcha_sent")


def verify_phone_captcha(phone: str, captcha: str, country_code: str = "86") -> dict[str, Any]:
    return _unwrap_result(
        _api_get("captcha/verify", {"phone": phone, "captcha": captcha, "ctcode": country_code or "86"}),
        "captcha_verify",
    )


def login_with_phone_captcha(phone: str, captcha: str, country_code: str = "86") -> tuple[str, dict[str, Any]]:
    result = _unwrap_result(
        _api_get(
            "login/cellphone",
            {"phone": phone, "captcha": captcha, "countrycode": country_code or "86", "ctcode": country_code or "86"},
        ),
        "login_cellphone",
    )
    raw_cookie = str(result.get("cookie") or result.get("data", {}).get("cookie") or "")
    cookie_string = build_login_cookie_string(raw_cookie) or raw_cookie
    if not cookie_string:
        raise RuntimeError("登录成功但响应未包含 Cookie，请稍后重试。")
    _session_state["last_cookie"] = cookie_string
    return cookie_string, result


def create_qr_login() -> tuple[str, str]:
    key_result = _unwrap_result(_api_get("login/qr/key"), "login_qr_key")
    key = str(key_result.get("data", {}).get("unikey") or "").strip()
    if not key:
        raise RuntimeError("二维码登录 key 获取失败")

    qr_result = _unwrap_result(_api_get("login/qr/create", {"key": key, "qrimg": "true"}), "login_qr_create")
    qrimg = str(qr_result.get("data", {}).get("qrimg") or "").strip()
    if not qrimg:
        raise RuntimeError("二维码图片生成失败")
    return key, qrimg


def check_qr_login(key: str) -> tuple[int, str, str]:
    result = _api_get("login/qr/check", {"key": key})
    code = int(result.get("code") or 0)
    message = str(result.get("message") or result.get("msg") or "")
    raw_cookie = str(result.get("cookie") or result.get("data", {}).get("cookie") or "")
    cookie_string = build_login_cookie_string(raw_cookie) or raw_cookie
    if code == 803 and cookie_string:
        _session_state["last_cookie"] = cookie_string
    return code, message, cookie_string


def cleanup_ncm_session():
    _session_state["initialized"] = False
    _session_state["last_cookie"] = ""
    logger.info("网易云 HTTP API 会话已清理")
