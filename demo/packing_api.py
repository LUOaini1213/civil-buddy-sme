"""Existing packing tools exposed under a bounded, model-free domain prefix.

The legacy gateway remains a standalone application. Only the routes needed by
its packing workbench are reused here; generic agents, MCP and arbitrary local
path ingestion are not registered on the unified host.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.routing import APIRoute

from gateway import app as gateway

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()


async def deterministic_request(request: Request):
    if request.method != "POST" or "application/json" not in request.headers.get("content-type", ""):
        return
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "请求必须是有效 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求必须是 JSON 对象")
    for name in ("mode", "agent_mode"):
        if body.get(name) not in (None, "", "steps"):
            raise HTTPException(400, "装箱工作台使用固定计算流程；模型规划由主工作台负责")
    options = body.get("packing_options") or {}
    if body.get("ns_llm_enrich") or isinstance(options, dict) and options.get("ns_llm_enrich"):
        raise HTTPException(400, "此装箱服务不调用模型")
    if body.get("path"):
        raise HTTPException(400, "请上传材料表或提交已解析的材料行")


@router.get("/packing", response_class=HTMLResponse)
def workbench():
    html = (ROOT / "frontend/workbench.html").read_text(encoding="utf-8")
    html = html.replace('/static/vendor/', '/packing/static/vendor/')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/packing/static/vendor/{name}")
def vendor(name: str):
    if name not in {"vue.min.js", "marked.min.js", "cb-fix.js", "cb-doc.js"}:
        raise HTTPException(404, "资源不存在")
    return FileResponse(ROOT / "frontend/vendor" / name)


@router.get("/packing/api/health")
def health():
    value = gateway.api_health()
    value["unified_packing"] = {"available": True, "agent_mode": "steps", "model_calls": False,
                                "streaming": "sse", "websocket": False, "page": "/packing"}
    return value


@router.get("/packing/api/engine-ab")
def benchmark_status():
    # The standalone endpoint launches a benchmark subprocess and writes into
    # the source checkout. It is not part of an interactive packing operation.
    return {"ok": False, "available": False, "reason": "此工作台仅运行当前材料计算，不启动仓库基准测试"}


@router.post("/packing/api/table/parse")
async def table_upload(request: Request, file: UploadFile = File(...), session_id: str = Form(""),
                       store_session: str = Form("0"), path: str = Form("")):
    if path:
        raise HTTPException(400, "请上传材料表，不接受本机路径")
    raw = await file.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "材料表不得超过20MiB")
    await file.seek(0)
    return await gateway.api_table_parse(request=request, file=file, session_id=session_id, store_session=store_session, path="")


@router.get("/packing/api/artifact")
def artifact(path: str = ""):
    root = Path(os.environ.get("PACKING_OUTPUT_DIR", ROOT / "output")).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(403, "不是当前装箱工作台的产物")
    if target.suffix.lower() not in {".md", ".markdown", ".json", ".jsonl", ".txt"}:
        raise HTTPException(403, "不支持预览此格式")
    if not target.is_file():
        raise HTTPException(404, "产物不存在")
    if target.stat().st_size > 8 * 1024 * 1024:
        raise HTTPException(413, "文本预览超过8MiB，请使用导出")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "不是可预览的文本")
    return {"ok": True, "name": target.name, "expert": "", "text": text}


# Reuse the actual parser, solvers, confirmation gate, exports and saved runs.
# Deliberately omit generic chat/agent/tool dispatch and filesystem-path readers.
PACKING_ROUTES = {
    "/api/experts", "/api/profiles", "/api/whatif/scenarios", "/api/whatif", "/api/whatif/apply",
    "/api/team-a", "/api/revise-nl", "/api/confirm", "/api/demo", "/api/demo-presets",
    "/api/pipeline", "/api/pipeline/stream", "/api/table/parse/json",
    "/api/export/shipment", "/api/export/file", "/api/business-presets", "/api/nonstandard/inspect",
    "/api/runs", "/api/runs/compare", "/api/runs/{run_id}", "/api/runs/{run_id}/cancel",
    "/api/runs/{run_id}/replay", "/api/runs/{run_id}/events", "/api/audit",
    "/api/resume/{session_id}", "/api/session/{session_id}",
    "/api/checkpoints", "/api/checkpoints/{thread_id}", "/api/checkpoints/{thread_id}/resume",
    "/api/architecture",
}
legacy = APIRouter()
legacy.routes.extend(sorted((route for route in gateway.app.routes
                             if isinstance(route, APIRoute) and route.path in PACKING_ROUTES),
                            key=lambda route: ("{" in route.path, route.path)))
router.include_router(legacy, prefix="/packing", dependencies=[Depends(deterministic_request)])
