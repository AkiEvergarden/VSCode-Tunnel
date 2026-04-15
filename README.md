# VSCode Tunnel

通过 WebSocket 隧道安全访问运行在容器内的 [code-server](https://github.com/coder/code-server)。

```
用户浏览器 ──HTTP/WS──► tunnel_server ──WS──► tunnel_agent ──HTTP/WS──► code-server
                    (公网服务器)         (容器内)              (容器内)
```

## 架构

| 组件 | 文件 | 运行环境 | 说明 |
|------|------|---------|------|
| 隧道服务器 | `tunnel_server/` | 公网服务器 | 接收用户请求，转发给容器 |
| 隧道客户端 | `tunnel_agent.py` | 容器内 | 连接服务器，转发请求到本地 code-server |
| 启动器 | `start_vscode.py` | 容器内 | 启动 code-server，可选启动 agent |

## 快速开始

### 1. 部署隧道服务器（公网机器）

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务器
python -m tunnel_server.run --host 0.0.0.0 --port 8080
```

### 2. 构建容器镜像

```bash
# 本地构建（需要预先下载 code-server deb 包）
docker build -f Dockerfile.local -t vscode-tunnel .

# 使用预下载包构建
docker build -f Dockerfile -t vscode-tunnel .
```

### 3. 启动容器

```bash
docker run -d \
  --name vscode \
  -p 8080:8080 \
  vscode-tunnel \
  python start_vscode.py --tunnel --server ws://你的服务器IP:8080
```

启动后访问 `http://你的服务器IP:8080/` 获取 Session ID 和访问地址。

## 目录结构

```
.
├── tunnel_server/          # 隧道服务器（部署在公网）
│   ├── __init__.py
│   ├── constants.py        # 消息类型、路径、超时配置
│   ├── utils.py             # 编解码、ID 生成工具
│   ├── models.py            # AgentConn 数据类
│   ├── registry.py          # Session 注册表
│   ├── handlers/            # 请求处理器
│   │   ├── agent.py         # Agent WebSocket 连接处理
│   │   ├── proxy.py         # HTTP/WS 代理
│   │   ├── api.py           # 管理 API
│   │   └── index.py         # 首页
│   ├── app.py               # 应用工厂
│   └── run.py               # 入口
├── tunnel_agent.py          # 隧道客户端（在容器内运行）
├── start_vscode.py          # code-server 启动器
├── requirements.txt         # Python 依赖
├── Dockerfile               # 容器镜像（需要预下载 deb）
└── Dockerfile.local         # 容器镜像（自动下载 deb）
```

## 启动器用法

```bash
# 本地访问（不启动隧道）
python start_vscode.py

# 指定 Session ID
python start_vscode.py --sid mysession

# 同时启动隧道客户端
python start_vscode.py --tunnel --server ws://服务器地址:8080

# 指定端口和绑定地址
python start_vscode.py --tunnel --port 8443 --bind 0.0.0.0

# 禁用密码认证
python start_vscode.py --auth none
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SESSION_ID` | 自动生成 | Session ID |
| `CODE_SERVER` | `code-server` | code-server 路径 |
| `TUNNEL_SERVER` | `ws://localhost:8080` | 隧道服务器地址 |
| `LOCAL_URL` | `http://127.0.0.1:8443` | 本地 code-server 地址 |

## API

### 管理接口

- `GET /__api__/sessions` — 列出所有在线 Session
- `GET /__api__/health` — 健康检查

### 隧道接口

- `WS /__tunnel__` — Agent 连接端点

## 依赖

- Python >= 3.10
- aiohttp >= 3.8.0
- brotli >= 1.0.9 (用于解压 code-server 响应)

## 协议

服务器与 Agent 之间通过 JSON over WebSocket 通信，消息类型：

| 方向 | 类型 | 说明 |
|------|------|------|
| S→A | `hr` | HTTP 请求 |
| S→A | `wm` | WebSocket 消息 |
| S→A | `wc` | WebSocket 关闭 |
| S→A | `pi` | 心跳 Ping |
| A→S | `rg` | 注册 |
| A→S | `hs` | HTTP 响应头 |
| A→S | `hb` | HTTP Body 分片 |
| A→S | `he` | HTTP 响应结束 |
| A→S | `po` | 心跳 Pong |
| A→S | `er` | 错误 |
