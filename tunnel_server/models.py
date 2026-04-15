"""
Data models for the VSCode Tunnel Server
========================================
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict

from aiohttp import web


@dataclass
class AgentConn:
    """代表一个已连接的容器代理"""
    sid: str                          # Session ID
    ws: web.WebSocketResponse         # WebSocket 连接
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_at: float = field(default_factory=time.time)
    # cid -> asyncio.Queue，用于路由该 agent 的响应到正确的 HTTP handler
    pending: Dict[int, asyncio.Queue] = field(default_factory=dict)
    pending_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, msg: dict):
        """线程安全地发送 JSON 消息"""
        async with self.write_lock:
            await self.ws.send_json(msg)
        self.active_at = time.time()

    async def register_pending(self, cid: int, queue: asyncio.Queue):
        async with self.pending_lock:
            self.pending[cid] = queue

    async def unregister_pending(self, cid: int):
        async with self.pending_lock:
            self.pending.pop(cid, None)

    async def get_pending(self, cid: int) -> Optional[asyncio.Queue]:
        async with self.pending_lock:
            return self.pending.get(cid)
