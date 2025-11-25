"""网易云音乐 API 封装"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from nekro_agent.api.plugin import dynamic_import_pkg
from nekro_agent.core import logger

from .models import SongInfo

# 类型检查时导入
if TYPE_CHECKING:
    import pyncm
    from pyncm import GetCurrentSession, Session, SetCurrentSession
    from pyncm.apis import cloudsearch, track

# 运行时动态导入
pyncm = dynamic_import_pkg("pyncm==1.8.1", import_name="pyncm")

# 导入后需要显式导入子模块
from pyncm import GetCurrentSession, Session, SetCurrentSession
from pyncm.apis import cloudsearch, track

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
    # 调用pyncm API搜索
    search_result = cloudsearch.GetSearchResult(keyword, stype=cloudsearch.SONG)

    # pyncm API返回的是dict
    result_dict: Dict[str, Any] = search_result  # type: ignore
    songs_data: List[Dict[str, Any]] = result_dict.get("result", {}).get("songs", [])

    if not songs_data:
        raise ValueError(f"未找到与'{keyword}'相关的歌曲")

    # 处理歌曲数据
    song_infos: List[SongInfo] = []
    for s in songs_data[:max_results]:
        try:
            cover_url_raw = s.get("al", {}).get("picUrl")
            cover_url = f"{cover_url_raw}?param=140y140" if cover_url_raw else default_cover_url

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
            # 跳过格式异常的歌曲数据
            logger.warning(f"处理歌曲 {s.get('name', 'Unknown')} 失败: {e}")
            continue

    if not song_infos:
        raise ValueError(f"未能解析'{keyword}'的搜索结果")

    return song_infos


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


def cleanup_pyncm_session():
    """清理pyncm会话"""
    if _session_state["initialized"]:
        empty_session = Session()
        SetCurrentSession(empty_session)
        _session_state["initialized"] = False
        _session_state["last_cookie"] = None
        logger.info("pyncm会话已清理")

