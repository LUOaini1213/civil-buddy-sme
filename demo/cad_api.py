"""CAD previews plus explicitly saved, versioned projects; deterministic geometry.

Unsaved previews expire after 30 minutes. Saved recipes retain the original DXF
in the workspace output directory. All portable exports require confirmation.
"""
from __future__ import annotations

from collections import OrderedDict
import asyncio
import base64
from copy import deepcopy
import importlib.util
import io
import json
import math
from pathlib import Path
from threading import BoundedSemaphore, Event, RLock
import re
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
import zipfile

from packing_assistant.cad3d.imports import MAX_SCAN_BYTES

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException, MultiPartParser

ROOT = Path(__file__).resolve().parents[1]
from packing_assistant.runtime.civil_config import CONFIRM as CONFIRMATION, CONFIRM_EN, is_confirmation  # noqa: E402
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024
TTL_SECONDS = 30 * 60
DEPENDENCIES = {"ezdxf": "ezdxf", "shapely": "shapely", "trimesh": "trimesh", "mapbox_earcut": "mapbox-earcut"}


class MemoryStore:
    """Immutable snapshots, random identifiers, TTL and a total serialized budget."""

    def __init__(self, *, max_items: int = 16, max_bytes: int = 64 * 1024 * 1024, ttl: float = TTL_SECONDS):
        self.max_items, self.max_bytes, self.ttl = max_items, max_bytes, ttl
        self._items: OrderedDict[str, tuple[float, int, dict]] = OrderedDict()
        self._lock = RLock()

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [key for key, (created, _, _) in self._items.items() if now - created >= self.ttl]:
            del self._items[key]

    def put(self, value: dict) -> str:
        from packing_assistant.runtime.cancel import check
        check()
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if size > self.max_bytes:
            raise ValueError("模型或图纸超过本机预览缓存上限，请减少图层或实体。")
        snapshot = deepcopy(value)
        check()
        with self._lock:
            check()
            self._expire()
            while self._items and (len(self._items) >= self.max_items or sum(v[1] for v in self._items.values()) + size > self.max_bytes):
                self._items.popitem(last=False)
            key = uuid4().hex
            self._items[key] = (time.monotonic(), size, snapshot)
            return key

    def get(self, key: str) -> dict:
        with self._lock:
            self._expire()
            if key not in self._items:
                raise HTTPException(410, "图纸或模型缓存已过期、服务已重启或缓存已释放。请从最近项目重新打开；未保存的图纸需重新上传并生成。")
            return deepcopy(self._items[key][2])


DOCUMENTS = MemoryStore(max_items=8, max_bytes=192 * 1024 * 1024)
MODELS = MemoryStore(max_items=12, max_bytes=64 * 1024 * 1024)
IMPORTS = MemoryStore(max_items=2, max_bytes=192 * 1024 * 1024)
COMPUTE = BoundedSemaphore(2)
OPERATIONS: dict[str, Event] = {}
OPERATIONS_LOCK = RLock()
CANCELLED_IMPORTS: OrderedDict[str, float] = OrderedDict()


async def local_request(request: Request) -> None:
    # Do not accept cross-site form submissions to this loopback tool. Requests
    # without Origin (CLI/tests) remain valid; the server binds loopback by default.
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise HTTPException(403, "请求来源无效。") from exc
        if parsed.scheme not in {"http", "https"} or parsed.netloc != request.url.netloc:
            raise HTTPException(403, "CAD 操作只接受当前工作台页面的请求。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "CAD 操作只接受当前工作台页面的请求。")


router = APIRouter(dependencies=[Depends(local_request)])


class BuildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    # The shared geometry core validates the complete config, including booleans,
    # finite dimensions, role/layer names, source handles and solid confirmation.
    config: dict


class CommandIn(BuildIn):
    message: str = Field(min_length=1, max_length=1000)
    selected_id: str | None = Field(default=None, max_length=128)


class SelectImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    layers: list[str] = Field(min_length=1, max_length=256)
    bounds: list[StrictFloat] | None = Field(default=None, min_length=4, max_length=4)


class DimensionBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension_id: str = Field(min_length=1, max_length=256)
    role: Literal["wall", "column", "slab", "section"] | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=256)
    parameter: Literal["height_m", "base_m"]
    value_source: Literal["annotation", "measurement"]


