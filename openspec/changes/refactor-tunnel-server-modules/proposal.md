## Why

The `tunnel_server.py` is a 593-line monolithic file that mixes constant definitions, data models, protocol logic, HTTP/WS handlers, and application setup. This structure makes the code difficult to understand, test, and extend. As the tunnel server grows (e.g., adding metrics, rate limiting, or connection pooling), a modular structure is needed.

## What Changes

- Split `tunnel_server.py` into a `tunnel_server/` directory with the following modules:
  - `constants.py` — Message type constants, path constants, timeout configurations
  - `utils.py` — Body encode/decode utilities, connection ID generator
  - `models.py` — `AgentConn` dataclass
  - `registry.py` — `SessionRegistry` class
  - `handlers/agent.py` — Agent WebSocket connection handler (`handle_agent_ws`)
  - `handlers/proxy.py` — User request proxy handlers (`handle_proxy`, `_proxy_http`, `_proxy_websocket`)
  - `handlers/api.py` — Admin API handlers (`handle_api_sessions`, `handle_api_health`)
  - `handlers/index.py` — Homepage handler (`handle_index`)
  - `app.py` — Application factory (`create_app`)
  - `run.py` — Entry point (`main`)
  - `__init__.py` — Module exports
- Preserve all existing functionality, message protocol, and API contracts
- No changes to external APIs or dependencies

## Capabilities

### New Capabilities

_(This is a pure refactoring — no new capabilities are introduced.)_

### Modified Capabilities

_(No existing capability requirements are changed — only internal implementation structure.)_

## Impact

- **Code location**: `tunnel_server.py` → `tunnel_server/` directory
- **Public API**: No change — all existing endpoints (`/__tunnel__`, `/s/{sid}`, `/__api__/sessions`, `/__api__/health`, `/`) remain identical
- **Dependencies**: No new dependencies introduced
- **Data model**: `AgentConn` and `SessionRegistry` remain unchanged in behavior
- **Message protocol**: Server↔Agent protocol unchanged
