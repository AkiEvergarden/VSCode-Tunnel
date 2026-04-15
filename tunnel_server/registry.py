"""
Session Registry for the VSCode Tunnel Server
=============================================
Manages all connected agents.
"""

import asyncio
from typing import Optional, List, Dict

from .models import AgentConn


class SessionRegistry:
    """管理所有已连接的 Agent"""

    def __init__(self):
        self._agents: Dict[str, AgentConn] = {}
        self._lock = asyncio.Lock()

    async def register(self, agent: AgentConn):
        async with self._lock:
            old = self._agents.get(agent.sid)
            if old:
                await old.ws.close()
            self._agents[agent.sid] = agent

    async def unregister(self, sid: str):
        async with self._lock:
            self._agents.pop(sid, None)

    async def get(self, sid: str) -> Optional[AgentConn]:
        async with self._lock:
            return self._agents.get(sid)

    async def list_sessions(self) -> List[dict]:
        async with self._lock:
            return [
                {"sid": a.sid, "active_at": a.active_at}
                for a in self._agents.values()
            ]


# 模块级单例
registry = SessionRegistry()
