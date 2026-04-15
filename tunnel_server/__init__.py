"""
VSCode Tunnel Server
====================
A WebSocket-based tunnel server for accessing code-server instances running in containers.
"""

from .app import create_app
from .run import main
from .models import AgentConn
from .registry import SessionRegistry, registry
from .constants import (
    T_HTTP_REQ, T_HTTP_RES, T_HTTP_BODY, T_HTTP_END,
    T_WS_MSG, T_WS_CLOSE, T_CLOSE, T_ERROR,
    T_PING, T_PONG, T_REGISTER,
    SESSION_PREFIX, AGENT_WS_PATH,
    HEARTBEAT_INTERVAL, REQUEST_TIMEOUT,
)
from .utils import encode_body, decode_body, next_cid

__all__ = [
    # Application
    "create_app",
    "main",
    # Models
    "AgentConn",
    # Registry
    "SessionRegistry",
    "registry",
    # Constants - Message Types (Server→Agent)
    "T_HTTP_REQ",
    "T_WS_MSG",
    "T_WS_CLOSE",
    "T_CLOSE",
    "T_PING",
    # Constants - Message Types (Agent→Server)
    "T_HTTP_RES",
    "T_HTTP_BODY",
    "T_HTTP_END",
    "T_ERROR",
    "T_PONG",
    "T_REGISTER",
    # Constants - Paths
    "SESSION_PREFIX",
    "AGENT_WS_PATH",
    # Constants - Config
    "HEARTBEAT_INTERVAL",
    "REQUEST_TIMEOUT",
    # Utils
    "encode_body",
    "decode_body",
    "next_cid",
]
