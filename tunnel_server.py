#!/usr/bin/env python3
"""
VSCode Tunnel Server
====================
部署在有公网IP的机器上，负责：
  1. 接收容器的 WebSocket 隧道连接（自动注册 session_id）
  2. 将用户浏览器请求 /s/{session_id}/* 代理到对应容器
  3. 支持普通 HTTP 和 WebSocket 双向代理

启动: python tunnel_server.py [--host 0.0.0.0] [--port 8080]
"""

import argparse
import asyncio
import json
import logging
import time
from typing import Optional, Dict
from dataclasses import dataclass, field

from aiohttp import web, WSMsgType

# ======================== 日志 ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tunnel-server")

# ======================== 消息类型常量 ========================
# 服务器 → Agent
T_HTTP_REQ   = "hr"   # HTTP 请求
T_WS_MSG     = "wm"   # WebSocket 消息
T_WS_CLOSE   = "wc"   # WebSocket 关闭
T_CLOSE      = "cl"   # 关闭子连接
T_PING       = "pi"   # 心跳

# Agent → 服务器
T_HTTP_RES   = "hs"   # HTTP 响应头
T_HTTP_BODY  = "hb"   # HTTP Body 分片
T_HTTP_END   = "he"   # HTTP 响应结束
T_ERROR      = "er"   # 错误
T_PONG       = "po"   # 心跳响应
T_REGISTER   = "rg"   # 注册（握手）

# ======================== 全局配置 ========================
SESSION_PREFIX = "/s/"
AGENT_WS_PATH  = "/__tunnel__"
HEARTBEAT_INTERVAL = 25        # 心跳间隔（秒）
REQUEST_TIMEOUT    = 300       # 单个请求超时（秒）
CID_COUNTER = 0                # 连接ID计数器


def next_cid() -> int:
    global CID_COUNTER
    CID_COUNTER += 1
    return CID_COUNTER


# ======================== Agent 连接表示 ========================
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


# ======================== Session 注册表 ========================
class SessionRegistry:
    """管理所有已连接的 Agent"""

    def __init__(self):
        self._agents: Dict[str, AgentConn] = {}
        self._lock = asyncio.Lock()

    async def register(self, agent: AgentConn):
        async with self._lock:
            old = self._agents.get(agent.sid)
            if old:
                log.warning("替换旧连接: %s", agent.sid)
                await old.ws.close()
            self._agents[agent.sid] = agent
            log.info("Agent 注册: %s (当前在线: %d)", agent.sid, len(self._agents))

    async def unregister(self, sid: str):
        async with self._lock:
            self._agents.pop(sid, None)
            log.info("Agent 注销: %s (当前在线: %d)", sid, len(self._agents))

    async def get(self, sid: str) -> Optional[AgentConn]:
        async with self._lock:
            return self._agents.get(sid)

    async def list_sessions(self) -> list:
        async with self._lock:
            return [
                {"sid": a.sid, "active_at": a.active_at}
                for a in self._agents.values()
            ]


registry = SessionRegistry()


# ======================== Agent WebSocket 连接处理 ========================
async def handle_agent_ws(request: web.Request) -> web.WebSocketResponse:
    """处理 Agent 的 WebSocket 连接"""
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
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
                    queue.put_nowait(msg)
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