class BindDimensionIn(BuildIn):
    binding: DimensionBindingIn


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirmation: str = Field(max_length=80)
    format: Literal["glb", "json", "zip", "step"] = "zip"


def capabilities() -> dict:
    from packing_assistant.cad3d.projects import MAX_BUNDLE
    step_ok = False
    if importlib.util.find_spec("packing_assistant.cad3d.step") is not None:
        from packing_assistant.cad3d.step import available as step_available
        step_ok = step_available()
    missing = [package for module, package in DEPENDENCIES.items() if importlib.util.find_spec(module) is None]
    return {
        "ok": True, "available": not missing, "missing_dependencies": missing,
        "install_command": "python -m pip install -r requirements-cad.txt",
        "modes": ["building", "section"], "units": ["mm", "cm", "m", "in", "ft"],
        "max_upload_bytes": MAX_SCAN_BYTES, "direct_import_max_bytes": MAX_UPLOAD_BYTES,
        "staged_import": True, "analysis_2d": True, "session_ttl_seconds": TTL_SECONDS,
        "storage": "temporary-memory-with-explicit-project-save", "formats": ["dxf"], "exports": ["glb", "json", "zip", "step"],
        "step_available": step_ok, "step_install_command": "python -m pip install -r requirements-cad-step.txt",
        "persistent_projects": True, "max_project_bundle_bytes": MAX_BUNDLE,
    }


def require_dependencies() -> None:
    state = capabilities()
    if not state["available"]:
        raise HTTPException(503, "CAD 依赖未安装，请运行：" + state["install_command"])


def require_export_permission() -> None:
    from packing_assistant.runtime.civil_config import load_config
    if not load_config().allow_write():
        raise HTTPException(403, "当前为只读模式，不能导出 CAD 模型或项目包。")


def compute(fn, *args):
    if not COMPUTE.acquire(blocking=False):
        raise HTTPException(429, "已有建模任务正在处理，请稍后重试。")
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        COMPUTE.release()


async def operation(request: Request, fn):
    """Isolated cooperative cancellation, including disconnects and cache publication."""
    from packing_assistant.runtime import cancel
    identifier = request.headers.get("x-cad-operation-id") or uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise HTTPException(422, "CAD 操作编号必须是 32 位小写十六进制。")
    event = Event()
    with OPERATIONS_LOCK:
        now = time.monotonic()
        for key in [key for key, created in CANCELLED_IMPORTS.items() if now - created > 60]:
            CANCELLED_IMPORTS.pop(key, None)
        if identifier in CANCELLED_IMPORTS:
            CANCELLED_IMPORTS.pop(identifier)
            event.set()
        if identifier in OPERATIONS:
            raise HTTPException(409, "该 CAD 操作正在执行，请勿重复提交。")
        if len(OPERATIONS) >= 4:
            raise HTTPException(429, "CAD 操作繁忙，请稍后重试。")
        OPERATIONS[identifier] = event

    async def watch_disconnect():
        while not event.is_set():
            if await request.is_disconnected():
                event.set()
                return
            await asyncio.sleep(0.1)

    def work():
        with cancel.scope(event=event):
            cancel.check()
            return compute(fn)

    watcher = asyncio.create_task(watch_disconnect())
    try:
        return await run_in_threadpool(work)
    except cancel.RunCancelled as exc:
        raise HTTPException(499, "CAD 操作已取消，未发布新的导入结果。") from exc
    finally:
        event.set()
        watcher.cancel()
        with OPERATIONS_LOCK:
            OPERATIONS.pop(identifier, None)


@router.post("/api/cad/import/{operation_id}/cancel")
def cancel_import(operation_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise HTTPException(422, "CAD 操作编号无效。")
    with OPERATIONS_LOCK:
        event = OPERATIONS.get(operation_id)
        if event is not None:
            event.set()
        else:
            # A cancel can arrive while the bounded multipart body is still
            # being read, before the CPU operation registers its identifier.
            CANCELLED_IMPORTS[operation_id] = time.monotonic()
            while len(CANCELLED_IMPORTS) > 256:
                CANCELLED_IMPORTS.popitem(last=False)
    return {"ok": True, "cancelled": True, "was_running": event is not None}


async def bounded_body(request: Request, limit: int) -> bytes:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "请求过大，请减少图纸大小或参数数量。")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_json(request: Request, schema):
    from pydantic import ValidationError
    data = await bounded_body(request, MAX_JSON_BYTES)

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("参数必须是有限数值")
        return number

    def invalid_constant(value):
        raise ValueError("不允许 NaN 或 Infinity")

    try:
        return schema.model_validate(json.loads(data, parse_float=finite_float, parse_constant=invalid_constant))
    except ValidationError as exc:
        # Do not echo entire drawings or arbitrary client-supplied objects.
        raise HTTPException(422, "请求参数不合法：" + str(exc.errors(include_input=False, include_url=False))) from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(422, "请求必须是有效 JSON，且所有数值必须有限。") from exc


