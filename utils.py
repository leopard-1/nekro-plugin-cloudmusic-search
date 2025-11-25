"""工具函数模块"""

from typing import Tuple

from nekro_agent.core import logger


def parse_chat_key(chat_key: str) -> Tuple[str, int]:
    """解析chat_key提取聊天类型和ID
    
    Args:
        chat_key: 聊天标识符，格式如 "onebot_v11-group_123456" 或 "onebot_v11-private_123456"
        
    Returns:
        (chat_type, target_id) 元组，如 ("group", 123456) 或 ("private", 123456)
        
    Raises:
        ValueError: chat_key格式不正确
    """
    if not chat_key:
        raise ValueError("chat_key不能为空")
    
    # 格式: onebot_v11-group_123456 或 onebot_v11-private_123456
    if "-" not in chat_key:
        raise ValueError(f"chat_key格式错误，缺少'-'分隔符: {chat_key}")
    
    # 第一次分割: ["onebot_v11", "group_123456"]
    _, chat_part = chat_key.split("-", 1)
    
    # 第二次分割: ["group", "123456"]
    if "_" not in chat_part:
        raise ValueError(f"chat_key格式错误，缺少'_'分隔符: {chat_key}")
    
    chat_type, target_id_str = chat_part.split("_", 1)
    
    if chat_type not in ("group", "private"):
        raise ValueError(f"不支持的聊天类型: {chat_type}")
    
    if not target_id_str.isdigit():
        raise ValueError(f"无效的目标ID: {target_id_str}")
    
    return chat_type, int(target_id_str)


def format_duration(milliseconds: int) -> str:
    """格式化时长
    
    Args:
        milliseconds: 毫秒数
        
    Returns:
        格式化的时长字符串，如 "3:45" 或 "1:23:45"
    """
    seconds = milliseconds // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
