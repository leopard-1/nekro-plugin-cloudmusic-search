"""数据模型定义"""

from typing import Literal

from pydantic import BaseModel, Field


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