@router.get("/cad")
def cad_page():
    return FileResponse(ROOT / "demo/static/cad.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/cad/capabilities")
def cad_capabilities():
    return capabilities()


@router.get("/api/cad/examples/{mode}")
def cad_example(mode: Literal["building", "section"]):
    names = {"building": "synthetic-building-mm.dxf", "section": "synthetic-hollow-section-mm.dxf"}
    return FileResponse(ROOT / "examples/cad-to-3d" / names[mode], media_type="application/dxf", filename=names[mode])


async def uploaded_dxf(request: Request, limit: int) -> tuple[bytes, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "multipart/form-data":
        raise HTTPException(400, "上传格式无效，请仅上传一份 DXF 文件。")
    payload = await bounded_body(request, limit + 64 * 1024)

    async def stream():
        yield payload
        yield b""

    parser = MultiPartParser(request.headers, stream(), max_files=1, max_fields=0)
    # Starlette normally spills uploads >1 MiB to disk. This request has already
    # passed a hard body limit; keep its spool entirely in memory. max_file_size
    # is the name used by earlier supported Starlette versions.
    parser.spool_max_size = parser.max_file_size = limit + 64 * 1024 + 1
    try:
        form = await parser.parse()
    except (MultiPartException, ValueError) as exc:
        # Older supported Starlette releases only clean up MultiPartException;
        # malformed multipart headers can raise the parser's ValueError instead.
        for spool in parser._files_to_close_on_error:
            spool.close()
        raise HTTPException(400, "上传格式无效，请仅上传一份 DXF 文件。") from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile) or len(form) != 1:
            raise HTTPException(422, "请上传一份 DXF 文件。")
        # Names are display metadata only. Never use them as filesystem paths.
        filename = (file.filename or "drawing.dxf").replace("\\", "/").split("/")[-1][:200]
        if not filename.lower().endswith(".dxf"):
            raise HTTPException(415, "首版仅支持 DXF；DWG 请先在 CAD 软件中另存为 DXF。")
        data = await file.read(limit + 1)
    finally:
        await form.close()
    if not data or len(data) > limit:
        raise HTTPException(413 if data else 422, f"DXF 文件不能为空且不能超过 {limit // 1024 // 1024} MiB；大图请使用分阶段导入。")
    return data, filename


@router.post("/api/cad/import")
async def cad_import(request: Request):
    require_dependencies()
    data, filename = await uploaded_dxf(request, MAX_UPLOAD_BYTES)
    from packing_assistant.cad3d.geometry import inspect_dxf
    document = await run_in_threadpool(compute, inspect_dxf, data, filename)
    try:
        document_id = DOCUMENTS.put({**document, "_source_b64": base64.b64encode(data).decode("ascii")})
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    return JSONResponse({"ok": True, "document_id": document_id, "document": document}, headers={"Cache-Control": "no-store"})


@router.post("/api/cad/import/scan")
async def cad_scan(request: Request):
    require_dependencies()
    data, filename = await uploaded_dxf(request, MAX_SCAN_BYTES)
    def scan():
        from packing_assistant.cad3d.imports import scan_dxf
        from packing_assistant.runtime.cancel import check
        index = scan_dxf(data, filename)
        check()
        import_id = IMPORTS.put({"index": index, "_source_b64": base64.b64encode(data).decode("ascii")})
        return {"ok": True, "import_id": import_id, "index": index}
    return JSONResponse(await operation(request, scan), headers={"Cache-Control": "no-store"})


