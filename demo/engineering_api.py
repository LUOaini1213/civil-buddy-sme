"""Optional, bounded engineering adapters shared by interactive workbench pages."""
from __future__ import annotations

import importlib.util
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool

try:
    import cad_api as cad
except ImportError:
    from demo import cad_api as cad

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(dependencies=[Depends(cad.local_request)])
RUNS = cad.MemoryStore(max_items=8, max_bytes=160 * 1024 * 1024)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrameIn(Input):
    model: dict


class SectionIn(cad.BuildIn):
    pass


class SaveIn(Input):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=100)
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: StrictInt | None = Field(default=None, ge=1)


class ScheduleIn(Input):
    name: str = Field(min_length=1, max_length=100)
    tasks: list[dict] = Field(max_length=300)
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: StrictInt | None = Field(default=None, ge=1)


class RevisionIn(Input):
    expected_revision: StrictInt = Field(ge=1)


class ExportIn(Input):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirmation: str = Field(max_length=80)


def store():
    from demo.config import OUT_ROOT
    from packing_assistant.engineering.records import AnalysisStore
    return AnalysisStore(OUT_ROOT / "_engineering")


def schedule_store():
    from demo.config import REPO_ROOT
    from packing_assistant.engineering.schedule import ScheduleStore
    import os
    from pathlib import Path
    return ScheduleStore(Path(os.environ.get("CIVIL_DOMAIN_WORKSPACE", str(REPO_ROOT))))


def storage_call(fn, *args, **kwargs):
    from packing_assistant.engineering.records import RecordConflict, RecordNotFound
    from packing_assistant.engineering.schedule import ScheduleConflict, ScheduleNotFound
    try:
        return fn(*args, **kwargs)
    except (RecordConflict, ScheduleConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RecordNotFound, ScheduleNotFound) as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "项目存储暂不可用，原记录未覆盖。") from exc


@router.get("/engineering")
def page():
    return FileResponse(ROOT / "demo/static/engineering.html", headers={"Cache-Control": "no-cache"})


@router.get("/engineering/schedule")
def schedule_page():
    return FileResponse(ROOT / "demo/static/engineering-schedule.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/engineering/capabilities")
def capabilities():
    packages = {"frame": ("Pynite", "PyniteFEA"), "section": ("sectionproperties", "sectionproperties"),
                "ifc_check": ("ifctester", "ifctester"), "ifc_diff": ("ifcdiff", "ifcdiff")}
    result = {}
    for key, (module, package) in packages.items():
        try:
            result[key] = {"available": importlib.util.find_spec(module) is not None, "version": version(package)}
        except (ImportError, PackageNotFoundError):
            result[key] = {"available": False, "version": None}
    return {"ok": True, "tools": result, "schedule": True,
            "install_command": "python -m pip install -r requirements-engineering.txt"}


def publish(kind, payload):
    from packing_assistant.engineering.worker import run
    from packing_assistant.runtime.cancel import check
    try:
        result = run(kind, payload)
    except ImportError as exc:
        raise HTTPException(503, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    check()
    identifier = RUNS.put({"kind": kind, "inputs": payload, "result": result})
    return {"ok": True, "run_id": identifier, "result": result}


@router.post("/api/engineering/frame")
async def frame(request: Request):
    body = await cad.read_json(request, FrameIn)
    return await cad.operation(request, lambda: publish("frame", body.model))


@router.post("/api/engineering/section")
async def section(request: Request):
    body = await cad.read_json(request, SectionIn)
    document = cad.DOCUMENTS.get(body.document_id)
    return await cad.operation(request, lambda: publish("section", {"document": document, "config": body.config}))


@router.post("/api/engineering/ifc/{action}")
async def ifc(request: Request, action: Literal["check", "diff"]):
    raw = await cad.bounded_body(request, 24 * 1024 * 1024)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(422, "请提交有效 IFC 输入对象。") from exc
    return await cad.operation(request, lambda: publish("ifc_" + action, payload))


@router.get("/api/engineering/examples/frame")
def frame_example(kind: Literal["beam", "frame"] = "beam"):
    from packing_assistant.engineering.frame import synthetic_example
    return {"model": synthetic_example(kind), "synthetic": True}


@router.get("/api/engineering/examples/ifc/{name}")
def ifc_example(name: Literal["old.ifc", "new.ifc", "requirements.ids"]):
    from packing_assistant.engineering.ifc import synthetic_examples
    try:
        raw = synthetic_examples()[name]
    except ImportError as exc:
        raise HTTPException(503, "请安装 requirements-engineering.txt 后使用 IFC 样例。") from exc
    return Response(raw, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="synthetic-{name}"', "Cache-Control": "no-store"})


@router.post("/api/engineering/operations/{operation_id}/cancel")
def cancel(operation_id: str):
    return cad.cancel_import(operation_id)


@router.get("/api/engineering/projects")
async def projects():
    return {"projects": await run_in_threadpool(storage_call, store().list_projects)}


@router.post("/api/engineering/projects")
async def save(request: Request):
    body = await cad.read_json(request, SaveIn)
    snapshot = RUNS.get(body.run_id)
    saved = await cad.operation(request, lambda: storage_call(store().save, body.name, snapshot, body.id, body.expected_revision))
    return {"ok": True, **saved}


@router.get("/api/engineering/projects/{identifier}")
async def open_project(identifier: str, version: int | None = None):
    saved = await run_in_threadpool(storage_call, store().open, identifier, version)
    snapshot = saved["snapshot"]
    run_id = RUNS.put(snapshot)
    response = {"ok": True, **saved, "run_id": run_id}
    if snapshot["kind"] == "section":
        response["document_id"] = cad.DOCUMENTS.put(snapshot["inputs"]["document"])
    return response


@router.post("/api/engineering/export")
async def export(request: Request):
    body = await cad.read_json(request, ExportIn)
    cad.require_export_permission()
    if not cad.is_confirmation(body.confirmation, strip=False):
        raise HTTPException(403, "导出计算记录前请完整键入：" + cad.CONFIRMATION + "（或 / or: " + cad.CONFIRM_EN + "）")
    snapshot = RUNS.get(body.run_id)
    # IFC original bytes and CAD source are retained in project storage. Export
    # here is the analysis record, not a rewritten model or signed deliverable.
    data = json.dumps({"schema": "civil-buddy.engineering-report.v1", "kind": snapshot["kind"],
                       "result": snapshot["result"]}, ensure_ascii=False, allow_nan=False, indent=2)
    return Response(data, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="analysis-record.json"', "Cache-Control": "no-store"})


@router.get("/api/engineering/schedules")
async def schedules():
    return {"projects": await run_in_threadpool(storage_call, schedule_store().list_projects)}


@router.post("/api/engineering/schedules")
async def save_schedule(request: Request):
    body = await cad.read_json(request, ScheduleIn)
    saved = await run_in_threadpool(storage_call, schedule_store().save, name=body.name, tasks=body.tasks,
                                   project_id=body.id, expected_revision=body.expected_revision)
    return {"project": saved}


@router.get("/api/engineering/schedules/{identifier}")
async def open_schedule(identifier: str):
    return {"project": await run_in_threadpool(storage_call, schedule_store().open, identifier)}


@router.post("/api/engineering/schedules/{identifier}/undo")
async def undo_schedule(identifier: str, request: Request):
    body = await cad.read_json(request, RevisionIn)
    return {"project": await run_in_threadpool(storage_call, schedule_store().undo, identifier, body.expected_revision)}
