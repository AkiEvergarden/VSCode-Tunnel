"""
Utility functions for the VSCode Tunnel Server
==============================================
"""

import base64

# ======================== 连接 ID 生成器 ========================
CID_COUNTER = 0


def next_cid() -> int:
    """生成唯一的连接 ID"""
    global CID_COUNTER
    CID_COUNTER += 1
    return CID_COUNTER


# ======================== 编码工具 ========================


def encode_body(data: bytes) -> str:
    """将 bytes 编码为 base64 字符串（用于 JSON 传输）"""
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")


def decode_body(data: str) -> bytes:
    """将 base64 字符串解码为 bytes"""
    if not data:
        return b""
    return base64.b64decode(data)
