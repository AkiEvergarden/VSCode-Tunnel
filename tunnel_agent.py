#!/usr/bin/env python3
"""
VSCode Tunnel Agent
===================
运行在容器内部，负责：
  1. 通过 WebSocket 连接到 Tunnel Server
  2. 注册 session_id
  3. 将隧道请求转发到本地 Code-Server
  4. 自动重连

环境变量:
  TUNNEL_SERVER  - 隧道服务器地址 (默认 ws://localhost:8080)
  SESSION_ID     - Session ID (默认自动生成)
  LOCAL_URL      - 本地 Code-Server 地址 (默认 http://127.0.0.1:8443)
  RETRY_INTERVAL - 重连间隔秒数 (默认 5)
  BASE_PATH      - 本地 Code-Server 的 base-path (默认 /s/{session_id})
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import time
from typing import Optional

import aiohttp

# ======================== 日志 ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tunnel-agent")

# ======================== 消息类型常量 ========================
T_HTTP_REQ  = "hr"
T_HTTP_RES  = "hs"
T_HTTP_BODY = "hb"
T_HTTP_END  = "he"
T_WS_MSG    = "wm"
T_WS_CLOSE  = "wc"
T_CLOSE     = "cl"
T_ERROR     = "er"
T_PING      = "pi"
T_PONG      = "po"
T_REGISTER  = "rg"

# ======================== 编码工具 ========================
def encode_body(data: bytes) -> str:
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")

def decode_body(data) -> bytes:
    if not data:
        return b""
    if isinstance(data, bytes):
        return data
    return base64.b64decode(data)


# ======================== Agent 核心类 ========================
class TunnelAgent:
    def __init__(
        self,
        server_url: str,
        session_id: str,
        local_url: str,
        base_path: str,
        retry_interval: float = 5,
    ):
        self.server_url = server_url
        self.session_id = session_id
        self.local_url = local_url
        self.base_path = base_path
        self.retry_interval = retry_interval
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._write_lock = asyncio.Lock()
        self._local_port = self._extract_port(local_url)

    def _extract_port(self, url: str) -> str:
        """从 URL 中提取端口"""
        try:
            from urllib.parse import urlparse
            p = urlparse(url)
            return p.port or ("443" if p.scheme == "https" else "80")
        except Exception:
            return "8080"

    async def _send(self, msg: dict):
        """安全发送 JSON 消息到服务器"""
        if self._ws is None or self._ws.closed:
            raise ConnectionError("WebSocket 未连接")
        async with self._write_lock:
            await self._ws.send_json(msg)

    async def run(self):
        """主循环：持续连接/重连"""
        while True:
            try:
                await self._connect_and_serve()
            except Exception as e:
                log.error("连接异常: %s", e)
            log.info(
                "%s 秒后重连 %s ...",
                self.retry_interval, self.server_url,
            )
            await asyncio.sleep(self.retry_interval)

    async def _connect_and_serve(self):
        """建立连接并处理消息"""
        tunnel_url = self.server_url.rstrip("/") + "/__tunnel__"
        log.info("连接隧道服务器: %s", tunnel_url)

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(tunnel_url, heartbeat=25) as ws:
                self._ws = ws
                log.info("WebSocket 已连接")

                # 1. 发送注册消息
                await self._send({
                    "t": T_REGISTER,
                    "sid": self.session_id,
                })
                log.info("已注册 Session: %s", self.session_id)

                # 2. 启动消息读取循环
                async for raw_msg in ws:
                    if raw_msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            msg = json.loads(raw_msg.data)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_message(msg)

                    elif raw_msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break

                log.info("WebSocket 连接已关闭")

    async def _handle_message(self, msg: dict):
        """处理从服务器收到的消息"""
        msg_type = msg.get("t")

        if msg_type == T_PING:
            await self._send({"t": T_PONG})
            return

        if msg_type == T_HTTP_REQ:
            asyncio.create_task(self._handle_http_request(msg))
        elif msg_type == T_WS_MSG:
            await self._handle_ws_message(msg)
        elif msg_type in (T_WS_CLOSE, T_CLOSE):
            log.debug("收到关闭: cid=%d", msg.get("c"))

    # ==================== HTTP 请求处理 ====================

    async def _handle_http_request(self, msg: dict):
        """处理 HTTP 请求：转发到本地 Code-Server"""
        cid = msg.get("c")
        method = msg.get("m", "GET")
        raw_path = msg.get("p", "/")
        headers = msg.get("h", {})
        body = decode_body(msg.get("b", ""))

        try:
            # 构建本地 URL
            # 服务器发来的路径是完整路径 /s/{sid}/rest/...
            # 需要剥离 base_path 前缀，因为 code-server 不支持 --base-path
            path = raw_path
            if path.startswith(self.base_path):
                path = path[len(self.base_path):] or "/"
            local_url = self.local_url.rstrip("/") + path

            log.info("转发 %s %s -> %s", method, raw_path, local_url)

            # 准备 headers
            filtered_headers = {}
            # WebSocket 升级请求需要保留 upgrade 和 connection headers
            HOP_BY_HOP = {
                "keep-alive", "transfer-encoding", "host",
            }
            is_ws_upgrade = headers.get("Upgrade", "").lower() == "websocket"
            for k, v in headers.items():
                lk = k.lower()
                # 对于 WebSocket 升级，保留 connection 和 upgrade
                if lk in HOP_BY_HOP:
                    continue
                if lk == "connection" and not is_ws_upgrade:
                    continue
                filtered_headers[k] = v

            # 判断是否为 WebSocket 升级请求
            if is_ws_upgrade:
                log.info("WebSocket 升级请求: %s", local_url)
                await self._handle_ws_upgrade(cid, local_url, filtered_headers)
            else:
                await self._forward_http(cid, method, local_url, filtered_headers, body)

        except Exception as e:
            log.error("HTTP 处理错误 cid=%d: %s", cid, e)
            try:
                await self._send({
                    "t": T_ERROR,
                    "c": cid,
                    "e": str(e),
                })
            except Exception:
                pass

    async def _forward_http(
        self,
        cid: int,
        method: str,
        url: str,
        headers: dict,
        body: bytes,
    ):
        """转发普通 HTTP 请求到本地 Code-Server"""
        timeout = aiohttp.ClientTimeout(total=300, sock_read=60)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, url,
                    headers=headers,
                    data=body if body else None,
                    allow_redirects=False,  # 手动处理重定向
                    ssl=False,
                ) as resp:
                    # 构建响应 headers
                    # 注意：aiohttp 会自动解压响应体，所以必须移除 content-encoding
                    resp_headers = {}
                    for k, v in resp.headers.items():
                        lk = k.lower()
                        if lk in ("connection", "keep-alive", "transfer-encoding", "content-encoding"):
                            continue
                        # 重写重定向 URL（将 localhost 地址转为相对路径）
                        if lk == "location":
                            v = self._rewrite_location(v)
                        resp_headers[k] = v

                    # 发送响应头
                    await self._send({
                        "t": T_HTTP_RES,
                        "c": cid,
                        "s": resp.status,
                        "h": resp_headers,
                    })

                    # 流式转发响应体
                    CHUNK = 32768  # 32KB
                    async for chunk in resp.content.iter_chunked(CHUNK):
                        await self._send({
                            "t": T_HTTP_BODY,
                            "c": cid,
                            "b": encode_body(chunk),
                        })

                    # 响应结束
                    await self._send({
                        "t": T_HTTP_END,
                        "c": cid,
                    })

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("转发 HTTP 错误 cid=%d: %s", cid, e)
            try:
                await self._send({
                    "t": T_ERROR,
                    "c": cid,
                    "e": str(e),
                })
            except Exception:
                pass

    def _rewrite_location(self, location: str) -> str:
        """重写重定向 URL：将 localhost 绝对地址转为带 base_path 的相对路径"""
        if not location:
            return location
        # http://localhost:port/path -> /s/{sid}/path
        for prefix in [
            f"http://localhost:{self._local_port}",
            f"http://127.0.0.1:{self._local_port}",
            f"ws://localhost:{self._local_port}",
            f"wss://localhost:{self._local_port}",
        ]:
            if location.startswith(prefix):
                path = location[len(prefix):] or "/"
                return self.base_path + path
        return location

    # ==================== WebSocket 代理处理 ====================

    async def _handle_ws_upgrade(
        self,
        cid: int,
        url: str,
        headers: dict,
    ):
        """处理 WebSocket 升级：连接本地 Code-Server 的 WebSocket"""
        # 将 http:// 转为 ws://
        ws_url = url
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[7:]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[8:]

        try:
            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(
                    ws_url,
                    headers=headers,
                    ssl=False,
                    heartbeat=30,
                ) as local_ws:
                    log.debug("本地 WS 已连接: %s", ws_url)

                    # 通知服务器升级成功
                    await self._send({
                        "t": T_HTTP_RES,
                        "c": cid,
                        "s": 101,
                        "h": {},
                    })

                    # 启动两个方向的转发
                    task1 = asyncio.create_task(
                        self._ws_local_to_tunnel(cid, local_ws)
                    )
                    task2 = asyncio.create_task(
                        self._ws_tunnel_to_local(cid, local_ws)
                    )

                    done, pending = await asyncio.wait(
                        [task1, task2],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except asyncio.CancelledError:
                            pass

                    log.debug("本地 WS 已关闭: cid=%d", cid)

        except Exception as e:
            log.error("WS 升级失败 cid=%d: %s", cid, e)
            try:
                await self._send({
                    "t": T_ERROR,
                    "c": cid,
                    "e": f"WebSocket upgrade failed: {e}",
                })
            except Exception:
                pass

    async def _ws_local_to_tunnel(self, cid: int, local_ws):
        """本地 WS → 隧道：读取本地 WebSocket 消息，发送到隧道"""
        try:
            async for ws_msg in local_ws:
                if ws_msg.type == aiohttp.WSMsgType.TEXT:
                    await self._send({
                        "t": T_WS_MSG,
                        "c": cid,
                        "b": encode_body(ws_msg.data.encode("utf-8")),
                        "n": False,
                    })
                elif ws_msg.type == aiohttp.WSMsgType.BINARY:
                    await self._send({
                        "t": T_WS_MSG,
                        "c": cid,
                        "b": encode_body(ws_msg.data),
                        "n": True,
                    })
                elif ws_msg.type in (
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("local→tunnel 错误 cid=%d: %s", cid, e)
        finally:
            try:
                await self._send({"t": T_WS_CLOSE, "c": cid})
            except Exception:
                pass

    async def _ws_tunnel_to_local(self, cid: int, local_ws):
        """
        隧道 → 本地 WS：
        这个方法其实不直接从隧道读消息。
        隧道消息由 _handle_ws_message 处理，它需要找到对应的 local_ws。
        但由于设计上隧道消息通过 _handle_message 统一分发，
        我们需要另一种方式来路由 WS 消息到正确的本地连接。
        
        这个方法仅作为「等待本地连接关闭」的占位。
        """
        # 等待本地 WebSocket 关闭
        try:
            async for _ in local_ws:
                pass
        except Exception:
            pass

    async def _handle_ws_message(self, msg: dict):
        """
        处理从隧道收到的 WebSocket 消息（来自浏览器）。
        需要转发到对应的本地 WebSocket 连接。
        
        注意：由于架构设计，这个方法接收的是主读取循环分发过来的 WS 消息。
        实际的 WS 连接管理由 _handle_ws_upgrade 中的 task 负责。
        """
        # 这个方法在当前简单架构中暂不需要复杂实现，
        # 因为 WS 消息的双向转发在 _handle_ws_upgrade 中完成。
        # 如果需要从隧道主动接收 WS 消息（而非通过回调），
        # 需要一个 cid -> local_ws 的映射。
        pass

    # ==================== 本地 WS 连接映射 ====================
    # 用于 _handle_message 中处理 WS_MSG 时，找到对应的本地连接
    # 需要在 _handle_ws_upgrade 成功后注册，关闭时注销


# ======================== 增强：完整 WS 消息路由 ====================
# 上面的基础版本中，WS 消息从隧道到本地的路由有 gap。
# 下面给出完整的增强实现：

class TunnelAgentFull(TunnelAgent):
    """增强版 Agent，正确处理双向 WebSocket 消息路由"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._local_ws_map: dict = {}  # cid -> local_ws
        self._local_ws_lock = asyncio.Lock()

    async def _handle_ws_upgrade(self, cid: int, url: str, headers: dict):
        """增强版：注册 local_ws 到映射"""
        ws_url = url
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[7:]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[8:]

        log.info("尝试连接本地 WebSocket: %s, headers=%s", ws_url, headers)

        # 移除可能导致 403 的 headers（Origin 等）
        ws_headers = {}
        for k, v in headers.items():
            lk = k.lower()
            # 跳过可能导致跨域问题的 headers
            if lk in ("origin", "host"):
                continue
            ws_headers[k] = v

        try:
            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(
                    ws_url, headers=ws_headers, ssl=False, heartbeat=30,
                ) as local_ws:
                    # 注册映射
                    async with self._local_ws_lock:
                        self._local_ws_map[cid] = local_ws

                    log.info("本地 WS 已连接: cid=%d, url=%s", cid, ws_url)

                    await self._send({
                        "t": T_HTTP_RES, "c": cid, "s": 101, "h": {},
                    })

                    # 本地 → 隧道：读取本地 WebSocket 消息并发送到隧道
                    local_to_tunnel = asyncio.create_task(
                        self._ws_local_to_tunnel(cid, local_ws)
                    )

                    # 等待本地连接关闭或任务完成
                    # 注意：不再在这里读取 local_ws，因为 local_to_tunnel 已经在读取
                    try:
                        await local_to_tunnel
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        log.debug("local_to_tunnel 异常: %s", e)

                    log.info("本地 WS 关闭: cid=%d", cid)

        except Exception as e:
            log.error("WS 升级失败 cid=%d: %s", cid, e)
            try:
                await self._send({
                    "t": T_ERROR, "c": cid,
                    "e": f"WS upgrade failed: {e}",
                })
            except Exception:
                pass
        finally:
            async with self._local_ws_lock:
                self._local_ws_map.pop(cid, None)

    async def _handle_ws_message(self, msg: dict):
        """增强版：将隧道的 WS 消息转发到本地 WebSocket"""
        cid = msg.get("c")
        data = decode_body(msg.get("b", ""))
        is_binary = msg.get("n", False)

        async with self._local_ws_lock:
            local_ws = self._local_ws_map.get(cid)

        if local_ws is None or local_ws.closed:
            log.warning("WS 消息无对应本地连接: cid=%d", cid)
            return

        try:
            if is_binary:
                await local_ws.send_bytes(data)
            else:
                await local_ws.send_str(data.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("转发 WS 消息到本地失败 cid=%d: %s", cid, e)

    async def _handle_message(self, msg: dict):
        """增强版消息分发"""
        msg_type = msg.get("t")

        if msg_type == T_PING:
            await self._send({"t": T_PONG})
        elif msg_type == T_HTTP_REQ:
            asyncio.create_task(self._handle_http_request(msg))
        elif msg_type == T_WS_MSG:
            await self._handle_ws_message(msg)
        elif msg_type in (T_WS_CLOSE, T_CLOSE):
            cid = msg.get("c")
            async with self._local_ws_lock:
                local_ws = self._local_ws_map.get(cid)
            if local_ws and not local_ws.closed:
                await local_ws.close()


# ======================== 主入口 ========================
def main():
    parser = argparse.ArgumentParser(description="VSCode Tunnel Agent")
    parser.add_argument("--server", default=os.getenv("TUNNEL_SERVER", "ws://localhost:8080"))
    parser.add_argument("--sid", default=os.getenv("SESSION_ID", ""))
    parser.add_argument("--local", default=os.getenv("LOCAL_URL", "http://127.0.0.1:8443"))
    parser.add_argument("--retry", type=float, default=float(os.getenv("RETRY_INTERVAL", "5")))
    args = parser.parse_args()

    # 生成 Session ID
    sid = args.sid
    if not sid:
        import random, string
        sid = os.getenv("SESSION_ID", "")
        if not sid:
            sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            os.environ["SESSION_ID"] = sid

    # 计算 base_path
    base_path = f"/s/{sid}"

    log.info("=" * 50)
    log.info("VSCode Tunnel Agent")
    log.info("Session ID: %s", sid)
    log.info("隧道服务器: %s", args.server)
    log.info("本地服务: %s", args.local)
    log.info("Base Path: %s", base_path)
    log.info("=" * 50)

    agent = TunnelAgentFull(
        server_url=args.server,
        session_id=sid,
        local_url=args.local,
        base_path=base_path,
        retry_interval=args.retry,
    )

    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
