"""
Application Factory
===================
"""

import logging

from aiohttp import web

from .constants import AGENT_WS_PATH, SESSION_PREFIX
from .handlers.agent import handle_agent_ws
from .handlers.proxy import handle_proxy
from .handlers.api import handle_api_sessions, handle_api_health
from .handlers.index import handle_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tunnel-server")


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
