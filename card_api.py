"""音乐卡片 API 模块"""

from typing import Optional

import httpx
from nekro_agent.core import logger


async def get_song_play_url(song_id: int) -> str:
    """获取歌曲播放链接
    
    Args:
        song_id: 歌曲ID
        
    Returns:
        播放链接
    """
    return f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"


async def get_cover_url(song_detail: dict, size: int = 500) -> Optional[str]:
    """获取歌曲封面URL
    
    Args:
        song_detail: 歌曲详情
        size: 封面尺寸
        
    Returns:
        封面URL，失败返回None
    """
    album = song_detail.get("al", {})
    pic_url = album.get("picUrl", "")

    if pic_url:
        # 添加尺寸参数
        return f"{pic_url}?param={size}y{size}"

    return None


async def get_signed_netease_card(
    song_id: int,
    title: str,
    artist: str,
    cover_url: str,
    music_url: str,
) -> Optional[str]:
    """通过API获取签名的网易云音乐JSON卡片
    
    Args:
        song_id: 歌曲ID
        title: 歌曲标题
        artist: 艺术家
        cover_url: 封面URL
        music_url: 音乐URL
        
    Returns:
        JSON卡片数据，失败返回None
    """
    try:
        web_jump_url = f"https://music.163.com/#/song?id={song_id}"
        
        data = {
            "url": music_url,
            "jump": web_jump_url,
            "song": title,
            "singer": artist,
            "cover": cover_url if cover_url else "",
            "format": "163",  # 网易云音乐格式
        }
        
        api_url = "https://oiapi.net/api/QQMusicJSONArk"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(api_url, data=data)
            if resp.status_code == 200:
                resp_json = resp.json()
                if resp_json.get("code") == 1 and resp_json.get("message"):
                    logger.info("获取网易云音乐卡片成功")
                    return resp_json["message"]
                logger.warning(f"获取卡片失败: {resp_json}")
            else:
                logger.warning(f"卡片API请求失败: {resp.status_code}")
    except Exception as e:
        logger.warning(f"获取卡片出错: {e}")
    
    return None

