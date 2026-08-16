"""数据模型定义"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SongInfo(BaseModel):
    """单首歌曲的信息模型"""

    id: int = Field(..., description="歌曲ID")
    name: str = Field(..., description="歌曲名称")
    artist: str = Field(..., description="艺术家")
    album: str = Field(..., description="专辑名称")
    duration: int = Field(..., description="时长(毫秒)")
    cover_url: str = Field(..., description="封面URL")


class AlbumInfo(BaseModel):
    """专辑信息模型"""

    id: int = Field(..., description="专辑ID")
    name: str = Field(..., description="专辑名称")
    artist: str = Field(default="未知歌手", description="艺术家")
    song_count: int = Field(default=0, description="歌曲数量")
    publish_time: Optional[int] = Field(default=None, description="发布时间戳")
    cover_url: str = Field(default="", description="封面URL")


class ArtistSearchResult(BaseModel):
    """按歌手聚合的搜索结果"""

    keyword: str = Field(..., description="歌手关键词")
    songs: list[SongInfo] = Field(default_factory=list, description="相关歌曲")
    albums: list[AlbumInfo] = Field(default_factory=list, description="相关专辑")


class AlbumDetail(BaseModel):
    """专辑详情模型"""

    album: AlbumInfo = Field(..., description="专辑信息")
    songs: list[SongInfo] = Field(default_factory=list, description="专辑歌曲")


class AudioDownloadInfo(BaseModel):
    """歌曲下载信息"""

    song_id: int = Field(..., description="歌曲ID")
    url: str = Field(..., description="音频下载URL")
    quality: str = Field(..., description="音质")
    extension: str = Field(default="mp3", description="文件扩展名")
    size: int = Field(default=0, description="文件大小")
    bitrate: int = Field(default=0, description="码率")


class PlaySongResponseCard(BaseModel):
    """播放歌曲响应卡片"""

    type: Literal["music_card"] = Field(default="music_card", description="卡片类型")
    title: str = Field(..., description="歌曲标题")
    artist: str = Field(..., description="艺术家")
    album: str = Field(..., description="专辑名称")
    cover_url: str = Field(..., description="封面URL")
    audio_url: str = Field(..., description="音频URL")
    message: str = Field(..., description="附加信息")