# ======================== HTTP 代理处理 ========================
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
    # 非 WebSocket 请求过滤所有 hop-by-hop headers
    HOP_BY_HOP_NORMAL = {
        "connection", "keep-alive", "transfer-encoding",
        "upgrade", "proxy-connection", "proxy-authorization",
        "host",
    }
    # WebSocket 升级请求保留 connection 和 upgrade
    HOP_BY_HOP_WS = {
        "keep-alive", "transfer-encoding",
        "proxy-connection", "proxy-authorization",
        "host",
    }
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
    agent: AgentConn,
    path: str,
    headers: dict,
    body: bytes,
) -> web.StreamResponse:
    """代理普通 HTTP 请求"""
    cid = next_cid()
    queue = asyncio.Queue(maxsize=256)
    await agent.register_pending(cid, queue)
    try:
        # 发送请求到 agent
        await agent.send_json({
            "t": T_HTTP_REQ,
            "c": cid,
            "m": request.method,
            "p": path,
            "h": headers,
            "b": _encode_body(body),
        })

        # 创建流式响应
        resp = web.StreamResponse()
        header_written = False

        # 带超时的读取循环
        deadline = time.time() + REQUEST_TIMEOUT
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
                data = _decode_body(msg.get("b", ""))
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
    agent: AgentConn,
    path: str,
    headers: dict,
    body: bytes,
) -> web.WebSocketResponse:
    """代理 WebSocket 连接"""
    cid = next_cid()
    queue = asyncio.Queue(maxsize=256)
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
        "b": _encode_body(body),
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
                        "b": _encode_body(ws_msg.data.encode("utf-8")),
                        "n": False,  # text frame
                    })
                elif ws_msg.type == WSMsgType.BINARY:
                    await agent.send_json({
                        "t": T_WS_MSG,
                        "c": cid,
                        "b": _encode_body(ws_msg.data),
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
                    data = _decode_body(msg.get("b", ""))
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


# ======================== 管理 API ========================
async def handle_api_sessions(request: web.Request) -> web.Response:
    sessions = await registry.list_sessions()
    return web.json_response({"sessions": sessions, "count": len(sessions)})


async def handle_api_health(request: web.Request) -> web.Response:
    sessions = await registry.list_sessions()
    return web.json_response({"status": "ok", "active_sessions": len(sessions)})


async def handle_index(request: web.Request) -> web.Response:
    """首页说明"""
    host = request.headers.get("Host", "localhost:8080")
    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>VSCode Tunnel Gateway</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 60px auto; padding: 0 20px; color: #333; }}
h1 {{ border-bottom: 2px solid #007acc; padding-bottom: 10px; color: #007acc; }}
code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-size: 14px; }}
pre {{ background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 8px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #007acc; color: white; }}
.endpoint {{ font-family: monospace; }}
</style>
</head>
<body>
<h1>VSCode Tunnel Gateway</h1>
<p>通过 WebSocket 隧道安全地访问容器内的 Code-Server。</p>

<h2>访问方式</h2>
<pre><code>http://{host}<b>/s/{{session_id}}/</b></code></pre>

<h2>当前在线 Session</h2>
<table>
<tr><th>Session ID</th><th>访问地址</th><th>最后活跃</th></tr>
<!-- 动态填充 -->
</table>
<p id="empty" style="color:#999;">暂无在线 Session</p>

<h2>API</h2>
<table>
<tr><th>Endpoint</th><th>说明</th></tr>
<tr><td class="endpoint">GET /__api__/sessions</td><td>列出所有在线 Session</td></tr>
<tr><td class="endpoint">GET /__api__/health</td><td>健康检查</td></tr>
<tr><td class="endpoint">WS  /__tunnel__</td><td>Agent 隧道连接端点</td></tr>
</table>

<script>
fetch('/__api__/sessions')
  .then(r => r.json())
  .then(data => {{
    const tbody = document.querySelector('table tbody') || document.querySelector('table');
    if (data.count === 0) return;
    document.getElementById('empty').style.display = 'none';
    data.sessions.forEach(s => {{
      const host = location.host;
      const url = location.protocol + '//' + host + '/s/' + s.sid + '/';
      const tr = document.createElement('tr');
      tr.innerHTML = '<td><code>' + s.sid + '</code></td>'
        + '<td><a href="' + url + '" target="_blank">' + url + '</a></td>'
        + '<td>' + new Date(s.active_at * 1000).toLocaleString() + '</td>';
      tbody.appendChild(tr);
    }});
  }});
</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html; charset=utf-8")


# ======================== 编码工具 ========================
import base64


def _encode_body(data: bytes) -> str:
    """将 bytes 编码为 base64 字符串（用于 JSON 传输）"""
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")


def _decode_body(data: str) -> bytes:
    """将 base64 字符串解码为 bytes"""
    if not data:
        return b""
    return base64.b64decode(data)


# ======================== 应用入口 ========================
def create_app() -> web.Application:
    app = web.Application()

    # Agent 隧道连接
    app.router.add_route("*", AGENT_WS_PATH, handle_agent_ws)

    # 用户代理（/s/{sid}/**）
    app.router.add_route("*", "/s/{_tail:.+}", handle_proxy)
    app.router.add_route("*", "/s/{_sid}", handle_proxy)

    # 管理 API
    app.router.add_get("/__api__/sessions", handle_api_sessions)
    app.router.add_get("/__api__/health", handle_api_health)

    # 首页
    app.router.add_get("/", handle_index)

    return app


def main():
    parser = argparse.ArgumentParser(description="VSCode Tunnel Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("VSCode Tunnel Server")
    log.info("监听: %s:%d", args.host, args.port)
    log.info("Agent 端点: ws://%s:%d%s", args.host, args.port, AGENT_WS_PATH)
    log.info("代理前缀: %s{session_id}/", SESSION_PREFIX)
    log.info("=" * 50)

    app = create_app()
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
