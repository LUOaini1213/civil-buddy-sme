"""Authenticated, deterministic tools for the Rust host's private sidecar.

Legacy handlers are reused explicitly, without mounting the legacy application
or exposing its model/configuration/file-editor APIs. This dedicated process
disables model configuration before imports. The launcher owns the loopback
listener and generates the token.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import importlib
import os
from pathlib import Path
import re
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

DEMO_ROOT = Path(__file__).resolve().parent
os.environ.update(PYTHON_DOTENV_DISABLED="1", CIVIL_AGENT_MODE="steps", PACKING_LLM_AGENT="0")
for _key in list(os.environ):
    if _key.upper().endswith("_API_KEY"):
        os.environ.pop(_key, None)
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))


def _legacy_module(name):
    """Share caches, roots and locks between script imports and package imports."""
    expected = DEMO_ROOT / (name + ".py")
    candidates = [sys.modules[key] for key in (name, "demo." + name) if key in sys.modules]
    if any(Path(getattr(module, "__file__", "")).resolve() != expected for module in candidates):
        raise RuntimeError("Domain service module collision: " + name)
    if len({id(module) for module in candidates}) > 1:
        raise RuntimeError("Domain service requires one module instance: " + name)
    module = candidates[0] if candidates else importlib.import_module(name)
    sys.modules[name] = sys.modules["demo." + name] = module
    return module


config = _legacy_module("config")
os.environ["CIVIL_JOB_ROOT"] = str(Path(os.environ.get("CIVIL_DOMAIN_WORKSPACE", str(config.OUT_ROOT))).resolve())
from packing_assistant import llm as _llm
_llm.set_runtime_llm({"api_key": "", "base_url": "", "model": ""})

for _name in ("cad_api", "engineering_api", "planning_api", "planning_chat_api", "routing_api", "logistics_api"):
    _legacy_module(_name)
for _name in ("projects", "turn_control", "uploads", "local_retrieval", "session_context"):
    _legacy_module(_name)
# The legacy app migrates old uploads at startup and lazily per session. Its
# repository-wide default must never be adopted by a private named instance.
_legacy_module("uploads").LEGACY_UPLOAD_ROOT = config.DATA_ROOT / "uploads"
legacy = _legacy_module("app")
chat_service = _legacy_module("chat_service")
for _name, _module in list(sys.modules.items()):
    if "." not in _name and getattr(_module, "__file__", None):
        if Path(_module.__file__).resolve().parent == DEMO_ROOT:
            _legacy_module(_name)


def _no_model(*_args, **_kwargs):
    raise RuntimeError("The domain sidecar does not call language models")


# Reuse leases, cancellation, events and tools; model chat and summaries stay off.
legacy.has_key = lambda: False
legacy.run_plain = _no_model


def _token():
    value = os.environ.get("CIVIL_DOMAIN_TOKEN", "")
    # Launcher: secrets.token_urlsafe(48). Never fall back to trusting localhost.
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,256}", value) or len(set(value)) < 12:
        return ""
    return value


class DomainTokenGuard:
    """Authenticate every path before routing, including health and 404s."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        expected = _token()
        values = [value.decode("latin-1") for name, value in scope.get("headers", [])
                  if name.lower() == b"authorization"]
        parts = values[0].split(" ", 1) if len(values) == 1 else []
        presented = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        accepted = bool(expected and presented and hmac.compare_digest(presented.encode(), expected.encode()))
        if not accepted:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            response = JSONResponse({"detail": "Domain service is not configured" if not expected else "Domain token required"},
                                    status_code=503 if not expected else 401,
                                    headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"})
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(_app):
    if not _token():
        raise RuntimeError("CIVIL_DOMAIN_TOKEN must be a generated high-entropy URL-safe token")
    async with legacy._lifespan(legacy.app):
        yield


app = FastAPI(title="Civil Buddy domain workers", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(DomainTokenGuard)

# Exact allowlist: new legacy routes are never exposed implicitly.
LEGACY_PATHS = frozenset({
    "/api/chat", "/api/catalog", "/api/skills", "/api/kb/{expert_id}", "/api/experts/{expert_id}/capability",
    "/api/task-route", "/api/workflows/{session_id}/{run_id}", "/api/job", "/api/upload", "/api/attachments",
    "/api/projects", "/api/projects/{pid}", "/api/projects/{pid}/merge",
    "/api/sessions", "/api/sessions/{sid}", "/api/sessions/{sid}/cancel", "/api/sessions/{sid}/live",
    "/api/sessions/{sid}/events", "/api/sessions/{sid}/export", "/api/session-import",
    "/api/context", "/api/context/rebuild", "/api/context/search", "/api/context/source",
    "/api/harness/audit/{sid}", "/api/deliverables.zip", "/api/file",
})
for _route in legacy.app.routes:
    if isinstance(_route, APIRoute) and _route.path in LEGACY_PATHS:
        app.router.routes.append(_route)
for _name in ("cad_api", "engineering_api", "planning_api", "planning_chat_api", "routing_api", "logistics_api"):
    app.include_router(_legacy_module(_name).router)
from .packing_api import router as packing_router
app.include_router(packing_router)
try:
    from .asr_service import router as asr_router
except ImportError:
    asr_router = None
if asr_router is not None:
    app.include_router(asr_router)


@app.get("/health")
@app.get("/api/health")
def health():
    return {"ok": True, "service": "deterministic-domains", "mode": "steps", "has_key": False,
            "chat_runtime": {"deterministic_legacy_tools": True, "model_loop": False},
            "asr_routes": asr_router is not None, "packing_routes": True, "logistics_routes": True}
