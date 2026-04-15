"""
Proxy Handlers
=============
Handles user requests to /s/{session_id}/*, proxying them to the appropriate agent.
"""

import asyncio
import logging
import time

from aiohttp import web, WSMsgType

from ..constants import (
    T_HTTP_REQ, T_HTTP_RES, T_HTTP_BODY, T_HTTP_END,
    T_WS_MSG, T_WS_CLOSE, T_CLOSE, T_ERROR,
    SESSION_PREFIX,
)
from ..registry import registry
from ..utils import encode_body, decode_body, next_cid

log = logging.getLogger("tunnel-server")

# ======================== Hop-by-hop Headers ========================
HOP_BY_HOP_NORMAL = {
    "connection", "keep-alive", "transfer-encoding",
    "upgrade", "proxy-connection", "proxy-authorization",
    "host",
}
HOP_BY_HOP_WS = {
    "keep-alive", "transfer-encoding",
    "proxy-connection", "proxy-authorization",
    "host",
}


async def handle_proxy(request: web.Request) -> web.StreamResponse:
    """
    代理所有 /s/{session_id}/* 请求到对应容器
    """
    path = request.path
    query = request.query_string

    if not path.startswith(SESSION_PREFIX):
        return web.Response(status=400, text="无效路径，格式: /s/{session_id}/...")

    # 提取 session_id: /s/{sid}/rest/path...
    rest = path[len(SESSION_PREFIX):]
    slash_idx = rest.find("/")
    if slash_idx == -1:
        sid = rest
        sub_path = "/"
    else:
        sid = rest[:slash_idx]
        sub_path = rest[slash_idx:]

    # 构建完整原始路径（agent需要知道完整路径以支持 code-server --base-path）
    original_path = path
    if query:
        original_path += "?" + query

    # 查找 agent
    agent = await registry.get(sid)
    if agent is None:
        return web.Response(
            status=404,
            text=f"Session '{sid}' 不存在或容器未运行。\n"
                 f"请确认容器已启动并成功连接隧道服务器。",
            content_type="text/plain; charset=utf-8",
        )

    log.info("代理 %s %s -> session=%s", request.method, request.path, sid)

    # 读取请求 body
    body = await request.read()

    # 判断是否为 WebSocket 升级请求
    is_ws = request.headers.get("Upgrade", "").lower() == "websocket"

    # 构建请求 headers（过滤 Hop-by-hop headers）
    headers = {}
    hop_by_hop = HOP_BY_HOP_WS if is_ws else HOP_BY_HOP_NORMAL

    for key, value in request.headers.items():
        if key.lower() in hop_by_hop:
            continue
        if len(value) > 0:
            headers[key] = value

    if is_ws:
        return await _proxy_websocket(request, agent, original_path, headers, body)
    else:
        return await _proxy_http(request, agent, original_path, headers, body)


async def _proxy_http(
    request: web.Request,
    agent,
    path: str,
    headers: dict,
    body: bytes,
) -> web.StreamResponse:
    """代理普通 HTTP 请求"""
    cid = next_cid()
    queue = asyncio.Queue(maxsize=8192)
    await agent.register_pending(cid, queue)
    try:
        # 发送请求到 agent
        await agent.send_json({
            "t": T_HTTP_REQ,
            "c": cid,
            "m": request.method,
            "p": path,
            "h": headers,
            "b": encode_body(body),
        })

        # 创建流式响应
        resp = web.StreamResponse()
        header_written = False

        # 带超时的读取循环
        deadline = time.time() + 300
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                if not header_written:
                    return web.Response(status=504, text="Gateway Timeout")
                break

            try:
                msg = await asyncio.wait_for(queue.get(), timeout=min(remaining, 30))
            except asyncio.TimeoutError:
                if not header_written:
                    return web.Response(status=504, text="Gateway Timeout")
                break

            msg_type = msg.get("t")

            if msg_type == T_HTTP_RES:
                # 响应头
                status = msg.get("s", 200)
                resp_h = msg.get("h", {})
                for k, v in resp_h.items():
                    resp.headers[k] = v
                resp.set_status(status)
                await resp.prepare(request)
                header_written = True

            elif msg_type == T_HTTP_BODY:
                # 响应体分片
                if not header_written:
                    resp.set_status(200)
                    await resp.prepare(request)
                    header_written = True
                data = decode_body(msg.get("b", ""))
                if data:
                    await resp.write(data)
                    await resp.drain()  # 流式刷新

            elif msg_type == T_HTTP_END:
                # 响应结束
                break

            elif msg_type == T_ERROR:
                if not header_written:
                    return web.Response(
                        status=502,
                        text=f"Agent Error: {msg.get('e', 'unknown')}",
                    )
                break

            elif msg_type == T_CLOSE:
                break

        return resp

    finally:
        await agent.unregister_pending(cid)


async def _proxy_websocket(
    request: web.Request,
    agent,
    path: str,
    headers: dict,
    body: bytes,
) -> web.WebSocketResponse:
    """代理 WebSocket 连接"""
    cid = next_cid()
    queue = asyncio.Queue(maxsize=8192)
    await agent.register_pending(cid, queue)

    # 先升级浏览器连接为 WebSocket
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    # 发送请求到 agent（agent 将连接本地 WebSocket）
    await agent.send_json({
        "t": T_HTTP_REQ,
        "c": cid,
        "m": request.method,
        "p": path,
        "h": headers,
        "b": encode_body(body),
    })

    # 等待 agent 的连接结果
    browser_done = asyncio.Event()

    async def browser_to_agent():
        """浏览器 → Agent：读取浏览器 WS 消息，转发到隧道"""
        try:
            async for ws_msg in ws:
                if ws_msg.type == WSMsgType.TEXT:
                    await agent.send_json({
                        "t": T_WS_MSG,
                        "c": cid,
                        "b": encode_body(ws_msg.data.encode("utf-8")),
                        "n": False,  # text frame
                    })
                elif ws_msg.type == WSMsgType.BINARY:
                    await agent.send_json({
                        "t": T_WS_MSG,
                        "c": cid,
                        "b": encode_body(ws_msg.data),
                        "n": True,  # binary frame
                    })
                elif ws_msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except Exception as e:
            log.debug("browser→agent 错误 cid=%d: %s", cid, e)
        finally:
            await agent.send_json({"t": T_WS_CLOSE, "c": cid})
            browser_done.set()

    async def agent_to_browser():
        """Agent → 浏览器：从隧道读取消息，写入浏览器 WS"""
        try:
            while not browser_done.is_set():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    continue

                msg_type = msg.get("t")

                if msg_type == T_WS_MSG:
                    data = decode_body(msg.get("b", ""))
                    is_binary = msg.get("n", False)
                    if is_binary:
                        await ws.send_bytes(data)
                    else:
                        await ws.send_str(data.decode("utf-8", errors="replace"))

                elif msg_type in (T_WS_CLOSE, T_CLOSE, T_ERROR):
                    break

                elif msg_type == T_HTTP_RES:
                    # Agent 确认 WebSocket 连接成功
                    pass

        except Exception as e:
            log.debug("agent→browser 错误 cid=%d: %s", cid, e)
        finally:
            if not ws.closed:
                await ws.close()

    # 并行运行两个方向
    t1 = asyncio.create_task(browser_to_agent())
    t2 = asyncio.create_task(agent_to_browser())

    try:
        await asyncio.gather(t1, t2, return_exceptions=True)
    finally:
        await agent.unregister_pending(cid)

    return ws
