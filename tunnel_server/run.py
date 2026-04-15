"""
Entry Point
===========
"""

import argparse
import logging

from .app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tunnel-server")


def main():
    parser = argparse.ArgumentParser(description="VSCode Tunnel Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("VSCode Tunnel Server")
    log.info("监听: %s:%d", args.host, args.port)
    log.info("Agent 端点: ws://%s:%d/__tunnel__", args.host, args.port)
    log.info("代理前缀: /s/{session_id}/")
    log.info("=" * 50)

    app = create_app()
    from aiohttp import web
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
