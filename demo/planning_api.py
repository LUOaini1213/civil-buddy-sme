"""Local-only construction planning endpoints sharing the workbench safeguards."""
from __future__ import annotations

import importlib.util
import base64
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field, StrictInt, StrictBool
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser, MultiPartException

try:
    import engineering_api as eng
except ImportError:
    from demo import engineering_api as eng

cad = eng.cad
router = APIRouter(dependencies=[Depends(cad.local_request)])
RUNS = cad.MemoryStore(max_items=30, max_bytes=64 * 1024 * 1024)
IMPORTS = cad.MemoryStore(max_items=30, max_bytes=64 * 1024 * 1024)
BASE = "/api/engineering/planning"


class PlanIn(eng.Input):
    plan: dict


class SaveIn(PlanIn):
    name: str = Field(min_length=1, max_length=100)
    weekly: list[dict] | None = Field(default=None, max_length=2000)
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    source_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    synthetic: StrictBool | None = None
    method: Literal["cpm", "resource"] | None = None


class ExportIn(PlanIn):
    format: Literal["json", "csv", "xlsx", "xml", "mspdi"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirmation: str = Field(max_length=80)


class BundleExportIn(eng.RevisionIn):
    confirmation: str = Field(max_length=80)


def store():
    from demo.config import REPO_ROOT
    from packing_assistant.engineering.planning_records import PlanningStore
    import os
    from pathlib import Path
    return PlanningStore(Path(os.environ.get("CIVIL_DOMAIN_WORKSPACE", str(REPO_ROOT))))


def example():
    def task(ident, name, duration, dependencies=(), resource=False):
        return {"id": ident, "name": name, "duration": duration, "progress": 0, "parent_id": None,
                "dependencies": [{"task_id": predecessor, "type": "FS", "lag": 0} for predecessor in dependencies],
                "resources": {"crew": 1} if resource else {}, "actual_start": None, "actual_finish": None}
    return {"start_date": "2026-09-21", "calendar": {"weekdays": [0, 1, 2, 3, 4], "holidays": []},
            "tasks": [task("A", "合成：施工准备", 2), task("B", "合成：分区一施工", 4, ("A",), True),
                      task("C", "合成：分区二施工", 3, ("A",), True), task("D", "合成：收尾", 2, ("B", "C"))],
            "resources": [{"id": "crew", "name": "合成：共享班组", "capacity": 1}]}


def publish(plan, method="cpm"):
    from packing_assistant.engineering.planning import calculate
    from packing_assistant.engineering.worker import run
    from packing_assistant.runtime.cancel import check
    try:
        computed = run("planning_optimize", plan, timeout=40) if method == "resource" else calculate(plan)
    except ImportError as exc:
        raise HTTPException(503, "资源排程依赖未就绪，请安装 requirements-planning.txt。") from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    check()
    payload = dict(computed, method=method)
    return {"ok": True, "run_id": RUNS.put(payload), **payload}


def snapshot(plan, run_id=None, method=None):
    from packing_assistant.engineering.planning import calculate, validate_plan
    from packing_assistant.engineering.planning_records import digest
    normalized = validate_plan(plan)
    if run_id:
        data = RUNS.get(run_id)
        if digest(data["plan"]) != digest(normalized):
            raise ValueError("计算结果对应旧参数，请重新计算后保存或导出。")
        if method is not None and method != data["method"]:
            raise ValueError("计算方式与结果来源不一致，请重新计算。")
        return data
    if method == "resource":
        raise ValueError("资源方案输入已修改，请先预览资源调整并应用，再保存；不会自动切换为 CPM。")
    return dict(calculate(normalized), method="cpm")


def project_response(project):
    if project.get("import_source"):
        source = dict(project["import_source"])
        blob = store().source_file(project["id"], source["source"]["sha256"])
        if blob is not None:
            source["source_file"] = blob
        project["source_id"] = IMPORTS.put(source)
    return {"project": project, "run_id": RUNS.put({k: project[k] for k in ("plan", "result", "method")})}


@router.get("/engineering/planning")
def page():
    return FileResponse(eng.ROOT / "demo/static/engineering-planning.html", headers={"Cache-Control": "no-cache"})


@router.get(BASE + "/capabilities")
def capabilities():
    from packing_assistant.engineering.planning_exchange import capabilities as formats
    return {"cpm": True, "resource_optimization": importlib.util.find_spec("ortools") is not None,
            "formats": formats(), "max_tasks": 250, "install_command": "python -m pip install -r requirements-planning.txt"}


@router.get(BASE + "/example")
def get_example():
    return {"plan": example(), "synthetic": True,
            "expected": {"cpm_workdays": 8, "one_crew_workdays": 11, "two_crews_workdays": 8}}


@router.post(BASE + "/calculate")
async def calculate(request: Request):
    body = await cad.read_json(request, PlanIn)
    return await cad.operation(request, lambda: publish(body.plan))


@router.post(BASE + "/optimize")
async def optimize(request: Request):
    body = await cad.read_json(request, PlanIn)
    return await cad.operation(request, lambda: publish(body.plan, "resource"))


@router.get(BASE + "/projects")
async def projects():
    return {"projects": await run_in_threadpool(eng.storage_call, store().list_projects)}


@router.post(BASE + "/projects")
async def save(request: Request):
    body = await cad.read_json(request, SaveIn)

    def work():
        method = body.method
        if method is None and body.id and not body.run_id:
            method = eng.storage_call(store().open, body.id)["method"]
        computed = snapshot(body.plan, body.run_id, method)
        source = IMPORTS.get(body.source_id) if body.source_id else None
        return eng.storage_call(store().save, name=body.name, weekly=body.weekly,
                 project_id=body.id, expected_revision=body.expected_revision, synthetic=body.synthetic,
                 import_source=source, **computed)
    project = await cad.operation(request, work)
    # Persistence owns the cancellation boundary. After atomic replacement,
    # publishing response caches must not report the durable commit as undone.
    return await run_in_threadpool(project_response, project)


@router.get(BASE + "/projects/{ident}")
async def open_project(ident: str, version: int | None = None):
    project = await run_in_threadpool(eng.storage_call, store().open, ident, version)
    return await run_in_threadpool(project_response, project)


@router.post(BASE + "/projects/{ident}/export")
async def export_project(ident: str, request: Request):
    from packing_assistant.engineering.planning_bundle import export_bundle
    body = await cad.read_json(request, BundleExportIn)
    cad.require_export_permission()
    if not cad.is_confirmation(body.confirmation, strip=False):
        raise HTTPException(403, "导出项目前请完整输入现有签认确认句。")
    def work():
        payload = eng.storage_call(export_bundle, store(), ident, body.expected_revision)
        return Response(payload, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="civil-planning-project.zip"',
            "Cache-Control": "no-store"})
    return await cad.operation(request, work)