@router.post("/api/cad/import/select")
async def cad_select(request: Request):
    body = await read_json(request, SelectImportIn)
    require_dependencies()
    saved = IMPORTS.get(body.import_id)
    def select():
        from packing_assistant.cad3d.geometry import inspect_dxf
        from packing_assistant.cad3d.imports import normalize_filter
        from packing_assistant.runtime.cancel import check
        source_filter = normalize_filter({"layers": body.layers, **({"bounds": body.bounds} if body.bounds is not None else {})})
        data = base64.b64decode(saved["_source_b64"])
        document = inspect_dxf(data, saved["index"]["filename"], source_filter=source_filter)
        check()
        document_id = DOCUMENTS.put({**document, "_source_b64": saved["_source_b64"]})
        return {"ok": True, "document_id": document_id, "document": document}
    return JSONResponse(await operation(request, select), headers={"Cache-Control": "no-store"})


@router.post("/api/cad/analyze")
async def cad_analyze(request: Request):
    body = await read_json(request, BuildIn)
    require_dependencies()
    document = DOCUMENTS.get(body.document_id)
    def analyze():
        from packing_assistant.cad3d.geometry import analyze_document
        return {"ok": True, **analyze_document(document, body.config)}
    return JSONResponse(await operation(request, analyze), headers={"Cache-Control": "no-store"})


@router.post("/api/cad/bind-dimension")
async def cad_bind_dimension(request: Request):
    body = await read_json(request, BindDimensionIn)
    document = DOCUMENTS.get(body.document_id)
    if (body.binding.role is None) == (body.binding.target_id is None):
        raise HTTPException(422, "必须且只能指定 role 或 target_id 中的一项。")
    from packing_assistant.cad3d.dimensions import bind_dimension
    value = await run_in_threadpool(compute, bind_dimension, document, body.config,
                                   body.binding.model_dump(exclude_none=True))
    return JSONResponse({"ok": True, **value}, headers={"Cache-Control": "no-store"})


@router.post("/api/cad/build")
async def cad_build(request: Request):
    body = await read_json(request, BuildIn)
    require_dependencies()
    document = DOCUMENTS.get(body.document_id)
    from packing_assistant.cad3d.geometry import build_model
    model = await run_in_threadpool(compute, build_model, document, body.config)
    if not model.get("objects"):
        raise HTTPException(422, {"message": "没有可生成的实体，请检查图层、闭合轮廓和参数。", "report": model.get("report", [])})
    model["source"] = {"filename": document["filename"], "sha256": document["sha256"], "units": document["units"]}
    try:
        model_id = MODELS.put(model)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    return JSONResponse({"ok": True, "model_id": model_id, "model": model}, headers={"Cache-Control": "no-store"})


@router.post("/api/cad/command")
async def cad_command(request: Request):
    body = await read_json(request, CommandIn)
    document = DOCUMENTS.get(body.document_id)
    from packing_assistant.cad3d.commands import apply_command
    value = await run_in_threadpool(compute, apply_command, document, body.config, body.message, body.selected_id)
    return JSONResponse({"ok": True, **value}, headers={"Cache-Control": "no-store"})


def export_model(model: dict, format: str) -> tuple[bytes, str, str]:
    if format == "step":
        from packing_assistant.cad3d.step import export_step
        return export_step(model), "application/step", "cad-preview.step"
    record = {"schema": "civil-buddy.cad3d.parameters.v1", "purpose": "geometry-preview-not-certified-bim",
              **{k: v for k, v in model.items() if k != "objects"},
              "objects": [{k: v for k, v in obj.items() if k not in {"vertices", "faces"}} for obj in model["objects"]]}
    parameters = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if format == "json":
        return parameters, "application/json", "cad-parameters.json"
    from packing_assistant.cad3d.geometry import export_glb
    glb = export_glb(model)
    if format == "glb":
        return glb, "model/gltf-binary", "cad-preview.glb"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cad-preview.glb", glb)
        archive.writestr("cad-parameters.json", parameters)
        archive.writestr("README.txt", "Civil Buddy CAD → 3D\n几何预览模型，不是签认件或完整 BIM。\nGLB 长度单位为米、Y 向上；参数记录保留源图单位、原点变换、来源实体与未处理项。\n")
    return stream.getvalue(), "application/zip", "cad-preview.zip"


@router.post("/api/cad/export")
async def cad_export(request: Request):
    body = await read_json(request, ExportIn)
    require_export_permission()
    if not is_confirmation(body.confirmation, strip=False):
        raise HTTPException(403, "导出前请完整键入：" + CONFIRMATION + "（或 / or: " + CONFIRM_EN + "）")
    model = MODELS.get(body.model_id)
    data, media_type, filename = await run_in_threadpool(compute, export_model, model, body.format)
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class ProjectSaveIn(BuildIn):
    name: str = Field(min_length=1, max_length=100)
    project_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    model_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ProjectExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(max_length=80)
    expected_revision: StrictInt = Field(ge=1)


