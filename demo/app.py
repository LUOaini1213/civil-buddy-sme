from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile

from agent import run_plain
from packing_assistant import access_guard
from catalog import catalog_payload, get_expert, resolve_mentions
from config import DEMO_ROOT, OUT_ROOT, llm_model
from kbio import MAX_FILE_BYTES, create_file, delete_file, format_bytes, read_text, write_text
from llm import has_key
from rag import list_kb
from store import (
    disable_or_delete_expert,
    set_soft_limit,
    tree_payload,
    upsert_category,
    upsert_expert,
)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Turns the previous process left "running" on disk are stale now; say so before any list is served."""
    from chat_service import sweep_stale
    import uploads as _uploads

    access_guard.check_startup_bind()
    sweep_stale(OUT_ROOT)
    _uploads.migrate_legacy_uploads()
    yield


app = FastAPI(title="Civil Buddy Workbench", lifespan=_lifespan)
STATIC = DEMO_ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# CAD dependencies load only when this optional modeling workspace is used.
from cad_api import router as cad_router
app.include_router(cad_router)
from engineering_api import router as engineering_router
app.include_router(engineering_router)
from planning_api import router as planning_router
app.include_router(planning_router)
try:
    from planning_chat_api import router as planning_chat_router
except ImportError:
    from demo.planning_chat_api import router as planning_chat_router
app.include_router(planning_chat_router)
from routing_api import router as routing_router
app.include_router(routing_router)
from logistics_api import router as logistics_router
app.include_router(logistics_router)


# Pages and their static files load before the token so the page can ask for it; /api/health says auth is on.
_PUBLIC = {"/", "/sw.js", "/api/health", "/cad", "/logistics", "/engineering", "/engineering/schedule",
           "/engineering/planning", "/engineering/routes"}


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=40_000)
    locale: str = Field(default="zh-CN", pattern=r"^(zh-CN|en)$")
    history: list[dict] = Field(default_factory=list, max_length=80)
    expert_ids: list[str] = Field(default_factory=list, max_length=8)
    confirm_ok: StrictBool = False  # still sent for the Rust workbench that serves the same page; never approves here
    confirm_text: str = Field(default="", max_length=64)
    session_id: str = Field(default="", max_length=32)
    project_id: str = Field(default="", max_length=64)
    cad_project_id: str = Field(default="", pattern=r"^(?:[0-9a-f]{32})?$")
    planning_project_id: str = Field(default="", pattern=r"^(?:[0-9a-f]{32})?$")
    logistics_project_id: str = Field(default="", pattern=r"^(?:[0-9a-f]{32})?$")
    attachments: list[str] = Field(default_factory=list, max_length=12)
    workflow_budget: dict | None = None
    attachment_roles: dict[str, str] = Field(default_factory=dict)
    background: bool = False  # run with no reader attached; the page follows it as a running session


async def require_logistics_chat_access(request: Request, body: ChatIn):
    """Protect both new bindings and continuation of a logistics session."""
    from chat_service import session_uses_logistics
    from logistics_api import local_request
    from starlette.concurrency import run_in_threadpool
    try:
        restricted = bool(body.logistics_project_id) or await run_in_threadpool(session_uses_logistics, OUT_ROOT, body.session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if restricted:
        await local_request(request)


async def require_logistics_session_access(request: Request, sid: str):
    """Restoring a chat or its event stream must not bypass its project boundary."""
    from chat_service import session_uses_logistics
    from logistics_api import local_request
    from starlette.concurrency import run_in_threadpool
    try:
        restricted = await run_in_threadpool(session_uses_logistics, OUT_ROOT, sid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if restricted:
        await local_request(request)


async def can_read_logistics_sessions(request: Request) -> bool:
    """Ordinary LAN sessions remain visible; only logistics previews are filtered."""
    from logistics_api import local_request
    try:
        await local_request(request)
        return True
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise


class ExpertIn(BaseModel):
    id: str
    name: str
    category: str
    title: str = ""
    delivers: str = ""
    risk: str = "low"
    aliases: str = ""
    pipeline: str = ""


class CategoryIn(BaseModel):
    id: str
    name: str
    blurb: str = ""


class FileIn(BaseModel):
    path: str
    content: str = ""


class LimitIn(BaseModel):
    kb_soft_limit_kb: int


class LLMConfigIn(BaseModel):
    # Existing setting fields retain model_settings' validation and error text.
    # The new flag is strict at the HTTP boundary, including rejecting null.
    model_config = {"extra": "allow"}
    semantic_summary: StrictBool = False


def _version_static_links(html: str) -> str:
    """Stamp /static/*.js|css links with the file's mtime so a deploy never serves a stale app.js
    to a phone that cached the old one; the hand-written ?v= tags stay as a fallback elsewhere."""
    import re

    def stamp(m: "re.Match[str]") -> str:
        rel = m.group(1)
        target = STATIC / rel[len("/static/"):]
        if not target.is_file():
            return m.group(0)
        return f"{rel}?v={target.stat().st_mtime_ns // 1_000_000:x}"

    return re.sub(r'(/static/[^"\'?\s]+\.(?:js|css))(\?v=[^"\'\s]*)?', stamp, html)


@app.get("/sw.js")
def service_worker() -> FileResponse:
    """The service worker must be served from the root to control "/"; never cached stale."""
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/")
def index() -> HTMLResponse:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(_version_static_links(html), headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def static_revalidates(request: Request, call_next):
    """/static is served with ETag/Last-Modified; no-cache makes the browser ask every time
    (a 304 when unchanged) instead of trusting a copy from before the last deploy."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health(request: Request) -> dict:
    from context import policy
    from packing_assistant.office_job import job_root, job_root_granted, list_job_files

    authorised = access_guard.authorised(request.scope)
    return {
        "ok": True,
        "product": "civil-codex",
        "product_name": "Civil Buddy",
        "tagline": "土木版 Codex",
        "has_key": has_key(),
        "mode": "configured" if has_key() else "offline",
        "capabilities": {"chat": True, "drafts": True, "model_settings": True,
                         "attachments": True, "upload_url": True, "audit": True, "packing": False,
                         "session_backup": True, "cancel": True, "word_export": True,
                         "task_memory": True, "local_rag": True, "task_routing": True,
                         "expert_contracts": True, "tender_collaboration": True, "semantic_summary": True,
                         "asr": _asr_installed(), "live_progress": True,
                         "auth": bool(access_guard.configured_token()) and not authorised,
                         "file_ref": True, "event_log": True, "background_turns": True,
                         "cad": True},
        "deepseek": has_key(),
        "model": llm_model(),
        "context": policy(),
        "job": {
            "granted": job_root_granted(),
            "root": str(job_root()) if job_root_granted() and authorised else "",
            "n": len(list_job_files()),
        },
    }


@app.get("/api/llm-config")
def llm_settings() -> dict:
    from model_settings import get_settings
    return get_settings()


@app.get("/api/experts/{expert_id}/capability")
def expert_capability(expert_id: str) -> dict:
    from packing_assistant.expert_capabilities import get_capability
    value = get_capability(expert_id)
    if not value:
        raise HTTPException(404, "岗位能力契约不存在")
    return {"ok": True, **value}


@app.post("/api/task-route")
def task_route(body: ChatIn) -> dict:
    from task_router import route_task
    if any(not get_expert(eid) for eid in body.expert_ids):
        raise HTTPException(400, "请选择有效岗位")
    return {"ok": True, "route": route_task(body.message, body.expert_ids)}


@app.get("/api/workflows/{session_id}/{run_id}")
def workflow_detail(session_id: str, run_id: str) -> dict:
    from packing_assistant.runtime.tender_workflow import load_workflow
    from workflow_service import public_result
    try:
        value = load_workflow(OUT_ROOT, session_id, run_id)
        return {"ok": True, "workflow": public_result(value), "active": value["active"]}
    except (OSError, ValueError):
        raise HTTPException(404, "协作运行记录不存在") from None


@app.post("/api/llm-config")
def llm_settings_update(body: LLMConfigIn) -> dict:
    from model_settings import set_settings
    try:
        return set_settings(body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class UploadUrlIn(BaseModel):
    session_id: str = Field(default="", max_length=120)
    url: str = Field(default="", max_length=2000)


@app.post("/api/upload-url")
async def upload_url(body: UploadUrlIn) -> dict:
    """A tender named by its address: fetched here (public addresses only, 20 MB, documents only) and attached like
    an upload - the page gets the same {ok, files} back."""
    from starlette.concurrency import run_in_threadpool

    from uploads import UploadError, UploadTooLarge, fetch_upload

    try:
        return await run_in_threadpool(fetch_upload, body.session_id, body.url)
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, PermissionError) as exc:
        raise HTTPException(500, "无法保存附件，请检查工作台目录权限") from exc


@app.post("/api/upload")
async def upload(request: Request) -> dict:
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.formparsers import MultiPartException
    from uploads import MAX_BYTES, MAX_REQUEST_BYTES, UploadError, UploadTooLarge, save_uploads
    # Bound the body before multipart parsing, including chunked uploads without Content-Length.
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "一次上传不能超过 25 MB")
        data.extend(chunk)
    async def receive():
        return {"type": "http.request", "body": bytes(data), "more_body": False}
    bounded = Request(request.scope, receive)
    try:
        async with bounded.form(max_files=12, max_fields=1) as form:
            sid = str(form.get("session_id") or "")
            files = []
            for _, value in form.multi_items():
                if isinstance(value, UploadFile):
                    payload = await value.read(MAX_BYTES + 1)
                    if len(payload) > MAX_BYTES:
                        raise UploadTooLarge("单个附件不能超过 20 MB")
                    files.append((value.filename or "", payload))
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(save_uploads, sid, files)
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (MultiPartException, StarletteHTTPException) as exc:
        # Starlette's own limits (max_files / max_fields / malformed body) come
        # back as English text (re-raised as HTTPException inside request.form);
        # the UI shows the detail verbatim, so translate the known ones.
        detail = str(getattr(exc, "detail", None) or getattr(exc, "message", None) or exc)
        if "Too many files" in detail:
            raise HTTPException(400, "一次最多上传 12 个附件") from exc
        if "Too many fields" in detail:
            raise HTTPException(400, "上传表单字段过多") from exc
        if isinstance(exc, StarletteHTTPException) and exc.status_code != 400:
            raise
        raise HTTPException(400, "上传内容格式无效，请重新选择文件后再试") from exc
    except (OSError, PermissionError) as exc:
        raise HTTPException(500, "无法保存附件，请检查工作台目录权限") from exc


def _asr_installed() -> bool:
    import asr

    return asr.engine_installed()


@app.get("/api/asr/status")
def asr_status() -> dict:
    import asr

    return {"ok": True, **asr.status()}


@app.post("/api/asr/prepare")
def asr_prepare() -> dict:
    """Load (first time: download) the model in the background; the page polls status until ready."""
    import asr

    try:
        return {"ok": True, "state": asr.prepare()}
    except asr.AsrUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/asr")
async def asr_transcribe(request: Request) -> dict:
    """Speech to text for the composer. Returns text only: no chat, no task, no file."""
    import logging

    import asr
    from starlette.concurrency import run_in_threadpool

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > asr.MAX_AUDIO_BYTES:
            raise HTTPException(413, "录音不能超过 8 MB")
        data.extend(chunk)
    try:
        return {"ok": True, **(await run_in_threadpool(asr.transcribe, bytes(data), language=request.headers.get("x-civil-asr-language", "zh")))}
    except asr.AsrTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except asr.AsrInputError as exc:
        raise HTTPException(400, str(exc)) from exc
    except asr.AsrNotReady as exc:
        raise HTTPException(409, str(exc)) from exc
    except asr.AsrBusy as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc
    except asr.AsrUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # anything else is an engine fault: 503 lets the page fall back
        logging.getLogger(__name__).exception("asr failed")
        raise HTTPException(503, f"本机识别出错：{type(exc).__name__}") from exc


@app.get("/api/attachments")
def attachments(session_id: str) -> dict:
    from uploads import list_uploads
    try:
        return {"ok": True, "files": list_uploads(session_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/job")
def job_listing() -> dict:
    from packing_assistant.office_job import job_root, job_root_granted, list_job_files

    return {
        "ok": True,
        "granted": job_root_granted(),
        "root": str(job_root()) if job_root_granted() else "",
        "files": list_job_files(),
        "hint": (
            "说「写一份」会自动抄作业根里的 xlsx/docx/csv/txt，不必再上传。"
            if job_root_granted()
            else "设 CIVIL_JOB_ROOT 为工程文件夹后，本岗直接读该目录，不必上传。禁止 D:\\layout。"
        ),
    }


@app.get("/api/mcp/capabilities")
def mcp_capabilities() -> dict:
    from mcp_surface import initialize_capabilities

    return {"ok": True, "capabilities": initialize_capabilities()}


@app.get("/api/mcp/resources")
def mcp_resources(expert_id: str) -> dict:
    from mcp_surface import list_resources

    exp = get_expert(expert_id)
    if not exp:
        raise HTTPException(404, "unknown expert")
    return {"ok": True, "resources": list_resources(exp.id, exp.category)}


class McpResourceIn(BaseModel):
    uri: str
    expert_id: str = "bid-parse"


@app.post("/api/mcp/resources/read")
def mcp_resource_read(body: McpResourceIn) -> dict:
    from mcp_surface import read_resource

    exp = get_expert(body.expert_id)
    if not exp:
        raise HTTPException(404, "unknown expert")
    return {"ok": True, **read_resource(exp.id, exp.category, body.uri)}


@app.get("/api/mcp/prompts")
def mcp_prompts(expert_id: str = "") -> dict:
    from mcp_surface import list_prompts

    if expert_id and not get_expert(expert_id):
        raise HTTPException(404, "unknown expert")
    return {"ok": True, "prompts": list_prompts(expert_id=expert_id or None)}


class McpPromptIn(BaseModel):
    name: str
    expert_id: str = "bid-parse"
    arguments: dict = Field(default_factory=dict)


@app.post("/api/mcp/prompts/get")
def mcp_prompt_get(body: McpPromptIn) -> dict:
    from mcp_surface import get_prompt

    if not get_expert(body.expert_id):
        raise HTTPException(404, "unknown expert")
    return {"ok": True, **get_prompt(body.name, body.arguments, expert_id=body.expert_id)}


def _mcp_expert_ok(expert_id: str) -> bool:
    return (not expert_id) or bool(get_expert(expert_id)) or expert_id in {"pack-ship"}


@app.get("/api/mcp/tools")
def mcp_tools(expert_id: str = "") -> dict:
    from mcp_surface import list_tools

    if expert_id and not _mcp_expert_ok(expert_id):
        raise HTTPException(404, "unknown expert")
    return {"ok": True, "tools": list_tools(expert_id=expert_id or None)}


class McpToolIn(BaseModel):
    name: str
    expert_id: str = "pack-ship"
    arguments: dict = Field(default_factory=dict)


@app.post("/api/mcp/tools/call")
def mcp_tool_call(body: McpToolIn) -> dict:
    from mcp_surface import call_tool

    if body.expert_id and not _mcp_expert_ok(body.expert_id):
        raise HTTPException(404, "unknown expert")
    return {"ok": True, **call_tool(body.name, body.arguments, expert_id=body.expert_id or None)}


@app.get("/api/skills")
def skills() -> dict:
    from packing_assistant.runtime.expert_skills import catalog

    rows = catalog()
    return {"ok": True, "n": len(rows), "skills": rows, "host": "civil-buddy"}


@app.get("/api/config")
def get_config() -> dict:
    from packing_assistant.runtime.civil_config import load_config

    return {"ok": True, **load_config().to_dict()}


class PolicyIn(BaseModel):
    sandbox: str = ""
    approval: str = ""


@app.post("/api/config")
def set_config(body: PolicyIn) -> dict:
    import os

    from packing_assistant.runtime.civil_config import SANDBOX_MODES, APPROVAL_MODES, load_config

    if body.sandbox and body.sandbox not in SANDBOX_MODES:
        raise HTTPException(400, "bad sandbox")
    if body.approval and body.approval not in APPROVAL_MODES:
        raise HTTPException(400, "bad approval")
    if body.sandbox:
        os.environ["CIVIL_SANDBOX"] = body.sandbox
    if body.approval:
        os.environ["CIVIL_APPROVAL"] = body.approval
    cfg = load_config()
    if body.sandbox and cfg.sandbox not in SANDBOX_MODES:
        raise HTTPException(400, "bad sandbox")
    if body.approval and cfg.approval not in APPROVAL_MODES:
        raise HTTPException(400, "bad approval")
    return {"ok": True, **cfg.to_dict()}


# ===== ux(round19) 项目 / 会话索引（Rust workbench/src/projects.rs 的镜像）=====
# 契约单源 contract/projects.v1.json；行为差异由 scripts/test_projects_parity.py 对拍。
# 说明：/api/threads 是 /new 与 /bg 的**并行任务通道**（threads.py 里 session_id ==
# thread_id），**不是会话列表** —— 主 SSE 聊天路径从不创建 thread。左栏数据源是
# 下面的 /api/sessions。


@app.get("/api/projects")
def projects_list() -> dict:
    import projects as pj

    return pj.list_projects(OUT_ROOT, recorded_only=True)


class ProjectIn(BaseModel):
    name: str = ""


@app.post("/api/projects")
def projects_create(body: ProjectIn) -> dict:
    import projects as pj

    name = " ".join(str(body.name or "").split())
    if len(name) > PROJECT_NAME_MAX:
        raise HTTPException(400, f"项目名称最多 {PROJECT_NAME_MAX} 个字符")
    try:
        item, merged = pj.create_project(OUT_ROOT, name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "project": item, "merged": merged}


class ProjectPatchIn(BaseModel):
    name: str | None = None
    archived: bool | None = None


PROJECT_NAME_MAX = 60  # 手机侧栏一行能放下的上限；超过就换行溢出


@app.patch("/api/projects/{pid}")
def projects_patch(pid: str, body: ProjectPatchIn) -> dict:
    import projects as pj

    if body.name is not None and len(" ".join(body.name.split())) > PROJECT_NAME_MAX:
        raise HTTPException(400, f"项目名称最多 {PROJECT_NAME_MAX} 个字符")

    try:
        item = pj.patch_project(OUT_ROOT, pid, body.name, body.archived)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "project": item}


class MergeIn(BaseModel):
    into: str = ""


@app.get("/api/sessions/{sid}/live", dependencies=[Depends(require_logistics_session_access)])
def session_live(sid: str) -> dict:
    """Text and status a running (or just finished) turn has produced so far."""
    from chat_service import live_state

    try:
        return live_state(sid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{pid}/merge")
def projects_merge(pid: str, body: MergeIn) -> dict:
    import projects as pj

    try:
        item = pj.merge_project(OUT_ROOT, pid, body.into)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "project": item}


@app.get("/api/sessions")
def sessions_list(project_id: str = "", q: str = "", limit: int = 0, offset: int = 0,
                  include_logistics: bool = Depends(can_read_logistics_sessions)) -> dict:
    import projects as pj
    import turn_control

    from chat_service import public_session_listing, turn_status
    listing = (pj.list_sessions(OUT_ROOT, project_id, q, limit or pj.DEFAULT_LIMIT, offset, recorded_only=True)
               if include_logistics else public_session_listing(OUT_ROOT, project_id, q, limit or pj.DEFAULT_LIMIT, offset))

    for row in listing.get("sessions", []):
        # A turn detached from its browser keeps running; the list must say so. After a
        # restart a cut-off turn is "stale", never "running".
        current = turn_status(OUT_ROOT, row["session_id"])
        row["running"] = current["active"]
        row["turn_state"] = current["state"]
    return listing


@app.get("/api/sessions/{sid}", dependencies=[Depends(require_logistics_session_access)])
def session_get(sid: str) -> dict:
    from chat_service import session_detail

    try:
        return session_detail(OUT_ROOT, sid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/sessions/{sid}/cancel")
def session_cancel(sid: str) -> dict:
    from chat_service import valid_session
    from turn_control import cancel
    try:
        return cancel(valid_session(sid))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/context")
def task_context(session_id: str) -> dict:
    from chat_service import valid_session
    from session_context import detail
    try:
        return {"ok": True, **detail(OUT_ROOT, valid_session(session_id))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ContextSearchIn(BaseModel):
    session_id: str = Field(min_length=4, max_length=32)
    query: str = Field(min_length=1, max_length=2000)
    attachments: list[str] = Field(default_factory=list, max_length=12)


class ContextRebuildIn(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: str = Field(min_length=4, max_length=32)


@app.post("/api/context/rebuild")
def rebuild_task_context(body: ContextRebuildIn) -> dict:
    from chat_service import SessionBusy, SessionLease, valid_session
    from context_maintenance import rebuild
    lease = None
    try:
        sid = valid_session(body.session_id)
        lease = SessionLease(sid)
        return rebuild(OUT_ROOT, sid)
    except SessionBusy as exc:
        raise HTTPException(409, "当前任务正在运行，请停止或等待完成后重新整理记忆") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "任务或原始资料不存在，无法重建；请核对当前任务的记录和附件") from exc
    except PermissionError as exc:
        raise HTTPException(403, "当前模式或文件权限不允许重建；原始资料未改动") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "重建未完成，请检查存储空间或文件占用后重试；原始资料未改动") from exc
    finally:
        if lease:
            lease.release()


@app.post("/api/context/search")
def search_task_context(body: ContextSearchIn) -> dict:
    from chat_service import valid_session
    from session_context import citation
    import local_retrieval
    import projects
    import uploads
    try:
        sid = valid_session(body.session_id)
        documents = uploads.extracted_documents(sid, body.attachments)
        documents = uploads.extracted_documents(sid, [d["id"] for d in uploads.list_uploads(sid)])
        local_retrieval.sync_session(OUT_ROOT, sid, projects.read_full_history(OUT_ROOT, sid), documents)
        hits = local_retrieval.search(OUT_ROOT, sid, body.query, attachment_ids=body.attachments, limit=8)
        return {"ok": True, "citations": [citation(sid, hit) for hit in hits]}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/context/source")
def context_source(session_id: str, source_id: str, start: int = 0, end: int = 8000) -> dict:
    from chat_service import valid_session
    import local_retrieval
    try:
        sid = valid_session(session_id)
        if start < 0 or end <= start or end - start > 20_000:
            raise ValueError("来源范围须为 1–20000 个字符")
        value = local_retrieval.source(OUT_ROOT, sid, source_id)
        if value is None:
            raise HTTPException(404, "该来源已失效，请重新检索")
        return {"ok": True, **{k: v for k, v in value.items() if k != "text"},
                "text": value["text"][start:end], "start": start, "end": min(end, len(value["text"]))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/sessions/{sid}/export")
def session_export(sid: str):
    """The backup zip is written to a temp file and streamed from there; nothing bigger than
    one attachment is held in memory."""
    import tempfile

    from starlette.background import BackgroundTask

    from chat_service import SessionBusy, SessionLease, valid_session
    from session_bundle import export_session

    lease = None
    tmp = None
    try:
        valid_session(sid)
        lease = SessionLease(sid)
        fd, tmp = tempfile.mkstemp(prefix="civil-export-", suffix=".zip", dir=str(OUT_ROOT))
        os.close(fd)
        export_session(OUT_ROOT, sid, tmp)
        return FileResponse(tmp, media_type="application/zip", filename=f"civil-task-{sid}.zip",
                            background=BackgroundTask(_unlink_quietly, tmp))
    except SessionBusy as exc:
        raise HTTPException(409, "当前任务正在运行，请停止或等待完成后备份") from exc
    except (ValueError, OSError) as exc:
        _unlink_quietly(tmp)
        raise HTTPException(400, "备份失败：" + str(exc)) from exc
    except Exception:
        _unlink_quietly(tmp)
        raise
    finally:
        if lease:
            lease.release()


def _unlink_quietly(path) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


@app.post("/api/session-import")
async def session_import(request: Request) -> dict:
    """The upload is spooled to a temp file chunk by chunk (128 MB cap counted on the way in),
    then imported from disk."""
    import tempfile

    from starlette.concurrency import run_in_threadpool

    from session_bundle import MAX_BYTES, BundleError, import_session

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="civil-import-", suffix=".zip", dir=str(OUT_ROOT))
    received = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_BYTES:
                    raise HTTPException(413, "备份包不能超过 128 MB")
                fh.write(chunk)
        return await run_in_threadpool(import_session, OUT_ROOT, Path(tmp))
    except BundleError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, "当前模式或目录权限不允许导入任务") from exc
    except OSError as exc:
        raise HTTPException(500, "导入失败，请检查工作台目录空间和权限") from exc
    finally:
        _unlink_quietly(tmp)


class SessionPatchIn(BaseModel):
    project_id: str | None = None
    title: str | None = None


@app.patch("/api/sessions/{sid}")
def session_patch(sid: str, body: SessionPatchIn) -> dict:
    import projects as pj
    from chat_service import valid_session

    try:
        valid_session(sid)
        return {"ok": True, "session": pj.set_session_meta(OUT_ROOT, sid, body.project_id, body.title)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/harness/audit/{sid}")
def session_audit(sid: str) -> dict:
    from chat_service import audit_session
    try:
        return audit_session(OUT_ROOT, sid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/catalog")
def catalog() -> dict:
    return catalog_payload()


@app.get("/api/kb/{expert_id}")
def kb(expert_id: str) -> dict:
    exp = get_expert(expert_id)
    if not exp:
        raise HTTPException(404, "unknown expert")
    files = list_kb(exp.id, exp.category)
    total = sum(int(f.get("bytes") or 0) for f in files)
    return {
        "expert": expert_id,
        "files": files,
        "bytes": total,
        "label": format_bytes(total),
    }


@app.get("/api/studio/tree")
def studio_tree() -> dict:
    return tree_payload()


@app.get("/api/studio/file")
def studio_read(path: str) -> dict:
    got = read_text(path)
    if not got:
        raise HTTPException(404, "文件不存在")
    text, st = got
    return {"path": path, "content": text, **st}


@app.put("/api/studio/file")
def studio_write(body: FileIn) -> dict:
    try:
        st = write_text(body.path, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **st, "label": format_bytes(st["bytes"])}


@app.post("/api/studio/file")
def studio_create(body: FileIn) -> dict:
    try:
        st = create_file(body.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **st}


@app.delete("/api/studio/file")
def studio_delete(path: str) -> dict:
    try:
        delete_file(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/studio/experts")
def studio_expert(body: ExpertIn) -> dict:
    try:
        exp = upsert_expert(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not exp:
        raise HTTPException(500, "保存失败")
    return exp.to_dict()


@app.delete("/api/studio/experts/{expert_id}")
def studio_expert_del(expert_id: str, delete_kb: bool = True) -> dict:
    if not get_expert(expert_id):
        raise HTTPException(404, "unknown expert")
    disable_or_delete_expert(expert_id, delete_kb=delete_kb)
    return {"ok": True}


@app.post("/api/studio/categories")
def studio_category(body: CategoryIn) -> dict:
    try:
        return upsert_category(body.id, body.name, body.blurb)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/studio/limit")
def studio_limit(body: LimitIn) -> dict:
    return {"kb_soft_limit_kb": set_soft_limit(body.kb_soft_limit_kb), "max_file_bytes": MAX_FILE_BYTES}


@app.post("/api/chat", dependencies=[Depends(require_logistics_chat_access)])
def chat(body: ChatIn):
    from chat_service import SessionBusy, SessionLease, prepare_turn, start_background_turn, stream_turn, valid_session

    lease = None
    try:
        payload = body.model_dump()
        payload["session_id"] = valid_session(body.session_id or uuid4().hex[:12])
        lease = SessionLease(payload["session_id"])
        turn = prepare_turn(OUT_ROOT, payload)
    except SessionBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        if lease:
            lease.release()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        if lease:
            lease.release()
        raise

    if body.background:
        # 并行任务：同一条 /api/chat，只是没有人在读。202 + session_id，页面从会话列表跟进。
        started = start_background_turn(OUT_ROOT, turn, key_available=has_key(), plain_runner=run_plain, lease=lease)
        return Response(json.dumps(started, ensure_ascii=False), status_code=202, media_type="application/json")

    def events():
        try:
            for event in stream_turn(OUT_ROOT, turn, key_available=has_key(), plain_runner=run_plain, lease=lease):
                yield _sse(event)
        finally:
            lease.disconnect()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                             background=BackgroundTask(lease.disconnect))


def _sse(ev: dict) -> str:
    if ev["event"] == "heartbeat":
        return ": ping\n\n"  # keeps the connection (and the disconnect probe) alive without a JSON event to parse
    head = f"id: {ev['seq']}\n" if ev.get("seq") else ""
    return f"{head}event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"


@app.get("/api/sessions/{sid}/events", dependencies=[Depends(require_logistics_session_access)])
def session_events(sid: str, request: Request, after: int = 0) -> StreamingResponse:
    """Resume a turn's event stream from seq `after` (or the Last-Event-ID header): replays
    what the browser missed, then follows the turn live until done. Same frames, same ids as
    /api/chat, so the page runs one handler for both."""
    from chat_service import has_event_log, replay_events, valid_session

    try:
        sid = valid_session(sid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    header = request.headers.get("last-event-id") or ""
    if header.strip().isdigit():
        after = max(after, int(header.strip()))
    if not has_event_log(OUT_ROOT, sid):
        raise HTTPException(404, "这个会话还没有事件记录")

    def frames():
        for ev in replay_events(OUT_ROOT, sid, after):
            yield ": ping\n\n" if ev["event"] == "ping" else _sse(ev)

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/deliverables.zip")
def deliverables_zip(session_id: str, run_id: str = "") -> Response:
    """One download for a whole run's files (md + docx + xlsx) — three save
    dialogs on a phone is how files get lost. run_id empty = every run."""
    import io
    import re
    import zipfile
    from chat_service import read_runs, valid_session

    try:
        sid = valid_session(session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rid = str(run_id or "")
    if rid and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", rid):
        raise HTTPException(400, "run_id 无效")
    runs = [r for r in read_runs(OUT_ROOT, sid) if not rid or r.get("run_id") == rid]
    buf = io.BytesIO()
    count = 0
    from packing_assistant.sandbox import assert_open
    from uploads import safe_filename
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as bundle:
        for r in runs:
            # Run records are data (a backup import writes them too), so nothing in one is
            # trusted as a path: the folder and the member names are rebuilt, never copied, and a
            # file has to pass the same checks GET /api/file applies to it.
            folder = re.sub(r"[^A-Za-z0-9_-]", "", str(r.get("run_id") or ""))[:8]
            used: set[str] = set()
            for f in r.get("deliverables", []):
                src = Path(str(f.get("path") or "")).resolve()
                try:
                    src.relative_to(OUT_ROOT.resolve())
                    assert_open(src)
                except (ValueError, PermissionError):
                    continue
                if not src.is_file():
                    continue
                shown = safe_filename(str(f.get("name") or ""))
                name = shown if f.get("name") and Path(shown).suffix.lower() == src.suffix.lower() else src.name
                stem, suffix, serial = Path(name).stem, Path(name).suffix, 1
                if name in used:  # same display name twice in a run: tell them apart by their source
                    name = safe_filename(f"{stem}-{src.name.split('-', 1)[0]}{suffix}")
                while name in used:
                    serial += 1
                    name = f"{stem}-{serial}{suffix}"
                used.add(name)
                arc = f"{folder}/{name}" if not rid and folder else name
                bundle.write(src, arc)
                count += 1
    if not count:
        raise HTTPException(404, "这轮没有可下载的文书")
    label = f"civil-docs-{sid}" + (f"-{rid[:8]}" if rid else "")
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{label}.zip"',
    })


@app.get("/api/file")
def file(path: str = "", name: str = "", session: str = "", run: str = "", file: str = "",
         upload: str = "") -> FileResponse:
    """One deliverable or attachment. Preferred forms: ?session=&run=&file=<stored basename>
    (a deliverable) or ?session=&upload=<attachment id> (an uploaded original), plus
    &name=<shown name> — no server path in the link, and the link survives a backup imported on
    another machine. ?path=<absolute> is kept for the Rust workbench and old cards."""
    import re

    if upload:
        import uploads as _uploads

        try:
            target, stored_name = _uploads.upload_file(session, upload)
        except _uploads.UploadError as exc:
            raise HTTPException(404 if "不存在" in str(exc) else 400, str(exc)) from exc
        name = name or stored_name
        trusted_name = stored_name  # went through safe_filename at save time; the stored file is <id>.bin
    elif session or run or file:
        from chat_service import valid_session
        try:
            sid = valid_session(session)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run or ""):
            raise HTTPException(400, "run 无效")
        if not file or file != Path(file).name or file in {".", ".."}:
            raise HTTPException(400, "file 无效")
        target = (OUT_ROOT / sid / "deliverables" / run / file).resolve()
    elif path:
        target = Path(path).resolve()
    else:
        raise HTTPException(400, "缺少 path 或 session/run/file")
    try:
        target.relative_to(OUT_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(403, "not a deliverable") from exc
    if not target.is_file():
        raise HTTPException(404, "missing")
    from packing_assistant.sandbox import assert_open
    try:
        assert_open(target)
    except PermissionError as exc:
        raise HTTPException(403, "not a deliverable") from exc
    # Name the download server-side: iOS Safari / PWA ignore <a download> and
    # would otherwise save "file" or open .md inline. Starlette emits
    # filename*=UTF-8'' for non-ASCII names. The card's display name may be
    # used only when it is a plain basename with the same extension.
    from uploads import safe_filename
    shown = safe_filename(name) if name else ""
    download_name = shown if shown and Path(shown).suffix.lower() == target.suffix.lower() else target.name
    if upload:
        import mimetypes

        download_name = safe_filename(shown or trusted_name) or trusted_name
        media_type = mimetypes.guess_type(download_name)[0] or "application/octet-stream"
        return FileResponse(target, filename=download_name, media_type=media_type, content_disposition_type="attachment")
    return FileResponse(target, filename=download_name, content_disposition_type="attachment")


app.add_middleware(access_guard.AccessGuard, public=lambda path: path in _PUBLIC or path.startswith("/static/"))