@router.post(BASE + "/projects/import")
async def import_project(request: Request):
    from packing_assistant.engineering.planning_bundle import MAX_BUNDLE, import_bundle
    data, filename = await read_upload(request, MAX_BUNDLE)
    if not filename.lower().endswith(".zip"):
        raise HTTPException(422, "完整施工项目包须为 ZIP 文件。")
    project = await cad.operation(request, lambda: eng.storage_call(import_bundle, store(), data))
    response = await run_in_threadpool(project_response, project)
    return {**response, "confirmation_reset": True, "copy_revision_policy": "preserve_history_continue"}


@router.post(BASE + "/projects/{ident}/source")
async def restore_source(ident: str, expected_revision: int, request: Request):
    data, _filename = await read_upload(request, 8 * 1024 * 1024)
    project = await cad.operation(request, lambda: eng.storage_call(store().attach_source, ident, data, expected_revision))
    return await run_in_threadpool(project_response, project)


@router.post(BASE + "/projects/{ident}/{action}")
async def mutate(ident: str, action: Literal["undo", "baseline"], request: Request):
    body = await cad.read_json(request, eng.RevisionIn)
    fn = store().undo if action == "undo" else store().set_baseline
    project = await cad.operation(request, lambda: eng.storage_call(fn, ident, body.expected_revision))
    return await run_in_threadpool(project_response, project)


async def read_upload(request, limit):
    raw = await cad.bounded_body(request, limit + 65536)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(400, "请上传一份进度计划文件。")

    async def stream():
        yield raw
        yield b""

    parser = MultiPartParser(request.headers, stream(), max_files=1, max_fields=0)
    parser.spool_max_size = parser.max_file_size = len(raw) + 1
    try:
        form = await parser.parse()
    except (MultiPartException, ValueError) as exc:
        for spool in parser._files_to_close_on_error:
            spool.close()
        raise HTTPException(400, "计划上传格式无效。") from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile) or len(form) != 1:
            raise HTTPException(422, "每次仅上传一份计划文件。")
        data = await file.read(limit + 1)
        filename = (file.filename or "plan.json").replace("\\", "/").split("/")[-1][:200]
    finally:
        await form.close()
    if not data or len(data) > limit:
        raise HTTPException(413, f"上传文件不能为空且不能超过 {limit // (1024 * 1024)} MiB。")
    return data, filename


@router.post(BASE + "/import")
async def import_file(request: Request):
    from packing_assistant.engineering.planning_exchange import import_plan
    data, filename = await read_upload(request, 8 * 1024 * 1024)

    def work():
        try:
            imported = import_plan(data, filename)
            from packing_assistant.runtime.cancel import check
            check()
            source = {k: imported[k] for k in ("source", "original_dates", "report")}
            source["source_file"] = {"data": base64.b64encode(data).decode("ascii"), "size": len(data)}
            imported["source_id"] = IMPORTS.put(source)
            return imported
        except ImportError as exc:
            raise HTTPException(503, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, "计划转换超时，请拆分文件后重试；当前项目未覆盖。") from exc
    return await cad.operation(request, work)


@router.post(BASE + "/export")
async def export(request: Request):
    from packing_assistant.engineering.planning_exchange import export_plan
    body = await cad.read_json(request, ExportIn)
    cad.require_export_permission()
    if not cad.is_confirmation(body.confirmation, strip=False):
        raise HTTPException(403, "导出前请完整输入现有签认确认句。")

    def work():
        computed = snapshot(body.plan, body.run_id)
        exported = export_plan(computed["plan"], computed["result"], body.format)
        from packing_assistant.runtime.cancel import check
        check()
        return Response(exported["data"], media_type=exported["media_type"], headers={
            "Content-Disposition": 'attachment; filename="' + exported["filename"] + '"',
            "Cache-Control": "no-store",
        })
    return await cad.operation(request, work)
