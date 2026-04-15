## Context

The current `tunnel_server.py` (593 lines) is a single-file implementation handling:
- Protocol constants and message types
- Data models (`AgentConn`, `SessionRegistry`)
- Agent WebSocket lifecycle management
- HTTP and WebSocket proxy logic
- Admin API endpoints
- Application factory and entry point

This monolithic structure makes it difficult to:
- Understand the code boundaries between different responsibilities
- Write unit tests for individual components
- Extend the server with new features (metrics, rate limiting, etc.)
- Onboard new contributors

## Goals / Non-Goals

**Goals:**
- Split `tunnel_server.py` into a `tunnel_server/` package with clear module boundaries
- Preserve all existing functionality, API endpoints, and message protocol
- Maintain the same runtime behavior (no behavior changes)
- Improve code navigability and enable future extensibility

**Non-Goals:**
- Adding new capabilities or changing existing behavior
- Modifying the Server↔Agent message protocol
- Adding new external dependencies
- Changing configuration management approach
- Adding tests (out of scope for this refactoring)

## Decisions

### 1. Directory structure: `tunnel_server/` package

**Decision**: Create a `tunnel_server/` directory as a Python package, with submodules organized by responsibility.

**Rationale**: Follows Python's natural packaging conventions. A flat package structure (vs. deeper nesting) keeps the refactoring simple while achieving clear separation.

**Alternatives considered**:
- Keep a single file but use `# === region ===` comments + IDE folding: Does not actually separate code into distinct units
- Multiple packages by layer (`protocol/`, `handlers/`): Over-engineered for a single-service refactoring
- Rename the module entirely (e.g., `server/`): Would break existing imports

**Resulting structure**:
```
tunnel_server/
├── __init__.py          # Exports create_app, main, registry, AgentConn
├── constants.py         # MESSAGE_TYPES, PATHS, CONFIG
├── utils.py             # encode_body, decode_body, next_cid
├── models.py            # AgentConn dataclass
├── registry.py          # SessionRegistry class
├── handlers/
│   ├── __init__.py
│   ├── agent.py         # handle_agent_ws
│   ├── proxy.py         # handle_proxy, _proxy_http, _proxy_websocket
│   ├── api.py           # handle_api_sessions, handle_api_health
│   └── index.py         # handle_index
├── app.py               # create_app
└── run.py               # main
```

### 2. Global state: Keep `registry` as a module-level singleton

**Decision**: `SessionRegistry` remains instantiated as a module-level singleton (`registry = SessionRegistry()`) in `registry.py`, imported directly by handlers.

**Rationale**: The current design uses `registry` as a shared global. Introducing dependency injection (passing registry via `request.app['registry']`) would require changing every handler signature and adds complexity without immediate benefit.

**Alternatives considered**:
- Dependency injection via `request.app['registry']`: Cleaner for testing but adds significant churn
- Class-based app context: More sophisticated, better for larger apps

### 3. Handler imports: Use relative imports within the package

**Decision**: Handlers import `registry` directly from `tunnel_server.registry` (or `..registry` for intra-package imports).

**Rationale**: Simple and explicit. Python's import system handles the package structure naturally.

### 4. Preserving `_proxy_http` and `_proxy_websocket` as module-level functions

**Decision**: Keep `_proxy_http` and `_proxy_websocket` as private module-level functions within `handlers/proxy.py`, not class methods.

**Rationale**: These functions operate on shared `AgentConn` state accessed via `registry`. Converting to class methods would require wrapping them in a class that holds no additional state — unnecessary indirection.

### 5. Body codec placement

**Decision**: Place `encode_body`/`decode_body` in `utils.py`. They are used by both server and agent (though currently only server uses them, the symmetry is intentional).

**Rationale**: Encoding/decoding is a standalone utility with no dependencies on server state.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| **Breaking existing imports** — If other code imports from `tunnel_server.py` directly | Create `tunnel_server/__init__.py` that re-exports the public API (`create_app`, `main`, `AgentConn`, `SessionRegistry`, `registry`) |
| **Circular imports** — Handlers importing registry, which may be needed during app creation | Ensure `registry.py` has no imports from `handlers/` — it only defines `SessionRegistry` |
| **Losing co-location** — Related code now in separate files may be harder to trace | Keep functions in logical order matching original file, add module docstrings |

## Migration Plan

1. **Create new `tunnel_server/` directory** with all new modules
2. **Verify imports work** — Ensure `python -c "from tunnel_server import create_app, main"` succeeds
3. **Run existing integration tests** (if any) to verify behavior unchanged
4. **Remove original `tunnel_server.py`**
5. **Update any code referencing `tunnel_server` module** (e.g., Dockerfile, documentation)

**Rollback**: Revert to single `tunnel_server.py` if issues arise.

## Open Questions

- **Q1**: Should `next_cid()` move into a class (e.g., `ConnectionIdGenerator`) for better testability, or remain a module-level function?
  - **Current**: Module-level function, acceptable for single-process server
- **Q2**: Should we add `__all__` exports to each module to explicitly define public API?
  - **Decision**: No — use implicit public API (no leading underscore) for simplicity
