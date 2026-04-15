#!/usr/bin/env python3
"""
VSCode Code-Server Launcher
===========================
启动 code-server 并自动配置 --base-path，同时可选启动 tunnel-agent 连接隧道服务器。

用法:
    python start_vscode.py                    # 自动生成 session_id
    python start_vscode.py --sid mysession    # 指定 session_id
    python start_vscode.py --tunnel           # 同时启动 tunnel-agent
    python start_vscode.py --tunnel --server ws://remote:8080

环境变量:
    SESSION_ID     - 指定 session_id
    CODE_SERVER    - code-server 可执行文件路径 (默认 code-server)
    TUNNEL_SERVER  - 隧道服务器地址 (默认 ws://localhost:8080)
"""

import argparse
import logging
import os
import random
import shutil
import signal
import string
import subprocess
import sys
import time
import threading
from typing import Optional

# ======================== 日志 ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("vscode-launcher")


# ======================== 日志读取线程 ========================
def stream_reader(process: subprocess.Popen, prefix: str, stop_event: threading.Event):
    """从进程 stdout 读取日志并输出的线程函数"""
    try:
        while not stop_event.is_set() and process.poll() is None:
            line = process.stdout.readline()
            if line:
                log.info("[%s] %s", prefix, line.rstrip())
            elif process.poll() is not None:
                break
    except Exception as e:
        log.debug("%s 日志读取异常: %s", prefix, e)