def project_store():
    # App entry points also import config as a top-level module; its root is
    # resolved from __file__, so package imports use the same storage location.
    from demo.config import OUT_ROOT
    from packing_assistant.cad3d.projects import CadProjectStore
    return CadProjectStore(OUT_ROOT / "_cad")


def project_compute(fn, *args, **kwargs):
    from packing_assistant.cad3d.projects import ProjectConflict, ProjectNotFound
    if not COMPUTE.acquire(blocking=False):
        raise HTTPException(429, "已有建模任务正在处理，请稍后重试。")
    try:
        return fn(*args, **kwargs)
    except ProjectConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "项目存储暂不可用，请稍后重试。原项目保持不变。") from exc
    finally:
        COMPUTE.release()


def cache_project(snapshot: dict) -> dict:
    document = snapshot["document"]
    source = project_store().source(snapshot["project"]["id"])
    snapshot["document_id"] = DOCUMENTS.put({**document, "_source_b64": base64.b64encode(source).decode("ascii")})
    if snapshot.get("model"):
        snapshot["model_id"] = MODELS.put(snapshot["model"])
    return {"ok": True, **snapshot}


@router.get("/api/cad/projects")
async def cad_projects():
    rows = await run_in_threadpool(project_compute, project_store().list_projects)
    return JSONResponse({"ok": True, "projects": rows}, headers={"Cache-Control": "no-store"})


@router.post("/api/cad/projects")
async def cad_project_save(request: Request):
    body = await read_json(request, ProjectSaveIn)
    require_dependencies()
    document = DOCUMENTS.get(body.document_id)
    source = base64.b64decode(document.pop("_source_b64", ""))
    model = MODELS.get(body.model_id) if body.model_id else None
    value = await run_in_threadpool(project_compute, project_store().save, name=body.name, document=document,
                                   source=source, draft_config=body.config, model=model, project_id=body.project_id,
                                   expected_revision=body.expected_revision)
    return JSONResponse({"ok": True, "project": value}, headers={"Cache-Control": "no-store"})


@router.post("/api/cad/projects/import")
async def cad_project_import(request: Request):
    from packing_assistant.cad3d.projects import MAX_BUNDLE
    require_dependencies()
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(400, "请选择一个 CAD 项目 ZIP 包。")
    payload = await bounded_body(request, MAX_BUNDLE + 65536)

    async def stream():
        yield payload
        yield b""

    parser = MultiPartParser(request.headers, stream(), max_files=1, max_fields=0)
    parser.spool_max_size = parser.max_file_size = MAX_BUNDLE + 65537
    try:
        form = await parser.parse()
    except (MultiPartException, ValueError) as exc:
        for spool in parser._files_to_close_on_error:
            spool.close()
        raise HTTPException(400, "项目包上传格式无效。") from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile) or len(form) != 1:
            raise HTTPException(422, "请选择一份 CAD 项目包。")
        data = await file.read(MAX_BUNDLE + 1)
    finally:
        await form.close()
    if len(data) > MAX_BUNDLE:
        raise HTTPException(413, f"CAD 项目包不能超过 {MAX_BUNDLE // 1024 // 1024} MiB。")

    def restore():
        return cache_project(project_store().import_bundle(data))

    value = await run_in_threadpool(project_compute, restore)
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.get("/api/cad/projects/{project_id}")
async def cad_project_open(project_id: str, version: int | None = None):
    require_dependencies()

    def restore():
        return cache_project(project_store().open(project_id, version=version))

    value = await run_in_threadpool(project_compute, restore)
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.post("/api/cad/projects/{project_id}/export")
async def cad_project_export(project_id: str, request: Request):
    body = await read_json(request, ProjectExportIn)
    require_export_permission()
    if not is_confirmation(body.confirmation, strip=False):
        raise HTTPException(403, "导出前请完整键入：" + CONFIRMATION + "（或 / or: " + CONFIRM_EN + "）")
    payload = await run_in_threadpool(project_compute, project_store().export_bundle, project_id, body.expected_revision)
    return Response(payload, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="cad-project.zip"',
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
