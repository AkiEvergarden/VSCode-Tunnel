"""
Admin API Handlers
==================
"""

from aiohttp import web

from ..registry import registry


async def handle_api_sessions(request: web.Request) -> web.Response:
    sessions = await registry.list_sessions()
    return web.json_response({"sessions": sessions, "count": len(sessions)})


async def handle_api_health(request: web.Request) -> web.Response:
    sessions = await registry.list_sessions()
    return web.json_response({"status": "ok", "active_sessions": len(sessions)})