# ======================== Code-Server 进程管理 ========================
class CodeServerLauncher:
    """管理 code-server 进程"""

    def __init__(
        self,
        session_id: str,
        port: int = 8443,
        code_server_cmd: str = "code-server",
        password: Optional[str] = None,
        bind_addr: str = "127.0.0.1",
        auth: str = "password",
        extra_args: list = None,
    ):
        self.session_id = session_id
        self.port = port
        self.code_server_cmd = code_server_cmd
        self.auth = auth
        self.password = password if auth == "password" else None
        if self.auth == "password" and not self.password:
            self.password = self._generate_password()
        self.bind_addr = bind_addr
        self.extra_args = extra_args or []
        self.process: Optional[subprocess.Popen] = None
        self.base_path = f"/s/{session_id}"

    def _generate_password(self) -> str:
        """生成随机密码"""
        return "".join(random.choices(string.ascii_letters + string.digits, k=16))

    def _find_code_server(self) -> str:
        """查找 code-server 可执行文件"""
        # 优先使用配置的路径
        if self.code_server_cmd and os.path.isfile(self.code_server_cmd):
            return self.code_server_cmd

        # 搜索 PATH
        for path in ["code-server", "code-server.cmd"]:
            found = shutil.which(path)
            if found:
                return found

        raise FileNotFoundError(
            "未找到 code-server，请确保已安装或通过 --code-server 指定路径"
        )

    def build_args(self) -> list:
        """构建 code-server 启动参数"""
        cmd = self._find_code_server()

        args = [
            cmd,
            "--bind-addr", f"{self.bind_addr}:{self.port}",
            "--auth", self.auth,
        ]

        # 设置密码（通过环境变量，仅在 password 认证时）
        env = os.environ.copy()
        if self.auth == "password" and self.password:
            env["PASSWORD"] = self.password

        # 添加额外参数
        args.extend(self.extra_args)

        return args, env

    def start(self) -> subprocess.Popen:
        """启动 code-server"""
        args, env = self.build_args()

        log.info("=" * 50)
        log.info("启动 Code-Server")
        log.info("Session ID: %s", self.session_id)
        log.info("Base Path:  %s", self.base_path)
        log.info("端口:       %s:%d", self.bind_addr, self.port)
        log.info("认证模式:   %s", self.auth)
        if self.auth == "password" and self.password:
            log.info("密码:       %s", self.password)
        log.info("本地访问:   http://%s:%d%s/", self.bind_addr, self.port, self.base_path)
        log.info("=" * 50)

        self.process = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        return self.process

    def wait(self):
        """等待进程结束"""
        if self.process:
            # 实时输出日志
            try:
                for line in self.process.stdout:
                    log.info("[code-server] %s", line.rstrip())
            except KeyboardInterrupt:
                log.info("收到中断信号，正在停止...")
                self.stop()

    def stop(self):
        """停止进程"""
        if self.process:
            log.info("停止 code-server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            log.info("code-server 已停止")


# ======================== Tunnel Agent 进程管理 ========================
class TunnelAgentLauncher:
    """管理 tunnel-agent 进程"""

    def __init__(
        self,
        session_id: str,
        tunnel_server: str = "ws://localhost:8080",
        local_url: str = "http://127.0.0.1:8443",
        retry_interval: float = 5,
    ):
        self.session_id = session_id
        self.tunnel_server = tunnel_server
        self.local_url = local_url
        self.retry_interval = retry_interval
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> subprocess.Popen:
        """启动 tunnel-agent"""
        # 找到 tunnel_agent.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        agent_script = os.path.join(script_dir, "tunnel_agent.py")

        if not os.path.isfile(agent_script):
            raise FileNotFoundError(f"未找到 tunnel_agent.py: {agent_script}")

        # 使用当前 Python 解释器
        python = sys.executable

        args = [
            python, agent_script,
            "--server", self.tunnel_server,
            "--sid", self.session_id,
            "--local", self.local_url,
            "--retry", str(self.retry_interval),
        ]

        log.info("=" * 50)
        log.info("启动 Tunnel Agent")
        log.info("隧道服务器: %s", self.tunnel_server)
        log.info("Session ID: %s", self.session_id)
        log.info("本地服务:   %s", self.local_url)
        log.info("=" * 50)

        self.process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        return self.process

    def wait(self):
        """等待进程结束"""
        if self.process:
            try:
                for line in self.process.stdout:
                    log.info("[tunnel-agent] %s", line.rstrip())
            except KeyboardInterrupt:
                log.info("收到中断信号，正在停止...")
                self.stop()

    def stop(self):
        """停止进程"""
        if self.process:
            log.info("停止 tunnel-agent...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            log.info("tunnel-agent 已停止")


# ======================== 主入口 ========================
def main():
    parser = argparse.ArgumentParser(description="启动 VSCode Code-Server")
    parser.add_argument("--sid", default=os.getenv("SESSION_ID", ""), help="Session ID")
    parser.add_argument("--port", type=int, default=8443, help="Code-Server 端口")
    parser.add_argument("--bind", default="127.0.0.1", help="绑定地址")
    parser.add_argument("--auth", default="password", choices=["password", "none"], help="认证模式 (password/none)")
    parser.add_argument("--password", help="访问密码（默认自动生成，仅在 --auth password 时有效）")
    parser.add_argument("--code-server", default=os.getenv("CODE_SERVER", "code-server"), help="code-server 路径")
    parser.add_argument("--tunnel", action="store_true", help="同时启动 tunnel-agent")
    parser.add_argument("--server", default=os.getenv("TUNNEL_SERVER", "ws://localhost:8080"), help="隧道服务器地址")
    parser.add_argument("--retry", type=float, default=5, help="重连间隔秒数")
    parser.add_argument("--extra", nargs="*", help="传递给 code-server 的额外参数")
    args = parser.parse_args()

    # 生成或使用指定的 Session ID
    sid = args.sid
    if not sid:
        sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        os.environ["SESSION_ID"] = sid

    # 创建启动器
    vscode = CodeServerLauncher(
        session_id=sid,
        port=args.port,
        code_server_cmd=args.code_server,
        password=args.password,
        bind_addr=args.bind,
        auth=args.auth,
        extra_args=args.extra,
    )

    tunnel = None
    if args.tunnel:
        tunnel = TunnelAgentLauncher(
            session_id=sid,
            tunnel_server=args.server,
            local_url=f"http://{args.bind}:{args.port}",
            retry_interval=args.retry,
        )

    # 启动进程
    vscode.start()
    if tunnel:
        tunnel.start()

    # 设置信号处理
    stop_event = threading.Event()

    def handle_signal(signum, frame):
        log.info("收到信号 %d，正在停止所有进程...", signum)
        stop_event.set()
        vscode.stop()
        if tunnel:
            tunnel.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # 启动日志读取线程
    vscode_reader = threading.Thread(
        target=stream_reader,
        args=(vscode.process, "code-server", stop_event),
        daemon=True
    )
    vscode_reader.start()

    if tunnel:
        tunnel_reader = threading.Thread(
            target=stream_reader,
            args=(tunnel.process, "tunnel-agent", stop_event),
            daemon=True
        )
        tunnel_reader.start()

    # 等待进程
    try:
        if tunnel:
            # 两个进程并行运行
            while not stop_event.is_set():
                # 检查进程状态
                if vscode.process and vscode.process.poll() is not None:
                    log.error("code-server 已退出")
                    stop_event.set()
                    tunnel.stop()
                    break
                if tunnel.process and tunnel.process.poll() is not None:
                    log.warning("tunnel-agent 已退出，code-server 继续运行")
                time.sleep(0.1)
        else:
            while not stop_event.is_set() and vscode.process.poll() is None:
                time.sleep(0.1)
    except KeyboardInterrupt:
        handle_signal(signal.SIGINT, None)


if __name__ == "__main__":
    main()
