"""
Constants for the VSCode Tunnel Server
======================================
Protocol message types, path constants, and timeout configurations.
"""

# ======================== 消息类型常量 ========================
# 服务器 → Agent
T_HTTP_REQ = "hr"   # HTTP 请求
T_WS_MSG   = "wm"   # WebSocket 消息
T_WS_CLOSE = "wc"   # WebSocket 关闭
T_CLOSE    = "cl"   # 关闭子连接
T_PING     = "pi"   # 心跳

# Agent → 服务器
T_HTTP_RES = "hs"   # HTTP 响应头
T_HTTP_BODY = "hb"  # HTTP Body 分片
T_HTTP_END  = "he"  # HTTP 响应结束
T_ERROR     = "er"  # 错误
T_PONG      = "po"  # 心跳响应
T_REGISTER  = "rg"  # 注册（握手）

# ======================== 路径常量 ========================
SESSION_PREFIX = "/s/"
AGENT_WS_PATH  = "/__tunnel__"

# ======================== 超时配置 ========================
HEARTBEAT_INTERVAL = 25        # 心跳间隔（秒）
REQUEST_TIMEOUT    = 300       # 单个请求超时（秒）
