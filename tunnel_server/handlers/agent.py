"""
Agent WebSocket Handler
=======================
Handles the tunnel agent's WebSocket connection lifecycle.
"""

import asyncio
import json
import logging
import time

from aiohttp import web, WSMsgType

from ..constants import T_REGISTER, T_PONG, T_CLOSE, T_ERROR
from ..registry import registry
from ..models import AgentConn

log = logging.getLogger("tunnel-server")


async def handle_agent_ws(request: web.Request) -> web.WebSocketResponse:
    """处理 Agent 的 WebSocket 连接"""
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    # 1. 等待注册消息（第一条必须是 T_REGISTER）
    try:
        first_msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
    except Exception as e:
        log.error("等待注册消息超时: %s", e)
        await ws.close()
        return ws

    if not isinstance(first_msg, dict) or first_msg.get("t") != T_REGISTER:
        log.error("首条消息不是注册消息: %s", first_msg)
        await ws.close()
        return ws

    sid = first_msg.get("sid", "").strip()
    if not sid:
        log.error("注册消息缺少 sid")
        await ws.close()
        return ws

    agent = AgentConn(sid=sid, ws=ws)
    await registry.register(agent)

    # 2. 启动读取循环
    try:
        async for raw_msg in ws:
            if raw_msg.type == WSMsgType.TEXT:
                try:
                    msg = json.loads(raw_msg.data)
                except json.JSONDecodeError:
                    log.warning("无效 JSON: %s", raw_msg.data[:200])
                    continue

                agent.active_at = time.time()

                if msg.get("t") == T_PONG:
                    continue  # 心跳响应，无需处理

                # 路由到对应的 pending queue
                cid = msg.get("c")
                if cid is None:
                    log.warning("消息缺少 cid: %s", msg.get("t"))
                    continue

                queue = await agent.get_pending(cid)
                if queue is None:
                    log.warning("无 pending handler: cid=%d t=%s", cid, msg.get("t"))
                    continue

                try:
                    await queue.put(msg)
                except asyncio.QueueFull:
                    log.warning("pending queue 满: cid=%d", cid)

            elif raw_msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break

    except Exception as e:
        log.error("Agent %s 读取异常: %s", sid, e)
    finally:
        # 清理所有 pending handler（通知它们连接已断开）
        async with agent.pending_lock:
            for cid, q in agent.pending.items():
                q.put_nowait({"t": T_ERROR, "c": cid, "e": "Agent disconnected"})
        await registry.unregister(sid)
        await ws.close()
        log.info("Agent %s 连接关闭", sid)

    return ws
