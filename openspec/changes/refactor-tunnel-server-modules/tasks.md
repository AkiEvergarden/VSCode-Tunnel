## 1. Create Directory Structure

- [x] 1.1 Create `tunnel_server/` directory
- [x] 1.2 Create `tunnel_server/handlers/` subdirectory
- [x] 1.3 Create `tunnel_server/handlers/__init__.py`

## 2. Extract Constants and Utilities

- [x] 2.1 Create `tunnel_server/constants.py` — Move message type constants (`T_HTTP_REQ`, `T_WS_MSG`, etc.), path constants (`SESSION_PREFIX`, `AGENT_WS_PATH`), and timeout configs (`HEARTBEAT_INTERVAL`, `REQUEST_TIMEOUT`)
- [x] 2.2 Create `tunnel_server/utils.py` — Move `next_cid()`, `_encode_body()`, `_decode_body()` from tunnel_server.py

## 3. Extract Data Models

- [x] 3.1 Create `tunnel_server/models.py` — Move `AgentConn` dataclass

## 4. Extract Session Registry

- [x] 4.1 Create `tunnel_server/registry.py` — Move `SessionRegistry` class and instantiate module-level singleton `registry = SessionRegistry()`

## 5. Extract Handlers

- [x] 5.1 Create `tunnel_server/handlers/agent.py` — Move `handle_agent_ws()` function; import `registry` from `..registry`
- [x] 5.2 Create `tunnel_server/handlers/proxy.py` — Move `handle_proxy()`, `_proxy_http()`, `_proxy_websocket()` functions; import constants from `..constants`, utils from `..utils`
- [x] 5.3 Create `tunnel_server/handlers/api.py` — Move `handle_api_sessions()`, `handle_api_health()`; import `registry` from `..registry`
- [x] 5.4 Create `tunnel_server/handlers/index.py` — Move `handle_index()` function

## 6. Create Application Factory and Entry Point

- [x] 6.1 Create `tunnel_server/app.py` — Move `create_app()` function; update imports to reference local modules
- [x] 6.2 Create `tunnel_server/run.py` — Move `main()` function; import `create_app` from `.app`

## 7. Create Package Init

- [x] 7.1 Create `tunnel_server/__init__.py` — Re-export public API: `create_app`, `main`, `AgentConn`, `SessionRegistry`, `registry`, and key constants/utils

## 8. Verify and Cleanup

- [x] 8.1 Verify imports work: `python -c "from tunnel_server import create_app, main"`
- [x] 8.2 Run existing integration test (if available) to verify behavior unchanged — Verified by importing and creating app with all routes
- [x] 8.3 Remove original `tunnel_server.py` file
- [x] 8.4 Update any external references (Dockerfile, README) if they reference `tunnel_server.py` directly — No external references found
